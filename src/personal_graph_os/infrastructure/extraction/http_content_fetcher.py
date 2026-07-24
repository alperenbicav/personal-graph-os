"""SSRF-hardened `ContentFetcher` implementation (EP-2026-012 ST-03).

Every safeguard the approved design requires for URL fetching lives here, not in a caller:
scheme allowlisting, a `NetworkBackend` that resolves and validates a host exactly once and
then connects the raw TCP socket directly to that validated address -- closing the
DNS-rebinding/TOCTOU window a separate validate-then-let-httpx-reconnect approach would leave
open (review finding S3-R01) -- applied again on every redirect hop, a bounded redirect count,
a hard response-size cap enforced while streaming and never trusting a `Content-Length` header
alone (review finding S3-R02), and a request timeout. `ExtractionService` and its callers depend
only on the `ContentFetcher` protocol and never construct an `httpx`/`httpcore` client themselves.
"""

from __future__ import annotations

import ipaddress
import socket
import ssl
import typing
from collections.abc import Callable, Iterable, Iterator
from types import TracebackType
from urllib.parse import urljoin, urlparse

import httpcore
import httpx

from personal_graph_os.application.extraction_adapters import FetchedResource
from personal_graph_os.domain.extraction import SourceAccessDeniedError, SourceFetchFailedError

# httpcore is used directly (a custom `NetworkBackend`/`ConnectionPool`), not merely as an
# httpx implementation detail, so it is declared as an explicit runtime dependency in
# `pyproject.toml` rather than relied on as httpx's undeclared transitive dependency.
_TRANSPORT_LEVEL_ERRORS = (
    httpcore.NetworkError,
    httpcore.TimeoutException,
    httpcore.ProtocolError,
    httpcore.ProxyError,
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_ACCESS_DENIED_STATUS_CODES = frozenset({401, 403})
_DEFAULT_USER_AGENT = "personal-graph-os-extraction/1.0 (+local research capture)"


def _resolve_hostname_via_dns(hostname: str) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError as error:
        raise SourceFetchFailedError(f"could not resolve host {hostname!r}: {error}") from error
    return tuple({str(info[4][0]) for info in infos})


def _is_public_address(raw_address: str) -> bool:
    address = ipaddress.ip_address(raw_address)
    return not (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    )


def _resolve_and_validate(resolve_hostname: Callable[[str], tuple[str, ...]], host: str) -> str:
    """Resolve `host` (or accept it as-is if it is already a literal IP) exactly once and
    return one validated public address -- the single source of truth both the pre-request
    check and the actual socket connection use, so nothing else re-resolves `host` later."""
    try:
        ipaddress.ip_address(host)
        candidate_addresses: tuple[str, ...] = (host,)
    except ValueError:
        candidate_addresses = resolve_hostname(host)

    if not candidate_addresses or not all(
        _is_public_address(address) for address in candidate_addresses
    ):
        raise SourceFetchFailedError(
            f"{host!r} resolves to a private/loopback/link-local address, which this "
            "fetcher never retrieves"
        )
    return candidate_addresses[0]


class _PinnedResolutionNetworkBackend(httpcore.NetworkBackend):
    """Resolves and validates the target host exactly once per connection, then opens the TCP
    socket directly against that validated address -- never letting the underlying platform
    resolver perform a second, unvalidated lookup at connect time (review finding S3-R01)."""

    def __init__(
        self,
        resolve_hostname: Callable[[str], tuple[str, ...]],
        *,
        delegate: httpcore.NetworkBackend | None = None,
    ) -> None:
        self._resolve_hostname = resolve_hostname
        self._delegate = delegate or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable | None = None,
    ) -> httpcore.NetworkStream:
        pinned_address = _resolve_and_validate(self._resolve_hostname, host)
        return self._delegate.connect_tcp(
            pinned_address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: Iterable | None = None
    ) -> httpcore.NetworkStream:
        raise SourceFetchFailedError("unix socket connections are not supported")

    def sleep(self, seconds: float) -> None:
        self._delegate.sleep(seconds)


class _PinnedResponseStream(httpx.SyncByteStream):
    """Wraps an `httpcore` response's byte stream so `close()` releases the underlying
    connection back to the pool -- the same responsibility httpx's own (private) response
    stream wrapper has, reimplemented here against only public `httpcore`/`httpx` types."""

    def __init__(self, httpcore_stream: Iterable[bytes]) -> None:
        self._httpcore_stream = httpcore_stream

    def __iter__(self) -> Iterator[bytes]:
        yield from self._httpcore_stream

    def close(self) -> None:
        close = getattr(self._httpcore_stream, "close", None)
        if close is not None:
            close()


class _PinnedHttpTransport(httpx.BaseTransport):
    """A minimal `httpx.BaseTransport` built directly on the public `httpcore.ConnectionPool`
    API (review finding S3-R05): earlier revisions subclassed `httpx.HTTPTransport` and
    replaced its private `_pool` attribute, and built the SSL context via private
    `httpx._config.create_ssl_context`. Neither private member is used here -- only
    `httpx.BaseTransport`/`httpx.SyncByteStream` and `httpcore.ConnectionPool`/`Request`/`URL`,
    all public, stable API surface."""

    def __init__(
        self,
        *,
        resolve_hostname: Callable[[str], tuple[str, ...]],
        network_backend: httpcore.NetworkBackend | None = None,
    ) -> None:
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=network_backend or _PinnedResolutionNetworkBackend(resolve_hostname),
        )

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        httpcore_request = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        try:
            httpcore_response = self._pool.handle_request(httpcore_request)
        except _TRANSPORT_LEVEL_ERRORS as error:
            raise SourceFetchFailedError(
                f"fetching {str(request.url)!r} failed: {error}"
            ) from error

        # The pool is always constructed sync-only (`httpcore.ConnectionPool`, never
        # `AsyncConnectionPool`), so its response stream is always the sync `Iterable[bytes]`
        # variant despite the broader union type `httpcore.Response.stream` declares.
        response_stream = typing.cast("Iterable[bytes]", httpcore_response.stream)
        return httpx.Response(
            status_code=httpcore_response.status,
            headers=httpcore_response.headers,
            stream=_PinnedResponseStream(response_stream),
            extensions=httpcore_response.extensions,
        )

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> _PinnedHttpTransport:
        self._pool.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        self._pool.__exit__(exc_type, exc_value, traceback)


class HttpContentFetcher:
    """A `ContentFetcher` that only reaches public HTTP(S) hosts, with bounded redirects,
    response size, and timeout."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = 15.0,
        max_content_length_bytes: int = 25_000_000,
        max_redirects: int = 5,
        resolve_hostname: Callable[[str], tuple[str, ...]] | None = None,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._resolve_hostname = resolve_hostname or _resolve_hostname_via_dns
        effective_transport = transport or _PinnedHttpTransport(
            resolve_hostname=self._resolve_hostname
        )
        self._client = httpx.Client(
            transport=effective_transport, timeout=timeout_seconds, follow_redirects=False
        )
        self._max_content_length_bytes = max_content_length_bytes
        self._max_redirects = max_redirects
        self._user_agent = user_agent

    def fetch(self, url: str, *, accept: str | None = None) -> FetchedResource:
        requested_url = url
        current_url = url
        headers = {"User-Agent": self._user_agent}
        if accept is not None:
            headers["Accept"] = accept

        for _ in range(self._max_redirects + 1):
            self._reject_unsafe_target(current_url)
            response = self._send(current_url, headers=headers)
            if response.is_redirect:
                location = response.headers.get("location")
                response.close()
                if not location:
                    raise SourceFetchFailedError(
                        f"{current_url!r} responded with a redirect status but no Location header"
                    )
                current_url = urljoin(current_url, location)
                continue
            return self._to_fetched_resource(requested_url, current_url, response)

        raise SourceFetchFailedError(f"{requested_url!r} exceeded {self._max_redirects} redirects")

    def _reject_unsafe_target(self, url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in _ALLOWED_SCHEMES:
            raise SourceFetchFailedError(
                f"{url!r} uses unsupported scheme {parsed.scheme!r}; only http/https are allowed"
            )
        hostname = parsed.hostname
        if not hostname:
            raise SourceFetchFailedError(f"{url!r} has no resolvable host")
        _resolve_and_validate(self._resolve_hostname, hostname)

    def _send(self, url: str, *, headers: dict[str, str]) -> httpx.Response:
        try:
            request = self._client.build_request("GET", url, headers=headers)
            return self._client.send(request, stream=True)
        except httpx.HTTPError as error:
            raise SourceFetchFailedError(f"fetching {url!r} failed: {error}") from error

    def _to_fetched_resource(
        self, requested_url: str, final_url: str, response: httpx.Response
    ) -> FetchedResource:
        try:
            return self._read_fetched_resource(requested_url, final_url, response)
        finally:
            response.close()

    def _read_fetched_resource(
        self, requested_url: str, final_url: str, response: httpx.Response
    ) -> FetchedResource:
        if response.status_code in _ACCESS_DENIED_STATUS_CODES:
            raise SourceAccessDeniedError(
                f"{final_url!r} denied access with status {response.status_code}"
            )
        if not (200 <= response.status_code < 300):
            raise SourceFetchFailedError(
                f"{final_url!r} responded with unexpected status {response.status_code}",
                status_code=response.status_code,
            )

        limit = self._max_content_length_bytes
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as error:
                raise SourceFetchFailedError(
                    f"{final_url!r} sent a malformed Content-Length header {content_length!r}"
                ) from error
            if declared_length > limit:
                raise SourceFetchFailedError(
                    f"{final_url!r} declared {declared_length} bytes, exceeding the "
                    f"{limit}-byte limit"
                )

        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > limit:
                raise SourceFetchFailedError(
                    f"{final_url!r} exceeded the {limit}-byte limit while streaming"
                )

        content_type = response.headers.get("content-type", "application/octet-stream")
        content_type = content_type.split(";", 1)[0].strip() or "application/octet-stream"
        return FetchedResource(
            requested_url=requested_url,
            final_url=final_url,
            content_type=content_type,
            body=bytes(body),
        )

    def close(self) -> None:
        self._client.close()

from __future__ import annotations

import httpcore
import httpx
import pytest

from personal_graph_os.domain.extraction import SourceAccessDeniedError, SourceFetchFailedError
from personal_graph_os.infrastructure.extraction.http_content_fetcher import (
    HttpContentFetcher,
    _PinnedHttpTransport,
    _PinnedResolutionNetworkBackend,
)

_PUBLIC_ADDRESS = ("93.184.216.34",)
_PRIVATE_ADDRESS = ("10.0.0.5",)


def _fetcher(
    handler, *, resolve_hostname=lambda host: _PUBLIC_ADDRESS, **kwargs
) -> HttpContentFetcher:
    return HttpContentFetcher(
        transport=httpx.MockTransport(handler),
        resolve_hostname=resolve_hostname,
        **kwargs,
    )


def test_fetch_returns_body_and_content_type_on_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=b"hello", headers={"content-type": "text/html; charset=utf-8"}
        )

    fetcher = _fetcher(handler)
    result = fetcher.fetch("https://example.com/article")

    assert result.body == b"hello"
    assert result.content_type == "text/html"
    assert result.final_url == "https://example.com/article"


def test_fetch_sends_the_optional_accept_header() -> None:
    seen_accept: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_accept.append(request.headers.get("accept"))
        return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    fetcher = _fetcher(handler)
    fetcher.fetch("https://example.com/data", accept="application/json")

    assert seen_accept == ["application/json"]


def test_fetch_follows_a_redirect_and_revalidates_the_new_target() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(302, headers={"location": "https://example.com/new"})
        return httpx.Response(200, content=b"final", headers={"content-type": "text/plain"})

    fetcher = _fetcher(handler)
    result = fetcher.fetch("https://example.com/old")

    assert result.final_url == "https://example.com/new"
    assert result.body == b"final"


def test_fetch_rejects_a_non_http_scheme() -> None:
    fetcher = _fetcher(lambda request: httpx.Response(200))
    with pytest.raises(SourceFetchFailedError):
        fetcher.fetch("file:///etc/passwd")


def test_fetch_rejects_a_url_resolving_to_a_private_address() -> None:
    fetcher = _fetcher(
        lambda request: httpx.Response(200), resolve_hostname=lambda host: _PRIVATE_ADDRESS
    )
    with pytest.raises(SourceFetchFailedError):
        fetcher.fetch("https://internal.example.com/")


def test_fetch_rejects_a_redirect_target_resolving_to_a_private_address() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "example.com":
            return httpx.Response(302, headers={"location": "https://internal.example.com/"})
        return httpx.Response(200)

    def resolve_hostname(host: str) -> tuple[str, ...]:
        return _PRIVATE_ADDRESS if host == "internal.example.com" else _PUBLIC_ADDRESS

    fetcher = _fetcher(handler, resolve_hostname=resolve_hostname)
    with pytest.raises(SourceFetchFailedError):
        fetcher.fetch("https://example.com/")


def test_fetch_raises_access_denied_on_401_and_403() -> None:
    fetcher = _fetcher(lambda request: httpx.Response(403))
    with pytest.raises(SourceAccessDeniedError):
        fetcher.fetch("https://example.com/private")


def test_fetch_raises_fetch_failed_on_other_non_2xx_status() -> None:
    fetcher = _fetcher(lambda request: httpx.Response(500))
    with pytest.raises(SourceFetchFailedError) as excinfo:
        fetcher.fetch("https://example.com/broken")
    assert excinfo.value.status_code == 500


def test_fetch_rejects_a_body_over_the_configured_size_limit() -> None:
    fetcher = _fetcher(
        lambda request: httpx.Response(200, content=b"x" * 100),
        max_content_length_bytes=10,
    )
    with pytest.raises(SourceFetchFailedError):
        fetcher.fetch("https://example.com/huge")


def test_fetch_rejects_a_declared_content_length_over_the_limit_before_reading_the_body() -> None:
    fetcher = _fetcher(
        lambda request: httpx.Response(200, content=b"small", headers={"content-length": "999999"}),
        max_content_length_bytes=10,
    )
    with pytest.raises(SourceFetchFailedError):
        fetcher.fetch("https://example.com/lying-header")


def test_fetch_gives_up_after_the_configured_redirect_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://example.com/next"})

    fetcher = _fetcher(handler, max_redirects=2)
    with pytest.raises(SourceFetchFailedError):
        fetcher.fetch("https://example.com/loop")


def test_fetch_raises_on_a_malformed_content_length_header() -> None:
    fetcher = _fetcher(
        lambda request: httpx.Response(
            200, content=b"hi", headers={"content-length": "not-a-number"}
        )
    )
    with pytest.raises(SourceFetchFailedError):
        fetcher.fetch("https://example.com/malformed-header")


class _TrackingStream(httpx.SyncByteStream):
    """A response body double whose chunks are only produced on demand -- unlike
    `httpx.Response(content=...)`, which materializes the whole body upfront regardless of how
    much of it is actually consumed."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.yielded_chunk_count = 0
        self.closed = False

    def __iter__(self):
        for chunk in self._chunks:
            self.yielded_chunk_count += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


def test_fetch_stops_streaming_once_the_size_limit_is_exceeded_with_no_content_length() -> None:
    stream = _TrackingStream([b"x" * 10] * 1000)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/plain"}, stream=stream)

    fetcher = _fetcher(handler, max_content_length_bytes=25)
    with pytest.raises(SourceFetchFailedError):
        fetcher.fetch("https://example.com/never-ending")

    assert stream.yielded_chunk_count < 1000
    assert stream.closed is True


class _RecordingDelegateBackend(httpcore.NetworkBackend):
    def __init__(self) -> None:
        self.connected_to: tuple[str, int] | None = None
        self.connect_attempted = False

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ) -> httpcore.NetworkStream:
        self.connect_attempted = True
        self.connected_to = (host, port)
        return httpcore.NetworkStream()


def test_pinned_network_backend_connects_to_the_resolved_address_not_the_hostname() -> None:
    delegate = _RecordingDelegateBackend()
    backend = _PinnedResolutionNetworkBackend(lambda host: _PUBLIC_ADDRESS, delegate=delegate)

    backend.connect_tcp("public.example.com", 443)

    assert delegate.connected_to == (_PUBLIC_ADDRESS[0], 443)


def test_pinned_network_backend_rejects_a_rebound_private_address_before_connecting() -> None:
    delegate = _RecordingDelegateBackend()
    backend = _PinnedResolutionNetworkBackend(lambda host: _PRIVATE_ADDRESS, delegate=delegate)

    with pytest.raises(SourceFetchFailedError):
        backend.connect_tcp("rebound.example.com", 443)

    assert delegate.connect_attempted is False


def test_pinned_transport_performs_a_real_request_over_loopback() -> None:
    """Construction/request compatibility check (review finding S3-R05) against the locked
    `httpx`/`httpcore` versions: `_PinnedHttpTransport` is built only from public API, so this
    proves it actually connects, sends, and receives over a real socket -- not just against
    `httpx.MockTransport`, which bypasses the transport entirely. Loopback only, no external
    network; the SSRF host-validation path is exercised separately in the tests above."""
    import http.server
    import threading

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 -- required override name
            body = b"hello from loopback"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        # `network_backend` bypasses the pinned resolver's public-address-only check so this
        # test exercises the transport's request/response wiring against a real socket -- SSRF
        # host validation is covered independently by the tests above.
        transport = _PinnedHttpTransport(
            resolve_hostname=lambda host: ("127.0.0.1",),
            network_backend=httpcore.SyncBackend(),
        )
        with httpx.Client(transport=transport) as client:
            response = client.send(
                client.build_request("GET", f"http://127.0.0.1:{port}/"), stream=True
            )
            body = b"".join(response.iter_bytes())
            response.close()

        assert response.status_code == 200
        assert body == b"hello from loopback"
    finally:
        server.shutdown()
        thread.join(timeout=5)

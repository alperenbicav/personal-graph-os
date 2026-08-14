"""Real Telegram Bot API client (EP-2026-012 ST-11): the narrowest transport surface the
long-polling ingress needs.

Exposes `getUpdates` (long-polled, message updates only) and `sendMessage` (acknowledgements).
Telegram is an ingress channel only -- the bot never deletes/edits anything it did not itself
send as an ack, and there is deliberately no other write surface. The token is sent only as the
API path prefix (`/bot<token>/`); it is never logged or stored.
"""

from __future__ import annotations

import json

import httpx

from personal_graph_os.application.telegram_adapters import (
    TelegramAccessDeniedError,
    TelegramFetchFailedError,
    TelegramRateLimitedError,
    TelegramUpdate,
)

DEFAULT_TELEGRAM_API_URL = "https://api.telegram.org"
_DEFAULT_TIMEOUT_SECONDS = 30.0
_NON_SUCCESSFUL_HTTP_ERRORS = (AttributeError, KeyError, TypeError, ValueError)


class HttpTelegramClient:
    """`TelegramClient` backed by the Telegram Bot HTTP API over `httpx`.

    `transport` is a testability seam only (mirroring the provider factories and the ClickUp
    client): real callers never pass it; tests inject an `httpx.MockTransport`.
    """

    def __init__(
        self,
        *,
        api_token: str,
        allowed_chat_ids: frozenset[int],
        api_url: str = DEFAULT_TELEGRAM_API_URL,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._allowed_chat_ids = allowed_chat_ids
        self._base_url = f"{api_url.rstrip('/')}/bot{api_token}"
        self._client = httpx.Client(timeout=timeout_seconds, transport=transport)

    def is_chat_allowed(self, chat_id: int) -> bool:
        return chat_id in self._allowed_chat_ids

    def get_updates(
        self, *, offset: int | None, timeout_seconds: int
    ) -> tuple[TelegramUpdate, ...]:
        url = f"{self._base_url}/getUpdates"
        params = {"allowed_updates": json.dumps(["message"]), "timeout": str(timeout_seconds)}
        if offset is not None:
            params["offset"] = str(offset)
        try:
            response = self._client.get(url, params=params)
        except httpx.TimeoutException as error:
            raise TelegramFetchFailedError("Telegram getUpdates timed out") from error
        except httpx.HTTPError:
            # The raw exception can echo the request URL (which carries the token in its path),
            # so it is deliberately never stringified (S11-F04).
            raise TelegramFetchFailedError("Telegram getUpdates failed: transport error") from None

        payload = self._parse_response("getUpdates", response)
        result = payload.get("result")
        if not isinstance(result, list):
            raise TelegramFetchFailedError(
                "Telegram getUpdates returned no result list in the response body"
            )
        try:
            return tuple(TelegramUpdate.model_validate(item) for item in result)
        except _NON_SUCCESSFUL_HTTP_ERRORS as error:
            raise TelegramFetchFailedError(
                f"Telegram getUpdates returned updates in an unexpected shape: {error}"
            ) from error

    def send_message(self, chat_id: int, text: str) -> None:
        url = f"{self._base_url}/sendMessage"
        try:
            response = self._client.post(url, json={"chat_id": chat_id, "text": text})
        except httpx.TimeoutException as error:
            raise TelegramFetchFailedError("Telegram sendMessage timed out") from error
        except httpx.HTTPError:
            # Never stringify the raw exception: it can echo the token-carrying request URL
            # (S11-F04).
            raise TelegramFetchFailedError("Telegram sendMessage failed: transport error") from None

        self._parse_response("sendMessage", response)

    def download_file(self, file_id: str) -> bytes:
        """Download a file referenced by `file_id` (a photo/document) via `getFile` + the file
        endpoint. The file path is fetched over the token-carrying base URL, so transport errors
        are never stringified (S11-F04); the bytes themselves carry no token."""
        file_url = f"{self._base_url}/getFile"
        try:
            response = self._client.get(file_url, params={"file_id": file_id})
        except httpx.HTTPError:
            raise TelegramFetchFailedError("Telegram getFile failed: transport error") from None
        payload = self._parse_response("getFile", response)
        result = payload.get("result")
        file_path = result.get("file_path") if isinstance(result, dict) else None
        if not isinstance(file_path, str) or not file_path:
            raise TelegramFetchFailedError(
                "Telegram getFile returned no file_path for the requested file"
            )

        api_root = self._base_url.split("/bot", 1)[0]
        bot_suffix = self._base_url[self._base_url.index("/bot") :]
        download_url = f"{api_root}/file{bot_suffix}/{file_path}"
        try:
            file_response = self._client.get(download_url)
        except httpx.HTTPError:
            raise TelegramFetchFailedError(
                "Telegram file download failed: transport error"
            ) from None
        if file_response.status_code != 200:
            raise TelegramFetchFailedError(
                f"Telegram file download returned HTTP {file_response.status_code}"
            )
        return file_response.content

    def _parse_response(self, method: str, response: httpx.Response) -> dict[str, object]:
        if response.status_code in (401, 403):
            raise TelegramAccessDeniedError(
                f"Telegram API rejected the configured token (HTTP {response.status_code})"
            )
        if response.status_code == 429:
            raise TelegramRateLimitedError("Telegram API rate-limited the request (HTTP 429)")
        if response.status_code != 200:
            raise TelegramFetchFailedError(
                f"Telegram {method} returned HTTP {response.status_code}"
            )
        try:
            payload = response.json()
        except json.JSONDecodeError as error:
            raise TelegramFetchFailedError(
                f"Telegram {method} did not return valid JSON"
            ) from error
        if payload.get("ok") is not True:
            description = payload.get("description")
            raise TelegramFetchFailedError(
                f"Telegram {method} failed: {description or 'ok:false in the response body'}"
            )
        return payload

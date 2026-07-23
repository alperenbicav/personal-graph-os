"""Bearer-token enforcement for the mounted `/mcp` ASGI app.

The MCP Streamable HTTP transport is mounted as a raw ASGI sub-application, so FastAPI's
`Depends(require_api_token)` dependency injection never runs for it. This wraps that
sub-app so every request — including `initialize`, capability discovery, and every
resource/tool call — is rejected before it reaches the MCP session manager, using the
same local bearer token REST already enforces (product decision 11: no OAuth/RBAC/legacy
transport, one shared local capability token).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class BearerTokenASGIMiddleware:
    """Rejects any HTTP request without a matching `Authorization: Bearer` token."""

    def __init__(self, app: ASGIApp, token_provider: Callable[[], str]) -> None:
        self._app = app
        self._token_provider = token_provider

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        headers = dict(scope.get("headers") or ())
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        if not authorization.startswith("Bearer "):
            await JSONResponse({"detail": "Missing bearer token"}, status_code=401)(
                scope, receive, send
            )
            return

        provided = authorization.removeprefix("Bearer ").strip()
        expected = self._token_provider()
        if not provided or not secrets.compare_digest(provided, expected):
            await JSONResponse({"detail": "Invalid bearer token"}, status_code=401)(
                scope, receive, send
            )
            return

        await self._app(scope, receive, send)


def with_bearer_token(app: ASGIApp, token_provider: Callable[[], str]) -> ASGIApp:
    return BearerTokenASGIMiddleware(app, token_provider)

"""Local bearer-token authentication for the HTTP API.

The product contract requires every API/MCP write to require token authentication and
forbids an unauthenticated write surface, even on a loopback-bound local server (a local
process, malware, or a misconfigured bind could otherwise reach it). This module issues a
random token on first run, persists it next to the workspace database, and enforces it on
every route — reads included, since an unauthenticated read is still an information
disclosure the product contract does not carve out an exception for.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import Header, HTTPException, Request

TOKEN_FILE_NAME = "api-token"


def get_or_create_api_token(token_path: Path) -> str:
    """Return the persisted local API token, generating and storing one if absent."""
    if token_path.exists():
        existing = token_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing

    token = secrets.token_urlsafe(32)
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(token, encoding="utf-8")
    try:
        token_path.chmod(0o600)
    except OSError:
        # Best-effort on platforms/filesystems that do not support POSIX permissions;
        # the token itself is still required for every request either way.
        pass
    return token


async def require_api_token(
    request: Request, authorization: str | None = Header(default=None)
) -> None:
    """FastAPI dependency: fail closed unless a matching `Authorization: Bearer` token
    is present. Applied to every router so there is no unauthenticated route."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")

    provided = authorization.removeprefix("Bearer ").strip()
    expected: str = request.app.state.api_token
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid bearer token")

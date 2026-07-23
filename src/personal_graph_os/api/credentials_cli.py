"""Local credential CLI: `pgos-token show` and `pgos-token rotate`.

The running server (`api/__main__.py`) never prints the bearer token on startup; this is
the only supported way to read or change it. Rotation only replaces the token file — apply
it with the documented stop/rotate/restart sequence, since a live server process holds its
token in memory for its whole lifetime.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from personal_graph_os.api.app import DEFAULT_DATABASE_PATH
from personal_graph_os.api.auth import TOKEN_FILE_NAME, get_or_create_api_token, rotate_api_token


def _token_path(database_path: Path) -> Path:
    return database_path.parent / TOKEN_FILE_NAME


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="pgos-token", description=__doc__)
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DEFAULT_DATABASE_PATH,
        help="Workspace database path; the token file lives alongside it (default: %(default)s)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("show", help="Print the current bearer token")
    subparsers.add_parser("rotate", help="Replace the bearer token; requires a server restart")

    args = parser.parse_args(argv)
    token_path = _token_path(args.db_path)

    if args.command == "show":
        print(get_or_create_api_token(token_path))
    else:
        token = rotate_api_token(token_path)
        print(token)
        print("Token rotated. Stop the running server, then restart it to use this token.")


if __name__ == "__main__":
    main()

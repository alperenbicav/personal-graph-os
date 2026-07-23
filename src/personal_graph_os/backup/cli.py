"""`backup create|verify|restore` -- a plain standard-library CLI over `backup.service`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from personal_graph_os.backup.service import (
    BackupError,
    create_backup,
    restore_backup,
    verify_backup,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pgos-backup", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser(
        "create", help="Create a backup of a workspace directory."
    )
    create_parser.add_argument("workspace_dir", type=Path)
    create_parser.add_argument("output_dir", type=Path)

    verify_parser = subparsers.add_parser("verify", help="Verify a backup archive's integrity.")
    verify_parser.add_argument("backup_path", type=Path)

    restore_parser = subparsers.add_parser(
        "restore", help="Restore a backup archive into an empty destination directory."
    )
    restore_parser.add_argument("backup_path", type=Path)
    restore_parser.add_argument("destination_dir", type=Path)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "create":
            backup_path = create_backup(args.workspace_dir, args.output_dir)
            print(backup_path)
        elif args.command == "verify":
            verify_backup(args.backup_path)
            print(f"OK: {args.backup_path} is valid")
        elif args.command == "restore":
            restore_backup(args.backup_path, args.destination_dir)
            print(f"OK: restored into {args.destination_dir}")
    except BackupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

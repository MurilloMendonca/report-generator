"""Operator commands.

    python -m app.cli migrate
    python -m app.cli create-demo-user --email demo@gengardev.com.br
    python -m app.cli import-legacy --owner demo@gengardev.com.br [--dry-run]
"""
import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("migrate", help="Bring the database schema up to date")

    demo = commands.add_parser("create-demo-user", help="Create the demo account that owns imported reports")
    demo.add_argument("--email", required=True)

    legacy = commands.add_parser("import-legacy", help="Import workspace/sessions/*.json and the template library")
    legacy.add_argument("--owner", required=True, help="Email of the account that will own the reports")
    legacy.add_argument("--workspace", default="workspace", type=Path)
    legacy.add_argument("--dry-run", action="store_true", help="Report what would be imported, change nothing")

    args = parser.parse_args(argv)

    from app import db
    from app.services import legacy_import

    if args.command == "migrate":
        db.get_engine()  # migrates on first use
        print("Database schema is up to date.")
    elif args.command == "create-demo-user":
        user, created = legacy_import.ensure_demo_user(args.email)
        print(f"{'Created' if created else 'Already exists'}: {user.email} (id {user.id})")
        print("Its password is set in Phase 2 (sign-in); until then nobody can log in as it.")
    elif args.command == "import-legacy":
        try:
            summary = legacy_import.import_legacy(args.owner, workspace=args.workspace, dry_run=args.dry_run)
        except LookupError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(summary.as_text())
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("alembic").setLevel(logging.WARNING)
    sys.exit(main())

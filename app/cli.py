"""Operator commands.

    python -m app.cli migrate
    python -m app.cli create-admin --email you@example.com
    python -m app.cli create-user --email someone@example.com [--admin]
    python -m app.cli set-password --email someone@example.com
    python -m app.cli create-demo-user --email demo@gengardev.com.br
    python -m app.cli import-legacy --owner demo@gengardev.com.br [--dry-run]
    python -m app.cli assign-unowned --owner demo@gengardev.com.br

Accounts made here are already verified. Passwords are generated and printed
once; nothing stores them in plain text.
"""
import argparse
import logging
import secrets
import sys
from pathlib import Path

from dotenv import load_dotenv


def _generated_password() -> str:
    return secrets.token_urlsafe(18)


def _print_password(email: str, password: str) -> None:
    print(f"Password for {email}: {password}")
    print("It is shown only once. Sign in and change it under Conta > Senha.")


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("migrate", help="Bring the database schema up to date")

    admin = commands.add_parser("create-admin", help="Create an admin account, or make an existing account admin")
    admin.add_argument("--email", required=True)

    user = commands.add_parser("create-user", help="Create a verified account (works while sign-up is closed)")
    user.add_argument("--email", required=True)
    user.add_argument("--name", default="")
    user.add_argument("--admin", action="store_true")

    password = commands.add_parser("set-password", help="Generate a new password and sign the account out everywhere")
    password.add_argument("--email", required=True)

    demo = commands.add_parser("create-demo-user", help="Create the demo account that owns imported reports")
    demo.add_argument("--email", required=True)

    legacy = commands.add_parser("import-legacy", help="Import workspace/sessions/*.json and the template library")
    legacy.add_argument("--owner", required=True, help="Email of the account that will own the reports")
    legacy.add_argument("--workspace", default="workspace", type=Path)
    legacy.add_argument("--dry-run", action="store_true", help="Report what would be imported, change nothing")

    unowned = commands.add_parser("assign-unowned", help="Give reports without an owner to an account")
    unowned.add_argument("--owner", required=True)

    args = parser.parse_args(argv)

    from app import db
    from app.services import auth_service, legacy_import
    from app.services.auth_service import AuthError

    try:
        if args.command == "migrate":
            db.get_engine()  # migrates on first use
            print("Database schema is up to date.")

        elif args.command in {"create-admin", "create-user"}:
            make_admin = args.command == "create-admin" or args.admin
            existing = auth_service.get_user_by_email(args.email)
            if existing is not None:
                if make_admin and existing.role != "admin":
                    auth_service.set_role(existing.id, "admin")
                    print(f"{existing.email} is now an admin.")
                else:
                    print(f"{existing.email} already exists; use set-password to give it a new password.")
                return 0
            new_password = _generated_password()
            created = auth_service.create_user(
                args.email, new_password, display_name=getattr(args, "name", ""),
                role="admin" if make_admin else "user", verified=True,
            )
            print(f"Created {'admin' if make_admin else 'user'} {created.email} (id {created.id})")
            _print_password(created.email, new_password)

        elif args.command == "set-password":
            existing = auth_service.get_user_by_email(args.email)
            if existing is None:
                print(f"No account for {args.email!r}.", file=sys.stderr)
                return 1
            new_password = _generated_password()
            auth_service.set_password(existing.id, new_password)
            _print_password(existing.email, new_password)

        elif args.command == "create-demo-user":
            demo_user, created = legacy_import.ensure_demo_user(args.email)
            print(f"{'Created' if created else 'Already exists'}: {demo_user.email} (id {demo_user.id})")
            if created or demo_user.password_hash is None:
                new_password = _generated_password()
                auth_service.set_password(demo_user.id, new_password)
                _print_password(demo_user.email, new_password)

        elif args.command == "import-legacy":
            try:
                summary = legacy_import.import_legacy(args.owner, workspace=args.workspace, dry_run=args.dry_run)
            except LookupError as exc:
                print(exc, file=sys.stderr)
                return 1
            print(summary.as_text())

        elif args.command == "assign-unowned":
            owner = auth_service.get_user_by_email(args.owner)
            if owner is None:
                print(f"No account for {args.owner!r}.", file=sys.stderr)
                return 1
            print(f"Reports assigned to {owner.email}: {legacy_import.assign_unowned(owner.id)}")
    except AuthError as exc:
        print(exc.message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("alembic").setLevel(logging.WARNING)
    sys.exit(main())

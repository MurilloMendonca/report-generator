"""List models with an explicit key (or a hidden prompt), never a server env key."""
import argparse
from getpass import getpass

from app.services.credential_service import CredentialValidationError, validate_key
from app.services.llm.providers.registry import ADAPTERS


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=ADAPTERS, required=True)
    parser.add_argument("--api-key", help="Provider key; omit to enter it at a hidden prompt")
    args = parser.parse_args()
    try:
        models = validate_key(args.provider, args.api_key or getpass("API key: "))
    except CredentialValidationError as exc:
        print(str(exc))
        return 1
    print("\n".join(models))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

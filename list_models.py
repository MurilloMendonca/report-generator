"""List models with an explicit key/hidden prompt, or a keyless custom endpoint."""
import argparse
from getpass import getpass

from app.services.credential_service import CredentialValidationError, validate_key
from app.services.llm.providers.registry import ADAPTERS, get_adapter


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=ADAPTERS, required=True)
    parser.add_argument("--api-key", help="Provider key; omit to enter it at a hidden prompt")
    args = parser.parse_args()
    try:
        adapter = get_adapter(args.provider)
        key = args.api_key or (getpass("API key: ") if adapter.requires_api_key else "not-required")
        models = validate_key(args.provider, key)
    except CredentialValidationError as exc:
        print(str(exc))
        return 1
    print("\n".join(models))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""AES-256-GCM envelope encryption. Master keys never live in the database."""
import base64
import os
import re
from dataclasses import dataclass, replace
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class EncryptionConfigError(RuntimeError):
    pass


@dataclass(frozen=True, repr=False)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    wrapped_dek: bytes
    dek_nonce: bytes
    kek_version: int


def keyring() -> tuple[dict[int, bytes], int]:
    """Read a versioned keyring from env or a mounted Docker secret."""
    try:
        path = os.getenv("APP_ENCRYPTION_KEYS_FILE")
        raw = Path(path).read_text().strip() if path else os.getenv("APP_ENCRYPTION_KEYS", "")
        keys = {}
        for item in raw.split(","):
            version, encoded = item.strip().split(":", 1)
            if not re.fullmatch(r"v[1-9][0-9]*", version):
                raise ValueError
            number = int(version[1:])
            key = base64.b64decode(encoded, validate=True)
            if len(key) != 32 or number in keys:
                raise ValueError
            keys[number] = key
        active = os.getenv("APP_ENCRYPTION_ACTIVE_KEY", "v1")
        if not re.fullmatch(r"v[1-9][0-9]*", active) or int(active[1:]) not in keys:
            raise ValueError
        return keys, int(active[1:])
    except (OSError, ValueError, TypeError):
        raise EncryptionConfigError("Configure APP_ENCRYPTION_KEYS (or APP_ENCRYPTION_KEYS_FILE) and APP_ENCRYPTION_ACTIVE_KEY with versioned 32-byte base64 keys.") from None


def encrypt_secret(secret: str, aad: bytes) -> EncryptedSecret:
    keys, active = keyring()
    dek = AESGCM.generate_key(bit_length=256)
    nonce, dek_nonce = os.urandom(12), os.urandom(12)
    return EncryptedSecret(
        AESGCM(dek).encrypt(nonce, secret.encode(), aad), nonce,
        AESGCM(keys[active]).encrypt(dek_nonce, dek, aad), dek_nonce, active,
    )


def _unwrap(envelope: EncryptedSecret, aad: bytes, keys: dict[int, bytes]) -> bytes:
    if envelope.kek_version not in keys:
        raise EncryptionConfigError("A stored credential requires an unavailable encryption key version.")
    return AESGCM(keys[envelope.kek_version]).decrypt(envelope.dek_nonce, envelope.wrapped_dek, aad)


def decrypt_secret(envelope: EncryptedSecret, aad: bytes) -> str:
    keys, _ = keyring()
    dek = _unwrap(envelope, aad, keys)
    return AESGCM(dek).decrypt(envelope.nonce, envelope.ciphertext, aad).decode()


def rewrap(envelope: EncryptedSecret, aad: bytes) -> EncryptedSecret:
    """Rotate only the wrapping key; the API-key ciphertext stays unchanged."""
    keys, active = keyring()
    dek = _unwrap(envelope, aad, keys)
    nonce = os.urandom(12)
    return replace(envelope, wrapped_dek=AESGCM(keys[active]).encrypt(nonce, dek, aad), dek_nonce=nonce, kek_version=active)


def dev_server_keys_enabled() -> bool:
    enabled = os.getenv("PDF_REPORT_DEV_SERVER_KEYS", "0") == "1"
    if enabled and os.getenv("APP_ENV", "development").lower() not in {"development", "dev", "local", "test"}:
        raise EncryptionConfigError("PDF_REPORT_DEV_SERVER_KEYS is forbidden in production.")
    return enabled


def validate_configuration() -> None:
    dev_server_keys_enabled()
    if (os.getenv("APP_ENV", "development").lower() not in {"development", "dev", "local", "test"}
            or os.getenv("APP_ENCRYPTION_KEYS") or os.getenv("APP_ENCRYPTION_KEYS_FILE")):
        keyring()

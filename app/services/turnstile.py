"""Cloudflare Turnstile verification. Disabled when the keys aren't configured (development)."""
import logging
import os

import httpx

logger = logging.getLogger(__name__)
VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def site_key() -> str:
    return os.getenv("TURNSTILE_SITE_KEY", "").strip()


def enabled() -> bool:
    return bool(site_key() and os.getenv("TURNSTILE_SECRET_KEY", "").strip())


def verify(token: str | None, remote_ip: str | None = None) -> bool:
    if not enabled():
        return True
    if not token or len(token) > 2048:
        return False
    data = {"secret": os.getenv("TURNSTILE_SECRET_KEY", "").strip(), "response": token}
    if remote_ip and remote_ip != "unknown":
        data["remoteip"] = remote_ip
    try:
        response = httpx.post(VERIFY_URL, data=data, timeout=10)
        return bool(response.json().get("success"))
    except (httpx.HTTPError, ValueError):
        logger.exception("Turnstile verification failed")
        return False

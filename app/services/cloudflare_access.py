"""Validate Access's signed assertion against a configured issuer and audience."""
import os
import re
from functools import lru_cache

import jwt
from fastapi import HTTPException


@lru_cache(maxsize=4)
def _jwks(issuer):
    return jwt.PyJWKClient(f"{issuer}/cdn-cgi/access/certs", timeout=5, lifespan=300)


def configuration():
    issuer = os.getenv("CLOUDFLARE_ACCESS_ISSUER", "").strip().rstrip("/")
    audience = os.getenv("CLOUDFLARE_ACCESS_AUD", "").strip()
    valid = bool(re.fullmatch(r"https://[a-z0-9-]+\.cloudflareaccess\.com", issuer) and audience)
    return issuer, audience, valid


def require_access(request, user):
    production = os.getenv("APP_ENV", "development").lower() not in {"dev", "development", "local", "test"}
    issuer, audience, valid = configuration()
    required = production or os.getenv("PDF_REPORT_REQUIRE_CLOUDFLARE_ACCESS", "0") == "1" or bool(issuer or audience)
    if not required:
        return
    if not valid:
        raise HTTPException(503, detail="Configure o Cloudflare Access para acessar a administração.")
    assertion = request.headers.get("Cf-Access-Jwt-Assertion", "")
    if not assertion or len(assertion) > 16384:
        raise HTTPException(403, detail="Cloudflare Access obrigatório.")
    try:
        key = _jwks(issuer).get_signing_key_from_jwt(assertion).key
        claims = jwt.decode(assertion, key, algorithms=["RS256"], issuer=issuer, audience=audience,
                            options={"require": ["exp", "iss", "aud", "email", "sub"]})
        if str(claims["email"]).casefold() != user.email.casefold():
            raise jwt.InvalidTokenError("Identity mismatch")
    except (jwt.PyJWTError, ValueError, TypeError):
        raise HTTPException(403, detail="Cloudflare Access inválido ou expirado.") from None

"""Check a Supabase sign-in token.

The browser signs in with Supabase and gets an "access token" (a long string). It sends that token
with every API call:   Authorization: Bearer <token>
We never trust it by itself. We ask Supabase "who does this token belong to?" and use the answer.
The answer is cached for a minute so we do not call Supabase on every single request.
"""
import hashlib
import logging

import httpx

from app.config import settings
from app.memory import cache

log = logging.getLogger("sahyog")
CACHE_SECONDS = 60


def enabled() -> bool:
    return bool(settings.supabase_url and settings.supabase_publishable_key)


def verify_token(token: str) -> dict | None:
    """Return {"id", "email", "name"} for a valid token, or None if the token is bad or Supabase is unreachable."""
    if not enabled() or not token:
        return None
    key = "sbtoken:" + hashlib.sha256(token.encode()).hexdigest()  # never store the raw token
    hit = cache.get(key)
    if hit:
        return hit
    try:
        res = httpx.get(f"{settings.supabase_url}/auth/v1/user", timeout=5,
                        headers={"apikey": settings.supabase_publishable_key, "Authorization": f"Bearer {token}"})
    except httpx.HTTPError as err:
        log.warning("Supabase unreachable while checking a token: %s", err)
        return None
    if res.status_code != 200:
        return None
    data = res.json()
    meta = data.get("user_metadata") or {}
    email = data.get("email") or ""
    who = {"id": data["id"], "email": email, "name": meta.get("full_name") or meta.get("name") or email.split("@")[0] or "Guest"}
    cache.set(key, who, CACHE_SECONDS)
    return who

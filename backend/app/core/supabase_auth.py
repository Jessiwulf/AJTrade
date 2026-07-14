import os
import time
from typing import Dict, Any, Optional

import httpx
from jose import jwt

# Simple cached jwks
_JWKS_CACHE: Dict[str, Any] = {"jwks": None, "fetched_at": 0}


def _get_supabase_url() -> str:
    url = os.environ.get("SUPABASE_URL", "").strip()
    if not url:
        raise ValueError("SUPABASE_URL is not set")
    return url[:-1] if url.endswith('/') else url


def _get_supabase_anon_key() -> str:
    key = os.environ.get("SUPABASE_ANON_KEY", "").strip()
    if not key:
        raise ValueError("SUPABASE_ANON_KEY is not set")
    return key


def _jwks_url() -> str:
    base = _get_supabase_url()
    return f"{base}/auth/v1/.well-known/jwks.json"


def _fetch_jwks(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force and _JWKS_CACHE.get("jwks") and (now - _JWKS_CACHE.get("fetched_at", 0) < 3600):
        return _JWKS_CACHE["jwks"]
    url = _jwks_url()
    try:
        r = httpx.get(url, timeout=10.0)
        r.raise_for_status()
        jwks = r.json()
    except Exception:
        # fallback to root well-known
        try:
            r = httpx.get(f"{_get_supabase_url()}/.well-known/jwks.json", timeout=10.0)
            r.raise_for_status()
            jwks = r.json()
        except Exception:
            jwks = {"keys": []}
    _JWKS_CACHE["jwks"] = jwks
    _JWKS_CACHE["fetched_at"] = now
    return jwks


def sign_up(email: str, password: str) -> Dict[str, Any]:
    """Call Supabase Auth signup endpoint."""
    base = _get_supabase_url()
    anon_key = _get_supabase_anon_key()
    url = f"{base}/auth/v1/signup"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }
    payload = {"email": email, "password": password}
    r = httpx.post(url, json=payload, headers=headers, timeout=10.0)
    r.raise_for_status()
    return r.json()


def sign_in(email: str, password: str) -> Dict[str, Any]:
    """Exchange credentials for tokens via Supabase token endpoint."""
    base = _get_supabase_url()
    anon_key = _get_supabase_anon_key()
    url = f"{base}/auth/v1/token"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }
    payload = {"email": email, "password": password}
    r = httpx.post(f"{url}?grant_type=password", json=payload, headers=headers, timeout=10.0)
    r.raise_for_status()
    return r.json()


def send_password_reset(email: str) -> Dict[str, Any]:
    base = _get_supabase_url()
    anon_key = _get_supabase_anon_key()
    url = f"{base}/auth/v1/recover"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {anon_key}",
        "Content-Type": "application/json",
    }
    payload = {"email": email}
    r = httpx.post(url, json=payload, headers=headers, timeout=10.0)
    r.raise_for_status()
    return r.json()


def update_password_with_token(token: str, new_password: str) -> Dict[str, Any]:
    base = _get_supabase_url()
    anon_key = _get_supabase_anon_key()
    url = f"{base}/auth/v1/user"
    headers = {
        "apikey": anon_key,
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {"password": new_password}
    r = httpx.put(url, json=payload, headers=headers, timeout=10.0)
    r.raise_for_status()
    return r.json()


def verify_jwt(token: str) -> Optional[Dict[str, Any]]:
    """Verify Supabase JWT using JWKs. Returns payload or None on failure."""
    if not token:
        return None
    try:
        unverified = jwt.get_unverified_header(token)
        kid = unverified.get("kid")
        alg = unverified.get("alg")
        jwks = _fetch_jwks()
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                key = k
                break
        if not key:
            # refresh once
            jwks = _fetch_jwks(force=True)
            for k in jwks.get("keys", []):
                if k.get("kid") == kid:
                    key = k
                    break
        if not key:
            return None
        # Decode and verify.
        # Supabase may sign JWTs with ES256 (EC) or RS256 depending on project settings.
        allowed_algs = {"ES256", "RS256"}
        key_alg = key.get("alg")
        alg_to_use = (alg or key_alg or "").strip()
        if not alg_to_use:
            return None
        if alg_to_use not in allowed_algs:
            return None
        if key_alg and key_alg != alg_to_use:
            return None

        issuer = f"{_get_supabase_url()}/auth/v1"
        payload = jwt.decode(
            token,
            key,
            algorithms=[alg_to_use],
            issuer=issuer,
            options={"verify_aud": False},
        )
        return payload
    except Exception:
        return None

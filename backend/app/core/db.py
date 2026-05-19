import os
import ssl
import socket
from typing import Optional

from urllib.parse import parse_qs, urlparse

import databases

DATABASE_URL = os.environ.get('DATABASE_URL', 'postgresql://ajtrade:ajtrade@localhost:5432/ajtrade_db')

database: Optional[databases.Database] = None


def _validate_database_url(database_url: str) -> None:
    # Common misconfig: users paste the Supabase REST URL (https://.../rest/v1/...) instead of a Postgres URI.
    parsed = urlparse(database_url)
    scheme = (parsed.scheme or '').lower()
    allowed = {'postgresql', 'postgres', 'postgresql+asyncpg', 'postgres+asyncpg'}
    if scheme and scheme not in allowed:
        raise ValueError(
            "Invalid DATABASE_URL scheme. Expected a Postgres connection string like "
            "postgresql://user:password@host:5432/dbname?sslmode=require (from Supabase Database settings). "
            f"Got scheme '{scheme}'."
        )

    host = parsed.hostname
    port = parsed.port or 5432
    if host and scheme in allowed:
        # Fast check for IPv4 availability. Some Supabase DB endpoints may be IPv6-only (AAAA record only).
        # Docker Desktop commonly runs containers on IPv4-only networks, causing 'Name or service not known'
        # or 'Network is unreachable' when trying to connect.
        try:
            socket.getaddrinfo(host, port, family=socket.AF_INET)
        except socket.gaierror:
            raise ValueError(
                "DATABASE_URL host has no IPv4 (A) record. If you're running the backend in Docker Desktop, "
                "containers are often IPv4-only and cannot reach IPv6-only DB hosts. "
                "Use the Supabase 'Connection pooling' (pooler) connection string, or enable IPv6 for Docker. "
                f"Host: {host}"
            )


def _needs_ssl(database_url: str) -> bool:
    try:
        qs = parse_qs(urlparse(database_url).query)
        sslmode = (qs.get('sslmode', [None])[0] or '').lower()
        if sslmode in {'require', 'verify-ca', 'verify-full'}:
            return True
        ssl_param = (qs.get('ssl', [None])[0] or '').lower()
        if ssl_param in {'1', 'true', 'yes', 'require'}:
            return True
    except Exception:
        return False
    return False


def _ssl_context_for(database_url: str) -> ssl.SSLContext:
    """Create an SSL context that matches libpq-style sslmode semantics.

    - sslmode=require: encrypt transport but do NOT verify server cert.
    - sslmode=verify-ca / verify-full: verify server cert (and hostname for verify-full).
    """
    qs = parse_qs(urlparse(database_url).query)
    sslmode = (qs.get('sslmode', [None])[0] or '').lower()
    ssl_param = (qs.get('ssl', [None])[0] or '').lower()

    if sslmode in {'verify-ca', 'verify-full'}:
        ctx = ssl.create_default_context()
        if sslmode == 'verify-ca':
            ctx.check_hostname = False
        return ctx

    if sslmode in {'require', 'prefer', 'allow'} or ssl_param in {'1', 'true', 'yes', 'require'}:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    # Fallback: default context (safe) — should rarely be reached because callers only use this
    # when _needs_ssl() is true.
    return ssl.create_default_context()


def get_database() -> databases.Database:
    global database
    if database is None:
        _validate_database_url(DATABASE_URL)
        if _needs_ssl(DATABASE_URL):
            database = databases.Database(DATABASE_URL, ssl=_ssl_context_for(DATABASE_URL))
        else:
            database = databases.Database(DATABASE_URL)
    return database

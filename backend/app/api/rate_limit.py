from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict

from fastapi import HTTPException, Request, status

from app.api.dependencies import get_current_user


MAX_GUEST_LLM_REQUESTS_PER_DAY = 5
WINDOW_SECONDS = 24 * 60 * 60


@dataclass
class RateLimitBucket:
    window_start: float
    count: int = 0


_BUCKETS: Dict[str, RateLimitBucket] = {}
_LOCK = asyncio.Lock()


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get('x-forwarded-for', '')
    if forwarded_for:
        first = forwarded_for.split(',', 1)[0].strip()
        if first:
            return first

    real_ip = request.headers.get('x-real-ip', '').strip()
    if real_ip:
        return real_ip

    host = getattr(request.client, 'host', None)
    return host or 'unknown'


def _bucket_key(request: Request) -> str:
    return f'guest-llm:{_client_ip(request)}'


def _cleanup(now: float) -> None:
    expired = [key for key, bucket in _BUCKETS.items() if now - bucket.window_start >= WINDOW_SECONDS]
    for key in expired:
        _BUCKETS.pop(key, None)


async def enforce_guest_llm_rate_limit(request: Request):
    """Allow authenticated users through, but cap anonymous guest LLM traffic.

    Guests are counted by IP so the public LLM surface cannot be used for token
    abuse. Authenticated users bypass this limiter and should be governed by any
    user-scoped quota instead.
    """

    try:
        await get_current_user(request)
        return
    except HTTPException:
        pass

    now = time.time()
    key = _bucket_key(request)

    async with _LOCK:
        _cleanup(now)
        bucket = _BUCKETS.get(key)
        if not bucket or now - bucket.window_start >= WINDOW_SECONDS:
            _BUCKETS[key] = RateLimitBucket(window_start=now, count=1)
            return

        if bucket.count >= MAX_GUEST_LLM_REQUESTS_PER_DAY:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail='Guest LLM rate limit exceeded. Please sign in to continue.',
            )

        bucket.count += 1
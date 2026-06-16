from __future__ import annotations

from typing import Any, Callable, Dict

from fastapi import Depends, HTTPException, Request, status

from app.core import supabase_auth
from app.core.db import get_database


ROLE_ORDER = {
    'guest': 0,
    'authenticated_user': 1,
    'admin': 2,
}


def _extract_token(request: Request) -> str | None:
    token = request.cookies.get('session')
    if token:
        return token.strip() or None

    auth_header = request.headers.get('Authorization') or ''
    if auth_header.lower().startswith('bearer '):
        return auth_header.split(' ', 1)[1].strip() or None
    return None


async def _load_profile(user_id: str):
    try:
        db = get_database()
        return await db.fetch_one(
            query=(
                'SELECT id, full_name, avatar_url, role, suspended_at, created_at '
                'FROM profiles WHERE id = :id'
            ),
            values={'id': user_id},
        )
    except Exception:
        return None


async def get_current_user(request: Request) -> Dict[str, Any]:
    """Verify the Supabase JWT and attach the server-side role.

    The JWT proves identity. The profiles table is the source of truth for the
    app role and suspension state.
    """

    token = _extract_token(request)
    verify_jwt = getattr(supabase_auth, 'verify_jwt', None)
    if token and not callable(verify_jwt):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Auth service unavailable')

    payload = verify_jwt(token) if token else None
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')

    user_id = payload.get('sub')
    if not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')

    profile = await _load_profile(user_id)
    role = 'authenticated_user'
    suspended_at = None
    if profile:
        try:
            profile_role = profile['role']
        except Exception:
            profile_role = None
        if isinstance(profile_role, str):
            profile_role = profile_role.strip() or None
        # Guest is reserved for anonymous visitors only.
        # Once JWT verification succeeds, the user is an authenticated_user
        # unless they are explicitly marked admin in the database.
        if profile_role == 'admin':
            role = 'admin'
        try:
            suspended_at = profile['suspended_at']
        except Exception:
            suspended_at = None

    if suspended_at:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Account suspended')

    if role not in ROLE_ORDER:
        role = 'authenticated_user'

    payload['role'] = role
    payload['profile_id'] = user_id
    return payload


def require_role(required_role: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    required_role = (required_role or '').strip()
    if required_role not in ROLE_ORDER:
        raise ValueError(f'Unknown role: {required_role}')

    async def _dependency(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
        actual_role_value = user.get('role') or 'authenticated_user'
        actual_role = actual_role_value.strip() if isinstance(actual_role_value, str) else 'authenticated_user'
        actual_rank = ROLE_ORDER.get(actual_role, ROLE_ORDER['authenticated_user'])
        required_rank = ROLE_ORDER[required_role]
        if actual_rank < required_rank:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Forbidden')
        return user

    return _dependency
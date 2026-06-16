from fastapi import APIRouter, HTTPException, status, Request, Response, Depends
from pydantic import BaseModel, EmailStr

from app.api.dependencies import get_current_user
from app.core import supabase_auth
from app.core.db import get_database

router = APIRouter()


class SignUpPayload(BaseModel):
    email: EmailStr
    password: str


class SignInPayload(BaseModel):
    email: EmailStr
    password: str


class EmailPayload(BaseModel):
    email: EmailStr


class ProfileIn(BaseModel):
    full_name: str | None = None
    avatar_url: str | None = None


@router.post('/signup')
async def signup(payload: SignUpPayload):
    try:
        res = supabase_auth.sign_up(payload.email, payload.password)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return res


@router.post('/login')
async def login(payload: SignInPayload, response: Response, request: Request):
    try:
        res = supabase_auth.sign_in(payload.email, payload.password)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    # res typically contains access_token, refresh_token, expires_in
    access_token = res.get('access_token')
    refresh_token = res.get('refresh_token')
    expires_in = res.get('expires_in', 3600)
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Login failed')

    # Cookie security:
    # - Browsers ignore `Secure` cookies on plain HTTP (common in local dev),
    #   which makes subsequent /me calls look "Unauthorized".
    # - Default to Secure only when the request is HTTPS (or forwarded as HTTPS).
    import os

    secure_cookie_env = (os.environ.get('AJTRADE_COOKIE_SECURE') or '').strip().lower()
    if secure_cookie_env:
        secure_cookie = secure_cookie_env in ('1', 'true', 'yes')
    else:
        disable_secure = os.environ.get('AJTRADE_DEV_DISABLE_SECURE_COOKIE', '').lower() in (
            '1',
            'true',
            'yes',
        )
        if disable_secure:
            secure_cookie = False
        else:
            forwarded_proto = (request.headers.get('x-forwarded-proto') or '').split(',')[0].strip().lower()
            scheme = (forwarded_proto or request.url.scheme or '').lower()
            secure_cookie = scheme == 'https'
    response.set_cookie(
        key='session',
        value=access_token,
        httponly=True,
        secure=secure_cookie,
        samesite='lax',
        max_age=int(expires_in),
        path='/'
    )
    # For local development, optionally return tokens so the frontend can attach Authorization headers.
    import os
    if os.environ.get('AJTRADE_DEV_RETURN_TOKENS', '').lower() in ('1', 'true', 'yes'):
        return {
            'status': 'ok',
            'expires_in': expires_in,
            'access_token': access_token,
            'refresh_token': refresh_token,
        }
    return {'status': 'ok', 'expires_in': expires_in}


@router.post('/logout')
async def logout(response: Response):
    response.delete_cookie('session', path='/')
    return {'status': 'ok'}


@router.post('/password-reset')
async def password_reset(payload: EmailPayload):
    try:
        res = supabase_auth.send_password_reset(payload.email)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return res


def _profile_defaults(user: dict) -> dict:
    email = (user.get('email') or '').strip()
    full_name = user.get('full_name') or user.get('user_metadata', {}).get('full_name')
    if not full_name and email:
        full_name = email.split('@', 1)[0]
    avatar_url = user.get('avatar_url') or user.get('user_metadata', {}).get('avatar_url')
    return {
        'id': user.get('sub'),
        'full_name': full_name,
        'avatar_url': avatar_url,
        'email': email,
        'role': user.get('role') or 'authenticated_user',
    }


@router.get('/profile')
async def get_profile(user=Depends(get_current_user)):
    owner = user.get('sub')
    defaults = _profile_defaults(user)
    try:
        db = get_database()
        if owner:
            row = await db.fetch_one(
                query="SELECT id, full_name, avatar_url, created_at FROM profiles WHERE id = :id",
                values={'id': owner},
            )
        else:
            row = None
    except Exception as e:
        # Best effort: if the DB table is unavailable, fall back to auth metadata defaults.
        row = None

    if not row:
        return defaults

    return {
        'id': str(row['id']),
        'full_name': row['full_name'] or defaults['full_name'],
        'avatar_url': row['avatar_url'] or defaults['avatar_url'],
        'email': defaults['email'],
        'role': user.get('role') or defaults['role'],
        'created_at': str(row['created_at']) if row['created_at'] else None,
    }


@router.put('/profile')
async def update_profile(payload: ProfileIn, user=Depends(get_current_user)):
    owner = user.get('sub')
    full_name = (payload.full_name or '').strip() or None
    avatar_url = (payload.avatar_url or '').strip() or None

    try:
        db = get_database()
        owner = user.get('sub')
        if owner:
            await db.execute(
                query=(
                    "INSERT INTO profiles (id, full_name, avatar_url) "
                    "VALUES (:id, :full_name, :avatar_url) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "full_name = EXCLUDED.full_name, "
                    "avatar_url = EXCLUDED.avatar_url"
                ),
                values={'id': owner, 'full_name': full_name, 'avatar_url': avatar_url},
            )
            row = await db.fetch_one(
                query="SELECT id, full_name, avatar_url, created_at FROM profiles WHERE id = :id",
                values={'id': owner},
            )
        else:
            row = None
    except Exception:
        row = None

    defaults = _profile_defaults(user)
    return {
        'id': str(row['id']) if row else owner,
        'full_name': row['full_name'] if row else full_name or defaults['full_name'],
        'avatar_url': row['avatar_url'] if row else avatar_url or defaults['avatar_url'],
        'email': defaults['email'],
        'role': user.get('role') or defaults['role'],
        'created_at': str(row['created_at']) if row and row['created_at'] else None,
    }


@router.get('/me')
async def me(user=Depends(get_current_user)):
    return {'user': user, 'profile': _profile_defaults(user)}

from fastapi import APIRouter, HTTPException, status, Request, Response, Depends
from pydantic import BaseModel, EmailStr

from app.core import supabase_auth

router = APIRouter()


class SignUpPayload(BaseModel):
    email: EmailStr
    password: str


class SignInPayload(BaseModel):
    email: EmailStr
    password: str


class EmailPayload(BaseModel):
    email: EmailStr


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


def get_current_user(request: Request):
    # prefer cookie
    token = None
    if 'session' in request.cookies:
        token = request.cookies.get('session')
    else:
        auth = request.headers.get('Authorization')
        if auth and auth.lower().startswith('bearer '):
            token = auth.split(' ', 1)[1]
    payload = supabase_auth.verify_jwt(token) if token else None
    if not payload:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    return payload


@router.get('/me')
async def me(user=Depends(get_current_user)):
    return {'user': user}

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.api.dependencies import get_current_user
from app.core import supabase_auth

router = APIRouter()


class ChangePasswordIn(BaseModel):
    currentPassword: str
    newPassword: str
    confirmNewPassword: str


def _extract_token(request: Request) -> str | None:
    token = request.cookies.get('session')
    if token:
        return token.strip() or None

    auth_header = request.headers.get('Authorization') or ''
    if auth_header.lower().startswith('bearer '):
        return auth_header.split(' ', 1)[1].strip() or None
    return None


@router.put('/password')
async def change_password(
    payload: ChangePasswordIn,
    request: Request,
    user=Depends(get_current_user),
):
    current_password = payload.currentPassword or ''
    new_password = payload.newPassword or ''
    confirm_password = payload.confirmNewPassword or ''

    if len(new_password) < 6:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Password must be at least 6 characters.')

    if new_password != confirm_password:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Passwords do not match')

    email = (user.get('email') or '').strip()
    if not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Current password is incorrect.')

    try:
        supabase_auth.sign_in(email, current_password)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Current password is incorrect.')

    access_token = _extract_token(request)
    if not access_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Current password is incorrect.')

    try:
        supabase_auth.update_password_with_token(access_token, new_password)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))

    return {'message': 'Password Changed Successfully.'}

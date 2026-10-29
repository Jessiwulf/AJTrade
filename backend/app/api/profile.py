import asyncio

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


# --- Delete account (PDPA right to erasure) -------------------------------------------------
class DeleteAccountIn(BaseModel):
    password: str
    confirm: str


@router.delete('/account')
async def delete_account(payload: DeleteAccountIn, user=Depends(get_current_user)):
    """Deletes the member's account and all their data. Needs the current password and "DELETE"."""
    from app.core import account
    from app.core.db import get_database

    owner = user.get('sub')
    email = (user.get('email') or '').strip()
    if not owner or not email:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    if (payload.confirm or '').strip().upper() != 'DELETE':
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Type DELETE to confirm.')
    try:
        await asyncio.to_thread(supabase_auth.sign_in, email, payload.password or '')
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Current password is incorrect.')
    try:
        deleted = await account.delete_member(get_database(), owner)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f'Could not delete the account, nothing was removed: {exc}')
    return {'deleted': True, 'rows': sum(deleted.values())}


# --- Broker provider (Feature: Alpaca / Settrade switch) -------------------------------------
class BrokerProviderIn(BaseModel):
    provider: str


async def _provider_connections(owner: str) -> dict:
    """Which providers have credentials saved (names only; nothing is decrypted except the broker id)."""
    from app.core.db import get_database

    rows = await get_database().fetch_all(
        query="SELECT DISTINCT lower(service) AS service FROM encrypted_api_keys WHERE owner = :owner",
        values={"owner": owner},
    )
    services = {r["service"] for r in rows}
    settrade_ready = {"settrade_app_id", "settrade_app_secret", "settrade_account_no"} <= services
    environment = None
    if settrade_ready:
        try:
            from app.core.settrade import load_credentials

            environment = "sandbox" if (await load_credentials(owner)).is_sandbox else "production"
        except Exception:
            environment = None
    return {
        "alpaca": {"connected": {"alpaca_key_id", "alpaca_secret_key"} <= services, "environment": "paper"},
        "settrade": {
            "connected": settrade_ready,
            "environment": environment,
            "trading_enabled": settrade_ready and environment == "sandbox" and "settrade_pin" in services,
        },
    }


async def _broker_payload(owner: str) -> dict:
    from app.core import brokers

    connections = await _provider_connections(owner)
    return {
        "provider": await brokers.get_active_provider(owner),
        "providers": [
            {"key": key, **brokers.PROVIDER_INFO[key], **connections[key]}
            for key in brokers.PROVIDERS
        ],
    }


@router.get('/broker')
async def get_broker_provider(user=Depends(get_current_user)):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    return await _broker_payload(owner)


@router.put('/broker')
async def set_broker_provider(payload: BrokerProviderIn, user=Depends(get_current_user)):
    from app.core import brokers

    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    await brokers.set_active_provider(owner, payload.provider)
    return await _broker_payload(owner)

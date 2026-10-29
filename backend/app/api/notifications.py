import time
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.dependencies import get_current_user
from app.core import notifier

router = APIRouter()

_TEST_COOLDOWN_S = 20
_last_test_at: Dict[str, float] = {}


class PreferencesUpdate(BaseModel):
    email_enabled: Optional[bool] = None
    discord_enabled: Optional[bool] = None
    # Omit to keep the stored webhook; a URL replaces it; "" removes it.
    discord_webhook_url: Optional[str] = None
    events: Optional[Dict[str, bool]] = None


def _owner(user) -> str:
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    return str(owner)


def _public_preferences(prefs, account_email: Optional[str]):
    email = prefs.get('email') or account_email
    if prefs['email_enabled'] and prefs['discord_enabled']:
        mode = 'both'
    elif prefs['email_enabled']:
        mode = 'email'
    elif prefs['discord_enabled']:
        mode = 'discord'
    else:
        mode = 'off'
    return {
        'mode': mode,
        'email': email,
        'email_enabled': bool(prefs['email_enabled']),
        'discord_enabled': bool(prefs['discord_enabled']),
        # The webhook URL is a secret (anyone holding it can post to the channel): never echo it back.
        'discord_webhook': {
            'configured': bool(prefs.get('discord_webhook_enc')),
            'hint': prefs.get('discord_webhook_hint'),
        },
        'events': prefs['events'],
        'catalog': notifier.EVENT_CATALOG,
        'email_delivery_available': notifier.smtp_configured(),
        'updated_at': prefs['updated_at'].isoformat() if prefs.get('updated_at') else None,
    }


@router.get('/preferences')
async def get_preferences(user=Depends(get_current_user)):
    owner = _owner(user)
    await notifier.remember_member_email(owner, user.get('email'))
    prefs = await notifier.load_preferences(owner)
    return _public_preferences(prefs, user.get('email'))


@router.put('/preferences')
async def update_preferences(payload: PreferencesUpdate, user=Depends(get_current_user)):
    owner = _owner(user)
    unknown = sorted(set(payload.events or {}) - set(notifier.EVENT_KEYS))
    if unknown:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown event types: {', '.join(unknown)}")

    webhook = payload.discord_webhook_url
    try:
        prefs = await notifier.save_preferences(
            owner,
            email=user.get('email'),
            email_enabled=payload.email_enabled,
            discord_enabled=payload.discord_enabled,
            discord_webhook_url=webhook.strip() if webhook else None,
            remove_discord_webhook=webhook is not None and not webhook.strip(),
            events=payload.events,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _public_preferences(prefs, user.get('email'))


@router.post('/test')
async def send_test_notification(user=Depends(get_current_user)):
    owner = _owner(user)
    now = time.monotonic()
    wait = _TEST_COOLDOWN_S - (now - _last_test_at.get(owner, 0.0))
    if wait > 0:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=f'Please wait {int(wait) + 1}s before sending another test.')
    _last_test_at[owner] = now

    await notifier.remember_member_email(owner, user.get('email'))
    result = await notifier.dispatch(owner, notifier.build_test_event(), force=True)
    if result.get('reason') == 'no_active_channel':
        # Nothing was sent, so don't make the member wait before trying again.
        _last_test_at.pop(owner, None)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='All alerts are off. Turn on Email or Discord first.')
    return result


@router.get('/history')
async def get_history(limit: int = Query(default=30, ge=1, le=100), user=Depends(get_current_user)):
    return {'items': await notifier.recent_history(_owner(user), limit)}

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List

from app.core.db import get_database
from app.core import crypto
from app.api.auth import get_current_user

router = APIRouter()


class KeyIn(BaseModel):
    service: str
    api_key: str


class PingIn(BaseModel):
    service: str


class KeyOut(BaseModel):
    id: str
    service: str
    preview: str
    created_at: str


@router.post('/keys')
async def store_key(payload: KeyIn, user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid user')

    service = (payload.service or '').strip().lower()
    if not service:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid service')

    # Clean up case-variant duplicates (e.g. "NewsAPI" vs "newsapi") so lookups are reliable.
    try:
        await db.execute(
            query=(
                "DELETE FROM encrypted_api_keys "
                "WHERE owner = :owner AND lower(service) = :service AND service <> :service"
            ),
            values={"owner": owner, "service": service},
        )
    except Exception:
        # best-effort cleanup; ignore
        pass
    blob = crypto.encrypt_api_key(payload.api_key.encode('utf-8'))
    query = (
        "INSERT INTO encrypted_api_keys (owner, service, encrypted_blob) "
        "VALUES (:owner, :service, :blob) "
        "ON CONFLICT (owner, service) DO UPDATE SET encrypted_blob = EXCLUDED.encrypted_blob, created_at = now() "
        "RETURNING id, created_at"
    )
    row = await db.fetch_one(query=query, values={"owner": owner, "service": service, "blob": blob})
    return {"id": str(row['id']), "service": service, "created_at": str(row['created_at'])}


@router.post('/ping')
async def ping_key(payload: PingIn, user=Depends(get_current_user)):
    """Test stored API keys for a given service.

    Supported services:
    - newsapi
    - alpaca (requires alpaca_key_id + alpaca_secret_key to be stored)
    """
    import os
    import httpx

    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid user')

    service = (payload.service or '').strip().lower()
    if not service:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid service')

    async def _get_service_key(svc: str) -> str:
        row = await db.fetch_one(
            query=(
                "SELECT encrypted_blob FROM encrypted_api_keys "
                "WHERE owner = :owner AND lower(service) = :service "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            values={"owner": owner, "service": svc},
        )
        if not row:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{svc} key not found in vault")
        return crypto.decrypt_api_key(row['encrypted_blob']).decode('utf-8')

    if service == 'newsapi':
        api_key = await _get_service_key('newsapi')
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    "https://newsapi.org/v2/top-headlines",
                    params={"q": "market", "pageSize": 1, "language": "en"},
                    headers={"X-Api-Key": api_key},
                )
            data = r.json() if r.content else {}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"NewsAPI ping failed: {e}")

        if r.status_code != 200 or (isinstance(data, dict) and data.get('status') == 'error'):
            msg = None
            if isinstance(data, dict):
                msg = data.get('message') or data.get('code')
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg or 'NewsAPI key invalid')

        return {"status": "ok", "service": "newsapi"}

    if service == 'alpaca':
        key_id = await _get_service_key('alpaca_key_id')
        secret_key = await _get_service_key('alpaca_secret_key')
        base_url = (os.environ.get('ALPACA_BASE_URL') or 'https://paper-api.alpaca.markets').rstrip('/')

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(
                    f"{base_url}/v2/account",
                    headers={
                        'APCA-API-KEY-ID': key_id,
                        'APCA-API-SECRET-KEY': secret_key,
                    },
                )
            data = r.json() if r.content else {}
        except Exception as e:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Alpaca ping failed: {e}")

        if r.status_code != 200:
            msg = None
            if isinstance(data, dict):
                msg = data.get('message')
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg or 'Alpaca key invalid')

        return {
            "status": "ok",
            "service": "alpaca",
            "account_id": data.get('id'),
            "cash": data.get('cash'),
            "buying_power": data.get('buying_power'),
        }

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail='Ping not supported for this service (supported: newsapi, alpaca)',
    )


@router.get('/keys', response_model=List[KeyOut])
async def list_keys(user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    owner = user.get('sub')
    query = "SELECT id, service, encrypted_blob, created_at FROM encrypted_api_keys WHERE owner = :owner ORDER BY created_at DESC"
    rows = await db.fetch_all(query=query, values={"owner": owner})
    out = []
    for r in rows:
        blob = r['encrypted_blob']
        # preview: try to decrypt to show masked value
        try:
            plain = crypto.decrypt_api_key(blob).decode('utf-8')
            preview = plain[:4] + '...' + plain[-4:]
        except Exception:
            preview = '****'
        out.append({"id": str(r['id']), "service": r['service'], "preview": preview, "created_at": str(r['created_at'])})
    return out


@router.delete('/keys/{key_id}')
async def delete_key(key_id: str, user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    owner = user.get('sub')
    query = "DELETE FROM encrypted_api_keys WHERE id = :id AND owner = :owner RETURNING id"
    row = await db.fetch_one(query=query, values={"id": key_id, "owner": owner})
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Key not found')
    return {"status": "deleted", "id": key_id}


@router.get('/keys/{key_id}/raw')
async def get_raw_key(key_id: str, user=Depends(get_current_user)):
    # For security, only return plaintext when a special env flag is enabled (server-side use preferred)
    import os
    allow = os.environ.get('AJTRADE_DEV_RETURN_PLAINTEXT', '').lower() in ('1', 'true', 'yes')
    if not allow:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail='Not allowed')
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))
    owner = user.get('sub')
    query = "SELECT encrypted_blob FROM encrypted_api_keys WHERE id = :id AND owner = :owner"
    row = await db.fetch_one(query=query, values={"id": key_id, "owner": owner})
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Key not found')
    blob = row['encrypted_blob']
    plain = crypto.decrypt_api_key(blob).decode('utf-8')
    return {"api_key": plain}

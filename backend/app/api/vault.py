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


class KeyOut(BaseModel):
    id: str
    service: str
    preview: str
    created_at: str


@router.post('/keys')
async def store_key(payload: KeyIn, user=Depends(get_current_user)):
    db = get_database()
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid user')
    blob = crypto.encrypt_api_key(payload.api_key.encode('utf-8'))
    query = (
        "INSERT INTO encrypted_api_keys (owner, service, encrypted_blob) "
        "VALUES (:owner, :service, :blob) "
        "ON CONFLICT (owner, service) DO UPDATE SET encrypted_blob = EXCLUDED.encrypted_blob, created_at = now() "
        "RETURNING id, created_at"
    )
    row = await db.fetch_one(query=query, values={"owner": owner, "service": payload.service, "blob": blob})
    return {"id": str(row['id']), "service": payload.service, "created_at": str(row['created_at'])}


@router.get('/keys', response_model=List[KeyOut])
async def list_keys(user=Depends(get_current_user)):
    db = get_database()
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
    db = get_database()
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
    db = get_database()
    owner = user.get('sub')
    query = "SELECT encrypted_blob FROM encrypted_api_keys WHERE id = :id AND owner = :owner"
    row = await db.fetch_one(query=query, values={"id": key_id, "owner": owner})
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Key not found')
    blob = row['encrypted_blob']
    plain = crypto.decrypt_api_key(blob).decode('utf-8')
    return {"api_key": plain}

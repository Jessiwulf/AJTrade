from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import httpx
from fastapi import HTTPException, status

from app.core import crypto
from app.core.db import get_database


async def _get_owner_service_key(owner: str, service: str) -> str:
    db = get_database()
    row = await db.fetch_one(
        query=(
            "SELECT encrypted_blob FROM encrypted_api_keys "
            "WHERE owner = :owner AND lower(service) = :service "
            "ORDER BY created_at DESC LIMIT 1"
        ),
        values={"owner": owner, "service": service.lower()},
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{service} key not found in vault",
        )
    return crypto.decrypt_api_key(row["encrypted_blob"]).decode("utf-8")


async def get_owner_alpaca_credentials(owner: str) -> Tuple[str, str, str]:
    key_id = await _get_owner_service_key(owner, "alpaca_key_id")
    secret_key = await _get_owner_service_key(owner, "alpaca_secret_key")
    base_url = (os.environ.get("ALPACA_BASE_URL") or "https://paper-api.alpaca.markets").rstrip("/")
    return key_id, secret_key, base_url


async def alpaca_request(
    owner: str,
    method: str,
    path: str,
    *,
    params: Dict[str, Any] | None = None,
    json: Dict[str, Any] | None = None,
    timeout_s: float = 20.0,
) -> Any:
    key_id, secret_key, base_url = await get_owner_alpaca_credentials(owner)
    headers = {
        "APCA-API-KEY-ID": key_id,
        "APCA-API-SECRET-KEY": secret_key,
        "accept": "application/json",
    }
    url = f"{base_url}{path}"

    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.request(method.upper(), url, headers=headers, params=params, json=json)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Alpaca request failed: {exc}",
        ) from exc

    payload: Any = None
    try:
        payload = response.json() if response.content else None
    except Exception:
        payload = response.text

    if response.status_code >= 400:
        message = None
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("detail")
        if not message and isinstance(payload, str):
            message = payload
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message or f"Alpaca error ({response.status_code})",
        )

    return payload


async def get_alpaca_account(owner: str) -> Dict[str, Any]:
    payload = await alpaca_request(owner, "GET", "/v2/account")
    return payload if isinstance(payload, dict) else {}


async def get_alpaca_positions(owner: str) -> List[Dict[str, Any]]:
    payload = await alpaca_request(owner, "GET", "/v2/positions")
    return payload if isinstance(payload, list) else []


async def submit_alpaca_order(
    owner: str,
    *,
    symbol: str,
    side: str,
    quantity: str | None = None,
    notional: str | None = None,
    client_order_id: str | None = None,
) -> Dict[str, Any]:
    order_payload: Dict[str, Any] = {
        "symbol": symbol,
        "side": side.lower(),
        "type": "market",
        "time_in_force": "day",
    }
    if quantity:
        order_payload["qty"] = quantity
    elif notional:
        order_payload["notional"] = notional
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="quantity_or_notional_required")

    if client_order_id:
        order_payload["client_order_id"] = client_order_id

    payload = await alpaca_request(owner, "POST", "/v2/orders", json=order_payload)
    return payload if isinstance(payload, dict) else {}
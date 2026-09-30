from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

import httpx
from fastapi import HTTPException, status

from app.core import crypto
from app.core.db import get_database

# Crypto symbols: the app uses Yahoo's form ("BTC-USD") everywhere; Alpaca takes "BTC/USD" for orders and
# reports "BTC/USD" on orders but "BTCUSD" on positions. These helpers translate at the Alpaca boundary.
CRYPTO_QUOTE_CURRENCIES = ("USD",)
# Coins Alpaca trades against USD (a bare "SOL" on Yahoo is a stock ticker, so the watchlist maps these to "-USD").
ALPACA_CRYPTO_BASES = {
    "AAVE", "AVAX", "BAT", "BCH", "BTC", "CRV", "DOGE", "DOT", "ETH", "GRT", "LINK", "LTC", "MKR",
    "PEPE", "SHIB", "SOL", "SUSHI", "TRUMP", "UNI", "XRP", "XTZ", "YFI",
}


def is_crypto_symbol(symbol: str) -> bool:
    base, sep, quote = (symbol or "").strip().upper().rpartition("-")
    return bool(sep and base.isalnum() and quote in CRYPTO_QUOTE_CURRENCIES)


def to_alpaca_symbol(symbol: str) -> str:
    symbol = (symbol or "").strip().upper()
    return symbol.replace("-", "/") if is_crypto_symbol(symbol) else symbol


def from_alpaca_symbol(symbol: str, asset_class: str | None = None) -> str:
    symbol = (symbol or "").strip().upper()
    if "/" in symbol:
        return symbol.replace("/", "-")
    if str(asset_class or "").lower() == "crypto":
        for quote in CRYPTO_QUOTE_CURRENCIES:
            if symbol.endswith(quote) and len(symbol) > len(quote):
                return f"{symbol[:-len(quote)]}-{quote}"
    return symbol


def _with_app_symbol(item: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(item, dict) and item.get("symbol"):
        item["symbol"] = from_alpaca_symbol(str(item["symbol"]), item.get("asset_class"))
    return item


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
    return [_with_app_symbol(position) for position in payload] if isinstance(payload, list) else []


async def list_alpaca_filled_orders(owner: str, *, max_pages: int = 6) -> List[Dict[str, Any]]:
    """Closed orders that filled at least partly, newest first (up to max_pages x 500 orders)."""
    orders: List[Dict[str, Any]] = []
    until = None
    for _ in range(max_pages):
        params: Dict[str, Any] = {"status": "closed", "limit": 500, "direction": "desc", "nested": "false"}
        if until:
            params["until"] = until
        page = await alpaca_request(owner, "GET", "/v2/orders", params=params)
        if not isinstance(page, list) or not page:
            break
        orders.extend(page)
        if len(page) < 500:
            break
        until = page[-1].get("submitted_at")
        if not until:
            break
    seen = set()
    filled = []
    for order in orders:
        order_id = order.get("id")
        if order_id in seen:
            continue
        seen.add(order_id)
        try:
            filled_qty = float(order.get("filled_qty") or 0)
        except (TypeError, ValueError):
            filled_qty = 0.0
        if filled_qty > 0 and order.get("filled_avg_price"):
            filled.append(_with_app_symbol(order))
    return filled


async def get_alpaca_portfolio_history(owner: str, *, period: str = "1M", timeframe: str = "1D") -> Dict[str, Any]:
    payload = await alpaca_request(
        owner, "GET", "/v2/account/portfolio/history", params={"period": period, "timeframe": timeframe}
    )
    return payload if isinstance(payload, dict) else {}


async def submit_alpaca_order(
    owner: str,
    *,
    symbol: str,
    side: str,
    quantity: str | None = None,
    notional: str | None = None,
    client_order_id: str | None = None,
) -> Dict[str, Any]:
    # Crypto trades 24/7 and Alpaca only accepts gtc/ioc for it; stock market orders stay "day".
    order_payload: Dict[str, Any] = {
        "symbol": to_alpaca_symbol(symbol),
        "side": side.lower(),
        "type": "market",
        "time_in_force": "gtc" if is_crypto_symbol(symbol) else "day",
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
    return _with_app_symbol(payload) if isinstance(payload, dict) else {}
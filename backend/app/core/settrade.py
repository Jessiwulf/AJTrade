"""Settrade Open API (Thai SET equities) client.

Follows the protocol of Settrade's official Python SDK (settrade-v2): ECDSA-signed login, bearer
token with refresh, and the SEOS v3/v4 equity endpoints. It talks to the REST API directly because
the SDK keeps its sandbox/production switch in a process-wide global (one member on SANDBOX would
flip every other member), is synchronous, and writes a config file into the home directory.

Credentials live in the encrypted vault per member:
  settrade_app_id, settrade_app_secret      (required)
  settrade_broker_id                        (default "SANDBOX")
  settrade_app_code                         (default "SANDBOX")
  settrade_account_no                       (required for account data and orders)
  settrade_pin                              (required to place or cancel orders)

App symbols for SET stocks carry Yahoo's ".BK" suffix ("PTT.BK"); Settrade uses the bare ticker.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import math
import time
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException, status

logger = logging.getLogger("ajtrade.settrade")

PROD_BASE_URL = "https://open-api.settrade.com"
SANDBOX_BASE_URL = "https://open-api-test.settrade.com"
SANDBOX = "SANDBOX"
SANDBOX_BROKER_ID = "098"
BOARD_LOT = 100
_REFRESH_BEFORE_EXPIRY_S = 100

SERVICE_KEYS = (
    "settrade_app_id",
    "settrade_app_secret",
    "settrade_broker_id",
    "settrade_app_code",
    "settrade_account_no",
    "settrade_pin",
)


# --- Symbols, lots and ticks ----------------------------------------------------------------

def to_settrade_symbol(symbol: str) -> str:
    symbol = (symbol or "").strip().upper()
    return symbol[:-3] if symbol.endswith(".BK") else symbol


def from_settrade_symbol(symbol: str) -> str:
    symbol = (symbol or "").strip().upper()
    return symbol if not symbol or symbol.endswith(".BK") else f"{symbol}.BK"


# SET price spreads (tick sizes) for stocks, by price band.
_TICKS = (
    (Decimal("2"), Decimal("0.01")),
    (Decimal("5"), Decimal("0.02")),
    (Decimal("10"), Decimal("0.05")),
    (Decimal("25"), Decimal("0.10")),
    (Decimal("100"), Decimal("0.25")),
    (Decimal("200"), Decimal("0.50")),
    (Decimal("400"), Decimal("1.00")),
)


def tick_size(price: Decimal) -> Decimal:
    for upper, tick in _TICKS:
        if price < upper:
            return tick
    return Decimal("2.00")


def round_to_tick(price: Any) -> Decimal:
    value = Decimal(str(price))
    tick = tick_size(value)
    return ((value / tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * tick).quantize(Decimal("0.01"))


def board_lot_volume(amount: Any, price: Any) -> int:
    """Whole board lots (100 shares) that `amount` THB buys at `price`."""
    price = Decimal(str(price))
    if price <= 0:
        return 0
    lots = (Decimal(str(amount)) / price / BOARD_LOT).to_integral_value(rounding=ROUND_DOWN)
    return int(lots) * BOARD_LOT


# --- Response parsing -----------------------------------------------------------------------

def _first(item: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if isinstance(item, dict) and item.get(key) not in (None, ""):
            return item[key]
    return None


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_account(info: Dict[str, Any]) -> Dict[str, Any]:
    cash = _num(_first(info, "cashBalance", "cash", "availableCash", "cashBalanceAvailable"))
    return {
        "currency": "THB",
        "cash": cash,
        "buying_power": _num(_first(info, "lineAvailable", "buyingPower", "maxLineAvailable", "creditLimit")),
        "credit_type": _first(info, "creditType"),
        "can_buy": _first(info, "canBuy"),
        "can_sell": _first(info, "canSell"),
    }


def _portfolio_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("portfolioList", "portfolios", "data"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def normalize_positions(payload: Any) -> List[Dict[str, Any]]:
    positions = []
    for row in _portfolio_rows(payload):
        symbol = str(_first(row, "symbol", "stock") or "").strip().upper()
        quantity = _num(_first(row, "actualVolume", "currentVolume", "volume", "startVolume"))
        if not symbol or not quantity or quantity <= 0:
            continue
        avg_price = _num(_first(row, "averagePrice", "avgPrice", "averageCost", "startPrice")) or 0.0
        market_price = _num(_first(row, "marketPrice", "lastPrice", "last"))
        market_value = _num(_first(row, "marketValue"))
        if market_value is None and market_price is not None:
            market_value = market_price * quantity
        cost = _num(_first(row, "amount")) or avg_price * quantity
        unrealized = _num(_first(row, "profit", "unrealizedProfit", "unrealizedPl"))
        if unrealized is None and market_value is not None:
            unrealized = market_value - cost
        positions.append(
            {
                "symbol": from_settrade_symbol(symbol),
                "qty": quantity,
                "avg_entry_price": avg_price,
                "current_price": market_price,
                "market_value": market_value,
                "unrealized_pl": unrealized,
                "unrealized_plpc": (unrealized / cost) if unrealized is not None and cost else None,
            }
        )
    return positions


def _side(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    if text in {"B", "BUY"}:
        return "BUY"
    if text in {"S", "SELL"}:
        return "SELL"
    return None


def order_outcome(order: Dict[str, Any]) -> Dict[str, Any]:
    """Maps a Settrade order to the bot's outcome shape: filled / pending / rejected."""
    order_no = _first(order, "orderNo", "orderNumber")
    volume = _num(_first(order, "vol", "volume")) or 0.0
    matched = _num(_first(order, "matchQty", "matchVolume", "matchedVolume", "qtyMatch")) or 0.0
    status_text = str(_first(order, "showStatus", "status", "orderStatus") or "").strip()
    lowered = status_text.lower()
    outcome: Dict[str, Any] = {"order_id": str(order_no) if order_no is not None else None, "status": status_text}
    if volume > 0 and matched >= volume:
        price = _num(_first(order, "matchPrice", "avgMatchPrice", "averageMatchPrice", "price")) or 0.0
        outcome.update(state="filled", fill_price=Decimal(str(price)), filled_qty=Decimal(str(matched)))
    elif any(word in lowered for word in ("reject", "cancel", "expire", "fail")):
        if matched > 0:
            price = _num(_first(order, "matchPrice", "avgMatchPrice", "price")) or 0.0
            outcome.update(state="filled", fill_price=Decimal(str(price)), filled_qty=Decimal(str(matched)))
        else:
            reason = _first(order, "rejectReason", "rejectCode") or status_text or "rejected"
            outcome.update(state="rejected", message=f"Rejected: Settrade order {reason}")
    else:
        outcome.update(state="pending")
    return outcome


def normalize_trades(payload: Any) -> List[Dict[str, Any]]:
    """Today's executions ("trades") as fills: one row per matched trade."""
    rows = payload if isinstance(payload, list) else _portfolio_rows(payload)
    fills = []
    for row in rows:
        side = _side(_first(row, "side", "buySell"))
        symbol = str(_first(row, "symbol") or "").strip().upper()
        quantity = _num(_first(row, "qty", "volume", "matchVolume", "matchedVolume", "vol"))
        price = _num(_first(row, "price", "matchPrice", "tradePrice"))
        if not side or not symbol or not quantity or not price:
            continue
        trade_id = _first(row, "tradeNo", "tradeId", "dealNo")
        order_no = _first(row, "orderNo")
        if trade_id is None:
            # Stable id when the API gives none: same trade -> same id across syncs.
            basis = f"{order_no}|{symbol}|{side}|{quantity}|{price}|{_first(row, 'tradeTime', 'tradeDateTime', 'entryTime')}"
            trade_id = hashlib.sha1(basis.encode()).hexdigest()[:20]
        fills.append(
            {
                "id": f"settrade-{trade_id}",
                "order_no": str(order_no) if order_no is not None else None,
                "symbol": from_settrade_symbol(symbol),
                "side": side,
                "qty": quantity,
                "price": price,
                "time": _first(row, "tradeDateTime", "tradeTime", "entryTime", "tradeDate"),
            }
        )
    return fills


# --- Client ---------------------------------------------------------------------------------

def _sign(app_secret: str, content: str) -> str:
    """SHA256-with-ECDSA (P-256) signature, hex encoded: the App Secret is the base64 private scalar."""
    try:
        scalar = int.from_bytes(base64.b64decode(app_secret), "big")
        private_key = ec.derive_private_key(scalar, ec.SECP256R1())
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Settrade App Secret is not valid") from exc
    return private_key.sign(content.encode(), ec.ECDSA(hashes.SHA256())).hex()


class SettradeCredentials:
    def __init__(self, app_id: str, app_secret: str, broker_id: str, app_code: str, account_no: Optional[str], pin: Optional[str]):
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self.is_sandbox = (broker_id or SANDBOX).strip().upper() == SANDBOX
        self.broker_id = SANDBOX_BROKER_ID if self.is_sandbox else broker_id.strip()
        self.app_code = (app_code or SANDBOX).strip()
        self.account_no = (account_no or "").strip() or None
        self.pin = (pin or "").strip() or None
        self.base_url = SANDBOX_BASE_URL if self.is_sandbox else PROD_BASE_URL

    @property
    def fingerprint(self) -> str:
        basis = f"{self.app_id}|{self.app_secret}|{self.broker_id}|{self.app_code}|{self.base_url}"
        return hashlib.sha256(basis.encode()).hexdigest()


async def load_credentials(owner: str) -> SettradeCredentials:
    from app.core.alpaca import _get_owner_service_key

    async def optional(service: str) -> Optional[str]:
        try:
            return await _get_owner_service_key(owner, service)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_404_NOT_FOUND:
                return None
            raise

    app_id = await optional("settrade_app_id")
    app_secret = await optional("settrade_app_secret")
    if not app_id or not app_secret:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settrade not connected. Add your Settrade App Id and App Secret in API Management.",
        )
    return SettradeCredentials(
        app_id=app_id,
        app_secret=app_secret,
        broker_id=await optional("settrade_broker_id") or SANDBOX,
        app_code=await optional("settrade_app_code") or SANDBOX,
        account_no=await optional("settrade_account_no"),
        pin=await optional("settrade_pin"),
    )


class _Session:
    def __init__(self) -> None:
        self.fingerprint: Optional[str] = None
        self.token_type = "Bearer"
        self.token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.expires_at = 0.0
        self.lock = asyncio.Lock()


_sessions: Dict[str, _Session] = {}


def _error_from(response: httpx.Response, action: str) -> HTTPException:
    try:
        data = response.json()
    except ValueError:
        data = {}
    message = (data or {}).get("message") or response.text[:200] or f"HTTP {response.status_code}"
    code = (data or {}).get("code")
    detail = f"Settrade {action} failed: {message}" + (f" ({code})" if code else "")
    if response.status_code in (401, 403):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY if response.status_code >= 500 else status.HTTP_400_BAD_REQUEST, detail=detail)


async def _login(client: httpx.AsyncClient, creds: SettradeCredentials, session: _Session) -> None:
    timestamp = str(int(time.time() * 1000))
    params = ""
    response = await client.post(
        f"{creds.base_url}/api/oam/v1/{creds.broker_id}/broker-apps/{creds.app_code}/login",
        json={
            "apiKey": creds.app_id,
            "params": params,
            "signature": _sign(creds.app_secret, f"{creds.app_id}.{params}.{timestamp}"),
            "timestamp": timestamp,
        },
    )
    if not response.is_success:
        raise _error_from(response, "login")
    data = response.json()
    session.fingerprint = creds.fingerprint
    session.token_type = data.get("token_type") or "Bearer"
    session.token = data["access_token"]
    session.refresh_token = data.get("refresh_token")
    session.expires_at = time.time() + float(data.get("expires_in") or 0)


async def _refresh(client: httpx.AsyncClient, creds: SettradeCredentials, session: _Session) -> bool:
    if not session.refresh_token:
        return False
    response = await client.post(
        f"{creds.base_url}/api/oam/v1/{creds.broker_id}/broker-apps/{creds.app_code}/refresh-token",
        json={"apiKey": creds.app_id, "refreshToken": session.refresh_token},
    )
    if not response.is_success:
        return False
    data = response.json()
    session.token = data["access_token"]
    session.refresh_token = data.get("refresh_token") or session.refresh_token
    session.expires_at = time.time() + float(data.get("expires_in") or 0)
    return True


async def _authorized_headers(client: httpx.AsyncClient, owner: str, creds: SettradeCredentials) -> Dict[str, str]:
    session = _sessions.setdefault(owner, _Session())
    async with session.lock:
        if session.fingerprint != creds.fingerprint or not session.token:
            await _login(client, creds, session)
        elif session.expires_at - time.time() <= _REFRESH_BEFORE_EXPIRY_S:
            if not await _refresh(client, creds, session):
                await _login(client, creds, session)
        return {"Authorization": f"{session.token_type} {session.token}", "Content-Type": "application/json"}


async def _request(owner: str, method: str, path: str, *, json: Any = None, action: str = "request", creds: Optional[SettradeCredentials] = None) -> Any:
    creds = creds or await load_credentials(owner)
    async with httpx.AsyncClient(timeout=20.0) as client:
        headers = await _authorized_headers(client, owner, creds)
        url = f"{creds.base_url}{path.format(broker=creds.broker_id, account=creds.account_no)}"
        response = await client.request(method, url, headers=headers, json=json)
        if response.status_code == 401:
            # Token revoked or expired early: log in again once.
            _sessions.pop(owner, None)
            headers = await _authorized_headers(client, owner, creds)
            response = await client.request(method, url, headers=headers, json=json)
    if not response.is_success:
        raise _error_from(response, action)
    try:
        return response.json()
    except ValueError:
        return {}


def _require_account(creds: SettradeCredentials) -> None:
    if not creds.account_no:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Settrade account number missing. Add it in API Management.",
        )


async def get_account_info(owner: str) -> Dict[str, Any]:
    creds = await load_credentials(owner)
    _require_account(creds)
    return await _request(owner, "GET", "/api/seos/v3/{broker}/accounts/{account}/account-info", action="account info", creds=creds)


async def get_portfolios(owner: str) -> Any:
    creds = await load_credentials(owner)
    _require_account(creds)
    return await _request(owner, "GET", "/api/seos/v3/{broker}/accounts/{account}/portfolios", action="portfolio", creds=creds)


async def get_orders(owner: str) -> List[Dict[str, Any]]:
    creds = await load_credentials(owner)
    _require_account(creds)
    data = await _request(owner, "GET", "/api/seos/v3/{broker}/accounts/{account}/orders", action="orders", creds=creds)
    return data if isinstance(data, list) else _portfolio_rows(data)


async def get_order(owner: str, order_no: str) -> Dict[str, Any]:
    creds = await load_credentials(owner)
    _require_account(creds)
    data = await _request(owner, "GET", f"/api/seos/v3/{{broker}}/accounts/{{account}}/orders/{order_no}", action="order", creds=creds)
    return data if isinstance(data, dict) else {}


async def get_trades(owner: str) -> Any:
    creds = await load_credentials(owner)
    _require_account(creds)
    return await _request(owner, "GET", "/api/seos/v4/{broker}/accounts/{account}/trades", action="trades", creds=creds)


async def place_order(owner: str, *, symbol: str, side: str, volume: int, price: Decimal) -> Dict[str, Any]:
    """Day limit order. Only allowed on the Settrade SANDBOX: AJTrade never sends real-money orders."""
    creds = await load_credentials(owner)
    _require_account(creds)
    if not creds.is_sandbox:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Trading is only enabled on the Settrade SANDBOX. Live Settrade accounts are read-only in AJTrade.",
        )
    if not creds.pin:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Settrade PIN missing. Add it in API Management.")
    if volume <= 0 or volume % BOARD_LOT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Volume must be a multiple of {BOARD_LOT} shares (one board lot).")
    body = {
        "pin": creds.pin,
        "side": "Buy" if side.upper() == "BUY" else "Sell",
        "symbol": to_settrade_symbol(symbol),
        "trusteeIdType": "Local",
        "volume": int(volume),
        "qtyOpen": 0,
        "price": float(round_to_tick(price)),
        "priceType": "Limit",
        "validityType": "Day",
        "clientType": "Individual",
    }
    data = await _request(owner, "POST", "/api/seos/v3/{broker}/accounts/{account}/orders", json=body, action="order", creds=creds)
    return data if isinstance(data, dict) else {}


async def test_connection(owner: str) -> Dict[str, Any]:
    """Logs in and, when an account number is set, reads the account: proves every credential works."""
    creds = await load_credentials(owner)
    _sessions.pop(owner, None)  # always prove a fresh login
    result: Dict[str, Any] = {
        "environment": "sandbox" if creds.is_sandbox else "production",
        "broker_id": creds.broker_id,
        "trading_enabled": creds.is_sandbox and bool(creds.pin),
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        await _authorized_headers(client, owner, creds)
    result["login"] = "ok"
    if creds.account_no:
        account = normalize_account(await get_account_info(owner))
        result.update(account_no=creds.account_no, cash=account["cash"], buying_power=account["buying_power"])
    else:
        result["warning"] = "Login works. Add your Settrade account number to load cash, positions and trades."
    return result

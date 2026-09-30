import logging
import re
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.core.alpaca import get_alpaca_account, get_alpaca_positions, list_alpaca_filled_orders, submit_alpaca_order
from app.core.db import get_database

router = APIRouter()
logger = logging.getLogger(__name__)

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,24}$")


def _normalize_symbol(symbol: str) -> str:
    # "BTC/USD" (Alpaca's crypto form) -> "BTC-USD" (the form used across the app).
    return (symbol or "").strip().upper().replace("/", "-")


async def _validate_symbol(symbol: str) -> None:
    symbol = _normalize_symbol(symbol)
    if not symbol or not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ticker symbol")

    try:
        from app.api.market import get_market_price

        await get_market_price(symbol)
    except HTTPException as exc:
        if exc.status_code in {status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Ticker {symbol} is not available") from exc
        raise


def _maybe_schema_hint(error: Exception, table: str) -> Optional[str]:
    msg = str(error)
    if f'relation "{table}" does not exist' in msg:
        return (
            f"Database table '{table}' does not exist. "
            "Apply schema in Supabase SQL editor: backend/app/models/local_schema.sql"
        )
    return None


class PositionIn(BaseModel):
    symbol: str
    quantity: Decimal = Field(..., ge=0)
    avg_price: Decimal = Field(Decimal("0"), ge=0)


class InitializePortfolioIn(BaseModel):
    cash_balance: Decimal = Field(..., ge=0)
    positions: List[PositionIn] = []


class UpdateCashIn(BaseModel):
    cash_balance: Decimal = Field(..., ge=0)


class PaperOrderIn(BaseModel):
    symbol: str
    side: str
    quantity: Optional[Decimal] = Field(None, gt=0)
    notional: Optional[Decimal] = Field(None, gt=0)
    notes: Optional[str] = None


def _decimal_string(value: Any, default: str = "0") -> str:
    if value in (None, ""):
        return default
    return str(value)


async def _get_owner_portfolio(db, owner: str):
    return await db.fetch_one(
        query="SELECT id, cash_balance, updated_at FROM portfolios WHERE owner = :owner",
        values={"owner": owner},
    )


async def _ensure_owner_portfolio(db, owner: str, cash_balance: Decimal) -> Dict[str, Any]:
    row = await db.fetch_one(
        query=(
            "INSERT INTO portfolios (owner, cash_balance) "
            "VALUES (:owner, :cash) "
            "ON CONFLICT (owner) DO UPDATE SET cash_balance = EXCLUDED.cash_balance, updated_at = now() "
            "RETURNING id, cash_balance, updated_at"
        ),
        values={"owner": owner, "cash": cash_balance},
    )
    return row


async def _upsert_watchlist_symbol(db, owner: str, symbol: str) -> None:
    await db.execute(
        query=(
            "INSERT INTO watchlists (owner, symbol) VALUES (:owner, :symbol) "
            "ON CONFLICT (owner, symbol) DO NOTHING"
        ),
        values={"owner": owner, "symbol": symbol},
    )


async def _log_trade(
    db,
    *,
    portfolio_id: str,
    symbol: str,
    trade_type: str,
    quantity: Decimal,
    price: Decimal,
    notional: Decimal,
    signal_source: str,
    notes: Optional[str],
) -> Dict[str, Any]:
    fee = Decimal("0")
    row = await db.fetch_one(
        query=(
            "INSERT INTO trading_history (portfolio_id, symbol, trade_type, quantity, price, notional, fee, signal_source, notes) "
            "VALUES (:portfolio_id, :symbol, :trade_type, :quantity, :price, :notional, :fee, :signal_source, :notes) "
            "RETURNING id, created_at"
        ),
        values={
            "portfolio_id": portfolio_id,
            "symbol": symbol,
            "trade_type": trade_type,
            "quantity": quantity,
            "price": price,
            "notional": notional,
            "fee": fee,
            "signal_source": signal_source,
            "notes": notes,
        },
    )
    return row


async def _sync_portfolio_from_alpaca(db, owner: str, *, add_to_watchlist: bool = True) -> Dict[str, Any]:
    account = await get_alpaca_account(owner)
    positions = await get_alpaca_positions(owner)
    cash_balance = Decimal(_decimal_string(account.get("cash"), "0"))

    async with db.transaction():
        portfolio = await _ensure_owner_portfolio(db, owner, cash_balance)
        portfolio_id = portfolio["id"]

        await db.execute(
            query="DELETE FROM portfolio_positions WHERE portfolio_id = :pid",
            values={"pid": portfolio_id},
        )

        synced_positions = []
        for position in positions:
            symbol = _normalize_symbol(str(position.get("symbol") or ""))
            if not symbol:
                continue
            quantity = Decimal(_decimal_string(position.get("qty"), "0"))
            avg_price = Decimal(_decimal_string(position.get("avg_entry_price"), "0"))
            if quantity <= 0:
                continue
            await db.execute(
                query=(
                    "INSERT INTO portfolio_positions (portfolio_id, symbol, quantity, avg_price) "
                    "VALUES (:pid, :symbol, :qty, :avg)"
                ),
                values={
                    "pid": portfolio_id,
                    "symbol": symbol,
                    "qty": quantity,
                    "avg": avg_price,
                },
            )
            if add_to_watchlist:
                await _upsert_watchlist_symbol(db, owner, symbol)
            synced_positions.append(
                {
                    "symbol": symbol,
                    "quantity": str(quantity),
                    "avg_price": str(avg_price),
                }
            )

    return {
        "portfolio_id": str(portfolio["id"]),
        "cash_balance": str(portfolio["cash_balance"]),
        "positions": synced_positions,
        "account": {
            "account_id": account.get("id"),
            "status": account.get("status"),
            "currency": account.get("currency"),
            "buying_power": account.get("buying_power"),
            "portfolio_value": account.get("portfolio_value"),
            "cash": account.get("cash"),
        },
    }


@router.get('')
async def get_portfolio(user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        p = await db.fetch_one(
            query="SELECT id, cash_balance, updated_at FROM portfolios WHERE owner = :owner",
            values={"owner": owner},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not initialized")

    try:
        rows = await db.fetch_all(
            query=(
                "SELECT id, symbol, quantity, avg_price, updated_at "
                "FROM portfolio_positions WHERE portfolio_id = :pid "
                "ORDER BY updated_at DESC"
            ),
            values={"pid": p["id"]},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolio_positions")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    positions = [
        {
            "id": str(r["id"]),
            "symbol": r["symbol"],
            "quantity": str(r["quantity"]),
            "avg_price": str(r["avg_price"]),
            "updated_at": str(r["updated_at"]),
        }
        for r in rows
    ]

    return {
        "id": str(p["id"]),
        "cash_balance": str(p["cash_balance"]),
        "updated_at": str(p["updated_at"]),
        "positions": positions,
    }


@router.get('/paper-account')
async def get_paper_account(user=Depends(get_current_user)):
    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    account = await get_alpaca_account(owner)
    positions = await get_alpaca_positions(owner)
    return {
        "account_id": account.get("id"),
        "status": account.get("status"),
        "currency": account.get("currency"),
        "cash": account.get("cash"),
        "buying_power": account.get("buying_power"),
        "portfolio_value": account.get("portfolio_value"),
        "positions": [
            {
                "symbol": _normalize_symbol(str(p.get("symbol") or "")),
                "quantity": _decimal_string(p.get("qty"), "0"),
                "avg_entry_price": _decimal_string(p.get("avg_entry_price"), "0"),
                "market_value": _decimal_string(p.get("market_value"), "0"),
                "unrealized_pl": _decimal_string(p.get("unrealized_pl"), "0"),
            }
            for p in positions
            if p.get("symbol")
        ],
    }


_HISTORY_SCHEMA_READY = False


async def _ensure_history_schema(db) -> None:
    global _HISTORY_SCHEMA_READY
    if _HISTORY_SCHEMA_READY:
        return
    await db.execute(query="ALTER TABLE trading_history ADD COLUMN IF NOT EXISTS broker_order_id text")
    await db.execute(
        query=(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_trading_history_broker_order "
            "ON trading_history(portfolio_id, broker_order_id)"
        )
    )
    _HISTORY_SCHEMA_READY = True


def _order_source(client_order_id: str) -> str:
    """Who placed the order: the AJTrade bot, the AJTrade trade ticket, or elsewhere (Alpaca's site/API)."""
    client_order_id = str(client_order_id or "")
    if client_order_id.startswith("ajbot-"):
        return "bot"
    if client_order_id.startswith("ajtrade-"):
        return "manual"
    return "alpaca"


def _parse_alpaca_time(value: Any) -> Optional[datetime]:
    """Alpaca timestamps ("2026-06-17T13:31:06.065077Z", sometimes with nanoseconds) -> aware datetime."""
    text = str(value or "").strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    # Python accepts at most 6 fractional digits.
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


async def sync_trade_history_from_alpaca(db, owner: str) -> int:
    """Makes trading_history mirror the account's filled Alpaca orders (actual fill price and quantity).

    Realized P/L on sells uses the average-cost method over the fills in order; a sell whose buys are not
    in the fetched history gets no P/L rather than a guessed one. Rows not backed by an Alpaca order
    (simulated, seeded or estimated entries) are removed. Returns the number of new fills recorded.
    """
    await _ensure_history_schema(db)
    orders = await list_alpaca_filled_orders(owner)
    portfolio = await _get_owner_portfolio(db, owner)
    if not portfolio:
        await _sync_portfolio_from_alpaca(db, owner, add_to_watchlist=False)
        portfolio = await _get_owner_portfolio(db, owner)
    if not portfolio:
        return 0
    portfolio_id = portfolio["id"]

    fills = sorted(orders, key=lambda o: str(o.get("filled_at") or o.get("submitted_at") or ""))
    holdings: Dict[str, List[Decimal]] = {}  # symbol -> [quantity, average cost]
    rows = []
    for order in fills:
        symbol = _normalize_symbol(str(order.get("symbol") or ""))
        side = str(order.get("side") or "").upper()
        if not symbol or side not in {"BUY", "SELL"}:
            continue
        quantity = Decimal(_decimal_string(order.get("filled_qty"), "0"))
        price = Decimal(_decimal_string(order.get("filled_avg_price"), "0"))
        if quantity <= 0 or price <= 0:
            continue
        held_qty, avg_cost = holdings.get(symbol, [Decimal("0"), Decimal("0")])
        realized_pl = None
        if side == "BUY":
            new_qty = held_qty + quantity
            avg_cost = ((held_qty * avg_cost) + (quantity * price)) / new_qty
            held_qty = new_qty
        else:
            if held_qty > 0:
                matched = min(quantity, held_qty)
                realized_pl = ((price - avg_cost) * matched).quantize(Decimal("0.01"))
                held_qty -= matched
        holdings[symbol] = [held_qty, avg_cost]
        rows.append(
            {
                "portfolio_id": portfolio_id,
                "symbol": symbol,
                "trade_type": side,
                "quantity": quantity,
                "price": price,
                "notional": (quantity * price).quantize(Decimal("0.01")),
                "pl": realized_pl,
                "signal_source": _order_source(order.get("client_order_id")),
                "notes": f"Alpaca order {order.get('id')}",
                "broker_order_id": str(order.get("id")),
                "created_at": _parse_alpaca_time(order.get("filled_at") or order.get("submitted_at")),
            }
        )

    async with db.transaction():
        await db.execute(
            query="DELETE FROM trading_history WHERE portfolio_id = :pid AND broker_order_id IS NULL",
            values={"pid": portfolio_id},
        )
        existing = await db.fetch_all(
            query="SELECT broker_order_id FROM trading_history WHERE portfolio_id = :pid",
            values={"pid": portfolio_id},
        )
        known = {r["broker_order_id"] for r in existing}
        new_rows = [r for r in rows if r["broker_order_id"] not in known]
        if new_rows:
            await db.execute_many(
                query=(
                    "INSERT INTO trading_history "
                    "(portfolio_id, symbol, trade_type, quantity, price, notional, fee, pl, signal_source, notes, broker_order_id, created_at) "
                    "VALUES (:portfolio_id, :symbol, :trade_type, :quantity, :price, :notional, 0, :pl, :signal_source, :notes, "
                    ":broker_order_id, COALESCE(:created_at, now())) "
                    "ON CONFLICT (portfolio_id, broker_order_id) DO NOTHING"
                ),
                values=new_rows,
            )
    return len(new_rows)


async def sync_account_from_alpaca(db, owner: str) -> Dict[str, Any]:
    """Cash, positions and trade history, all from Alpaca."""
    result = await _sync_portfolio_from_alpaca(db, owner)
    try:
        result["new_fills"] = await sync_trade_history_from_alpaca(db, owner)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("trade_history_sync_failed owner=%s error=%s", owner, exc)
        result["new_fills"] = None
    return result


@router.post('/sync-paper')
async def sync_paper_portfolio(user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        return await sync_account_from_alpaca(db, owner)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@router.post('/paper-orders')
async def place_paper_order(payload: PaperOrderIn, user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    symbol = _normalize_symbol(payload.symbol)
    await _validate_symbol(symbol)

    side = (payload.side or "").strip().upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="side must be BUY or SELL")
    if payload.quantity is None and payload.notional is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="quantity_or_notional_required")
    if side == "SELL" and payload.quantity is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SELL orders require quantity")

    order = await submit_alpaca_order(
        owner,
        symbol=symbol,
        side=side,
        quantity=str(payload.quantity) if payload.quantity is not None else None,
        notional=str(payload.notional) if payload.notional is not None else None,
        client_order_id=f"ajtrade-{uuid4().hex[:24]}",
    )

    await _upsert_watchlist_symbol(db, owner, symbol)
    # The trade history is taken from Alpaca's actual fill (not an estimate made here); an order that is
    # still open (e.g. market closed) appears once it fills and the account is synced again.
    sync_result = None
    sync_error = None
    try:
        sync_result = await sync_account_from_alpaca(db, owner)
    except HTTPException as exc:
        sync_error = str(exc.detail or exc)
        logger.warning('paper_order_sync_pending owner=%s symbol=%s status=%s error=%s', owner, symbol, order.get('status'), sync_error)
    except Exception as exc:
        sync_error = str(exc)
        logger.warning('paper_order_sync_pending owner=%s symbol=%s status=%s error=%s', owner, symbol, order.get('status'), sync_error)

    return {
        "order": {
            "id": order.get("id"),
            "client_order_id": order.get("client_order_id"),
            "symbol": order.get("symbol") or symbol,
            "side": order.get("side") or side.lower(),
            "status": order.get("status"),
            "qty": order.get("qty"),
            "notional": order.get("notional"),
            "filled_qty": order.get("filled_qty"),
            "filled_avg_price": order.get("filled_avg_price"),
            "submitted_at": order.get("submitted_at"),
        },
        "sync": sync_result,
        "sync_status": "synced" if sync_result else "pending",
        "sync_error": sync_error,
        "synced_portfolio": sync_result,
    }

@router.post('/initialize')
async def initialize_portfolio(payload: InitializePortfolioIn, user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    for pos in payload.positions:
        await _validate_symbol(pos.symbol)

    try:
        p = await db.fetch_one(
            query=(
                "INSERT INTO portfolios (owner, cash_balance) "
                "VALUES (:owner, :cash) "
                "ON CONFLICT (owner) DO UPDATE SET cash_balance = EXCLUDED.cash_balance, updated_at = now() "
                "RETURNING id, cash_balance, updated_at"
            ),
            values={"owner": owner, "cash": payload.cash_balance},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    portfolio_id = p["id"]

    # Upsert positions (best-effort) if provided.
    for pos in payload.positions:
        symbol = _normalize_symbol(pos.symbol)
        if pos.quantity == 0:
            continue
        try:
            await db.execute(
                query=(
                    "INSERT INTO portfolio_positions (portfolio_id, symbol, quantity, avg_price) "
                    "VALUES (:pid, :symbol, :qty, :avg) "
                    "ON CONFLICT (portfolio_id, symbol) DO UPDATE SET "
                    "quantity = EXCLUDED.quantity, avg_price = EXCLUDED.avg_price, updated_at = now()"
                ),
                values={
                    "pid": portfolio_id,
                    "symbol": symbol,
                    "qty": pos.quantity,
                    "avg": pos.avg_price,
                },
            )
        except Exception as e:
            hint = _maybe_schema_hint(e, "portfolio_positions")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    return {
        "status": "ok",
        "portfolio_id": str(portfolio_id),
        "cash_balance": str(p["cash_balance"]),
        "updated_at": str(p["updated_at"]),
    }


@router.put('/cash')
async def update_cash(payload: UpdateCashIn, user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        row = await db.fetch_one(
            query=(
                "UPDATE portfolios SET cash_balance = :cash, updated_at = now() "
                "WHERE owner = :owner RETURNING id, cash_balance, updated_at"
            ),
            values={"owner": owner, "cash": payload.cash_balance},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not initialized")

    return {
        "status": "ok",
        "portfolio_id": str(row["id"]),
        "cash_balance": str(row["cash_balance"]),
        "updated_at": str(row["updated_at"]),
    }


@router.post('/positions')
async def upsert_position(payload: PositionIn, user=Depends(get_current_user)):
    symbol = _normalize_symbol(payload.symbol)
    await _validate_symbol(symbol)

    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        p = await db.fetch_one(
            query="SELECT id FROM portfolios WHERE owner = :owner",
            values={"owner": owner},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not p:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Initialize your portfolio first")

    portfolio_id = p["id"]

    if payload.quantity == 0:
        try:
            await db.execute(
                query="DELETE FROM portfolio_positions WHERE portfolio_id = :pid AND symbol = :symbol",
                values={"pid": portfolio_id, "symbol": symbol},
            )
        except Exception as e:
            hint = _maybe_schema_hint(e, "portfolio_positions")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))
        return {"status": "deleted", "symbol": symbol}

    try:
        row = await db.fetch_one(
            query=(
                "INSERT INTO portfolio_positions (portfolio_id, symbol, quantity, avg_price) "
                "VALUES (:pid, :symbol, :qty, :avg) "
                "ON CONFLICT (portfolio_id, symbol) DO UPDATE SET "
                "quantity = EXCLUDED.quantity, avg_price = EXCLUDED.avg_price, updated_at = now() "
                "RETURNING id, symbol, quantity, avg_price, updated_at"
            ),
            values={
                "pid": portfolio_id,
                "symbol": symbol,
                "qty": payload.quantity,
                "avg": payload.avg_price,
            },
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolio_positions")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    return {
        "status": "ok",
        "position": {
            "id": str(row["id"]),
            "symbol": row["symbol"],
            "quantity": str(row["quantity"]),
            "avg_price": str(row["avg_price"]),
            "updated_at": str(row["updated_at"]),
        },
    }


@router.delete('/positions/{position_id}')
async def delete_position(position_id: str, user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        p = await db.fetch_one(query="SELECT id FROM portfolios WHERE owner = :owner", values={"owner": owner})
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not initialized")

    try:
        row = await db.fetch_one(
            query=(
                "DELETE FROM portfolio_positions "
                "WHERE id = :id AND portfolio_id = :pid "
                "RETURNING id"
            ),
            values={"id": position_id, "pid": p["id"]},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolio_positions")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found")

    return {"status": "deleted", "id": position_id}

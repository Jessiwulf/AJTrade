import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.core import brokers, trade_stats
from app.core.alpaca import list_alpaca_filled_orders, submit_alpaca_order
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


def _owner(user) -> str:
    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")
    return owner


async def _resolve_provider(owner: str, provider: Optional[str]) -> str:
    return brokers.normalize_provider(provider) if provider else await brokers.get_active_provider(owner)


# --- Schema: one portfolio mirror per member and provider -----------------------------------
_PORTFOLIO_SCHEMA_READY = False


async def _ensure_portfolio_schema(db) -> None:
    global _PORTFOLIO_SCHEMA_READY
    if _PORTFOLIO_SCHEMA_READY:
        return
    await db.execute(
        query=f"ALTER TABLE portfolios ADD COLUMN IF NOT EXISTS provider text NOT NULL DEFAULT '{brokers.ALPACA}'"
    )
    # Replace UNIQUE(owner) with UNIQUE(owner, provider). Look the old constraint up by its columns:
    # its name differs between databases.
    rows = await db.fetch_all(
        query=(
            "SELECT c.conname FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
            "WHERE t.relname = 'portfolios' AND c.contype = 'u' "
            "AND array_length(c.conkey, 1) = 1 "
            "AND c.conkey[1] = (SELECT attnum FROM pg_attribute WHERE attrelid = t.oid AND attname = 'owner')"
        )
    )
    for row in rows:
        await db.execute(query=f'ALTER TABLE portfolios DROP CONSTRAINT IF EXISTS "{row["conname"]}"')
    await db.execute(
        query="CREATE UNIQUE INDEX IF NOT EXISTS idx_portfolios_owner_provider ON portfolios(owner, provider)"
    )
    _PORTFOLIO_SCHEMA_READY = True


async def _get_owner_portfolio(db, owner: str, provider: str = brokers.ALPACA):
    await _ensure_portfolio_schema(db)
    return await db.fetch_one(
        query="SELECT id, cash_balance, updated_at FROM portfolios WHERE owner = :owner AND provider = :provider",
        values={"owner": owner, "provider": provider},
    )


async def _ensure_owner_portfolio(db, owner: str, cash_balance: Decimal, provider: str = brokers.ALPACA) -> Dict[str, Any]:
    await _ensure_portfolio_schema(db)
    return await db.fetch_one(
        query=(
            "INSERT INTO portfolios (owner, provider, cash_balance) "
            "VALUES (:owner, :provider, :cash) "
            "ON CONFLICT (owner, provider) DO UPDATE SET cash_balance = EXCLUDED.cash_balance, updated_at = now() "
            "RETURNING id, cash_balance, updated_at"
        ),
        values={"owner": owner, "provider": provider, "cash": cash_balance},
    )


async def _upsert_watchlist_symbol(db, owner: str, symbol: str) -> None:
    await db.execute(
        query=(
            "INSERT INTO watchlists (owner, symbol) VALUES (:owner, :symbol) "
            "ON CONFLICT (owner, symbol) DO NOTHING"
        ),
        values={"owner": owner, "symbol": symbol},
    )


async def _sync_portfolio(db, owner: str, provider: str, *, add_to_watchlist: bool = True) -> Dict[str, Any]:
    """Mirrors the broker's cash and positions into the provider's local portfolio."""
    try:
        account = await brokers.get_account(owner, provider)
    except HTTPException as exc:
        raise brokers.not_connected(provider, exc)
    cash_balance = Decimal(_decimal_string(account.get("cash"), "0"))

    async with db.transaction():
        portfolio = await _ensure_owner_portfolio(db, owner, cash_balance, provider)
        portfolio_id = portfolio["id"]
        await db.execute(query="DELETE FROM portfolio_positions WHERE portfolio_id = :pid", values={"pid": portfolio_id})

        synced_positions = []
        for position in account["positions"]:
            symbol = _normalize_symbol(str(position.get("symbol") or ""))
            quantity = Decimal(_decimal_string(position.get("qty"), "0"))
            avg_price = Decimal(_decimal_string(position.get("avg_entry_price"), "0"))
            if not symbol or quantity <= 0:
                continue
            await db.execute(
                query=(
                    "INSERT INTO portfolio_positions (portfolio_id, symbol, quantity, avg_price) "
                    "VALUES (:pid, :symbol, :qty, :avg)"
                ),
                values={"pid": portfolio_id, "symbol": symbol, "qty": quantity, "avg": avg_price},
            )
            if add_to_watchlist:
                await _upsert_watchlist_symbol(db, owner, symbol)
            synced_positions.append({"symbol": symbol, "quantity": str(quantity), "avg_price": str(avg_price)})

    return {
        "provider": provider,
        "portfolio_id": str(portfolio["id"]),
        "cash_balance": str(portfolio["cash_balance"]),
        "positions": synced_positions,
        "account": {
            "account_id": account.get("account_id"),
            "status": account.get("status"),
            "currency": account.get("currency"),
            "buying_power": account.get("buying_power"),
            "portfolio_value": account.get("equity"),
            "cash": account.get("cash"),
        },
    }


# Backwards-compatible name used by the trading bot.
async def _sync_portfolio_from_alpaca(db, owner: str, *, add_to_watchlist: bool = True) -> Dict[str, Any]:
    return await _sync_portfolio(db, owner, brokers.ALPACA, add_to_watchlist=add_to_watchlist)


@router.get('')
async def get_portfolio(provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    db = get_database()
    owner = _owner(user)
    provider = await _resolve_provider(owner, provider)
    try:
        p = await _get_owner_portfolio(db, owner, provider)
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not initialized")

    rows = await db.fetch_all(
        query=(
            "SELECT id, symbol, quantity, avg_price, updated_at "
            "FROM portfolio_positions WHERE portfolio_id = :pid ORDER BY updated_at DESC"
        ),
        values={"pid": p["id"]},
    )
    return {
        "id": str(p["id"]),
        "provider": provider,
        "currency": brokers.PROVIDER_INFO[provider]["currency"],
        "cash_balance": str(p["cash_balance"]),
        "updated_at": str(p["updated_at"]),
        "positions": [
            {
                "id": str(r["id"]),
                "symbol": r["symbol"],
                "quantity": str(r["quantity"]),
                "avg_price": str(r["avg_price"]),
                "updated_at": str(r["updated_at"]),
            }
            for r in rows
        ],
    }


@router.get('/paper-account')
async def get_paper_account(provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    """The active provider's brokerage account (Alpaca paper or Settrade), live from the broker."""
    owner = _owner(user)
    provider = await _resolve_provider(owner, provider)
    try:
        account = await brokers.get_account(owner, provider)
    except HTTPException as exc:
        raise brokers.not_connected(provider, exc)
    return {
        "provider": provider,
        "account_id": account.get("account_id"),
        "status": account.get("status"),
        "currency": account.get("currency"),
        "cash": _decimal_string(account.get("cash"), "0"),
        "buying_power": _decimal_string(account.get("buying_power"), "0"),
        "portfolio_value": _decimal_string(account.get("equity"), "0"),
        "positions": [
            {
                "symbol": _normalize_symbol(str(p.get("symbol") or "")),
                "quantity": _decimal_string(p.get("qty"), "0"),
                "avg_entry_price": _decimal_string(p.get("avg_entry_price"), "0"),
                "market_value": _decimal_string(p.get("market_value"), "0"),
                "unrealized_pl": _decimal_string(p.get("unrealized_pl"), "0"),
            }
            for p in account["positions"]
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


_BANGKOK = timezone(timedelta(hours=7))


def _parse_settrade_time(value: Any) -> Optional[datetime]:
    """Settrade reports local Bangkok time, often without an offset ("2026-10-28T10:15:30" or "10:15:30")."""
    text = str(value or "").strip().replace("Z", "+00:00")
    if not text:
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            clock = datetime.strptime(text[:8], "%H:%M:%S").time()
        except ValueError:
            return None
        parsed = datetime.combine(datetime.now(_BANGKOK).date(), clock)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_BANGKOK)


async def _broker_fills(db, owner: str, provider: str) -> List[Dict[str, Any]]:
    """Filled orders as {broker_order_id, symbol, side, quantity, price, source, notes, created_at}."""
    fills = []
    if provider == brokers.SETTRADE:
        from app.core import settrade

        bot_orders = await _settrade_bot_order_numbers(db, owner)
        for fill in settrade.normalize_trades(await settrade.get_trades(owner)):
            fills.append(
                {
                    "broker_order_id": fill["id"],
                    "symbol": fill["symbol"],
                    "side": fill["side"],
                    "quantity": Decimal(str(fill["qty"])),
                    "price": Decimal(str(fill["price"])),
                    "source": "bot" if fill.get("order_no") in bot_orders else "settrade",
                    "notes": f"Settrade order {fill.get('order_no') or '-'}",
                    "created_at": _parse_settrade_time(fill.get("time")),
                }
            )
        return fills

    for order in await list_alpaca_filled_orders(owner):
        fills.append(
            {
                "broker_order_id": str(order.get("id")),
                "symbol": _normalize_symbol(str(order.get("symbol") or "")),
                "side": str(order.get("side") or "").upper(),
                "quantity": Decimal(_decimal_string(order.get("filled_qty"), "0")),
                "price": Decimal(_decimal_string(order.get("filled_avg_price"), "0")),
                "source": _order_source(order.get("client_order_id")),
                "notes": f"Alpaca order {order.get('id')}",
                "created_at": _parse_alpaca_time(order.get("filled_at") or order.get("submitted_at")),
            }
        )
    return fills


async def _settrade_bot_order_numbers(db, owner: str) -> set:
    try:
        rows = await db.fetch_all(
            query="SELECT broker_order_id FROM bot_execution_logs WHERE owner_id = :owner AND broker_order_id LIKE 'st-%'",
            values={"owner": owner},
        )
    except Exception:
        return set()
    return {str(r["broker_order_id"])[3:] for r in rows}


async def sync_trade_history(db, owner: str, provider: str) -> int:
    """Makes trading_history mirror the broker's fills (actual fill price and quantity).

    Alpaca returns the account's whole order history; Settrade returns today's trades only, so its
    history accumulates in AJTrade from the first sync on. Realized P/L on sells uses the average-cost
    method over the recorded fills, re-checked for every sell on each sync. A sell whose buys are not
    recorded takes the P/L the bot booked when it placed that sell, otherwise none rather than a guess.
    Returns the number of new fills recorded.
    """
    await _ensure_history_schema(db)
    fills = await _broker_fills(db, owner, provider)
    portfolio = await _get_owner_portfolio(db, owner, provider)
    if not portfolio:
        await _sync_portfolio(db, owner, provider, add_to_watchlist=False)
        portfolio = await _get_owner_portfolio(db, owner, provider)
    if not portfolio:
        return 0
    portfolio_id = portfolio["id"]

    existing = await db.fetch_all(
        query=(
            "SELECT id, broker_order_id, symbol, trade_type, quantity, price, pl, notes, created_at FROM trading_history "
            "WHERE portfolio_id = :pid AND broker_order_id IS NOT NULL"
        ),
        values={"pid": portfolio_id},
    )
    known = {r["broker_order_id"] for r in existing}
    new_fills = [f for f in fills if f["broker_order_id"] not in known and f["quantity"] > 0 and f["price"] > 0 and f["side"] in {"BUY", "SELL"}]

    # Replay every recorded fill plus the new ones in time order. Every sell is re-priced on each
    # sync, so a sell recorded before its buys (or saved without P/L) is corrected later on.
    timeline = [
        {"id": r["broker_order_id"], "symbol": r["symbol"], "side": r["trade_type"], "quantity": r["quantity"],
         "price": r["price"], "at": r["created_at"]}
        for r in existing
    ] + [
        {"id": f["broker_order_id"], "symbol": f["symbol"], "side": f["side"], "quantity": f["quantity"], "price": f["price"],
         "at": f["created_at"]}
        for f in new_fills
    ]
    realized = trade_stats.replay_realized_pl(timeline)
    # Sells whose buys are not in the history (opened before it starts): use the P/L the bot booked
    # when it closed the position, if the bot placed that sell.
    unpriced = [f["id"] for f in timeline if f["side"] == "SELL" and realized.get(f["id"]) is None]
    if unpriced:
        notes = {r["broker_order_id"]: r["notes"] for r in existing}
        notes.update({f["broker_order_id"]: f["notes"] for f in new_fills})
        booked = await _bot_booked_pl(db, owner)
        for order_id in unpriced:
            pl = booked.get(order_id)
            realized[order_id] = pl if pl is not None else booked.get(_settrade_order_key(notes.get(order_id)))

    corrections = [
        {"id": r["id"], "pl": realized.get(r["broker_order_id"])}
        for r in existing
        if r["trade_type"] == "SELL" and _pl_changed(r["pl"], realized.get(r["broker_order_id"]))
    ]

    rows = [
        {
            "portfolio_id": portfolio_id,
            "symbol": f["symbol"],
            "trade_type": f["side"],
            "quantity": f["quantity"],
            "price": f["price"],
            "notional": (f["quantity"] * f["price"]).quantize(Decimal("0.01")),
            "pl": realized.get(f["broker_order_id"]) if f["side"] == "SELL" else None,
            "signal_source": f["source"],
            "notes": f["notes"],
            "broker_order_id": f["broker_order_id"],
            "created_at": f["created_at"],
        }
        for f in new_fills
    ]

    async with db.transaction():
        # Rows not backed by a broker fill (simulated or estimated entries) don't belong in the mirror.
        await db.execute(
            query="DELETE FROM trading_history WHERE portfolio_id = :pid AND broker_order_id IS NULL",
            values={"pid": portfolio_id},
        )
        if rows:
            await db.execute_many(
                query=(
                    "INSERT INTO trading_history "
                    "(portfolio_id, symbol, trade_type, quantity, price, notional, fee, pl, signal_source, notes, broker_order_id, created_at) "
                    "VALUES (:portfolio_id, :symbol, :trade_type, :quantity, :price, :notional, 0, :pl, :signal_source, :notes, "
                    ":broker_order_id, COALESCE(:created_at, now())) "
                    "ON CONFLICT (portfolio_id, broker_order_id) DO NOTHING"
                ),
                values=rows,
            )
        for correction in corrections:
            await db.execute(query="UPDATE trading_history SET pl = :pl WHERE id = :id", values=correction)
    if corrections:
        logger.info("trade_history_pl_corrected owner=%s provider=%s sells=%d", owner, provider, len(corrections))
    return len(rows)


def _pl_changed(stored: Any, computed: Optional[Decimal]) -> bool:
    if stored is None or computed is None:
        return (stored is None) != (computed is None)
    return Decimal(str(stored)).quantize(Decimal("0.01")) != computed.quantize(Decimal("0.01"))


def _settrade_order_key(notes: Any) -> Optional[str]:
    """"Settrade order 12345" -> "st-12345" (how the bot log stores Settrade order numbers)."""
    match = re.match(r"Settrade order (\S+)", str(notes or ""))
    return f"st-{match.group(1)}" if match and match.group(1) != "-" else None


async def _bot_booked_pl(db, owner: str) -> Dict[str, Decimal]:
    """Realized P/L the bot recorded for each of its filled sells, by broker order id."""
    try:
        rows = await db.fetch_all(
            query=(
                "SELECT broker_order_id, signal_received FROM bot_execution_logs "
                "WHERE owner_id = :owner AND action_taken = 'Executed' AND broker_order_id IS NOT NULL"
            ),
            values={"owner": owner},
        )
    except Exception:
        return {}
    booked: Dict[str, Decimal] = {}
    for row in rows:
        try:
            signal = json.loads(row["signal_received"] or "{}")
        except (TypeError, ValueError):
            continue
        if not isinstance(signal, dict) or str(signal.get("side") or "").upper() != "SELL":
            continue
        if signal.get("estimated_pnl") is None:
            continue
        try:
            booked[str(row["broker_order_id"])] = Decimal(str(signal["estimated_pnl"])).quantize(Decimal("0.01"))
        except Exception:
            continue
    return booked


async def sync_trade_history_from_alpaca(db, owner: str) -> int:
    return await sync_trade_history(db, owner, brokers.ALPACA)


async def sync_account(db, owner: str, provider: str) -> Dict[str, Any]:
    """Cash, positions and trade history, all from the provider's broker."""
    result = await _sync_portfolio(db, owner, provider)
    try:
        result["new_fills"] = await sync_trade_history(db, owner, provider)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("trade_history_sync_failed owner=%s provider=%s error=%s", owner, provider, exc)
        result["new_fills"] = None
    return result


async def sync_account_from_alpaca(db, owner: str) -> Dict[str, Any]:
    return await sync_account(db, owner, brokers.ALPACA)


@router.post('/sync-paper')
async def sync_paper_portfolio(provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    owner = _owner(user)
    provider = await _resolve_provider(owner, provider)
    try:
        return await sync_account(get_database(), owner, provider)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


async def _place_settrade_order(owner: str, symbol: str, side: str, payload: PaperOrderIn) -> Dict[str, Any]:
    """Day limit order at the current price (rounded to the SET tick), in whole board lots."""
    from app.api.market import get_market_price
    from app.core import settrade

    price = Decimal(str(await get_market_price(symbol)))
    limit_price = settrade.round_to_tick(price)
    if payload.quantity is not None:
        volume = int(payload.quantity)
        if volume != payload.quantity or volume % settrade.BOARD_LOT:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Thai stocks trade in board lots: quantity must be a multiple of {settrade.BOARD_LOT}.")
    else:
        volume = settrade.board_lot_volume(payload.notional, limit_price)
        if volume <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"฿{payload.notional} is less than one board lot ({settrade.BOARD_LOT} shares at ฿{limit_price}).",
            )
    order = await settrade.place_order(owner, symbol=symbol, side=side, volume=volume, price=limit_price)
    return {
        "id": order.get("orderNo"),
        "client_order_id": None,
        "symbol": symbol,
        "side": side.lower(),
        "status": order.get("showStatus") or order.get("status") or "submitted",
        "qty": str(volume),
        "notional": str((limit_price * volume).quantize(Decimal("0.01"))),
        "limit_price": str(limit_price),
        "filled_qty": None,
        "filled_avg_price": None,
        "submitted_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post('/paper-orders')
async def place_paper_order(payload: PaperOrderIn, user=Depends(get_current_user)):
    db = get_database()
    owner = _owner(user)

    symbol = _normalize_symbol(payload.symbol)
    await _validate_symbol(symbol)

    side = (payload.side or "").strip().upper()
    if side not in {"BUY", "SELL"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="side must be BUY or SELL")
    if payload.quantity is None and payload.notional is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="quantity_or_notional_required")
    if side == "SELL" and payload.quantity is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="SELL orders require quantity")

    active = await brokers.get_active_provider(owner)
    provider = await brokers.provider_for_symbol(symbol)
    if provider != active:
        other = brokers.PROVIDER_INFO[provider]["label"]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{symbol} trades on {other}. Switch your provider to {other} in Profile & Settings to trade it.",
        )

    if provider == brokers.SETTRADE:
        order = await _place_settrade_order(owner, symbol, side, payload)
    else:
        raw = await submit_alpaca_order(
            owner,
            symbol=symbol,
            side=side,
            quantity=str(payload.quantity) if payload.quantity is not None else None,
            notional=str(payload.notional) if payload.notional is not None else None,
            client_order_id=f"ajtrade-{uuid4().hex[:24]}",
        )
        order = {
            "id": raw.get("id"),
            "client_order_id": raw.get("client_order_id"),
            "symbol": raw.get("symbol") or symbol,
            "side": raw.get("side") or side.lower(),
            "status": raw.get("status"),
            "qty": raw.get("qty"),
            "notional": raw.get("notional"),
            "filled_qty": raw.get("filled_qty"),
            "filled_avg_price": raw.get("filled_avg_price"),
            "submitted_at": raw.get("submitted_at"),
        }

    await _upsert_watchlist_symbol(db, owner, symbol)
    # The trade history is taken from the broker's actual fill (not an estimate made here); an order that
    # is still open (e.g. market closed) appears once it fills and the account is synced again.
    sync_result = None
    sync_error = None
    try:
        sync_result = await sync_account(db, owner, provider)
    except Exception as exc:
        sync_error = str(getattr(exc, "detail", None) or exc)
        logger.warning('paper_order_sync_pending owner=%s symbol=%s status=%s error=%s', owner, symbol, order.get('status'), sync_error)

    return {
        "provider": provider,
        "order": order,
        "sync": sync_result,
        "sync_status": "synced" if sync_result else "pending",
        "sync_error": sync_error,
        "synced_portfolio": sync_result,
    }


@router.post('/initialize')
async def initialize_portfolio(payload: InitializePortfolioIn, provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    db = get_database()
    owner = _owner(user)
    provider = await _resolve_provider(owner, provider)

    for pos in payload.positions:
        await _validate_symbol(pos.symbol)

    try:
        p = await _ensure_owner_portfolio(db, owner, payload.cash_balance, provider)
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    portfolio_id = p["id"]
    for pos in payload.positions:
        symbol = _normalize_symbol(pos.symbol)
        if pos.quantity == 0:
            continue
        await db.execute(
            query=(
                "INSERT INTO portfolio_positions (portfolio_id, symbol, quantity, avg_price) "
                "VALUES (:pid, :symbol, :qty, :avg) "
                "ON CONFLICT (portfolio_id, symbol) DO UPDATE SET "
                "quantity = EXCLUDED.quantity, avg_price = EXCLUDED.avg_price, updated_at = now()"
            ),
            values={"pid": portfolio_id, "symbol": symbol, "qty": pos.quantity, "avg": pos.avg_price},
        )

    return {
        "status": "ok",
        "provider": provider,
        "portfolio_id": str(portfolio_id),
        "cash_balance": str(p["cash_balance"]),
        "updated_at": str(p["updated_at"]),
    }


@router.put('/cash')
async def update_cash(payload: UpdateCashIn, provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    db = get_database()
    owner = _owner(user)
    provider = await _resolve_provider(owner, provider)
    await _ensure_portfolio_schema(db)
    row = await db.fetch_one(
        query=(
            "UPDATE portfolios SET cash_balance = :cash, updated_at = now() "
            "WHERE owner = :owner AND provider = :provider RETURNING id, cash_balance, updated_at"
        ),
        values={"owner": owner, "provider": provider, "cash": payload.cash_balance},
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not initialized")
    return {
        "status": "ok",
        "provider": provider,
        "portfolio_id": str(row["id"]),
        "cash_balance": str(row["cash_balance"]),
        "updated_at": str(row["updated_at"]),
    }


@router.post('/positions')
async def upsert_position(payload: PositionIn, provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    symbol = _normalize_symbol(payload.symbol)
    await _validate_symbol(symbol)
    db = get_database()
    owner = _owner(user)
    provider = await _resolve_provider(owner, provider)

    p = await _get_owner_portfolio(db, owner, provider)
    if not p:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Initialize your portfolio first")
    portfolio_id = p["id"]

    if payload.quantity == 0:
        await db.execute(
            query="DELETE FROM portfolio_positions WHERE portfolio_id = :pid AND symbol = :symbol",
            values={"pid": portfolio_id, "symbol": symbol},
        )
        return {"status": "deleted", "symbol": symbol}

    row = await db.fetch_one(
        query=(
            "INSERT INTO portfolio_positions (portfolio_id, symbol, quantity, avg_price) "
            "VALUES (:pid, :symbol, :qty, :avg) "
            "ON CONFLICT (portfolio_id, symbol) DO UPDATE SET "
            "quantity = EXCLUDED.quantity, avg_price = EXCLUDED.avg_price, updated_at = now() "
            "RETURNING id, symbol, quantity, avg_price, updated_at"
        ),
        values={"pid": portfolio_id, "symbol": symbol, "qty": payload.quantity, "avg": payload.avg_price},
    )
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
async def delete_position(position_id: str, provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    db = get_database()
    owner = _owner(user)
    provider = await _resolve_provider(owner, provider)
    p = await _get_owner_portfolio(db, owner, provider)
    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not initialized")
    row = await db.fetch_one(
        query="DELETE FROM portfolio_positions WHERE id = :id AND portfolio_id = :pid RETURNING id",
        values={"id": position_id, "pid": p["id"]},
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found")
    return {"status": "deleted", "id": position_id}

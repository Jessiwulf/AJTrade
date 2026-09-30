import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4
from typing import Any, Awaitable, Callable, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.core.db import get_database

router = APIRouter()
logger = logging.getLogger("ajtrade.trading_bot")

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,24}$")
_MODE_VALUES = {"paper", "live"}
# "Pending" = submitted to Alpaca but not filled yet (e.g. market closed); settled by reconcile_pending_orders.
_ACTION_VALUES = {"Executed", "Rejected", "Pending"}
_SCHEMA_READY = False

# How long to wait for a market order to fill before treating it as pending.
_ORDER_FILL_WAIT_SECONDS = 6
_ORDER_FAILED_STATUSES = {"canceled", "expired", "rejected", "suspended", "stopped", "done_for_day"}

# Strategy thresholds (kept in sync with the strategy descriptions in frontend/pages/automated.js).
MEAN_REVERSION_MOVE_PCT = 3.0
NEWS_MOMENTUM_SENTIMENT = 0.3
DCA_STRATEGY = "Dollar-Cost Averaging"
DEFAULT_DCA_INTERVAL_HOURS = 24


def get_bot_cooldown() -> timedelta:
    """Minimum gap between bot trades on the same asset (AJTRADE_BOT_COOLDOWN_MINUTES, default 3).

    A few seconds of grace absorbs scheduler tick jitter, so a 3-minute cooldown with 1-minute
    ticks trades on the 3rd tick rather than slipping to the 4th.
    """
    try:
        minutes = max(float(os.environ.get("AJTRADE_BOT_COOLDOWN_MINUTES", "3")), 0.0)
    except (TypeError, ValueError):
        minutes = 3.0
    return max(timedelta(minutes=minutes) - timedelta(seconds=10), timedelta(0))


def _normalize_symbol(value: str) -> str:
    # "BTC/USD" (Alpaca's crypto form) -> "BTC-USD" (the form used across the app).
    return (value or "").strip().upper().replace("/", "-")


def decide_strategy_side(strategy: str, insight: Dict[str, Any], min_confidence: int) -> Tuple[Optional[str], str]:
    """Turns an AI insight into BUY / SELL / None (hold) according to the asset's strategy.

    Returns (side or None, human-readable reason).
    """
    if insight.get("unavailable"):
        return None, "No insight available for this asset"
    ai_signal = str(insight.get("signal") or "HOLD").upper()
    # When the AI model could not run, the signal is a rule-based estimate: only News Momentum (which
    # uses the real news sentiment, not the model) may act on it.
    if insight.get("model_fallback") and strategy != "News Momentum":
        return None, "AI model unavailable for this asset; not trading on a rule-based estimate"
    try:
        confidence = int(insight.get("confidence") or 0)
        price_change = float(insight.get("price_change_pct") or 0.0)
        sentiment = float(insight.get("latest_sentiment_score") or 0.0)
    except (TypeError, ValueError):
        return None, "Insight data unavailable"

    if strategy == DCA_STRATEGY:
        # DCA ignores signals: the scheduler buys on its own timetable (see _run_dca_for_asset).
        return None, "Dollar-cost averaging buys on a schedule, not on signals"

    if strategy == "News Momentum":
        if sentiment >= NEWS_MOMENTUM_SENTIMENT:
            return "BUY", f"News sentiment {sentiment:+.2f} is strongly positive"
        if sentiment <= -NEWS_MOMENTUM_SENTIMENT:
            return "SELL", f"News sentiment {sentiment:+.2f} is strongly negative"
        return None, f"News sentiment {sentiment:+.2f} is not strong enough"

    if ai_signal not in {"BUY", "SELL"} or confidence < min_confidence:
        return None, f"AI signal {ai_signal} at {confidence}% confidence (needs BUY/SELL at {min_confidence}%+)"

    if strategy == "Mean Reversion":
        if ai_signal == "BUY" and price_change <= -MEAN_REVERSION_MOVE_PCT:
            return "BUY", f"AI BUY after a {price_change:.1f}% drop"
        if ai_signal == "SELL" and price_change >= MEAN_REVERSION_MOVE_PCT:
            return "SELL", f"AI SELL after a {price_change:+.1f}% rise"
        return None, (
            f"AI {ai_signal} but price moved {price_change:+.1f}% "
            f"(needs a {MEAN_REVERSION_MOVE_PCT:.0f}% move the other way)"
        )

    if strategy == "AI Momentum + Sentiment":
        if (ai_signal == "BUY" and sentiment > 0) or (ai_signal == "SELL" and sentiment < 0):
            return ai_signal, f"AI {ai_signal} confirmed by news sentiment {sentiment:+.2f}"
        return None, f"AI {ai_signal} but news sentiment {sentiment:+.2f} disagrees"

    # Trend Following (default)
    if (ai_signal == "BUY" and price_change > 0) or (ai_signal == "SELL" and price_change < 0):
        return ai_signal, f"AI {ai_signal} in line with the {price_change:+.1f}% trend"
    return None, f"AI {ai_signal} against the {price_change:+.1f}% trend"


def _exit_trigger(
    *,
    entry_price: Decimal,
    price: Decimal,
    trailing_stop_level: Decimal,
    stop_loss_pct: Decimal,
    trailing_stop_pct: Decimal,
    take_profit_pct: Decimal,
) -> Optional[str]:
    if take_profit_pct > 0 and price >= entry_price * (1 + take_profit_pct / 100):
        return "Take-Profit"
    if stop_loss_pct > 0 and price <= entry_price * (1 - stop_loss_pct / 100):
        return "Stop-Loss"
    # The trailing stop only takes over once it has climbed above the entry price, i.e. it locks in
    # gains; until then the absolute stop-loss is the floor.
    if trailing_stop_pct > 0 and trailing_stop_level > entry_price and price <= trailing_stop_level:
        return "Trailing Stop"
    return None


def _default_rule() -> Dict[str, Any]:
    return {
        "strategy": "Trend Following",
        "stopLossPct": 2.0,
        "trailingStopLossPct": 1.2,
        "takeProfitPct": 5.0,
        "maxCapitalPerTrade": 1000.0,
        "maxDailyLossLimit": 500.0,
    }


def _coerce_decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    try:
        return Decimal(str(value))
    except Exception:
        return default


def _safe_json_loads(raw: Optional[str]) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _compute_daily_loss(signal_payload: Dict[str, Any]) -> Decimal:
    for key in ("realized_pnl", "estimated_pnl", "pnl"):
        value = signal_payload.get(key)
        if value is None:
            continue
        amount = _coerce_decimal(value)
        if amount < 0:
            return abs(amount)
    return Decimal("0")


async def _ensure_schema() -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return

    db = get_database()
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS trading_rules ("
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            "owner_id uuid NOT NULL,"
            "asset_symbol text NOT NULL,"
            "strategy text NOT NULL DEFAULT 'Trend Following',"
            "is_active boolean NOT NULL DEFAULT false,"
            "mode text NOT NULL DEFAULT 'paper' CHECK (mode IN ('paper', 'live')),"
            "stop_loss_pct numeric(8,4) NOT NULL DEFAULT 2.0,"
            "trailing_stop_pct numeric(8,4) NOT NULL DEFAULT 1.2,"
            "take_profit_pct numeric(8,4) NOT NULL DEFAULT 5.0,"
            "max_capital numeric(18,2) NOT NULL DEFAULT 1000.00,"
            "max_daily_loss numeric(18,2) NOT NULL DEFAULT 500.00,"
            "created_at timestamptz DEFAULT now(),"
            "updated_at timestamptz DEFAULT now(),"
            "UNIQUE(owner_id, asset_symbol)"
            ")"
        )
    )
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS bot_execution_logs ("
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            "owner_id uuid NOT NULL,"
            "timestamp timestamptz NOT NULL DEFAULT now(),"
            "asset_symbol text NOT NULL,"
            "signal_received text NOT NULL,"
            "action_taken text NOT NULL CHECK (action_taken IN ('Executed', 'Rejected')),"
            "execution_price numeric(18,6),"
            "reject_reason text"
            ")"
        )
    )
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS bot_active_positions ("
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            "owner_id uuid NOT NULL,"
            "asset_symbol text NOT NULL,"
            "entry_price numeric(18,6) NOT NULL DEFAULT 0,"
            "current_price numeric(18,6) NOT NULL DEFAULT 0,"
            "quantity numeric(18,8) NOT NULL DEFAULT 0,"
            "unrealized_pl numeric(18,6) NOT NULL DEFAULT 0,"
            "trailing_stop_level numeric(18,6),"
            "opened_at timestamptz NOT NULL DEFAULT now(),"
            "updated_at timestamptz NOT NULL DEFAULT now(),"
            "UNIQUE(owner_id, asset_symbol)"
            ")"
        )
    )
    # Bot orders go to Alpaca: keep the Alpaca order id and allow the "Pending" (submitted, not filled) state.
    await db.execute(query="ALTER TABLE bot_execution_logs ADD COLUMN IF NOT EXISTS broker_order_id text")
    await db.execute(
        query="ALTER TABLE bot_execution_logs DROP CONSTRAINT IF EXISTS bot_execution_logs_action_taken_check"
    )
    await db.execute(
        query=(
            "ALTER TABLE bot_execution_logs ADD CONSTRAINT bot_execution_logs_action_taken_check "
            "CHECK (action_taken IN ('Executed', 'Rejected', 'Pending'))"
        )
    )
    await db.execute(
        query=(
            "ALTER TABLE trading_rules ADD COLUMN IF NOT EXISTS dca_interval_hours integer "
            f"NOT NULL DEFAULT {DEFAULT_DCA_INTERVAL_HOURS}"
        )
    )
    await db.execute(
        query="CREATE INDEX IF NOT EXISTS idx_trading_rules_owner_asset ON trading_rules(owner_id, asset_symbol)"
    )
    await db.execute(
        query="CREATE INDEX IF NOT EXISTS idx_bot_logs_owner_time ON bot_execution_logs(owner_id, timestamp DESC)"
    )
    await db.execute(
        query="CREATE INDEX IF NOT EXISTS idx_bot_logs_owner_asset_time ON bot_execution_logs(owner_id, asset_symbol, timestamp DESC)"
    )
    await db.execute(
        query="CREATE INDEX IF NOT EXISTS idx_bot_positions_owner_asset ON bot_active_positions(owner_id, asset_symbol)"
    )
    await db.execute(
        query="CREATE INDEX IF NOT EXISTS idx_bot_positions_owner_updated ON bot_active_positions(owner_id, updated_at DESC)"
    )
    _SCHEMA_READY = True


class AssetRuleConfig(BaseModel):
    strategy: str = "Trend Following"
    # Per-asset bot switch; None falls back to the payload-level botActive.
    isActive: Optional[bool] = None
    # 0 turns an exit off (long-term / DCA holders).
    stopLossPct: float = Field(default=2.0, ge=0, lt=100)
    trailingStopLossPct: float = Field(default=1.2, ge=0, lt=100)
    takeProfitPct: float = Field(default=5.0, ge=0, le=1000)
    maxCapitalPerTrade: float = Field(default=1000.0, ge=0)
    maxDailyLossLimit: float = Field(default=500.0, ge=0)
    dcaIntervalHours: int = Field(default=DEFAULT_DCA_INTERVAL_HOURS, ge=1, le=24 * 31)


class BotRulesPayload(BaseModel):
    botActive: bool = False
    executionMode: Literal["paper", "live"] = "paper"
    selectedAsset: Optional[str] = None
    assetRules: Dict[str, AssetRuleConfig] = Field(default_factory=dict)


class SignalData(BaseModel):
    asset_symbol: str
    signal_received: str
    side: Literal["BUY", "SELL"] = "BUY"
    requested_amount: float = Field(..., gt=0)
    requested_price: Optional[float] = Field(default=None, gt=0)
    confidence: Optional[float] = Field(default=None, ge=0, le=1)
    estimated_pnl: Optional[float] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ExecutionResult(BaseModel):
    ok: bool
    action_taken: Literal["Executed", "Rejected", "Pending"]
    asset_symbol: str
    mode: Literal["paper", "live"]
    execution_price: Optional[float] = None
    reason: Optional[str] = None


def _signal_log_payload(signal_data: SignalData, asset_symbol: str) -> Dict[str, Any]:
    return {
        "asset_symbol": asset_symbol,
        "signal_received": signal_data.signal_received,
        "side": signal_data.side,
        "requested_amount": signal_data.requested_amount,
        "requested_price": signal_data.requested_price,
        "confidence": signal_data.confidence,
        "estimated_pnl": signal_data.estimated_pnl,
        "metadata": signal_data.metadata,
    }


async def _log_execution(
    *,
    owner_id: str,
    asset_symbol: str,
    signal_payload: Dict[str, Any],
    action_taken: str,
    execution_price: Optional[float],
    reject_reason: Optional[str],
    broker_order_id: Optional[str] = None,
) -> None:
    if action_taken not in _ACTION_VALUES:
        raise ValueError("Invalid action_taken")

    await _ensure_schema()
    db = get_database()
    await db.execute(
        query=(
            "INSERT INTO bot_execution_logs "
            "(owner_id, asset_symbol, signal_received, action_taken, execution_price, reject_reason, broker_order_id) "
            "VALUES (:owner_id, :asset_symbol, :signal_received, :action_taken, :execution_price, :reject_reason, :broker_order_id)"
        ),
        values={
            "owner_id": owner_id,
            "asset_symbol": asset_symbol,
            "signal_received": json.dumps(signal_payload, separators=(",", ":"), default=str),
            "action_taken": action_taken,
            "execution_price": execution_price,
            "reject_reason": reject_reason,
            "broker_order_id": broker_order_id,
        },
    )


async def _load_rule(owner_id: str, asset_symbol: str) -> Optional[Dict[str, Any]]:
    await _ensure_schema()
    db = get_database()
    row = await db.fetch_one(
        query=(
            "SELECT asset_symbol, strategy, is_active, mode, stop_loss_pct, trailing_stop_pct, "
            "take_profit_pct, max_capital, max_daily_loss, dca_interval_hours "
            "FROM trading_rules WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol"
        ),
        values={"owner_id": owner_id, "asset_symbol": asset_symbol},
    )
    return dict(row) if row else None


async def _fetch_market_price(asset_symbol: str) -> Optional[float]:
    try:
        from app.api.market import get_market_price

        price = float(await get_market_price(asset_symbol))
    except Exception as exc:
        logger.warning("bot_market_price_failed asset=%s error=%s", asset_symbol, exc)
        return None
    return price if price > 0 else None


async def get_bot_position_quantity(owner_id: str, asset_symbol: str) -> Decimal:
    """Shares the bot itself holds. Bot sells never touch shares bought manually in the portfolio."""
    await _ensure_schema()
    row = await get_database().fetch_one(
        query="SELECT quantity FROM bot_active_positions WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol",
        values={"owner_id": owner_id, "asset_symbol": _normalize_symbol(asset_symbol)},
    )
    return _coerce_decimal(row["quantity"]) if row else Decimal("0")


async def load_bot_rule(owner_id: str, asset_symbol: str) -> Optional[Dict[str, Any]]:
    return await _load_rule(owner_id, _normalize_symbol(asset_symbol))


async def _persist_active_position(
    *,
    owner_id: str,
    asset_symbol: str,
    side: str,
    execution_price: Optional[float],
    requested_amount: float,
    trailing_stop_pct: Any,
) -> None:
    await _ensure_schema()
    if execution_price is None:
        return

    fill_price = _coerce_decimal(execution_price, Decimal("0"))
    if fill_price <= 0:
        return

    requested = _coerce_decimal(requested_amount, Decimal("0"))
    if requested <= 0:
        return

    db = get_database()
    trailing_pct = _coerce_decimal(trailing_stop_pct, Decimal("0"))
    trailing_multiplier = Decimal("1") - (trailing_pct / Decimal("100"))
    trailing_level = (
        (fill_price * trailing_multiplier).quantize(Decimal("0.000001"))
        if trailing_pct > 0 and trailing_multiplier > 0
        else None
    )
    fill_quantity = (requested / fill_price).quantize(Decimal("0.00000001"))

    existing = await db.fetch_one(
        query=(
            "SELECT entry_price, current_price, quantity, trailing_stop_level "
            "FROM bot_active_positions "
            "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol"
        ),
        values={"owner_id": owner_id, "asset_symbol": asset_symbol},
    )

    normalized_side = str(side or "BUY").upper()

    if normalized_side == "SELL":
        if not existing:
            return

        existing_qty = _coerce_decimal(existing["quantity"], Decimal("0"))
        remaining_qty = existing_qty - fill_quantity
        if remaining_qty <= 0:
            await db.execute(
                query=(
                    "DELETE FROM bot_active_positions "
                    "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol"
                ),
                values={"owner_id": owner_id, "asset_symbol": asset_symbol},
            )
            return

        entry_price = _coerce_decimal(existing["entry_price"], Decimal("0"))
        unrealized = ((fill_price - entry_price) * remaining_qty).quantize(Decimal("0.000001"))
        next_trailing = trailing_level
        existing_trailing = _coerce_decimal(existing["trailing_stop_level"], Decimal("0"))
        if existing_trailing > 0 and next_trailing is not None:
            next_trailing = max(existing_trailing, next_trailing)

        await db.execute(
            query=(
                "UPDATE bot_active_positions "
                "SET current_price = :current_price, quantity = :quantity, unrealized_pl = :unrealized_pl, "
                "trailing_stop_level = :trailing_stop_level, updated_at = now() "
                "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol"
            ),
            values={
                "owner_id": owner_id,
                "asset_symbol": asset_symbol,
                "current_price": float(fill_price),
                "quantity": float(remaining_qty),
                "unrealized_pl": float(unrealized),
                "trailing_stop_level": float(next_trailing) if next_trailing is not None else None,
            },
        )
        return

    if not existing:
        await db.execute(
            query=(
                "INSERT INTO bot_active_positions "
                "(owner_id, asset_symbol, entry_price, current_price, quantity, unrealized_pl, trailing_stop_level, opened_at, updated_at) "
                "VALUES (:owner_id, :asset_symbol, :entry_price, :current_price, :quantity, :unrealized_pl, :trailing_stop_level, now(), now())"
            ),
            values={
                "owner_id": owner_id,
                "asset_symbol": asset_symbol,
                "entry_price": float(fill_price),
                "current_price": float(fill_price),
                "quantity": float(fill_quantity),
                "unrealized_pl": 0.0,
                "trailing_stop_level": float(trailing_level) if trailing_level is not None else None,
            },
        )
        return

    existing_qty = _coerce_decimal(existing["quantity"], Decimal("0"))
    next_qty = existing_qty + fill_quantity
    if next_qty <= 0:
        next_qty = fill_quantity

    existing_entry = _coerce_decimal(existing["entry_price"], Decimal("0"))
    weighted_entry = ((existing_entry * existing_qty) + (fill_price * fill_quantity)) / next_qty
    weighted_entry = weighted_entry.quantize(Decimal("0.000001"))
    unrealized = ((fill_price - weighted_entry) * next_qty).quantize(Decimal("0.000001"))

    existing_trailing = _coerce_decimal(existing["trailing_stop_level"], Decimal("0"))
    next_trailing = trailing_level
    if existing_trailing > 0 and next_trailing is not None:
        next_trailing = max(existing_trailing, next_trailing)

    await db.execute(
        query=(
            "UPDATE bot_active_positions "
            "SET entry_price = :entry_price, current_price = :current_price, quantity = :quantity, "
            "unrealized_pl = :unrealized_pl, trailing_stop_level = :trailing_stop_level, updated_at = now() "
            "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol"
        ),
        values={
            "owner_id": owner_id,
            "asset_symbol": asset_symbol,
            "entry_price": float(weighted_entry),
            "current_price": float(fill_price),
            "quantity": float(next_qty),
            "unrealized_pl": float(unrealized),
            "trailing_stop_level": float(next_trailing) if next_trailing is not None else None,
        },
    )


async def _fetch_active_positions(owner_id: str) -> list[Dict[str, Any]]:
    await _ensure_schema()
    db = get_database()
    rows = await db.fetch_all(
        query=(
            "SELECT asset_symbol, entry_price, current_price, quantity, unrealized_pl, trailing_stop_level, updated_at "
            "FROM bot_active_positions "
            "WHERE owner_id = :owner_id "
            "ORDER BY updated_at DESC"
        ),
        values={"owner_id": owner_id},
    )
    positions: list[Dict[str, Any]] = []
    for row in rows:
        positions.append(
            {
                "asset": row["asset_symbol"],
                "entryPrice": float(row["entry_price"] or 0),
                "currentPrice": float(row["current_price"] or 0),
                "quantity": float(row["quantity"] or 0),
                "unrealizedPl": float(row["unrealized_pl"] or 0),
                "trailingStopLevel": float(row["trailing_stop_level"] or 0),
            }
        )
    return positions


def _alpaca_qty(quantity: Decimal) -> str:
    """Alpaca accepts up to 9 decimal places; round down so we never sell more than is held."""
    return format(quantity.quantize(Decimal("0.000000001"), rounding=ROUND_DOWN).normalize(), "f")


def _alpaca_error_reason(exc: HTTPException) -> str:
    detail = str(exc.detail or "").strip()
    if exc.status_code == status.HTTP_404_NOT_FOUND and "key not found" in detail.lower():
        return "Rejected: Alpaca Not Connected"
    return f"Rejected: Alpaca - {detail or 'order failed'}"


async def _get_alpaca_position_quantities(owner_id: str) -> Dict[str, Decimal]:
    from app.core.alpaca import get_alpaca_positions

    quantities: Dict[str, Decimal] = {}
    for position in await get_alpaca_positions(owner_id):
        symbol = _normalize_symbol(str(position.get("symbol") or ""))
        if symbol:
            quantities[symbol] = _coerce_decimal(position.get("qty"))
    return quantities


def _order_outcome(order: Dict[str, Any]) -> Dict[str, Any]:
    order_status = str(order.get("status") or "").lower()
    outcome: Dict[str, Any] = {"order_id": order.get("id"), "status": order_status}
    if order_status == "filled":
        outcome.update(
            state="filled",
            fill_price=_coerce_decimal(order.get("filled_avg_price")),
            filled_qty=_coerce_decimal(order.get("filled_qty")),
        )
    elif order_status in _ORDER_FAILED_STATUSES:
        outcome.update(state="rejected", message=f"Rejected: Alpaca order {order_status}")
    else:
        outcome.update(state="pending")
    return outcome


async def _get_alpaca_order(owner_id: str, order_id: str) -> Dict[str, Any]:
    from app.core.alpaca import alpaca_request

    order = await alpaca_request(owner_id, "GET", f"/v2/orders/{order_id}")
    return order if isinstance(order, dict) else {}


async def _submit_order_to_alpaca(
    owner_id: str,
    signal_data: SignalData,
    *,
    sell_quantity: Optional[Decimal] = None,
) -> Dict[str, Any]:
    """Sends a market order to the owner's Alpaca paper account and waits briefly for the fill.

    BUYs spend requested_amount dollars (whole shares if the asset is not fractionable); SELLs sell
    sell_quantity shares. Returns {"state": "filled" | "pending" | "rejected", ...}.
    """
    from app.core.alpaca import submit_alpaca_order

    asset_symbol = _normalize_symbol(signal_data.asset_symbol)
    client_order_id = f"ajbot-{uuid4().hex[:24]}"
    try:
        if signal_data.side == "BUY":
            notional = _coerce_decimal(signal_data.requested_amount).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            try:
                order = await submit_alpaca_order(
                    owner_id, symbol=asset_symbol, side="BUY", notional=str(notional), client_order_id=client_order_id
                )
            except HTTPException as exc:
                price = _coerce_decimal(signal_data.requested_price)
                if "fractional" not in str(exc.detail).lower() or price <= 0:
                    raise
                # Not fractionable: buy the whole shares the amount covers.
                whole_shares = int(notional / price)
                if whole_shares < 1:
                    return {"state": "rejected", "message": "Rejected: Amount Below One Share"}
                order = await submit_alpaca_order(
                    owner_id, symbol=asset_symbol, side="BUY", quantity=str(whole_shares), client_order_id=client_order_id
                )
        else:
            if not sell_quantity or sell_quantity <= 0:
                return {"state": "rejected", "message": "Rejected: No Bot Position"}
            order = await submit_alpaca_order(
                owner_id, symbol=asset_symbol, side="SELL", quantity=_alpaca_qty(sell_quantity), client_order_id=client_order_id
            )
    except HTTPException as exc:
        return {"state": "rejected", "message": _alpaca_error_reason(exc)}

    # Market orders usually fill within a second or two while the market is open.
    outcome = _order_outcome(order)
    waited = 0.0
    while outcome["state"] == "pending" and outcome.get("order_id") and waited < _ORDER_FILL_WAIT_SECONDS:
        await asyncio.sleep(1.0)
        waited += 1.0
        try:
            outcome = _order_outcome(await _get_alpaca_order(owner_id, outcome["order_id"]))
        except HTTPException:
            break
    return outcome


async def _sync_after_fill(owner_id: str, asset_symbol: str) -> None:
    """Refreshes the local portfolio (cash, positions) and trade history from Alpaca after a bot fill, so
    the recorded trade is Alpaca's actual fill."""
    from app.api.portfolio import sync_account_from_alpaca

    try:
        await sync_account_from_alpaca(get_database(), owner_id)
    except Exception as exc:
        logger.warning("bot_account_sync_failed owner=%s asset=%s error=%s", owner_id, asset_symbol, exc)


async def _apply_fill(
    owner_id: str,
    *,
    asset_symbol: str,
    side: str,
    fill_price: Decimal,
    filled_qty: Decimal,
    trailing_stop_pct: Any,
) -> Optional[Decimal]:
    """Books an Alpaca fill into the bot position and trade history. Returns realized P/L for sells."""
    realized_pl: Optional[Decimal] = None
    if side == "SELL":
        row = await get_database().fetch_one(
            query="SELECT entry_price FROM bot_active_positions WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol",
            values={"owner_id": owner_id, "asset_symbol": asset_symbol},
        )
        entry_price = _coerce_decimal(row["entry_price"]) if row else fill_price
        realized_pl = (fill_price - entry_price) * filled_qty
    try:
        await _persist_active_position(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            side=side,
            execution_price=float(fill_price),
            requested_amount=float(fill_price * filled_qty),
            trailing_stop_pct=trailing_stop_pct,
        )
    except Exception as exc:
        logger.warning("active_position_persist_failed owner=%s asset=%s side=%s error=%s", owner_id, asset_symbol, side, exc)
    await _sync_after_fill(owner_id, asset_symbol)
    return realized_pl


async def _has_pending_order(owner_id: str, asset_symbol: str, side: str) -> bool:
    rows = await get_database().fetch_all(
        query=(
            "SELECT signal_received FROM bot_execution_logs "
            "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol AND action_taken = 'Pending'"
        ),
        values={"owner_id": owner_id, "asset_symbol": asset_symbol},
    )
    return any(str(_safe_json_loads(row["signal_received"]).get("side") or "").upper() == side for row in rows)


async def _finish_order(
    *,
    owner_id: str,
    asset_symbol: str,
    signal_data: SignalData,
    outcome: Dict[str, Any],
    trailing_stop_pct: Any,
    mode: str,
    success_reason: str,
) -> ExecutionResult:
    """Logs an Alpaca order outcome and, when filled, books it into the bot position and portfolio."""
    order_id = outcome.get("order_id")
    if outcome["state"] == "rejected":
        reason = outcome.get("message") or "Rejected: Alpaca order failed"
        await _log_execution(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            signal_payload=_signal_log_payload(signal_data, asset_symbol),
            action_taken="Rejected",
            execution_price=None,
            reject_reason=reason,
            broker_order_id=order_id,
        )
        return ExecutionResult(ok=False, action_taken="Rejected", asset_symbol=asset_symbol, mode=mode, reason=reason)

    if outcome["state"] == "pending":
        reason = f"Submitted to Alpaca ({outcome.get('status') or 'accepted'}), waiting for fill"
        await _log_execution(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            signal_payload=_signal_log_payload(signal_data, asset_symbol),
            action_taken="Pending",
            execution_price=None,
            reject_reason=reason,
            broker_order_id=order_id,
        )
        return ExecutionResult(ok=True, action_taken="Pending", asset_symbol=asset_symbol, mode=mode, reason=reason)

    fill_price = outcome["fill_price"]
    realized_pl = await _apply_fill(
        owner_id,
        asset_symbol=asset_symbol,
        side=signal_data.side,
        fill_price=fill_price,
        filled_qty=outcome["filled_qty"],
        trailing_stop_pct=trailing_stop_pct,
    )
    if realized_pl is not None:
        # Realized P/L feeds the daily loss limit (see _compute_daily_loss).
        signal_data.estimated_pnl = float(realized_pl)
    await _log_execution(
        owner_id=owner_id,
        asset_symbol=asset_symbol,
        signal_payload=_signal_log_payload(signal_data, asset_symbol),
        action_taken="Executed",
        execution_price=float(fill_price),
        reject_reason=None,
        broker_order_id=order_id,
    )
    return ExecutionResult(
        ok=True,
        action_taken="Executed",
        asset_symbol=asset_symbol,
        mode=mode,
        execution_price=float(fill_price),
        reason=success_reason,
    )


async def evaluate_and_execute_trade(signal_data: SignalData, owner_id: str) -> ExecutionResult:
    """Evaluate trading constraints and execute a broker order when all checks pass."""
    asset_symbol = _normalize_symbol(signal_data.asset_symbol)
    if not asset_symbol or not _SYMBOL_RE.match(asset_symbol):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid asset symbol")

    rule = await _load_rule(owner_id, asset_symbol)
    signal_payload = _signal_log_payload(signal_data, asset_symbol)

    if not rule or not bool(rule.get("is_active")):
        reason = "Rejected: Bot Inactive"
        await _log_execution(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            signal_payload=signal_payload,
            action_taken="Rejected",
            execution_price=None,
            reject_reason=reason,
        )
        return ExecutionResult(ok=False, action_taken="Rejected", asset_symbol=asset_symbol, mode="paper", reason=reason)

    mode = str(rule.get("mode") or "paper").lower()
    if mode not in _MODE_VALUES:
        mode = "paper"
    if mode == "live":
        # Orders go to the Alpaca paper account; real-money trading is not wired up.
        reason = "Rejected: Live Trading Not Enabled"
        await _log_execution(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            signal_payload=signal_payload,
            action_taken="Rejected",
            execution_price=None,
            reject_reason=reason,
        )
        return ExecutionResult(ok=False, action_taken="Rejected", asset_symbol=asset_symbol, mode=mode, reason=reason)

    # Quote used for sizing (the fill price comes from Alpaca).
    if not signal_data.requested_price:
        signal_data.requested_price = await _fetch_market_price(asset_symbol)
        signal_payload["requested_price"] = signal_data.requested_price

    # Step 2: Global circuit breaker based on today's accumulated losses from executed signals.
    db = get_database()
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    daily_rows = await db.fetch_all(
        query=(
            "SELECT signal_received FROM bot_execution_logs "
            "WHERE owner_id = :owner_id AND timestamp >= :today_start AND action_taken = 'Executed'"
        ),
        values={"owner_id": owner_id, "today_start": today_start},
    )
    total_daily_loss = Decimal("0")
    for row in daily_rows:
        signal_record = _safe_json_loads(row["signal_received"])
        total_daily_loss += _compute_daily_loss(signal_record)

    max_daily_loss = _coerce_decimal(rule.get("max_daily_loss"), Decimal("0"))
    if max_daily_loss > 0 and total_daily_loss >= max_daily_loss:
        reason = "Rejected: Daily Loss Limit Hit"
        await _log_execution(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            signal_payload=signal_payload,
            action_taken="Rejected",
            execution_price=None,
            reject_reason=reason,
        )
        return ExecutionResult(ok=False, action_taken="Rejected", asset_symbol=asset_symbol, mode=mode, reason=reason)

    # Step 3: Signal cooldown to prevent repetitive fills in a short interval.
    cooldown_start = datetime.now(timezone.utc) - get_bot_cooldown()
    recent_trade = await db.fetch_one(
        query=(
            "SELECT id FROM bot_execution_logs "
            "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol "
            "AND action_taken IN ('Executed', 'Pending') AND timestamp >= :cooldown_start "
            "ORDER BY timestamp DESC LIMIT 1"
        ),
        values={"owner_id": owner_id, "asset_symbol": asset_symbol, "cooldown_start": cooldown_start},
    )
    if recent_trade:
        reason = "Rejected: Cooldown Period"
        await _log_execution(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            signal_payload=signal_payload,
            action_taken="Rejected",
            execution_price=None,
            reject_reason=reason,
        )
        return ExecutionResult(ok=False, action_taken="Rejected", asset_symbol=asset_symbol, mode=mode, reason=reason)

    # Step 4: Position sizing guardrail against max capital allocation.
    max_capital = _coerce_decimal(rule.get("max_capital"), Decimal("0"))
    if max_capital > 0 and Decimal(str(signal_data.requested_amount)) > max_capital:
        reason = "Rejected: Max Capital Exceeded"
        await _log_execution(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            signal_payload=signal_payload,
            action_taken="Rejected",
            execution_price=None,
            reject_reason=reason,
        )
        return ExecutionResult(ok=False, action_taken="Rejected", asset_symbol=asset_symbol, mode=mode, reason=reason)

    # Step 5: Alpaca guardrails. One open order per asset and side; a BUY needs enough Alpaca cash; a
    # SELL needs shares the bot bought that Alpaca still holds (manual shares are never sold).
    from app.core.alpaca import get_alpaca_account

    reason = None
    sell_quantity: Optional[Decimal] = None
    try:
        if await _has_pending_order(owner_id, asset_symbol, signal_data.side):
            reason = "Rejected: Order Already Pending"
        elif signal_data.side == "BUY":
            account = await get_alpaca_account(owner_id)
            if Decimal(str(signal_data.requested_amount)) > _coerce_decimal(account.get("cash")):
                reason = "Rejected: Insufficient Cash"
        else:
            bot_quantity = await get_bot_position_quantity(owner_id, asset_symbol)
            alpaca_quantity = (await _get_alpaca_position_quantities(owner_id)).get(asset_symbol, Decimal("0"))
            sell_quantity = min(bot_quantity, alpaca_quantity)
            if bot_quantity <= 0:
                reason = "Rejected: No Bot Position"
            elif sell_quantity <= 0:
                reason = "Rejected: No Alpaca Position"
            else:
                # Sell at most what this signal is worth, capped at the bot's shares.
                price = _coerce_decimal(signal_data.requested_price)
                if price > 0:
                    sell_quantity = min(sell_quantity, Decimal(str(signal_data.requested_amount)) / price)
    except HTTPException as exc:
        reason = _alpaca_error_reason(exc)
    if reason:
        await _log_execution(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            signal_payload=signal_payload,
            action_taken="Rejected",
            execution_price=None,
            reject_reason=reason,
        )
        return ExecutionResult(ok=False, action_taken="Rejected", asset_symbol=asset_symbol, mode=mode, reason=reason)

    # Step 6: Send the order to Alpaca and book the fill.
    outcome = await _submit_order_to_alpaca(owner_id, signal_data, sell_quantity=sell_quantity)
    return await _finish_order(
        owner_id=owner_id,
        asset_symbol=asset_symbol,
        signal_data=signal_data,
        outcome=outcome,
        trailing_stop_pct=rule.get("trailing_stop_pct"),
        mode=mode,
        success_reason="Filled on Alpaca paper",
    )


async def _execute_position_exit(
    *,
    owner_id: str,
    asset_symbol: str,
    mode: str,
    quantity: Decimal,
    price: Decimal,
    trailing_stop_pct: Any,
    trigger: str,
) -> ExecutionResult:
    """Sells the bot's whole position on Alpaca. Exits reduce risk, so they skip the entry guardrails
    (cooldown, daily loss limit, max capital)."""
    signal_data = SignalData(
        asset_symbol=asset_symbol,
        signal_received=f"EXIT: {trigger}",
        side="SELL",
        requested_amount=float(quantity * price),
        requested_price=float(price),
        metadata={"source": "bot-risk-exit", "trigger": trigger},
    )
    if mode == "live":
        outcome: Dict[str, Any] = {"state": "rejected", "message": "Rejected: Live Trading Not Enabled"}
    else:
        outcome = await _submit_order_to_alpaca(owner_id, signal_data, sell_quantity=quantity)
    return await _finish_order(
        owner_id=owner_id,
        asset_symbol=asset_symbol,
        signal_data=signal_data,
        outcome=outcome,
        trailing_stop_pct=trailing_stop_pct,
        mode=mode,
        success_reason=f"{trigger} exit filled on Alpaca paper",
    )


async def reconcile_pending_orders(owner_id: str) -> List[ExecutionResult]:
    """Settles bot orders that were still open on Alpaca (e.g. placed while the market was closed)."""
    await _ensure_schema()
    db = get_database()
    rows = await db.fetch_all(
        query=(
            "SELECT id, asset_symbol, signal_received, broker_order_id FROM bot_execution_logs "
            "WHERE owner_id = :owner_id AND action_taken = 'Pending' AND broker_order_id IS NOT NULL "
            "ORDER BY timestamp ASC"
        ),
        values={"owner_id": owner_id},
    )
    results: List[ExecutionResult] = []
    for row in rows:
        try:
            outcome = _order_outcome(await _get_alpaca_order(owner_id, row["broker_order_id"]))
        except HTTPException as exc:
            logger.warning("bot_pending_order_lookup_failed owner=%s order=%s error=%s", owner_id, row["broker_order_id"], exc.detail)
            continue
        if outcome["state"] == "pending":
            continue

        asset_symbol = row["asset_symbol"]
        payload = _safe_json_loads(row["signal_received"])
        side = str(payload.get("side") or "BUY").upper()
        if outcome["state"] == "rejected":
            await db.execute(
                query="UPDATE bot_execution_logs SET action_taken = 'Rejected', reject_reason = :reason WHERE id = :id",
                values={"id": row["id"], "reason": outcome.get("message")},
            )
            results.append(ExecutionResult(ok=False, action_taken="Rejected", asset_symbol=asset_symbol, mode="paper", reason=outcome.get("message")))
            continue

        rule = await _load_rule(owner_id, asset_symbol) or {}
        realized_pl = await _apply_fill(
            owner_id,
            asset_symbol=asset_symbol,
            side=side,
            fill_price=outcome["fill_price"],
            filled_qty=outcome["filled_qty"],
            trailing_stop_pct=rule.get("trailing_stop_pct"),
        )
        if realized_pl is not None:
            payload["estimated_pnl"] = float(realized_pl)
        await db.execute(
            query=(
                "UPDATE bot_execution_logs SET action_taken = 'Executed', execution_price = :price, "
                "reject_reason = NULL, signal_received = :payload WHERE id = :id"
            ),
            values={
                "id": row["id"],
                "price": float(outcome["fill_price"]),
                "payload": json.dumps(payload, separators=(",", ":"), default=str),
            },
        )
        results.append(
            ExecutionResult(ok=True, action_taken="Executed", asset_symbol=asset_symbol, mode="paper", execution_price=float(outcome["fill_price"]), reason="Filled on Alpaca paper")
        )
    return results


async def monitor_position_exits(
    owner_id: str,
    get_price: Callable[[str], Awaitable[Optional[float]]],
) -> List[ExecutionResult]:
    """Marks the owner's bot positions to market, ratchets their trailing stops, and closes any
    position that hit its stop-loss, trailing stop, or take-profit while its bot is active."""
    await _ensure_schema()
    db = get_database()
    rows = await db.fetch_all(
        query=(
            "SELECT p.asset_symbol, p.entry_price, p.quantity, p.trailing_stop_level, "
            "r.is_active, r.mode, r.stop_loss_pct, r.trailing_stop_pct, r.take_profit_pct "
            "FROM bot_active_positions p "
            "JOIN trading_rules r ON r.owner_id = p.owner_id AND r.asset_symbol = p.asset_symbol "
            "WHERE p.owner_id = :owner_id"
        ),
        values={"owner_id": owner_id},
    )

    results: List[ExecutionResult] = []
    if not rows:
        return results
    try:
        alpaca_quantities = await _get_alpaca_position_quantities(owner_id)
    except HTTPException as exc:
        logger.debug("bot_exit_check_skipped owner=%s error=%s", owner_id, exc.detail)
        return results

    for row in rows:
        asset_symbol = row["asset_symbol"]
        entry_price = _coerce_decimal(row["entry_price"])
        quantity = _coerce_decimal(row["quantity"])
        if entry_price <= 0 or quantity <= 0:
            continue
        # The bot can only hold what Alpaca holds (e.g. after a manual sell at Alpaca, or bot positions
        # left from before orders went to Alpaca): shrink or drop the bot position to match.
        alpaca_quantity = alpaca_quantities.get(asset_symbol, Decimal("0"))
        if alpaca_quantity < quantity:
            logger.info(
                "bot_position_reconciled owner=%s asset=%s bot_qty=%s alpaca_qty=%s", owner_id, asset_symbol, quantity, alpaca_quantity
            )
            if alpaca_quantity <= 0:
                await db.execute(
                    query="DELETE FROM bot_active_positions WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol",
                    values={"owner_id": owner_id, "asset_symbol": asset_symbol},
                )
                continue
            quantity = alpaca_quantity
            await db.execute(
                query="UPDATE bot_active_positions SET quantity = :quantity WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol",
                values={"owner_id": owner_id, "asset_symbol": asset_symbol, "quantity": float(quantity)},
            )
        live_price = await get_price(asset_symbol)
        if live_price is None or live_price <= 0:
            continue
        price = _coerce_decimal(live_price)

        trailing_stop_pct = _coerce_decimal(row["trailing_stop_pct"])
        trailing_stop_level = _coerce_decimal(row["trailing_stop_level"])
        if trailing_stop_pct > 0:
            trailing_stop_level = max(trailing_stop_level, price * (1 - trailing_stop_pct / 100))

        await db.execute(
            query=(
                "UPDATE bot_active_positions "
                "SET current_price = :current_price, unrealized_pl = :unrealized_pl, "
                "trailing_stop_level = :trailing_stop_level, updated_at = now() "
                "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol"
            ),
            values={
                "owner_id": owner_id,
                "asset_symbol": asset_symbol,
                "current_price": float(price),
                "unrealized_pl": float((price - entry_price) * quantity),
                "trailing_stop_level": float(trailing_stop_level) if trailing_stop_level > 0 else None,
            },
        )

        if not bool(row["is_active"]) or await _has_pending_order(owner_id, asset_symbol, "SELL"):
            continue
        trigger = _exit_trigger(
            entry_price=entry_price,
            price=price,
            trailing_stop_level=trailing_stop_level,
            stop_loss_pct=_coerce_decimal(row["stop_loss_pct"]),
            trailing_stop_pct=trailing_stop_pct,
            take_profit_pct=_coerce_decimal(row["take_profit_pct"]),
        )
        if not trigger:
            continue
        mode = str(row["mode"] or "paper").lower()
        results.append(
            await _execute_position_exit(
                owner_id=owner_id,
                asset_symbol=asset_symbol,
                mode=mode if mode in _MODE_VALUES else "paper",
                quantity=quantity,
                price=price,
                trailing_stop_pct=trailing_stop_pct,
                trigger=trigger,
            )
        )
    return results


def _symbol_rule_to_ui(rule_row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "isActive": bool(rule_row.get("is_active")),
        "strategy": rule_row.get("strategy") or "Trend Following",
        "stopLossPct": float(rule_row.get("stop_loss_pct") or 0),
        "trailingStopLossPct": float(rule_row.get("trailing_stop_pct") or 0),
        "takeProfitPct": float(rule_row.get("take_profit_pct") or 0),
        "maxCapitalPerTrade": float(rule_row.get("max_capital") or 0),
        "maxDailyLossLimit": float(rule_row.get("max_daily_loss") or 0),
        "dcaIntervalHours": int(rule_row.get("dca_interval_hours") or DEFAULT_DCA_INTERVAL_HOURS),
    }


async def _build_rules_response(owner_id: str) -> Dict[str, Any]:
    await _ensure_schema()
    db = get_database()
    rows = await db.fetch_all(
        query=(
            "SELECT asset_symbol, strategy, is_active, mode, stop_loss_pct, trailing_stop_pct, "
            "take_profit_pct, max_capital, max_daily_loss, dca_interval_hours, updated_at "
            "FROM trading_rules WHERE owner_id = :owner_id ORDER BY updated_at DESC"
        ),
        values={"owner_id": owner_id},
    )

    asset_rules: Dict[str, Any] = {}
    bot_active = False
    execution_mode = "paper"
    selected_asset = "NO_ASSET"
    updated_at = None

    for index, row in enumerate(rows):
        row_dict = dict(row)
        symbol = row_dict.get("asset_symbol")
        if not symbol:
            continue
        asset_rules[symbol] = _symbol_rule_to_ui(row_dict)
        bot_active = bot_active or bool(row_dict.get("is_active"))
        if index == 0:
            execution_mode = str(row_dict.get("mode") or "paper")
            selected_asset = symbol
            updated_at = row_dict.get("updated_at")

    log_rows = await db.fetch_all(
        query=(
            "SELECT timestamp, asset_symbol, signal_received, action_taken, execution_price, reject_reason "
            "FROM bot_execution_logs WHERE owner_id = :owner_id "
            "ORDER BY timestamp DESC LIMIT 100"
        ),
        values={"owner_id": owner_id},
    )

    execution_logs = []
    for row in log_rows:
        signal_payload = _safe_json_loads(row["signal_received"])
        execution_logs.append(
            {
                "timestamp": str(row["timestamp"]) if row["timestamp"] else None,
                "asset": row["asset_symbol"],
                "aiSignal": signal_payload.get("signal_received") or "-",
                "actionTaken": row["action_taken"],
                "slippageReason": row["reject_reason"]
                or (f"Filled @ {row['execution_price']}" if row["execution_price"] is not None else "-"),
            }
        )

    return {
        "botActive": bot_active,
        "executionMode": execution_mode if execution_mode in _MODE_VALUES else "paper",
        "selectedAsset": selected_asset,
        "updatedAt": str(updated_at) if updated_at else None,
        "assetRules": asset_rules,
        "activePositions": await _fetch_active_positions(owner_id),
        "executionLogs": execution_logs,
    }


@router.get('/rules')
async def get_rules(user=Depends(get_current_user)):
    owner_id = user.get("sub")
    if not owner_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")
    return await _build_rules_response(owner_id)


@router.post('/rules')
async def create_or_update_rules(payload: BotRulesPayload, user=Depends(get_current_user)):
    owner_id = user.get("sub")
    if not owner_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    await _ensure_schema()
    db = get_database()

    selected_asset = _normalize_symbol(payload.selectedAsset or "")
    mode = payload.executionMode if payload.executionMode in _MODE_VALUES else "paper"

    sanitized_rules: Dict[str, AssetRuleConfig] = {}
    for symbol_raw, config in payload.assetRules.items():
        symbol = _normalize_symbol(symbol_raw)
        if not symbol or symbol == "NO_ASSET":
            continue
        if not _SYMBOL_RE.match(symbol):
            continue
        sanitized_rules[symbol] = config

    if selected_asset and selected_asset != "NO_ASSET" and selected_asset not in sanitized_rules and _SYMBOL_RE.match(selected_asset):
        sanitized_rules[selected_asset] = AssetRuleConfig(**_default_rule())

    if not sanitized_rules:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No valid asset rules provided")

    for symbol, config in sanitized_rules.items():
        await db.execute(
            query=(
                "INSERT INTO trading_rules "
                "(owner_id, asset_symbol, strategy, is_active, mode, stop_loss_pct, trailing_stop_pct, take_profit_pct, max_capital, max_daily_loss, dca_interval_hours, updated_at) "
                "VALUES (:owner_id, :asset_symbol, :strategy, :is_active, :mode, :stop_loss_pct, :trailing_stop_pct, :take_profit_pct, :max_capital, :max_daily_loss, :dca_interval_hours, now()) "
                "ON CONFLICT (owner_id, asset_symbol) DO UPDATE SET "
                "strategy = EXCLUDED.strategy, "
                "is_active = EXCLUDED.is_active, "
                "mode = EXCLUDED.mode, "
                "stop_loss_pct = EXCLUDED.stop_loss_pct, "
                "trailing_stop_pct = EXCLUDED.trailing_stop_pct, "
                "take_profit_pct = EXCLUDED.take_profit_pct, "
                "max_capital = EXCLUDED.max_capital, "
                "max_daily_loss = EXCLUDED.max_daily_loss, "
                "dca_interval_hours = EXCLUDED.dca_interval_hours, "
                "updated_at = now()"
            ),
            values={
                "owner_id": owner_id,
                "asset_symbol": symbol,
                "strategy": config.strategy,
                "is_active": config.isActive if config.isActive is not None else payload.botActive,
                "mode": mode,
                "stop_loss_pct": config.stopLossPct,
                "trailing_stop_pct": config.trailingStopLossPct,
                "take_profit_pct": config.takeProfitPct,
                "max_capital": config.maxCapitalPerTrade,
                "max_daily_loss": config.maxDailyLossLimit,
                "dca_interval_hours": config.dcaIntervalHours,
            },
        )

    return await _build_rules_response(owner_id)


@router.put('/rules')
async def update_rules(payload: BotRulesPayload, user=Depends(get_current_user)):
    return await create_or_update_rules(payload, user)


@router.post('/signals/evaluate', response_model=ExecutionResult)
async def evaluate_signal(signal_data: SignalData, user=Depends(get_current_user)):
    owner_id = user.get("sub")
    if not owner_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")
    return await evaluate_and_execute_trade(signal_data, owner_id)

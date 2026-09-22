import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.core.db import get_database

router = APIRouter()
logger = logging.getLogger("ajtrade.trading_bot")

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,24}$")
_MODE_VALUES = {"paper", "live"}
_ACTION_VALUES = {"Executed", "Rejected"}
_SCHEMA_READY = False


def _normalize_symbol(value: str) -> str:
    return (value or "").strip().upper()


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
    stopLossPct: float = Field(default=2.0, ge=0, le=100)
    trailingStopLossPct: float = Field(default=1.2, ge=0, le=100)
    takeProfitPct: float = Field(default=5.0, ge=0, le=200)
    maxCapitalPerTrade: float = Field(default=1000.0, ge=0)
    maxDailyLossLimit: float = Field(default=500.0, ge=0)


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
    action_taken: Literal["Executed", "Rejected"]
    asset_symbol: str
    mode: Literal["paper", "live"]
    execution_price: Optional[float] = None
    reason: Optional[str] = None


async def place_broker_order(mode: str, signal_data: SignalData) -> Dict[str, Any]:
    requested_price = signal_data.requested_price or 100.0
    price_multiplier = Decimal("1.0005") if signal_data.side == "BUY" else Decimal("0.9995")
    execution_price = (Decimal(str(requested_price)) * price_multiplier).quantize(Decimal("0.000001"))

    # Mock broker behavior: reject very large live orders.
    if mode == "live" and signal_data.requested_amount > 1_000_000:
        return {
            "ok": False,
            "execution_price": None,
            "message": "Rejected: Broker Limit",
        }

    return {
        "ok": True,
        "execution_price": float(execution_price),
        "message": "Paper order filled" if mode == "paper" else "Live order submitted",
    }


async def _log_execution(
    *,
    owner_id: str,
    asset_symbol: str,
    signal_payload: Dict[str, Any],
    action_taken: str,
    execution_price: Optional[float],
    reject_reason: Optional[str],
) -> None:
    if action_taken not in _ACTION_VALUES:
        raise ValueError("Invalid action_taken")

    await _ensure_schema()
    db = get_database()
    await db.execute(
        query=(
            "INSERT INTO bot_execution_logs (owner_id, asset_symbol, signal_received, action_taken, execution_price, reject_reason) "
            "VALUES (:owner_id, :asset_symbol, :signal_received, :action_taken, :execution_price, :reject_reason)"
        ),
        values={
            "owner_id": owner_id,
            "asset_symbol": asset_symbol,
            "signal_received": json.dumps(signal_payload, separators=(",", ":")),
            "action_taken": action_taken,
            "execution_price": execution_price,
            "reject_reason": reject_reason,
        },
    )


async def _load_rule(owner_id: str, asset_symbol: str) -> Optional[Dict[str, Any]]:
    await _ensure_schema()
    db = get_database()
    row = await db.fetch_one(
        query=(
            "SELECT asset_symbol, strategy, is_active, mode, stop_loss_pct, trailing_stop_pct, "
            "take_profit_pct, max_capital, max_daily_loss "
            "FROM trading_rules WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol"
        ),
        values={"owner_id": owner_id, "asset_symbol": asset_symbol},
    )
    return dict(row) if row else None


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
    trailing_level = (fill_price * trailing_multiplier).quantize(Decimal("0.000001")) if trailing_multiplier > 0 else None
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


async def _sync_execution_to_portfolio(
    *,
    owner_id: str,
    signal_data: SignalData,
    execution_price: Optional[float],
) -> None:
    if execution_price is None:
        return

    price = _coerce_decimal(execution_price, Decimal("0"))
    if price <= 0:
        return

    requested_notional = _coerce_decimal(signal_data.requested_amount, Decimal("0"))
    if requested_notional <= 0:
        return

    fill_qty = (requested_notional / price).quantize(Decimal("0.000001"))
    if fill_qty <= 0:
        return

    asset_symbol = _normalize_symbol(signal_data.asset_symbol)
    side = str(signal_data.side or "BUY").upper()
    db = get_database()

    portfolio = await db.fetch_one(
        query="SELECT id, cash_balance FROM portfolios WHERE owner = :owner",
        values={"owner": owner_id},
    )
    if not portfolio:
        portfolio = await db.fetch_one(
            query=(
                "INSERT INTO portfolios (owner, cash_balance) VALUES (:owner, :cash_balance) "
                "RETURNING id, cash_balance"
            ),
            values={"owner": owner_id, "cash_balance": Decimal("100000")},
        )
    if not portfolio:
        return

    portfolio_id = portfolio["id"]
    cash_balance = _coerce_decimal(portfolio["cash_balance"], Decimal("100000"))
    notes = f"Automated bot {side} via {signal_data.signal_received}"

    async with db.transaction():
        position = await db.fetch_one(
            query=(
                "SELECT quantity, avg_price FROM portfolio_positions "
                "WHERE portfolio_id = :portfolio_id AND symbol = :symbol"
            ),
            values={"portfolio_id": portfolio_id, "symbol": asset_symbol},
        )

        if side == "BUY":
            if position:
                existing_qty = _coerce_decimal(position["quantity"], Decimal("0"))
                existing_avg = _coerce_decimal(position["avg_price"], Decimal("0"))
                next_qty = existing_qty + fill_qty
                if next_qty <= 0:
                    next_qty = fill_qty
                next_avg = ((existing_avg * existing_qty) + (price * fill_qty)) / next_qty
                await db.execute(
                    query=(
                        "UPDATE portfolio_positions "
                        "SET quantity = :quantity, avg_price = :avg_price, updated_at = now() "
                        "WHERE portfolio_id = :portfolio_id AND symbol = :symbol"
                    ),
                    values={
                        "portfolio_id": portfolio_id,
                        "symbol": asset_symbol,
                        "quantity": next_qty,
                        "avg_price": next_avg,
                    },
                )
            else:
                await db.execute(
                    query=(
                        "INSERT INTO portfolio_positions (portfolio_id, symbol, quantity, avg_price) "
                        "VALUES (:portfolio_id, :symbol, :quantity, :avg_price)"
                    ),
                    values={
                        "portfolio_id": portfolio_id,
                        "symbol": asset_symbol,
                        "quantity": fill_qty,
                        "avg_price": price,
                    },
                )

            cash_balance = cash_balance - requested_notional
            await db.execute(
                query="UPDATE portfolios SET cash_balance = :cash_balance, updated_at = now() WHERE id = :id",
                values={"id": portfolio_id, "cash_balance": cash_balance},
            )
            await db.execute(
                query=(
                    "INSERT INTO trading_history "
                    "(portfolio_id, symbol, trade_type, quantity, price, notional, fee, pl, signal_source, notes) "
                    "VALUES (:portfolio_id, :symbol, 'BUY', :quantity, :price, :notional, :fee, :pl, :signal_source, :notes)"
                ),
                values={
                    "portfolio_id": portfolio_id,
                    "symbol": asset_symbol,
                    "quantity": fill_qty,
                    "price": price,
                    "notional": requested_notional,
                    "fee": Decimal("0"),
                    "pl": Decimal("0"),
                    "signal_source": "bot",
                    "notes": notes,
                },
            )
            return

        # SELL path: only close/reduce if local portfolio position exists.
        if not position:
            return

        existing_qty = _coerce_decimal(position["quantity"], Decimal("0"))
        existing_avg = _coerce_decimal(position["avg_price"], Decimal("0"))
        sell_qty = fill_qty if fill_qty <= existing_qty else existing_qty
        if sell_qty <= 0:
            return

        notional = (sell_qty * price).quantize(Decimal("0.01"))
        realized_pl = ((price - existing_avg) * sell_qty).quantize(Decimal("0.01"))
        remaining_qty = existing_qty - sell_qty

        if remaining_qty <= 0:
            await db.execute(
                query="DELETE FROM portfolio_positions WHERE portfolio_id = :portfolio_id AND symbol = :symbol",
                values={"portfolio_id": portfolio_id, "symbol": asset_symbol},
            )
        else:
            await db.execute(
                query=(
                    "UPDATE portfolio_positions "
                    "SET quantity = :quantity, updated_at = now() "
                    "WHERE portfolio_id = :portfolio_id AND symbol = :symbol"
                ),
                values={
                    "portfolio_id": portfolio_id,
                    "symbol": asset_symbol,
                    "quantity": remaining_qty,
                },
            )

        cash_balance = cash_balance + notional
        await db.execute(
            query="UPDATE portfolios SET cash_balance = :cash_balance, updated_at = now() WHERE id = :id",
            values={"id": portfolio_id, "cash_balance": cash_balance},
        )
        await db.execute(
            query=(
                "INSERT INTO trading_history "
                "(portfolio_id, symbol, trade_type, quantity, price, notional, fee, pl, signal_source, notes) "
                "VALUES (:portfolio_id, :symbol, 'SELL', :quantity, :price, :notional, :fee, :pl, :signal_source, :notes)"
            ),
            values={
                "portfolio_id": portfolio_id,
                "symbol": asset_symbol,
                "quantity": sell_qty,
                "price": price,
                "notional": notional,
                "fee": Decimal("0"),
                "pl": realized_pl,
                "signal_source": "bot",
                "notes": notes,
            },
        )


async def evaluate_and_execute_trade(signal_data: SignalData, owner_id: str) -> ExecutionResult:
    """Evaluate trading constraints and execute a broker order when all checks pass."""
    asset_symbol = _normalize_symbol(signal_data.asset_symbol)
    if not asset_symbol or not _SYMBOL_RE.match(asset_symbol):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid asset symbol")

    rule = await _load_rule(owner_id, asset_symbol)
    signal_payload = {
        "asset_symbol": asset_symbol,
        "signal_received": signal_data.signal_received,
        "side": signal_data.side,
        "requested_amount": signal_data.requested_amount,
        "requested_price": signal_data.requested_price,
        "confidence": signal_data.confidence,
        "estimated_pnl": signal_data.estimated_pnl,
        "metadata": signal_data.metadata,
    }

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
    cooldown_start = datetime.now(timezone.utc) - timedelta(minutes=15)
    recent_trade = await db.fetch_one(
        query=(
            "SELECT id FROM bot_execution_logs "
            "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol "
            "AND action_taken = 'Executed' AND timestamp >= :cooldown_start "
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

    # Step 5: Mock execution by mode.
    broker_result = await place_broker_order(mode, signal_data)
    if not broker_result.get("ok"):
        reason = str(broker_result.get("message") or "Rejected: Broker Error")
        await _log_execution(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            signal_payload=signal_payload,
            action_taken="Rejected",
            execution_price=None,
            reject_reason=reason,
        )
        return ExecutionResult(ok=False, action_taken="Rejected", asset_symbol=asset_symbol, mode=mode, reason=reason)

    # Step 6: Persist successful execution details.
    execution_price = broker_result.get("execution_price")
    await _log_execution(
        owner_id=owner_id,
        asset_symbol=asset_symbol,
        signal_payload=signal_payload,
        action_taken="Executed",
        execution_price=execution_price,
        reject_reason=None,
    )
    try:
        await _persist_active_position(
            owner_id=owner_id,
            asset_symbol=asset_symbol,
            side=signal_data.side,
            execution_price=execution_price,
            requested_amount=signal_data.requested_amount,
            trailing_stop_pct=rule.get("trailing_stop_pct"),
        )
    except Exception as exc:
        logger.warning(
            "active_position_persist_failed owner=%s asset=%s side=%s error=%s",
            owner_id,
            asset_symbol,
            signal_data.side,
            exc,
        )
    try:
        await _sync_execution_to_portfolio(
            owner_id=owner_id,
            signal_data=signal_data,
            execution_price=execution_price,
        )
    except Exception as exc:
        logger.warning(
            "portfolio_sync_from_bot_failed owner=%s asset=%s side=%s error=%s",
            owner_id,
            asset_symbol,
            signal_data.side,
            exc,
        )

    return ExecutionResult(
        ok=True,
        action_taken="Executed",
        asset_symbol=asset_symbol,
        mode=mode,
        execution_price=execution_price,
        reason=str(broker_result.get("message") or "Executed"),
    )


def _symbol_rule_to_ui(rule_row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "strategy": rule_row.get("strategy") or "Trend Following",
        "stopLossPct": float(rule_row.get("stop_loss_pct") or 0),
        "trailingStopLossPct": float(rule_row.get("trailing_stop_pct") or 0),
        "takeProfitPct": float(rule_row.get("take_profit_pct") or 0),
        "maxCapitalPerTrade": float(rule_row.get("max_capital") or 0),
        "maxDailyLossLimit": float(rule_row.get("max_daily_loss") or 0),
    }


async def _build_rules_response(owner_id: str) -> Dict[str, Any]:
    await _ensure_schema()
    db = get_database()
    rows = await db.fetch_all(
        query=(
            "SELECT asset_symbol, strategy, is_active, mode, stop_loss_pct, trailing_stop_pct, "
            "take_profit_pct, max_capital, max_daily_loss, updated_at "
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
                "(owner_id, asset_symbol, strategy, is_active, mode, stop_loss_pct, trailing_stop_pct, take_profit_pct, max_capital, max_daily_loss, updated_at) "
                "VALUES (:owner_id, :asset_symbol, :strategy, :is_active, :mode, :stop_loss_pct, :trailing_stop_pct, :take_profit_pct, :max_capital, :max_daily_loss, now()) "
                "ON CONFLICT (owner_id, asset_symbol) DO UPDATE SET "
                "strategy = EXCLUDED.strategy, "
                "is_active = EXCLUDED.is_active, "
                "mode = EXCLUDED.mode, "
                "stop_loss_pct = EXCLUDED.stop_loss_pct, "
                "trailing_stop_pct = EXCLUDED.trailing_stop_pct, "
                "take_profit_pct = EXCLUDED.take_profit_pct, "
                "max_capital = EXCLUDED.max_capital, "
                "max_daily_loss = EXCLUDED.max_daily_loss, "
                "updated_at = now()"
            ),
            values={
                "owner_id": owner_id,
                "asset_symbol": symbol,
                "strategy": config.strategy,
                "is_active": payload.botActive,
                "mode": mode,
                "stop_loss_pct": config.stopLossPct,
                "trailing_stop_pct": config.trailingStopLossPct,
                "take_profit_pct": config.takeProfitPct,
                "max_capital": config.maxCapitalPerTrade,
                "max_daily_loss": config.maxDailyLossLimit,
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

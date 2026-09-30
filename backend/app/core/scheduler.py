from __future__ import annotations

import json
import logging
import importlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Set, Tuple

import httpx
try:
    AsyncIOScheduler = importlib.import_module('apscheduler.schedulers.asyncio').AsyncIOScheduler
except Exception:  # pragma: no cover - runtime fallback when dependency is missing
    AsyncIOScheduler = None  # type: ignore[assignment,misc]

from app.core.db import get_database


logger = logging.getLogger('ajtrade.scheduler')
_scheduler: Any = None
_last_run_at: datetime | None = None
_refreshed_users_total: int = 0
_refreshed_users_last_run: int = 0
_alerted_signal_ids: Set[str] = set()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# How often the watchlist job ticks. Each tick only rebuilds caches that are due, so a short
# interval is cheap; it mainly decides how quickly the bot reacts once a trade is allowed.
SCHEDULER_INTERVAL_MINUTES = max(_env_float('AJTRADE_SCHEDULER_INTERVAL_MINUTES', 1.0), 0.5)
# How often news is re-fetched and a new AI signal (insight) is generated per watchlist symbol.
NEWS_REFRESH_MINUTES = _env_float('AJTRADE_NEWS_REFRESH_MINUTES', 15.0)
SIGNAL_REFRESH_MINUTES = _env_float('AJTRADE_SIGNAL_REFRESH_MINUTES', 3.0)
# The bot acts on the same signal at most this many times (spaced by the bot cooldown).
BOT_MAX_TRADES_PER_SIGNAL = max(int(_env_float('AJTRADE_BOT_MAX_TRADES_PER_SIGNAL', 1)), 1)


def _cache_ttl_minutes(refresh_minutes: float) -> float:
    # Caches are only checked on ticks; half a tick of slack makes the refresh land on the first
    # tick at `refresh_minutes` instead of flip-flopping with the tick after it.
    return max(refresh_minutes - SCHEDULER_INTERVAL_MINUTES / 2, 0.0)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def get_scheduler_status() -> Dict[str, Any]:
    try:
        running = bool(_scheduler is not None and _scheduler.running)
    except Exception:
        running = False
    return {
        'state': 'running' if running else 'stopped',
        'last_executed_run': _last_run_at.isoformat() if _last_run_at else None,
        'refreshed_users_total': int(_refreshed_users_total),
        'refreshed_users_last_run': int(_refreshed_users_last_run),
    }


async def _send_alert_webhook(owner: str, symbol: str, signal: str, payload: Dict[str, Any]) -> None:
    """Posts BUY/SELL threshold alerts to the Discord/Email webhooks when configured (AJTRADE_DISCORD_WEBHOOK_URL /
    AJTRADE_EMAIL_WEBHOOK_URL) and records the alert."""
    discord_url = (os.environ.get('AJTRADE_DISCORD_WEBHOOK_URL') or '').strip()
    email_url = (os.environ.get('AJTRADE_EMAIL_WEBHOOK_URL') or '').strip()

    message = {
        'owner': owner,
        'symbol': symbol,
        'signal': signal,
        'payload': payload,
        'source': 'apscheduler-watchlist-refresh',
    }

    delivery_status = 'triggered'
    logger.info('alert_triggered payload=%s', message)

    async with httpx.AsyncClient(timeout=8.0) as client:
        if discord_url:
            try:
                await client.post(discord_url, json={'content': f"[{signal}] {symbol} for {owner}", 'meta': payload})
                delivery_status = 'sent'
            except Exception as exc:
                delivery_status = 'failed'
                logger.warning('discord_webhook_failed owner=%s symbol=%s error=%s', owner, symbol, exc)
        if email_url:
            try:
                await client.post(email_url, json=message)
                delivery_status = 'sent'
            except Exception as exc:
                delivery_status = 'failed'
                logger.warning('email_webhook_failed owner=%s symbol=%s error=%s', owner, symbol, exc)

    alert_message = f"{signal} threshold met for {symbol}; confidence={payload.get('confidence')}"
    try:
        db = get_database()
        await db.execute(
            query=(
                "INSERT INTO alert_events (owner_id, alert_type, message, status, created_at) "
                "VALUES (:owner_id, :alert_type, :message, :status, now())"
            ),
            values={
                'owner_id': owner,
                'alert_type': str(signal or 'ALERT').upper(),
                'message': alert_message,
                'status': delivery_status,
            },
        )
    except Exception as exc:
        logger.warning('alert_event_insert_failed owner=%s symbol=%s error=%s', owner, symbol, exc)


def _build_trade_signal_data(
    symbol: str,
    side: str,
    insight: Dict[str, Any],
    *,
    strategy: str,
    strategy_reason: str,
    signal_id: str,
    price: Optional[float],
    amount: float,
) -> Any:
    from app.api.trading_bot import SignalData

    signal_price = insight.get('latest_price')
    requested_price = price
    if requested_price is None:
        try:
            requested_price = float(signal_price) if signal_price is not None else 0.0
        except (TypeError, ValueError):
            requested_price = 0.0
    if requested_price <= 0:
        # No usable price: evaluate_and_execute_trade fetches a quote or rejects the order.
        requested_price = None

    try:
        confidence_value = int(insight.get('confidence') or 0)
    except (TypeError, ValueError):
        confidence_value = 0

    return SignalData(
        asset_symbol=str(symbol or '').upper(),
        signal_received=f"{side} ({strategy})",
        side=side,
        requested_amount=max(amount, 0.01),
        requested_price=requested_price,
        confidence=min(max(confidence_value / 100.0, 0.0), 1.0),
        metadata={
            'strategy': strategy,
            'strategy_reason': strategy_reason,
            'ai_signal': insight.get('signal'),
            'trend_summary': insight.get('trend_summary'),
            'probability_up': insight.get('probability_up'),
            'signal_price': signal_price,
            'signal_id': signal_id,
            'signal_generated_at': insight.get('generated_at'),
            'source': 'apscheduler-watchlist-refresh',
        },
    )


def _trade_amount(rule: Dict[str, Any]) -> float:
    """Dollar size of one bot order: the asset's "Max Capital per Trade", else the env default."""
    try:
        max_capital = float(rule.get('max_capital') or 0)
    except (TypeError, ValueError):
        max_capital = 0.0
    if max_capital > 0:
        return max_capital
    try:
        return float(os.environ.get('AJTRADE_BOT_DEFAULT_TRADE_AMOUNT', '1000'))
    except (TypeError, ValueError):
        return 1000.0


async def _fetch_live_price(symbol: str) -> Optional[float]:
    try:
        from app.api.market import get_market_price

        price = float(await get_market_price(symbol))
    except Exception as exc:
        logger.warning('bot_live_price_failed symbol=%s error=%s', symbol, exc)
        return None
    return price if price > 0 else None


def _parse_iso(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


async def _load_signal_attempts(
    owner: str, symbol: str, signal_id: str, generated_at: Optional[datetime], cooldown: timedelta
) -> Tuple[int, Optional[datetime]]:
    """Returns (bot attempts on this signal, time of the latest bot attempt on this symbol)."""
    from app.api.trading_bot import _ensure_schema

    now = _now_utc()
    cooldown_start = now - cooldown
    since = min(generated_at or (now - timedelta(days=1)), cooldown_start)

    await _ensure_schema()
    db = get_database()
    rows = await db.fetch_all(
        query=(
            "SELECT timestamp, signal_received FROM bot_execution_logs "
            "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol AND timestamp >= :since "
            "ORDER BY timestamp DESC"
        ),
        values={'owner_id': owner, 'asset_symbol': symbol, 'since': since},
    )

    attempts = 0
    last_attempt_at: Optional[datetime] = None
    for row in rows:
        if last_attempt_at is None:
            last_attempt_at = row['timestamp']
        try:
            record = json.loads(row['signal_received'] or '{}')
        except (TypeError, ValueError):
            continue
        metadata = record.get('metadata') if isinstance(record, dict) else None
        if isinstance(metadata, dict) and metadata.get('signal_id') == signal_id:
            attempts += 1
    return attempts, last_attempt_at


async def _send_signal_alert(owner: str, symbol: str, signal: str, payload: Dict[str, Any]) -> None:
    """Alert callback for strong AI signals: notifies once per signal, independent of the bot."""
    signal_id = f"{symbol}:{signal}:{payload.get('generated_at')}"
    if signal_id in _alerted_signal_ids:
        return
    _alerted_signal_ids.add(signal_id)
    try:
        await _send_alert_webhook(owner, symbol, signal, payload)
    except Exception as exc:
        logger.warning('alert_webhook_failed owner=%s symbol=%s error=%s', owner, symbol, exc)


async def _run_dca_for_asset(owner: str, symbol: str, rule: Dict[str, Any], insight: Dict[str, Any]) -> None:
    """Dollar-cost averaging: buys the asset's max capital per trade once every dca_interval_hours,
    whatever the AI signals say. It never sells on signals; only the exits (if on) close the position."""
    from app.api.trading_bot import (
        DCA_STRATEGY,
        DEFAULT_DCA_INTERVAL_HOURS,
        _ensure_schema,
        _has_pending_order,
        evaluate_and_execute_trade,
        get_bot_cooldown,
    )

    await _ensure_schema()
    if await _has_pending_order(owner, symbol, 'BUY'):
        return  # the last scheduled buy is still waiting to fill on Alpaca

    interval_hours = int(rule.get('dca_interval_hours') or DEFAULT_DCA_INTERVAL_HOURS)
    now = _now_utc()
    cooldown = get_bot_cooldown()
    # 30s of grace so the schedule doesn't slip a tick each time (a buy is logged a moment after its tick).
    since = now - max(timedelta(hours=interval_hours) - timedelta(seconds=30), cooldown)

    await _ensure_schema()
    rows = await get_database().fetch_all(
        query=(
            "SELECT timestamp, signal_received FROM bot_execution_logs "
            "WHERE owner_id = :owner_id AND asset_symbol = :asset_symbol AND timestamp >= :since "
            "ORDER BY timestamp DESC"
        ),
        values={'owner_id': owner, 'asset_symbol': symbol, 'since': since},
    )
    for index, row in enumerate(rows):
        # Wait out the bot cooldown after any trade on this symbol (e.g. an exit).
        if index == 0 and now - row['timestamp'] < cooldown:
            return
        try:
            record = json.loads(row['signal_received'] or '{}')
        except (TypeError, ValueError):
            continue
        metadata = record.get('metadata') if isinstance(record, dict) else None
        # One DCA attempt per interval; a rejected one (e.g. insufficient cash) also waits for the next.
        if isinstance(metadata, dict) and metadata.get('strategy') == DCA_STRATEGY:
            return

    trade_signal = _build_trade_signal_data(
        symbol,
        'BUY',
        insight,
        strategy=DCA_STRATEGY,
        strategy_reason=f"Scheduled buy every {interval_hours}h",
        signal_id=f"{symbol}:DCA:{now.isoformat()}",
        price=await _fetch_live_price(symbol),
        amount=_trade_amount(rule),
    )
    try:
        execution_result = await evaluate_and_execute_trade(trade_signal, owner)
        logger.info('bot_dca_buy owner=%s symbol=%s result=%s', owner, symbol, execution_result.dict())
    except Exception as exc:
        logger.warning('bot_dca_buy_failed owner=%s symbol=%s error=%s', owner, symbol, exc)


async def _run_bot_for_insight(owner: str, symbol: str, insight: Dict[str, Any]) -> None:
    """Insight callback: applies the asset's strategy to the latest insight and, when it says BUY/SELL,
    lets the bot act on that signal up to BOT_MAX_TRADES_PER_SIGNAL times, one bot cooldown apart."""
    from app.api.ml_v2 import BOT_MIN_SIGNAL_CONFIDENCE
    from app.api.trading_bot import (
        DCA_STRATEGY,
        _has_pending_order,
        decide_strategy_side,
        evaluate_and_execute_trade,
        get_bot_cooldown,
        get_bot_position_quantity,
        load_bot_rule,
    )

    rule = await load_bot_rule(owner, symbol)
    if not rule or not bool(rule.get('is_active')):
        return

    strategy = str(rule.get('strategy') or 'Trend Following')
    if strategy == DCA_STRATEGY:
        await _run_dca_for_asset(owner, symbol, rule, insight)
        return
    side, reason = decide_strategy_side(strategy, insight, BOT_MIN_SIGNAL_CONFIDENCE)
    if side is None:
        return
    if side == 'SELL':
        # The bot only sells shares it bought; with none, a SELL signal is a HOLD for the bot, so
        # don't fill the audit log with rejections.
        if await get_bot_position_quantity(owner, symbol) <= 0:
            return
    # An order for this asset and side is still open on Alpaca (e.g. market closed): wait for it
    # rather than logging a rejection on every new signal.
    if await _has_pending_order(owner, symbol, side):
        return

    generated_at = insight.get('generated_at')
    signal_id = f"{symbol}:{side}:{generated_at}"
    try:
        cooldown = get_bot_cooldown()
        attempts, last_attempt_at = await _load_signal_attempts(
            owner, symbol, signal_id, _parse_iso(generated_at), cooldown
        )
    except Exception as exc:
        logger.warning('bot_signal_attempts_lookup_failed owner=%s symbol=%s error=%s', owner, symbol, exc)
        return

    if attempts >= BOT_MAX_TRADES_PER_SIGNAL:
        return
    if last_attempt_at is not None and _now_utc() - last_attempt_at < cooldown:
        return

    # Fill at the current market price rather than the price captured when the signal was generated;
    # fall back to the signal's price if the quote lookup fails.
    trade_signal = _build_trade_signal_data(
        symbol,
        side,
        insight,
        strategy=strategy,
        strategy_reason=reason,
        signal_id=signal_id,
        price=await _fetch_live_price(symbol),
        amount=_trade_amount(rule),
    )
    try:
        execution_result = await evaluate_and_execute_trade(trade_signal, owner)
        logger.info(
            'bot_trade_execution owner=%s symbol=%s side=%s strategy=%s reason=%s result=%s',
            owner,
            symbol,
            side,
            strategy,
            reason,
            execution_result.dict() if hasattr(execution_result, 'dict') else execution_result,
        )
    except Exception as exc:
        logger.warning('bot_trade_execution_failed owner=%s symbol=%s side=%s error=%s', owner, symbol, side, exc)


async def _run_position_exits(price_cache: Dict[str, Optional[float]]) -> None:
    """Settles bot orders still open on Alpaca, then checks every open bot position against its
    stop-loss / trailing stop / take-profit."""
    from app.api.trading_bot import _ensure_schema, monitor_position_exits, reconcile_pending_orders

    async def get_price(symbol: str) -> Optional[float]:
        if symbol not in price_cache:
            price_cache[symbol] = await _fetch_live_price(symbol)
        return price_cache[symbol]

    try:
        await _ensure_schema()
        rows = await get_database().fetch_all(
            query=(
                "SELECT owner_id FROM bot_active_positions "
                "UNION SELECT owner_id FROM bot_execution_logs WHERE action_taken = 'Pending'"
            )
        )
    except Exception as exc:
        logger.warning('bot_exit_owner_fetch_failed error=%s', exc)
        return

    for row in rows:
        owner = str(row['owner_id'])
        try:
            for result in await reconcile_pending_orders(owner):
                logger.info('bot_pending_order_settled owner=%s result=%s', owner, result.dict())
        except Exception as exc:
            logger.warning('bot_pending_reconcile_failed owner=%s error=%s', owner, exc)
        try:
            for result in await monitor_position_exits(owner, get_price):
                logger.info('bot_position_exit owner=%s result=%s', owner, result.dict())
        except Exception as exc:
            logger.warning('bot_exit_check_failed owner=%s error=%s', owner, exc)


async def refresh_watchlists_job() -> None:
    """Runs every SCHEDULER_INTERVAL_MINUTES: closes bot positions that hit their exits, refreshes
    OHLCV+News derived caches when due, sends signal alerts, and runs each asset's bot strategy."""
    global _last_run_at, _refreshed_users_total, _refreshed_users_last_run

    _last_run_at = _now_utc()
    refreshed_count = 0

    # Protective exits first, so a position that crossed its stop is closed before new entries.
    await _run_position_exits({})

    try:
        db = get_database()
        rows = await db.fetch_all(
            query=(
                'SELECT DISTINCT owner FROM watchlists '
                'WHERE owner IS NOT NULL '
                'ORDER BY owner'
            )
        )
    except Exception as exc:
        logger.warning('scheduler_watchlist_owner_fetch_failed error=%s', exc)
        return

    if not rows:
        logger.info('scheduler_watchlist_refresh_no_users')
        return

    try:
        from app.api.ml_v2 import refresh_watchlist_cache_for_owner
    except Exception as exc:
        logger.warning('scheduler_ml_v2_import_failed error=%s', exc)
        return

    for row in rows:
        try:
            owner_value = row['owner']
        except Exception:
            owner_value = None
        owner = str(owner_value) if owner_value is not None else None
        if not owner:
            continue
        try:
            summary = await refresh_watchlist_cache_for_owner(
                owner,
                news_ttl_minutes=_cache_ttl_minutes(NEWS_REFRESH_MINUTES),
                insights_ttl_minutes=_cache_ttl_minutes(SIGNAL_REFRESH_MINUTES),
                alert_callback=_send_signal_alert,
                insight_callback=_run_bot_for_insight,
            )
            refreshed_count += 1
            logger.info('scheduler_watchlist_refresh_success owner=%s summary=%s', owner, summary)
        except Exception as exc:
            logger.warning('scheduler_watchlist_refresh_failed owner=%s error=%s', owner, exc)

    _refreshed_users_last_run = refreshed_count
    _refreshed_users_total += refreshed_count


def start_scheduler() -> None:
    global _scheduler
    if AsyncIOScheduler is None:
        logger.warning('scheduler_disabled_missing_dependency apscheduler_not_installed')
        return

    try:
        if _scheduler is not None and _scheduler.running:
            return

        scheduler = AsyncIOScheduler(timezone='UTC')
        scheduler.add_job(
            refresh_watchlists_job,
            trigger='interval',
            seconds=int(SCHEDULER_INTERVAL_MINUTES * 60),
            id='watchlist-refresh-job',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info(
            'scheduler_started interval_minutes=%s news_refresh_minutes=%s signal_refresh_minutes=%s '
            'bot_max_trades_per_signal=%s',
            SCHEDULER_INTERVAL_MINUTES,
            NEWS_REFRESH_MINUTES,
            SIGNAL_REFRESH_MINUTES,
            BOT_MAX_TRADES_PER_SIGNAL,
        )
    except Exception as exc:
        _scheduler = None
        logger.warning('scheduler_start_failed error=%s', exc)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
        logger.info('scheduler_stopped')
    finally:
        _scheduler = None

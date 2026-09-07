from __future__ import annotations

import logging
import importlib
import os
from datetime import datetime, timezone
from typing import Any, Dict

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


async def _send_mock_alert_webhook(owner: str, symbol: str, signal: str, payload: Dict[str, Any]) -> None:
    """Mock notifier for Discord/Email webhooks triggered by BUY/SELL threshold events."""
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
    logger.info('mock_alert_triggered payload=%s', message)

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


def _build_trade_signal_data(symbol: str, signal: str, payload: Dict[str, Any]) -> Any:
    from app.api.trading_bot import SignalData

    latest_price = payload.get('latest_price')
    try:
        requested_price = float(latest_price) if latest_price is not None else 0.0
    except Exception:
        requested_price = 0.0

    if requested_price <= 0:
        requested_price = 1.0

    confidence = payload.get('confidence')
    try:
        confidence_value = int(confidence) if confidence is not None else 0
    except Exception:
        confidence_value = 0

    try:
        requested_amount = float(os.environ.get('AJTRADE_BOT_DEFAULT_TRADE_AMOUNT', '1000'))
    except (TypeError, ValueError):
        requested_amount = 1000.0
    requested_amount = max(requested_amount, 0.01)

    return SignalData(
        asset_symbol=str(symbol or '').upper(),
        signal_received=str(signal or '').upper(),
        side=str(signal or 'BUY').upper(),
        requested_amount=requested_amount,
        requested_price=requested_price,
        confidence=min(max(confidence_value / 100.0, 0.0), 1.0),
        metadata={
            'owner': payload.get('owner'),
            'trend_summary': payload.get('trend_summary'),
            'probability_up': payload.get('probability_up'),
            'source': 'apscheduler-watchlist-refresh',
        },
    )


async def _send_mock_alert_and_execute_trade(owner: str, symbol: str, signal: str, payload: Dict[str, Any]) -> None:
    try:
        await _send_mock_alert_webhook(owner, symbol, signal, payload)
    except Exception as exc:
        logger.warning('mock_alert_webhook_failed owner=%s symbol=%s error=%s', owner, symbol, exc)

    try:
        from app.api.trading_bot import evaluate_and_execute_trade

        trade_signal = _build_trade_signal_data(symbol, signal, payload)
        execution_result = await evaluate_and_execute_trade(trade_signal, owner)
        logger.info(
            'bot_trade_execution owner=%s symbol=%s signal=%s result=%s',
            owner,
            symbol,
            signal,
            execution_result.dict() if hasattr(execution_result, 'dict') else execution_result,
        )
    except Exception as exc:
        logger.warning('bot_trade_execution_failed owner=%s symbol=%s signal=%s error=%s', owner, symbol, signal, exc)


async def refresh_watchlists_job() -> None:
    """Runs every 15 minutes: refreshes OHLCV+News derived caches and evaluates alert thresholds."""
    global _last_run_at, _refreshed_users_total, _refreshed_users_last_run

    _last_run_at = _now_utc()
    refreshed_count = 0

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
                news_ttl_minutes=60,
                insights_ttl_minutes=15,
                alert_callback=_send_mock_alert_and_execute_trade,
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
            minutes=15,
            id='watchlist-refresh-job',
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        _scheduler = scheduler
        logger.info('scheduler_started interval_minutes=15')
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

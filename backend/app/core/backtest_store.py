"""Saved backtest results and the bot's backtest gate.

Backtests are heavy (the AI model is retrained ~50 times per asset), so they never run when a page
loads. A background job refreshes them one at a time, at most daily, and pages only read the saved
results:
- backtest_rule_results: each member's saved bot settings for an asset, replayed over the past year.
- backtest_tuned: per asset (shared), the settings that did best in the past-year backtest (see backtest.tune_rule).

The gate: the bot only opens a new position on an AI outlook when the asset's saved settings closed at
least GATE_MIN_WIN_RATE % of at least GATE_MIN_TRADES simulated trades in profit in that backtest. Sells and exits are
never blocked, so a position can always be closed.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi.concurrency import run_in_threadpool

from app.core import backtest
from app.core.db import get_database

logger = logging.getLogger('ajtrade.backtest')


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


GATE_ENABLED = os.environ.get('AJTRADE_BOT_BACKTEST_GATE', 'true').strip().lower() not in {'0', 'false', 'no', 'off'}
GATE_MIN_WIN_RATE = _env_float('AJTRADE_BOT_BACKTEST_MIN_WIN_RATE', 60.0)
GATE_MIN_TRADES = int(_env_float('AJTRADE_BOT_BACKTEST_MIN_TRADES', 3))
RESULT_MAX_AGE = timedelta(hours=24)
REFRESH_INTERVAL_SECONDS = int(_env_float('AJTRADE_BACKTEST_REFRESH_SECONDS', 120))

# One backtest at a time, whoever asks (API, background job, rule save).
RUNNING = asyncio.Semaphore(1)
_schema_ready = False


async def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    db = get_database()
    await db.execute(
        "CREATE TABLE IF NOT EXISTS backtest_rule_results ("
        " owner_id uuid NOT NULL,"
        " symbol text NOT NULL,"
        " rule_key text NOT NULL,"
        " computed_at timestamptz NOT NULL DEFAULT now(),"
        " payload jsonb NOT NULL,"
        " PRIMARY KEY (owner_id, symbol))"
    )
    await db.execute(
        "CREATE TABLE IF NOT EXISTS backtest_tuned ("
        " symbol text PRIMARY KEY,"
        " computed_at timestamptz NOT NULL DEFAULT now(),"
        " payload jsonb NOT NULL)"
    )
    # Per-asset switch for the check (also added by the trading bot's own schema setup).
    await db.execute("ALTER TABLE IF EXISTS trading_rules ADD COLUMN IF NOT EXISTS backtest_check boolean NOT NULL DEFAULT true")
    _schema_ready = True


def _num(value: Any) -> float:
    try:
        return round(float(value or 0), 4)
    except (TypeError, ValueError):
        return 0.0


def rule_key(rule: Dict[str, Any]) -> str:
    """Identifies the settings a result was computed for, so edited rules are re-tested."""
    return '|'.join([
        str(rule.get('strategy') or 'Trend Following'),
        str(_num(rule.get('stop_loss_pct'))),
        str(_num(rule.get('trailing_stop_pct'))),
        str(_num(rule.get('take_profit_pct'))),
        str(int(rule.get('dca_interval_hours') or 24)),
    ])


def passes(summary: Optional[Dict[str, Any]]) -> bool:
    if not summary:
        return False
    win = summary.get('win_rate')
    return win is not None and float(win) >= GATE_MIN_WIN_RATE and int(summary.get('trades') or 0) >= GATE_MIN_TRADES


def _decode(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or '{}')
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


async def _news(symbol: str) -> List[Dict[str, Any]]:
    try:
        from app.core import brokers, news_history

        if await brokers.provider_for_symbol(symbol) != brokers.ALPACA:
            return []
        return (await news_history.daily_history([symbol])).get(symbol) or []
    except Exception:
        return []


def _min_confidence() -> int:
    from app.api.ml_v2 import BOT_MIN_SIGNAL_CONFIDENCE

    return int(BOT_MIN_SIGNAL_CONFIDENCE)


# --- Computing ------------------------------------------------------------------------------------

async def compute_rule(owner: str, symbol: str, rule: Dict[str, Any]) -> Dict[str, Any]:
    """Backtests the member's saved settings for the asset over the past year and saves the result."""
    await ensure_schema()
    news = await _news(symbol)
    async with RUNNING:
        result = await run_in_threadpool(
            backtest.run_backtest, symbol, rule, period='1y', news_daily=news, min_confidence=_min_confidence(),
        )
    m = result['metrics']
    summary = {
        'win_rate': m.get('win_rate_incl_open'),
        'trades': m.get('trades_incl_open'),
        'return_pct': m.get('return_pct'),
        'hold_return_pct': m.get('hold_return_pct'),
        'max_drawdown_pct': m.get('max_drawdown_pct'),
        'start': result.get('start'),
        'end': result.get('end'),
        'strategy': result.get('strategy'),
        'rule_key': rule_key(rule),
    }
    await get_database().execute(
        query=(
            "INSERT INTO backtest_rule_results (owner_id, symbol, rule_key, computed_at, payload) "
            "VALUES (:owner, :symbol, :key, now(), CAST(:payload AS jsonb)) "
            "ON CONFLICT (owner_id, symbol) DO UPDATE SET rule_key = EXCLUDED.rule_key, "
            "computed_at = EXCLUDED.computed_at, payload = EXCLUDED.payload"
        ),
        values={'owner': owner, 'symbol': symbol, 'key': summary['rule_key'], 'payload': json.dumps(summary)},
    )
    return summary


async def compute_tuned(symbol: str) -> Dict[str, Any]:
    """Finds settings that pass the gate for the asset and saves them (shared)."""
    await ensure_schema()
    news = await _news(symbol)
    async with RUNNING:
        result = await run_in_threadpool(
            backtest.tune_rule, symbol, target_win_rate=GATE_MIN_WIN_RATE, min_trades=GATE_MIN_TRADES,
            news_daily=news, min_confidence=_min_confidence(),
        )
    await get_database().execute(
        query=(
            "INSERT INTO backtest_tuned (symbol, computed_at, payload) VALUES (:symbol, now(), CAST(:payload AS jsonb)) "
            "ON CONFLICT (symbol) DO UPDATE SET computed_at = EXCLUDED.computed_at, payload = EXCLUDED.payload"
        ),
        values={'symbol': symbol, 'payload': json.dumps(result, default=str)},
    )
    return result


def compute_rule_soon(owner: str, symbol: str, rule: Dict[str, Any]) -> None:
    """Re-tests just-saved settings in the background, so the gate reflects them within seconds."""

    async def run() -> None:
        try:
            await compute_rule(owner, symbol, rule)
        except Exception as exc:
            logger.warning('backtest_rule_refresh_failed owner=%s symbol=%s error=%s', owner, symbol, exc)

    try:
        asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        pass


async def refresh_changed(owner: str, rules: Dict[str, Dict[str, Any]]) -> int:
    """After a save: re-tests the active (non-DCA) rules whose settings differ from their saved result."""
    if not GATE_ENABLED:
        return 0
    await ensure_schema()
    rows = await get_database().fetch_all(
        query="SELECT symbol, rule_key FROM backtest_rule_results WHERE owner_id = :owner",
        values={'owner': owner},
    )
    saved = {r['symbol']: r['rule_key'] for r in rows}
    started = 0
    for symbol, rule in rules.items():
        if not rule.get('is_active') or str(rule.get('strategy')) == 'Dollar-Cost Averaging' or rule.get('backtest_check') is False:
            continue
        if saved.get(symbol.upper()) != rule_key(rule):
            compute_rule_soon(owner, symbol.upper(), rule)
            started += 1
    return started


# --- Reading --------------------------------------------------------------------------------------

async def rule_result(owner: str, symbol: str) -> Optional[Dict[str, Any]]:
    await ensure_schema()
    row = await get_database().fetch_one(
        query="SELECT rule_key, computed_at, payload FROM backtest_rule_results WHERE owner_id = :owner AND symbol = :symbol",
        values={'owner': owner, 'symbol': symbol},
    )
    if not row:
        return None
    return {**_decode(row['payload']), 'rule_key': row['rule_key'], 'computed_at': row['computed_at'].isoformat()}


async def gate_status(owner: str, symbol: str, rule: Dict[str, Any]) -> Dict[str, Any]:
    """{'state': 'pass' | 'fail' | 'pending' | 'off', 'result': saved backtest or None}."""
    if not GATE_ENABLED:
        return {'state': 'off', 'result': None}
    if str(rule.get('strategy') or '') == 'Dollar-Cost Averaging':
        # DCA buys on a schedule and ignores the AI outlook, so there is nothing to gate.
        return {'state': 'off', 'result': None}
    if rule.get('backtest_check') is False:
        # The member switched the check off for this asset: the bot trades on the AI outlook alone.
        return {'state': 'disabled', 'result': await rule_result(owner, symbol)}
    result = await rule_result(owner, symbol)
    if not result or result.get('rule_key') != rule_key(rule):
        return {'state': 'pending', 'result': None}
    return {'state': 'pass' if passes(result) else 'fail', 'result': result}


async def status_for(owner: str, symbols: List[str]) -> Dict[str, Any]:
    """Gate status of the member's saved settings, and the tuned settings, for each asset."""
    await ensure_schema()
    db = get_database()
    rules = {
        str(r['asset_symbol']).upper(): dict(r)
        for r in await db.fetch_all(
            query=(
                "SELECT asset_symbol, strategy, is_active, stop_loss_pct, trailing_stop_pct, take_profit_pct, dca_interval_hours, "
                "backtest_check FROM trading_rules WHERE owner_id = :owner"
            ),
            values={'owner': owner},
        )
    }
    wanted = sorted({s.upper() for s in symbols} | set(rules))
    tuned = {}
    if wanted:
        rows = await db.fetch_all(
            query="SELECT symbol, computed_at, payload FROM backtest_tuned WHERE symbol = ANY(:symbols)",
            values={'symbols': wanted},
        )
        tuned = {r['symbol']: {**_decode(r['payload']), 'computed_at': r['computed_at'].isoformat()} for r in rows}
    assets = {}
    for symbol in wanted:
        rule = rules.get(symbol)
        gate = await gate_status(owner, symbol, rule) if rule else {'state': 'no_rule', 'result': None}
        assets[symbol] = {'gate': gate['state'], 'rule_result': gate['result'], 'bot_on': bool(rule and rule.get('is_active')), 'tuned': tuned.get(symbol)}
    return {
        'enabled': GATE_ENABLED,
        'min_win_rate': GATE_MIN_WIN_RATE,
        'min_trades': GATE_MIN_TRADES,
        'assets': assets,
    }


# --- Background refresh ---------------------------------------------------------------------------

async def refresh_tick(now: Optional[datetime] = None) -> Optional[str]:
    """Refreshes ONE stale result per call: first active bots whose settings changed or whose result is
    over a day old, then the tuned settings of watchlist assets. Returns what it did, or None."""
    await ensure_schema()
    now = now or datetime.now(timezone.utc)
    db = get_database()
    rules = await db.fetch_all(
        query=(
            "SELECT r.owner_id, r.asset_symbol, r.strategy, r.stop_loss_pct, r.trailing_stop_pct, r.take_profit_pct, "
            "r.dca_interval_hours, b.rule_key, b.computed_at "
            "FROM trading_rules r LEFT JOIN backtest_rule_results b "
            "ON b.owner_id = r.owner_id AND b.symbol = r.asset_symbol "
            "WHERE r.is_active AND r.strategy <> 'Dollar-Cost Averaging' AND r.backtest_check IS NOT FALSE "
            "ORDER BY b.computed_at NULLS FIRST"
        )
    )
    for row in rules:
        rule = dict(row)
        if row['rule_key'] == rule_key(rule) and row['computed_at'] and now - row['computed_at'] < RESULT_MAX_AGE:
            continue
        owner, symbol = str(row['owner_id']), str(row['asset_symbol']).upper()
        try:
            await compute_rule(owner, symbol, rule)
            return f'rule {symbol}'
        except Exception as exc:
            logger.warning('backtest_rule_refresh_failed owner=%s symbol=%s error=%s', owner, symbol, exc)
            return None

    symbols = await db.fetch_all(
        query=(
            "SELECT w.symbol FROM (SELECT DISTINCT upper(symbol) AS symbol FROM watchlists) w "
            "LEFT JOIN backtest_tuned t ON t.symbol = w.symbol "
            "WHERE t.computed_at IS NULL OR t.computed_at < :stale ORDER BY t.computed_at NULLS FIRST LIMIT 1"
        ),
        values={'stale': now - RESULT_MAX_AGE},
    )
    for row in symbols:
        try:
            await compute_tuned(row['symbol'])
            return f"tuned {row['symbol']}"
        except Exception as exc:
            # No price history (e.g. FX pairs Yahoo lacks): mark it so it is retried tomorrow, not every tick.
            logger.info('backtest_tune_skipped symbol=%s error=%s', row['symbol'], exc)
            await db.execute(
                query=(
                    "INSERT INTO backtest_tuned (symbol, computed_at, payload) VALUES (:symbol, now(), CAST(:payload AS jsonb)) "
                    "ON CONFLICT (symbol) DO UPDATE SET computed_at = EXCLUDED.computed_at, payload = EXCLUDED.payload"
                ),
                values={'symbol': row['symbol'], 'payload': json.dumps({'symbol': row['symbol'], 'error': str(exc)})},
            )
            return None
    return None

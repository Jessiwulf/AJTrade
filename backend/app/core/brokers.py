"""Broker providers: which one a member is using, which market a symbol trades on, and one interface
over Alpaca (US stocks + crypto, USD) and Settrade (Thai SET stocks, THB).

Pages show the active provider only, so Alpaca and Settrade data are never mixed; the trading bot
routes each asset to the broker that lists it.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from app.core.db import get_database

logger = logging.getLogger("ajtrade.brokers")

ALPACA = "alpaca"
SETTRADE = "settrade"
PROVIDERS = (ALPACA, SETTRADE)
DEFAULT_PROVIDER = ALPACA
PROVIDER_INFO = {
    ALPACA: {"label": "Alpaca", "market": "US stocks & crypto", "currency": "USD"},
    SETTRADE: {"label": "Settrade", "market": "Thai SET stocks", "currency": "THB"},
}


def normalize_provider(value: Any) -> str:
    provider = str(value or "").strip().lower()
    if provider not in PROVIDERS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unknown provider (use {' or '.join(PROVIDERS)})")
    return provider


# --- Member setting -------------------------------------------------------------------------
_schema_ready = False


async def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    db = get_database()
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS user_settings ("
            "owner_id uuid PRIMARY KEY,"
            f"broker_provider text NOT NULL DEFAULT '{DEFAULT_PROVIDER}',"
            "updated_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )
    )
    # Daily account value per provider: Settrade has no account-history endpoint, so the app keeps
    # its own (Alpaca's history comes from Alpaca).
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS broker_equity_snapshots ("
            "owner_id uuid NOT NULL,"
            "provider text NOT NULL,"
            "day date NOT NULL,"
            "equity numeric(18,4) NOT NULL,"
            "cash numeric(18,4),"
            "updated_at timestamptz NOT NULL DEFAULT now(),"
            "PRIMARY KEY (owner_id, provider, day)"
            ")"
        )
    )
    _schema_ready = True


async def get_active_provider(owner: str) -> str:
    try:
        await ensure_schema()
        value = await get_database().fetch_val(
            query="SELECT broker_provider FROM user_settings WHERE owner_id = :owner", values={"owner": owner}
        )
    except Exception as exc:
        logger.warning("broker_provider_lookup_failed owner=%s error=%s", owner, exc)
        return DEFAULT_PROVIDER
    return value if value in PROVIDERS else DEFAULT_PROVIDER


async def set_active_provider(owner: str, provider: str) -> str:
    provider = normalize_provider(provider)
    await ensure_schema()
    await get_database().execute(
        query=(
            "INSERT INTO user_settings (owner_id, broker_provider, updated_at) VALUES (:owner, :provider, now()) "
            "ON CONFLICT (owner_id) DO UPDATE SET broker_provider = EXCLUDED.broker_provider, updated_at = now()"
        ),
        values={"owner": owner, "provider": provider},
    )
    return provider


# --- Markets --------------------------------------------------------------------------------
_symbol_provider_cache: Dict[str, str] = {}


def provider_for_symbol_hint(symbol: str) -> Optional[str]:
    """Decides from the symbol's form alone; None for bare tickers that could be US or Thai."""
    symbol = (symbol or "").strip().upper()
    if symbol.endswith(".BK"):
        return SETTRADE
    if not symbol.isalpha():
        return ALPACA  # BTC-USD, BRK-B, HKDTHB=X, VOD.L ...
    return _symbol_provider_cache.get(symbol)


async def provider_for_symbol(symbol: str) -> str:
    """Settrade for SET stocks (".BK", or a bare ticker that only Yahoo's Thai listing knows), else Alpaca."""
    symbol = (symbol or "").strip().upper()
    hint = provider_for_symbol_hint(symbol)
    if hint:
        return hint
    provider = ALPACA
    try:
        from starlette.concurrency import run_in_threadpool

        from app.api.market import _fetch_yahoo_chart_with_variants

        chart = await run_in_threadpool(_fetch_yahoo_chart_with_variants, symbol, "day")
        if str(chart.get("symbol") or "").upper().endswith(".BK"):
            provider = SETTRADE
    except Exception as exc:
        logger.info("symbol_market_lookup_failed symbol=%s error=%s", symbol, exc)
        return ALPACA  # don't cache a failed lookup
    _symbol_provider_cache[symbol] = provider
    return provider


async def filter_symbols(symbols: List[str], provider: str) -> List[str]:
    out = []
    for symbol in symbols:
        if await provider_for_symbol(symbol) == provider:
            out.append(symbol)
    return out


# --- Unified broker interface ---------------------------------------------------------------
def _f(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def get_positions(owner: str, provider: str) -> List[Dict[str, Any]]:
    """[{symbol, qty, avg_entry_price, current_price, market_value, unrealized_pl, unrealized_plpc}]"""
    if provider == SETTRADE:
        from app.core import settrade

        return settrade.normalize_positions(await settrade.get_portfolios(owner))

    from app.core.alpaca import get_alpaca_positions

    return [
        {
            "symbol": p.get("symbol"),
            "qty": _f(p.get("qty")),
            "avg_entry_price": _f(p.get("avg_entry_price")),
            "current_price": _f(p.get("current_price")),
            "market_value": _f(p.get("market_value")),
            "unrealized_pl": _f(p.get("unrealized_pl")),
            "unrealized_plpc": _f(p.get("unrealized_plpc")),
        }
        for p in await get_alpaca_positions(owner)
        if p.get("symbol")
    ]


async def get_account(owner: str, provider: str) -> Dict[str, Any]:
    """{provider, currency, cash, equity, last_equity, buying_power, positions_value, positions}"""
    positions = await get_positions(owner, provider)
    positions_value = sum(p["market_value"] or 0.0 for p in positions)

    if provider == SETTRADE:
        from app.core import settrade

        account = settrade.normalize_account(await settrade.get_account_info(owner))
        cash = account["cash"] or 0.0
        equity = cash + positions_value
        last_equity = await _previous_snapshot_equity(owner, provider)
        await record_equity_snapshot(owner, provider, equity, cash)
        return {
            "provider": provider,
            "currency": "THB",
            "cash": cash,
            "equity": equity,
            "last_equity": last_equity,
            "buying_power": account["buying_power"],
            "positions_value": positions_value,
            "positions": positions,
            "status": "ACTIVE",
        }

    from app.core.alpaca import get_alpaca_account

    account = await get_alpaca_account(owner)
    return {
        "provider": provider,
        "currency": account.get("currency") or "USD",
        "cash": _f(account.get("cash")) or 0.0,
        "equity": _f(account.get("equity") or account.get("portfolio_value")) or 0.0,
        "last_equity": _f(account.get("last_equity")),
        "buying_power": _f(account.get("buying_power")),
        "positions_value": positions_value,
        "positions": positions,
        "status": account.get("status"),
        "account_id": account.get("id"),
    }


async def record_equity_snapshot(owner: str, provider: str, equity: float, cash: Optional[float]) -> None:
    """Keeps today's latest account value (Bangkok trading day for Settrade)."""
    try:
        await ensure_schema()
        day = (datetime.now(timezone.utc) + timedelta(hours=7)).date()
        await get_database().execute(
            query=(
                "INSERT INTO broker_equity_snapshots (owner_id, provider, day, equity, cash, updated_at) "
                "VALUES (:owner, :provider, :day, :equity, :cash, now()) "
                "ON CONFLICT (owner_id, provider, day) DO UPDATE SET equity = EXCLUDED.equity, cash = EXCLUDED.cash, updated_at = now()"
            ),
            values={"owner": owner, "provider": provider, "day": day, "equity": equity, "cash": cash},
        )
    except Exception as exc:
        logger.warning("equity_snapshot_failed owner=%s provider=%s error=%s", owner, provider, exc)


async def _previous_snapshot_equity(owner: str, provider: str) -> Optional[float]:
    try:
        await ensure_schema()
        today = (datetime.now(timezone.utc) + timedelta(hours=7)).date()
        value = await get_database().fetch_val(
            query=(
                "SELECT equity FROM broker_equity_snapshots WHERE owner_id = :owner AND provider = :provider AND day < :today "
                "ORDER BY day DESC LIMIT 1"
            ),
            values={"owner": owner, "provider": provider, "today": today},
        )
    except Exception:
        return None
    return _f(value)


async def get_equity_history(owner: str, provider: str, days: int) -> List[Dict[str, Any]]:
    """[{date, equity}] oldest first. Settrade: the app's daily snapshots. Alpaca: Alpaca's history."""
    if provider == SETTRADE:
        await ensure_schema()
        since: date = (datetime.now(timezone.utc) + timedelta(hours=7)).date() - timedelta(days=days)
        rows = await get_database().fetch_all(
            query=(
                "SELECT day, equity FROM broker_equity_snapshots WHERE owner_id = :owner AND provider = :provider AND day >= :since "
                "ORDER BY day ASC"
            ),
            values={"owner": owner, "provider": provider, "since": since},
        )
        return [{"date": r["day"].isoformat(), "equity": float(r["equity"])} for r in rows]

    from app.core.alpaca import get_alpaca_portfolio_history

    period = next((p for limit, p in ((7, "1W"), (31, "1M"), (93, "3M"), (186, "6M")) if days <= limit), "1A")
    history = await get_alpaca_portfolio_history(owner, period=period, timeframe="1D")
    out = []
    for ts, value in zip(history.get("timestamp") or [], history.get("equity") or []):
        value = _f(value)
        if value is None or value <= 0:
            continue
        out.append({"date": datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat(), "equity": value})
    return out


def not_connected(provider: str, exc: HTTPException) -> HTTPException:
    """Turns a missing-credentials error into a clear 409 the pages can show as "connect your account"."""
    detail = str(exc.detail or "")
    if provider == ALPACA and exc.status_code == status.HTTP_404_NOT_FOUND and "key not found" in detail.lower():
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Alpaca paper account not connected. Add your Alpaca keys in API Management.")
    return exc

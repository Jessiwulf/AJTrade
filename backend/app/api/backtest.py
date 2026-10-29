"""POST /api/bot/backtest: replays one asset's bot settings over past prices (see app.core.backtest)."""
import logging
import re
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.api.dependencies import get_current_user
from app.core import backtest, backtest_store

logger = logging.getLogger('ajtrade.backtest')
router = APIRouter()

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,24}$")
# Training the model day by day is CPU-heavy: one backtest or tuning at a time (shared with the
# background refresh), on one core.
_RUNNING = backtest_store.RUNNING


class BacktestRule(BaseModel):
    # Same field names as the Auto Trade page, so it can send the settings being edited (saved or not).
    strategy: str = 'Trend Following'
    stopLossPct: float = Field(5.0, ge=0, le=100)
    trailingStopLossPct: float = Field(0.0, ge=0, le=100)
    takeProfitPct: float = Field(0.0, ge=0, le=1000)
    maxCapitalPerTrade: float = Field(1000.0, gt=0, le=10_000_000)
    dcaIntervalHours: int = Field(24, ge=1, le=24 * 31)


class BacktestIn(BaseModel):
    symbol: str
    period: Literal['3m', '6m', '1y', '5y', '10y', '20y'] = '1y'
    startCash: float = Field(10_000.0, ge=100, le=100_000_000)
    rule: BacktestRule = BacktestRule()


@router.post('/backtest')
async def run_backtest(payload: BacktestIn, user=Depends(get_current_user)):
    if not user.get('sub'):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    symbol = payload.symbol.strip().upper().replace('/', '-')
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid asset symbol')

    from app.api.ml_v2 import BOT_MIN_SIGNAL_CONFIDENCE

    rule = {
        'strategy': payload.rule.strategy,
        'stop_loss_pct': payload.rule.stopLossPct,
        'trailing_stop_pct': payload.rule.trailingStopLossPct,
        'take_profit_pct': payload.rule.takeProfitPct,
        'max_capital': payload.rule.maxCapitalPerTrade,
        'dca_interval_hours': payload.rule.dcaIntervalHours,
    }
    news = await _saved_news(symbol)
    async with _RUNNING:
        try:
            return await run_in_threadpool(
                backtest.run_backtest,
                symbol,
                rule,
                period=payload.period,
                start_cash=payload.startCash,
                news_daily=news,
                min_confidence=BOT_MIN_SIGNAL_CONFIDENCE,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning('backtest_failed symbol=%s error=%s', symbol, exc)
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Backtest failed: {exc}')


@router.get('/backtest/status')
async def backtest_status(user=Depends(get_current_user)):
    """Saved backtest results for the member's watchlist and bot assets (never computed here):
    the gate state of their saved settings, and the tuned settings per asset."""
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    from app.core.db import get_database

    rows = await get_database().fetch_all(
        query="SELECT symbol FROM watchlists WHERE owner = :owner", values={'owner': owner}
    )
    return await backtest_store.status_for(owner, [str(r['symbol']) for r in rows])


@router.get('/autoconfig')
async def auto_config(symbol: str, target: float = Query(backtest_store.GATE_MIN_WIN_RATE, ge=50, le=95), user=Depends(get_current_user)):
    """Bot settings (strategy + exits) that did best in the past year's backtest (passing the gate),
    with how they did on the months the search never saw. Saved per asset and refreshed daily."""
    if not user.get('sub'):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Unauthorized')
    symbol = symbol.strip().upper().replace('/', '-')
    if not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid asset symbol')

    # The background job tunes every watchlist asset daily: reuse that unless it is missing or old.
    status_now = await backtest_store.status_for(user['sub'], [symbol])
    saved = (status_now['assets'].get(symbol) or {}).get('tuned')
    if saved and saved.get('rule') and float(target) == float(saved.get('target_win_rate') or 0):
        return saved
    try:
        if float(target) == backtest_store.GATE_MIN_WIN_RATE:
            return await backtest_store.compute_tuned(symbol)
        from app.api.ml_v2 import BOT_MIN_SIGNAL_CONFIDENCE

        news = await _saved_news(symbol)
        async with _RUNNING:
            return await run_in_threadpool(
                backtest.tune_rule, symbol, target_win_rate=target, min_trades=backtest_store.GATE_MIN_TRADES, news_daily=news,
                min_confidence=BOT_MIN_SIGNAL_CONFIDENCE,
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    except Exception as exc:
        logger.warning('autoconfig_failed symbol=%s error=%s', symbol, exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'Auto-config failed: {exc}')


async def _saved_news(symbol: str) -> Optional[list]:
    """The saved daily Alpaca news sentiment for US stocks, ETFs and crypto (last 30 days)."""
    try:
        from app.core import brokers, news_history

        if await brokers.provider_for_symbol(symbol) != brokers.ALPACA:
            return []
        return (await news_history.daily_history([symbol])).get(symbol) or []
    except Exception:
        return []

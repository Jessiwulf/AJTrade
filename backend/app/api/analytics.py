"""
Feature #5: Performance Analytics & Market Dashboard API
Provides endpoints for:
  - Portfolio performance metrics (P/L, growth, win rate) from the member's active broker
    (Alpaca or Settrade; see app.core.brokers). The two are never mixed.
  - Transaction history (broker fills, see portfolio.sync_trade_history)
  - Market sentiment heatmap (from the cached FinBERT news scores)
  - Asset details (price, metrics, news)
"""
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.dependencies import get_current_user
from app.api.market import get_historical_data, get_market_price
from app.core import brokers, news_history, trade_stats
from app.core.db import get_database

router = APIRouter()


def _parse_json_payload(raw_value):
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        return raw_value
    if isinstance(raw_value, str):
        try:
            parsed = json.loads(raw_value)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None
    return None


def _sentiment_label_from_score(score: float) -> str:
    if score >= 0.5:
        return 'Very Bullish'
    if score >= 0.1:
        return 'Bullish'
    if score > -0.1:
        return 'Neutral'
    if score >= -0.5:
        return 'Bearish'
    return 'Very Bearish'


def _safe_float(value, default=0.0) -> float:
    try:
        n = float(value)
        return n if n == n else default
    except Exception:
        return default


async def _sentiment_from_cache(db, owner: str, symbol: str) -> Optional[dict]:
    insight_row = await db.fetch_one(
        query=(
            "SELECT payload, updated_at FROM insights "
            "WHERE owner = :owner AND symbol = :symbol "
            "ORDER BY updated_at DESC LIMIT 1"
        ),
        values={"owner": owner, "symbol": symbol},
    )

    news_row = await db.fetch_one(
        query=(
            "SELECT payload, updated_at FROM news_cache "
            "WHERE owner = :owner AND symbol = :symbol "
            "ORDER BY updated_at DESC LIMIT 1"
        ),
        values={"owner": owner, "symbol": symbol},
    )

    insight_payload = _parse_json_payload(insight_row['payload']) if insight_row else None
    news_payload = _parse_json_payload(news_row['payload']) if news_row else None
    # Per-day FinBERT sentiment saved with the latest AI insight (heatmap columns).
    daily = (insight_payload or {}).get('news_sentiment_daily') or []

    scored_articles = []
    for article in (news_payload or {}).get('articles') or []:
        if not isinstance(article, dict):
            continue
        if article.get('sentiment_score') is None:
            continue
        scored_articles.append(_safe_float(article.get('sentiment_score'), 0.0))

    if scored_articles:
        avg_sentiment = float(sum(scored_articles) / len(scored_articles))
        positive_count = sum(1 for score in scored_articles if score > 0.1)
        negative_count = sum(1 for score in scored_articles if score < -0.1)
        neutral_count = len(scored_articles) - positive_count - negative_count
        total_articles = len(scored_articles)
        return {
            'symbol': str(symbol).upper(),
            'avg_sentiment': avg_sentiment,
            'heatmap_label': _sentiment_label_from_score(avg_sentiment),
            'positive_count': positive_count,
            'negative_count': negative_count,
            'neutral_count': neutral_count,
            'total_articles': total_articles,
            'date': str((news_row['updated_at'] if news_row else None) or (insight_row['updated_at'] if insight_row else None) or ''),
            'daily': daily,
        }

    insight_score = _safe_float((insight_payload or {}).get('latest_sentiment_score'), None)
    if insight_score is not None:
        return {
            'symbol': str(symbol).upper(),
            'avg_sentiment': float(insight_score),
            'heatmap_label': _sentiment_label_from_score(float(insight_score)),
            'positive_count': 0,
            'negative_count': 0,
            'neutral_count': 0,
            'total_articles': 0,
            'date': str((insight_row['updated_at'] if insight_row else None) or ''),
            'daily': daily,
        }

    cached_avg = _safe_float((news_payload or {}).get('avg_sentiment'), None)
    if cached_avg is not None:
        return {
            'symbol': str(symbol).upper(),
            'avg_sentiment': float(cached_avg),
            'heatmap_label': _sentiment_label_from_score(float(cached_avg)),
            'positive_count': 0,
            'negative_count': 0,
            'neutral_count': 0,
            'total_articles': int((news_payload or {}).get('articles_count') or 0),
            'date': str((news_row['updated_at'] if news_row else None) or ''),
            'daily': daily,
        }

    return None


# ========== Models / Schemas ==========

class TransactionOut(BaseModel):
    """Transaction detail response"""
    id: str
    symbol: str
    trade_type: str
    quantity: float
    price: float
    notional: float
    fee: float
    pl: Optional[float]
    signal_source: str
    created_at: str


class PortfolioMetrics(BaseModel):
    """Portfolio performance snapshot"""
    total_value: float
    cash_balance: float
    positions_value: float
    total_pl: float
    unrealized_pl: float
    realized_pl: float
    win_rate: Optional[float]  # None until something has been sold
    total_trades: int
    closed_trades: int
    winning_trades: int
    losing_trades: int
    this_month: Dict[str, Any]  # same stats for the sells closed in the current UTC month
    daily_return: float


class PerformanceHistory(BaseModel):
    """Daily account value from Alpaca's portfolio history (for charting)"""
    date: str
    total_value: float
    total_pl: float


class SentimentDataPoint(BaseModel):
    """Market sentiment for a single asset"""
    symbol: str
    avg_sentiment: Optional[float]  # -1 to +1
    heatmap_label: Optional[str]  # 'Very Bullish', 'Bullish', etc.
    positive_count: int
    negative_count: int
    neutral_count: int
    total_articles: int
    date: Optional[str]


class AssetDetail(BaseModel):
    """Asset detail like Google Finance"""
    symbol: str
    price: float
    price_change: float
    price_change_pct: float
    market_cap: Optional[str]
    pe_ratio: Optional[float]
    dividend_yield: Optional[float]
    volume: Optional[int]
    avg_volume: Optional[int]
    week_52_high: Optional[float]
    week_52_low: Optional[float]
    description: Optional[str]
    sentiment: Optional[SentimentDataPoint]
    historical_data: Optional[dict]  # chart data


class HoldingPerformanceOut(BaseModel):
    """Individual holdings performance row."""
    asset_symbol: str
    quantity: float
    avg_buy_price: float
    current_price: float
    individual_pl: float
    return_pct: float
    is_open: bool = True  # False: sold out, listed for its closed-trade record
    win_rate: Optional[float] = None
    closed_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    realized_pl: float = 0.0


# ========== Internal Helpers ==========

async def _provider(owner: str, requested: Optional[str]) -> str:
    return brokers.normalize_provider(requested) if requested else await brokers.get_active_provider(owner)


async def _provider_portfolio(db, owner: str, provider: str):
    from app.api.portfolio import _get_owner_portfolio

    return await _get_owner_portfolio(db, owner, provider)


async def _history_rows(db, owner: str, provider: str):
    portfolio = await _provider_portfolio(db, owner, provider)
    if not portfolio:
        return []
    return await db.fetch_all(
        "SELECT * FROM trading_history WHERE portfolio_id = :id ORDER BY created_at DESC",
        {"id": portfolio['id']},
    )


async def calculate_portfolio_metrics(db, owner: str, provider: str = brokers.ALPACA) -> PortfolioMetrics:
    """Account value, cash, unrealized P/L and today's change straight from the provider's broker;
    realized P/L and win rate from that broker's fills in the trade history."""
    try:
        account = await brokers.get_account(owner, provider)
    except HTTPException as exc:
        raise brokers.not_connected(provider, exc)

    equity = _safe_float(account.get('equity'))
    last_equity = _safe_float(account.get('last_equity'))
    cash_balance = _safe_float(account.get('cash'))
    positions = account['positions']
    positions_value = sum(_safe_float(p.get('market_value')) for p in positions)
    unrealized_pl = sum(_safe_float(p.get('unrealized_pl')) for p in positions)

    trades = await _history_rows(db, owner, provider)
    # Win rate: share of closed (SELL) trades that realized a profit. BUYs open positions and can't
    # win or lose on their own, so they are excluded from the denominator.
    closed = trade_stats.closed_trades(trades)
    overall = trade_stats.summarize(closed)
    current_month = trade_stats.month_key(datetime.now(timezone.utc))
    this_month = {
        'month': current_month,
        **trade_stats.summarize(t for t in closed if trade_stats.month_key(t['at']) == current_month),
    }

    # Change since the previous trading day's close (Alpaca's last_equity; for Settrade, the app's
    # snapshot from the previous day it saw the account).
    daily_return = ((equity - last_equity) / last_equity * 100) if last_equity > 0 else 0

    return PortfolioMetrics(
        total_value=equity,
        cash_balance=cash_balance,
        positions_value=positions_value,
        total_pl=overall['realized_pl'] + unrealized_pl,
        unrealized_pl=unrealized_pl,
        realized_pl=overall['realized_pl'],
        win_rate=overall['win_rate'],
        total_trades=len(trades),
        closed_trades=overall['closed_trades'],
        winning_trades=overall['winning_trades'],
        losing_trades=overall['losing_trades'],
        this_month=this_month,
        daily_return=daily_return,
    )


# ========== API Endpoints ==========

@router.get('/portfolio/metrics')
async def get_portfolio_metrics(provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    """Current portfolio metrics from the active provider's account."""
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=400, detail='Invalid user')
    try:
        provider = await _provider(owner, provider)
        metrics = await calculate_portfolio_metrics(get_database(), owner, provider)
        return {**metrics.dict(), 'provider': provider, 'currency': brokers.PROVIDER_INFO[provider]['currency']}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/portfolio/history')
async def get_portfolio_history(days: int = 30, provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    """Daily account value: Alpaca's portfolio history, or for Settrade the daily snapshots AJTrade has
    recorded since the account was connected. Nothing is made up for days without data."""
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=400, detail='Invalid user')
    provider = await _provider(owner, provider)
    try:
        history = await brokers.get_equity_history(owner, provider, days)
    except HTTPException as exc:
        raise brokers.not_connected(provider, exc)

    base_value = history[0]['equity'] if history else 0.0
    return [
        PerformanceHistory(date=point['date'], total_value=point['equity'], total_pl=point['equity'] - base_value).dict()
        for point in history
    ]


@router.get('/transactions')
async def get_transactions(limit: int = 100, symbol: Optional[str] = None, provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    """Transaction history of the active provider's portfolio."""
    try:
        db = get_database()
        owner = user.get('sub')
        if not owner:
            raise HTTPException(status_code=400, detail='Invalid user')

        provider = await _provider(owner, provider)
        portfolio = await _provider_portfolio(db, owner, provider)
        if not portfolio:
            return []

        portfolio_id = portfolio['id']

        # Build query
        query = "SELECT * FROM trading_history WHERE portfolio_id = :id"
        params = {"id": portfolio_id}

        if symbol:
            query += " AND symbol = :symbol"
            params["symbol"] = symbol

        query += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = limit

        transactions = await db.fetch_all(query, params)

        return [
            TransactionOut(
                id=str(t['id']),
                symbol=t['symbol'],
                trade_type=t['trade_type'],
                quantity=float(t['quantity']),
                price=float(t['price']),
                notional=float(t['notional']),
                fee=float(t['fee']),
                pl=float(t['pl']) if t['pl'] is not None else None,
                signal_source=t['signal_source'],
                created_at=t['created_at'].isoformat() if t['created_at'] else None
            ).dict()
            for t in transactions
        ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/sentiment-heatmap')
async def get_sentiment_heatmap(provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    """Sentiment for each watchlist asset from the cached FinBERT news scores. Assets without scored news
    are returned with avg_sentiment = null ("No data") instead of a made-up neutral score."""
    try:
        db = get_database()
        owner = user.get('sub')
        if not owner:
            raise HTTPException(status_code=400, detail='Invalid user')

        watchlist = await db.fetch_all(
            "SELECT symbol FROM watchlists WHERE owner = :owner",
            {"owner": owner}
        )
        provider = await _provider(owner, provider)
        symbols = await brokers.filter_symbols([str(item['symbol']).upper() for item in watchlist], provider)

        # 30 days of saved Alpaca news sentiment (US stocks, ETFs, crypto), filled by the scheduler.
        covered = symbols if provider == brokers.ALPACA else []
        try:
            history = await news_history.daily_history(covered)
        except Exception:
            history = {}

        with_data = []
        without_data = []
        now = datetime.now(timezone.utc)
        for symbol in symbols:
            cached_sentiment = await _sentiment_from_cache(db, owner, symbol)
            saved_daily = history.get(symbol) or []
            coverage = 'alpaca' if symbol in covered and news_history.news_symbol(symbol) else None
            if cached_sentiment:
                cached_sentiment['daily'] = news_history.merge_daily(cached_sentiment.get('daily') or [], saved_daily)
                cached_sentiment['news_coverage'] = coverage
                with_data.append(cached_sentiment)
                continue
            recent = news_history.recent_average(saved_daily, now=now)
            if recent:
                # No AI insight yet: the tile shows the last 7 days of saved news.
                with_data.append({
                    'symbol': symbol,
                    **recent,
                    'heatmap_label': _sentiment_label_from_score(recent['avg_sentiment']),
                    'daily': saved_daily,
                    'news_coverage': coverage,
                })
            else:
                without_data.append({
                    "symbol": symbol,
                    "avg_sentiment": None,
                    "heatmap_label": "No data",
                    "positive_count": 0,
                    "negative_count": 0,
                    "neutral_count": 0,
                    "total_articles": 0,
                    "date": None,
                    "daily": saved_daily,
                    "news_coverage": coverage,
                })

        with_data.sort(key=lambda x: x['avg_sentiment'], reverse=True)
        return with_data + without_data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/holdings', response_model=List[HoldingPerformanceOut])
async def get_holdings_performance(provider: Optional[str] = Query(default=None), user=Depends(get_current_user)):
    """Per-asset performance straight from the active provider's positions, with each asset's win rate
    from its closed trades. Assets that were sold out are listed too (is_open = False)."""
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=400, detail='Invalid user')
    provider = await _provider(owner, provider)
    try:
        positions = await brokers.get_positions(owner, provider)
    except HTTPException as exc:
        raise brokers.not_connected(provider, exc)

    db = get_database()
    per_symbol = trade_stats.by_symbol(trade_stats.closed_trades(await _history_rows(db, owner, provider)))

    output: List[HoldingPerformanceOut] = []
    for position in positions:
        symbol = str(position.get('symbol') or '').upper()
        if not symbol:
            continue
        # Crypto sells can leave dust behind (e.g. 0.000000002 SOL): worth under a cent, so the
        # position counts as closed rather than as a $0.00 holding.
        if abs(_safe_float(position.get('market_value'))) < 0.01:
            continue
        output.append(
            HoldingPerformanceOut(
                asset_symbol=symbol,
                quantity=_safe_float(position.get('qty')),
                avg_buy_price=_safe_float(position.get('avg_entry_price')),
                current_price=_safe_float(position.get('current_price')),
                individual_pl=_safe_float(position.get('unrealized_pl')),
                return_pct=_safe_float(position.get('unrealized_plpc')) * 100.0,
                **per_symbol.pop(symbol, {}),
            )
        )
    for symbol, stats in per_symbol.items():
        output.append(
            HoldingPerformanceOut(
                asset_symbol=symbol, quantity=0, avg_buy_price=0, current_price=0, individual_pl=0, return_pct=0,
                is_open=False, **stats,
            )
        )
    return sorted(output, key=lambda row: (not row.is_open, row.asset_symbol))


@router.get('/asset/{symbol}')
async def get_asset_detail(symbol: str, range_: str = '1mo'):
    """Get asset detail like Google Finance (price, metrics, chart, sentiment)"""
    try:
        # Get current price
        price = await get_market_price(symbol)

        # Get historical data for chart
        try:
            hist_data = await get_historical_data(symbol, range_)
        except:
            hist_data = None

        # Calculate price change from the returned chart payload.
        price_change = 0
        price_change_pct = 0
        points = []
        if isinstance(hist_data, dict):
            points = hist_data.get('points') or []
        quote = (hist_data or {}).get('quote') if isinstance(hist_data, dict) else {}
        quote = quote or {}

        closes = [point.get('close') for point in points if point.get('close') is not None]
        if len(closes) > 1:
            old_price = float(closes[0])
            latest_price = float(closes[-1])
            price_change = latest_price - old_price
            price_change_pct = (price_change / old_price * 100) if old_price > 0 else 0

        return AssetDetail(
            symbol=symbol,
            price=float(price),
            price_change=price_change,
            price_change_pct=price_change_pct,
            market_cap=str(quote.get('market_cap')) if quote.get('market_cap') is not None else None,
            pe_ratio=float(quote.get('pe_ratio')) if quote.get('pe_ratio') is not None else None,
            dividend_yield=float(quote.get('dividend_yield')) if quote.get('dividend_yield') is not None else None,
            volume=int(float(quote.get('volume'))) if quote.get('volume') is not None else None,
            avg_volume=int(float(quote.get('avg_volume'))) if quote.get('avg_volume') is not None else None,
            week_52_high=float(quote.get('week_52_high')) if quote.get('week_52_high') is not None else None,
            week_52_low=float(quote.get('week_52_low')) if quote.get('week_52_low') is not None else None,
            description=None,
            sentiment=None,
            historical_data=hist_data
        ).dict()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

"""
Feature #5: Performance Analytics & Market Dashboard API
Provides endpoints for:
  - Portfolio performance metrics (P/L, growth, win rate) from the Alpaca paper account
  - Transaction history (Alpaca fills, see portfolio.sync_trade_history_from_alpaca)
  - Market sentiment heatmap (from the cached FinBERT news scores)
  - Asset details (price, metrics, news)
"""
from datetime import datetime, timezone
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.dependencies import get_current_user
from app.api.market import get_historical_data, get_market_price
from app.core.alpaca import get_alpaca_account, get_alpaca_portfolio_history, get_alpaca_positions
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
    win_rate: float
    total_trades: int
    closed_trades: int
    winning_trades: int
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


# ========== Internal Helpers ==========

def _alpaca_unavailable(exc: HTTPException) -> HTTPException:
    detail = str(exc.detail or '')
    if exc.status_code == status.HTTP_404_NOT_FOUND and 'key not found' in detail.lower():
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail='Alpaca paper account not connected. Add your Alpaca keys in API Management.')
    return exc


async def _history_rows(db, owner: str):
    portfolio = await db.fetch_one("SELECT id FROM portfolios WHERE owner = :owner", {"owner": owner})
    if not portfolio:
        return []
    return await db.fetch_all(
        "SELECT * FROM trading_history WHERE portfolio_id = :id ORDER BY created_at DESC",
        {"id": portfolio['id']},
    )


async def calculate_portfolio_metrics(db, owner: str) -> PortfolioMetrics:
    """Account value, cash, unrealized P/L and today's change straight from Alpaca; realized P/L and win
    rate from the Alpaca fills in the trade history."""
    try:
        account = await get_alpaca_account(owner)
        positions = await get_alpaca_positions(owner)
    except HTTPException as exc:
        raise _alpaca_unavailable(exc)

    equity = _safe_float(account.get('equity') or account.get('portfolio_value'))
    last_equity = _safe_float(account.get('last_equity'))
    cash_balance = _safe_float(account.get('cash'))
    positions_value = sum(_safe_float(p.get('market_value')) for p in positions)
    unrealized_pl = sum(_safe_float(p.get('unrealized_pl')) for p in positions)

    trades = await _history_rows(db, owner)
    closed_trades = [t for t in trades if t['trade_type'] == 'SELL' and t['pl'] is not None]
    realized_pl = sum(float(t['pl']) for t in closed_trades)
    # Win rate: share of closed (SELL) trades that realized a profit. BUYs open positions and can't
    # win or lose on their own, so they are excluded from the denominator.
    winning_trades = sum(1 for t in closed_trades if float(t['pl']) > 0)
    win_rate = (winning_trades / len(closed_trades) * 100) if closed_trades else 0

    # Change since the previous trading day's close (Alpaca's last_equity).
    daily_return = ((equity - last_equity) / last_equity * 100) if last_equity > 0 else 0

    return PortfolioMetrics(
        total_value=equity,
        cash_balance=cash_balance,
        positions_value=positions_value,
        total_pl=realized_pl + unrealized_pl,
        unrealized_pl=unrealized_pl,
        realized_pl=realized_pl,
        win_rate=win_rate,
        total_trades=len(trades),
        closed_trades=len(closed_trades),
        winning_trades=winning_trades,
        daily_return=daily_return,
    )


# ========== API Endpoints ==========

@router.get('/portfolio/metrics')
async def get_portfolio_metrics(user=Depends(get_current_user)):
    """Current portfolio metrics from the Alpaca paper account."""
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=400, detail='Invalid user')
    try:
        metrics = await calculate_portfolio_metrics(get_database(), owner)
        return metrics.dict()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


_HISTORY_PERIODS = ((7, '1W'), (31, '1M'), (93, '3M'), (186, '6M'))


@router.get('/portfolio/history')
async def get_portfolio_history(days: int = 30, user=Depends(get_current_user)):
    """Daily account value from Alpaca's portfolio history. Days before the account had any value are
    left out; with no history the list is empty (nothing is made up)."""
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=400, detail='Invalid user')
    period = next((p for limit, p in _HISTORY_PERIODS if days <= limit), '1A')
    try:
        history = await get_alpaca_portfolio_history(owner, period=period, timeframe='1D')
    except HTTPException as exc:
        raise _alpaca_unavailable(exc)

    timestamps = history.get('timestamp') or []
    equity = history.get('equity') or []
    base_value = _safe_float(history.get('base_value'))
    rows = []
    for ts, value in zip(timestamps, equity):
        value = _safe_float(value, None)
        if value is None or value <= 0:
            continue
        rows.append(
            PerformanceHistory(
                date=datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat(),
                total_value=value,
                total_pl=value - base_value if base_value > 0 else 0.0,
            ).dict()
        )
    return rows


@router.get('/transactions')
async def get_transactions(limit: int = 100, symbol: Optional[str] = None, user=Depends(get_current_user)):
    """Get transaction history for portfolio"""
    try:
        db = get_database()
        owner = user.get('sub')
        if not owner:
            raise HTTPException(status_code=400, detail='Invalid user')

        portfolio = await db.fetch_one(
            "SELECT id FROM portfolios WHERE owner = :owner",
            {"owner": owner}
        )
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
async def get_sentiment_heatmap(user=Depends(get_current_user)):
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

        with_data = []
        without_data = []
        for item in watchlist:
            symbol = str(item['symbol']).upper()
            cached_sentiment = await _sentiment_from_cache(db, owner, symbol)
            if cached_sentiment:
                with_data.append(cached_sentiment)
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
                    "daily": [],
                })

        with_data.sort(key=lambda x: x['avg_sentiment'], reverse=True)
        return with_data + without_data

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/holdings', response_model=List[HoldingPerformanceOut])
async def get_holdings_performance(user=Depends(get_current_user)):
    """Per-asset performance straight from the Alpaca paper account's positions."""
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=400, detail='Invalid user')
    try:
        positions = await get_alpaca_positions(owner)
    except HTTPException as exc:
        raise _alpaca_unavailable(exc)

    output: List[HoldingPerformanceOut] = []
    for position in positions:
        symbol = str(position.get('symbol') or '').upper()
        if not symbol:
            continue
        output.append(
            HoldingPerformanceOut(
                asset_symbol=symbol,
                quantity=_safe_float(position.get('qty')),
                avg_buy_price=_safe_float(position.get('avg_entry_price')),
                current_price=_safe_float(position.get('current_price')),
                individual_pl=_safe_float(position.get('unrealized_pl')),
                return_pct=_safe_float(position.get('unrealized_plpc')) * 100.0,
            )
        )
    return sorted(output, key=lambda row: row.asset_symbol)


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

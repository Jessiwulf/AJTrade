"""
Feature #5: Performance Analytics & Market Dashboard API
Provides endpoints for:
  - Portfolio performance metrics (P/L, growth, win rate)
  - Transaction history
  - Market sentiment heatmap
  - Asset details (price, metrics, news)
"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta, date
import json

from app.core.db import get_database
from app.api.dependencies import get_current_user, require_role
from app.api.market import get_market_price, get_historical_data

router = APIRouter()


# ========== Models / Schemas ==========

class TransactionIn(BaseModel):
    """Log a buy/sell transaction"""
    symbol: str
    trade_type: str  # 'BUY' or 'SELL'
    quantity: float
    price: float
    signal_source: Optional[str] = 'manual'  # 'manual', 'ai', 'bot'
    notes: Optional[str] = None


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
    winning_trades: int
    daily_return: float


class PerformanceHistory(BaseModel):
    """Historical portfolio performance (for charting)"""
    date: str
    total_value: float
    positions_value: float
    cash_balance: float
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


# ========== Internal Helpers ==========

async def calculate_portfolio_metrics(db, portfolio_id: str) -> PortfolioMetrics:
    """
    Calculate real-time portfolio metrics:
    - Total value (cash + positions)
    - P/L (realized + unrealized)
    - Win rate
    """
    # Get portfolio
    portfolio = await db.fetch_one(
        "SELECT cash_balance FROM portfolios WHERE id = :id",
        {"id": portfolio_id}
    )
    if not portfolio:
        raise ValueError("Portfolio not found")

    cash_balance = float(portfolio['cash_balance'])

    # Get positions with current prices
    positions = await db.fetch(
        "SELECT symbol, quantity, avg_price FROM portfolio_positions WHERE portfolio_id = :id AND quantity > 0",
        {"id": portfolio_id}
    )

    positions_value = 0
    for pos in positions:
        try:
            current_price = await get_market_price(pos['symbol'])
            positions_value += float(pos['quantity']) * float(current_price)
        except:
            # If price fetch fails, use avg_price as fallback
            positions_value += float(pos['quantity']) * float(pos['avg_price'])

    total_value = cash_balance + positions_value

    # Calculate P/L from trading history
    trades = await db.fetch(
        "SELECT trade_type, quantity, price, pl FROM trading_history WHERE portfolio_id = :id ORDER BY created_at",
        {"id": portfolio_id}
    )

    realized_pl = sum(float(t['pl']) or 0 for t in trades if t['trade_type'] == 'SELL')
    unrealized_pl = 0
    total_pl = realized_pl + unrealized_pl

    # Calculate win rate
    closed_trades = [t for t in trades if t['trade_type'] == 'SELL']
    winning_trades = sum(1 for t in closed_trades if (t['pl'] or 0) > 0)
    total_trades = len(trades)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

    # Daily return (simple: today's change)
    daily_return = 0
    if total_value > 0:
        yesterday_metrics = await db.fetch_one(
            "SELECT total_value FROM performance_metrics WHERE portfolio_id = :id ORDER BY metric_date DESC LIMIT 1",
            {"id": portfolio_id}
        )
        if yesterday_metrics:
            yesterday_value = float(yesterday_metrics['total_value'])
            daily_return = ((total_value - yesterday_value) / yesterday_value * 100) if yesterday_value > 0 else 0

    return PortfolioMetrics(
        total_value=total_value,
        cash_balance=cash_balance,
        positions_value=positions_value,
        total_pl=total_pl,
        unrealized_pl=unrealized_pl,
        realized_pl=realized_pl,
        win_rate=win_rate,
        total_trades=total_trades,
        winning_trades=winning_trades,
        daily_return=daily_return
    )


async def get_asset_sentiment(db, symbol: str, days: int = 7) -> Optional[SentimentDataPoint]:
    """Get latest sentiment data for an asset"""
    sentiment = await db.fetch_one(
        """
        SELECT symbol, avg_sentiment, heatmap_label, positive_count, negative_count, neutral_count, 
               total_articles, sentiment_date
        FROM market_sentiment
        WHERE symbol = :symbol AND sentiment_date >= :cutoff_date
        ORDER BY sentiment_date DESC
        LIMIT 1
        """,
        {"symbol": symbol, "cutoff_date": (date.today() - timedelta(days=days))}
    )
    
    if sentiment:
        return SentimentDataPoint(
            symbol=sentiment['symbol'],
            avg_sentiment=float(sentiment['avg_sentiment']) if sentiment['avg_sentiment'] else None,
            heatmap_label=sentiment['heatmap_label'],
            positive_count=sentiment['positive_count'],
            negative_count=sentiment['negative_count'],
            neutral_count=sentiment['neutral_count'],
            total_articles=sentiment['total_articles'],
            date=sentiment['sentiment_date'].isoformat() if sentiment['sentiment_date'] else None
        )
    return None


# ========== API Endpoints ==========

@router.get('/portfolio/metrics')
async def get_portfolio_metrics(user=Depends(get_current_user)):
    """Get current portfolio metrics (P/L, balance, win rate, etc.)"""
    try:
        db = get_database()
        owner = user.get('sub')
        if not owner:
            raise HTTPException(status_code=400, detail='Invalid user')

        # Get portfolio
        portfolio = await db.fetch_one(
            "SELECT id FROM portfolios WHERE owner = :owner",
            {"owner": owner}
        )
        if not portfolio:
            raise HTTPException(status_code=404, detail='Portfolio not found')

        metrics = await calculate_portfolio_metrics(db, portfolio['id'])
        return metrics.dict()

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/portfolio/history')
async def get_portfolio_history(days: int = 30, user=Depends(get_current_user)):
    """Get historical portfolio performance for charting"""
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
            raise HTTPException(status_code=404, detail='Portfolio not found')

        portfolio_id = portfolio['id']

        # Fetch historical metrics
        history = await db.fetch(
            """
            SELECT metric_date, total_value, positions_value, cash_balance, total_pl
            FROM performance_metrics
            WHERE portfolio_id = :id AND metric_date >= :cutoff_date
            ORDER BY metric_date ASC
            """,
            {"id": portfolio_id, "cutoff_date": (date.today() - timedelta(days=days))}
        )

        return [
            PerformanceHistory(
                date=str(h['metric_date']),
                total_value=float(h['total_value']),
                positions_value=float(h['positions_value']),
                cash_balance=float(h['cash_balance']),
                total_pl=float(h['total_pl'])
            ).dict()
            for h in history
        ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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
            raise HTTPException(status_code=404, detail='Portfolio not found')

        portfolio_id = portfolio['id']

        # Build query
        query = "SELECT * FROM trading_history WHERE portfolio_id = :id"
        params = {"id": portfolio_id}

        if symbol:
            query += " AND symbol = :symbol"
            params["symbol"] = symbol

        query += " ORDER BY created_at DESC LIMIT :limit"
        params["limit"] = limit

        transactions = await db.fetch(query, params)

        return [
            TransactionOut(
                id=str(t['id']),
                symbol=t['symbol'],
                trade_type=t['trade_type'],
                quantity=float(t['quantity']),
                price=float(t['price']),
                notional=float(t['notional']),
                fee=float(t['fee']),
                pl=float(t['pl']) if t['pl'] else None,
                signal_source=t['signal_source'],
                created_at=t['created_at'].isoformat() if t['created_at'] else None
            ).dict()
            for t in transactions
        ]

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/transactions')
async def log_transaction(payload: TransactionIn, user=Depends(require_role('authenticated_user'))):
    """Log a new buy/sell transaction"""
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
            raise HTTPException(status_code=404, detail='Portfolio not found')

        portfolio_id = portfolio['id']

        # Validate trade type
        if payload.trade_type not in ('BUY', 'SELL'):
            raise HTTPException(status_code=400, detail='Invalid trade_type')

        # Calculate notional
        notional = payload.quantity * payload.price
        fee = notional * 0.001  # 0.1% fee assumption

        # Insert transaction
        transaction = await db.fetch_one(
            """
            INSERT INTO trading_history (portfolio_id, symbol, trade_type, quantity, price, notional, fee, signal_source, notes)
            VALUES (:portfolio_id, :symbol, :trade_type, :quantity, :price, :notional, :fee, :signal_source, :notes)
            RETURNING id, created_at
            """,
            {
                "portfolio_id": portfolio_id,
                "symbol": payload.symbol,
                "trade_type": payload.trade_type,
                "quantity": payload.quantity,
                "price": payload.price,
                "notional": notional,
                "fee": fee,
                "signal_source": payload.signal_source,
                "notes": payload.notes
            }
        )

        return {"id": str(transaction['id']), "created_at": transaction['created_at'].isoformat()}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/sentiment-heatmap')
async def get_sentiment_heatmap(user=Depends(get_current_user)):
    """Get market sentiment heatmap for all watched symbols"""
    try:
        db = get_database()
        owner = user.get('sub')
        if not owner:
            raise HTTPException(status_code=400, detail='Invalid user')

        # Get user's watchlist
        watchlist = await db.fetch(
            "SELECT symbol FROM watchlists WHERE owner = :owner",
            {"owner": owner}
        )

        heatmap = []
        for item in watchlist:
            symbol = item['symbol']
            sentiment = await get_asset_sentiment(db, symbol)
            if sentiment:
                heatmap.append(sentiment.dict())
            else:
                # Return neutral if no sentiment data
                heatmap.append({
                    "symbol": symbol,
                    "avg_sentiment": 0,
                    "heatmap_label": "Neutral",
                    "positive_count": 0,
                    "negative_count": 0,
                    "neutral_count": 0,
                    "total_articles": 0,
                    "date": None
                })

        return sorted(heatmap, key=lambda x: x['avg_sentiment'] if x['avg_sentiment'] else 0, reverse=True)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/asset/{symbol}')
async def get_asset_detail(symbol: str, range_: str = '1mo'):
    """Get asset detail like Google Finance (price, metrics, chart, sentiment)"""
    try:
        db = get_database()

        # Get current price
        price = await get_market_price(symbol)

        # Get historical data for chart
        try:
            hist_data = await get_historical_data(symbol, range_)
        except:
            hist_data = None

        # Get sentiment
        sentiment = await get_asset_sentiment(db, symbol)

        # Calculate price change from the returned chart payload.
        price_change = 0
        price_change_pct = 0
        points = []
        if isinstance(hist_data, dict):
            points = hist_data.get('points') or []

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
            market_cap=None,  # Optional: fetch from yfinance
            pe_ratio=None,
            dividend_yield=None,
            volume=None,
            avg_volume=None,
            week_52_high=None,
            week_52_low=None,
            description=None,
            sentiment=sentiment.dict() if sentiment else None,
            historical_data=hist_data
        ).dict()

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post('/sentiment-record')
async def record_market_sentiment(symbol: str, avg_sentiment: float, positive: int, negative: int, neutral: int, user=Depends(get_current_user)):
    """
    Record market sentiment for an asset (called by ML pipeline after analyzing news)
    avg_sentiment: -1 (very bearish) to +1 (very bullish)
    """
    try:
        db = get_database()
        owner = user.get('sub')
        if not owner:
            raise HTTPException(status_code=400, detail='Invalid user')

        # Validate sentiment range
        if not (-1 <= avg_sentiment <= 1):
            raise HTTPException(status_code=400, detail='avg_sentiment must be between -1 and 1')

        # Map sentiment to label
        if avg_sentiment >= 0.5:
            label = 'Very Bullish'
        elif avg_sentiment >= 0.1:
            label = 'Bullish'
        elif avg_sentiment > -0.1:
            label = 'Neutral'
        elif avg_sentiment >= -0.5:
            label = 'Bearish'
        else:
            label = 'Very Bearish'

        total_articles = positive + negative + neutral

        # Upsert sentiment record
        await db.execute(
            """
            INSERT INTO market_sentiment (symbol, sentiment_date, avg_sentiment, positive_count, negative_count, neutral_count, total_articles, heatmap_label)
            VALUES (:symbol, :date, :sentiment, :positive, :negative, :neutral, :total, :label)
            ON CONFLICT (symbol, sentiment_date) DO UPDATE SET
                avg_sentiment = EXCLUDED.avg_sentiment,
                positive_count = EXCLUDED.positive_count,
                negative_count = EXCLUDED.negative_count,
                neutral_count = EXCLUDED.neutral_count,
                total_articles = EXCLUDED.total_articles,
                heatmap_label = EXCLUDED.heatmap_label
            """,
            {
                "symbol": symbol,
                "date": date.today(),
                "sentiment": avg_sentiment,
                "positive": positive,
                "negative": negative,
                "neutral": neutral,
                "total": total_articles,
                "label": label
            }
        )

        return {"status": "recorded", "symbol": symbol, "date": str(date.today()), "label": label}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

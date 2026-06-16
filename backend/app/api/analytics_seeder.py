"""
Feature #5: Test Data Seeder
Helper script to populate sample trading history and sentiment data for dashboard testing.
Call via: POST /api/analytics/seed-test-data
"""
from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timedelta, date
import json

from app.core.db import get_database
from app.api.auth import get_current_user

router = APIRouter()


@router.post('/seed-test-data')
async def seed_test_data(user=Depends(get_current_user)):
    """
    Populate sample trading history and sentiment data for the current user's portfolio.
    Useful for testing the analytics dashboard without executing real trades.
    """
    try:
        db = get_database()
        owner = user.get('sub')
        if not owner:
            raise HTTPException(status_code=400, detail='Invalid user')

        # Get or create portfolio
        portfolio = await db.fetch_one(
            "SELECT id FROM portfolios WHERE owner = :owner",
            {"owner": owner}
        )

        if not portfolio:
            raise HTTPException(status_code=404, detail='Portfolio not found. Create one first.')

        portfolio_id = portfolio['id']

        # Clear existing test data (optional: comment out to preserve)
        # await db.execute("DELETE FROM trading_history WHERE portfolio_id = :id", {"id": portfolio_id})
        # await db.execute("DELETE FROM market_sentiment WHERE symbol IN ('AAPL', 'GOOGL', 'MSFT', 'TSLA')")

        # Sample transaction data (last 30 days)
        sample_trades = [
            {'symbol': 'AAPL', 'trade_type': 'BUY', 'quantity': 10, 'price': 150.00, 'days_ago': 25},
            {'symbol': 'AAPL', 'trade_type': 'SELL', 'quantity': 5, 'price': 155.00, 'days_ago': 20},
            {'symbol': 'GOOGL', 'trade_type': 'BUY', 'quantity': 5, 'price': 140.00, 'days_ago': 22},
            {'symbol': 'MSFT', 'trade_type': 'BUY', 'quantity': 8, 'price': 380.00, 'days_ago': 18},
            {'symbol': 'TSLA', 'trade_type': 'BUY', 'quantity': 3, 'price': 250.00, 'days_ago': 15},
            {'symbol': 'TSLA', 'trade_type': 'SELL', 'quantity': 3, 'price': 255.00, 'days_ago': 10},
            {'symbol': 'AAPL', 'trade_type': 'BUY', 'quantity': 5, 'price': 160.00, 'days_ago': 8},
            {'symbol': 'GOOGL', 'trade_type': 'SELL', 'quantity': 5, 'price': 145.00, 'days_ago': 5},
        ]

        transaction_count = 0
        for trade in sample_trades:
            notional = trade['quantity'] * trade['price']
            fee = notional * 0.001
            pl = None

            # Calculate P/L for SELL trades
            if trade['trade_type'] == 'SELL':
                # Find matching BUY trade (simplified)
                buy_price = trade['price'] - 5  # Assume 5% profit
                pl = (trade['price'] - buy_price) * trade['quantity']

            created_at = datetime.now() - timedelta(days=trade['days_ago'])

            await db.execute(
                """
                INSERT INTO trading_history (portfolio_id, symbol, trade_type, quantity, price, notional, fee, pl, signal_source, notes)
                VALUES (:portfolio_id, :symbol, :trade_type, :quantity, :price, :notional, :fee, :pl, :signal_source, :notes)
                """,
                {
                    'portfolio_id': portfolio_id,
                    'symbol': trade['symbol'],
                    'trade_type': trade['trade_type'],
                    'quantity': trade['quantity'],
                    'price': trade['price'],
                    'notional': notional,
                    'fee': fee,
                    'pl': pl,
                    'signal_source': 'test-seed',
                    'notes': f"Test data: {trade['trade_type']} {trade['quantity']} @ {trade['price']}"
                }
            )
            transaction_count += 1

        # Sample sentiment data (last 7 days)
        sample_sentiments = [
            {'symbol': 'AAPL', 'sentiment': 0.65, 'pos': 12, 'neg': 2, 'neut': 6},
            {'symbol': 'GOOGL', 'sentiment': 0.45, 'pos': 8, 'neg': 4, 'neut': 8},
            {'symbol': 'MSFT', 'sentiment': 0.55, 'pos': 10, 'neg': 3, 'neut': 7},
            {'symbol': 'TSLA', 'sentiment': -0.35, 'pos': 3, 'neg': 10, 'neut': 7},
            {'symbol': 'META', 'sentiment': 0.25, 'pos': 5, 'neg': 4, 'neut': 11},
            {'symbol': 'NVDA', 'sentiment': 0.75, 'pos': 14, 'neg': 1, 'neut': 5},
        ]

        sentiment_count = 0
        for sentiment in sample_sentiments:
            avg_sentiment = sentiment['sentiment']
            pos = sentiment['pos']
            neg = sentiment['neg']
            neut = sentiment['neut']
            total = pos + neg + neut

            # Map to label
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

            await db.execute(
                """
                INSERT INTO market_sentiment (symbol, sentiment_date, avg_sentiment, positive_count, negative_count, neutral_count, total_articles, heatmap_label)
                VALUES (:symbol, :date, :sentiment, :pos, :neg, :neut, :total, :label)
                ON CONFLICT (symbol, sentiment_date) DO UPDATE SET
                    avg_sentiment = EXCLUDED.avg_sentiment,
                    positive_count = EXCLUDED.positive_count,
                    negative_count = EXCLUDED.negative_count,
                    neutral_count = EXCLUDED.neutral_count,
                    total_articles = EXCLUDED.total_articles,
                    heatmap_label = EXCLUDED.heatmap_label
                """,
                {
                    'symbol': sentiment['symbol'],
                    'date': date.today(),
                    'sentiment': avg_sentiment,
                    'pos': pos,
                    'neg': neg,
                    'neut': neut,
                    'total': total,
                    'label': label
                }
            )
            sentiment_count += 1

        # Generate performance metrics snapshot
        portfolio_obj = await db.fetch_one(
            "SELECT cash_balance FROM portfolios WHERE id = :id",
            {"id": portfolio_id}
        )

        total_value = float(portfolio_obj['cash_balance']) + (50000 * 1.15)  # Assume some positions
        positions_value = total_value - float(portfolio_obj['cash_balance'])
        total_pl = 5250.0  # Assume some profit

        await db.execute(
            """
            INSERT INTO performance_metrics (portfolio_id, metric_date, total_value, cash_balance, positions_value, total_pl, realized_pl, winning_trades, total_trades, win_rate, daily_return)
            VALUES (:portfolio_id, :date, :total_value, :cash_balance, :positions_value, :total_pl, :realized_pl, :winning_trades, :total_trades, :win_rate, :daily_return)
            ON CONFLICT (portfolio_id, metric_date) DO UPDATE SET
                total_value = EXCLUDED.total_value,
                positions_value = EXCLUDED.positions_value,
                total_pl = EXCLUDED.total_pl
            """,
            {
                'portfolio_id': portfolio_id,
                'date': date.today(),
                'total_value': total_value,
                'cash_balance': float(portfolio_obj['cash_balance']),
                'positions_value': positions_value,
                'total_pl': total_pl,
                'realized_pl': 500.0,
                'winning_trades': 5,
                'total_trades': 8,
                'win_rate': 62.5,
                'daily_return': 2.35
            }
        )

        return {
            'status': 'success',
            'transactions_created': transaction_count,
            'sentiments_created': sentiment_count,
            'metrics_updated': 1,
            'message': f'Seeded {transaction_count} transactions, {sentiment_count} sentiments, and metrics'
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

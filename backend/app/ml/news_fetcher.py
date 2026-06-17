from typing import List, Dict, Any
from datetime import datetime, timedelta

import httpx
import yfinance as yf


def fetch_news_for_symbol(api_key: str, symbol: str, from_dt: datetime = None, to_dt: datetime = None, page_size: int = 100) -> List[Dict[str, Any]]:
    if to_dt is None:
        to_dt = datetime.utcnow()
    if from_dt is None:
        from_dt = to_dt - timedelta(days=7)
    q = f"{symbol} OR {symbol} stock OR {symbol} company"
    articles = []
    try:
        response = httpx.get(
            'https://newsapi.org/v2/everything',
            params={
                'q': q,
                'from': from_dt.isoformat(),
                'to': to_dt.isoformat(),
                'language': 'en',
                'pageSize': page_size,
                'sortBy': 'publishedAt',
                'apiKey': api_key,
            },
            timeout=20.0,
        )
        response.raise_for_status()
        res = response.json()
        articles = res.get('articles', []) if isinstance(res, dict) else []
    except Exception:
        # best-effort: return empty list on failure
        articles = []
    return articles


def fetch_ohlcv(symbol: str, period: str = '30d', interval: str = '1d') -> Any:
    # uses yfinance to fetch historical OHLCV
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=False)
    return df

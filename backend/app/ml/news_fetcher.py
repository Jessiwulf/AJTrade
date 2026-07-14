import re
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

import httpx
import yfinance as yf


def _normalize_company_name(company_name: Optional[str]) -> Optional[str]:
    if not company_name:
        return None
    normalized = re.sub(r'\s+', ' ', str(company_name).strip())
    normalized = re.sub(r'\s+[(-].*$', '', normalized).strip()
    return normalized or None


def _build_news_query(symbol: str, company_name: Optional[str]) -> str:
    normalized_symbol = str(symbol or '').strip().upper()
    normalized_company_name = _normalize_company_name(company_name)

    # Use company name if available for better news matching, else symbol
    if normalized_company_name and normalized_company_name.upper() != normalized_symbol:
        return normalized_company_name
    return normalized_symbol


def fetch_news_for_symbol(
    api_key: str,
    symbol: str,
    from_dt: datetime = None,
    to_dt: datetime = None,
    page_size: int = 100,
    page: int = 1,
    company_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if to_dt is None:
        to_dt = datetime.utcnow()
    if from_dt is None:
        from_dt = to_dt - timedelta(days=7)
    q = _build_news_query(symbol, company_name)
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
                'page': max(int(page or 1), 1),
                'sortBy': 'publishedAt',
                'searchIn': 'title,description',
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

    deduped_articles: List[Dict[str, Any]] = []
    seen = set()
    for article in articles:
        key = str(article.get('url') or article.get('title') or '').strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped_articles.append(article)
    return deduped_articles


def fetch_ohlcv(symbol: str, period: str = '30d', interval: str = '1d') -> Any:
    # uses yfinance to fetch historical OHLCV
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval, auto_adjust=False)
    return df

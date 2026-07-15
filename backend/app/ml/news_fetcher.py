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


def _fetch_newsapi_articles(
    api_key: str,
    q: str,
    from_dt: datetime,
    to_dt: datetime,
    page_size: int,
    page: int,
) -> List[Dict[str, Any]]:
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
    return res.get('articles', []) if isinstance(res, dict) else []


def _fetch_newsdata_articles(
    api_key: str,
    q: str,
    from_dt: datetime,
    to_dt: datetime,
    page_size: int,
    page: int,
) -> List[Dict[str, Any]]:
    # NewsData free tier doesn't support date filtering - use only basic params
    params = {
        'apikey': api_key,
        'q': q,
        'language': 'en',
        'size': min(max(int(page_size or 1), 1), 10),  # Free tier max is 10
    }
    
    response = httpx.get(
        'https://newsdata.io/api/1/latest',
        params=params,
        timeout=20.0,
    )
    response.raise_for_status()
    res = response.json()
    results = res.get('results', []) if isinstance(res, dict) else []

    normalized = []
    for article in results:
        normalized.append(
            {
                'title': article.get('title') or 'Untitled article',
                'description': article.get('description') or article.get('content') or '',
                'content': article.get('content') or '',
                'url': article.get('link'),
                'publishedAt': article.get('pubDate'),
                'source': {
                    'name': article.get('source_name') or article.get('source_id') or 'Unknown source',
                },
            }
        )

    return normalized


def fetch_news_for_symbol(
    api_key: str,
    symbol: str,
    from_dt: datetime = None,
    to_dt: datetime = None,
    page_size: int = 100,
    page: int = 1,
    company_name: Optional[str] = None,
    provider: str = 'newsapi',
    fallback_api_key: Optional[str] = None,
    fallback_provider: Optional[str] = None,
) -> List[Dict[str, Any]]:
    if to_dt is None:
        to_dt = datetime.utcnow()
    if from_dt is None:
        from_dt = to_dt - timedelta(days=7)
    q = _build_news_query(symbol, company_name)
    articles = []

    def _fetch(provider_name: str, key_value: str) -> List[Dict[str, Any]]:
        normalized_provider = str(provider_name or '').strip().lower()
        if normalized_provider == 'newsdata':
            return _fetch_newsdata_articles(key_value, q, from_dt, to_dt, page_size, page)
        return _fetch_newsapi_articles(key_value, q, from_dt, to_dt, page_size, page)

    try:
        articles = _fetch(provider, api_key)
    except Exception:
        articles = []

    if not articles and fallback_api_key:
        try:
            articles = _fetch(fallback_provider or 'newsdata', fallback_api_key)
        except Exception:
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

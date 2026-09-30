import logging
import os
import re
import threading
import time
from typing import List, Dict, Any, Optional
from datetime import datetime, timedelta

import httpx
import yfinance as yf

logger = logging.getLogger('ajtrade.news')

# NewsAPI's free (Developer) plan only searches about the last month; asking for more fails the whole request.
try:
    NEWSAPI_MAX_LOOKBACK_DAYS = max(int(os.environ.get('NEWSAPI_MAX_LOOKBACK_DAYS', '29')), 1)
except ValueError:
    NEWSAPI_MAX_LOOKBACK_DAYS = 29

# After a quota / key error, stop calling that provider for a while instead of burning more requests.
_PROVIDER_PAUSE_SECONDS = {'rateLimited': 60 * 60, 'apiKeyExhausted': 60 * 60, 'http_429': 60 * 60,
                           'apiKeyInvalid': 30 * 60, 'apiKeyDisabled': 30 * 60, 'apiKeyMissing': 30 * 60,
                           'http_401': 30 * 60}
_ERROR_LOG_INTERVAL_SECONDS = 30 * 60
_status_lock = threading.Lock()
_provider_paused_until: Dict[str, float] = {}
_last_logged: Dict[tuple, float] = {}
# provider -> {"code", "message", "at"}; read by the API to explain empty news.
_last_errors: Dict[str, Dict[str, Any]] = {}


class NewsProviderError(Exception):
    def __init__(self, provider: str, code: str, message: str):
        super().__init__(f'{provider} {code}: {message}')
        self.provider = provider
        self.code = code
        self.message = message


def _record_error(error: NewsProviderError) -> None:
    now = time.monotonic()
    with _status_lock:
        _last_errors[error.provider] = {'code': error.code, 'message': error.message, 'at': datetime.utcnow().isoformat()}
        pause = _PROVIDER_PAUSE_SECONDS.get(error.code)
        if pause:
            _provider_paused_until[error.provider] = now + pause
        log_key = (error.provider, error.code)
        should_log = now - _last_logged.get(log_key, -1e9) >= _ERROR_LOG_INTERVAL_SECONDS
        if should_log:
            _last_logged[log_key] = now
    if should_log:
        logger.warning(
            'news_provider_failed provider=%s code=%s message=%s%s',
            error.provider, error.code, error.message[:200],
            f' (pausing this provider for {pause // 60} min)' if pause else '',
        )


def _is_paused(provider: str) -> bool:
    with _status_lock:
        return time.monotonic() < _provider_paused_until.get(provider, 0.0)


def news_provider_status() -> Dict[str, Dict[str, Any]]:
    """Latest error per provider and whether it is paused, e.g. {"newsapi": {"code": "rateLimited", ...}}."""
    now = time.monotonic()
    with _status_lock:
        status = {}
        for provider, error in _last_errors.items():
            paused_for = max(0.0, _provider_paused_until.get(provider, 0.0) - now)
            status[provider] = {**error, 'paused_minutes': round(paused_for / 60) if paused_for else 0}
        return status


def _clear_error(provider: str) -> None:
    with _status_lock:
        _last_errors.pop(provider, None)
        _provider_paused_until.pop(provider, None)


def _normalize_company_name(company_name: Optional[str]) -> Optional[str]:
    if not company_name:
        return None
    normalized = re.sub(r'\s+', ' ', str(company_name).strip())
    normalized = re.sub(r'\s+[(-].*$', '', normalized).strip()
    return normalized or None


# Coin names for news search (the insight builder often has no display name, and "SOL"/"LINK" alone match noise).
_CRYPTO_NAMES = {
    'AAVE': 'Aave', 'AVAX': 'Avalanche', 'BAT': 'Basic Attention Token', 'BCH': 'Bitcoin Cash', 'BTC': 'Bitcoin',
    'CRV': 'Curve DAO', 'DOGE': 'Dogecoin', 'DOT': 'Polkadot', 'ETH': 'Ethereum', 'GRT': 'The Graph',
    'LINK': 'Chainlink', 'LTC': 'Litecoin', 'MKR': 'Maker', 'PEPE': 'Pepe coin', 'SHIB': 'Shiba Inu',
    'SOL': 'Solana', 'SUSHI': 'SushiSwap', 'TRUMP': 'TRUMP memecoin', 'UNI': 'Uniswap', 'XRP': 'XRP',
    'XTZ': 'Tezos', 'YFI': 'yearn.finance',
}


# Index ETFs are written about by index name, not fund name ("SPDR S&P 500 ETF Trust" finds nothing).
_ETF_TOPICS = {'SPY': 'S&P 500', 'VOO': 'S&P 500', 'IVV': 'S&P 500', 'QQQ': 'Nasdaq', 'DIA': 'Dow Jones',
               'IWM': 'Russell 2000', 'GLD': 'gold price', 'SLV': 'silver price'}
_NAME_SUFFIX_RE = re.compile(
    r'[,.]?\s+(inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings|group|class [a-c]|'
    r'etf|trust|fund|n\.?v|s\.?a|ag|se)\.?$',
    re.IGNORECASE,
)


def _clean_company_name(name: str) -> str:
    previous = None
    while name and name != previous:
        previous = name
        name = _NAME_SUFFIX_RE.sub('', name).strip(' ,.')
    return name


_UNAMBIGUOUS_COINS = {'BTC', 'ETH', 'DOGE', 'XRP', 'LTC', 'BCH', 'SHIB'}


def _build_news_query(symbol: str, company_name: Optional[str]) -> str:
    normalized_symbol = str(symbol or '').strip().upper()
    normalized_company_name = _normalize_company_name(company_name)

    # Crypto pairs ("SOL-USD", Yahoo name "Solana USD"): search for the coin name, not the pair.
    crypto_match = re.match(r'^([A-Z0-9]+)-USD$', normalized_symbol)
    if crypto_match:
        base = crypto_match.group(1)
        coin_name = re.sub(r'\s+USD$', '', normalized_company_name or '', flags=re.IGNORECASE).strip()
        if coin_name.upper() in {base, normalized_symbol}:
            coin_name = ''
        name = _CRYPTO_NAMES.get(base) or coin_name or base
        if base in _UNAMBIGUOUS_COINS:
            return name
        # "Solana", "Avalanche", "Maker" are also products and words: require crypto context.
        return f'{name} AND (crypto OR cryptocurrency OR token OR blockchain)'

    if normalized_symbol in _ETF_TOPICS:
        return _ETF_TOPICS[normalized_symbol]

    # Use company name if available for better news matching, else symbol
    cleaned = _clean_company_name(normalized_company_name or '')
    if cleaned and cleaned.upper() != normalized_symbol:
        return cleaned
    return normalized_symbol


def _fetch_newsapi_articles(
    api_key: str,
    q: str,
    from_dt: datetime,
    to_dt: datetime,
    page_size: int,
    page: int,
) -> List[Dict[str, Any]]:
    earliest = datetime.utcnow() - timedelta(days=NEWSAPI_MAX_LOOKBACK_DAYS)
    if to_dt < earliest:
        return []  # entirely outside what the plan can search
    from_dt = max(from_dt, earliest)

    for attempt in range(2):
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
        try:
            res = response.json()
        except ValueError:
            res = {}
        if response.status_code == 200 and isinstance(res, dict) and res.get('status') == 'ok':
            return res.get('articles', []) or []

        code = str((res or {}).get('code') or f'http_{response.status_code}')
        message = str((res or {}).get('message') or response.text[:200])
        # "Your plan permits you to request articles as far back as 2026-08-28": retry from that date.
        allowed = re.search(r'as far back as (\d{4}-\d{2}-\d{2})', message)
        if attempt == 0 and code == 'parameterInvalid' and allowed:
            from_dt = datetime.fromisoformat(allowed.group(1)) + timedelta(days=1)
            continue
        raise NewsProviderError('newsapi', code, message)
    return []


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
    try:
        res = response.json()
    except ValueError:
        res = {}
    if response.status_code != 200 or not isinstance(res, dict) or res.get('status') != 'success':
        results_info = (res or {}).get('results') if isinstance(res, dict) else None
        info = results_info if isinstance(results_info, dict) else {}
        code = str(info.get('code') or f'http_{response.status_code}')
        if response.status_code == 429:
            code = 'http_429'
        raise NewsProviderError('newsdata', code, str(info.get('message') or response.text[:200]))
    results = res.get('results', []) or []

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

    def _try(provider_name: str, key_value: str) -> List[Dict[str, Any]]:
        name = str(provider_name or 'newsapi').strip().lower()
        if _is_paused(name):
            return []
        try:
            found = _fetch(name, key_value)
        except NewsProviderError as exc:
            _record_error(exc)
            return []
        except Exception as exc:
            _record_error(NewsProviderError(name, type(exc).__name__, str(exc)))
            return []
        _clear_error(name)
        return found

    articles = _try(provider, api_key)
    if not articles and fallback_api_key:
        articles = _try(fallback_provider or 'newsdata', fallback_api_key)

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

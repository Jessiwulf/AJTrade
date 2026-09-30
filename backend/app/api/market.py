from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool

import httpx
import logging
import os
import threading
import time

router = APIRouter()
logger = logging.getLogger(__name__)

_RANGE_MAP = {
    'day': ('1d', '5m'),
    'month': ('1mo', '1d'),
    'year': ('1y', '1d'),
    'all': ('max', '1wk'),
}


def _normalize_symbol(symbol: str) -> str:
    return (symbol or '').strip().upper()


def _normalize_range(value: str) -> str:
    value = (value or '').strip().lower()
    if value in {'1d', 'day'}:
        return 'day'
    if value in {'1mo', 'month'}:
        return 'month'
    if value in {'1y', 'year'}:
        return 'year'
    return 'all'


def _make_point(index, row) -> dict:
    ts = index.to_pydatetime().isoformat()
    return {
        't': ts,
        'open': float(row.get('Open')) if row.get('Open') is not None else None,
        'high': float(row.get('High')) if row.get('High') is not None else None,
        'low': float(row.get('Low')) if row.get('Low') is not None else None,
        'close': float(row.get('Close')) if row.get('Close') is not None else None,
        'volume': float(row.get('Volume')) if row.get('Volume') is not None else None,
    }


def _epoch_to_iso(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


_YAHOO_HEADERS = {
    'accept': 'application/json,text/plain,*/*',
    'accept-language': 'en-US,en;q=0.9',
    'user-agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/126.0.0.0 Safari/537.36'
    ),
}
_YAHOO_SESSION_TTL_S = 30 * 60
_FUNDAMENTALS_TTL_S = 5 * 60
_FUNDAMENTALS_FAILURE_TTL_S = 60

# Yahoo's quoteSummary endpoint requires a consent cookie plus a matching "crumb" token.
# Both are cached process-wide and refreshed on expiry or when Yahoo rejects them.
_yahoo_lock = threading.Lock()
_yahoo_client: Optional[httpx.Client] = None
_yahoo_crumb: Optional[str] = None
_yahoo_crumb_expires = 0.0
_fundamentals_cache: dict = {}


def _yahoo_session(force_refresh: bool = False):
    global _yahoo_client, _yahoo_crumb, _yahoo_crumb_expires
    with _yahoo_lock:
        now = time.monotonic()
        if _yahoo_client is None:
            _yahoo_client = httpx.Client(timeout=15.0, headers=_YAHOO_HEADERS, follow_redirects=True)
        if not force_refresh and _yahoo_crumb and now < _yahoo_crumb_expires:
            return _yahoo_client, _yahoo_crumb

        _yahoo_client.cookies.clear()
        try:
            # Sets the A3 cookie; this host answers 404, which is expected.
            _yahoo_client.get('https://fc.yahoo.com')
        except httpx.HTTPError:
            pass
        response = _yahoo_client.get('https://query2.finance.yahoo.com/v1/test/getcrumb')
        crumb = response.text.strip()
        if response.status_code != 200 or not crumb or '<' in crumb or ' ' in crumb:
            _yahoo_crumb = None
            raise RuntimeError(f'yahoo_crumb_unavailable: HTTP {response.status_code}')
        _yahoo_crumb = crumb
        _yahoo_crumb_expires = now + _YAHOO_SESSION_TTL_S
        return _yahoo_client, _yahoo_crumb


def _raw_or_none(node):
    if isinstance(node, dict):
        raw = node.get('raw')
        return raw if raw is not None else node.get('fmt')
    return node


def _request_quote_summary(symbol: str) -> Optional[dict]:
    """Return the quoteSummary result for symbol, or None when Yahoo has no such symbol."""
    params = {'modules': 'price,summaryDetail,defaultKeyStatistics,financialData'}
    for attempt in range(2):
        client, crumb = _yahoo_session(force_refresh=attempt > 0)
        response = client.get(
            f'https://query2.finance.yahoo.com/v10/finance/quoteSummary/{symbol}',
            params={**params, 'crumb': crumb},
        )
        if response.status_code in (401, 403) and attempt == 0:
            continue  # stale cookie/crumb: refresh once and retry
        if response.status_code == 404:
            return None
        response.raise_for_status()
        payload = response.json() or {}
        return (((payload.get('quoteSummary') or {}).get('result') or [None])[0]) or None
    return None


def _fetch_yahoo_fundamentals(symbol: str) -> dict:
    now = time.monotonic()
    cached = _fundamentals_cache.get(symbol)
    if cached and now < cached[0]:
        if isinstance(cached[1], Exception):
            raise cached[1]
        return cached[1]

    try:
        result = _request_quote_summary(symbol)
    except Exception as exc:
        # Remember failures briefly so polling clients don't hammer a rate-limited upstream.
        _fundamentals_cache[symbol] = (now + _FUNDAMENTALS_FAILURE_TTL_S, exc)
        raise

    data = {}
    if result:
        price = result.get('price') or {}
        summary = result.get('summaryDetail') or {}
        stats = result.get('defaultKeyStatistics') or {}
        financial = result.get('financialData') or {}
        data = {
            'currency': price.get('currency') or summary.get('currency'),
            'market_cap': _raw_or_none(price.get('marketCap')) or _raw_or_none(summary.get('marketCap')),
            'latest_price': _raw_or_none(price.get('regularMarketPrice')) or _raw_or_none(financial.get('currentPrice')),
            'previous_close': _raw_or_none(price.get('regularMarketPreviousClose')) or _raw_or_none(summary.get('previousClose')),
            'open': _raw_or_none(price.get('regularMarketOpen')) or _raw_or_none(summary.get('open')),
            'day_low': _raw_or_none(price.get('regularMarketDayLow')) or _raw_or_none(summary.get('dayLow')),
            'day_high': _raw_or_none(price.get('regularMarketDayHigh')) or _raw_or_none(summary.get('dayHigh')),
            'volume': _raw_or_none(price.get('regularMarketVolume')) or _raw_or_none(summary.get('volume')),
            'avg_volume': _raw_or_none(summary.get('averageVolume')),
            'pe_ratio': _raw_or_none(summary.get('trailingPE')) or _raw_or_none(stats.get('trailingPE')),
            'dividend_yield': _raw_or_none(summary.get('dividendYield')),
            'week_52_high': _raw_or_none(summary.get('fiftyTwoWeekHigh')),
            'week_52_low': _raw_or_none(summary.get('fiftyTwoWeekLow')),
            'revenue': _raw_or_none(financial.get('totalRevenue')),
            'net_income': _raw_or_none(stats.get('netIncomeToCommon')) or _raw_or_none(financial.get('netIncomeToCommon')),
            'eps': _raw_or_none(stats.get('trailingEps')) or _raw_or_none(financial.get('trailingEps')),
            'beta': _raw_or_none(summary.get('beta')) or _raw_or_none(stats.get('beta')),
        }

    _fundamentals_cache[symbol] = (now + _FUNDAMENTALS_TTL_S, data)
    return data


def _fetch_fundamentals_with_variants(symbol: str) -> dict:
    # Mirror the chart lookup: raw symbol first, then the Thai (.BK) listing.
    variants = [symbol]
    if symbol and symbol.isalpha() and len(symbol) <= 6:
        variants.append(f'{symbol}.BK')
    last_exc = None
    for variant in variants:
        try:
            data = _fetch_yahoo_fundamentals(variant)
        except Exception as exc:
            last_exc = exc
            continue
        if data:
            return data
    if last_exc:
        raise last_exc
    return {}


def _fetch_yahoo_chart(symbol: str, range_name: str) -> dict:
    period, interval = _RANGE_MAP[range_name]
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
    params = {
        'range': period,
        'interval': interval,
        'includePrePost': 'false',
        'events': 'div,splits',
        'corsDomain': 'finance.yahoo.com',
    }
    headers = {
        'accept': 'application/json,text/plain,*/*',
        'accept-language': 'en-US,en;q=0.9',
        'user-agent': (
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/126.0.0.0 Safari/537.36'
        ),
    }

    with httpx.Client(timeout=15.0, headers=headers, follow_redirects=True) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        payload = response.json()

    chart = (payload or {}).get('chart') or {}
    results = chart.get('result') or []
    if not results:
        error = (chart.get('error') or {}).get('description') or 'No price data found'
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'{error} for {symbol}')

    result = results[0] or {}
    timestamps = result.get('timestamp') or []
    indicators = result.get('indicators') or {}
    quotes = indicators.get('quote') or [{}]
    quote = quotes[0] or {}
    meta = result.get('meta') or {}
    fundamentals = {}
    try:
        fundamentals = _fetch_yahoo_fundamentals(symbol)
    except Exception:
        fundamentals = {}

    points = []
    for idx, ts in enumerate(timestamps):
        point = {
            't': _epoch_to_iso(ts),
            'open': None,
            'high': None,
            'low': None,
            'close': None,
            'volume': None,
        }
        for field in ('open', 'high', 'low', 'close', 'volume'):
            series = quote.get(field) or []
            value = series[idx] if idx < len(series) else None
            if value is not None:
                point[field] = float(value)
        points.append(point)

    closes = [point['close'] for point in points if point.get('close') is not None]
    latest = meta.get('regularMarketPrice')
    if latest is None and closes:
        latest = closes[-1]
    previous = meta.get('regularMarketPreviousClose')
    if previous is None and len(closes) >= 2:
        previous = closes[-2]
    change = (latest - previous) if latest is not None and previous is not None else None
    change_pct = (change / previous * 100.0) if change is not None and previous not in (None, 0) else None

    return {
        'symbol': symbol,
        'period': range_name,
        'points': points,
        'quote': {
            'price': float(latest) if latest is not None else None,
            'previous_close': float(previous) if previous is not None else None,
            'change': float(change) if change is not None else None,
            'change_percent': float(change_pct) if change_pct is not None else None,
            'currency': meta.get('currency'),
            'market_cap': fundamentals.get('market_cap') or meta.get('marketCap'),
            'short_name': meta.get('shortName'),
            'long_name': meta.get('longName'),
            'display_name': meta.get('shortName') or meta.get('longName') or symbol,
            'exchange_name': meta.get('exchangeName'),
            'volume': fundamentals.get('volume') or meta.get('regularMarketVolume'),
            'avg_volume': fundamentals.get('avg_volume') or meta.get('averageDailyVolume3Month'),
            'pe_ratio': fundamentals.get('pe_ratio') or meta.get('trailingPE'),
            'dividend_yield': fundamentals.get('dividend_yield'),
            'week_52_high': fundamentals.get('week_52_high') or meta.get('fiftyTwoWeekHigh'),
            'week_52_low': fundamentals.get('week_52_low') or meta.get('fiftyTwoWeekLow'),
        },
    }


def _fetch_yahoo_chart_with_variants(symbol: str, range_name: str) -> dict:
    # Try the raw symbol first, then common Yahoo variants (Thai market: .BK suffix)
    variants = [symbol]
    if symbol and symbol.isalpha() and '.' not in symbol and len(symbol) <= 6:
        variants.append(f"{symbol}.BK")

    last_exc = None
    for s in variants:
        try:
            return _fetch_yahoo_chart(s, range_name)
        except HTTPException as e:
            last_exc = e
            # try next variant
            continue
        except httpx.HTTPStatusError as e:
            # Yahoo answers an unknown symbol (e.g. "PTT" instead of "PTT.BK") with a raw 404.
            if e.response.status_code != 404:
                raise
            last_exc = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'No price data found for {s}')
            continue
    # re-raise the last HTTP exception if all variants failed
    if last_exc:
        raise last_exc
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'No price data found for {symbol}')


def _is_thai_symbol(symbol: str) -> bool:
    # crude heuristic: all letters and short
    return bool(symbol and symbol.isalpha() and len(symbol) <= 6)


def _try_settrade_quote(symbol: str) -> Optional[dict]:
    """Try Settrade Open API if configured via env vars. Returns quote dict or None."""
    base = os.environ.get('SETTRADE_BASE_URL')
    api_key = os.environ.get('SETTRADE_API_KEY')
    if not base:
        return None

    headers = {}
    if api_key:
        headers['x-api-key'] = api_key

    candidates = [
        f"{base.rstrip('/')}/openapi/market/quote",
        f"{base.rstrip('/')}/openapi/market/quotes",
        f"{base.rstrip('/')}/openapi/quote/{symbol}",
        f"{base.rstrip('/')}/openapi/quote",
    ]
    params = {'symbol': symbol}
    try:
        with httpx.Client(timeout=10.0, headers=headers) as client:
            for url in candidates:
                try:
                    r = client.get(url, params=params)
                except Exception:
                    continue
                if r.status_code != 200:
                    continue
                try:
                    data = r.json()
                except Exception:
                    continue
                # try to normalize common shapes
                if isinstance(data, dict):
                    # example: {'price':..., 'previous_close':...}
                    if data.get('price') is not None:
                        return data
                    # nested payload
                    for k in ('data', 'result', 'quote'):
                        v = data.get(k)
                        if isinstance(v, dict) and v.get('price') is not None:
                            return v
    except Exception:
        return None
    return None


def _try_alpaca_quote(symbol: str) -> Optional[dict]:
    key = os.environ.get('ALPACA_KEY_ID')
    secret = os.environ.get('ALPACA_SECRET_KEY')
    base = (os.environ.get('ALPACA_BASE_URL') or 'https://data.alpaca.markets').rstrip('/')
    # Stock bars only; crypto pairs ("BTC-USD") are quoted from Yahoo.
    if not key or not secret or symbol.endswith('-USD'):
        return None
    # try Bars endpoint
    try:
        with httpx.Client(timeout=10.0, headers={'APCA-API-KEY-ID': key, 'APCA-API-SECRET-KEY': secret}) as client:
            # v2 bars endpoint
            url = f"{base}/v2/stocks/{symbol}/bars"
            r = client.get(url, params={'timeframe': '1Day', 'limit': 2})
            if r.status_code == 200:
                j = r.json()
                bars = j.get('bars') or []
                if bars:
                    last = bars[-1]
                    prev = bars[-2] if len(bars) >= 2 else None
                    price = last.get('c') or last.get('close')
                    prev_close = prev.get('c') if prev else None
                    change = (price - prev_close) if price is not None and prev_close is not None else None
                    change_pct = (change / prev_close * 100.0) if change is not None and prev_close not in (None, 0) else None
                    return {'symbol': symbol, 'price': price, 'previous_close': prev_close, 'change': change, 'change_percent': change_pct}
    except Exception:
        return None
    return None


def _fetch_history(symbol: str, range_name: str) -> dict:
    try:
        return _fetch_yahoo_chart(symbol, range_name)
    except HTTPException:
        raise
    except Exception:
        # Secondary best-effort path: fall back to yfinance in case the chart endpoint changes.
        import yfinance as yf

        period, interval = _RANGE_MAP[range_name]
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval, auto_adjust=False)
        if df is None or df.empty:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'No price data found for {symbol}')

        df = df.reset_index()
        time_col = df.columns[0]
        points = []
        for _, row in df.iterrows():
            points.append(_make_point(row[time_col], row))

        info = {}
        try:
            info = ticker.fast_info or {}
        except Exception:
            info = {}

        close_values = [p['close'] for p in points if p.get('close') is not None]
        latest = close_values[-1] if close_values else None
        previous = close_values[-2] if len(close_values) >= 2 else None
        change = (latest - previous) if latest is not None and previous is not None else None
        change_pct = (change / previous * 100.0) if change is not None and previous not in (None, 0) else None

        return {
            'symbol': symbol,
            'period': range_name,
            'points': points,
            'quote': {
                'price': latest,
                'previous_close': previous,
                'change': change,
                'change_percent': change_pct,
                'currency': info.get('currency') if isinstance(info, dict) else None,
                'market_cap': info.get('marketCap') if isinstance(info, dict) else None,
                'short_name': info.get('shortName') if isinstance(info, dict) else None,
                'long_name': info.get('longName') if isinstance(info, dict) else None,
                'display_name': (
                    info.get('shortName') if isinstance(info, dict) else None
                ) or (
                    info.get('longName') if isinstance(info, dict) else None
                ) or symbol,
                'volume': info.get('regularMarketVolume') if isinstance(info, dict) else None,
                'avg_volume': info.get('averageVolume') if isinstance(info, dict) else None,
                'pe_ratio': info.get('trailingPE') if isinstance(info, dict) else None,
                'dividend_yield': info.get('dividendYield') if isinstance(info, dict) else None,
                'week_52_high': info.get('fiftyTwoWeekHigh') if isinstance(info, dict) else None,
                'week_52_low': info.get('fiftyTwoWeekLow') if isinstance(info, dict) else None,
            },
        }


async def get_market_price(symbol: str):
    normalized = _normalize_symbol(symbol)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid symbol')

    chart = await run_in_threadpool(_fetch_yahoo_chart_with_variants, normalized, 'day')
    quote = chart.get('quote') or {}
    price = quote.get('price')
    if price is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'No price data found for {normalized}')
    return price


async def get_historical_data(symbol: str, range_: str = '1mo'):
    normalized = _normalize_symbol(symbol)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid symbol')

    range_name = _normalize_range(range_)
    return await run_in_threadpool(_fetch_history, normalized, range_name)


def _fetch_quotes(symbols: List[str]) -> List[dict]:
    out = []
    for raw_symbol in symbols:
        symbol = _normalize_symbol(raw_symbol)
        if not symbol:
            continue
        try:
            # Try Settrade (Thai) if configured
            if _is_thai_symbol(symbol):
                st = _try_settrade_quote(symbol)
                if st:
                    out.append({'symbol': symbol, **st})
                    continue

            # Try Alpaca if configured
            alp = _try_alpaca_quote(symbol)
            if alp:
                out.append({'symbol': symbol, **alp})
                continue

            chart = _fetch_yahoo_chart_with_variants(symbol, 'day')
            quote = chart.get('quote') or {}
            if quote.get('price') is None:
                out.append({'symbol': symbol, 'error': 'No price data found'})
                continue
            out.append({'symbol': symbol, **quote})
        except HTTPException:
            out.append({'symbol': symbol, 'error': 'No price data found'})
        except Exception:
            out.append({'symbol': symbol, 'error': 'No price data found'})
    return out


def _none_if_nan(value):
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip().upper().replace(',', '')
        if not text:
            return None
        multiplier = 1.0
        if text.endswith('%'):
            text = text[:-1]
        suffix_multipliers = {
            'K': 1_000.0,
            'M': 1_000_000.0,
            'B': 1_000_000_000.0,
            'T': 1_000_000_000_000.0,
        }
        last = text[-1:]
        if last in suffix_multipliers:
            multiplier = suffix_multipliers[last]
            text = text[:-1]
        try:
            number = float(text) * multiplier
        except (TypeError, ValueError):
            return None
        if number != number:
            return None
        return number

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _fetch_asset_statistics(symbol: str) -> dict:
    normalized = _normalize_symbol(symbol)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid symbol')

    fundamentals = {}
    try:
        fundamentals = _fetch_fundamentals_with_variants(normalized)
    except Exception as exc:
        logger.warning('yahoo_fundamentals_failed symbol=%s error=%s', normalized, exc)

    def pick(key):
        return _none_if_nan(fundamentals.get(key))

    latest_price = pick('latest_price')
    previous_close = pick('previous_close')
    open_price = pick('open')
    day_low = pick('day_low')
    day_high = pick('day_high')
    week_low = pick('week_52_low')
    week_high = pick('week_52_high')
    volume = pick('volume')
    currency = fundamentals.get('currency')

    # Price fields can always be recovered from the chart endpoint, which needs no crumb.
    if None in (latest_price, previous_close, open_price, day_low, day_high, week_low, week_high, volume):
        try:
            chart = _fetch_yahoo_chart_with_variants(normalized, 'day')
        except Exception as exc:
            if not fundamentals:
                not_found = (
                    (isinstance(exc, HTTPException) and exc.status_code == status.HTTP_404_NOT_FOUND)
                    or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 404)
                )
                if not_found:
                    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'No market data found for {normalized}')
                raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f'market_data_unavailable: {exc}')
            chart = {}
        quote = chart.get('quote') or {}
        points = chart.get('points') or []
        lows = [p['low'] for p in points if p.get('low') is not None]
        highs = [p['high'] for p in points if p.get('high') is not None]
        first_open = next(
            (p.get('open') if p.get('open') is not None else p.get('close') for p in points
             if p.get('open') is not None or p.get('close') is not None),
            None,
        )

        latest_price = latest_price if latest_price is not None else _none_if_nan(quote.get('price'))
        previous_close = previous_close if previous_close is not None else _none_if_nan(quote.get('previous_close'))
        open_price = open_price if open_price is not None else _none_if_nan(first_open)
        day_low = day_low if day_low is not None else (float(min(lows)) if lows else None)
        day_high = day_high if day_high is not None else (float(max(highs)) if highs else None)
        week_low = week_low if week_low is not None else _none_if_nan(quote.get('week_52_low'))
        week_high = week_high if week_high is not None else _none_if_nan(quote.get('week_52_high'))
        volume = volume if volume is not None else _none_if_nan(quote.get('volume'))
        currency = currency or quote.get('currency')

    return {
        'symbol': normalized,
        'currency': currency,
        'latest_price': latest_price,
        'previous_close': previous_close,
        'open': open_price,
        'day_low': day_low,
        'day_high': day_high,
        'week_52_low': week_low,
        'week_52_high': week_high,
        'volume': volume,
        'market_cap': pick('market_cap'),
        'revenue': pick('revenue'),
        'net_income': pick('net_income'),
        'eps': pick('eps'),
        'pe_ratio': pick('pe_ratio'),
        'beta': pick('beta'),
    }


_FX_LIVE_TTL_S = 60
_FX_FALLBACK_TTL_S = 60 * 60
# FX trades 24/5, so the last tick can legitimately be from Friday; allow for weekends and holidays.
_FX_STALE_AFTER_S = 4 * 24 * 60 * 60
_fx_lock = threading.Lock()
_fx_cache: dict = {}
_fx_last_good: dict = {}
_fx_fallback_table: dict = {'expires': 0.0, 'rates': {}, 'as_of': None}


def _fetch_fx_yahoo(currency: str) -> dict:
    """Live USD->currency rate from Yahoo's `{CUR}=X` pair (units of currency per 1 USD)."""
    with httpx.Client(timeout=10.0, headers=_YAHOO_HEADERS, follow_redirects=True) as client:
        response = client.get(
            f'https://query1.finance.yahoo.com/v8/finance/chart/{currency}=X',
            params={'range': '1d', 'interval': '1m'},
        )
        response.raise_for_status()
        payload = response.json() or {}
    results = (payload.get('chart') or {}).get('result') or []
    meta = (results[0] or {}).get('meta') or {} if results else {}
    rate = _none_if_nan(meta.get('regularMarketPrice'))
    if rate is None or rate <= 0 or str(meta.get('currency') or '').upper() != currency:
        raise ValueError(f'yahoo_fx_invalid_quote for {currency}')
    market_time = meta.get('regularMarketTime')
    return {
        'rate': float(rate),
        'source': 'Yahoo Finance',
        'as_of': _epoch_to_iso(market_time) if market_time else datetime.now(timezone.utc).isoformat(),
    }


def _fetch_fx_fallback(currency: str) -> dict:
    """Daily reference rates (open.er-api.com, no key) used only when the live feed fails."""
    now = time.monotonic()
    if now >= _fx_fallback_table['expires']:
        with httpx.Client(timeout=10.0) as client:
            response = client.get('https://open.er-api.com/v6/latest/USD')
            response.raise_for_status()
            payload = response.json() or {}
        if payload.get('result') != 'success':
            raise ValueError('fx_fallback_unavailable')
        updated = payload.get('time_last_update_unix')
        _fx_fallback_table.update(
            expires=now + _FX_FALLBACK_TTL_S,
            rates=payload.get('rates') or {},
            as_of=_epoch_to_iso(updated) if updated else None,
        )
    rate = _none_if_nan(_fx_fallback_table['rates'].get(currency))
    if rate is None or rate <= 0:
        raise ValueError(f'fx_fallback_missing {currency}')
    return {'rate': float(rate), 'source': 'ExchangeRate-API (daily)', 'as_of': _fx_fallback_table['as_of']}


def _is_stale(as_of: Optional[str]) -> bool:
    if not as_of:
        return True
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(as_of)
    except ValueError:
        return True
    return age.total_seconds() > _FX_STALE_AFTER_S


def _get_fx_rate(currency: str) -> Optional[dict]:
    if currency == 'USD':
        return {'rate': 1.0, 'source': 'identity', 'as_of': datetime.now(timezone.utc).isoformat(), 'stale': False}

    now = time.monotonic()
    with _fx_lock:
        cached = _fx_cache.get(currency)
        if cached and now < cached[0]:
            return cached[1]

    entry = None
    for fetcher in (_fetch_fx_yahoo, _fetch_fx_fallback):
        try:
            entry = fetcher(currency)
            break
        except Exception as exc:
            logger.warning('fx_source_failed source=%s currency=%s error=%s', fetcher.__name__, currency, exc)

    with _fx_lock:
        if entry is not None:
            entry['stale'] = _is_stale(entry['as_of'])
            _fx_last_good[currency] = entry
        else:
            # Both sources down: serve the last good rate, clearly flagged, rather than inventing one.
            last = _fx_last_good.get(currency)
            entry = {**last, 'stale': True} if last else None
        if entry is not None:
            _fx_cache[currency] = (now + _FX_LIVE_TTL_S, entry)
    return entry


def _fetch_fx_rates(currencies: List[str]) -> dict:
    rates = {'USD': 1.0}
    details = {}
    unavailable = []
    for currency in currencies:
        entry = _get_fx_rate(currency)
        if entry is None:
            unavailable.append(currency)
            continue
        rates[currency] = entry['rate']
        details[currency] = entry
    return {
        'base': 'USD',
        'rates': rates,
        'details': details,
        'unavailable': unavailable,
        'fetched_at': datetime.now(timezone.utc).isoformat(),
    }


@router.get('/fx')
async def get_fx_rates(symbols: str = Query(default='THB')):
    """Units of each currency per 1 USD, e.g. {"rates": {"USD": 1, "THB": 33.58}}."""
    currencies = []
    for raw in symbols.split(','):
        code = raw.strip().upper()
        if len(code) == 3 and code.isalpha() and code not in currencies and code != 'USD':
            currencies.append(code)
    if len(currencies) > 20:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Too many currencies (max 20)')
    return await run_in_threadpool(_fetch_fx_rates, currencies)


_FX_HISTORY_TTL_S = {'day': 60, 'month': 10 * 60, 'year': 60 * 60, 'all': 6 * 60 * 60}
_fx_history_cache: dict = {}


def _fetch_fx_history_series(currency: str, range_name: str) -> List[dict]:
    """USD->currency rates over the same window/interval the price chart uses."""
    key = (currency, range_name)
    now = time.monotonic()
    cached = _fx_history_cache.get(key)
    if cached and now < cached[0]:
        return cached[1]

    period, interval = _RANGE_MAP[range_name]
    with httpx.Client(timeout=15.0, headers=_YAHOO_HEADERS, follow_redirects=True) as client:
        response = client.get(
            f'https://query1.finance.yahoo.com/v8/finance/chart/{currency}=X',
            params={'range': period, 'interval': interval, 'includePrePost': 'false'},
        )
        response.raise_for_status()
        payload = response.json() or {}
    results = (payload.get('chart') or {}).get('result') or []
    if not results:
        raise ValueError(f'fx_history_unavailable for {currency}')
    result = results[0] or {}
    if str((result.get('meta') or {}).get('currency') or '').upper() != currency:
        raise ValueError(f'fx_history_currency_mismatch for {currency}')
    closes = (((result.get('indicators') or {}).get('quote') or [{}])[0] or {}).get('close') or []

    points = []
    for ts, rate in zip(result.get('timestamp') or [], closes):
        rate = _none_if_nan(rate)
        if rate is not None and rate > 0:
            points.append({'t': _epoch_to_iso(ts), 'rate': float(rate)})
    if not points:
        raise ValueError(f'fx_history_empty for {currency}')

    _fx_history_cache[key] = (now + _FX_HISTORY_TTL_S[range_name], points)
    return points


def _fetch_fx_history(currencies: List[str], range_name: str) -> dict:
    series = {}
    unavailable = []
    for currency in currencies:
        try:
            series[currency] = _fetch_fx_history_series(currency, range_name)
        except Exception as exc:
            logger.warning('fx_history_failed currency=%s range=%s error=%s', currency, range_name, exc)
            unavailable.append(currency)
    return {
        'base': 'USD',
        'range': range_name,
        'source': 'Yahoo Finance',
        'series': series,
        'unavailable': unavailable,
    }


@router.get('/fx/history')
async def get_fx_history(symbols: str = Query(default='THB'), range: str = Query(default='month')):
    """Historical units of each currency per 1 USD, aligned to the chart ranges (day/month/year/all)."""
    currencies = []
    for raw in symbols.split(','):
        code = raw.strip().upper()
        if len(code) == 3 and code.isalpha() and code not in currencies and code != 'USD':
            currencies.append(code)
    if len(currencies) > 5:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Too many currencies (max 5)')
    return await run_in_threadpool(_fetch_fx_history, currencies, _normalize_range(range))


@router.get('/quotes')
async def get_quotes(symbols: str = Query(default='')):
    parsed = [_normalize_symbol(s) for s in symbols.split(',') if _normalize_symbol(s)]
    if not parsed:
        return []
    try:
        return await run_in_threadpool(_fetch_quotes, parsed)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get('/stats/{symbol}')
async def get_asset_statistics(symbol: str):
    normalized = _normalize_symbol(symbol)
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid symbol')
    try:
        return await run_in_threadpool(_fetch_asset_statistics, normalized)
    except HTTPException:
        raise
    except Exception as exc:
        # Upstream data provider failure, not a client error.
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))


@router.get('/chart/{symbol}')
async def get_chart(symbol: str, range: str = Query(default='day')):
    symbol = _normalize_symbol(symbol)
    range_name = _normalize_range(range)
    if not symbol:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Invalid symbol')
    try:
        # use variants-aware fetch
        return await run_in_threadpool(_fetch_yahoo_chart_with_variants, symbol, range_name)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
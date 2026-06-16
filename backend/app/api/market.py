from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query, status
from starlette.concurrency import run_in_threadpool

import httpx
import os

router = APIRouter()

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
            'market_cap': meta.get('marketCap'),
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
    if not key or not secret:
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
            },
        }


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
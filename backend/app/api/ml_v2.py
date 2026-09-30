from __future__ import annotations

import logging
import os
import time
import json
from datetime import date, datetime, time as dt_time, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.auth import get_current_user
from app.api.rate_limit import enforce_guest_llm_rate_limit
from app.core.db import get_database
from app.ml.news_fetcher import fetch_news_for_symbol, fetch_ohlcv, news_provider_status
from app.ml.news_sentiment_analyzer import NewsSentimentAnalyzer, NewsSentimentAnalyzerError
from app.ml.market_forecaster import MarketForecaster, MarketForecasterError
from app.ml.trading_bot import AutomatedTradingBot, RiskParameterViolation
from app.ml.dual_llm_manager import DualLLMManager, DualLLMManagerError
from app.api.ai_performance import (
    _estimate_tokens,
    record_finbert_batch,
    record_forecaster_event,
    record_llm_prompt,
)


logger = logging.getLogger(__name__)
router = APIRouter()


# Singletons (lazy-load internally)
_SENTIMENT_ANALYZER = NewsSentimentAnalyzer()
_TRADING_BOT = AutomatedTradingBot()
_DUAL_LLM = DualLLMManager()


# In-memory per-user forecaster store for demo/prototyping.
# Key: (owner_sub, symbol)
_FORECASTERS: Dict[Tuple[str, str], MarketForecaster] = {}
_GUEST_ALLOWED_SYMBOLS = {'BTC', 'ETH', 'AAPL', 'MSFT', 'TSLA'}

_FEATURE_LABEL_MAP = {
    'ret_3': 'Short-term 3-day return',
    'close': 'Current price level',
    'ma_5': '5-day moving average',
    'ma_10': '10-day moving average',
    'vol_chg_1': 'Daily trading volume change',
    'ret_1': 'Daily return',
    'range_pct': 'Intraday price range',
    'sentiment_score': 'News sentiment score',
}

NEWS_CACHE_TTL_MINUTES = 60
# Daily fade of news sentiment on days without new articles (0.7 = half-life of about 2 days).
NEWS_SENTIMENT_DECAY = 0.7
INSIGHTS_CACHE_TTL_MINUTES = 15

# (owner, service) pairs whose stored API key could not be read, already reported in the log.
_KEY_LOOKUP_WARNED: set = set()

# Minimum model confidence (0-100) for a BUY/SELL insight to raise an alert / reach the trading bot.
try:
    BOT_MIN_SIGNAL_CONFIDENCE = int(os.environ.get('AJTRADE_BOT_MIN_CONFIDENCE', '70'))
except (TypeError, ValueError):
    BOT_MIN_SIGNAL_CONFIDENCE = 70

# (owner, symbol) -> (monotonic fetch time, articles, FinBERT-scored articles) used by insight rebuilds,
# so signals can refresh more often than news is fetched.
_INSIGHT_NEWS_CACHE: Dict[Tuple[str, str], Tuple[float, List[Dict[str, Any]], List[Dict[str, Any]]]] = {}


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _utcnow() -> datetime:
    return datetime.utcnow()


def _is_fresh(updated_at: Optional[datetime], ttl_minutes: float) -> bool:
    if updated_at is None:
        return False
    now = datetime.now(updated_at.tzinfo) if updated_at.tzinfo is not None else _utcnow()
    return (now - updated_at) <= timedelta(minutes=float(ttl_minutes))


def _to_json_payload(value: Dict[str, Any]) -> str:
    return json.dumps(value, separators=(",", ":"), default=str)


def _from_json_payload(raw_value: Any) -> Optional[Dict[str, Any]]:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        return raw_value
    try:
        if isinstance(raw_value, str):
            loaded = json.loads(raw_value)
            return loaded if isinstance(loaded, dict) else None
    except Exception:
        return None
    return None


def _build_news_cache_key(symbol: str, *, days: int, page: int, page_size: int, from_date: Optional[str], to_date: Optional[str]) -> str:
    return "|".join(
        [
            _normalize_symbol(symbol),
            str(int(days)),
            str(int(page)),
            str(int(page_size)),
            str(from_date or ''),
            str(to_date or ''),
        ]
    )


async def _read_news_cache(
    db,
    *,
    owner: str,
    symbol: str,
    days: int,
    page: int,
    page_size: int,
    from_date: Optional[str],
    to_date: Optional[str],
    ttl_minutes: int = NEWS_CACHE_TTL_MINUTES,
) -> Optional[Dict[str, Any]]:
    cache_key = _build_news_cache_key(
        symbol,
        days=days,
        page=page,
        page_size=page_size,
        from_date=from_date,
        to_date=to_date,
    )
    try:
        row = await db.fetch_one(
            query=(
                "SELECT payload, updated_at FROM news_cache "
                "WHERE owner = :owner AND symbol = :symbol AND cache_key = :cache_key "
                "LIMIT 1"
            ),
            values={
                'owner': owner,
                'symbol': _normalize_symbol(symbol),
                'cache_key': cache_key,
            },
        )
        if not row:
            return None
        updated_at = row['updated_at'] if 'updated_at' in row else None
        if not _is_fresh(updated_at, ttl_minutes):
            return None
        return _from_json_payload(row['payload'])
    except Exception:
        return None


async def _write_news_cache(
    db,
    *,
    owner: str,
    symbol: str,
    days: int,
    page: int,
    page_size: int,
    from_date: Optional[str],
    to_date: Optional[str],
    payload: Dict[str, Any],
) -> None:
    cache_key = _build_news_cache_key(
        symbol,
        days=days,
        page=page,
        page_size=page_size,
        from_date=from_date,
        to_date=to_date,
    )
    try:
        await db.execute(
            query=(
                "INSERT INTO news_cache (owner, symbol, cache_key, payload, updated_at) "
                "VALUES (:owner, :symbol, :cache_key, CAST(:payload AS jsonb), now()) "
                "ON CONFLICT (owner, symbol, cache_key) DO UPDATE SET "
                "payload = EXCLUDED.payload, updated_at = now() "
                # An empty fetch (quota hit, provider down) must not wipe articles fetched earlier.
                "WHERE COALESCE(jsonb_array_length(EXCLUDED.payload->'articles'), 0) > 0 "
                "OR COALESCE(jsonb_array_length(news_cache.payload->'articles'), 0) = 0"
            ),
            values={
                'owner': owner,
                'symbol': _normalize_symbol(symbol),
                'cache_key': cache_key,
                'payload': _to_json_payload(payload),
            },
        )
    except Exception as e:
        logger.debug('news_cache_write_failed owner=%s symbol=%s error=%s', owner, symbol, e)


async def _read_latest_news_cache_for_symbol(
    db,
    *,
    owner: str,
    symbol: str,
    ttl_minutes: int = NEWS_CACHE_TTL_MINUTES,
) -> Optional[Dict[str, Any]]:
    try:
        row = await db.fetch_one(
            query=(
                "SELECT payload, updated_at FROM news_cache "
                "WHERE owner = :owner AND symbol = :symbol "
                "ORDER BY updated_at DESC LIMIT 1"
            ),
            values={
                'owner': owner,
                'symbol': _normalize_symbol(symbol),
            },
        )
        if not row:
            return None
        updated_at = row['updated_at'] if 'updated_at' in row else None
        if not _is_fresh(updated_at, ttl_minutes):
            return None
        return _from_json_payload(row['payload'])
    except Exception:
        return None


async def _read_saved_news_for_symbol(db, *, owner: str, symbol: str) -> Optional[Tuple[Dict[str, Any], Any]]:
    """Most recent cached news payload for the symbol that has articles, regardless of age."""
    try:
        row = await db.fetch_one(
            query=(
                "SELECT payload, updated_at FROM news_cache "
                "WHERE owner = :owner AND symbol = :symbol "
                "AND COALESCE(jsonb_array_length(payload->'articles'), 0) > 0 "
                "ORDER BY updated_at DESC LIMIT 1"
            ),
            values={'owner': owner, 'symbol': _normalize_symbol(symbol)},
        )
    except Exception as exc:
        logger.debug('saved_news_lookup_failed owner=%s symbol=%s error=%s', owner, symbol, exc)
        return None
    if not row:
        return None
    payload = _from_json_payload(row['payload'])
    return (payload, row['updated_at']) if payload else None


def _news_unavailable_reason() -> Optional[str]:
    status = news_provider_status()
    error = status.get('newsapi') or status.get('newsdata')
    if not error:
        return None
    code = str(error.get('code') or '')
    if code in {'rateLimited', 'apiKeyExhausted', 'http_429'}:
        return 'the news API daily limit was reached'
    if code in {'apiKeyInvalid', 'apiKeyDisabled', 'apiKeyMissing', 'http_401'}:
        return 'the news API key was rejected'
    return f"the news API returned an error ({code})"


async def _with_saved_articles(db, *, owner: str, symbol: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """When a news payload has no articles, show the last articles saved for the symbol (marked as saved)."""
    if payload.get('articles') or int(payload.get('page') or 1) > 1:
        return payload
    saved = await _read_saved_news_for_symbol(db, owner=owner, symbol=symbol)
    reason = _news_unavailable_reason()
    if not saved:
        return {**payload, 'news_note': f'No live news right now: {reason}.' if reason else None}
    saved_payload, saved_at = saved
    return {
        **saved_payload,
        'page': 1,
        'has_more': False,
        'stale': True,
        'saved_at': saved_at.isoformat() if hasattr(saved_at, 'isoformat') else str(saved_at),
        'news_note': f"Showing saved news{': ' + reason if reason else ' (no newer articles found)'}.",
    }


async def _read_insight_cache(
    db,
    *,
    owner: str,
    symbol: str,
    ttl_minutes: int = INSIGHTS_CACHE_TTL_MINUTES,
) -> Optional[Dict[str, Any]]:
    try:
        row = await db.fetch_one(
            query=(
                "SELECT payload, updated_at FROM insights "
                "WHERE owner = :owner AND symbol = :symbol "
                "LIMIT 1"
            ),
            values={'owner': owner, 'symbol': _normalize_symbol(symbol)},
        )
        if not row:
            return None
        updated_at = row['updated_at'] if 'updated_at' in row else None
        if not _is_fresh(updated_at, ttl_minutes):
            return None
        return _from_json_payload(row['payload'])
    except Exception:
        return None


async def _write_insight_cache(db, *, owner: str, symbol: str, payload: Dict[str, Any]) -> None:
    try:
        await db.execute(
            query=(
                "INSERT INTO insights (owner, symbol, payload, updated_at) "
                "VALUES (:owner, :symbol, CAST(:payload AS jsonb), now()) "
                "ON CONFLICT (owner, symbol) DO UPDATE SET "
                "payload = EXCLUDED.payload, updated_at = now()"
            ),
            values={
                'owner': owner,
                'symbol': _normalize_symbol(symbol),
                'payload': _to_json_payload(payload),
            },
        )
    except Exception as e:
        logger.debug('insight_cache_write_failed owner=%s symbol=%s error=%s', owner, symbol, e)


def _article_text(article: Dict[str, Any]) -> str:
    return " ".join(filter(None, [article.get("title", ""), article.get("description", ""), article.get("content", "")]))


def _article_excerpt(article: Dict[str, Any]) -> str:
    text = article.get('description') or article.get('content') or ''
    text = str(text).strip()
    if len(text) > 220:
        return f"{text[:217].rstrip()}..."
    return text


def _article_thumbnail_url(article: Dict[str, Any]) -> Optional[str]:
    image = article.get('urlToImage')
    if image:
        return str(image)
    if article.get('image_url'):
        return str(article.get('image_url'))
    if article.get('image'):
        return str(article.get('image'))
    return None


def _asset_display_name(asset: Dict[str, Any], symbol: str) -> str:
    historical_data = asset.get('historical_data') or {}
    quote = historical_data.get('quote') or {}
    return str(
        quote.get('display_name')
        or quote.get('short_name')
        or quote.get('long_name')
        or asset.get('display_name')
        or symbol
    )


def _parse_published_date(article: Dict[str, Any]) -> Optional[pd.Timestamp]:
    s = article.get("publishedAt")
    if not s:
        return None
    try:
        ts = pd.to_datetime(s, utc=True, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.tz_convert(None).normalize()
    except Exception:
        return None


def _scored_items_from_cached_news_payload(cached_news_payload: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(cached_news_payload, dict):
        return []

    scored_items: List[Dict[str, Any]] = []
    for article in (cached_news_payload.get('articles') or []):
        if not isinstance(article, dict):
            continue
        published = _parse_published_date({'publishedAt': article.get('published_at')})
        if published is None:
            continue
        try:
            score = float(article.get('sentiment_score'))
        except Exception:
            continue

        scored_items.append(
            {
                'published_date': published,
                'score': score,
                'label': article.get('sentiment_label') or 'UNSCORED',
            }
        )
    return scored_items


def _resolve_news_window(days: int, from_date: Optional[str], to_date: Optional[str]) -> Tuple[datetime, datetime]:
    max_days = max(int(days), 1)
    end_dt = datetime.utcnow()

    if to_date:
        try:
            parsed_to = date.fromisoformat(str(to_date))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail='invalid_to_date') from exc
        end_dt = datetime.combine(parsed_to, dt_time.max)

    start_dt = end_dt - timedelta(days=max_days)
    if from_date:
        try:
            parsed_from = date.fromisoformat(str(from_date))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail='invalid_from_date') from exc
        start_dt = datetime.combine(parsed_from, dt_time.min)

    if start_dt > end_dt:
        raise HTTPException(status_code=400, detail='from_date_must_be_before_to_date')

    return start_dt, end_dt


async def _get_newsapi_key_for_owner(owner: str) -> Optional[str]:
    try:
        db = get_database()
        row = await db.fetch_one(
            query=(
                "SELECT encrypted_blob FROM encrypted_api_keys "
                "WHERE owner = :owner AND lower(service) = 'newsapi' "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            values={"owner": owner},
        )
        if not row:
            return None

        from app.core import crypto

        return crypto.decrypt_api_key(row["encrypted_blob"]).decode("utf-8")
    except Exception as exc:
        logger.warning('newsapi_key_lookup_failed owner=%s error=%s', owner, exc)
        return None


async def _get_api_key_for_owner(owner: str, service: str) -> Optional[str]:
    try:
        db = get_database()
        row = await db.fetch_one(
            query=(
                "SELECT encrypted_blob FROM encrypted_api_keys "
                "WHERE owner = :owner AND lower(service) = :service "
                "ORDER BY created_at DESC LIMIT 1"
            ),
            values={"owner": owner, "service": str(service or '').strip().lower()},
        )
        if not row:
            return None

        from app.core import crypto

        return crypto.decrypt_api_key(row["encrypted_blob"]).decode("utf-8")
    except Exception as exc:
        # Signals refresh every few minutes, so report each unreadable key once per process.
        warn_key = (owner, str(service or '').lower())
        if warn_key not in _KEY_LOOKUP_WARNED:
            _KEY_LOOKUP_WARNED.add(warn_key)
            logger.warning(
                'service_key_lookup_failed owner=%s service=%s error=%s (an empty error usually means the key was '
                'saved by a server with a different AJTRADE_DATA_KEY; re-save it in API Management)',
                owner,
                service,
                type(exc).__name__,
            )
        return None


async def _get_news_credentials(owner: str) -> Dict[str, Optional[str]]:
    newsapi_key = await _get_api_key_for_owner(owner, 'newsapi')
    newsdata_key = await _get_api_key_for_owner(owner, 'newsdata')

    # Environment fallbacks for non-vault deployments.
    if not newsapi_key:
        newsapi_key = (os.environ.get('NEWSAPI_API_KEY') or '').strip() or None
    if not newsdata_key:
        newsdata_key = (os.environ.get('NEWSDATA_API_KEY') or 'pub_b26c66dae79a41bb8ad13ee302ef38e0').strip() or None

    # Prefer NewsAPI when available; fallback to NewsData.
    if newsapi_key:
        return {
            'primary_provider': 'newsapi',
            'primary_key': newsapi_key,
            'fallback_provider': 'newsdata' if newsdata_key else None,
            'fallback_key': newsdata_key,
        }
    if newsdata_key:
        return {
            'primary_provider': 'newsdata',
            'primary_key': newsdata_key,
            'fallback_provider': None,
            'fallback_key': None,
        }

    return {
        'primary_provider': None,
        'primary_key': None,
        'fallback_provider': None,
        'fallback_key': None,
    }


async def _get_watchlist_symbols(owner: str) -> List[str]:
    db = get_database()
    rows = await db.fetch_all(
        query=(
            "SELECT symbol FROM watchlists "
            "WHERE owner = :owner "
            "ORDER BY created_at DESC"
        ),
        values={"owner": owner},
    )
    symbols: List[str] = []
    for row in rows or []:
        symbol = None
        try:
            symbol = row['symbol']
        except Exception:
            if isinstance(row, dict):
                symbol = row.get('symbol')
        if symbol:
            symbols.append(str(symbol).upper())
    return symbols


def _build_sentiment_series(df_ohlcv: pd.DataFrame, scored_articles: List[Dict[str, Any]]) -> pd.Series:
    idx = pd.DatetimeIndex(df_ohlcv.index).tz_localize(None).normalize()

    # Aggregate article sentiment by day (mean score).
    scores_by_day: Dict[pd.Timestamp, List[float]] = {}
    for item in scored_articles:
        d: Optional[pd.Timestamp] = item.get("published_date")
        score = item.get("score")
        if d is None:
            continue
        try:
            s = float(score)
        except Exception:
            continue
        scores_by_day.setdefault(d, []).append(s)

    day_mean: Dict[pd.Timestamp, float] = {d: float(sum(vals) / len(vals)) for d, vals in scores_by_day.items() if vals}

    # Align to OHLCV rows. News mood lasts beyond the day it was published, and the free NewsAPI plan
    # delivers articles about a day late, so days without articles carry the last value forward,
    # fading by NEWS_SENTIMENT_DECAY per day (instead of dropping to 0 = "neutral").
    article_days = sorted(day_mean)
    values: List[float] = []
    carried = 0.0
    last_day: Optional[pd.Timestamp] = None
    pos = 0
    for d in idx:
        # Fold in every article day up to this row (covers weekends/holidays between price rows).
        while pos < len(article_days) and article_days[pos] <= d:
            last_day = article_days[pos]
            carried = day_mean[last_day]
            pos += 1
        if last_day is None:
            values.append(0.0)
        else:
            values.append(float(carried * NEWS_SENTIMENT_DECAY ** max((d - last_day).days, 0)))

    return pd.Series(values, index=df_ohlcv.index, name="sentiment_score", dtype=float)


def _score_articles_finbert(analyzer: NewsSentimentAnalyzer, articles: List[Dict[str, Any]], *, max_articles: int = 60) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []

    for art in (articles or [])[: int(max_articles)]:
        text = _article_text(art)
        if not text:
            continue
        published_date = _parse_published_date(art)
        if published_date is None:
            continue
        try:
            res = analyzer.analyze_text(text)
        except Exception as e:
            # Best-effort: skip bad articles rather than failing the whole run.
            logger.debug("FinBERT scoring failed for one article: %s", e)
            continue

        scored.append(
            {
                "published_date": published_date,
                "score": float(res.get("score", 0.0)),
                "label": res.get("label"),
                "confidence_pct": float(res.get("confidence_pct", 0.0)),
                "title": art.get("title"),
                "source": (art.get("source") or {}).get("name"),
                "url": art.get("url"),
            }
        )

    return scored


def _daily_sentiment_summary(scored: List[Dict[str, Any]], *, max_headlines: int = 3) -> List[Dict[str, Any]]:
    """Per-day FinBERT sentiment from scored articles, for the Analytics heatmap.

    Each day: average score (-1..+1), article counts (positive > +0.1, negative < -0.1, otherwise neutral)
    and the headlines with the strongest scores.
    """
    days: Dict[str, Dict[str, Any]] = {}
    for item in scored or []:
        published = item.get('published_date')
        if published is None:
            continue
        day = published.date() if hasattr(published, 'date') else published
        key = str(day.isoformat() if hasattr(day, 'isoformat') else day)[:10]
        try:
            score = float(item.get('score'))
        except (TypeError, ValueError):
            continue
        bucket = days.setdefault(key, {'scores': [], 'headlines': []})
        bucket['scores'].append(score)
        if item.get('title'):
            bucket['headlines'].append(
                {'title': item.get('title'), 'source': item.get('source'), 'url': item.get('url'), 'score': round(score, 4)}
            )

    summary = []
    for key in sorted(days):
        scores = days[key]['scores']
        positive = sum(1 for s in scores if s > 0.1)
        negative = sum(1 for s in scores if s < -0.1)
        headlines = sorted(days[key]['headlines'], key=lambda h: abs(h['score']), reverse=True)[:max_headlines]
        summary.append(
            {
                'date': key,
                'avg': round(sum(scores) / len(scores), 4),
                'count': len(scores),
                'positive': positive,
                'negative': negative,
                'neutral': len(scores) - positive - negative,
                'headlines': headlines,
            }
        )
    return summary


def _sentiment_label(avg_score: float) -> str:
    if avg_score >= 0.35:
        return 'Strong Bullish'
    if avg_score >= 0.1:
        return 'Bullish'
    if avg_score <= -0.35:
        return 'Strong Bearish'
    if avg_score <= -0.1:
        return 'Bearish'
    return 'Neutral'


def _trend_outlook(price_change_pct: float, avg_sentiment: float) -> str:
    composite = (price_change_pct / 100.0) + avg_sentiment
    if composite >= 0.18:
        return 'Uptrend likely to continue'
    if composite >= 0.05:
        return 'Positive bias with manageable volatility'
    if composite <= -0.18:
        return 'Downtrend risk remains elevated'
    if composite <= -0.05:
        return 'Weak trend with bearish pressure'
    return 'Sideways until a stronger catalyst appears'


def _humanize_feature_name(feature: str) -> str:
    return _FEATURE_LABEL_MAP.get(str(feature or '').strip(), str(feature or '').replace('_', ' ').strip())


def _build_natural_rationale_line(feature: Dict[str, Any]) -> str:
    human_name = _humanize_feature_name(feature.get('feature'))
    impact = float(feature.get('impact_pct') or 0.0)
    direction = str(feature.get('direction') or '').lower()
    if direction == 'positive':
        return f"The {human_name} is supporting upside potential ({abs(impact):.1f}% impact)."
    return f"The {human_name} is adding downside pressure ({abs(impact):.1f}% impact)."


def _build_llm_asset_context(asset: Dict[str, Any]) -> Dict[str, Any]:
    historical_data = asset.get('historical_data') or {}
    quote = historical_data.get('quote') or {}
    points = (historical_data.get('points') or [])[-8:]
    recent_closes = []

    for point in points:
        close_value = point.get('close')
        if close_value is None:
            continue
        recent_closes.append({
            't': point.get('t'),
            'close': close_value,
        })

    return {
        'symbol': asset.get('symbol'),
        'price': asset.get('price'),
        'price_change': asset.get('price_change'),
        'price_change_pct': asset.get('price_change_pct'),
        'volume': quote.get('volume'),
        'quote': quote,
        'sentiment': asset.get('sentiment'),
        'history_period': historical_data.get('period'),
        'recent_closes': recent_closes,
        'latest_quote': (historical_data.get('quote') or {}).get('price'),
    }


def _history_payload_to_ohlcv_df(history_payload: Dict[str, Any]) -> pd.DataFrame:
    rows = []
    for point in (history_payload.get('points') or []):
        timestamp = point.get('t')
        if not timestamp:
            continue
        rows.append(
            {
                'Date': pd.to_datetime(timestamp, utc=True, errors='coerce'),
                'Open': point.get('open'),
                'High': point.get('high'),
                'Low': point.get('low'),
                'Close': point.get('close'),
                'Volume': point.get('volume'),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    df = df.dropna(subset=['Date', 'Close']).set_index('Date').sort_index()
    for column in ['Open', 'High', 'Low', 'Close', 'Volume']:
        df[column] = pd.to_numeric(df[column], errors='coerce')
    df['Volume'] = df['Volume'].fillna(0.0)
    return df.dropna(subset=['Open', 'High', 'Low', 'Close'])


def _fallback_explanation(shap_context: Dict[str, Any], prompt: str) -> Dict[str, Any]:
    symbol = str(shap_context.get('symbol') or 'Asset')
    price = shap_context.get('price')
    price_change_pct = float(shap_context.get('price_change_pct') or 0.0)
    sentiment = shap_context.get('sentiment') or {}
    sentiment_label = sentiment.get('heatmap_label') or _sentiment_label(float(sentiment.get('avg_sentiment') or 0.0))
    avg_sentiment = float(sentiment.get('avg_sentiment') or 0.0)
    outlook = _trend_outlook(price_change_pct, avg_sentiment)

    lines = [
        f"{symbol} is trading around {price if price is not None else 'an unavailable price'}.",
        f"Recent price performance is {price_change_pct:.2f}% and the news tone is {sentiment_label}.",
        f"Current outlook: {outlook}.",
        f"Requested focus: {prompt}",
        "This is a rule-based fallback summary because the LLM service is unavailable.",
    ]

    return {
        'model_used': 'rule-based-fallback',
        'used_fallback': True,
        'explanation': ' '.join(lines),
    }


def _minimal_llm_context(symbol: str, context: Dict[str, Any]) -> Dict[str, Any]:
    quote = context.get('quote') if isinstance(context.get('quote'), dict) else {}
    sentiment = context.get('sentiment') if isinstance(context.get('sentiment'), dict) else {}
    return {
        'symbol': symbol,
        'latest_price': context.get('price') or context.get('latest_price') or quote.get('price'),
        'price_change_pct': context.get('price_change_pct') or quote.get('change_percent') or 0.0,
        'volume': context.get('volume') or quote.get('volume'),
        'latest_sentiment_score': context.get('latest_sentiment_score') or sentiment.get('avg_sentiment') or 0.0,
        'signal': context.get('signal') or context.get('recommendation') or 'HOLD',
    }


async def _generate_with_rescue(user_pref: str, symbol: str, context: Dict[str, Any], prompt: str) -> str:
    errors: List[str] = []

    try:
        return await _DUAL_LLM.generate_explanation(user_pref, context, prompt)
    except Exception as exc:
        errors.append(f"contextual_generation_failed={exc}")

    try:
        minimal_context = _minimal_llm_context(symbol, context)
        return await _DUAL_LLM.generate_explanation(user_pref, minimal_context, prompt)
    except Exception as exc:
        errors.append(f"minimal_context_generation_failed={exc}")

    # Final rescue: direct model call without strict context serialization.
    try:
        direct_prompt = (
            f"User message: {prompt}\n"
            f"Primary symbol: {symbol}\n"
            "Reply naturally and concisely in the same language as the user."
        )
        text = await _DUAL_LLM._ollama_generate(  # pylint: disable=protected-access
            model=_DUAL_LLM.model_open_source,
            system="You are AJTrade assistant. Keep answers brief and helpful.",
            prompt=direct_prompt,
        )
        _DUAL_LLM.last_model_used = _DUAL_LLM.model_open_source
        _DUAL_LLM.last_used_fallback = False
        return text
    except Exception as exc:
        errors.append(f"direct_generation_failed={exc}")

    raise DualLLMManagerError(' | '.join(errors))


async def _build_watch_asset_news(
    symbol: str,
    news_creds: Optional[Dict[str, Optional[str]]],
    *,
    owner: Optional[str] = None,
    days: int = 30,
    page: int = 1,
    page_size: int = 6,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
) -> Dict[str, Any]:
    from app.api.analytics import get_asset_detail

    asset = await get_asset_detail(symbol, '1mo')
    display_name = _asset_display_name(asset, symbol)
    price = float(asset.get('price') or 0.0)
    price_change_pct = float(asset.get('price_change_pct') or 0.0)
    scored: List[Dict[str, Any]] = []
    raw_articles: List[Dict[str, Any]] = []
    from_dt, to_dt = _resolve_news_window(days, from_date, to_date)

    primary_key = (news_creds or {}).get('primary_key') if isinstance(news_creds, dict) else None
    primary_provider = (news_creds or {}).get('primary_provider') if isinstance(news_creds, dict) else 'newsapi'
    fallback_key = (news_creds or {}).get('fallback_key') if isinstance(news_creds, dict) else None
    fallback_provider = (news_creds or {}).get('fallback_provider') if isinstance(news_creds, dict) else None

    if primary_key:
        raw_articles = await run_in_threadpool(
            fetch_news_for_symbol,
            primary_key,
            symbol,
            from_dt,
            to_dt,
            max(int(page_size), 1),
            max(int(page), 1),
            display_name,
            primary_provider or 'newsapi',
            fallback_key,
            fallback_provider,
        )
        scored = await run_in_threadpool(
            _score_articles_finbert,
            _SENTIMENT_ANALYZER,
            raw_articles,
            max_articles=max(int(page_size), 1),
        )

    avg_sentiment = float(sum(item['score'] for item in scored) / len(scored)) if scored else 0.0
    normalized_page_size = max(int(page_size), 1)

    if scored and owner:
        record_finbert_batch(owner=owner, scored_items=scored, provider=primary_provider or 'newsapi')

    articles = []
    for index, article in enumerate(raw_articles[:normalized_page_size]):
        scored_item = scored[index] if index < len(scored) else None
        articles.append(
            {
                'title': article.get('title') or 'Untitled article',
                'source': (article.get('source') or {}).get('name') or 'Unknown source',
                'url': article.get('url'),
                'published_at': article.get('publishedAt'),
                'thumbnail': _article_thumbnail_url(article),
                'excerpt': _article_excerpt(article),
                'sentiment_label': scored_item.get('label') if scored_item else 'UNSCORED',
                'sentiment_score': float(scored_item.get('score', 0.0)) if scored_item else 0.0,
            }
        )

    return {
        'symbol': symbol,
        'display_name': display_name,
        'price': price,
        'price_change_pct': price_change_pct,
        'avg_sentiment': avg_sentiment,
        'sentiment_label': _sentiment_label(avg_sentiment),
        'articles_count': len(articles),
        'articles': articles,
        'page': max(int(page), 1),
        'page_size': normalized_page_size,
        'lookback_days': max(int(days), 1),
        'from_date': from_dt.date().isoformat(),
        'to_date': to_dt.date().isoformat(),
        'has_more': len(raw_articles) >= normalized_page_size,
    }


async def _build_watch_asset_insight(
    symbol: str,
    owner: str,
    api_key: Optional[str],
    *,
    news_ttl_minutes: float = NEWS_CACHE_TTL_MINUTES,
) -> Dict[str, Any]:
    from app.api.analytics import get_asset_detail
    from app.api.market import get_historical_data

    asset = await get_asset_detail(symbol, '1mo')
    history_payload = await get_historical_data(symbol, 'year')
    df = await run_in_threadpool(_history_payload_to_ohlcv_df, history_payload)
    if len(df) > 180:
        df = df.tail(180)
    if df is None or getattr(df, 'empty', True):
        raise HTTPException(status_code=400, detail=f'no price data for {symbol}')

    db = get_database()
    news_creds = await _get_news_credentials(owner)
    primary_key = news_creds.get('primary_key')
    primary_provider = news_creds.get('primary_provider')
    fallback_key = news_creds.get('fallback_key')
    fallback_provider = news_creds.get('fallback_provider')

    articles: List[Dict[str, Any]] = []
    scored: List[Dict[str, Any]] = []
    news_cache_key = (owner, symbol)
    cached_news = _INSIGHT_NEWS_CACHE.get(news_cache_key)
    if cached_news and time.monotonic() - cached_news[0] <= float(news_ttl_minutes) * 60:
        _, articles, scored = cached_news
    else:
        if primary_key:
            to_dt = datetime.utcnow()
            from_dt = to_dt - timedelta(days=30)
            articles = await run_in_threadpool(
                fetch_news_for_symbol,
                primary_key,
                symbol,
                from_dt,
                to_dt,
                25,
                1,
                asset.get('display_name') or symbol,
                primary_provider or 'newsapi',
                fallback_key,
                fallback_provider,
            )

        scored = await run_in_threadpool(_score_articles_finbert, _SENTIMENT_ANALYZER, articles, max_articles=25)
        # Cache empty results too: news is fetched at most once per news TTL per asset, even when the
        # provider has nothing or is rate-limited (retrying every rebuild only burns the daily quota).
        _INSIGHT_NEWS_CACHE[news_cache_key] = (time.monotonic(), articles, scored)

    saved_news = await _read_saved_news_for_symbol(db, owner=owner, symbol=symbol)
    cached_news_payload = saved_news[0] if saved_news else None
    cached_avg_sentiment = 0.0
    try:
        if cached_news_payload is not None:
            cached_avg_sentiment = float(cached_news_payload.get('avg_sentiment') or 0.0)
    except Exception:
        cached_avg_sentiment = 0.0

    if not scored and cached_news_payload:
        scored = _scored_items_from_cached_news_payload(cached_news_payload)

    sentiment_series = await run_in_threadpool(_build_sentiment_series, df, scored)
    latest_sentiment = float(sentiment_series.iloc[-1]) if len(sentiment_series) else float(cached_avg_sentiment)

    if len(sentiment_series) and not scored and cached_avg_sentiment:
        sentiment_series = pd.Series([float(cached_avg_sentiment)] * len(sentiment_series), index=sentiment_series.index)

    signal = 'HOLD'
    probability_up: Optional[float] = None
    confidence: Optional[int] = None
    model_fallback = False
    rationale = []
    drivers: List[Dict[str, Any]] = []
    _default_forecaster = MarketForecaster()
    thresholds = {'buy': _default_forecaster.buy_threshold, 'sell': _default_forecaster.sell_threshold}
    try:
        forecaster = MarketForecaster()
        await run_in_threadpool(forecaster.train_model, df, sentiment_series)
        df2 = df.copy()
        df2['sentiment_score'] = sentiment_series
        signal = await run_in_threadpool(forecaster.generate_signal, df2)
        row = forecaster._to_feature_row(df2)  # type: ignore[attr-defined]
        probability_up = float(forecaster.model.predict_proba(row)[0, 1])  # type: ignore[union-attr]
        confidence = int(round(max(probability_up, 1 - probability_up) * 100))
        shap_expl = await run_in_threadpool(forecaster.get_shap_explanation, df2)
        rationale = [
            f"Price trend: {asset.get('price_change_pct', 0):.2f}% over the selected window.",
            f"News sentiment: {_sentiment_label(latest_sentiment)} ({latest_sentiment:.2f}).",
        ]
        for feature in (shap_expl.get('top_features') or [])[:3]:
            rationale.append(_build_natural_rationale_line(feature))
        # Structured SHAP drivers for the Insights chart: signed impact_pct (+ pushes toward "up").
        drivers = [
            {
                'feature': str(feat.get('feature')),
                'label': _humanize_feature_name(feat.get('feature')),
                'impact_pct': round(float(feat.get('impact_pct') or 0.0), 2),
            }
            for feat in (shap_expl.get('top_features') or [])[:5]
        ]
        snippet = ' | '.join(
            [
                f"{feat.get('feature')}={feat.get('impact_pct')}%"
                for feat in (shap_expl.get('top_features') or [])[:3]
            ]
        ) if (shap_expl.get('top_features') or []) else f"Signal={signal}; ProbUp={probability_up:.4f}; Sentiment={latest_sentiment:.3f}"
        # Thresholds on the same -1..+1 scale as raw_forecast_score (2 * P(up) - 1).
        record_forecaster_event(
            owner,
            symbol=symbol,
            raw_forecast_score=(probability_up * 2.0) - 1.0,
            bull_threshold=(forecaster.buy_threshold * 2.0) - 1.0,
            bear_threshold=(forecaster.sell_threshold * 2.0) - 1.0,
            shap_snippet=snippet,
        )
    except Exception as exc:
        # The model could not be trained (e.g. too little history). Fall back to a plain rule, clearly
        # labelled, without inventing a probability or confidence; the bot does not trade on it.
        logger.warning('forecaster_unavailable symbol=%s error=%s', symbol, exc)
        model_fallback = True
        probability_up = None
        confidence = None
        price_change_pct = float(asset.get('price_change_pct') or 0.0)
        outlook = _trend_outlook(price_change_pct, latest_sentiment)
        if latest_sentiment > 0.1 and price_change_pct > 0:
            signal = 'BUY'
        elif latest_sentiment < -0.1 and price_change_pct < 0:
            signal = 'SELL'
        else:
            signal = 'HOLD'
        rationale = [
            "AI model unavailable for this asset; this is a rule-based estimate (price trend + news sentiment).",
            f"Outlook: {outlook}.",
            f"Recent price change is {price_change_pct:.2f}%.",
            f"Average news sentiment score is {latest_sentiment:.2f}.",
        ]

    recommendation_map = {
        'BUY': 'Bullish',
        'SELL': 'Bearish',
        'HOLD': 'Neutral',
    }

    closes = df['Close'].tail(30)
    price_history = [
        {'date': idx.date().isoformat() if hasattr(idx, 'date') else str(idx)[:10], 'close': round(float(value), 4)}
        for idx, value in closes.items()
    ]
    daily_returns = df['Close'].pct_change().dropna().tail(30)
    # Typical daily move (standard deviation of daily returns, %), used to size suggested stops.
    volatility_pct = round(float(daily_returns.std() * 100), 3) if len(daily_returns) > 1 else None

    return {
        'symbol': symbol,
        'display_name': symbol,
        'signal': signal,
        'recommendation': recommendation_map.get(signal, signal),
        'confidence': confidence,
        'probability_up': probability_up,
        'latest_price': float(asset.get('price') or 0.0),
        'price_change_pct': float(asset.get('price_change_pct') or 0.0),
        'latest_sentiment_score': latest_sentiment,
        'trend_summary': _trend_outlook(float(asset.get('price_change_pct') or 0.0), latest_sentiment),
        'rationale': rationale,
        # True when the AI model could not run and the signal is a rule-based estimate.
        'model_fallback': model_fallback,
        # Per-day FinBERT sentiment of the articles behind this insight (Analytics heatmap).
        'news_sentiment_daily': _daily_sentiment_summary(scored),
        # For the Insights page: why the model decided (SHAP), recent prices, volatility and the
        # probability thresholds the signal is based on.
        'drivers': drivers,
        'price_history': price_history,
        'volatility_pct': volatility_pct,
        'thresholds': thresholds,
        # Identifies this signal so the trading bot can cap how often it acts on the same one.
        'generated_at': datetime.now(timezone.utc).isoformat(),
    }


class SentimentRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=10000)


class TrainV2Request(BaseModel):
    symbol: str
    lookback_days: int = Field(60, ge=7, le=365)
    max_articles: int = Field(60, ge=5, le=200)


class SignalV2Request(BaseModel):
    symbol: str
    period: str = "90d"
    max_articles: int = Field(60, ge=0, le=200)


class ExplainLLMRequest(BaseModel):
    user_preference: str = Field("open-source", description="Either 'open-source' or 'custom'")
    shap_context: Dict[str, Any]
    prompt: str = Field(..., min_length=1, max_length=2000)


class GuestExplainRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    prompt: str = Field(..., min_length=1, max_length=2000)
    range: str = Field('1mo', description='Chart range used to build public context')


class BotEvaluateRequest(BaseModel):
    signal: str
    current_price: float
    risk_profile: Dict[str, Any]


class WatchlistAssistantRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=16)
    prompt: str = Field(..., min_length=1, max_length=2000)
    range: str = Field('1mo')
    user_preference: str = Field('open-source')


@router.post("/sentiment/analyze")
async def v2_sentiment_analyze(req: SentimentRequest, user=Depends(get_current_user)):
    try:
        # FinBERT inference is CPU-bound; run in threadpool.
        return await run_in_threadpool(_SENTIMENT_ANALYZER.analyze_text, req.text)
    except NewsSentimentAnalyzerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/forecaster/train")
async def v2_forecaster_train(req: TrainV2Request, user=Depends(get_current_user)):
    symbol = _normalize_symbol(req.symbol)
    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=401, detail="unauthorized")

    news_creds = await _get_news_credentials(owner)
    api_key = news_creds.get('primary_key')
    used_newsapi = bool(api_key)

    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=int(req.lookback_days))

    try:
        # Fetching can block (network + yfinance); use threadpool.
        articles = []
        if api_key:
            articles = await run_in_threadpool(
                fetch_news_for_symbol,
                api_key,
                symbol,
                from_dt,
                to_dt,
                100,
                1,
                symbol,
                news_creds.get('primary_provider') or 'newsapi',
                news_creds.get('fallback_key'),
                news_creds.get('fallback_provider'),
            )
        df = await run_in_threadpool(fetch_ohlcv, symbol, f"{int(req.lookback_days)}d", "1d")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"data_fetch_failed: {e}")

    if df is None or getattr(df, "empty", True):
        raise HTTPException(status_code=400, detail="no price data")

    try:
        scored = await run_in_threadpool(
            _score_articles_finbert,
            _SENTIMENT_ANALYZER,
            articles,
            max_articles=int(req.max_articles),
        )
        if scored:
            record_finbert_batch(owner=owner, scored_items=scored, provider=news_creds.get('primary_provider') or 'newsapi')
        sentiment_series = await run_in_threadpool(_build_sentiment_series, df, scored)

        forecaster = MarketForecaster()
        train_report = await run_in_threadpool(forecaster.train_model, df, sentiment_series)

        _FORECASTERS[(owner, symbol)] = forecaster

        # Small summary for transparency
        last_sent = float(sentiment_series.iloc[-1]) if len(sentiment_series) else 0.0
        label_counts: Dict[str, int] = {}
        for a in scored:
            lbl = str(a.get("label") or "Unknown")
            label_counts[lbl] = label_counts.get(lbl, 0) + 1

        return {
            "symbol": symbol,
            "status": "trained",
            "train_report": train_report,
            "articles_scored": int(len(scored)),
            "label_counts": label_counts,
            "latest_sentiment_score": last_sent,
            "used_newsapi": used_newsapi,
        }
    except (MarketForecasterError, NewsSentimentAnalyzerError) as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"train_failed: {e}")


def _get_forecaster(owner: str, symbol: str) -> MarketForecaster:
    fc = _FORECASTERS.get((owner, symbol))
    if fc is None:
        raise HTTPException(status_code=404, detail="model_not_trained")
    return fc


@router.post("/forecaster/signal")
async def v2_forecaster_signal(req: SignalV2Request, user=Depends(get_current_user)):
    symbol = _normalize_symbol(req.symbol)
    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=401, detail="unauthorized")

    forecaster = _get_forecaster(owner, symbol)

    # Best-effort: sentiment uses NewsAPI if available, otherwise defaults to 0.
    news_creds = await _get_news_credentials(owner)
    api_key = news_creds.get('primary_key')

    try:
        df = await run_in_threadpool(fetch_ohlcv, symbol, req.period, "1d")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"price_fetch_failed: {e}")

    if df is None or getattr(df, "empty", True):
        raise HTTPException(status_code=400, detail="no price data")

    scored: List[Dict[str, Any]] = []
    if api_key and int(req.max_articles) > 0:
        try:
            # pull recent news (last 7 days) to keep runtime bounded
            to_dt = datetime.utcnow()
            from_dt = to_dt - timedelta(days=7)
            articles = await run_in_threadpool(
                fetch_news_for_symbol,
                api_key,
                symbol,
                from_dt,
                to_dt,
                100,
                1,
                symbol,
                news_creds.get('primary_provider') or 'newsapi',
                news_creds.get('fallback_key'),
                news_creds.get('fallback_provider'),
            )
            scored = await run_in_threadpool(
                _score_articles_finbert,
                _SENTIMENT_ANALYZER,
                articles,
                max_articles=int(req.max_articles),
            )
            if scored:
                record_finbert_batch(owner=owner, scored_items=scored, provider=news_creds.get('primary_provider') or 'newsapi')
        except Exception as e:
            logger.warning("News sentiment fetch/score failed; proceeding with neutral sentiment. Error=%s", e)
            scored = []

    sentiment_series = await run_in_threadpool(_build_sentiment_series, df, scored)
    df2 = df.copy()
    df2["sentiment_score"] = sentiment_series

    try:
        signal = await run_in_threadpool(forecaster.generate_signal, df2)

        # Provide probability (no SHAP) for quick transparency.
        # Uses the forecaster's internal model.
        X_row = forecaster._to_feature_row(df2)  # type: ignore[attr-defined]
        prob_up = float(forecaster.model.predict_proba(X_row)[0, 1])  # type: ignore[union-attr]
        raw_score = (prob_up * 2.0) - 1.0
        record_forecaster_event(
            owner,
            symbol=symbol,
            raw_forecast_score=raw_score,
            bull_threshold=0.2,
            bear_threshold=-0.2,
            shap_snippet=f"Signal={signal}; ProbUp={prob_up:.4f}; Sentiment={float(sentiment_series.iloc[-1]) if len(sentiment_series) else 0.0:.3f}",
        )

        return {
            "symbol": symbol,
            "signal": signal,
            "probability_up": prob_up,
            "latest_close": float(df2["Close"].iloc[-1]),
            "latest_sentiment_score": float(sentiment_series.iloc[-1]) if len(sentiment_series) else 0.0,
            "articles_scored": int(len(scored)),
        }
    except MarketForecasterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"signal_failed: {e}")


@router.post("/forecaster/shap")
async def v2_forecaster_shap(req: SignalV2Request, user=Depends(get_current_user)):
    symbol = _normalize_symbol(req.symbol)
    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=401, detail="unauthorized")

    forecaster = _get_forecaster(owner, symbol)

    news_creds = await _get_news_credentials(owner)
    api_key = news_creds.get('primary_key')

    try:
        df = await run_in_threadpool(fetch_ohlcv, symbol, req.period, "1d")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"price_fetch_failed: {e}")

    if df is None or getattr(df, "empty", True):
        raise HTTPException(status_code=400, detail="no price data")

    scored: List[Dict[str, Any]] = []
    if api_key and int(req.max_articles) > 0:
        try:
            to_dt = datetime.utcnow()
            from_dt = to_dt - timedelta(days=7)
            articles = await run_in_threadpool(
                fetch_news_for_symbol,
                api_key,
                symbol,
                from_dt,
                to_dt,
                100,
                1,
                symbol,
                news_creds.get('primary_provider') or 'newsapi',
                news_creds.get('fallback_key'),
                news_creds.get('fallback_provider'),
            )
            scored = await run_in_threadpool(
                _score_articles_finbert,
                _SENTIMENT_ANALYZER,
                articles,
                max_articles=int(req.max_articles),
            )
            if scored:
                record_finbert_batch(owner=owner, scored_items=scored, provider=news_creds.get('primary_provider') or 'newsapi')
        except Exception as e:
            logger.warning("News sentiment fetch/score failed; proceeding with neutral sentiment. Error=%s", e)
            scored = []

    sentiment_series = await run_in_threadpool(_build_sentiment_series, df, scored)
    df2 = df.copy()
    df2["sentiment_score"] = sentiment_series

    try:
        signal = await run_in_threadpool(forecaster.generate_signal, df2)
        shap_expl = await run_in_threadpool(forecaster.get_shap_explanation, df2)
        top_features = (shap_expl or {}).get('top_features') or []
        snippet = ' | '.join(
            [
                f"{feat.get('feature')}={feat.get('impact_pct')}%"
                for feat in top_features[:3]
            ]
        ) if top_features else f"Signal={signal}"
        X_row = forecaster._to_feature_row(df2)  # type: ignore[attr-defined]
        prob_up = float(forecaster.model.predict_proba(X_row)[0, 1])  # type: ignore[union-attr]
        record_forecaster_event(
            owner,
            symbol=symbol,
            raw_forecast_score=(prob_up * 2.0) - 1.0,
            bull_threshold=0.2,
            bear_threshold=-0.2,
            shap_snippet=snippet,
        )
        return {
            "symbol": symbol,
            "signal": signal,
            "shap": shap_expl,
            "latest_close": float(df2["Close"].iloc[-1]),
            "latest_sentiment_score": float(sentiment_series.iloc[-1]) if len(sentiment_series) else 0.0,
            "articles_scored": int(len(scored)),
        }
    except MarketForecasterError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"shap_failed: {e}")


@router.post("/llm/explain")
async def v2_llm_explain(req: ExplainLLMRequest, user=Depends(get_current_user)):
    # 'open-source' / 'custom' comes from DB in the long-term; for now it's passed in.
    pref = (req.user_preference or "").strip().lower()
    user_pref = "custom" if pref == "custom" else "open-source"

    owner = user.get('sub') or 'unknown'
    started = time.perf_counter()

    try:
        explanation = await _DUAL_LLM.generate_explanation(user_pref, req.shap_context, req.prompt)
        latency_ms = (time.perf_counter() - started) * 1000.0
        tokens = _estimate_tokens(req.prompt, explanation)
        record_llm_prompt(
            owner,
            symbol=str(req.shap_context.get('symbol') or '-'),
            prompt=req.prompt,
            model_used=_DUAL_LLM.last_model_used,
            latency_ms=latency_ms,
            prompt_tokens=tokens['prompt_tokens'],
            completion_tokens=tokens['completion_tokens'],
        )
        return {
            'model_used': _DUAL_LLM.last_model_used,
            'used_fallback': _DUAL_LLM.last_used_fallback,
            'explanation': explanation,
        }
    except DualLLMManagerError:
        fallback = _fallback_explanation(req.shap_context, req.prompt)
        latency_ms = (time.perf_counter() - started) * 1000.0
        tokens = _estimate_tokens(req.prompt, str(fallback.get('explanation') or ''))
        record_llm_prompt(
            owner,
            symbol=str(req.shap_context.get('symbol') or '-'),
            prompt=req.prompt,
            model_used=str(fallback.get('model_used') or 'rule-based-fallback'),
            latency_ms=latency_ms,
            prompt_tokens=tokens['prompt_tokens'],
            completion_tokens=tokens['completion_tokens'],
        )
        return fallback
    except Exception:
        fallback = _fallback_explanation(req.shap_context, req.prompt)
        latency_ms = (time.perf_counter() - started) * 1000.0
        tokens = _estimate_tokens(req.prompt, str(fallback.get('explanation') or ''))
        record_llm_prompt(
            owner,
            symbol=str(req.shap_context.get('symbol') or '-'),
            prompt=req.prompt,
            model_used=str(fallback.get('model_used') or 'rule-based-fallback'),
            latency_ms=latency_ms,
            prompt_tokens=tokens['prompt_tokens'],
            completion_tokens=tokens['completion_tokens'],
        )
        return fallback


@router.post("/public/explain")
async def v2_public_llm_explain(req: GuestExplainRequest, _: None = Depends(enforce_guest_llm_rate_limit)):
    symbol = _normalize_symbol(req.symbol)
    if symbol not in _GUEST_ALLOWED_SYMBOLS:
        raise HTTPException(status_code=403, detail='symbol_not_allowed')

    try:
        from app.api.analytics import get_asset_detail

        asset = await get_asset_detail(symbol, req.range)
        shap_context = _build_llm_asset_context(asset)
        try:
            explanation = await _DUAL_LLM.generate_explanation('open-source', shap_context, req.prompt)
            return {
                'model_used': _DUAL_LLM.last_model_used,
                'used_fallback': _DUAL_LLM.last_used_fallback,
                'explanation': explanation,
            }
        except Exception:
            return _fallback_explanation(shap_context, req.prompt)
    except HTTPException:
        raise
    except DualLLMManagerError as e:
        return _fallback_explanation({'symbol': symbol}, req.prompt)
    except Exception as e:
        return _fallback_explanation({'symbol': symbol}, req.prompt)


@router.post("/bot/evaluate")
async def v2_bot_evaluate(req: BotEvaluateRequest, user=Depends(get_current_user)):
    try:
        payload = await run_in_threadpool(
            _TRADING_BOT.evaluate_and_execute,
            req.signal,
            req.current_price,
            req.risk_profile,
        )
        return payload
    except RiskParameterViolation as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get('/watchlist/news')
async def watchlist_news(
    days: int = Query(default=30, ge=1, le=90),
    page_size: int = Query(default=6, ge=1, le=20),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    user=Depends(get_current_user),
):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=401, detail='unauthorized')

    symbols = await _get_watchlist_symbols(owner)
    if not symbols:
        return []

    db = get_database()
    news_creds = await _get_news_credentials(owner)
    payload = []
    for symbol in symbols:
        try:
            cached = await _read_news_cache(
                db,
                owner=owner,
                symbol=symbol,
                days=days,
                page=1,
                page_size=page_size,
                from_date=from_date,
                to_date=to_date,
                ttl_minutes=NEWS_CACHE_TTL_MINUTES,
            )
            if cached:
                payload.append(await _with_saved_articles(db, owner=owner, symbol=symbol, payload=cached))
                continue

            fresh = await _build_watch_asset_news(
                symbol,
                news_creds,
                owner=owner,
                days=days,
                page=1,
                page_size=page_size,
                from_date=from_date,
                to_date=to_date,
            )
            await _write_news_cache(
                db,
                owner=owner,
                symbol=symbol,
                days=days,
                page=1,
                page_size=page_size,
                from_date=from_date,
                to_date=to_date,
                payload=fresh,
            )
            payload.append(await _with_saved_articles(db, owner=owner, symbol=symbol, payload=fresh))
        except Exception as e:
            logger.warning('watchlist_news_failed symbol=%s error=%s', symbol, e)
            payload.append({
                'symbol': symbol,
                'display_name': symbol,
                'price': 0.0,
                'price_change_pct': 0.0,
                'avg_sentiment': 0.0,
                'sentiment_label': 'Unavailable',
                'articles_count': 0,
                'articles': [],
                'page': 1,
                'page_size': page_size,
                'lookback_days': days,
                'from_date': from_date,
                'to_date': to_date,
                'has_more': False,
            })
    return payload


@router.get('/watchlist/news/{symbol}')
async def watch_asset_news(
    symbol: str,
    days: int = Query(default=30, ge=1, le=180),
    page: int = Query(default=1, ge=1, le=20),
    page_size: int = Query(default=6, ge=1, le=20),
    from_date: Optional[str] = Query(default=None),
    to_date: Optional[str] = Query(default=None),
    user=Depends(get_current_user),
):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=401, detail='unauthorized')

    normalized_symbol = _normalize_symbol(symbol)
    symbols = await _get_watchlist_symbols(owner)
    if normalized_symbol not in symbols:
        raise HTTPException(status_code=403, detail='symbol_not_in_watchlist')

    db = get_database()
    cached = await _read_news_cache(
        db,
        owner=owner,
        symbol=normalized_symbol,
        days=days,
        page=page,
        page_size=page_size,
        from_date=from_date,
        to_date=to_date,
        ttl_minutes=NEWS_CACHE_TTL_MINUTES,
    )
    if cached:
        return await _with_saved_articles(db, owner=owner, symbol=normalized_symbol, payload=cached)

    news_creds = await _get_news_credentials(owner)
    fresh = await _build_watch_asset_news(
        normalized_symbol,
        news_creds,
        owner=owner,
        days=days,
        page=page,
        page_size=page_size,
        from_date=from_date,
        to_date=to_date,
    )
    await _write_news_cache(
        db,
        owner=owner,
        symbol=normalized_symbol,
        days=days,
        page=page,
        page_size=page_size,
        from_date=from_date,
        to_date=to_date,
        payload=fresh,
    )
    return await _with_saved_articles(db, owner=owner, symbol=normalized_symbol, payload=fresh)


@router.get('/watchlist/insights')
async def watchlist_insights(user=Depends(get_current_user)):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=401, detail='unauthorized')

    symbols = await _get_watchlist_symbols(owner)
    if not symbols:
        return []

    db = get_database()
    api_key = await _get_newsapi_key_for_owner(owner)
    payload = []
    for symbol in symbols:
        try:
            cached = await _read_insight_cache(
                db,
                owner=owner,
                symbol=symbol,
                ttl_minutes=INSIGHTS_CACHE_TTL_MINUTES,
            )
            if cached:
                payload.append(cached)
                continue

            fresh = await _build_watch_asset_insight(symbol, owner, api_key)
            await _write_insight_cache(db, owner=owner, symbol=symbol, payload=fresh)
            payload.append(fresh)
        except Exception as e:
            logger.warning('watchlist_insight_failed symbol=%s error=%s', symbol, e)
            # No insight could be built: report it as unavailable rather than a neutral-looking signal.
            payload.append({
                'symbol': symbol,
                'display_name': symbol,
                'signal': 'N/A',
                'recommendation': 'Unavailable',
                'confidence': None,
                'probability_up': None,
                'latest_price': None,
                'price_change_pct': None,
                'latest_sentiment_score': None,
                'trend_summary': 'Insight generation is temporarily unavailable.',
                'rationale': ['Price history could not be loaded for this symbol yet.'],
                'unavailable': True,
            })
    return payload


async def refresh_watchlist_cache_for_owner(
    owner: str,
    *,
    news_ttl_minutes: float = NEWS_CACHE_TTL_MINUTES,
    insights_ttl_minutes: float = INSIGHTS_CACHE_TTL_MINUTES,
    alert_callback: Optional[Callable[[str, str, str, Dict[str, Any]], Any]] = None,
    insight_callback: Optional[Callable[[str, str, Dict[str, Any]], Any]] = None,
) -> Dict[str, Any]:
    """Refresh cached watchlist news/insights for an owner, emit alerts on strong BUY/SELL signals,
    and pass every insight to `insight_callback` (the trading bot)."""
    symbols = await _get_watchlist_symbols(owner)
    if not symbols:
        return {'owner': owner, 'symbols': 0, 'news_refreshed': 0, 'insights_refreshed': 0, 'alerts': []}

    db = get_database()
    news_creds = await _get_news_credentials(owner)
    api_key = await _get_newsapi_key_for_owner(owner)

    news_refreshed = 0
    insights_refreshed = 0
    alerts: List[Dict[str, Any]] = []

    for symbol in symbols:
        try:
            cached_news = await _read_news_cache(
                db,
                owner=owner,
                symbol=symbol,
                days=7,
                page=1,
                page_size=6,
                from_date=None,
                to_date=None,
                ttl_minutes=news_ttl_minutes,
            )
            if not cached_news:
                news_payload = await _build_watch_asset_news(
                    symbol,
                    news_creds,
                    owner=owner,
                    days=7,
                    page=1,
                    page_size=6,
                    from_date=None,
                    to_date=None,
                )
                await _write_news_cache(
                    db,
                    owner=owner,
                    symbol=symbol,
                    days=7,
                    page=1,
                    page_size=6,
                    from_date=None,
                    to_date=None,
                    payload=news_payload,
                )
                news_refreshed += 1
        except Exception as e:
            logger.warning('background_news_refresh_failed owner=%s symbol=%s error=%s', owner, symbol, e)

        try:
            cached_insight = await _read_insight_cache(
                db,
                owner=owner,
                symbol=symbol,
                ttl_minutes=insights_ttl_minutes,
            )
            # Insights cached before 'generated_at' existed are rebuilt once so they carry a signal id.
            if cached_insight and cached_insight.get('generated_at'):
                insight_payload = cached_insight
            else:
                insight_payload = await _build_watch_asset_insight(
                    symbol, owner, api_key, news_ttl_minutes=news_ttl_minutes
                )
                await _write_insight_cache(db, owner=owner, symbol=symbol, payload=insight_payload)
                insights_refreshed += 1

            signal = str(insight_payload.get('signal') or '').upper()
            confidence = int(insight_payload.get('confidence') or 0)
            if signal in {'BUY', 'SELL'} and confidence >= BOT_MIN_SIGNAL_CONFIDENCE:
                alert_payload = {
                    'owner': owner,
                    'symbol': symbol,
                    'signal': signal,
                    'generated_at': insight_payload.get('generated_at'),
                    'confidence': confidence,
                    'probability_up': insight_payload.get('probability_up'),
                    'latest_price': insight_payload.get('latest_price'),
                    'trend_summary': insight_payload.get('trend_summary'),
                }
                alerts.append(alert_payload)
                if alert_callback is not None:
                    result = alert_callback(owner, symbol, signal, alert_payload)
                    if hasattr(result, '__await__'):
                        await result

            # Every insight (including HOLD) goes to the bot; its per-asset strategy decides what to do.
            if insight_callback is not None:
                result = insight_callback(owner, symbol, insight_payload)
                if hasattr(result, '__await__'):
                    await result
        except Exception as e:
            logger.warning('background_insight_refresh_failed owner=%s symbol=%s error=%s', owner, symbol, e)

    return {
        'owner': owner,
        'symbols': len(symbols),
        'news_refreshed': news_refreshed,
        'insights_refreshed': insights_refreshed,
        'alerts': alerts,
    }


@router.post('/assistant/explain')
async def watchlist_assistant_explain(req: WatchlistAssistantRequest, user=Depends(get_current_user)):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=401, detail='unauthorized')

    symbol = _normalize_symbol(req.symbol)
    symbols = await _get_watchlist_symbols(owner)
    if symbol not in symbols:
        raise HTTPException(status_code=403, detail='symbol_not_in_watchlist')

    shap_context: Dict[str, Any] = {'symbol': symbol}
    try:
        from app.api.analytics import get_asset_detail

        asset = await get_asset_detail(symbol, req.range)
        built = _build_llm_asset_context(asset)
        if isinstance(built, dict):
            shap_context.update(built)
    except Exception as exc:
        logger.warning('assistant_asset_context_failed owner=%s symbol=%s error=%s', owner, symbol, exc)

    try:
        api_key = await _get_newsapi_key_for_owner(owner)
    except Exception as exc:
        logger.warning('assistant_newsapi_key_lookup_failed owner=%s symbol=%s error=%s', owner, symbol, exc)
        api_key = None

    try:
        db = get_database()
        insight = await _read_insight_cache(
            db,
            owner=owner,
            symbol=symbol,
            ttl_minutes=INSIGHTS_CACHE_TTL_MINUTES,
        )
        if not insight:
            insight = await _build_watch_asset_insight(symbol, owner, api_key)
            try:
                await _write_insight_cache(db, owner=owner, symbol=symbol, payload=insight)
            except Exception as cache_exc:
                logger.warning('assistant_insight_cache_write_failed owner=%s symbol=%s error=%s', owner, symbol, cache_exc)

        if isinstance(insight, dict):
            shap_context.update(
                {
                    'signal': insight.get('signal'),
                    'recommendation': insight.get('recommendation'),
                    'latest_sentiment_score': insight.get('latest_sentiment_score'),
                    'probability_up': insight.get('probability_up'),
                    'latest_price': insight.get('latest_price'),
                    'price_change_pct': insight.get('price_change_pct'),
                }
            )
    except Exception as exc:
        logger.warning('assistant_insight_context_failed owner=%s symbol=%s error=%s', owner, symbol, exc)

    pref = (req.user_preference or '').strip().lower()
    user_pref = 'custom' if pref == 'custom' else 'open-source'
    started = time.perf_counter()

    try:
        explanation = await _generate_with_rescue(user_pref, symbol, shap_context, req.prompt)
        latency_ms = (time.perf_counter() - started) * 1000.0
        tokens = _estimate_tokens(req.prompt, explanation)
        record_llm_prompt(
            owner,
            symbol=symbol,
            prompt=req.prompt,
            model_used=_DUAL_LLM.last_model_used,
            latency_ms=latency_ms,
            prompt_tokens=tokens['prompt_tokens'],
            completion_tokens=tokens['completion_tokens'],
        )
        return {
            'model_used': _DUAL_LLM.last_model_used,
            'used_fallback': _DUAL_LLM.last_used_fallback,
            'explanation': explanation,
        }
    except Exception as llm_exc:
        logger.warning('assistant_llm_fallback owner=%s symbol=%s error=%s', owner, symbol, llm_exc)
        fallback = _fallback_explanation(shap_context, req.prompt)
        latency_ms = (time.perf_counter() - started) * 1000.0
        tokens = _estimate_tokens(req.prompt, str(fallback.get('explanation') or ''))
        record_llm_prompt(
            owner,
            symbol=symbol,
            prompt=req.prompt,
            model_used=str(fallback.get('model_used') or 'rule-based-fallback'),
            latency_ms=latency_ms,
            prompt_tokens=tokens['prompt_tokens'],
            completion_tokens=tokens['completion_tokens'],
        )
        return fallback

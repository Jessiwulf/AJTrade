"""Saved daily news sentiment for the Analytics heatmap (30-day backfill from Alpaca news).

Alpaca's news API (Benzinga) keeps years of history, tagged by ticker. A scheduler job fills the last
NEWS_HISTORY_DAYS days for every watchlist symbol Alpaca covers (US stocks, ETFs and crypto): up to
NEWS_HISTORY_PER_DAY articles per day, each scored once by FinBERT and stored. A finished day never
changes, so it is fetched once; today (and yesterday, until late articles are in) is re-fetched every
NEWS_HISTORY_REFRESH_MINUTES. Days are UTC days, like the rest of the heatmap.

The articles are public news, so the tables are shared by all members: a symbol's day is fetched once,
with the Alpaca keys of any member who watches it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time as monotonic_time
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx
from fastapi import HTTPException

from app.core import alpaca
from app.core.db import get_database

logger = logging.getLogger('ajtrade.news_history')


def _env_int(name: str, default: int, minimum: int) -> int:
    try:
        return max(int(float(os.environ.get(name, default))), minimum)
    except (TypeError, ValueError):
        return default


NEWS_HISTORY_ENABLED = (os.environ.get('AJTRADE_NEWS_HISTORY_ENABLED') or 'true').strip().lower() not in ('0', 'false', 'no', 'off')
NEWS_HISTORY_DAYS = _env_int('AJTRADE_NEWS_HISTORY_DAYS', 30, 1)
NEWS_HISTORY_PER_DAY = min(_env_int('AJTRADE_NEWS_HISTORY_PER_DAY', 10, 1), 50)  # Alpaca allows 50 per page
NEWS_HISTORY_REFRESH_MINUTES = _env_int('AJTRADE_NEWS_HISTORY_REFRESH_MINUTES', 30, 5)
# How long one scheduler tick may spend fetching and scoring, so FinBERT never hogs the CPU.
NEWS_HISTORY_TICK_SECONDS = _env_int('AJTRADE_NEWS_HISTORY_TICK_SECONDS', 25, 5)
NEWS_HISTORY_INTERVAL_SECONDS = _env_int('AJTRADE_NEWS_HISTORY_INTERVAL_SECONDS', 60, 15)
NEWS_HISTORY_RETENTION_DAYS = max(_env_int('AJTRADE_NEWS_HISTORY_RETENTION_DAYS', 45, 1), NEWS_HISTORY_DAYS)
ALPACA_DATA_URL = (os.environ.get('ALPACA_DATA_URL') or 'https://data.alpaca.markets').rstrip('/')

# A day counts as final once it was fetched this long after it ended (late articles are in).
FINAL_GRACE = timedelta(hours=1)
# Members whose keys were rejected for news are retried after this long.
ACCESS_RETRY = timedelta(hours=1)

_US_TICKER_RE = re.compile(r'^[A-Z]{1,5}(-[A-Z])?$')
_TAG_RE = re.compile(r'<[^>]+>')


class NewsAccessError(Exception):
    """The member's Alpaca keys can't read news (missing, rejected or no news access)."""


class NewsRateLimited(Exception):
    """Alpaca asked us to slow down; the tick stops and the next one carries on."""


# --- Symbols ----------------------------------------------------------------------------------
def news_symbol(symbol: str) -> Optional[str]:
    """The symbol Alpaca's news API tags articles with, or None when Alpaca has no news for it.

    Covers US stocks and ETFs (BRK-B -> BRK.B) and the crypto Alpaca trades (BTC-USD -> BTCUSD).
    Thai stocks (.BK), currencies (=X), gold (XAU-USD), indices (^) and other exchanges are not covered.
    """
    s = (symbol or '').strip().upper()
    if alpaca.is_crypto_symbol(s):
        base = s.rsplit('-', 1)[0]
        return f'{base}USD' if base in alpaca.ALPACA_CRYPTO_BASES else None
    if _US_TICKER_RE.match(s):
        return alpaca.to_alpaca_symbol(s)
    return None


# --- Planning ---------------------------------------------------------------------------------
def days_to_fetch(fetched: Dict[date, datetime], *, now: datetime, days: int = NEWS_HISTORY_DAYS) -> List[date]:
    """Days in the window that still need a fetch, newest first.

    A day is skipped when it was fetched after it ended (+ FINAL_GRACE), or fetched recently.
    """
    today = now.astimezone(timezone.utc).date()
    refresh = timedelta(minutes=NEWS_HISTORY_REFRESH_MINUTES)
    out = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        fetched_at = fetched.get(day)
        if fetched_at is None:
            out.append(day)
            continue
        day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=timezone.utc)
        if fetched_at >= day_end + FINAL_GRACE:
            continue
        if now - fetched_at >= refresh:
            out.append(day)
    return out


def plan_tasks(symbol_days: Dict[str, List[date]]) -> List[Tuple[str, date]]:
    """Interleaves symbols so the heatmap fills from today backwards for every asset at once."""
    tasks = [(symbol, day) for symbol, days in symbol_days.items() for day in days]
    return sorted(tasks, key=lambda task: (-task[1].toordinal(), task[0]))


# --- Daily summaries (same shape as the AI insight's news_sentiment_daily) ----------------------
def _score_bucket(score: float) -> str:
    if score > 0.1:
        return 'positive'
    if score < -0.1:
        return 'negative'
    return 'neutral'


def summarize_days(articles: Iterable[Dict[str, Any]], *, max_headlines: int = 3) -> List[Dict[str, Any]]:
    """Per-day average score, article counts and the strongest headlines, oldest day first."""
    days: Dict[str, Dict[str, Any]] = {}
    for article in articles:
        try:
            score = float(article['score'])
        except (KeyError, TypeError, ValueError):
            continue
        key = str(article.get('day'))[:10]
        bucket = days.setdefault(key, {'scores': [], 'headlines': []})
        bucket['scores'].append(score)
        if article.get('title'):
            bucket['headlines'].append(
                {'title': article.get('title'), 'source': article.get('source'), 'url': article.get('url'), 'score': round(score, 4)}
            )

    summary = []
    for key in sorted(days):
        scores = days[key]['scores']
        counts = {'positive': 0, 'negative': 0, 'neutral': 0}
        for score in scores:
            counts[_score_bucket(score)] += 1
        headlines = sorted(days[key]['headlines'], key=lambda h: abs(h['score']), reverse=True)[:max_headlines]
        summary.append({'date': key, 'avg': round(sum(scores) / len(scores), 4), 'count': len(scores), **counts, 'headlines': headlines})
    return summary


def merge_daily(*series: List[Dict[str, Any]], max_headlines: int = 3) -> List[Dict[str, Any]]:
    """Combines per-day summaries from different news sources (article-weighted average per day)."""
    merged: Dict[str, Dict[str, Any]] = {}
    for entries in series:
        for entry in entries or []:
            if not isinstance(entry, dict) or not entry.get('date'):
                continue
            key = str(entry['date'])[:10]
            try:
                avg = float(entry.get('avg'))
            except (TypeError, ValueError):
                continue
            count = max(int(entry.get('count') or 0), 1)
            current = merged.get(key)
            if current is None:
                merged[key] = {
                    'date': key,
                    'avg': round(avg, 4),
                    'count': count,
                    'positive': int(entry.get('positive') or 0),
                    'negative': int(entry.get('negative') or 0),
                    'neutral': int(entry.get('neutral') or 0),
                    'headlines': list(entry.get('headlines') or []),
                }
                continue
            total = current['count'] + count
            current['avg'] = round((current['avg'] * current['count'] + avg * count) / total, 4)
            current['count'] = total
            for field in ('positive', 'negative', 'neutral'):
                current[field] += int(entry.get(field) or 0)
            current['headlines'].extend(entry.get('headlines') or [])

    out = []
    for key in sorted(merged):
        entry = merged[key]
        seen = set()
        headlines = []
        for headline in sorted(entry['headlines'], key=lambda h: abs(float(h.get('score') or 0)), reverse=True):
            ident = headline.get('url') or headline.get('title')
            if ident in seen:
                continue
            seen.add(ident)
            headlines.append(headline)
        entry['headlines'] = headlines[:max_headlines]
        out.append(entry)
    return out


def recent_average(daily: List[Dict[str, Any]], *, now: datetime, days: int = 7) -> Optional[Dict[str, Any]]:
    """Article-weighted sentiment over the last `days` days of a daily summary (None without news)."""
    since = (now.astimezone(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    recent = [d for d in daily if str(d.get('date')) >= since and d.get('count')]
    if not recent:
        return None
    total = sum(int(d['count']) for d in recent)
    return {
        'avg_sentiment': sum(float(d['avg']) * int(d['count']) for d in recent) / total,
        'positive_count': sum(int(d.get('positive') or 0) for d in recent),
        'negative_count': sum(int(d.get('negative') or 0) for d in recent),
        'neutral_count': sum(int(d.get('neutral') or 0) for d in recent),
        'total_articles': total,
        'date': max(str(d['date']) for d in recent),
    }


# --- Storage ----------------------------------------------------------------------------------
_schema_ready = False


async def ensure_schema() -> None:
    global _schema_ready
    if _schema_ready:
        return
    db = get_database()
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS news_history_articles ("
            "symbol text NOT NULL,"
            "article_id text NOT NULL,"
            "day date NOT NULL,"
            "published_at timestamptz NOT NULL,"
            "title text NOT NULL,"
            "source text,"
            "url text,"
            "score real NOT NULL,"
            "label text,"
            "created_at timestamptz NOT NULL DEFAULT now(),"
            "PRIMARY KEY (symbol, article_id)"
            ")"
        )
    )
    await db.execute(query="CREATE INDEX IF NOT EXISTS news_history_articles_symbol_day ON news_history_articles (symbol, day)")
    # One row per symbol and day that was fetched, including days with no articles.
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS news_history_days ("
            "symbol text NOT NULL,"
            "day date NOT NULL,"
            "fetched_at timestamptz NOT NULL,"
            "article_count integer NOT NULL DEFAULT 0,"
            "PRIMARY KEY (symbol, day)"
            ")"
        )
    )
    _schema_ready = True


async def daily_history(symbols: List[str], *, days: int = NEWS_HISTORY_DAYS, now: Optional[datetime] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Saved per-day sentiment for each symbol over the last `days` days."""
    wanted = {str(s).upper() for s in symbols if news_symbol(s)}
    if not wanted:
        return {}
    await ensure_schema()
    now = now or datetime.now(timezone.utc)
    since = now.date() - timedelta(days=days - 1)
    rows = await get_database().fetch_all(
        query="SELECT symbol, day, title, source, url, score FROM news_history_articles WHERE day >= :since",
        values={'since': since},
    )
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        symbol = row['symbol']
        if symbol in wanted:
            grouped.setdefault(symbol, []).append(
                {'day': row['day'].isoformat(), 'title': row['title'], 'source': row['source'], 'url': row['url'], 'score': row['score']}
            )
    return {symbol: summarize_days(items) for symbol, items in grouped.items()}


# --- Alpaca news ------------------------------------------------------------------------------
def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


async def fetch_day_news(client: httpx.AsyncClient, credentials: Tuple[str, str], news_sym: str, day: date) -> List[Dict[str, Any]]:
    """Up to NEWS_HISTORY_PER_DAY of the day's newest articles for one symbol."""
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    response = await client.get(
        f'{ALPACA_DATA_URL}/v1beta1/news',
        params={
            'symbols': news_sym,
            'start': _iso_z(start),
            'end': _iso_z(start + timedelta(days=1)),
            'limit': NEWS_HISTORY_PER_DAY,
            'sort': 'desc',
            'include_content': 'false',
        },
        headers={'APCA-API-KEY-ID': credentials[0], 'APCA-API-SECRET-KEY': credentials[1], 'accept': 'application/json'},
    )
    if response.status_code in (401, 403):
        raise NewsAccessError(f'Alpaca news access denied ({response.status_code})')
    if response.status_code == 429:
        raise NewsRateLimited()
    response.raise_for_status()
    payload = response.json() or {}
    return [item for item in (payload.get('news') or []) if isinstance(item, dict)]


def _article_text(item: Dict[str, Any]) -> str:
    headline = str(item.get('headline') or '').strip()
    summary = ' '.join(_TAG_RE.sub(' ', str(item.get('summary') or '')).split())
    return f'{headline}. {summary}' if summary else headline


def _score_articles(analyzer: Any, items: List[Dict[str, Any]]) -> List[Tuple[Dict[str, Any], float, Optional[str]]]:
    scored = []
    for item in items:
        text = _article_text(item)
        if not text:
            continue
        try:
            result = analyzer.analyze_text(text)
            scored.append((item, float(result.get('score', 0.0)), result.get('label')))
        except Exception as exc:  # one bad article shouldn't stop the day
            logger.debug('news_history_score_failed id=%s error=%s', item.get('id'), exc)
    return scored


def _published_at(item: Dict[str, Any]) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(item.get('created_at')).replace('Z', '+00:00')).astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


async def _store_day(db, symbol: str, day: date, items: List[Dict[str, Any]], analyzer: Any, now: datetime) -> int:
    """Scores the articles not stored yet, saves them and marks the day fetched. Returns new articles."""
    existing = await db.fetch_all(
        query="SELECT article_id FROM news_history_articles WHERE symbol = :symbol AND day = :day",
        values={'symbol': symbol, 'day': day},
    )
    known = {row['article_id'] for row in existing}
    fresh = [item for item in items if str(item.get('id')) not in known and _published_at(item)]
    scored = await asyncio.to_thread(_score_articles, analyzer, fresh) if fresh else []

    rows = [
        {
            'symbol': symbol,
            'article_id': str(item.get('id')),
            'day': day,
            'published_at': _published_at(item),
            'title': str(item.get('headline') or '')[:500] or '(untitled)',
            'source': (str(item.get('source') or '').strip().title() or 'Benzinga')[:80],
            'url': item.get('url'),
            'score': score,
            'label': label,
        }
        for item, score, label in scored
    ]
    if rows:  # one multi-row INSERT: a round trip per day, not per article
        columns = list(rows[0])
        placeholders = ', '.join('(' + ', '.join(f':{col}_{i}' for col in columns) + ')' for i in range(len(rows)))
        await db.execute(
            query=(
                f"INSERT INTO news_history_articles ({', '.join(columns)}) VALUES {placeholders} "
                "ON CONFLICT (symbol, article_id) DO NOTHING"
            ),
            values={f'{col}_{i}': row[col] for i, row in enumerate(rows) for col in columns},
        )

    await db.execute(
        query=(
            "INSERT INTO news_history_days (symbol, day, fetched_at, article_count) "
            "VALUES (:symbol, :day, :now, (SELECT count(*) FROM news_history_articles WHERE symbol = :symbol AND day = :day)) "
            "ON CONFLICT (symbol, day) DO UPDATE SET fetched_at = EXCLUDED.fetched_at, article_count = EXCLUDED.article_count"
        ),
        values={'symbol': symbol, 'day': day, 'now': now},
    )
    return len(scored)


# --- Scheduler tick ---------------------------------------------------------------------------
# owner -> time their keys were rejected for news (retried after ACCESS_RETRY).
_denied_owners: Dict[str, datetime] = {}


async def _credentials_for(owner: str, cache: Dict[str, Optional[Tuple[str, str]]], now: datetime) -> Optional[Tuple[str, str]]:
    if owner in cache:
        return cache[owner]
    denied_at = _denied_owners.get(owner)
    if denied_at and now - denied_at < ACCESS_RETRY:
        cache[owner] = None
        return None
    try:
        key_id, secret_key, _base_url = await alpaca.get_owner_alpaca_credentials(owner)
        cache[owner] = (key_id, secret_key)
    except HTTPException:
        cache[owner] = None  # no Alpaca keys saved
    except Exception as exc:
        logger.warning('news_history_credentials_failed owner=%s error=%s', owner, exc)
        cache[owner] = None
    return cache[owner]


async def backfill_tick(*, analyzer: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """One scheduler tick: fetches and scores the missing days, newest first, within the time budget."""
    if not NEWS_HISTORY_ENABLED:
        return {'enabled': False}
    started = monotonic_time.monotonic()
    now = now or datetime.now(timezone.utc)
    await ensure_schema()
    db = get_database()

    # Which members watch which Alpaca-covered symbols (Settrade's Thai stocks have no Alpaca news).
    from app.core import brokers

    watchers: Dict[str, List[str]] = {}
    for row in await db.fetch_all(query="SELECT owner, symbol FROM watchlists WHERE owner IS NOT NULL ORDER BY created_at"):
        symbol = str(row['symbol'] or '').upper()
        if news_symbol(symbol):
            watchers.setdefault(symbol, [])
            if str(row['owner']) not in watchers[symbol]:
                watchers[symbol].append(str(row['owner']))
    for symbol in list(watchers):
        # A bare ticker can be a Thai SET stock (e.g. one only Yahoo's Thai listing knows).
        if await brokers.provider_for_symbol(symbol) != brokers.ALPACA:
            del watchers[symbol]
    if not watchers:
        return {'symbols': 0, 'days': 0, 'articles': 0}

    since = now.date() - timedelta(days=NEWS_HISTORY_DAYS - 1)
    fetched: Dict[str, Dict[date, datetime]] = {symbol: {} for symbol in watchers}
    for row in await db.fetch_all(query="SELECT symbol, day, fetched_at FROM news_history_days WHERE day >= :since", values={'since': since}):
        if row['symbol'] in fetched:
            fetched[row['symbol']][row['day']] = row['fetched_at']
    tasks = plan_tasks({symbol: days_to_fetch(state, now=now) for symbol, state in fetched.items()})
    if not tasks:
        return {'symbols': len(watchers), 'days': 0, 'articles': 0, 'pending': 0}

    if analyzer is None:
        from app.api.ml_v2 import _SENTIMENT_ANALYZER as analyzer  # lazy: loads FinBERT

    credentials: Dict[str, Optional[Tuple[str, str]]] = {}
    days_done = articles = 0
    no_keys: set = set()
    failed: set = set()
    stop_reason = None
    async with httpx.AsyncClient(timeout=20.0) as client:
        for symbol, day in tasks:
            if monotonic_time.monotonic() - started > NEWS_HISTORY_TICK_SECONDS:
                stop_reason = 'time budget'
                break
            if symbol in no_keys or symbol in failed:
                continue
            items = None
            try:
                for owner in watchers[symbol]:
                    creds = await _credentials_for(owner, credentials, now)
                    if not creds:
                        continue
                    try:
                        items = await fetch_day_news(client, creds, news_symbol(symbol), day)
                        break
                    except NewsAccessError as exc:
                        logger.warning('news_history_access_denied owner=%s error=%s', owner, exc)
                        _denied_owners[owner] = now
                        credentials[owner] = None
            except NewsRateLimited:
                stop_reason = 'rate limited'
                break
            except httpx.HTTPError as exc:
                # Network trouble or an Alpaca error for this symbol: try it again next tick.
                logger.warning('news_history_fetch_failed symbol=%s day=%s error=%s', symbol, day, exc)
                failed.add(symbol)
                continue
            if items is None:
                no_keys.add(symbol)  # nobody watching it has working Alpaca keys
                continue
            articles += await _store_day(db, symbol, day, items, analyzer, datetime.now(timezone.utc))
            days_done += 1

    await db.execute(
        query="DELETE FROM news_history_articles WHERE day < :cutoff",
        values={'cutoff': now.date() - timedelta(days=NEWS_HISTORY_RETENTION_DAYS)},
    )
    await db.execute(
        query="DELETE FROM news_history_days WHERE day < :cutoff",
        values={'cutoff': now.date() - timedelta(days=NEWS_HISTORY_RETENTION_DAYS)},
    )
    return {
        'symbols': len(watchers),
        'days': days_done,
        'articles': articles,
        'pending': len(tasks) - days_done,
        'no_keys': sorted(no_keys),
        'failed': sorted(failed),
        'stopped': stop_reason,
    }

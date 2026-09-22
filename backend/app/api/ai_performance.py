from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_current_user
from app.core.db import get_database

router = APIRouter()

_TELEMETRY_SCHEMA_READY = False


async def _ensure_telemetry_schema() -> None:
    global _TELEMETRY_SCHEMA_READY
    if _TELEMETRY_SCHEMA_READY:
        return

    db = get_database()
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS finbert_telemetry_events ("
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            "owner_id uuid NOT NULL,"
            "provider text NOT NULL DEFAULT 'newsapi',"
            "article_count int NOT NULL DEFAULT 0,"
            "positive_count int NOT NULL DEFAULT 0,"
            "neutral_count int NOT NULL DEFAULT 0,"
            "negative_count int NOT NULL DEFAULT 0,"
            "created_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )
    )
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS forecaster_telemetry_events ("
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            "owner_id uuid NOT NULL,"
            "asset_symbol text NOT NULL,"
            "raw_forecast_score numeric(12,6) NOT NULL DEFAULT 0,"
            "bull_threshold numeric(12,6) NOT NULL DEFAULT 0.2,"
            "bear_threshold numeric(12,6) NOT NULL DEFAULT -0.2,"
            "treeshap_log text,"
            "created_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )
    )
    await db.execute(
        query=(
            "CREATE TABLE IF NOT EXISTS llm_telemetry_events ("
            "id uuid PRIMARY KEY DEFAULT gen_random_uuid(),"
            "owner_id uuid NOT NULL,"
            "asset_symbol text,"
            "prompt text NOT NULL,"
            "model_used text NOT NULL DEFAULT 'unknown',"
            "latency_ms numeric(18,4) NOT NULL DEFAULT 0,"
            "prompt_tokens int NOT NULL DEFAULT 0,"
            "completion_tokens int NOT NULL DEFAULT 0,"
            "total_tokens int NOT NULL DEFAULT 0,"
            "created_at timestamptz NOT NULL DEFAULT now()"
            ")"
        )
    )
    await db.execute(query="CREATE INDEX IF NOT EXISTS idx_finbert_telemetry_owner_time ON finbert_telemetry_events(owner_id, created_at DESC)")
    await db.execute(query="CREATE INDEX IF NOT EXISTS idx_forecaster_telemetry_owner_time ON forecaster_telemetry_events(owner_id, created_at DESC)")
    await db.execute(query="CREATE INDEX IF NOT EXISTS idx_llm_telemetry_owner_time ON llm_telemetry_events(owner_id, created_at DESC)")
    _TELEMETRY_SCHEMA_READY = True


def _spawn_background(coro) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(coro)


async def _persist_finbert_event(
    owner: str,
    *,
    provider: str,
    article_count: int,
    positive_count: int,
    neutral_count: int,
    negative_count: int,
) -> None:
    try:
        await _ensure_telemetry_schema()
        db = get_database()
        await db.execute(
            query=(
                "INSERT INTO finbert_telemetry_events "
                "(owner_id, provider, article_count, positive_count, neutral_count, negative_count) "
                "VALUES (:owner_id, :provider, :article_count, :positive_count, :neutral_count, :negative_count)"
            ),
            values={
                'owner_id': owner,
                'provider': str(provider or 'newsapi').strip().lower(),
                'article_count': int(article_count),
                'positive_count': int(positive_count),
                'neutral_count': int(neutral_count),
                'negative_count': int(negative_count),
            },
        )
    except Exception:
        return


async def _persist_forecaster_event(
    owner: str,
    *,
    symbol: str,
    raw_forecast_score: float,
    bull_threshold: float,
    bear_threshold: float,
    shap_snippet: str,
) -> None:
    try:
        await _ensure_telemetry_schema()
        db = get_database()
        await db.execute(
            query=(
                "INSERT INTO forecaster_telemetry_events "
                "(owner_id, asset_symbol, raw_forecast_score, bull_threshold, bear_threshold, treeshap_log) "
                "VALUES (:owner_id, :asset_symbol, :raw_forecast_score, :bull_threshold, :bear_threshold, :treeshap_log)"
            ),
            values={
                'owner_id': owner,
                'asset_symbol': str(symbol or '-').strip().upper(),
                'raw_forecast_score': float(raw_forecast_score),
                'bull_threshold': float(bull_threshold),
                'bear_threshold': float(bear_threshold),
                'treeshap_log': shap_snippet,
            },
        )
    except Exception:
        return


async def _persist_llm_prompt(
    owner: str,
    *,
    symbol: Optional[str],
    prompt: str,
    model_used: Optional[str],
    latency_ms: float,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    try:
        await _ensure_telemetry_schema()
        db = get_database()
        await db.execute(
            query=(
                "INSERT INTO llm_telemetry_events "
                "(owner_id, asset_symbol, prompt, model_used, latency_ms, prompt_tokens, completion_tokens, total_tokens) "
                "VALUES (:owner_id, :asset_symbol, :prompt, :model_used, :latency_ms, :prompt_tokens, :completion_tokens, :total_tokens)"
            ),
            values={
                'owner_id': owner,
                'asset_symbol': str(symbol or '-').strip().upper(),
                'prompt': str(prompt or '').strip(),
                'model_used': str(model_used or 'unknown'),
                'latency_ms': float(latency_ms),
                'prompt_tokens': int(prompt_tokens),
                'completion_tokens': int(completion_tokens),
                'total_tokens': int(prompt_tokens + completion_tokens),
            },
        )
    except Exception:
        return


def record_llm_prompt(
    owner: str,
    *,
    symbol: Optional[str],
    prompt: str,
    model_used: Optional[str],
    latency_ms: float,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    _spawn_background(
        _persist_llm_prompt(
            owner,
            symbol=symbol,
            prompt=prompt,
            model_used=model_used,
            latency_ms=latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
    )


def record_forecaster_event(
    owner: str,
    *,
    symbol: str,
    raw_forecast_score: float,
    bull_threshold: float,
    bear_threshold: float,
    shap_snippet: str,
) -> None:
    _spawn_background(
        _persist_forecaster_event(
            owner,
            symbol=symbol,
            raw_forecast_score=raw_forecast_score,
            bull_threshold=bull_threshold,
            bear_threshold=bear_threshold,
            shap_snippet=shap_snippet,
        )
    )


def record_finbert_batch(owner: str, *, scored_items: List[Dict[str, Any]], provider: str = 'newsapi') -> None:
    if not scored_items:
        return

    positive = 0
    neutral = 0
    negative = 0
    for item in scored_items:
        label = str(item.get('label') or '').strip().lower()
        score = float(item.get('score') or 0.0)
        if label == 'positive' or score > 0.1:
            positive += 1
        elif label == 'negative' or score < -0.1:
            negative += 1
        else:
            neutral += 1

    _spawn_background(
        _persist_finbert_event(
            owner,
            provider=provider,
            article_count=len(scored_items),
            positive_count=positive,
            neutral_count=neutral,
            negative_count=negative,
        )
    )


def _estimate_tokens(prompt: str, completion: str = '') -> Dict[str, int]:
    # Practical heuristic for local telemetry when provider token usage is unavailable.
    prompt_tokens = max(1, int(len((prompt or '').strip()) / 4))
    completion_tokens = max(1, int(len((completion or '').strip()) / 4)) if completion else 0
    return {
        'prompt_tokens': prompt_tokens,
        'completion_tokens': completion_tokens,
    }


@router.get('/finbert')
async def get_finbert_monitor(user=Depends(get_current_user)):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=400, detail='Invalid user')

    await _ensure_telemetry_schema()
    db = get_database()

    telemetry_row = await db.fetch_one(
        query=(
            "SELECT COALESCE(SUM(article_count), 0) AS total_articles, "
            "COALESCE(SUM(positive_count), 0) AS positive_count, "
            "COALESCE(SUM(neutral_count), 0) AS neutral_count, "
            "COALESCE(SUM(negative_count), 0) AS negative_count, "
            "COALESCE(SUM(CASE WHEN provider = 'newsapi' THEN article_count ELSE 0 END), 0) AS newsapi_used, "
            "MAX(created_at) AS updated_at "
            "FROM finbert_telemetry_events WHERE owner_id = :owner"
        ),
        values={'owner': owner},
    )

    cache_row = await db.fetch_one(
        query=(
            "SELECT COALESCE(SUM(jsonb_array_length(payload->'articles')), 0) AS cached_articles "
            "FROM news_cache WHERE owner = :owner"
        ),
        values={'owner': owner},
    )

    total_articles = int((telemetry_row['total_articles'] if telemetry_row else 0) or 0)
    positive = int((telemetry_row['positive_count'] if telemetry_row else 0) or 0)
    neutral = int((telemetry_row['neutral_count'] if telemetry_row else 0) or 0)
    negative = int((telemetry_row['negative_count'] if telemetry_row else 0) or 0)
    newsapi_used = int((telemetry_row['newsapi_used'] if telemetry_row else 0) or 0)
    cached_articles = int((cache_row['cached_articles'] if cache_row else 0) or 0)
    total_articles = max(total_articles, cached_articles)

    if total_articles > 0 and (positive + neutral + negative) == 0:
        cache_rows = await db.fetch_all(
            query=(
                "SELECT payload FROM news_cache WHERE owner = :owner "
                "ORDER BY updated_at DESC LIMIT 200"
            ),
            values={'owner': owner},
        )
        for row in cache_rows:
            payload = row['payload'] if row and 'payload' in row else None
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = None
            if not isinstance(payload, dict):
                continue
            for article in payload.get('articles') or []:
                if not isinstance(article, dict):
                    continue
                label = str(article.get('sentiment_label') or '').strip().lower()
                score = float(article.get('sentiment_score') or 0.0)
                if label == 'positive' or score > 0.1:
                    positive += 1
                elif label == 'negative' or score < -0.1:
                    negative += 1
                else:
                    neutral += 1

    updated_at_value = telemetry_row['updated_at'] if telemetry_row and 'updated_at' in telemetry_row else None
    updated_at_epoch = updated_at_value.timestamp() if updated_at_value else None
    newsapi_limit = 1000

    distribution_total = max(total_articles, 1)
    return {
        'total_articles_fetched': total_articles,
        'newsapi_rate_limit_usage': {
            'used': newsapi_used,
            'limit': newsapi_limit,
            'usage_pct': round((newsapi_used / newsapi_limit) * 100.0, 2) if newsapi_limit > 0 else 0.0,
        },
        'aggregated_sentiment_distribution': {
            'positive': {'count': positive, 'pct': round((positive / distribution_total) * 100.0, 2)},
            'neutral': {'count': neutral, 'pct': round((neutral / distribution_total) * 100.0, 2)},
            'negative': {'count': negative, 'pct': round((negative / distribution_total) * 100.0, 2)},
        },
        'updated_at_epoch': updated_at_epoch,
    }


@router.get('/forecaster')
async def get_forecaster_monitor(user=Depends(get_current_user)):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=400, detail='Invalid user')

    await _ensure_telemetry_schema()
    db = get_database()

    rows = await db.fetch_all(
        query=(
            "SELECT created_at, asset_symbol, raw_forecast_score, bull_threshold, bear_threshold, treeshap_log "
            "FROM forecaster_telemetry_events WHERE owner_id = :owner "
            "ORDER BY created_at DESC LIMIT 30"
        ),
        values={'owner': owner},
    )

    execution_summary = await db.fetch_one(
        query=(
            "SELECT "
            "COALESCE(SUM(CASE WHEN action_taken = 'Executed' THEN 1 ELSE 0 END), 0) AS executed_count, "
            "COALESCE(SUM(CASE WHEN action_taken = 'Rejected' THEN 1 ELSE 0 END), 0) AS rejected_count "
            "FROM bot_execution_logs WHERE owner_id = :owner"
        ),
        values={'owner': owner},
    )

    logs = [
        {
            'timestamp': row['created_at'].timestamp() if row['created_at'] else None,
            'asset': row['asset_symbol'],
            'raw_forecast_score': float(row['raw_forecast_score'] or 0.0),
            'bull_threshold': float(row['bull_threshold'] or 0.2),
            'bear_threshold': float(row['bear_threshold'] or -0.2),
            'treeshap_log': row['treeshap_log'] or '',
        }
        for row in rows
    ]

    if not logs:
        insight_rows = await db.fetch_all(
            query=(
                "SELECT symbol, payload, updated_at FROM insights "
                "WHERE owner = :owner ORDER BY updated_at DESC LIMIT 30"
            ),
            values={'owner': owner},
        )
        for row in insight_rows:
            payload = row['payload'] if row and 'payload' in row else None
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except Exception:
                    payload = None
            if not isinstance(payload, dict):
                continue

            probability_up = float(payload.get('probability_up') or 0.5)
            latest_sentiment = float(payload.get('latest_sentiment_score') or 0.0)
            signal = str(payload.get('signal') or 'HOLD').upper()
            logs.append(
                {
                    'timestamp': row['updated_at'].timestamp() if row and 'updated_at' in row and row['updated_at'] else None,
                    'asset': str(row['symbol'] or payload.get('symbol') or '-').upper(),
                    'raw_forecast_score': (probability_up * 2.0) - 1.0,
                    'bull_threshold': 0.2,
                    'bear_threshold': -0.2,
                    'treeshap_log': f"From insights cache: Signal={signal}; ProbUp={probability_up:.4f}; Sentiment={latest_sentiment:.3f}",
                }
            )

    return {
        'rows': logs,
        'count': len(logs),
        'default_thresholds': {'bull': 0.2, 'bear': -0.2},
        'execution_summary': {
            'executed': int((execution_summary['executed_count'] if execution_summary else 0) or 0),
            'rejected': int((execution_summary['rejected_count'] if execution_summary else 0) or 0),
        },
    }


@router.get('/llm')
async def get_llm_monitor(user=Depends(get_current_user)):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=400, detail='Invalid user')

    await _ensure_telemetry_schema()
    db = get_database()

    rows = await db.fetch_all(
        query=(
            "SELECT created_at, asset_symbol, prompt, model_used, latency_ms, prompt_tokens, completion_tokens, total_tokens "
            "FROM llm_telemetry_events WHERE owner_id = :owner "
            "ORDER BY created_at DESC LIMIT 200"
        ),
        values={'owner': owner},
    )

    logs = [
        {
            'timestamp_epoch': row['created_at'].timestamp() if row['created_at'] else None,
            'symbol': row['asset_symbol'] or '-',
            'prompt': row['prompt'],
            'model_used': row['model_used'],
            'latency_ms': float(row['latency_ms'] or 0),
            'prompt_tokens': int(row['prompt_tokens'] or 0),
            'completion_tokens': int(row['completion_tokens'] or 0),
            'total_tokens': int(row['total_tokens'] or 0),
        }
        for row in rows
    ]

    total_prompts = len(logs)
    token_usage = sum(int(item.get('total_tokens') or 0) for item in logs)
    avg_latency = round(sum(float(item.get('latency_ms') or 0) for item in logs) / total_prompts, 2) if total_prompts else 0.0

    return {
        'total_chat_prompts': total_prompts,
        'token_usage': token_usage,
        'avg_response_latency_ms': avg_latency,
        'recent_prompts_history': logs[:20],
    }


__all__ = [
    'router',
    'record_llm_prompt',
    'record_forecaster_event',
    'record_finbert_batch',
    '_estimate_tokens',
]

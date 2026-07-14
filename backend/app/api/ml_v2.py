from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.api.auth import get_current_user
from app.api.rate_limit import enforce_guest_llm_rate_limit
from app.core.db import get_database
from app.ml.news_fetcher import fetch_news_for_symbol, fetch_ohlcv
from app.ml.news_sentiment_analyzer import NewsSentimentAnalyzer, NewsSentimentAnalyzerError
from app.ml.market_forecaster import MarketForecaster, MarketForecasterError
from app.ml.trading_bot import AutomatedTradingBot, RiskParameterViolation
from app.ml.dual_llm_manager import DualLLMManager, DualLLMManagerError


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


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def _article_text(article: Dict[str, Any]) -> str:
    return " ".join(filter(None, [article.get("title", ""), article.get("description", ""), article.get("content", "")]))


def _article_excerpt(article: Dict[str, Any]) -> str:
    text = article.get('description') or article.get('content') or ''
    text = str(text).strip()
    if len(text) > 220:
        return f"{text[:217].rstrip()}..."
    return text


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


def _resolve_news_window(days: int, from_date: Optional[str], to_date: Optional[str]) -> Tuple[datetime, datetime]:
    max_days = max(int(days), 1)
    end_dt = datetime.utcnow()

    if to_date:
        try:
            parsed_to = date.fromisoformat(str(to_date))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail='invalid_to_date') from exc
        end_dt = datetime.combine(parsed_to, time.max)

    start_dt = end_dt - timedelta(days=max_days)
    if from_date:
        try:
            parsed_from = date.fromisoformat(str(from_date))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail='invalid_from_date') from exc
        start_dt = datetime.combine(parsed_from, time.min)

    if start_dt > end_dt:
        raise HTTPException(status_code=400, detail='from_date_must_be_before_to_date')

    return start_dt, end_dt


async def _get_newsapi_key_for_owner(owner: str) -> Optional[str]:
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

    # Align to OHLCV rows.
    values: List[float] = []
    for d in idx:
        values.append(float(day_mean.get(d, 0.0)))

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


def _build_llm_asset_context(asset: Dict[str, Any]) -> Dict[str, Any]:
    historical_data = asset.get('historical_data') or {}
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


async def _build_watch_asset_news(
    symbol: str,
    api_key: Optional[str],
    *,
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

    if api_key:
        raw_articles = await run_in_threadpool(
            fetch_news_for_symbol,
            api_key,
            symbol,
            from_dt,
            to_dt,
            max(int(page_size), 1),
            max(int(page), 1),
            display_name,
        )
        scored = await run_in_threadpool(
            _score_articles_finbert,
            _SENTIMENT_ANALYZER,
            raw_articles,
            max_articles=max(int(page_size), 1),
        )

    avg_sentiment = float(sum(item['score'] for item in scored) / len(scored)) if scored else 0.0
    normalized_page_size = max(int(page_size), 1)

    articles = []
    for index, article in enumerate(raw_articles[:normalized_page_size]):
        scored_item = scored[index] if index < len(scored) else None
        articles.append(
            {
                'title': article.get('title') or 'Untitled article',
                'source': (article.get('source') or {}).get('name') or 'Unknown source',
                'url': article.get('url'),
                'published_at': article.get('publishedAt'),
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


async def _build_watch_asset_insight(symbol: str, owner: str, api_key: Optional[str]) -> Dict[str, Any]:
    from app.api.analytics import get_asset_detail
    from app.api.market import get_historical_data

    asset = await get_asset_detail(symbol, '1mo')
    history_payload = await get_historical_data(symbol, 'year')
    df = await run_in_threadpool(_history_payload_to_ohlcv_df, history_payload)
    if len(df) > 180:
        df = df.tail(180)
    if df is None or getattr(df, 'empty', True):
        raise HTTPException(status_code=400, detail=f'no price data for {symbol}')

    articles: List[Dict[str, Any]] = []
    if api_key:
        to_dt = datetime.utcnow()
        from_dt = to_dt - timedelta(days=30)
        articles = await run_in_threadpool(fetch_news_for_symbol, api_key, symbol, from_dt, to_dt, 25)

    scored = await run_in_threadpool(_score_articles_finbert, _SENTIMENT_ANALYZER, articles, max_articles=25)
    sentiment_series = await run_in_threadpool(_build_sentiment_series, df, scored)
    latest_sentiment = float(sentiment_series.iloc[-1]) if len(sentiment_series) else 0.0

    signal = 'HOLD'
    probability_up = 0.5
    confidence = 50
    rationale = []
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
            direction = 'supports upside' if feature.get('direction') == 'positive' else 'adds downside risk'
            rationale.append(f"{feature.get('feature')}: {direction} ({feature.get('impact_pct', 0):.1f}% impact).")
    except Exception:
        price_change_pct = float(asset.get('price_change_pct') or 0.0)
        outlook = _trend_outlook(price_change_pct, latest_sentiment)
        if latest_sentiment > 0.1 and price_change_pct > 0:
            signal = 'BUY'
            probability_up = 0.62
        elif latest_sentiment < -0.1 and price_change_pct < 0:
            signal = 'SELL'
            probability_up = 0.38
        else:
            signal = 'HOLD'
            probability_up = 0.5
        confidence = int(round(abs(latest_sentiment) * 50 + min(abs(price_change_pct), 10) * 5))
        rationale = [
            f"Outlook: {outlook}.",
            f"Recent price change is {price_change_pct:.2f}%.",
            f"Average news sentiment score is {latest_sentiment:.2f}.",
        ]

    recommendation_map = {
        'BUY': 'Bullish',
        'SELL': 'Bearish',
        'HOLD': 'Neutral',
    }

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

    api_key = await _get_newsapi_key_for_owner(owner)
    used_newsapi = bool(api_key)

    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=int(req.lookback_days))

    try:
        # Fetching can block (network + yfinance); use threadpool.
        articles = []
        if api_key:
            articles = await run_in_threadpool(fetch_news_for_symbol, api_key, symbol, from_dt, to_dt)
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
    api_key = await _get_newsapi_key_for_owner(owner)

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
            articles = await run_in_threadpool(fetch_news_for_symbol, api_key, symbol, from_dt, to_dt)
            scored = await run_in_threadpool(
                _score_articles_finbert,
                _SENTIMENT_ANALYZER,
                articles,
                max_articles=int(req.max_articles),
            )
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

    api_key = await _get_newsapi_key_for_owner(owner)

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
            articles = await run_in_threadpool(fetch_news_for_symbol, api_key, symbol, from_dt, to_dt)
            scored = await run_in_threadpool(
                _score_articles_finbert,
                _SENTIMENT_ANALYZER,
                articles,
                max_articles=int(req.max_articles),
            )
        except Exception as e:
            logger.warning("News sentiment fetch/score failed; proceeding with neutral sentiment. Error=%s", e)
            scored = []

    sentiment_series = await run_in_threadpool(_build_sentiment_series, df, scored)
    df2 = df.copy()
    df2["sentiment_score"] = sentiment_series

    try:
        signal = await run_in_threadpool(forecaster.generate_signal, df2)
        shap_expl = await run_in_threadpool(forecaster.get_shap_explanation, df2)
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

    try:
        explanation = await _DUAL_LLM.generate_explanation(user_pref, req.shap_context, req.prompt)
        return {
            'model_used': _DUAL_LLM.last_model_used,
            'used_fallback': _DUAL_LLM.last_used_fallback,
            'explanation': explanation,
        }
    except DualLLMManagerError as e:
        return _fallback_explanation(req.shap_context, req.prompt)
    except Exception as e:
        return _fallback_explanation(req.shap_context, req.prompt)


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

    api_key = await _get_newsapi_key_for_owner(owner)
    payload = []
    for symbol in symbols:
        try:
            payload.append(
                await _build_watch_asset_news(
                    symbol,
                    api_key,
                    days=days,
                    page=1,
                    page_size=page_size,
                    from_date=from_date,
                    to_date=to_date,
                )
            )
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

    api_key = await _get_newsapi_key_for_owner(owner)
    return await _build_watch_asset_news(
        normalized_symbol,
        api_key,
        days=days,
        page=page,
        page_size=page_size,
        from_date=from_date,
        to_date=to_date,
    )


@router.get('/watchlist/insights')
async def watchlist_insights(user=Depends(get_current_user)):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=401, detail='unauthorized')

    symbols = await _get_watchlist_symbols(owner)
    if not symbols:
        return []

    api_key = await _get_newsapi_key_for_owner(owner)
    payload = []
    for symbol in symbols:
        try:
            payload.append(await _build_watch_asset_insight(symbol, owner, api_key))
        except Exception as e:
            logger.warning('watchlist_insight_failed symbol=%s error=%s', symbol, e)
            payload.append({
                'symbol': symbol,
                'display_name': symbol,
                'signal': 'HOLD',
                'recommendation': 'Neutral',
                'confidence': 0,
                'probability_up': 0.5,
                'latest_price': 0.0,
                'price_change_pct': 0.0,
                'latest_sentiment_score': 0.0,
                'trend_summary': 'Insight generation is temporarily unavailable.',
                'rationale': ['Price history could not be loaded for this symbol yet.'],
            })
    return payload


@router.post('/assistant/explain')
async def watchlist_assistant_explain(req: WatchlistAssistantRequest, user=Depends(get_current_user)):
    owner = user.get('sub')
    if not owner:
        raise HTTPException(status_code=401, detail='unauthorized')

    symbol = _normalize_symbol(req.symbol)
    symbols = await _get_watchlist_symbols(owner)
    if symbol not in symbols:
        raise HTTPException(status_code=403, detail='symbol_not_in_watchlist')

    try:
        from app.api.analytics import get_asset_detail

        asset = await get_asset_detail(symbol, req.range)
        shap_context = _build_llm_asset_context(asset)
        pref = (req.user_preference or '').strip().lower()
        user_pref = 'custom' if pref == 'custom' else 'open-source'
        try:
            explanation = await _DUAL_LLM.generate_explanation(user_pref, shap_context, req.prompt)
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

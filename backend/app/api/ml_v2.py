from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from fastapi import APIRouter, Depends, HTTPException
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
        res = await _DUAL_LLM.generate_explanation(user_pref, req.shap_context, req.prompt)
        return res
    except DualLLMManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/public/explain")
async def v2_public_llm_explain(req: GuestExplainRequest, _: None = Depends(enforce_guest_llm_rate_limit)):
    symbol = _normalize_symbol(req.symbol)
    if symbol not in _GUEST_ALLOWED_SYMBOLS:
        raise HTTPException(status_code=403, detail='symbol_not_allowed')

    try:
        from app.api.analytics import get_asset_detail

        asset = await get_asset_detail(symbol, req.range)
        shap_context = {
            'symbol': asset.get('symbol'),
            'price': asset.get('price'),
            'price_change': asset.get('price_change'),
            'price_change_pct': asset.get('price_change_pct'),
            'sentiment': asset.get('sentiment'),
            'historical_data': asset.get('historical_data'),
        }
        return await _DUAL_LLM.generate_explanation('open-source', shap_context, req.prompt)
    except HTTPException:
        raise
    except DualLLMManagerError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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

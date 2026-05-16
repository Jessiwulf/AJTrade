from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any
from datetime import datetime, timedelta

from app.api.auth import get_current_user
from app.ml import news_fetcher, sentiment, model as mlmodel, shap_explainer
from app.core.db import get_database

router = APIRouter()


class TrainRequest(BaseModel):
    symbol: str
    lookback_days: int = 30


class PredictRequest(BaseModel):
    symbol: str
    period: str = '30d'


@router.post('/fetch_and_train')
async def fetch_and_train(req: TrainRequest, user=Depends(get_current_user)):
    # retrieve newsapi key from vault for this user
    db = get_database()
    owner = user.get('sub')
    row = await db.fetch_one(query="SELECT encrypted_blob FROM encrypted_api_keys WHERE owner = :owner AND service = 'newsapi' LIMIT 1", values={"owner": owner})
    if not row:
        raise HTTPException(status_code=400, detail='newsapi key not found in vault')
    from app.core import crypto
    api_key = crypto.decrypt_api_key(row['encrypted_blob']).decode('utf-8')
    # fetch news for the same lookback window (best-effort; may be limited by NewsAPI plan)
    to_dt = datetime.utcnow()
    from_dt = to_dt - timedelta(days=int(req.lookback_days))
    articles = news_fetcher.fetch_news_for_symbol(api_key, req.symbol, from_dt=from_dt, to_dt=to_dt)
    sent_agg = sentiment.aggregate_article_sentiments(articles)
    # fetch OHLCV
    df = news_fetcher.fetch_ohlcv(req.symbol, period=f"{req.lookback_days}d", interval='1d')
    if df is None or df.empty:
        raise HTTPException(status_code=400, detail='no price data')
    try:
        X, y, vectorizer, feature_names, _meta = mlmodel.prepare_article_training(df, articles)
        path = mlmodel.train_and_save(req.symbol, X, y, vectorizer, feature_names)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "status": "trained",
        "model_path": path,
        "samples": int(len(y)),
        "vocab_size": int(len(vectorizer.get_feature_names_out())),
        "sentiment": sent_agg,
    }


@router.post('/predict')
async def predict(req: PredictRequest, user=Depends(get_current_user)):
    df = news_fetcher.fetch_ohlcv(req.symbol, period=req.period, interval='1d')
    if df is None or df.empty:
        raise HTTPException(status_code=400, detail='no price data')
    # minimal sentiment: try to fetch news similarly (optional)
    db = get_database()
    owner = user.get('sub')
    row = await db.fetch_one(query="SELECT encrypted_blob FROM encrypted_api_keys WHERE owner = :owner AND service = 'newsapi' LIMIT 1", values={"owner": owner})
    sent_agg = {}
    if row:
        from app.core import crypto
        api_key = crypto.decrypt_api_key(row['encrypted_blob']).decode('utf-8')
        articles = news_fetcher.fetch_news_for_symbol(api_key, req.symbol,)
        sent_agg = sentiment.aggregate_article_sentiments(articles)
    # Predict using article-level model (keywords + price features)
    res = mlmodel.predict(req.symbol, df, articles if row else [])
    return {"symbol": req.symbol, "result": res, "sentiment": sent_agg}


@router.post('/explain')
async def explain(req: PredictRequest, user=Depends(get_current_user)):
    df = news_fetcher.fetch_ohlcv(req.symbol, period=req.period, interval='1d')
    if df is None or df.empty:
        raise HTTPException(status_code=400, detail='no price data')
    db = get_database()
    owner = user.get('sub')
    row = await db.fetch_one(query="SELECT encrypted_blob FROM encrypted_api_keys WHERE owner = :owner AND service = 'newsapi' LIMIT 1", values={"owner": owner})
    sent_agg = {}
    if row:
        from app.core import crypto
        api_key = crypto.decrypt_api_key(row['encrypted_blob']).decode('utf-8')
        articles = news_fetcher.fetch_news_for_symbol(api_key, req.symbol)
        sent_agg = sentiment.aggregate_article_sentiments(articles)
    bundle = mlmodel.load_model_bundle(req.symbol)
    if bundle is None:
        raise HTTPException(status_code=404, detail='model_not_found')
    # Build inference matrix from the same vectorizer used at training
    X, _meta = mlmodel.build_inference_matrix(bundle, df, articles if row else [])
    expl = shap_explainer.explain(req.symbol, X)
    expl['top_keywords_frequency'] = sent_agg.get('top_keywords', [])
    return {"symbol": req.symbol, "explanation": expl, "sentiment": sent_agg}

import os
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
import pandas as pd
import numpy as np
import joblib
import lightgbm as lgb
from sklearn.feature_extraction.text import TfidfVectorizer
from scipy import sparse

from app.ml.sentiment import sentiment_compound

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')
os.makedirs(MODEL_DIR, exist_ok=True)


def _create_features(df: pd.DataFrame) -> pd.DataFrame:
    # expects df with columns ['Open','High','Low','Close','Volume'] indexed by date
    X = pd.DataFrame(index=df.index)
    X['close'] = df['Close']
    X['ret_1'] = df['Close'].pct_change(1)
    X['ret_3'] = df['Close'].pct_change(3)
    X['ma_5'] = df['Close'].rolling(5).mean()
    X['ma_10'] = df['Close'].rolling(10).mean()
    X['vol_1'] = df['Volume'].pct_change(1)
    X = X.fillna(0)
    return X


def _article_text(article: Dict[str, Any]) -> str:
    return ' '.join(filter(None, [article.get('title', ''), article.get('description', ''), article.get('content', '')]))


def _parse_published_at(article: Dict[str, Any]) -> Optional[pd.Timestamp]:
    s = article.get('publishedAt')
    if not s:
        return None
    try:
        # NewsAPI often uses ISO8601 with 'Z'
        ts = pd.to_datetime(s, utc=True, errors='coerce')
        if pd.isna(ts):
            return None
        return ts.tz_convert(None).normalize()
    except Exception:
        return None


def _align_article_to_price_row(df_price: pd.DataFrame, published_date: pd.Timestamp) -> Optional[int]:
    # Align to next available trading day in df_price
    if df_price is None or df_price.empty:
        return None
    idx = pd.DatetimeIndex(df_price.index).tz_localize(None).normalize()
    target = pd.Timestamp(published_date).normalize()
    pos = int(idx.searchsorted(target, side='left'))
    if pos >= len(idx):
        return None
    return pos


def prepare_article_training(
    df_price: pd.DataFrame,
    articles: List[Dict[str, Any]],
    *,
    max_articles: int = 200,
    max_text_features: int = 500,
) -> Tuple[sparse.csr_matrix, np.ndarray, TfidfVectorizer, List[str], List[Dict[str, Any]]]:
    """Build an article-level training set.

    Each news article becomes one sample. We align article published date to the next trading day.
    Label is next-day direction from that day.
    Features = numeric price indicators at that day + per-article sentiment + TF-IDF keywords.
    """
    if not articles:
        raise ValueError('no_articles')

    num_df = _create_features(df_price)
    numeric_feature_names = list(num_df.columns) + ['sentiment_compound']

    X_num_rows: List[np.ndarray] = []
    texts: List[str] = []
    y: List[int] = []
    meta: List[Dict[str, Any]] = []

    # limit to most recent articles
    recent_articles = articles[:max_articles]
    for art in recent_articles:
        pub = _parse_published_at(art)
        if pub is None:
            continue
        pos = _align_article_to_price_row(df_price, pub)
        if pos is None:
            continue
        if pos + 1 >= len(df_price):
            continue

        # numeric price features from that day
        x_num = num_df.iloc[pos].to_numpy(dtype=float)
        txt = _article_text(art)
        s_comp = sentiment_compound(txt)
        x_num = np.concatenate([x_num, np.array([s_comp], dtype=float)])

        # label: next-day direction
        close_today = float(df_price['Close'].iloc[pos])
        close_next = float(df_price['Close'].iloc[pos + 1])
        label = 1 if close_next > close_today else 0

        X_num_rows.append(x_num)
        texts.append(txt)
        y.append(label)
        meta.append({
            'publishedAt': art.get('publishedAt'),
            'title': art.get('title'),
            'source': (art.get('source') or {}).get('name'),
            'url': art.get('url'),
        })

    if len(y) < 20:
        raise ValueError('not_enough_samples')

    vectorizer = TfidfVectorizer(stop_words='english', max_features=max_text_features, ngram_range=(1, 2))
    X_text = vectorizer.fit_transform(texts)

    X_num = np.vstack(X_num_rows)
    X_num_sparse = sparse.csr_matrix(X_num)
    X = sparse.hstack([X_num_sparse, X_text], format='csr')

    text_feature_names = [f"kw:{w}" for w in vectorizer.get_feature_names_out().tolist()]
    feature_names = numeric_feature_names + text_feature_names

    return X, np.array(y, dtype=int), vectorizer, feature_names, meta


def train_and_save(symbol: str, X: sparse.csr_matrix, y: np.ndarray, vectorizer: TfidfVectorizer, feature_names: List[str]) -> str:
    dtrain = lgb.Dataset(X, label=y, feature_name=feature_names)
    params = {
        'objective': 'binary',
        'metric': 'binary_logloss',
        'verbosity': -1,
        'learning_rate': 0.05,
        'num_leaves': 31,
    }
    booster = lgb.train(params, dtrain, num_boost_round=200)
    path = os.path.join(MODEL_DIR, f"{symbol}.pkl")
    bundle = {
        'schema_version': 1,
        'booster': booster,
        'vectorizer': vectorizer,
        'feature_names': feature_names,
        'numeric_feature_count': len(feature_names) - len(vectorizer.get_feature_names_out()),
    }
    joblib.dump(bundle, path)
    return path


def load_model_bundle(symbol: str):
    path = os.path.join(MODEL_DIR, f"{symbol}.pkl")
    if not os.path.exists(path):
        return None
    return joblib.load(path)


def build_inference_matrix(bundle: Dict[str, Any], df_price: pd.DataFrame, articles: List[Dict[str, Any]]) -> Tuple[sparse.csr_matrix, List[Dict[str, Any]]]:
    num_df = _create_features(df_price)
    numeric_cols = list(num_df.columns) + ['sentiment_compound']

    X_num_rows: List[np.ndarray] = []
    texts: List[str] = []
    meta: List[Dict[str, Any]] = []

    if not articles:
        # fallback to a single empty-text sample on latest day
        pos = max(0, len(df_price) - 2)
        x_num = num_df.iloc[pos].to_numpy(dtype=float)
        x_num = np.concatenate([x_num, np.array([0.0], dtype=float)])
        X_num_rows.append(x_num)
        texts.append('')
        meta.append({'publishedAt': None, 'title': None, 'source': None, 'url': None})
    else:
        for art in articles:
            pub = _parse_published_at(art)
            if pub is None:
                continue
            pos = _align_article_to_price_row(df_price, pub)
            if pos is None:
                continue
            if pos + 1 >= len(df_price):
                continue
            x_num = num_df.iloc[pos].to_numpy(dtype=float)
            txt = _article_text(art)
            s_comp = sentiment_compound(txt)
            x_num = np.concatenate([x_num, np.array([s_comp], dtype=float)])
            X_num_rows.append(x_num)
            texts.append(txt)
            meta.append({
                'publishedAt': art.get('publishedAt'),
                'title': art.get('title'),
                'source': (art.get('source') or {}).get('name'),
                'url': art.get('url'),
            })

    vectorizer: TfidfVectorizer = bundle['vectorizer']
    X_text = vectorizer.transform(texts)
    X_num = np.vstack(X_num_rows)
    X_num_sparse = sparse.csr_matrix(X_num)
    X = sparse.hstack([X_num_sparse, X_text], format='csr')
    return X, meta


def predict(symbol: str, df_price: pd.DataFrame, articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    bundle = load_model_bundle(symbol)
    if bundle is None:
        return {'error': 'model_not_found'}
    booster = bundle['booster']
    X, meta = build_inference_matrix(bundle, df_price, articles)
    probs = booster.predict(X)
    agg_prob = float(np.mean(probs)) if len(probs) else 0.5
    signal = 'buy' if agg_prob > 0.55 else ('sell' if agg_prob < 0.45 else 'hold')
    return {
        'probability': agg_prob,
        'signal': signal,
        'n_articles': int(len(probs)),
        'per_article': [{
            'probability': float(p),
            **(meta[i] if i < len(meta) else {}),
        } for i, p in enumerate(probs[:25])],
    }

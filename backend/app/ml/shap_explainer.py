import os
from typing import Dict, Any
import joblib
import numpy as np
from scipy import sparse

MODEL_DIR = os.path.join(os.path.dirname(__file__), 'models')


def explain(symbol: str, X: sparse.csr_matrix) -> Dict[str, Any]:
    """Return TreeSHAP (SHAP) attributions from LightGBM for the provided feature matrix.

    This uses LightGBM's built-in TreeSHAP (`pred_contrib=True`) to compute SHAP values.
    It also maps keyword TF-IDF features back to human-readable keywords.
    """
    path = os.path.join(MODEL_DIR, f"{symbol}.pkl")
    if not os.path.exists(path):
        return {'error': 'model_not_found'}

    bundle = joblib.load(path)
    booster = bundle.get('booster')
    feature_names = bundle.get('feature_names')
    n_numeric = int(bundle.get('numeric_feature_count', 0))
    if booster is None or not feature_names:
        return {'error': 'invalid_model_bundle'}

    try:
        contrib = booster.predict(X, pred_contrib=True)
        # contrib shape: (n_samples, n_features + 1_bias)
        contrib_mean = np.asarray(contrib).mean(axis=0)
        bias = float(contrib_mean[-1])
        feat_vals = contrib_mean[:-1]

        pairs = list(zip(feature_names, feat_vals))
        top_features = sorted(pairs, key=lambda x: abs(x[1]), reverse=True)[:15]

        # Identify keyword features and filter to only those present in the input text
        present_mask = None
        if sparse.issparse(X) and n_numeric < X.shape[1]:
            X_text = X[:, n_numeric:]
            term_sum = np.asarray(X_text.sum(axis=0)).ravel()
            present_mask = term_sum > 0

        kw_pairs = []
        kw_start = n_numeric
        for i, (name, val) in enumerate(pairs):
            if not name.startswith('kw:'):
                continue
            kw_idx = i - kw_start
            if present_mask is not None and (kw_idx < 0 or kw_idx >= len(present_mask) or not present_mask[kw_idx]):
                continue
            kw_pairs.append((name[3:], float(val)))

        top_kw_abs = sorted(kw_pairs, key=lambda x: abs(x[1]), reverse=True)[:15]
        top_kw_pos = sorted([p for p in kw_pairs if p[1] > 0], key=lambda x: x[1], reverse=True)[:10]
        top_kw_neg = sorted([p for p in kw_pairs if p[1] < 0], key=lambda x: x[1])[:10]

        return {
            'bias': bias,
            'top_features': [{'feature': f, 'value': float(v)} for f, v in top_features],
            'keyword_shap': {
                'top_absolute': [{'keyword': k, 'value': float(v)} for k, v in top_kw_abs],
                'top_positive': [{'keyword': k, 'value': float(v)} for k, v in top_kw_pos],
                'top_negative': [{'keyword': k, 'value': float(v)} for k, v in top_kw_neg],
            },
        }
    except Exception as e:
        return {'error': str(e)}

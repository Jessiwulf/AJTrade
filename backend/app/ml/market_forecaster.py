from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


class MarketForecasterError(RuntimeError):
    """Raised when the MarketForecaster cannot train or predict."""


class MarketForecaster:
    """LightGBM-based short-term market forecaster with SHAP explanations.

    This class intentionally separates:
    - Training (LightGBM classifier)
    - Signal generation (BUY/SELL/HOLD)
    - Explainability (SHAP TreeExplainer)

    Expected inputs:
    - historical_ohlcv: DataFrame with columns Open, High, Low, Close, Volume
    - sentiment_scores: Series aligned to the same time index (one score per row)
    """

    def __init__(
        self,
        *,
        buy_threshold: float = 0.55,
        sell_threshold: float = 0.45,
        random_state: int = 42,
    ) -> None:
        self.buy_threshold = float(buy_threshold)
        self.sell_threshold = float(sell_threshold)
        self.random_state = int(random_state)

        self.model = None
        self.feature_columns: List[str] = []

    @staticmethod
    def _validate_ohlcv(df: pd.DataFrame) -> None:
        required = {"Open", "High", "Low", "Close", "Volume"}
        if df is None or df.empty:
            raise MarketForecasterError("historical_ohlcv is empty")
        missing = [c for c in sorted(required) if c not in df.columns]
        if missing:
            raise MarketForecasterError(f"historical_ohlcv missing columns: {missing}")

    @staticmethod
    def _build_features(df: pd.DataFrame, sentiment_scores: pd.Series) -> pd.DataFrame:
        """Create a minimal, fast feature set from OHLCV + sentiment."""
        # Align indices
        idx = pd.DatetimeIndex(df.index)
        sent = sentiment_scores.reindex(idx)

        X = pd.DataFrame(index=idx)
        close = df["Close"].astype(float)
        vol = df["Volume"].astype(float)

        X["close"] = close
        X["ret_1"] = close.pct_change(1)
        X["ret_3"] = close.pct_change(3)
        X["ma_5"] = close.rolling(5).mean()
        X["ma_10"] = close.rolling(10).mean()
        X["vol_chg_1"] = vol.pct_change(1)
        X["range_pct"] = (df["High"].astype(float) - df["Low"].astype(float)) / close.replace(0, np.nan)
        X["sentiment_score"] = sent.astype(float)

        X = X.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        return X

    @staticmethod
    def _build_labels(df: pd.DataFrame) -> pd.Series:
        close = df["Close"].astype(float)
        y = (close.shift(-1) > close).astype(int)
        return y

    @staticmethod
    def _time_split(X: pd.DataFrame, y: pd.Series, train_ratio: float = 0.8) -> Tuple[pd.DataFrame, pd.DataFrame, pd.Series, pd.Series]:
        if len(X) < 50:
            # Keep it simple; caller can still train, but warn about limited data.
            logger.warning("Training with small dataset (n=%s)", len(X))
        split = max(1, int(len(X) * train_ratio))
        X_train, X_test = X.iloc[:split], X.iloc[split:]
        y_train, y_test = y.iloc[:split], y.iloc[split:]
        return X_train, X_test, y_train, y_test

    def train_model(self, historical_ohlcv: pd.DataFrame, sentiment_scores: pd.Series) -> Dict[str, Any]:
        """Train a LightGBM classifier from OHLCV + sentiment.

        Target label: 1 if next period Close > current Close else 0.

        Returns training metrics (JSON-serializable) for quick sanity checks.
        """
        self._validate_ohlcv(historical_ohlcv)
        if sentiment_scores is None:
            raise MarketForecasterError("sentiment_scores is required")

        X = self._build_features(historical_ohlcv, sentiment_scores)
        y = self._build_labels(historical_ohlcv)

        # Drop last row (no next-day label)
        X = X.iloc[:-1]
        y = y.iloc[:-1]

        if len(X) < 20:
            raise MarketForecasterError("not_enough_samples")

        X_train, X_test, y_train, y_test = self._time_split(X, y)

        try:
            import lightgbm as lgb  # type: ignore
        except Exception as e:
            raise MarketForecasterError("lightgbm is not installed") from e

        model = lgb.LGBMClassifier(
            n_estimators=400,
            learning_rate=0.05,
            num_leaves=31,
            subsample=0.9,
            colsample_bytree=0.9,
            random_state=self.random_state,
        )
        model.fit(X_train, y_train)

        self.model = model
        self.feature_columns = list(X.columns)

        # Lightweight metrics
        train_acc = float((model.predict(X_train) == y_train).mean())
        test_acc = float((model.predict(X_test) == y_test).mean()) if len(X_test) else train_acc
        test_proba_mean = float(np.mean(model.predict_proba(X_test)[:, 1])) if len(X_test) else float(np.mean(model.predict_proba(X_train)[:, 1]))

        return {
            "status": "trained",
            "n_samples": int(len(X)),
            "n_features": int(X.shape[1]),
            "feature_columns": self.feature_columns,
            "train_accuracy": train_acc,
            "test_accuracy": test_acc,
            "test_prob_up_mean": test_proba_mean,
        }

    def _ensure_trained(self) -> None:
        if self.model is None or not self.feature_columns:
            raise MarketForecasterError("model_not_trained")

    def _to_feature_row(self, current_data: Any) -> pd.DataFrame:
        """Convert various current_data shapes into a 1-row feature DataFrame."""
        self._ensure_trained()

        # Case 1: OHLCV history provided (DataFrame) -> compute features, take last row
        if isinstance(current_data, pd.DataFrame) and {"Open", "High", "Low", "Close", "Volume"}.issubset(set(current_data.columns)):
            # sentiment can be provided either as a column or separately
            if "sentiment_score" in current_data.columns:
                sent = current_data["sentiment_score"].astype(float)
            else:
                # If absent, assume neutral
                sent = pd.Series(0.0, index=current_data.index)
            feats = self._build_features(current_data, sent)
            row = feats.iloc[[-1]]
            return row[self.feature_columns]

        # Case 2: already a feature mapping / vector
        if isinstance(current_data, pd.Series):
            row_dict = current_data.to_dict()
        elif isinstance(current_data, dict):
            row_dict = dict(current_data)
        else:
            raise MarketForecasterError("current_data must be a DataFrame, dict, or Series")

        # Build a 1-row df aligned to feature columns; missing -> 0
        row = {c: float(row_dict.get(c, 0.0) or 0.0) for c in self.feature_columns}
        return pd.DataFrame([row], columns=self.feature_columns)

    def generate_signal(self, current_data: Any) -> str:
        """Generate a BUY/SELL/HOLD signal from current_data."""
        self._ensure_trained()
        X = self._to_feature_row(current_data)

        prob_up = float(self.model.predict_proba(X)[0, 1])
        if prob_up >= self.buy_threshold:
            return "BUY"
        if prob_up <= self.sell_threshold:
            return "SELL"
        return "HOLD"

    def get_shap_explanation(self, current_data: Any) -> Dict[str, Any]:
        """Return a JSON-serializable SHAP explanation for current_data.

        We compute percent impact per feature as:
            abs(shap_value) / sum(abs(shap_values)) * 100
        then apply the original sign (e.g., +15.2% / -10.1%).
        """
        self._ensure_trained()

        X = self._to_feature_row(current_data)
        proba_up = float(self.model.predict_proba(X)[0, 1])

        try:
            import shap  # type: ignore
        except Exception as e:
            raise MarketForecasterError("shap is not installed") from e

        explainer = shap.TreeExplainer(self.model)

        # SHAP API varies by version; support both list and array outputs.
        shap_values = explainer.shap_values(X)
        expected_value = explainer.expected_value

        # For binary classification, prefer class-1 contributions when available.
        if isinstance(shap_values, list) and len(shap_values) >= 2:
            sv = np.asarray(shap_values[1])[0]
            base = expected_value[1] if isinstance(expected_value, (list, tuple, np.ndarray)) else expected_value
        else:
            sv = np.asarray(shap_values)[0]
            base = expected_value[0] if isinstance(expected_value, (list, tuple, np.ndarray)) else expected_value

        values = X.iloc[0].to_numpy(dtype=float)
        total_abs = float(np.sum(np.abs(sv)))

        features: List[Dict[str, Any]] = []
        for name, val, shap_val in zip(self.feature_columns, values, sv):
            shap_val_f = float(shap_val)
            abs_pct = float((abs(shap_val_f) / total_abs * 100.0) if total_abs > 0 else 0.0)
            impact_pct = abs_pct if shap_val_f >= 0 else -abs_pct
            features.append(
                {
                    "feature": str(name),
                    "value": float(val),
                    "shap_value": shap_val_f,
                    "impact_pct": impact_pct,
                    "direction": "positive" if shap_val_f >= 0 else "negative",
                }
            )

        # Sort by absolute impact
        features_sorted = sorted(features, key=lambda x: abs(float(x.get("impact_pct", 0.0))), reverse=True)

        # Ensure sentiment_score is present and easy to find
        sentiment_item = next((f for f in features if f["feature"] == "sentiment_score"), None)

        return {
            "predicted_probability_up": proba_up,
            "base_value": float(base) if base is not None else None,
            "top_features": features_sorted[:20],
            "sentiment": sentiment_item,
        }

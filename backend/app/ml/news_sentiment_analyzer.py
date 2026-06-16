from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, Optional


logger = logging.getLogger(__name__)


class NewsSentimentAnalyzerError(RuntimeError):
    """Raised when the NewsSentimentAnalyzer cannot analyze the provided text."""


class NewsSentimentAnalyzer:
    """Financial news sentiment analyzer using a Hugging Face Transformers model (FinBERT).

    Returns a structured result with:
    - label: Positive | Negative | Neutral
    - score: sentiment score normalized to [-1, 1]
    - confidence_pct: [0, 100]

    Notes:
    - Model is loaded lazily to avoid slowing down FastAPI startup.
    - Defaults to ProsusAI/finbert but can be overridden via AJTRADE_FINBERT_MODEL.
    """

    DEFAULT_MODEL = "ProsusAI/finbert"

    def __init__(
        self,
        model_name: Optional[str] = None,
        *,
        device: Optional[str] = None,
        max_length: int = 256,
    ) -> None:
        self.model_name = (model_name or os.environ.get("AJTRADE_FINBERT_MODEL") or self.DEFAULT_MODEL).strip()
        self.device_preference = (device or os.environ.get("AJTRADE_FINBERT_DEVICE") or "auto").strip().lower()
        self.max_length = int(os.environ.get("AJTRADE_FINBERT_MAX_LENGTH", str(max_length)))

        self._lock = threading.Lock()
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._device = None

    def _ensure_loaded(self) -> None:
        if self._model is not None and self._tokenizer is not None and self._torch is not None:
            return

        with self._lock:
            if self._model is not None and self._tokenizer is not None and self._torch is not None:
                return

            try:
                import torch  # type: ignore
                from transformers import AutoModelForSequenceClassification, AutoTokenizer  # type: ignore
            except Exception as e:
                raise NewsSentimentAnalyzerError(
                    "Missing dependencies for FinBERT sentiment analysis. "
                    "Install 'transformers' and 'torch' in the backend environment."
                ) from e

            try:
                tokenizer = AutoTokenizer.from_pretrained(self.model_name)
                model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            except Exception as e:
                raise NewsSentimentAnalyzerError(
                    f"Failed to load transformers model '{self.model_name}'. "
                    "Check model name and network/cache availability."
                ) from e

            model.eval()

            if self.device_preference in {"", "auto"}:
                # Default: prefer CUDA if available, else CPU.
                device = "cuda" if torch.cuda.is_available() else "cpu"
            else:
                device = self.device_preference

            try:
                model.to(device)
            except Exception:
                logger.warning("Unable to move model to device '%s'; falling back to CPU", device)
                device = "cpu"
                model.to(device)

            self._torch = torch
            self._tokenizer = tokenizer
            self._model = model
            self._device = device

            logger.info("Loaded FinBERT model '%s' on device '%s'", self.model_name, self._device)

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        return " ".join((text or "").split())

    @staticmethod
    def _to_label(raw_label: str) -> str:
        s = (raw_label or "").strip().lower()
        if "pos" in s:
            return "Positive"
        if "neg" in s:
            return "Negative"
        if "neu" in s:
            return "Neutral"
        return "Neutral"

    def analyze_text(self, text: str) -> Dict[str, Any]:
        """Analyze unstructured financial news text.

        Args:
            text: Raw news text.

        Returns:
            Dict with keys: label, score, confidence_pct (plus optional meta/probabilities).
        """
        if not isinstance(text, str):
            raise NewsSentimentAnalyzerError("text must be a string")

        cleaned = self._normalize_whitespace(text)
        if not cleaned:
            raise NewsSentimentAnalyzerError("text is empty")

        self._ensure_loaded()
        assert self._torch is not None and self._tokenizer is not None and self._model is not None and self._device is not None

        torch = self._torch
        tokenizer = self._tokenizer
        model = self._model

        try:
            inputs = tokenizer(
                cleaned,
                return_tensors="pt",
                truncation=True,
                max_length=self.max_length,
            )
            inputs = {k: v.to(self._device) for k, v in inputs.items()}

            with torch.no_grad():
                outputs = model(**inputs)
                logits = outputs.logits[0]
                probs_t = torch.softmax(logits, dim=-1).detach().cpu()
                probs = [float(x) for x in probs_t.tolist()]

            id2label = getattr(model.config, "id2label", None) or {}

            # Choose predicted class.
            pred_idx = int(max(range(len(probs)), key=lambda i: probs[i]))
            raw_label = str(id2label.get(pred_idx, str(pred_idx)))
            label = self._to_label(raw_label)

            # Locate positive/negative indices to compute a stable [-1, 1] score.
            def _find_label_index(needle: str) -> Optional[int]:
                for k, v in id2label.items():
                    try:
                        idx = int(k)
                    except Exception:
                        continue
                    if needle in str(v).lower():
                        return idx
                return None

            pos_idx = _find_label_index("positive")
            neg_idx = _find_label_index("negative")

            if pos_idx is not None and neg_idx is not None and pos_idx < len(probs) and neg_idx < len(probs):
                score = float(probs[pos_idx] - probs[neg_idx])
            else:
                # Fallback: sign by predicted label.
                sign = 0.0
                if label == "Positive":
                    sign = 1.0
                elif label == "Negative":
                    sign = -1.0
                score = float(sign * probs[pred_idx])

            # Clamp to [-1, 1] for safety.
            score = max(-1.0, min(1.0, score))

            confidence_pct = float(probs[pred_idx] * 100.0)

            # Optional: expose per-class probabilities for transparency.
            probabilities: Dict[str, float] = {}
            for i, p in enumerate(probs):
                raw = str(id2label.get(i, str(i)))
                probabilities[self._to_label(raw)] = float(p)

            return {
                "label": label,
                "score": score,
                "confidence_pct": confidence_pct,
                "probabilities": probabilities,
                "model": self.model_name,
            }
        except NewsSentimentAnalyzerError:
            raise
        except Exception as e:
            raise NewsSentimentAnalyzerError(f"sentiment_inference_failed: {e}") from e

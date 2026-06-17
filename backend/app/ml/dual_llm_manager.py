from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Optional

import httpx


logger = logging.getLogger(__name__)


class DualLLMManagerError(RuntimeError):
    """Raised when the DualLLMManager cannot generate an explanation."""


class DualLLMManager:
    """Model-agnostic dual-LLM manager with strict fallback.

    - OPEN_SOURCE_MODEL: default open-source LLM (FinGPT variant)
    - CUSTOM_MODEL: fine-tuned AJTrade model (future)

    This component is a *presentation layer only*: it must not generate trade signals
    or execute orders.
    """

    model_open_source = "llama3:8b"
    model_custom = "ajtrade-custom-v1"

    def __init__(
        self,
        *,
        ollama_host: Optional[str] = None,
        timeout_s: float = 90.0,
    ) -> None:
        base_host = (ollama_host or os.environ.get("OLLAMA_HOST") or "http://ollama:11434").rstrip("/")
        self.ollama_generate_url = f"{base_host}/api/generate"
        timeout_override = os.environ.get("OLLAMA_TIMEOUT_S")
        self.timeout_s = float(timeout_override or timeout_s)
        self.last_model_used: Optional[str] = None
        self.last_used_fallback: bool = False

    def _select_model(self, user_preference: str) -> str:
        pref = (user_preference or "").strip().lower()
        if pref == "custom":
            return self.model_custom
        return self.model_open_source

    @staticmethod
    def _build_system_prompt() -> str:
        return (
            "You are AJTrade's Conversational Agent. Your job is to EXPLAIN model outputs, not to trade.\n"
            "Rules (strict):\n"
            "1) Use ONLY the provided SHAP context. Do not add facts or assumptions.\n"
            "2) Do NOT give financial advice, recommendations, or guarantees.\n"
            "3) Do NOT invent prices, news, indicators, or portfolio data.\n"
            "4) If information is missing from the SHAP context, say 'Not enough information'.\n"
            "5) Output must be concise and human-readable (short paragraphs or bullets)."
        )

    async def _ollama_generate(self, *, model: str, system: str, prompt: str) -> str:
        payload = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": False,
        }
        timeout = httpx.Timeout(self.timeout_s)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(self.ollama_generate_url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, dict):
            raise DualLLMManagerError("ollama_invalid_response")

        text = str(data.get("response") or "").strip()
        if not text:
            raise DualLLMManagerError("ollama_empty_response")
        return text

    async def generate_explanation(self, user_preference: str, shap_context: Dict[str, Any], predicted_signal: str) -> str:
        """Generate a human-readable explanation using the selected LLM with strict fallback.

        Routing:
        - user_preference == 'custom' -> CUSTOM_MODEL
        - otherwise -> OPEN_SOURCE_MODEL

        Fallback:
        - If CUSTOM_MODEL fails (timeout/error), automatically retry using OPEN_SOURCE_MODEL.
        """
        if shap_context is None or not isinstance(shap_context, dict):
            raise DualLLMManagerError("shap_context must be a dict")

        target = self._select_model(user_preference)
        system = self._build_system_prompt()

        try:
            shap_json = json.dumps(shap_context, ensure_ascii=False, separators=(",", ":"))
        except Exception as e:
            raise DualLLMManagerError("shap_context is not JSON-serializable") from e

        user_prompt = (
            "SHAP_CONTEXT_JSON:\n"
            f"{shap_json}\n\n"
            "REQUEST_OR_SIGNAL:\n"
            f"{predicted_signal}\n\n"
            "Respond using ONLY SHAP_CONTEXT_JSON."
        )

        self.last_used_fallback = False
        self.last_model_used = None

        try:
            text = await self._ollama_generate(model=target, system=system, prompt=user_prompt)
            self.last_model_used = target
            return text
        except Exception as e:
            if target != self.model_custom:
                raise DualLLMManagerError(f"llm_generate_failed: {e}") from e

            # Strict fallback: custom failed -> open-source
            self.last_used_fallback = True
            logger.warning("CUSTOM_MODEL failed; falling back to OPEN_SOURCE_MODEL", exc_info=True)
            try:
                text = await self._ollama_generate(model=self.model_open_source, system=system, prompt=user_prompt)
                self.last_model_used = self.model_open_source
                return text
            except Exception as e2:
                raise DualLLMManagerError(f"llm_fallback_failed: {e2}") from e2

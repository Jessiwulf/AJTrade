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

    OPEN_SOURCE_MODEL = "llama3-fingpt"
    CUSTOM_MODEL = "ajtrade-custom-v1"

    def __init__(
        self,
        *,
        ollama_base_url: Optional[str] = None,
        timeout_s: float = 20.0,
    ) -> None:
        self.ollama_base_url = (ollama_base_url or os.environ.get("AJTRADE_OLLAMA_URL") or "http://ollama:11434").rstrip(
            "/"
        )
        self.timeout_s = float(timeout_s)

    def _select_model(self, user_preference: str) -> str:
        pref = (user_preference or "").strip().lower()
        if pref == "custom":
            return self.CUSTOM_MODEL
        return self.OPEN_SOURCE_MODEL

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

    async def _ollama_generate(self, *, model: str, system: str, prompt: str) -> Dict[str, Any]:
        url = f"{self.ollama_base_url}/api/generate"
        payload = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": False,
        }
        timeout = httpx.Timeout(self.timeout_s)

        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        if not isinstance(data, dict):
            raise DualLLMManagerError("ollama_invalid_response")

        return data

    async def generate_explanation(self, user_preference: str, shap_context: Dict[str, Any], prompt: str) -> Dict[str, Any]:
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
            "USER_REQUEST:\n"
            f"{prompt}\n\n"
            "Respond using ONLY SHAP_CONTEXT_JSON."
        )

        used_fallback = False

        try:
            data = await self._ollama_generate(model=target, system=system, prompt=user_prompt)
            text = (data.get("response") or "").strip()
            return {"model_used": target, "used_fallback": used_fallback, "explanation": text}
        except Exception as e:
            if target != self.CUSTOM_MODEL:
                raise DualLLMManagerError(f"llm_generate_failed: {e}") from e

            # Strict fallback: custom failed -> open-source
            used_fallback = True
            logger.warning("CUSTOM_MODEL failed; falling back to OPEN_SOURCE_MODEL", exc_info=True)
            try:
                data = await self._ollama_generate(model=self.OPEN_SOURCE_MODEL, system=system, prompt=user_prompt)
                text = (data.get("response") or "").strip()
                return {"model_used": self.OPEN_SOURCE_MODEL, "used_fallback": used_fallback, "explanation": text}
            except Exception as e2:
                raise DualLLMManagerError(f"llm_fallback_failed: {e2}") from e2

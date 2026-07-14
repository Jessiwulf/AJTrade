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

    model_open_source = "qwen3.5:2b"
    model_custom = "ajtrade-custom-v1"

    def __init__(
        self,
        *,
        ollama_host: Optional[str] = None,
        timeout_s: float = 180.0,
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
            "You are a highly intelligent and concise AI assistant for AJTrade. "
            "You MUST obey the following strict rules unconditionally:\n\n"
            "=== 1. LANGUAGE & TONE ===\n"
            "- You MUST reply ONLY in the exact language the user typed. If they type in Thai, reply ONLY in Thai. If English, ONLY English. NO Korean, NO Chinese.\n"
            "- Answer directly. NEVER start with robotic filler phrases like 'Based on the provided context...', 'Here is the summary...', or 'According to the data...'.\n\n"
            "=== 2. ZERO HALLUCINATION (CRITICAL) ===\n"
            "- Treat ticker symbols (e.g., 'USA', 'BTC', 'AAPL') purely as symbols. NEVER invent corporate backgrounds, country descriptions, or assume you know what the asset does.\n"
            "- NEVER invent prices, dates, calculations, or market events. Use ONLY the exact numbers provided in the BACKGROUND_DATA.\n"
            "- Do not give guaranteed financial advice.\n\n"
            "=== 3. STRICT FORMATTING ===\n"
            "- ALWAYS use double newlines (\\n\\n) to separate paragraphs.\n"
            "- If making a list, use a simple dash (-) and force a double newline (\\n\\n) after each item.\n"
            "- AVOID excessive markdown formatting. DO NOT put brackets or multiple asterisks around numbers (e.g., NEVER write [**$5.8**] or ****6.8****). Write numbers plainly like $5.80 or 5.80.\n\n"
            "=== 4. GENERAL CHAT VS DATA ===\n"
            "- If the user says 'Hello' or asks a general question ('1+1', 'What is AI?'), just answer naturally and concisely. Ignore the BACKGROUND_DATA entirely.\n"
            "- Only use the BACKGROUND_DATA if the user specifically asks about the asset's prediction, trend, or risks. Explain it simply without technical jargon."
        )

    async def _ollama_generate(self, *, model: str, system: str, prompt: str) -> str:
        payload = {
            "model": model,
            "system": system,
            "prompt": prompt,
            "stream": False,
            "think": False,
        }
        timeout = httpx.Timeout(connect=20.0, read=self.timeout_s, write=20.0, pool=20.0)

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

    async def generate_explanation(self, user_preference: str, shap_context: Dict[str, Any], user_message: str) -> str:
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

        signal_value = str(
            shap_context.get("signal")
            or shap_context.get("recommendation")
            or shap_context.get("outlook")
            or "Not provided"
        )

        user_prompt = (
            "--- BACKGROUND_DATA (Use this ONLY if the user's message is about analyzing the asset or signal) ---\n"
            f"Signal: {signal_value}\n"
            f"SHAP Values: {shap_json}\n"
            "--------------------------------------------------\n\n"
            f"USER_MESSAGE:\n{user_message}"
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

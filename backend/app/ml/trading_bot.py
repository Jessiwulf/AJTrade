from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


class RiskParameterViolation(ValueError):
    """Raised when an order violates the user's risk parameters."""


def _normalize_pct(value: Any, *, field: str) -> float:
    try:
        v = float(value)
    except Exception as e:
        raise RiskParameterViolation(f"{field} must be a number") from e

    # Support both fractional (0.02) and whole percent (2 for 2%) inputs.
    if v > 1.0 and v <= 100.0:
        v = v / 100.0

    if v <= 0.0 or v >= 1.0:
        raise RiskParameterViolation(f"{field} must be between 0 and 1 (or 0-100%)")

    return float(v)


def _normalize_money(value: Any, *, field: str) -> float:
    try:
        v = float(value)
    except Exception as e:
        raise RiskParameterViolation(f"{field} must be a number") from e

    if v <= 0.0:
        raise RiskParameterViolation(f"{field} must be > 0")

    return float(v)


@dataclass
class AutomatedTradingBot:
    """Strict, rule-based execution engine.

    This bot does NOT generate signals. It only validates and formats orders produced
    by upstream analytics (NLP/LightGBM) under a user's risk boundaries.
    """

    def evaluate_and_execute(self, signal: str, current_price: float, risk_profile: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(signal, str):
            raise RiskParameterViolation("signal must be a string")

        side = signal.strip().upper()
        if side not in {"BUY", "SELL", "HOLD"}:
            raise RiskParameterViolation("signal must be BUY, SELL, or HOLD")

        try:
            price = float(current_price)
        except Exception as e:
            raise RiskParameterViolation("current_price must be numeric") from e

        if price <= 0.0:
            raise RiskParameterViolation("current_price must be > 0")

        if risk_profile is None or not isinstance(risk_profile, dict):
            raise RiskParameterViolation("risk_profile must be a dict")

        if side == "HOLD":
            return {"action": "HOLD", "reason": "no_trade"}

        stop_loss_pct = _normalize_pct(risk_profile.get("stop_loss_pct"), field="stop_loss_pct")
        take_profit_pct = _normalize_pct(risk_profile.get("take_profit_pct"), field="take_profit_pct")
        max_capital = _normalize_money(risk_profile.get("max_capital_per_trade"), field="max_capital_per_trade")

        if side == "BUY":
            stop_loss_price = price * (1.0 - stop_loss_pct)
            take_profit_price = price * (1.0 + take_profit_pct)
        else:  # SELL (short / exit)
            stop_loss_price = price * (1.0 + stop_loss_pct)
            take_profit_price = price * (1.0 - take_profit_pct)

        if stop_loss_price <= 0.0 or take_profit_price <= 0.0:
            raise RiskParameterViolation("computed stop-loss/take-profit is invalid")

        # Simple sizing: use notional limit as a hard cap.
        quantity = max_capital / price
        if quantity <= 0.0:
            raise RiskParameterViolation("max_capital_per_trade too small for current_price")

        payload = {
            "action": side,
            "order_type": "market",
            "current_price": float(price),
            "quantity": float(quantity),
            "notional": float(max_capital),
            "risk": {
                "stop_loss_pct": float(stop_loss_pct),
                "take_profit_pct": float(take_profit_pct),
                "max_capital_per_trade": float(max_capital),
                "stop_loss_price": float(stop_loss_price),
                "take_profit_price": float(take_profit_price),
            },
        }

        # Extra strict validation (sanity checks)
        if side == "BUY" and not (stop_loss_price < price < take_profit_price):
            raise RiskParameterViolation("BUY bracket is invalid for the current price")
        if side == "SELL" and not (take_profit_price < price < stop_loss_price):
            raise RiskParameterViolation("SELL bracket is invalid for the current price")

        return payload

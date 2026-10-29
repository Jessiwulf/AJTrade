"""Realized P/L and win rates from the recorded broker fills (trading_history).

A "closed trade" is a SELL fill with a realized P/L. It wins when that P/L is above zero. BUYs only
open positions, so they never count towards the win rate.
"""
from collections import OrderedDict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _as_utc(value: Any) -> Optional[datetime]:
    if not isinstance(value, datetime):
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def replay_realized_pl(fills: Iterable[Dict[str, Any]]) -> Dict[Any, Optional[Decimal]]:
    """Average-cost P/L for every SELL, replaying all fills in time order.

    Each fill is {id, symbol, side, quantity, price, at}. Returns {sell id: P/L}, where P/L is None
    when nothing of that symbol is held in the recorded history (its buys are missing). A sell larger
    than the recorded holding is priced on the part that is covered.
    """
    # At the same timestamp, buys go first so a sell is never priced before its buy.
    ordered = sorted(fills, key=lambda f: (_as_utc(f.get("at")) or _EPOCH, 0 if f["side"] == "BUY" else 1))
    holdings: Dict[str, List[Decimal]] = {}  # symbol -> [quantity, average cost]
    realized: Dict[Any, Optional[Decimal]] = {}
    for fill in ordered:
        quantity = Decimal(str(fill["quantity"]))
        price = Decimal(str(fill["price"]))
        held_qty, avg_cost = holdings.get(fill["symbol"], [Decimal("0"), Decimal("0")])
        if fill["side"] == "BUY":
            new_qty = held_qty + quantity
            if new_qty > 0:
                avg_cost = ((held_qty * avg_cost) + (quantity * price)) / new_qty
            held_qty = new_qty
        elif fill["side"] == "SELL":
            pl = None
            if held_qty > 0:
                matched = min(quantity, held_qty)
                pl = ((price - avg_cost) * matched).quantize(Decimal("0.01"))
                held_qty -= matched
            realized[fill["id"]] = pl
        holdings[fill["symbol"]] = [held_qty, avg_cost]
    return realized


def closed_trades(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """trading_history rows -> [{symbol, pl, at}] for the sells that have a realized P/L."""
    closed = []
    for row in rows:
        if row["trade_type"] != "SELL" or row["pl"] is None:
            continue
        closed.append({"symbol": str(row["symbol"]).upper(), "pl": float(row["pl"]), "at": _as_utc(row["created_at"])})
    return closed


def summarize(closed: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    closed = list(closed)
    wins = sum(1 for t in closed if t["pl"] > 0)
    losses = sum(1 for t in closed if t["pl"] < 0)
    return {
        "closed_trades": len(closed),
        "winning_trades": wins,
        "losing_trades": losses,
        # None, not 0%, when nothing has been sold yet: "no data" is different from "always lost".
        "win_rate": round(wins / len(closed) * 100, 2) if closed else None,
        "realized_pl": round(sum(t["pl"] for t in closed), 2),
    }


def month_key(at: Optional[datetime]) -> Optional[str]:
    return at.astimezone(timezone.utc).strftime("%Y-%m") if at else None


def by_symbol(closed: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = OrderedDict()
    for trade in closed:
        groups.setdefault(trade["symbol"], []).append(trade)
    return {symbol: summarize(trades) for symbol, trades in groups.items()}

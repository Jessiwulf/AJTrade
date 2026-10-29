import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.core.db import get_database

router = APIRouter()

# Optional "=X" suffix is Yahoo's currency-pair form (e.g. HKDTHB=X). Pairs can be watched and
# charted; the portfolio and trading bot keep their stricter patterns because they can't be traded.
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,20}(=X)?$")


def _normalize_symbol(symbol: str) -> str:
    # "SOL/USD" (Alpaca's crypto form) -> "SOL-USD" (the form used across the app).
    return (symbol or "").strip().upper().replace("/", "-")


async def _validate_symbol(symbol: str) -> str:
    """Returns the symbol to store: the input, its Yahoo form for share classes (BRK.B -> BRK-B), or
    "<COIN>-USD" when a bare coin name isn't a Yahoo ticker."""
    from starlette.concurrency import run_in_threadpool

    from app.api.market import _fetch_yahoo_chart_with_variants, yahoo_symbol_candidates
    from app.core.alpaca import ALPACA_CRYPTO_BASES

    symbol = _normalize_symbol(symbol)
    if not symbol or not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ticker symbol")

    candidates = yahoo_symbol_candidates(symbol)
    if symbol in ALPACA_CRYPTO_BASES:
        candidates.append(f"{symbol}-USD")
    for candidate in candidates:
        try:
            chart = await run_in_threadpool(_fetch_yahoo_chart_with_variants, candidate, 'day')
            if (chart.get('quote') or {}).get('price') is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'No price data found for {candidate}')
            return str(chart.get('symbol') or candidate).upper()
        except HTTPException as exc:
            if exc.status_code not in {status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND}:
                raise
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Ticker {symbol} is not available (for crypto, use the -USD pair, e.g. BTC-USD)",
    )


def _maybe_schema_hint(error: Exception, table: str) -> Optional[str]:
    msg = str(error)
    if f'relation "{table}" does not exist' in msg:
        return (
            f"Database table '{table}' does not exist. "
            "Apply schema in Supabase SQL editor: backend/app/models/local_schema.sql"
        )
    return None


class WatchlistItemIn(BaseModel):
    symbol: str
    notes: Optional[str] = None


class WatchlistItemOut(BaseModel):
    id: str
    symbol: str
    notes: Optional[str] = None
    created_at: str
    # Broker that lists the asset ("alpaca" or "settrade"); pages show the active provider's assets only.
    provider: str = "alpaca"


@router.get('', response_model=List[WatchlistItemOut])
async def list_watchlist(user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        rows = await db.fetch_all(
            query=(
                "SELECT id, symbol, notes, created_at "
                "FROM watchlists WHERE owner = :owner "
                "ORDER BY created_at DESC"
            ),
            values={"owner": owner},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "watchlists")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    from app.core.brokers import provider_for_symbol

    return [
        {
            "id": str(r["id"]),
            "symbol": r["symbol"],
            "notes": r["notes"],
            "created_at": str(r["created_at"]),
            "provider": await provider_for_symbol(r["symbol"]),
        }
        for r in rows
    ]


@router.post('', status_code=status.HTTP_201_CREATED)
async def add_watchlist_item(payload: WatchlistItemIn, user=Depends(get_current_user)):
    symbol = await _validate_symbol(payload.symbol)

    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        row = await db.fetch_one(
            query=(
                "INSERT INTO watchlists (owner, symbol, notes) "
                "VALUES (:owner, :symbol, :notes) "
                "ON CONFLICT (owner, symbol) DO UPDATE SET notes = EXCLUDED.notes "
                "RETURNING id, symbol, notes, created_at"
            ),
            values={"owner": owner, "symbol": symbol, "notes": payload.notes},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "watchlists")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    return {
        "id": str(row["id"]),
        "symbol": row["symbol"],
        "notes": row["notes"],
        "created_at": str(row["created_at"]),
    }


@router.put('/{item_id}')
async def update_watchlist_item(item_id: str, payload: WatchlistItemIn, user=Depends(get_current_user)):
    symbol = await _validate_symbol(payload.symbol)

    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        row = await db.fetch_one(
            query=(
                "UPDATE watchlists SET symbol = :symbol, notes = :notes "
                "WHERE id = :id AND owner = :owner "
                "RETURNING id, symbol, notes, created_at"
            ),
            values={"id": item_id, "owner": owner, "symbol": symbol, "notes": payload.notes},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "watchlists")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist item not found")

    return {
        "id": str(row["id"]),
        "symbol": row["symbol"],
        "notes": row["notes"],
        "created_at": str(row["created_at"]),
    }


@router.delete('/{item_id}')
async def delete_watchlist_item(item_id: str, user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        row = await db.fetch_one(
            query="DELETE FROM watchlists WHERE id = :id AND owner = :owner RETURNING id",
            values={"id": item_id, "owner": owner},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "watchlists")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Watchlist item not found")

    return {"status": "deleted", "id": item_id}

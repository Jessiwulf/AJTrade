import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.api.auth import get_current_user
from app.core.db import get_database

router = APIRouter()

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,24}$")


def _normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


async def _validate_symbol(symbol: str) -> None:
    symbol = _normalize_symbol(symbol)
    if not symbol or not _SYMBOL_RE.match(symbol):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ticker symbol")

    # Validate against the available market data source (yfinance).
    # Run in threadpool to avoid blocking the event loop.
    def _fetch() -> bool:
        import yfinance as yf

        df = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
        return df is not None and not df.empty

    try:
        ok = await run_in_threadpool(_fetch)
    except Exception:
        ok = False

    # Best effort only: if market data is unavailable, still allow the symbol to be saved.
    # This keeps watchlist CRUD usable when yfinance or the upstream data source is down.
    if not ok:
        return


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

    return [
        {
            "id": str(r["id"]),
            "symbol": r["symbol"],
            "notes": r["notes"],
            "created_at": str(r["created_at"]),
        }
        for r in rows
    ]


@router.post('', status_code=status.HTTP_201_CREATED)
async def add_watchlist_item(payload: WatchlistItemIn, user=Depends(get_current_user)):
    symbol = _normalize_symbol(payload.symbol)
    await _validate_symbol(symbol)

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
    symbol = _normalize_symbol(payload.symbol)
    await _validate_symbol(symbol)

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

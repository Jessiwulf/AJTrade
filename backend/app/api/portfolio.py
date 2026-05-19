import re
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
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

    def _fetch() -> bool:
        import yfinance as yf

        df = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
        return df is not None and not df.empty

    try:
        ok = await run_in_threadpool(_fetch)
    except Exception:
        ok = False

    if not ok:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ticker not found (no market data returned)",
        )


def _maybe_schema_hint(error: Exception, table: str) -> Optional[str]:
    msg = str(error)
    if f'relation "{table}" does not exist' in msg:
        return (
            f"Database table '{table}' does not exist. "
            "Apply schema in Supabase SQL editor: backend/app/models/local_schema.sql"
        )
    return None


class PositionIn(BaseModel):
    symbol: str
    quantity: Decimal = Field(..., ge=0)
    avg_price: Decimal = Field(Decimal("0"), ge=0)


class InitializePortfolioIn(BaseModel):
    cash_balance: Decimal = Field(..., ge=0)
    positions: List[PositionIn] = []


class UpdateCashIn(BaseModel):
    cash_balance: Decimal = Field(..., ge=0)


@router.get('')
async def get_portfolio(user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        p = await db.fetch_one(
            query="SELECT id, cash_balance, updated_at FROM portfolios WHERE owner = :owner",
            values={"owner": owner},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not initialized")

    try:
        rows = await db.fetch_all(
            query=(
                "SELECT id, symbol, quantity, avg_price, updated_at "
                "FROM portfolio_positions WHERE portfolio_id = :pid "
                "ORDER BY updated_at DESC"
            ),
            values={"pid": p["id"]},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolio_positions")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    positions = [
        {
            "id": str(r["id"]),
            "symbol": r["symbol"],
            "quantity": str(r["quantity"]),
            "avg_price": str(r["avg_price"]),
            "updated_at": str(r["updated_at"]),
        }
        for r in rows
    ]

    return {
        "id": str(p["id"]),
        "cash_balance": str(p["cash_balance"]),
        "updated_at": str(p["updated_at"]),
        "positions": positions,
    }


@router.post('/initialize')
async def initialize_portfolio(payload: InitializePortfolioIn, user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    for pos in payload.positions:
        await _validate_symbol(pos.symbol)

    try:
        p = await db.fetch_one(
            query=(
                "INSERT INTO portfolios (owner, cash_balance) "
                "VALUES (:owner, :cash) "
                "ON CONFLICT (owner) DO UPDATE SET cash_balance = EXCLUDED.cash_balance, updated_at = now() "
                "RETURNING id, cash_balance, updated_at"
            ),
            values={"owner": owner, "cash": payload.cash_balance},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    portfolio_id = p["id"]

    # Upsert positions (best-effort) if provided.
    for pos in payload.positions:
        symbol = _normalize_symbol(pos.symbol)
        if pos.quantity == 0:
            continue
        try:
            await db.execute(
                query=(
                    "INSERT INTO portfolio_positions (portfolio_id, symbol, quantity, avg_price) "
                    "VALUES (:pid, :symbol, :qty, :avg) "
                    "ON CONFLICT (portfolio_id, symbol) DO UPDATE SET "
                    "quantity = EXCLUDED.quantity, avg_price = EXCLUDED.avg_price, updated_at = now()"
                ),
                values={
                    "pid": portfolio_id,
                    "symbol": symbol,
                    "qty": pos.quantity,
                    "avg": pos.avg_price,
                },
            )
        except Exception as e:
            hint = _maybe_schema_hint(e, "portfolio_positions")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    return {
        "status": "ok",
        "portfolio_id": str(portfolio_id),
        "cash_balance": str(p["cash_balance"]),
        "updated_at": str(p["updated_at"]),
    }


@router.put('/cash')
async def update_cash(payload: UpdateCashIn, user=Depends(get_current_user)):
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
                "UPDATE portfolios SET cash_balance = :cash, updated_at = now() "
                "WHERE owner = :owner RETURNING id, cash_balance, updated_at"
            ),
            values={"owner": owner, "cash": payload.cash_balance},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not initialized")

    return {
        "status": "ok",
        "portfolio_id": str(row["id"]),
        "cash_balance": str(row["cash_balance"]),
        "updated_at": str(row["updated_at"]),
    }


@router.post('/positions')
async def upsert_position(payload: PositionIn, user=Depends(get_current_user)):
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
        p = await db.fetch_one(
            query="SELECT id FROM portfolios WHERE owner = :owner",
            values={"owner": owner},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not p:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Initialize your portfolio first")

    portfolio_id = p["id"]

    if payload.quantity == 0:
        try:
            await db.execute(
                query="DELETE FROM portfolio_positions WHERE portfolio_id = :pid AND symbol = :symbol",
                values={"pid": portfolio_id, "symbol": symbol},
            )
        except Exception as e:
            hint = _maybe_schema_hint(e, "portfolio_positions")
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))
        return {"status": "deleted", "symbol": symbol}

    try:
        row = await db.fetch_one(
            query=(
                "INSERT INTO portfolio_positions (portfolio_id, symbol, quantity, avg_price) "
                "VALUES (:pid, :symbol, :qty, :avg) "
                "ON CONFLICT (portfolio_id, symbol) DO UPDATE SET "
                "quantity = EXCLUDED.quantity, avg_price = EXCLUDED.avg_price, updated_at = now() "
                "RETURNING id, symbol, quantity, avg_price, updated_at"
            ),
            values={
                "pid": portfolio_id,
                "symbol": symbol,
                "qty": payload.quantity,
                "avg": payload.avg_price,
            },
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolio_positions")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    return {
        "status": "ok",
        "position": {
            "id": str(row["id"]),
            "symbol": row["symbol"],
            "quantity": str(row["quantity"]),
            "avg_price": str(row["avg_price"]),
            "updated_at": str(row["updated_at"]),
        },
    }


@router.delete('/positions/{position_id}')
async def delete_position(position_id: str, user=Depends(get_current_user)):
    try:
        db = get_database()
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    owner = user.get("sub")
    if not owner:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid user")

    try:
        p = await db.fetch_one(query="SELECT id FROM portfolios WHERE owner = :owner", values={"owner": owner})
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolios")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not p:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Portfolio not initialized")

    try:
        row = await db.fetch_one(
            query=(
                "DELETE FROM portfolio_positions "
                "WHERE id = :id AND portfolio_id = :pid "
                "RETURNING id"
            ),
            values={"id": position_id, "pid": p["id"]},
        )
    except Exception as e:
        hint = _maybe_schema_hint(e, "portfolio_positions")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=hint or str(e))

    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Position not found")

    return {"status": "deleted", "id": position_id}

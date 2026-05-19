import logging
import os

from dotenv import load_dotenv, find_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Load .env if present (helps local dev when Docker isn't used)
load_dotenv(find_dotenv(), override=False)

from app.api import router as api_router
from app.api import auth as auth_router

logger = logging.getLogger("ajtrade")

app = FastAPI(title="AJTrade API")

# CORS: default to allowing localhost on any port for development.
# Override via:
# - AJTRADE_CORS_ALLOW_ALL=1 (reflect origin)
# - AJTRADE_CORS_ORIGINS="https://yourapp.vercel.app,http://localhost:3000"
# - AJTRADE_CORS_ORIGIN_REGEX="^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$"
cors_allow_all = os.environ.get('AJTRADE_CORS_ALLOW_ALL', '').lower() in ('1', 'true', 'yes')
cors_origins_env = os.environ.get('AJTRADE_CORS_ORIGINS', '')
cors_origins = [o.strip() for o in cors_origins_env.split(',') if o.strip()]
cors_origin_regex = os.environ.get('AJTRADE_CORS_ORIGIN_REGEX')

if cors_origin_regex is None and not cors_allow_all and not cors_origins:
    cors_origin_regex = r"^https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)(:\d+)?$"

app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'] if cors_allow_all else cors_origins,
    allow_origin_regex=None if (cors_allow_all or cors_origins) else cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


@app.get('/api/health')
async def health():
    return {"status": "ok", "service": "ajtrade-backend"}


# Mount API routers
app.include_router(api_router.router, prefix='/api')
app.include_router(auth_router.router, prefix='/api/auth')

# Optional routers (Vault/ML). These can be disabled if optional deps are missing.
try:
    from app.api import vault as vault_router

    app.include_router(vault_router.router, prefix='/api/vault')
except Exception as e:
    logger.warning("Vault router not loaded: %s", e)

try:
    from app.api import ml as ml_router

    app.include_router(ml_router.router, prefix='/api/ml')
except Exception as e:
    logger.warning("ML router not loaded: %s", e)

try:
    from app.api import watchlist as watchlist_router

    app.include_router(watchlist_router.router, prefix='/api/watchlist')
except Exception as e:
    logger.warning("Watchlist router not loaded: %s", e)

try:
    from app.api import portfolio as portfolio_router

    app.include_router(portfolio_router.router, prefix='/api/portfolio')
except Exception as e:
    logger.warning("Portfolio router not loaded: %s", e)


@app.on_event('startup')
async def startup():
    # Don't hard-fail startup if DB isn't available (so auth still works).
    try:
        from app.core.db import get_database

        db = get_database()
        await db.connect()
    except Exception as e:
        logger.warning("DB connect skipped/failed: %s", e)


@app.on_event('shutdown')
async def shutdown():
    try:
        from app.core.db import get_database

        db = get_database()
        await db.disconnect()
    except Exception:
        return

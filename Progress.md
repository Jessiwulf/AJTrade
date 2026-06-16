**Project Overview & Vision**

- **Project Name:** AJTrade — AI Asset Analysis and Automated Trading.
- **Team:** Jirapat Sereerat, Atiwit Tin Intasarn.
- **Advisor:** Asst.Prof. Tisinee Surapunt.
- **Core Objective:** Build a web-based automated trading platform that removes emotional bias and provides full transparency:
  - Extract quantifiable sentiment from financial news (NLP).
  - Produce short-term forecasts combining sentiment + historical OHLCV (LightGBM).
  - Execute trades automatically under strict user-defined risk parameters (stop-loss, position sizing).
  - Provide explainability (SHAP) tied back to the exact keywords driving signals.
- **Key Concepts:** Behavioral Finance, Quantitative Sentiment Trading, Market Irrationality Exploitation, Explainable AI (XAI).

**Core Architecture & Data Flow**

- **High-level flow**
  - Frontend (Next.js) <--> Backend (FastAPI) <--> Supabase (Postgres).
  - Backend handles:
    - Auth/session management (Supabase).
    - API key Vault (encrypted storage using AES‑256‑GCM).
    - Market adapters (Settrade, Alpaca, Yahoo JSON, yfinance fallback).
    - ML inference (NLP + LightGBM) and SHAP explanation generation.
    - Trading execution (broker adapters) under user-configured risk rules.
- **How Next.js talks to FastAPI**
  - The Next.js app calls REST endpoints on FastAPI (e.g., `/api/watchlist`, `/api/market`, `/api/vault`, `/api/auth`).
  - Production deployments typically route Next.js (Vercel) → FastAPI (container or cloud endpoint) over HTTPS. Local dev uses `credentials: 'include'` and cookie-based sessions for Supabase.
- **Secure 3rd‑party API usage**
  - API keys are encrypted using AES‑256‑GCM before being persisted in the `encrypted_api_keys` table (Vault).
  - The backend decrypts keys only in memory immediately before use, keeps plaintext keys for the shortest possible time, and zeroizes memory references as soon as the outbound call completes.
  - Recommended pattern: generate a 32‑byte symmetric data key (AES‑256), use `cryptography`'s `AESGCM`, and never store raw plaintext keys in logs.
- **Data and model storage**
  - Raw data and relational state: Supabase Postgres.
  - Model artifacts (trained LightGBM models, tokenizers, vectorizers): stored in a secure backend folder (e.g., `backend/models/`) or a model registry/object storage for production.
  - Feature store: short-term OHLCV + derived technical/sentiment features kept in Postgres or time-series store for training and audit.
- **Audit & Explainability**
  - All executed signals, model inputs, SHAP outputs, and executed trades are persisted for auditing and backtesting.

**Technology Stack**

- **Frontend**
  - Next.js (Pages router)
  - React
  - Tailwind CSS (UI)
  - Hosting: Vercel (production)
- **Backend**
  - FastAPI (Python)
  - Containerization: Docker (Compose for local dev)
  - HTTP client: httpx / requests (adapters)
  - JWT / Supabase integration for auth
- **Database & Auth**
  - Supabase (Postgres)
  - Tables include: `watchlists`, `portfolios`, `portfolio_positions`, `profiles`, `encrypted_api_keys`, (see schema).
  - Local DB schema snapshot: backend/app/models/local_schema.sql
- **AI / ML**
  - NLP: custom pipeline for financial news → quantifiable sentiment (Positive / Neutral / Negative), keyword extraction.
  - Forecaster: LightGBM (chosen for speed and low memory on cloud).
  - Explainability: SHAP to produce feature attributions and map them back to keywords.
  - Fallback utilities: `yfinance` + Yahoo chart JSON API for market data; adapters for Settrade (Thai) and Alpaca.
- **DevOps / Infra**
  - Docker & Docker Compose for local development.
  - CI: build & static checks (recommended).
  - Deployment: FastAPI containers to cloud VM / container service; Next.js to Vercel.
  - Secrets: API keys in Vault (encrypted in DB); environment variables in container/orchestrator.

**AI Architecture and Pipeline**

- **Core AI Philosophy**
  - The AI architecture is designed to exploit human emotion and market irrationality by extracting sentiment and acting on short-term market momentum before the general public reacts.
  - We intentionally use a strictly separated multi-model pipeline (instead of a single “God-mode” model) to prioritize speed, safety, and absolute transparency.

- **The 5-Component Pipeline**
  - **NLP (Natural Language Processing)**
    - Extracts unstructured text from financial news and converts it into quantifiable sentiment scores.
  - **LightGBM (Machine Learning Forecaster)**
    - Combines NLP sentiment scores with historical OHLCV data to forecast short-term trends and generate directional signals.
  - **SHAP (Explainable AI)**
    - Calculates the exact influence percentage (e.g., `+15.2%`) of specific news keywords on the LightGBM prediction.
  - **Automated Trading Bot**
    - A strict, rule-based execution engine.
    - Intercepts model signals and validates them against user-defined risk parameters (e.g., Stop-Loss / Take-Profit) before calling the Broker API.
  - **Conversational Agent (LLM)**
    - Presentation layer only: translates SHAP values and portfolio data into human-readable summaries.
    - The LLM MUST NOT generate trade signals or execute orders.

- **LLM Selection and Hosting Layer**
  - **Model:** FinGPT (using Meta Llama 3 — 8B as the base).
  - **Hosting:** runs locally inside the Dockerized FastAPI backend via an inference engine (e.g., Ollama).
  - **Constraint:** the LLM MUST NOT be deployed on the Vercel frontend due to serverless memory and timeout limitations.
    - Vercel (Next.js) remains strictly a client that makes HTTPS requests to the backend.

- **Dual-LLM Strategy & Data Flywheel**
  - **Model-agnostic routing:** Supabase stores each user’s `LLM_MODEL` preference (configurable via the UI settings), and FastAPI routes prompts dynamically based on this value.
  - **Phase 1 (Data collection):** use the open-source FinGPT model to interact with users and log high-quality Q&A interactions and RAG context to the database.
  - **Phase 2 (Custom model):** curate the dataset to fine-tune our own proprietary model for enterprise offerings.

**Current Development Status & Roadmap (May 2026)**

- **Phase 1 — Proposal:** Completed (Feb–Apr 2026).
- **Phase 2 — Progress I (Apr–June 2026):** In Development (current focus)
  - Completed/working:
    - System design & database setup.
    - Authentication system and role management.
    - Market data adapters (Yahoo JSON primary; yfinance fallback; Settrade & Alpaca adapters implemented but require keys).
    - Vault for encrypted API keys using AES‑256‑GCM.
    - Basic watchlist, portfolio schemas and REST endpoints.
    - Profile UI + backend endpoints (local fallback available).
    - Market News AI Analyzer (FinBERT NLP) + LightGBM forecaster + SHAP explanations.
    - Dual-LLM manager with open-source/custom model fallback.
    - Automated Trading Bot rule-based validation engine.
    - Feature #5: Performance Analytics & Market Dashboard (NEW).
  - In progress:
    - Portfolio & API Management polish.
    - Scheduled model retraining and persistence.
- **Phase 4 (Upcoming):**
  - Real‑time Notification System (Discord / Email webhooks).
  - Production hardening, rate-limit handling, and operational monitoring.
- **Known dev notes & blockers**
  - Thai tickers (e.g., `BLAND`) require Settrade credentials stored in Vault to fetch market data. If a ticker returns "No price data", add Settrade credentials via the UI or env.
  - Market fetch order: Settrade (when configured) → Alpaca (when configured) → Yahoo JSON → yfinance fallback.

**Feature #5: Performance Analytics & Market Dashboard** — **COMPLETE (v1)**

- **Overview:** Interactive analytics dashboard providing deep insights into trading performance, portfolio growth, and market sentiment.

- **Backend Enhancements** (analytics.py):
  - **New DB Tables:**
    - `trading_history`: Tracks all buy/sell transactions for P/L calculation
    - `market_sentiment`: Aggregated sentiment per asset per day (range: -1 to +1)
    - `performance_metrics`: Cache portfolio metrics for dashboard (daily snapshot)
  - **New Endpoints:**
    - `GET /api/analytics/portfolio/metrics` → Current P/L, balance, win rate, daily return
    - `GET /api/analytics/portfolio/history?days=30` → Historical portfolio value for charting
    - `GET /api/analytics/transactions?limit=100&symbol=AAPL` → Transaction history with filtering
    - `POST /api/analytics/transactions` → Log new buy/sell transaction
    - `GET /api/analytics/sentiment-heatmap` → Market sentiment for all watched symbols
    - `GET /api/analytics/asset/{symbol}?range_=1mo` → Asset detail (Google Finance style)
    - `POST /api/analytics/sentiment-record` → Record sentiment after ML analysis
  
- **Frontend Dashboard** (pages/analytics.js):
  - **Metrics Panel:** 4-card display of Portfolio Value, Total P/L, Win Rate, Daily Return
  - **Portfolio Growth Chart:** Area chart showing total_value over 30 days (Recharts)
  - **Market Sentiment Heatmap:** Color-coded grid of watched symbols
    - Green (Very Bullish): sentiment ≥ 0.5
    - Light Green (Bullish): sentiment ≥ 0.1
    - Gray (Neutral): -0.1 < sentiment ≤ 0.1
    - Orange (Bearish): sentiment ≥ -0.5
    - Red (Very Bearish): sentiment < -0.5
  - **Transaction History Table:** Sortable/filterable table (symbol, type, qty, price, P/L, source)
  - **Asset Detail Modal:** Google Finance-style view
    - Current price + % change
    - Market sentiment breakdown (positive/neutral/negative counts)
    - Price chart (1-month history)
    - Metadata placeholders (P/E, dividend, market cap — can be extended with yfinance)

- **Data Aggregation & Calculations:**
  - **Unrealized P/L:** Calculated from current position values vs. average entry price
  - **Realized P/L:** Sum of P/L from all closed trades (SELL orders)
  - **Win Rate:** (winning_trades / total_trades * 100)
  - **Daily Return:** (today's_value - yesterday's_value) / yesterday's_value * 100
  - **Sentiment Label:** Mapped from numeric score (-1..+1) to categorical (Very Bearish → Very Bullish)

- **Integration with ML Pipeline:**
  - Trading Bot can call `POST /api/analytics/transactions` to log executed trades
  - ML module calls `POST /api/analytics/sentiment-record` after news analysis
  - Dashboard pulls live sentiment via `/api/analytics/sentiment-heatmap`

- **UI/UX Features:**
  - Refresh button to reload all data
  - Symbol-based filtering in transaction history
  - Responsive grid layout (4 cols on desktop, 1 col on mobile)
  - Dark theme with accent colors (green for bullish, red for bearish, blue for metrics)
  - Hover effects on heatmap cells and cards
  - Modal-based asset detail viewer

- **Known v1 Limitations & Future Enhancements:**
  - Market cap, P/E ratio, dividend yield placeholders (can fetch from yfinance)
  - 52-week high/low not yet populated
  - Performance metrics snapshot taken on-demand (should be cached daily)
  - Charting range limited to 30 days (can expand to 1Y / all-time)
  - No export-to-CSV for transactions
  - No real-time WebSocket updates (dashboard polls on load + manual refresh)

---

**Getting Started / Developer Setup Guide**

- **Prerequisites**
  - Git
  - Node.js (14+ recommended)
  - npm or yarn
  - Python 3.11+
  - Docker & Docker Compose (for containerized backend/local DB)
  - psql client (optional, for DB access)
- **Clone repository**
```bash
git clone <repo-url> ajtrade
cd ajtrade
```
- **Environment variables**
  - Create a `.env` (or provide in your container orchestrator). Typical variables used by the backend:
```env
# Supabase
SUPABASE_URL=https://<your-supabase-url>
SUPABASE_ANON_KEY=<anon-key>
SUPABASE_SERVICE_ROLE_KEY=<service-role-key>

# Backend / app
AJTRADE_DEV_DISABLE_SECURE_COOKIE=true      # local dev: allow non-secure cookies
SETTRADE_BASE_URL=https://api.settrade.com   # or sandbox url
SETTRADE_APP_ID=<settrade app id>
SETTRADE_APP_SECRET=<settrade app secret>

# Alpaca (optional)
ALPACA_KEY_ID=<alpaca key id>
ALPACA_SECRET_KEY=<alpaca secret key>

# News / third-party
NEWSAPI_KEY=<newsapi key>
```
- **Database initialization**
  - Apply local schema (example using `psql` to a local Postgres instance):
```bash
psql -h localhost -U postgres -d ajtrade -f backend/app/models/local_schema.sql
```
  - Or use the provided Docker Compose which will mount and initialize the DB (see compose file if present).
- **Backend: Python (local venv)**
```bash
# from repo root
cd backend
python -m venv .venv
source .venv/Scripts/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# Optional: compile and run lightweight checks
python -m compileall app
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```
- **Backend: Docker (recommended for parity with prod)**
```bash
# builds and starts containers (backend, db, etc.)
docker-compose up --build
```
- **Frontend**
```bash
cd frontend
npm install
npm run dev        # local dev: http://localhost:3000
# build for production:
npm run build
```
- **Adding API keys via UI (Vault)**
  - Open the app → API Management / Keys page.
  - Add Settrade `settrade_app_id` and `settrade_app_secret` or Alpaca keys.
  - Use the "Test/Save" button — backend `vault/ping` validates stored keys.
- **Running ML components & local inference**
  - Place model artifact(s) under `backend/models/` (or configure model path in env).
  - Start backend and call the forecasting endpoint (e.g., `/api/ml/forecast?symbol=AAPL`).
  - For debugging, run the NLP/feature pipeline on a subset of data and confirm Grouped features are produced correctly.
- **Quick troubleshooting tips**
  - If `Failed to fetch` on profile save: ensure backend is running at `http://localhost:8000`, `AJTRADE_DEV_DISABLE_SECURE_COOKIE` is set for local testing, and CORS/credentials are allowed.
  - If Thai tickers (e.g., `BLAND`) show "No price data": add Settrade App ID & Secret in the Vault UI and ensure `SETTRADE_BASE_URL` is correct.
  - Market data oddities: prefer Yahoo chart JSON endpoint; `yfinance` is fallback — if yfinance fails inside containers, check that `ca-certificates` are installed in the image.
  - If Vault encryption errors occur: verify backend `crypto.generate_data_key()` returns 32 bytes and `AESGCM` is used.

**Developer Conventions & Code Locations**

- **Backend**
  - API routers: `backend/app/api/` (auth, vault, market, watchlist, portfolio, ml, analytics)
  - Models & schema: `backend/app/models/` — local schema snapshot: backend/app/models/local_schema.sql
  - Crypto core (AES handling): `backend/app/core/crypto.py`
  - Supabase auth glue: `backend/app/core/supabase_auth.py`
  - Market adapters: `backend/app/api/market.py` (Settrade, Alpaca, Yahoo JSON, yfinance fallback)
  - Analytics: `backend/app/api/analytics.py` (portfolio metrics, sentiment heatmap, asset details)
- **Frontend**
  - Pages: `frontend/pages/` (dashboard, watchlist, portfolio, api-keys, profile, login, analytics)
  - Components: `frontend/components/` (AppShell, TopNav)
  - Helpers: `frontend/lib/api.js`, `frontend/lib/userProfile.js`
  - Styles: `frontend/styles/` (includes Analytics.module.css for dashboard)
- **Models & ML code**
  - Suggested (or existing) folder for models and pipelines: `backend/models/` or `backend/app/ml/`
  - SHAP explainability performed in the inference code path and returns JSON-friendly attribution structures.

**Operational & Security Notes**

- **Secrets handling**
  - Do not store plaintext API keys in source. Use the Vault API to encrypt before persisting.
  - Limit access to `SUPABASE_SERVICE_ROLE_KEY` — store securely.
- **Key lifetime**
  - Decrypt keys only at request time; zero references immediately after outbound call.
- **Auditability**
  - Persist raw inputs for each prediction and the SHAP attributions to support backtesting and academic reproducibility.
- **Testing & CI**
  - Add unit tests for market adapters to mock provider responses.
  - Integration tests should validate: Vault encrypt/decrypt, market adapter chain (Settrade → Alpaca → Yahoo), and ML inference + SHAP outputs.

**Onboarding Checklist (for the new developer)**

- [ ] Clone repository and run full local stack (`docker-compose up`) successfully.
- [ ] Confirm `Next.js` frontend runs at `http://localhost:3000` and FastAPI at `http://localhost:8000`.
- [ ] Use the API Keys UI to add a test NewsAPI key and test Settrade/Alpaca keys (if available).
- [ ] Run a smoke test: fetch quotes for `AAPL` and a Thai ticker like `BLAND` (expect `BLAND` to require Settrade keys).
- [ ] Run ML inference for a symbol and view SHAP output in the UI.
- [ ] Read the schema file at backend/app/models/local_schema.sql and confirm table(s) match local Postgres.
- [ ] Read `backend/app/api/market.py` to understand adapter ordering and fallbacks.

**Contacts & Next Steps**

- **Primary contacts:** Jirapat Sereerat, Atiwit Tin Intasarn (for code walkthroughs and pairing).
- **Advisor:** Asst.Prof. Tisinee Surapunt (research & evaluation).
- **Suggested first tasks:**
  - Harden Settrade adapter retry/backoff; add comprehensive unit tests for the market pipeline.
  - Implement scheduled model retraining, model versioning, and a small model registry.
  - Add monitoring (Prometheus + Grafana) and structured logs for production observability.

If you want, I can:
- Generate a local checklist as a task list file.
- Create runnable dev scripts (Makefile / npm scripts) to simplify `docker-compose` + build steps.
- Draft the README.md in the repo root with subset of these instructions and quick commands. Which would you prefer next?
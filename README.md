# AJTrade — AI Asset Trader

This repository contains scaffolding for AJTrade: Next.js frontend and FastAPI backend.

Structure:
- frontend/ — Next.js app
- backend/ — FastAPI app + model code

Local dev (requires Docker):

1) Create `.env` from the example and fill in Supabase values.

```bash
copy .env.example .env
docker compose up --build
```

If the Postgres volume already exists and you add/modify init SQL, recreate the volume:

```bash
docker compose down -v
docker compose up --build
```

Next steps: confirm and pick which core feature to implement first (Auth, Vault, News+ML, Bot, Dashboard, Notifications).

Environment variables (backend):

- `DATABASE_URL` - Postgres connection string (docker compose sets this)
- `SUPABASE_URL` - Your Supabase project URL (for using Supabase Auth)
- `SUPABASE_ANON_KEY` - Supabase anon/public key
- `AJTRADE_DATA_KEY` - 32-byte key used for AES-256 encryption of API keys (replace in production with KMS)
- `AJTRADE_DEV_DISABLE_SECURE_COOKIE` - set to `1` to allow non-secure cookies in local dev
- `AJTRADE_DEV_RETURN_TOKENS` - set to `1` to return `access_token` in login response for dev (frontend stores it in localStorage)
- `AJTRADE_DEV_RETURN_PLAINTEXT` - set to `1` to allow `GET /api/vault/keys/{id}/raw` to return plaintext (dev only)

# AJTrade Frontend

Next.js frontend scaffold for AJTrade.

Run locally:

```bash
cd AJTrade/frontend
npm install
npm run dev
```

Default dev port: http://localhost:3000

Backend URL:

- By default, the frontend calls `http://localhost:8000`.
- Override with `NEXT_PUBLIC_BACKEND_URL`.

Example:

```bash
copy .env.local.example .env.local
npm run dev
```

Pages:
- `/login`, `/signup`, `/password-reset`
- `/ml` — minimal panel for Vault + Train/Predict/Explain + keyword SHAP

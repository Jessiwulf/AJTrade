-- AJTrade app-owned schema (Supabase-compatible).
-- Apply this in Supabase SQL Editor. Tables are scoped by `owner` (JWT `sub`),
-- and intentionally avoid dependencies on Supabase `auth.*` tables.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Feature #2: Watchlist + Portfolio tables

CREATE TABLE IF NOT EXISTS watchlists (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner uuid NOT NULL,
  symbol text NOT NULL,
  notes text,
  created_at timestamptz DEFAULT now(),
  UNIQUE(owner, symbol)
);

CREATE TABLE IF NOT EXISTS portfolios (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner uuid NOT NULL,
  cash_balance numeric(18,4) DEFAULT 100000.00,
  updated_at timestamptz DEFAULT now(),
  UNIQUE(owner)
);

CREATE TABLE IF NOT EXISTS portfolio_positions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id uuid NOT NULL REFERENCES portfolios ON DELETE CASCADE,
  symbol text NOT NULL,
  quantity numeric(18,6) DEFAULT 0,
  avg_price numeric(18,6) DEFAULT 0,
  updated_at timestamptz DEFAULT now(),
  UNIQUE(portfolio_id, symbol)
);

CREATE TABLE IF NOT EXISTS encrypted_api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner uuid NOT NULL,
  service text NOT NULL,
  encrypted_blob bytea NOT NULL,
  created_at timestamptz DEFAULT now(),
  UNIQUE(owner, service)
);

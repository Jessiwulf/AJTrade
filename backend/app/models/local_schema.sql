-- Local Postgres schema for docker-compose dev (NOT Supabase).
-- Stores app-owned tables without dependencies on Supabase auth schema.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS encrypted_api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner uuid NOT NULL,
  service text NOT NULL,
  encrypted_blob bytea NOT NULL,
  created_at timestamptz DEFAULT now(),
  UNIQUE(owner, service)
);

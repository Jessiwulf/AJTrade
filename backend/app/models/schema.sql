-- Supabase-compatible schema (draft)

-- Note: Supabase provides `auth.users`; keep a `profiles` table for user metadata.

DO $$ BEGIN
  CREATE TYPE user_role AS ENUM ('guest', 'authenticated_user', 'admin');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE profiles (
  id uuid PRIMARY KEY REFERENCES auth.users ON DELETE CASCADE,
  full_name text,
  avatar_url text,
  role user_role NOT NULL DEFAULT 'authenticated_user',
  suspended_at timestamptz,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE watchlists (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner uuid REFERENCES profiles ON DELETE CASCADE,
  symbol text NOT NULL,
  notes text,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE portfolios (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner uuid REFERENCES profiles ON DELETE CASCADE,
  cash_balance numeric(18,4) DEFAULT 100000.00,
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE portfolio_positions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id uuid REFERENCES portfolios ON DELETE CASCADE,
  symbol text NOT NULL,
  quantity numeric(18,6) DEFAULT 0,
  avg_price numeric(18,6) DEFAULT 0,
  updated_at timestamptz DEFAULT now()
);

CREATE TABLE encrypted_api_keys (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner uuid REFERENCES profiles ON DELETE CASCADE,
  service text NOT NULL,
  encrypted_blob bytea NOT NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE trade_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id uuid REFERENCES portfolios ON DELETE SET NULL,
  symbol text NOT NULL,
  side text NOT NULL,
  qty numeric(18,6) NOT NULL,
  price numeric(18,6) NOT NULL,
  reason text,
  status text NOT NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE news_cache (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner uuid REFERENCES profiles ON DELETE CASCADE,
  symbol text NOT NULL,
  cache_key text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE(owner, symbol, cache_key)
);

CREATE TABLE insights (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner uuid REFERENCES profiles ON DELETE CASCADE,
  symbol text NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE(owner, symbol)
);

CREATE TABLE alert_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid REFERENCES profiles ON DELETE CASCADE,
  alert_type text NOT NULL,
  message text NOT NULL,
  status text NOT NULL,
  created_at timestamptz DEFAULT now()
);

CREATE TABLE trading_rules (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid REFERENCES profiles ON DELETE CASCADE,
  asset_symbol text NOT NULL,
  strategy text NOT NULL DEFAULT 'Trend Following',
  is_active boolean NOT NULL DEFAULT false,
  mode text NOT NULL DEFAULT 'paper' CHECK (mode IN ('paper', 'live')),
  stop_loss_pct numeric(8,4) NOT NULL DEFAULT 2.0,
  trailing_stop_pct numeric(8,4) NOT NULL DEFAULT 1.2,
  take_profit_pct numeric(8,4) NOT NULL DEFAULT 5.0,
  max_capital numeric(18,2) NOT NULL DEFAULT 1000.00,
  max_daily_loss numeric(18,2) NOT NULL DEFAULT 500.00,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  UNIQUE(owner_id, asset_symbol)
);

CREATE TABLE bot_execution_logs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid REFERENCES profiles ON DELETE CASCADE,
  timestamp timestamptz NOT NULL DEFAULT now(),
  asset_symbol text NOT NULL,
  signal_received text NOT NULL,
  action_taken text NOT NULL CHECK (action_taken IN ('Executed', 'Rejected', 'Pending')),
  execution_price numeric(18,6),
  reject_reason text
);

CREATE TABLE bot_active_positions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid REFERENCES profiles ON DELETE CASCADE,
  asset_symbol text NOT NULL,
  entry_price numeric(18,6) NOT NULL DEFAULT 0,
  current_price numeric(18,6) NOT NULL DEFAULT 0,
  quantity numeric(18,8) NOT NULL DEFAULT 0,
  unrealized_pl numeric(18,6) NOT NULL DEFAULT 0,
  trailing_stop_level numeric(18,6),
  opened_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(owner_id, asset_symbol)
);

CREATE TABLE finbert_telemetry_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid REFERENCES profiles ON DELETE CASCADE,
  provider text NOT NULL DEFAULT 'newsapi',
  article_count int NOT NULL DEFAULT 0,
  positive_count int NOT NULL DEFAULT 0,
  neutral_count int NOT NULL DEFAULT 0,
  negative_count int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE forecaster_telemetry_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid REFERENCES profiles ON DELETE CASCADE,
  asset_symbol text NOT NULL,
  raw_forecast_score numeric(12,6) NOT NULL DEFAULT 0,
  bull_threshold numeric(12,6) NOT NULL DEFAULT 0.2,
  bear_threshold numeric(12,6) NOT NULL DEFAULT -0.2,
  treeshap_log text,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE llm_telemetry_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  owner_id uuid REFERENCES profiles ON DELETE CASCADE,
  asset_symbol text,
  prompt text NOT NULL,
  model_used text NOT NULL DEFAULT 'unknown',
  latency_ms numeric(18,4) NOT NULL DEFAULT 0,
  prompt_tokens int NOT NULL DEFAULT 0,
  completion_tokens int NOT NULL DEFAULT 0,
  total_tokens int NOT NULL DEFAULT 0,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_trading_rules_owner_asset ON trading_rules(owner_id, asset_symbol);
CREATE INDEX idx_bot_logs_owner_time ON bot_execution_logs(owner_id, timestamp DESC);
CREATE INDEX idx_bot_logs_owner_asset_time ON bot_execution_logs(owner_id, asset_symbol, timestamp DESC);
CREATE INDEX idx_bot_positions_owner_asset ON bot_active_positions(owner_id, asset_symbol);
CREATE INDEX idx_bot_positions_owner_updated ON bot_active_positions(owner_id, updated_at DESC);
CREATE INDEX idx_finbert_telemetry_owner_time ON finbert_telemetry_events(owner_id, created_at DESC);
CREATE INDEX idx_forecaster_telemetry_owner_time ON forecaster_telemetry_events(owner_id, created_at DESC);
CREATE INDEX idx_llm_telemetry_owner_time ON llm_telemetry_events(owner_id, created_at DESC);

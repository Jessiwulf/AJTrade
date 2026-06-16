
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$ BEGIN
  CREATE TYPE user_role AS ENUM ('guest', 'authenticated_user', 'admin');
EXCEPTION
  WHEN duplicate_object THEN NULL;
END $$;


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

CREATE TABLE IF NOT EXISTS profiles (
  id uuid PRIMARY KEY REFERENCES auth.users ON DELETE CASCADE,
  full_name text,
  avatar_url text,
  role user_role NOT NULL DEFAULT 'authenticated_user',
  suspended_at timestamptz,
  created_at timestamptz DEFAULT now()
);

-- Feature #5: Performance Analytics & Market Dashboard
-- Trading history: tracks all buy/sell transactions for P/L calculation
CREATE TABLE IF NOT EXISTS trading_history (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id uuid NOT NULL REFERENCES portfolios ON DELETE CASCADE,
  symbol text NOT NULL,
  trade_type text NOT NULL CHECK (trade_type IN ('BUY', 'SELL')),
  quantity numeric(18,6) NOT NULL,
  price numeric(18,6) NOT NULL,
  notional numeric(18,2) NOT NULL,
  fee numeric(18,2) DEFAULT 0,
  pl numeric(18,2) DEFAULT 0,  -- realized P/L for SELL orders
  signal_source text,  -- 'ai', 'manual', 'bot'
  notes text,
  created_at timestamptz DEFAULT now(),
  UNIQUE(id)
);

-- Market sentiment: aggregated sentiment per asset per day
CREATE TABLE IF NOT EXISTS market_sentiment (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  symbol text NOT NULL,
  sentiment_date date NOT NULL,
  avg_sentiment numeric(5,2),  -- -1 (negative) to +1 (positive)
  positive_count int DEFAULT 0,
  negative_count int DEFAULT 0,
  neutral_count int DEFAULT 0,
  total_articles int DEFAULT 0,
  heatmap_label text,  -- 'Very Bullish', 'Bullish', 'Neutral', 'Bearish', 'Very Bearish'
  created_at timestamptz DEFAULT now(),
  UNIQUE(symbol, sentiment_date)
);

-- Performance metrics: cache portfolio metrics for dashboard (updated daily/hourly)
CREATE TABLE IF NOT EXISTS performance_metrics (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  portfolio_id uuid NOT NULL REFERENCES portfolios ON DELETE CASCADE,
  metric_date date NOT NULL,
  total_value numeric(18,2) NOT NULL,  -- cash + positions value
  cash_balance numeric(18,2) NOT NULL,
  positions_value numeric(18,2) NOT NULL,
  total_pl numeric(18,2) DEFAULT 0,  -- realized + unrealized P/L
  unrealized_pl numeric(18,2) DEFAULT 0,
  realized_pl numeric(18,2) DEFAULT 0,
  win_rate numeric(5,2) DEFAULT 0,  -- % of winning trades
  total_trades int DEFAULT 0,
  winning_trades int DEFAULT 0,
  daily_return numeric(5,4) DEFAULT 0,  -- today's return %
  created_at timestamptz DEFAULT now(),
  UNIQUE(portfolio_id, metric_date)
);

-- Create indexes for faster queries
CREATE INDEX IF NOT EXISTS idx_trading_history_portfolio ON trading_history(portfolio_id);
CREATE INDEX IF NOT EXISTS idx_trading_history_symbol ON trading_history(symbol);
CREATE INDEX IF NOT EXISTS idx_trading_history_created ON trading_history(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_market_sentiment_symbol_date ON market_sentiment(symbol, sentiment_date DESC);
CREATE INDEX IF NOT EXISTS idx_performance_metrics_portfolio_date ON performance_metrics(portfolio_id, metric_date DESC);

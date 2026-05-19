-- Supabase-compatible schema (draft)

-- Note: Supabase provides `auth.users`; keep a `profiles` table for user metadata.

CREATE TABLE profiles (
  id uuid PRIMARY KEY REFERENCES auth.users ON DELETE CASCADE,
  full_name text,
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

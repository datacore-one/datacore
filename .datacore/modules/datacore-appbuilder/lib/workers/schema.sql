-- D1 Schema for datacore-appbuilder workers
-- Run: wrangler d1 execute DB_NAME --file=schema.sql

-- Licenses (managed by license worker)
CREATE TABLE IF NOT EXISTS licenses (
  id TEXT PRIMARY KEY,
  app_id TEXT NOT NULL,
  email TEXT NOT NULL,
  signature TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  revoked INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_licenses_email_app ON licenses (email, app_id);

-- Credit balances (managed by AI proxy)
CREATE TABLE IF NOT EXISTS credits (
  email TEXT PRIMARY KEY,
  balance REAL NOT NULL DEFAULT 0
);

-- AI usage tracking (managed by AI proxy)
CREATE TABLE IF NOT EXISTS usage (
  id TEXT PRIMARY KEY,
  license_email TEXT NOT NULL,
  app_id TEXT NOT NULL,
  model TEXT NOT NULL,
  input_tokens INTEGER NOT NULL,
  output_tokens INTEGER NOT NULL,
  credits_used REAL NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_usage_email ON usage (license_email);
CREATE INDEX IF NOT EXISTS idx_usage_created ON usage (created_at);

-- Rate limiting (managed by AI proxy)
CREATE TABLE IF NOT EXISTS rate_limits (
  license_email TEXT NOT NULL,
  app_id TEXT NOT NULL,
  day TEXT NOT NULL,
  call_count INTEGER DEFAULT 0,
  PRIMARY KEY (license_email, app_id, day)
);

-- Analytics events (fire-and-forget from apps)
CREATE TABLE IF NOT EXISTS events (
  id TEXT PRIMARY KEY,
  app_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload TEXT,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_app ON events (app_id, created_at);

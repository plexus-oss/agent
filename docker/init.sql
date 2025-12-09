-- Plexus Self-Host Database Initialization
-- This script runs on first startup to create the required tables

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Create telemetry table (main data storage)
CREATE TABLE IF NOT EXISTS telemetry (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  org_id TEXT NOT NULL DEFAULT 'default',
  metric TEXT NOT NULL,
  value DOUBLE PRECISION NOT NULL,
  timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  device_id TEXT,
  session_id TEXT,
  tags JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_telemetry_org_metric_time ON telemetry(org_id, metric, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_org_time ON telemetry(org_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_device ON telemetry(device_id, timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_telemetry_session ON telemetry(session_id, timestamp DESC);

-- Sessions table
CREATE TABLE IF NOT EXISTS sessions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  org_id TEXT NOT NULL DEFAULT 'default',
  session_id TEXT NOT NULL,
  device_id TEXT,
  started_at TIMESTAMPTZ DEFAULT NOW(),
  ended_at TIMESTAMPTZ,
  status TEXT DEFAULT 'active',
  tags JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_org ON sessions(org_id);
CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);

-- Dashboards table
CREATE TABLE IF NOT EXISTS dashboards (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  org_id TEXT NOT NULL DEFAULT 'default',
  name TEXT NOT NULL,
  description TEXT,
  config JSONB NOT NULL DEFAULT '{"panels": [], "timeRange": {"type": "relative", "value": "15m"}}',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  created_by TEXT,
  updated_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_dashboards_org ON dashboards(org_id);

-- API keys table
CREATE TABLE IF NOT EXISTS api_keys (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  org_id TEXT NOT NULL DEFAULT 'default',
  name TEXT NOT NULL,
  key_prefix TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  active BOOLEAN DEFAULT true,
  scopes TEXT[] DEFAULT ARRAY['otlp:write'],
  last_used_at TIMESTAMPTZ,
  request_count BIGINT DEFAULT 0,
  created_by TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ,
  revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_api_keys_org ON api_keys(org_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

-- Device auth requests (for CLI login flow)
CREATE TABLE IF NOT EXISTS device_auth_requests (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  device_code TEXT NOT NULL UNIQUE,
  user_code TEXT NOT NULL UNIQUE,
  status TEXT NOT NULL DEFAULT 'pending',
  api_key TEXT,
  org_id TEXT,
  user_id TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  expires_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_device_auth_device_code ON device_auth_requests(device_code);
CREATE INDEX IF NOT EXISTS idx_device_auth_user_code ON device_auth_requests(user_code);

-- Local users table (for self-host mode without Clerk)
CREATE TABLE IF NOT EXISTS local_users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  name TEXT,
  org_id TEXT NOT NULL DEFAULT 'default',
  created_at TIMESTAMPTZ DEFAULT NOW(),
  last_login_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_local_users_email ON local_users(email);

-- Create a default API key for easy setup
-- Key: plx_selfhost_default_key_12345678
-- This should be changed in production!
INSERT INTO api_keys (org_id, name, key_prefix, key_hash, scopes)
VALUES (
  'default',
  'Default Self-Host Key',
  'plx_self',
  encode(sha256('plx_selfhost_default_key_12345678'::bytea), 'hex'),
  ARRAY['otlp:write', 'otlp:read']
) ON CONFLICT DO NOTHING;

-- Function to update API key usage
CREATE OR REPLACE FUNCTION update_api_key_usage(p_key_hash TEXT)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
  UPDATE api_keys
  SET
    last_used_at = NOW(),
    request_count = request_count + 1
  WHERE key_hash = p_key_hash;
END;
$$;

-- Grant permissions
GRANT ALL ON ALL TABLES IN SCHEMA public TO plexus;
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO plexus;

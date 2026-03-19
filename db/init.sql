CREATE TABLE IF NOT EXISTS jaamsim_config (
  config_key TEXT PRIMARY KEY,
  content JSONB NOT NULL
);

-- store each incident analysis
CREATE TABLE IF NOT EXISTS incident_analysis (
  id BIGSERIAL PRIMARY KEY,
  thing_id TEXT,
  alert_type TEXT,
  event_time TIMESTAMPTZ,
  received_at TIMESTAMPTZ DEFAULT now(),
  event JSONB NOT NULL,
  facts JSONB,
  trace JSONB,
  analysis TEXT
);

CREATE INDEX IF NOT EXISTS idx_incident_analysis_time ON incident_analysis(event_time DESC);
CREATE INDEX IF NOT EXISTS idx_incident_analysis_thing ON incident_analysis(thing_id);


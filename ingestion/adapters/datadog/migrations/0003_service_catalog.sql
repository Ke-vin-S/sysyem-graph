-- Staged Datadog Service Catalog (Service Definitions v2.x).
--
-- One row per service definition. Re-fetching the catalog upserts by
-- service_name, so the table always reflects the latest seen state.
-- `raw_json` keeps the full payload — we normalize the fields we use
-- today and leave the rest queryable as JSON for later phases.
CREATE TABLE IF NOT EXISTS services_catalog (
    service_name    TEXT PRIMARY KEY,
    team            TEXT NOT NULL DEFAULT '',
    tier            TEXT NOT NULL DEFAULT '',
    lifecycle       TEXT NOT NULL DEFAULT '',   -- 'production' / 'staging' / 'sandbox' / etc.
    application     TEXT NOT NULL DEFAULT '',   -- application / system this service belongs to
    description     TEXT NOT NULL DEFAULT '',
    language        TEXT NOT NULL DEFAULT '',
    owner_email     TEXT NOT NULL DEFAULT '',   -- primary contact email when set
    repo_url        TEXT NOT NULL DEFAULT '',   -- first repo URL when there are multiple
    repos_json      TEXT NOT NULL DEFAULT '[]', -- list of {name, provider, url}
    links_json      TEXT NOT NULL DEFAULT '{}', -- name -> url map (runbook, dashboard, docs, …)
    contacts_json   TEXT NOT NULL DEFAULT '[]', -- list of {name, type, contact}
    raw_json        TEXT NOT NULL DEFAULT '{}',
    schema_version  TEXT NOT NULL DEFAULT '',
    fetched_at      TEXT NOT NULL
);

-- Filter by team / tier comes up in operator queries ("show me all
-- payment-team services"); cheap to keep these indexed.
CREATE INDEX IF NOT EXISTS idx_services_catalog_team ON services_catalog(team);
CREATE INDEX IF NOT EXISTS idx_services_catalog_tier ON services_catalog(tier);

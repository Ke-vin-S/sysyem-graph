-- Track which migrations have been applied. Single-row-per-version,
-- ordered by `version` for determinism.
CREATE TABLE IF NOT EXISTS _migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    applied_at TEXT    NOT NULL
);

-- Per-API fetch audit. Lets us answer "when did we last pull spans?",
-- "did the catalog fetch succeed?", and drives the freshness/TTL logic.
-- One row per fetch attempt (success OR failure) so partial pulls are
-- recoverable: a later run sees the failed row and retries.
CREATE TABLE IF NOT EXISTS fetch_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    api           TEXT    NOT NULL,
    fetched_at    TEXT    NOT NULL,
    rows_written  INTEGER NOT NULL DEFAULT 0,
    status        TEXT    NOT NULL CHECK (status IN ('success','partial','failed')),
    error         TEXT    NOT NULL DEFAULT '',
    duration_ms   REAL    NOT NULL DEFAULT 0
);

-- Hot path: "give me the most recent successful fetch for API X" for the
-- staleness check. Filtered index on success only — we don't TTL-skip
-- because of a failed fetch.
CREATE INDEX IF NOT EXISTS idx_fetch_log_api_success
    ON fetch_log(api, fetched_at DESC)
    WHERE status = 'success';

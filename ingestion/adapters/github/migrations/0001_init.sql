-- Track which migrations have been applied. Mirrors the Datadog store
-- migration runner. Idempotent: CREATE statements all use IF NOT EXISTS so
-- partially-applied state self-heals on the next run.
CREATE TABLE IF NOT EXISTS _migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    applied_at TEXT    NOT NULL
);

-- One row per registered GitHub repository. `url` is the canonical
-- https://github.com/owner/name URL (we normalize short-form `owner/name`
-- at registration time).
CREATE TABLE IF NOT EXISTS repos (
    url                TEXT    PRIMARY KEY,
    owner              TEXT    NOT NULL,
    name               TEXT    NOT NULL,
    default_branch     TEXT,
    clone_path         TEXT    NOT NULL,
    last_commit_sha    TEXT,
    last_ingested_at   TEXT,
    last_ingested_sha  TEXT,
    status             TEXT    NOT NULL DEFAULT 'registered'
                       CHECK (status IN ('registered','cloned','ingested','error')),
    last_error         TEXT    NOT NULL DEFAULT '',
    added_at           TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_repos_status         ON repos(status);
CREATE INDEX IF NOT EXISTS idx_repos_last_ingested  ON repos(last_ingested_at);

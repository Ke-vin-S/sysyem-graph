-- Staged Datadog APM spans.
--
-- Natural key is (trace_id, span_id): the same span fetched again is the
-- same row, so re-fetching overlapping windows is safe (INSERT OR REPLACE
-- semantics in the writer).
--
-- `tags_json` keeps the full tag map; we don't normalize tags into a
-- side-table because the parser deserializes them whole into the
-- in-memory RawSpan and never queries inside tags from SQL.
CREATE TABLE IF NOT EXISTS spans (
    trace_id    TEXT    NOT NULL,
    span_id     TEXT    NOT NULL,
    parent_id   TEXT,
    service     TEXT    NOT NULL,
    resource    TEXT    NOT NULL DEFAULT '',
    operation   TEXT    NOT NULL DEFAULT '',
    type        TEXT    NOT NULL DEFAULT '',
    start       TEXT    NOT NULL,            -- ISO-8601 datetime
    duration_ms REAL    NOT NULL DEFAULT 0,
    error       INTEGER NOT NULL DEFAULT 0,  -- 0/1
    tags_json   TEXT    NOT NULL DEFAULT '{}',
    env         TEXT    NOT NULL DEFAULT '',
    fetched_at  TEXT    NOT NULL,
    PRIMARY KEY (trace_id, span_id)
);

-- Trace-tree reconstruction: "give me every span in this trace" must be
-- cheap. The PK covers it (leading column = trace_id).

-- Time-windowed reads: parser asks "spans since T (optionally for service S)".
CREATE INDEX IF NOT EXISTS idx_spans_service_start ON spans(service, start);
CREATE INDEX IF NOT EXISTS idx_spans_start ON spans(start);

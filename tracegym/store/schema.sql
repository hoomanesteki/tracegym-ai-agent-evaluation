-- TraceGym storage schema.
--
-- Everything large (agent inputs, tool outputs, LLM messages) is kept out of the
-- rows and stored once in the content-addressed blob store; the tables carry the
-- SHA-256 pointers. That keeps the DB small, makes identical payloads share
-- storage automatically, and gives every artifact a stable identity for caching.
--
-- All statements are idempotent so connect() can apply this file every time.
--
-- Foreign keys are declared to document the relationships but left unenforced
-- (SQLite's default). Capture streams spans as the agent runs and finalizes the
-- parent trace row at the end, so rows are legitimately written child-first.

-- A single recording session: point an agent at the proxy, run it, get a session.
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    mode        TEXT NOT NULL,              -- live | frozen_strict | frozen_record
    created_at  TEXT NOT NULL,
    meta        TEXT                        -- JSON
);

-- One top-level agent invocation (request in, final answer out).
CREATE TABLE IF NOT EXISTS traces (
    id          TEXT PRIMARY KEY,
    session_id  TEXT,
    case_id     TEXT,                       -- set when the trace backs a golden case
    input_sha   TEXT NOT NULL,
    output_sha  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    meta        TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions (id)
);

-- OTel-style spans within a trace: LLM calls and tool calls.
-- attributes holds the GenAI semconv fields (gen_ai.*) plus cost_usd.
CREATE TABLE IF NOT EXISTS spans (
    id          TEXT PRIMARY KEY,
    trace_id    TEXT NOT NULL,
    parent_id   TEXT,
    kind        TEXT NOT NULL,              -- llm | tool | agent
    name        TEXT NOT NULL,
    input_sha   TEXT,
    output_sha  TEXT,
    start_ns    INTEGER,
    end_ns      INTEGER,
    status      TEXT,
    attributes  TEXT,                       -- JSON
    FOREIGN KEY (trace_id) REFERENCES traces (id)
);
CREATE INDEX IF NOT EXISTS idx_spans_trace ON spans (trace_id);

-- Recorded tool/LLM I/O for deterministic replay, keyed by a hash of the call.
CREATE TABLE IF NOT EXISTS fixtures (
    key         TEXT PRIMARY KEY,           -- sha256(fn_name + canonical_json(kwargs))
    kind        TEXT NOT NULL,              -- tool | llm
    fn_name     TEXT NOT NULL,
    input_sha   TEXT NOT NULL,
    output_sha  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

-- A golden suite (support-rag, sql-analyst, meta-judge).
CREATE TABLE IF NOT EXISTS suites (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    rubric_sha  TEXT,
    created_at  TEXT NOT NULL
);

-- One case in a suite. input/checks/expected are JSON payloads.
CREATE TABLE IF NOT EXISTS cases (
    id          TEXT PRIMARY KEY,
    suite_id    TEXT NOT NULL,
    input       TEXT NOT NULL,              -- JSON
    checks      TEXT NOT NULL,              -- JSON list of check specs
    expected    TEXT,                       -- JSON
    tags        TEXT,                       -- JSON list
    created_at  TEXT NOT NULL,
    FOREIGN KEY (suite_id) REFERENCES suites (id)
);
CREATE INDEX IF NOT EXISTS idx_cases_suite ON cases (suite_id);

-- One execution of a suite. summary is a JSON rollup (pass rate, cost, ...).
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    suite_id    TEXT NOT NULL,
    mode        TEXT NOT NULL,              -- frozen | live
    model       TEXT,
    git_sha     TEXT,
    config_sha  TEXT,
    created_at  TEXT NOT NULL,
    summary     TEXT,                       -- JSON
    FOREIGN KEY (suite_id) REFERENCES suites (id)
);

-- Per-case outcome within a run. score is the [0,1] quality used by the gate.
-- Token and tool-call counts are denormalized here (also derivable from spans)
-- so the cost/speed advisor can profile a run with plain aggregate queries.
CREATE TABLE IF NOT EXISTS results (
    id                   TEXT PRIMARY KEY,
    run_id               TEXT NOT NULL,
    case_id              TEXT NOT NULL,
    trace_id             TEXT,
    output_sha           TEXT NOT NULL,
    l1_results           TEXT,              -- JSON list of CheckResult
    l1_invariant_fail    INTEGER NOT NULL DEFAULT 0,
    judge_pass           INTEGER,           -- 0/1/NULL
    score                REAL NOT NULL DEFAULT 0,
    cost_usd             REAL NOT NULL DEFAULT 0,
    latency_ms           REAL NOT NULL DEFAULT 0,
    input_tokens         INTEGER NOT NULL DEFAULT 0,
    output_tokens        INTEGER NOT NULL DEFAULT 0,
    tool_calls           INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs (id),
    FOREIGN KEY (case_id) REFERENCES cases (id)
);
CREATE INDEX IF NOT EXISTS idx_results_run ON results (run_id);

-- Cached LLM-judge verdicts. The (case, output, rubric, model) tuple is the
-- cache key: we never pay to judge the same output under the same rubric twice.
CREATE TABLE IF NOT EXISTS judgments (
    id          TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL,
    output_sha  TEXT NOT NULL,
    rubric_sha  TEXT NOT NULL,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    scores      TEXT,                       -- JSON {criterion: score}
    pass        INTEGER,                    -- 0/1
    rationale   TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (case_id, output_sha, rubric_sha, model)
);

-- Human labels for judge calibration. round distinguishes intra-rater relabels.
CREATE TABLE IF NOT EXISTS labels (
    id          TEXT PRIMARY KEY,
    case_id     TEXT NOT NULL,
    output_sha  TEXT NOT NULL,
    label_pass  INTEGER NOT NULL,           -- 0/1
    labeler     TEXT NOT NULL,
    round       INTEGER NOT NULL DEFAULT 1,
    notes       TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (case_id, output_sha, labeler, round)
);

-- Promoted baselines the gate compares against. name is usually "baseline".
CREATE TABLE IF NOT EXISTS baselines (
    name        TEXT PRIMARY KEY,
    suite_id    TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    promoted_at TEXT NOT NULL,
    note        TEXT,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);

-- Stead Preview demo schema. Single bound household, single tester.
-- Re-running this file is safe: every statement is IF NOT EXISTS.
-- No credential, token or raw transcript is ever stored here.

CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    household   TEXT NOT NULL,
    name        TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'general',
    value       TEXT NOT NULL,
    provenance  TEXT NOT NULL,
    -- 'stated' (Kerstin said it) or 'web' (searched, and approved by her).
    -- Unlike provenance, which the model writes freely, this is set by which
    -- path stored the row.
    source      TEXT NOT NULL DEFAULT 'stated',
    source_url  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    UNIQUE (household, name, scope)
);

CREATE TABLE IF NOT EXISTS members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    household   TEXT NOT NULL,
    name        TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL,
    UNIQUE (household, name)
);

CREATE TABLE IF NOT EXISTS goals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    household   TEXT NOT NULL,
    title       TEXT NOT NULL,
    detail      TEXT,
    due         TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    household   TEXT NOT NULL,
    title       TEXT NOT NULL,
    occurs_on   TEXT,
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    household   TEXT NOT NULL,
    goal_id     INTEGER REFERENCES goals(id),
    title       TEXT NOT NULL,
    due         TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    created_at  TEXT NOT NULL,
    resolved_at TEXT
);

-- Domain-level approval. A proposal is inert until explicitly approved.
CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    household   TEXT NOT NULL,
    ref         TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    payload     TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    created_at  TEXT NOT NULL,
    decided_at  TEXT
);

CREATE TABLE IF NOT EXISTS reminders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    household    TEXT NOT NULL,
    task_id      INTEGER REFERENCES tasks(id),
    proposal_id  INTEGER REFERENCES proposals(id),
    fire_at      TEXT NOT NULL,
    message      TEXT NOT NULL,
    delivered_at TEXT,
    created_at   TEXT NOT NULL,
    -- Hermes cron job id, set once the trusted scheduler has placed the job.
    -- Its presence is what makes scheduling idempotent.
    cron_job_id  TEXT
);

CREATE TABLE IF NOT EXISTS outcomes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    household   TEXT NOT NULL,
    task_id     INTEGER REFERENCES tasks(id),
    outcome     TEXT NOT NULL,
    verified_by TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    household   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    detail      TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (household, status);
CREATE INDEX IF NOT EXISTS idx_reminders_fire ON reminders (household, fire_at);
CREATE INDEX IF NOT EXISTS idx_proposals_ref ON proposals (ref);

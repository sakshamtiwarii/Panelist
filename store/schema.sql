-- Panelist — schedule state.
--
-- The dataset tables exist so "who is affected" is a query rather than a scan
-- of a JSON blob: a coordinator asking "which students does this company's
-- delay touch, and what else do they have that day" is a join, and answering
-- it in Python means loading the whole week into memory first.
--
-- Schedules are VERSIONED rather than mutated. A replan writes a new version
-- and flips `is_current`, so the schedule that existed before a disruption is
-- still there to diff against and to roll back to. Mutating in place would
-- destroy exactly the prior state the replanner needs.

CREATE TABLE IF NOT EXISTS datasets (
    name        TEXT PRIMARY KEY,
    seed        INTEGER     NOT NULL,
    config      JSONB       NOT NULL,
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS companies (
    dataset            TEXT     NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    id                 TEXT     NOT NULL,
    name               TEXT     NOT NULL,
    tier               SMALLINT NOT NULL,
    cgpa_cutoff        REAL     NOT NULL,
    panel_count        SMALLINT NOT NULL,
    interview_minutes  SMALLINT NOT NULL,
    duration_slots     SMALLINT NOT NULL,
    preferred_day      SMALLINT,
    shortlist_size     INTEGER  NOT NULL,
    PRIMARY KEY (dataset, id)
);

-- Disruption state that must outlive the replan that caused it: a company
-- that arrived late really was unavailable, and a panel that walked out is
-- still gone. Without these a second replan would silently forget the first.
ALTER TABLE companies ADD COLUMN IF NOT EXISTS
    unavailable_windows JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS
    panel_blackouts JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS students (
    dataset TEXT NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    id      TEXT NOT NULL,
    cgpa    REAL NOT NULL,
    branch  TEXT NOT NULL,
    PRIMARY KEY (dataset, id)
);

-- The shortlist relation is the thing that makes the problem hard, so it gets
-- to be a real table rather than an array column: it is the join that answers
-- contention questions ("which companies compete for this student").
CREATE TABLE IF NOT EXISTS shortlists (
    dataset    TEXT NOT NULL,
    company_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    PRIMARY KEY (dataset, company_id, student_id),
    FOREIGN KEY (dataset, company_id) REFERENCES companies(dataset, id) ON DELETE CASCADE,
    FOREIGN KEY (dataset, student_id) REFERENCES students(dataset, id)  ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS shortlists_student_idx ON shortlists (dataset, student_id);

CREATE TABLE IF NOT EXISTS rooms (
    dataset         TEXT  NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    id              TEXT  NOT NULL,
    name            TEXT  NOT NULL,
    blocked_windows JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (dataset, id)
);

CREATE TABLE IF NOT EXISTS schedules (
    id            SERIAL PRIMARY KEY,
    dataset       TEXT    NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    version       INTEGER NOT NULL,
    is_current    BOOLEAN NOT NULL DEFAULT FALSE,
    origin        TEXT    NOT NULL DEFAULT 'solve',   -- 'solve' | 'replan'
    solver_status TEXT,
    solve_seconds REAL,
    metrics       JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (dataset, version)
);

-- At most one current schedule per dataset, enforced by the database rather
-- than by remembering to clear the old flag.
CREATE UNIQUE INDEX IF NOT EXISTS schedules_one_current
    ON schedules (dataset) WHERE is_current;

CREATE TABLE IF NOT EXISTS appointments (
    schedule_id    INTEGER  NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    interview_id   TEXT     NOT NULL,
    company_id     TEXT     NOT NULL,
    student_id     TEXT     NOT NULL,
    day            SMALLINT NOT NULL,
    slot           SMALLINT NOT NULL,
    start_slot     INTEGER  NOT NULL,
    end_slot       INTEGER  NOT NULL,
    duration_slots SMALLINT NOT NULL,
    tier           SMALLINT NOT NULL,
    room           TEXT,
    panel          SMALLINT,
    PRIMARY KEY (schedule_id, interview_id)
);
CREATE INDEX IF NOT EXISTS appointments_student_idx ON appointments (schedule_id, student_id);
CREATE INDEX IF NOT EXISTS appointments_company_idx ON appointments (schedule_id, company_id);
CREATE INDEX IF NOT EXISTS appointments_board_idx   ON appointments (schedule_id, day, room);

CREATE TABLE IF NOT EXISTS unscheduled (
    schedule_id  INTEGER NOT NULL REFERENCES schedules(id) ON DELETE CASCADE,
    interview_id TEXT    NOT NULL,
    PRIMARY KEY (schedule_id, interview_id)
);

-- Audit trail: every applied replan, what caused it, and what it cost. Lets
-- the coordinator answer "what have we already moved today, and why" — which
-- a schedule alone cannot say.
CREATE TABLE IF NOT EXISTS replan_events (
    id             SERIAL PRIMARY KEY,
    dataset        TEXT    NOT NULL,
    from_schedule  INTEGER REFERENCES schedules(id) ON DELETE SET NULL,
    to_schedule    INTEGER REFERENCES schedules(id) ON DELETE SET NULL,
    applied_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    disruptions    JSONB   NOT NULL,
    descriptions   JSONB   NOT NULL,
    elective_churn INTEGER NOT NULL,
    forced_churn   INTEGER NOT NULL,
    churn_pct      REAL    NOT NULL,
    cap_exceeded   BOOLEAN NOT NULL DEFAULT FALSE,
    notify_count   INTEGER NOT NULL DEFAULT 0,
    diff           JSONB,
    notify         JSONB
);
CREATE INDEX IF NOT EXISTS replan_events_dataset_idx ON replan_events (dataset, applied_at DESC);

-- Accounts. Passwords are stored as scrypt hashes with a per-user salt --
-- never plaintext, never a bare digest. `role` gates mutation: a viewer can
-- read the board and inspect proposals but cannot apply one, which is the
-- distinction that matters here (a replan changes hundreds of people's days).
CREATE TABLE IF NOT EXISTS users (
    username      TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('coordinator', 'viewer')),
    salt          BYTEA NOT NULL,
    password_hash BYTEA NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login    TIMESTAMPTZ
);

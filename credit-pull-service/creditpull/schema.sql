-- ─────────────────────────────────────────────────────────────────────
-- Credit Pull module — Supabase / Postgres schema
-- ─────────────────────────────────────────────────────────────────────
-- Paste this whole file into Supabase SQL editor and run.
-- All tables are CASCADE-deleted from credit_subjects → credit_reports
-- → child tables, so deleting a subject cleans up everything.
--
-- 10 tables total:
--   credit_subjects                  (1 row per applicant)
--   credit_reports                   (1 row per pull, FK subject_id)
--   credit_report_questions          (security Qs asked during a pull)
--   credit_report_personal_info      (Personal Information section)
--   credit_report_scores             (Credit Score section, scores)
--   credit_report_risk_factors       (Credit Score section, risk factors)
--   credit_report_summary            (Summary section)
--   credit_report_accounts           (Account History — 1 row per account)
--   credit_report_account_details    (per account, per bureau)
--   credit_report_payment_history    (per account, per bureau, per month)
-- ─────────────────────────────────────────────────────────────────────

-- ── 1. Subject identity ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS credit_subjects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name       TEXT NOT NULL,
    date_of_birth   DATE,
    ssn_last4       TEXT,
    email           TEXT,
    phone           TEXT,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_subjects_full_name ON credit_subjects(full_name);
CREATE INDEX IF NOT EXISTS idx_subjects_dob_ssn   ON credit_subjects(date_of_birth, ssn_last4);


-- ── 2. Credit report (1 row per pull) ────────────────────────────────
CREATE TABLE IF NOT EXISTS credit_reports (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject_id        UUID NOT NULL REFERENCES credit_subjects(id) ON DELETE CASCADE,
    reference_number  TEXT,                  -- IIQ's M10889879
    report_date       DATE,                  -- date the report data was generated
    pulled_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status            TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','running','awaiting_input','awaiting_credentials','done','failed')),
    error_message     TEXT,
    raw_html          TEXT,                  -- optional, for debug — null after stable
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_reports_subject       ON credit_reports(subject_id);
CREATE INDEX IF NOT EXISTS idx_reports_pulled_at     ON credit_reports(pulled_at DESC);
CREATE INDEX IF NOT EXISTS idx_reports_active_status ON credit_reports(status)
    WHERE status IN ('pending','running','awaiting_input');


-- ── 3. Security questions asked during a pull ────────────────────────
CREATE TABLE IF NOT EXISTS credit_report_questions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       UUID NOT NULL REFERENCES credit_reports(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,        -- 1, 2, 3 if multiple questions appear
    question_text   TEXT NOT NULL,
    question_type   TEXT NOT NULL CHECK (question_type IN ('text','choice')),
    options         JSONB,                   -- ["Boston","Chicago"] for 'choice', else NULL
    answer          TEXT,                    -- filled by user via API
    asked_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    answered_at     TIMESTAMPTZ,
    attempt_count   INTEGER NOT NULL DEFAULT 1   -- incremented on wrong-answer retries; >1 means user is on retry N-1
);

CREATE INDEX IF NOT EXISTS idx_questions_report        ON credit_report_questions(report_id);
CREATE INDEX IF NOT EXISTS idx_questions_unanswered    ON credit_report_questions(report_id)
    WHERE answered_at IS NULL;


-- ── 4. Personal Information rows (label, bureau, value triples) ──────
CREATE TABLE IF NOT EXISTS credit_report_personal_info (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id   UUID NOT NULL REFERENCES credit_reports(id) ON DELETE CASCADE,
    field_name  TEXT NOT NULL,               -- 'Name', 'Date of Birth', 'Current Address(es)'
    bureau      TEXT NOT NULL CHECK (bureau IN ('transunion','experian','equifax')),
    value       TEXT
);

CREATE INDEX IF NOT EXISTS idx_pi_report ON credit_report_personal_info(report_id);


-- ── 5. Credit Scores per bureau ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS credit_report_scores (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id     UUID NOT NULL REFERENCES credit_reports(id) ON DELETE CASCADE,
    bureau        TEXT NOT NULL CHECK (bureau IN ('transunion','experian','equifax')),
    score         INTEGER,
    lender_rank   TEXT,                      -- 'Great', 'Good', 'Fair', 'Poor'
    score_scale   TEXT,                      -- e.g. '300-850'
    UNIQUE (report_id, bureau)
);

CREATE INDEX IF NOT EXISTS idx_scores_report ON credit_report_scores(report_id);


-- ── 6. Risk Factors per bureau (variable-length list) ────────────────
CREATE TABLE IF NOT EXISTS credit_report_risk_factors (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id   UUID NOT NULL REFERENCES credit_reports(id) ON DELETE CASCADE,
    bureau      TEXT NOT NULL CHECK (bureau IN ('transunion','experian','equifax')),
    position    INTEGER NOT NULL,            -- preserves display order
    factor      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_risk_report ON credit_report_risk_factors(report_id);


-- ── 7. Summary stats per bureau ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS credit_report_summary (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       UUID NOT NULL REFERENCES credit_reports(id) ON DELETE CASCADE,
    bureau          TEXT NOT NULL CHECK (bureau IN ('transunion','experian','equifax')),
    total_accounts  INTEGER,
    open_accounts   INTEGER,
    closed_accounts INTEGER,
    delinquent      INTEGER,
    derogatory      INTEGER,
    collection      INTEGER,
    balances        NUMERIC(14, 2),
    payments        NUMERIC(14, 2),
    public_records  INTEGER,
    inquiries_2yr   INTEGER,
    UNIQUE (report_id, bureau)
);

CREATE INDEX IF NOT EXISTS idx_summary_report ON credit_report_summary(report_id);


-- ── 8. Accounts (one row per account, regardless of bureau) ──────────
CREATE TABLE IF NOT EXISTS credit_report_accounts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id       UUID NOT NULL REFERENCES credit_reports(id) ON DELETE CASCADE,
    position        INTEGER NOT NULL,        -- order in the original report
    creditor_name   TEXT NOT NULL            -- e.g. 'JPMCB CARD', 'MACYS/CBNA'
);

CREATE INDEX IF NOT EXISTS idx_accounts_report   ON credit_report_accounts(report_id);
CREATE INDEX IF NOT EXISTS idx_accounts_creditor ON credit_report_accounts(creditor_name);


-- ── 9. Account Details (per account, per bureau) ─────────────────────
CREATE TABLE IF NOT EXISTS credit_report_account_details (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id            UUID NOT NULL REFERENCES credit_report_accounts(id) ON DELETE CASCADE,
    bureau                TEXT NOT NULL CHECK (bureau IN ('transunion','experian','equifax')),
    account_number        TEXT,              -- e.g. '41858014****'
    account_type          TEXT,              -- 'Revolving', 'Installment', etc.
    account_type_detail   TEXT,              -- 'Credit Card', 'Charge account', etc.
    bureau_code           TEXT,              -- 'Individual', 'Joint', 'Authorized User'
    account_status        TEXT,              -- 'Open', 'Closed', 'Paid'
    monthly_payment       NUMERIC(14, 2),
    date_opened           DATE,
    balance               NUMERIC(14, 2),
    no_of_months_terms    INTEGER,
    high_credit           NUMERIC(14, 2),
    credit_limit          NUMERIC(14, 2),
    past_due              NUMERIC(14, 2),
    payment_status        TEXT,              -- 'Current', 'Late 30', 'Charge-off'
    last_reported         DATE,
    comments              TEXT,
    date_last_active      DATE,
    date_of_last_payment  DATE,
    UNIQUE (account_id, bureau)
);

CREATE INDEX IF NOT EXISTS idx_acct_details_account ON credit_report_account_details(account_id);


-- ── 10. Payment History (per account, per bureau, per month) ─────────
CREATE TABLE IF NOT EXISTS credit_report_payment_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id      UUID NOT NULL REFERENCES credit_report_accounts(id) ON DELETE CASCADE,
    bureau          TEXT NOT NULL CHECK (bureau IN ('transunion','experian','equifax')),
    year            INTEGER NOT NULL,
    month           INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    status          TEXT NOT NULL,           -- 'OK', '30', '60', '90', '120', 'CO'
    UNIQUE (account_id, bureau, year, month)
);

CREATE INDEX IF NOT EXISTS idx_ph_account ON credit_report_payment_history(account_id);


-- ─────────────────────────────────────────────────────────────────────
-- Optional: row-level security (uncomment + adapt to your auth model)
-- ─────────────────────────────────────────────────────────────────────
-- ALTER TABLE credit_subjects                ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_reports                 ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_report_questions        ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_report_personal_info    ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_report_scores           ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_report_risk_factors     ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_report_summary          ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_report_accounts         ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_report_account_details  ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE credit_report_payment_history  ENABLE ROW LEVEL SECURITY;

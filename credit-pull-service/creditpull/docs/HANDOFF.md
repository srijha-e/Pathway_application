# Credit Pull Module — Handoff

> Paste this whole file into a new Claude session and say "continue from here" if the original chat is lost.

## Status

**Feature-complete** for the in-process module inside the underwriting CRM. All planned scraping + UI + retry + logging functionality is built. Two items deferred:
- FCRA audit + consent capture (v2)
- Microservice extraction (planned but not started — see `MICROSERVICE_PLAN.md`)

## Goal

A module in this CRM (Flask + Supabase) that lets an underwriter:
1. Onboard a loan applicant (subject)
2. Enter the applicant's IdentityIQ credentials
3. Drive Playwright through IIQ login → security questions → menu → credit report
4. Parse the report and store it in 10 normalized Postgres tables
5. Display 4 sections in the CRM (Personal Info, Credit Score, Summary, Account History)

## Architecture (locked decisions)

- **Scraping:** Playwright headless Chromium → DOM scrape (not JSON capture, not iframes — the report is in the main frame's DOM, just needs ~5s wait for AngularJS to render)
- **Credentials:** entered per-pull, in-memory only (`credentials_store.py`), **never persisted to disk or DB**
- **Subjects:** stored in DB so multiple pulls of the same person link together
- **Storage:** pure relational, **10 tables**, schema verified on local Postgres 18.3
- **Async:** background thread per pull; frontend polls every 2s; security Qs handled via DB-mediated state machine
- **CRM placement:** sidebar item under "Funded" → switches to a Credit Pull panel with 5 views
- **Logging:** credential-scrubbing filter strips `iiq_password`, `iiq_username`, `password`, `answer` from all log records and exception tracebacks

## State machine

```
pending → running
            ↓
    ┌──── awaiting_credentials ──┐    (bad login → user re-enters; no max attempts, IIQ lockout governs)
    │           ↑                 │
    │           ↓                 │
    │       running ──────────────┤
    │           ↓                 │
    │   awaiting_input ──┐        │    (security Q; user submits answer)
    │           ↑        │        │
    │           ↓        │        │
    │       running ─────┘        │    (wrong answer → same Q re-presented with attempt counter; 3 wrong = fail)
    │           ↓                 │
    └────→  done | failed ←───────┘
```

## End-to-end flow

```
POST /api/creditpull/run (creds in body)
  → INSERT credit_reports row (status='pending')
  → write creds to credentials_store (in-memory)
  → spawn thread → run_credit_pull(sb, report_id)
  → return { report_id, status: 'pending' } immediately

Background thread (scraper.py):
  status='running' → Playwright opens IIQ login page
  → login retry loop (no max — IIQ lockout governs):
       try login → if URL changes, success
                 → if URL unchanged, status='awaiting_credentials' + error
                 → wait_for_new_credentials (20-min timeout)
                 → retry with new creds
  → security Q loop (max 3 retries per Q):
       insert/update credit_report_questions row
       status='awaiting_input', poll DB for answer (20-min timeout)
       apply answer to page
       if same Q reappears → wrong, attempt_count++, error message
       if attempt_count > 3 → fail
  → dismiss ad popup if present
  → hover Reports & Scores → click Credit Reports
  → wait 5s for AngularJS render
  → scroll-to-bottom (collects all accounts via infinite scroll)
  → capture HTML → parser.parse_credit_report()
  → persistence.save_parsed_report()  (sets status='done')
  → finally: clear credentials from store, close browser

Frontend (creditpull.js):
  poll GET /api/creditpull/status/<id> every 2s
  if status='awaiting_credentials' → re-show pull form with red error banner
    on submit → POST /api/creditpull/credentials/<id>
  if status='awaiting_input' → show security Q form (text or radio)
    if attempt_count incremented → show "Wrong answer, attempt N of 3" banner
    on submit → POST /api/creditpull/answer/<question_id>
  if status='done' → GET /api/creditpull/report/<id> → render 4 sections
  if status='failed' → show error message
```

## REST API surface (9 endpoints)

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/creditpull/subjects` | Create a new subject |
| GET | `/api/creditpull/subjects` | List subjects |
| GET | `/api/creditpull/subjects/<id>` | Single subject |
| POST | `/api/creditpull/run` | Start a credit pull (body: subject_id, IIQ creds) |
| GET | `/api/creditpull/status/<report_id>` | Poll job status, includes pending question/error |
| POST | `/api/creditpull/answer/<question_id>` | Submit security question answer |
| POST | `/api/creditpull/credentials/<report_id>` | Re-submit creds after `awaiting_credentials` |
| GET | `/api/creditpull/report/<report_id>` | Fetch full parsed report |
| GET | `/api/creditpull/reports?subject_id=...` | List reports |

## Defaults baked in

| | |
|---|---|
| Login retries on bad creds | **Unlimited** — let IIQ's own lockout decide when to stop |
| Security Q wrong-answer retries | **Max 3** before failing |
| User-action timeout (security Q answer / credential re-entry) | **20 min** |
| Browser mode | `headless=True` (production); flip to `False` in `scraper.py` for local debug |
| Frontend poll interval | 2 seconds |
| Account-history scroll cap | 30 attempts, 2.5s settle each |
| Selector / page-changed errors | Hard fail with generic error message |

## Pre-existing env caveats

- **Local venv has broken supabase install** (`storage3` metadata missing). Same issue blocks the existing `leadgen` module (commented out at `app1.py:58, 82`). User only deploys + tests on Railway, so this doesn't block. All Python files verified clean via `./venv/bin/python -m py_compile`.
- **Local Postgres test DB** `creditpull_test` exists with the full 10-table schema applied + migrations 002 and 003. Drop with `dropdb creditpull_test` if not needed.
- **Playwright dependency** — currently pip-installed locally only. Must add `playwright>=1.59`, `beautifulsoup4`, `lxml`, `yarl` to `Statements/requirements.txt` before deployment, and run `playwright install chromium` on the deploy host.

## File inventory

```
Statements/creditpull/
├── HANDOFF.md                  ← this file
├── MICROSERVICE_PLAN.md        ← architect-facing extraction plan (not yet executed)
├── __init__.py
├── parser.py                   ← HTML → dict (verified against 32-account fixture)
├── persistence.py              ← dict → 10 tables; load_full_report → dict
├── scraper.py                  ← Playwright scraper, login + Q retry loops
├── credentials_store.py        ← in-memory creds, version-counter for retries
├── _logging.py                 ← credential-scrubbing logging filter
├── creditpull_api.py           ← Flask Blueprint, 9 endpoints, registered in app1.py
├── schema.sql                  ← fresh-install schema (10 tables + status CHECK includes awaiting_credentials)
├── fixtures/
│   ├── sample_credit_report.html      (1.7MB, 32 accounts — captured from staging IIQ)
│   └── sample_parsed_report.json      (parser output for UI/persistence testing)
├── migrations/
│   ├── 002_add_awaiting_credentials_status.sql
│   └── 003_add_question_attempt_count.sql
└── tools/
    ├── __init__.py
    ├── .gitignore                              (ignores _debug/ output dir)
    ├── diagnose_login_page.py                  (re-run if #txtUsername/#txtPassword/#imgBtnLogin selectors break)
    ├── diagnose_dashboard.py                   (re-run if div.css-txkp1t / Reports & Scores selector breaks)
    ├── diagnose_credit_report_iframes.py       (use if report ever moves into an iframe)
    └── test_parser.py                          (./venv/bin/python Statements/creditpull/tools/test_parser.py)

Statements/pub/
├── underwrite.html          ← sidebar item under Funded; 5-view #creditpull panel; cpPullFormError + cpSecurityQError elements
├── js/config.js             ← MODES.CREDITPULL added
├── js/main.js               ← import + initCreditPull + activateCreditPull, hide() in all mode branches
├── js/modules/creditpull.js ← 5-view UI, polling, security Q + creds retry handling
└── css/creditpull.css       ← display tables, payment grid colors, .cp-form-error red banner

Statements/app1.py           ← blueprint imported (line 59) + registered (line 82)
```

## Migrations to run on Supabase before deploy

If the `credit_reports` schema is already in place from an earlier session, run the deltas in order:

```sql
-- 002: allow awaiting_credentials status
\i Statements/creditpull/migrations/002_add_awaiting_credentials_status.sql

-- 003: track per-question retry attempts
\i Statements/creditpull/migrations/003_add_question_attempt_count.sql
```

For a fresh install, `schema.sql` already includes both and is idempotent.

## What's deferred (not started)

1. **Microservice extraction** — full plan in `MICROSERVICE_PLAN.md`. ~5–6 hours of work to extract `creditpull/` into a standalone Railway service consumed by the CRM, Loan Applications app, and Website. Decision: keep in-process for now.

2. **FCRA audit log** — every read/write of credit data should be logged with consumer, timestamp, IP, and permissible purpose. Required before exposing this to real consumer data in production.

3. **Consent record table** — timestamped consumer authorization (signed-off-by + when + what they agreed to). Also FCRA-required for production.

## How to resume (paste this into a new Claude session)

> Continuing from `HANDOFF.md` in `Statements/creditpull/`. The Credit Pull module is feature-complete in-process. The two open paths are:
> (a) Extract as a microservice — plan is in `MICROSERVICE_PLAN.md` (architect-reviewed plan, ~5–6h to execute)
> (b) Add FCRA audit + consent capture before going to production with real data
> Read `HANDOFF.md` for the current state and `MICROSERVICE_PLAN.md` if extraction is the goal. Don't re-explore — everything is documented.

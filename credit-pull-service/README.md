# Credit Pull Microservice

Standalone backend service that scrapes credit reports from IdentityIQ and stores them in Supabase.

Hosts both:
- **REST API** at `/api/creditpull/*` — for programmatic consumers
- **Standalone Web UI** at `/ui` — for end users (underwriters, applicants)

Designed to be consumed by multiple internal apps (CRM, Loan Applications, Website) sharing one Supabase database.

---

## Architecture at a glance

```
┌──────────────┐  ┌──────────────────┐  ┌──────────────┐
│     CRM      │  │ Loan Applications│  │   Website    │
│ X-API-Key:abc│  │ X-API-Key: def   │  │ X-API-Key:ghi│
└──────┬───────┘  └────────┬─────────┘  └──────┬───────┘
       │                   │                   │
       └───────────────────┼───────────────────┘
                           │  HTTPS + X-API-Key header
                           ▼
            ┌──────────────────────────────┐
            │ Credit Pull Service (Railway)│
            │  ─ /api/creditpull/*  (API)  │
            │  ─ /ui                (UI)   │
            │  ─ /healthz           (probe)│
            └──────────────┬───────────────┘
                           │
                           ▼
            ┌──────────────────────────────┐
            │ Supabase (shared)            │
            │ 10 tables, see schema.sql    │
            └──────────────────────────────┘
```

A pull made by any consumer is visible to all consumers (shared `subject_id`).

---

## Quick start (local)

```bash
# 1. Set up env
cp .env.example .env
# Edit .env with your Supabase URL + service-role key + at least one API key

# 2. Install
python -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/playwright install chromium

# 3. Run
./venv/bin/python app.py
# → service on http://localhost:5000
# → UI:  http://localhost:5000/ui?key=YOUR-API-KEY
# → API: curl -H "X-API-Key: YOUR-API-KEY" http://localhost:5000/api/creditpull/subjects
```

---

## Authentication

Every API request must include the header:

```
X-API-Key: <consumer-specific-key>
```

Keys are configured via env var, one per consumer:

```
CREDIT_PULL_API_KEYS=crm-key-abc:CRM,loanapp-key-def:LoanApplications,website-key-ghi:Website
```

The label after the colon is used in audit logs. Rotate keys by updating the env var and redeploying.

The standalone UI accepts the API key via URL query parameter, e.g.:

```
https://credit-pull.your-domain.com/ui?key=crm-key-abc
```

The UI stores the key in browser localStorage so subsequent visits don't need it. Each consumer should generate their own URL with their own key.

---

## REST API

All endpoints live under `/api/creditpull/`. See [`creditpull/docs/MODULE_OVERVIEW.md`](creditpull/docs/MODULE_OVERVIEW.md) for the full design walkthrough.

| Method | Path | Purpose |
|---|---|---|
| POST   | `/subjects` | Create an applicant |
| GET    | `/subjects` | List applicants |
| GET    | `/subjects/<id>` | Single applicant |
| POST   | `/run` | Start a pull (body: `subject_id`, `iiq_username`, `iiq_password`) |
| GET    | `/status/<report_id>` | Poll status, includes pending question/error |
| POST   | `/answer/<question_id>` | Submit security Q answer |
| POST   | `/credentials/<report_id>` | Resubmit credentials after rejection |
| GET    | `/report/<report_id>` | Fetch full parsed report |
| GET    | `/reports?subject_id=...` | List reports |

State machine:

```
pending → running → [awaiting_input ↔ running]
                  ↘ [awaiting_credentials ↔ running]
                  ↘ done | failed
```

---

## Database

Uses an existing Supabase project. Run [`creditpull/schema.sql`](creditpull/schema.sql) against your Supabase SQL editor for a fresh install. For existing setups, apply the deltas in [`creditpull/migrations/`](creditpull/migrations/) in order.

---

## Deployment on Railway

1. Push this repo to GitHub
2. Create a new Railway service from this repo
3. Set Root Directory to `/` (this repo's root)
4. Railway auto-detects the Dockerfile
5. Set required env vars in Railway:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE`
   - `CREDIT_PULL_API_KEYS` (your consumer keys)
   - `CORS_ALLOWED_ORIGINS` (your consumers' origins)
6. Optional: assign a custom domain (e.g. `credit-pull.your-domain.com`)
7. Health check at `/healthz` is configured in `railway.json`

---

## Repository layout

```
credit-pull-service/
├── app.py                  ← Flask boot
├── requirements.txt
├── Dockerfile
├── railway.json
├── .env.example
├── README.md               ← you are here
│
├── creditpull/             ← business logic (Python package)
│   ├── parser.py             HTML → dict
│   ├── persistence.py        dict → 10 tables (and back)
│   ├── scraper.py            Playwright driver
│   ├── credentials_store.py  in-memory creds (never persisted)
│   ├── _logging.py           credential-scrubbing log filter
│   ├── creditpull_api.py     Flask Blueprint, 9 endpoints
│   ├── auth.py               X-API-Key middleware
│   ├── schema.sql            fresh-install schema
│   ├── docs/                 deep design docs
│   ├── fixtures/             test fixtures
│   ├── migrations/           SQL deltas
│   ├── tests/                test scripts
│   └── tools/                diagnostic tools
│
└── static/                 ← standalone web UI
    ├── index.html
    ├── js/creditpull.js
    └── css/creditpull.css
```

---

## Documentation

- [`creditpull/docs/MODULE_OVERVIEW.md`](creditpull/docs/MODULE_OVERVIEW.md) — comprehensive design walkthrough (read this if you're new)
- [`creditpull/docs/HANDOFF.md`](creditpull/docs/HANDOFF.md) — current state snapshot
- [`creditpull/docs/MICROSERVICE_PLAN.md`](creditpull/docs/MICROSERVICE_PLAN.md) — original extraction plan (historical)

---

## Local testing

```bash
# Persistence layer end-to-end (uses local Postgres + the shim)
./venv/bin/python creditpull/tests/test_persistence_e2e.py

# Real Playwright run against IdentityIQ (browser visible)
IIQ_HEADLESS=false ./venv/bin/python creditpull/tests/test_scraper_e2e.py

# Generate the UI demo for non-technical stakeholders
./venv/bin/python creditpull/tests/preview_credit_pull_ui.py
open creditpull/tests/_preview/credit_pull_demo.html
```

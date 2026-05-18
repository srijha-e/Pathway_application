# Credit Pull — Microservice Extraction Plan

**For architect review.** Plan to extract the existing in-process Credit Pull module into a standalone backend service deployed on Railway, consumed by multiple internal apps.

---

## Context

We have a working **Credit Pull module** today, built as part of our underwriting CRM (Flask + Supabase). It scrapes credit reports from a third-party portal (IdentityIQ) using Playwright, parses the HTML, persists to a normalized 10-table Postgres schema, and serves the data back through 8 REST endpoints. It is currently a Python package inside the CRM monolith (`Statements/creditpull/`).

We want to extract it as a standalone microservice so it can be reused across multiple internal applications without coupling each one to the CRM codebase.

## Decisions already made

The following were confirmed before writing this plan:

- **Process model:** standalone backend service, separate Railway deployment from the CRM
- **Consumers:** three internal applications — the existing CRM, a Loan Applications app, and our marketing/applicant-facing Website
- **Database:** shared Supabase project (the same one the CRM already uses); the existing 10 credit-pull tables stay where they are
- **Service flavor:** API-only (each consumer builds its own UI; the service ships no frontend)
- **Auth:** per-consumer API keys passed in `X-API-Key` header

---

## Target architecture

```
┌─────────────────┐  ┌─────────────────────┐  ┌─────────────┐
│   CRM           │  │ Loan Applications   │  │  Website    │
│   X-API-Key:abc │  │ X-API-Key: def      │  │  X-API-Key: ghi
└────────┬────────┘  └─────────┬───────────┘  └──────┬──────┘
         │                     │                     │
         └─────────────────────┴─────────────────────┘
                               │
                               ▼  HTTPS + X-API-Key header
                ┌──────────────────────────────────┐
                │   Credit Pull Service (Railway)  │
                │                                  │
                │   Flask + creditpull/ package    │
                │   + CORS allow-list              │
                │   + per-consumer API key auth    │
                │   + background Playwright threads│
                └────────────┬─────────────────────┘
                             │
                             ▼
                ┌──────────────────────────────────┐
                │   Supabase (shared with CRM)     │
                │   credit_subjects, _reports, ... │
                └──────────────────────────────────┘
```

Each consumer builds its own UI and calls the same REST endpoints. Same database, separate API keys.

## Authentication

The service validates the `X-API-Key` header on every request against a list of valid keys configured via environment variable.

```
CREDIT_PULL_API_KEYS=crm-key-abc123:CRM,loanapp-key-def456:LoanApplications,website-key-ghi789:Website
```

Each key is paired with a label used for audit logging. Rotation = update the env var and redeploy. Revocation = remove the key from the env var.

This works fine for our 3 known consumers. If the consumer count grows past ~5, or we want self-service key generation, we would migrate keys to a database table — but the env-based scheme is sufficient and cheap to migrate later.

## CORS

The service rejects cross-origin calls from any origin not in an allow-list, also configured via env var:

```
CORS_ALLOWED_ORIGINS=https://crm.your-domain.com,https://loans.your-domain.com,https://www.your-domain.com
```

Local development origins (`localhost:5055`, etc.) added as needed.

## File layout

A new top-level directory in the existing repo (or a separate repo — see "open questions" below):

```
credit-pull-service/
├── app.py                     ← NEW — Flask boot, loads blueprint, wires CORS + auth
├── requirements.txt           ← NEW — extracted Python deps
├── Dockerfile                 ← NEW — installs Python 3.9 + Playwright Chromium
├── railway.json               ← NEW — Railway service config
├── .env.example               ← NEW — documents required env vars
├── README.md                  ← NEW — how to deploy, how to call, endpoint spec
└── creditpull/                ← MOVED from Statements/creditpull/
    ├── __init__.py
    ├── parser.py
    ├── persistence.py
    ├── scraper.py
    ├── credentials_store.py
    ├── _logging.py
    ├── creditpull_api.py      ← Flask Blueprint (no functional change)
    ├── auth.py                ← NEW — X-API-Key middleware
    ├── schema.sql
    ├── fixtures/
    ├── migrations/
    └── tools/
```

The only **new code** to write:

1. **`app.py`** (~30 lines) — boot Flask, register the blueprint, wire CORS and auth middleware
2. **`auth.py`** (~40 lines) — `before_request` hook that validates `X-API-Key`, sets `g.consumer_label` for logging
3. **`Dockerfile`** (~25 lines) — Python + Playwright + Chromium + Gunicorn
4. **`requirements.txt`** — pulled from the existing CRM's requirements (the relevant subset)
5. **`railway.json`** — points Railway at the Dockerfile

The existing `creditpull/` Python package moves wholesale, with no internal changes required.

## CRM migration

The existing CRM has a working Credit Pull UI today (sidebar item, 5-view panel, polling JS). Two options for how the CRM consumes the new service:

**Option A — keep the existing UI, just point it at the new URL.** Almost zero code change. The frontend `creditpull.js` gets a base-URL constant pointing at `https://credit-pull.your-domain.com` instead of relative `/api/creditpull`. The sidebar item, the panel HTML, the CSS — all stay. The existing `creditpull_api.py` blueprint is removed from the CRM (it now lives in the service).

**Option B — fully decouple.** Same as A, but also remove the credit-pull-specific JS/CSS/HTML from the CRM and rebuild it as a generic "embed external service" pattern (iframe to a service-hosted UI). More work upfront, cleaner separation later, but requires the service to also ship a UI which contradicts the API-only decision above.

**Recommendation: Option A.** Preserves all existing UI work, integration is a one-line constant change, and Option B can be revisited if a future need emerges.

For the new consumers (Loan Applications, Website), each builds its own UI calling the documented REST endpoints. We will write a formal API contract document for them when they're ready to integrate.

## Deployment plan on Railway

Two services under the existing Railway account:

1. **`crm`** — the current Flask app (unchanged except: remove the `Statements/creditpull/` folder + blueprint registration in `app1.py`)
2. **`credit-pull-service`** — the new service, deployed from the `credit-pull-service/` subfolder

Railway supports deploying multiple services from a single repo by configuring `Root Directory` per service. No need for a second repo unless we choose to split.

Both services point at the same Supabase by sharing `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE` env vars. The CRM additionally gets `CREDIT_PULL_SERVICE_URL` and `CREDIT_PULL_API_KEY` for outbound calls.

## REST API surface (already implemented)

The service exposes 8 endpoints, all under `/api/creditpull`:

| Method | Path | Purpose |
|---|---|---|
| POST | `/subjects` | Create a new subject (loan applicant) |
| GET | `/subjects` | List all subjects |
| GET | `/subjects/<id>` | Single subject |
| POST | `/run` | Start a credit pull (body: subject_id, IIQ creds) |
| GET | `/status/<report_id>` | Poll job status, including pending security questions |
| POST | `/answer/<question_id>` | Submit security question answer |
| POST | `/credentials/<report_id>` | Submit retry credentials when login fails |
| GET | `/report/<report_id>` | Fetch full parsed report |
| GET | `/reports` | List reports, optionally filtered by subject |

All endpoints accept and return JSON. Full status state machine: `pending → running → [awaiting_input ↔ running] → [awaiting_credentials ↔ running] → done | failed`.

## Open questions for architect review

1. **Same repo or new repo?** — Recommendation: same repo, sub-folder (Railway can deploy from a sub-folder). Easy to `git subtree split` to a separate repo later if needed.

2. **CRM migration approach** — Option A (keep UI, repoint URL) or Option B (strip and re-integrate)? Recommendation: A.

3. **Domain name for the service** — Railway provides a default `*.up.railway.app` URL. Will we set up `https://credit-pull.your-domain.com` (custom domain) or use the default?

4. **Health check endpoint** — Railway requires one for health checks. Recommendation: add `GET /healthz` returning `{ok: true}` as part of `app.py`.

5. **Logging / observability** — basic Python logging to stdout (Railway captures it) is the default. Do we want Sentry, Logfire, or any structured-logging integration?

6. **Rate limiting** — currently none. Do we want per-API-key rate limits to protect against runaway consumers?

7. **Audit log** — currently deferred. Per FCRA, every credit data read/write should be logged with consumer, timestamp, and purpose. This is a known gap to address before going to production with real consumer data.

8. **Schema ownership** — since multiple consumers share the same Supabase schema, the service is the de facto owner. Do we add a guard rail (e.g. a service-account role with limited permissions) to prevent the CRM from writing directly to credit_pull tables?

## Implementation effort estimate

Assuming all 8 open questions are resolved:

- Create `credit-pull-service/` skeleton (`app.py`, `auth.py`, `Dockerfile`, `requirements.txt`, `railway.json`): ~2 hours
- Move `creditpull/` package + verify no internal changes broke anything: ~30 min
- Wire up CORS + per-consumer API key middleware + tests: ~1 hour
- Configure Railway service (env vars, custom domain if applicable, deploy): ~1 hour
- Update CRM frontend to point at new service URL + remove old blueprint: ~30 min
- Smoke test end-to-end (create subject from CRM, pull report, verify rendering): ~30 min

**Total: ~5–6 hours of focused work**, assuming no surprises in deployment configuration.

## What we're NOT doing in this initial extraction

- No UI shipped with the service (consumer responsibility)
- No widget / SDK for embedding (can be added later if needed)
- No multi-tenant database (shared Supabase is sufficient for now)
- No FCRA audit log (deferred — must be addressed before production with real users)
- No formal API versioning (`/v1/` prefix) — fine for now since all 3 consumers are internal; revisit if external consumers are added

These are deliberate cuts to ship the smallest viable extraction first. Each can be added incrementally without breaking the foundation.

# Credit Pull Module — Complete Overview

> A top-to-bottom walkthrough of what this module does, how it was built, the design decisions behind it, and how every piece fits together. Read this first if you're new to the module (including future-you).

---

## 1. What this module does

This is a feature inside the Underwriting CRM that lets an underwriter pull a credit report for a loan applicant without leaving the CRM. The underwriter onboards an applicant (the "subject"), enters that applicant's IdentityIQ credentials, and clicks Pull. A background process logs into IdentityIQ on the applicant's behalf, navigates to their credit report page, extracts the data, stores it in our database, and renders it back inside the CRM with the same 3-bureau layout that IdentityIQ shows natively.

The whole thing is operationally invisible to the underwriter beyond entering credentials and answering any security questions IdentityIQ throws up. The complexity — browser automation, HTML parsing, retry logic, status polling — all lives behind a small REST API and a sidebar menu item.

## 2. The problem we solved

IdentityIQ (and its sibling MyScoreIQ) is a consumer credit-monitoring portal. It does not offer a Partner API in any form we could find. We considered three paths early on: integrate with the official IDIQ partner channel (no API documentation available, no contact path), have users manually copy JSON from DevTools (a documented but absurd workaround the vendor actually suggests), or automate the browser flow ourselves. We chose the third because it's the only one that produces a usable underwriting workflow without subjecting underwriters to a manual export ritual every time.

That choice locked us into Playwright as the scraping engine and shaped most of the downstream design. Everything else — the state machine, the retry logic, the in-memory credentials store — exists because we are driving a real browser session as the applicant, and that comes with its own set of failure modes.

## 3. High-level architecture

The module is structured as a self-contained Python package (`Statements/creditpull/`) inside the existing Flask monolith. It registers a Flask Blueprint exposing 9 REST endpoints. Each credit-pull request spawns a background thread that drives Playwright through the IdentityIQ flow. State coordinates between the background thread and the frontend through a normalized 10-table Postgres schema in our existing Supabase project. The frontend is a JavaScript module that lives in the CRM's main HTML page and polls the backend for status updates.

```
Underwriter
    │
    ▼ (clicks "Credit Pull" in CRM sidebar)
CRM Frontend (creditpull.js)
    │  ↑
    │  └─── polls every 2s
    ▼
Flask Blueprint (creditpull_api.py)
    │
    ├──► Spawns background thread per pull ─┐
    │                                       │
    │                                       ▼
    │                       Playwright (scraper.py)
    │                       ─ logs into IdentityIQ
    │                       ─ handles security Qs via DB
    │                       ─ navigates menu, scrolls, captures HTML
    │                       ─ parser.py → structured dict
    │                       ─ persistence.py → 10 tables
    │
    ▼
Supabase Postgres
    ├── credit_subjects
    ├── credit_reports
    ├── credit_report_questions
    ├── credit_report_personal_info
    ├── credit_report_scores
    ├── credit_report_risk_factors
    ├── credit_report_summary
    ├── credit_report_accounts
    ├── credit_report_account_details
    └── credit_report_payment_history
```

Nothing about this architecture is novel; the value is in the specifics — which selectors to use, when to wait for AngularJS to settle, how to detect a wrong security question answer, where to put credentials so they never touch disk. Those specifics came out of the discovery process, which is worth understanding before reading the code.

## 4. The discovery journey

We built this module using a method we called **Path B** — drive a headed Playwright session manually, watch it execute step by step, capture HTML snippets and screenshots at each pause point, and iteratively figure out what the page actually looks like at runtime. The alternative (Path A) would have been to capture static HTML fixtures by browsing manually with DevTools, then write code against those fixtures. We chose Path B because we kept hitting cases where the page rendered differently in automation than in a manual browser — Chakra UI's auto-generated React IDs, AngularJS rendering timing, infinite-scroll patterns — and those only become visible when you actually drive the page programmatically.

The discovery happened across roughly nine iterative scripts (now consolidated into three diagnostic tools in `tools/`). The key findings, in order:

The **login form** turned out to be a React/Chakra UI page where the labels' `for=` attributes point at React-generated IDs (`field-:r0:`) that have nothing to do with the actual input IDs (`#txtUsername`, `#txtPassword`). Playwright's `get_by_label("Username")` fails because the accessibility tree is wired incorrectly. The fix is to target the real input IDs directly. The submit button (`#imgBtnLogin`) is disabled until both fields contain text, so we have to wait for it to enable before clicking.

The **security question screen** is a separate ASP.NET-style page at `/security-question`. The question itself is text in a `<label>` element, and the answer field is `#FBfbforcechangesecurityanswer_txtSecurityAnswer` (server-rendered ID, stable). The screen can ask one question, or multiple questions in sequence, or none at all (depending on IdentityIQ's risk model). We never observed a multiple-choice variant in our test account but built defensive handling for it anyway.

The **dashboard** uses a Chakra sidebar where "Reports & Scores" is a styled `<div>` (not a link or button) that opens a submenu on hover. Hover alone is unreliable in automation; we fall back to a click if hover doesn't reveal the submenu within a second. The submenu items appear only after this interaction — they are not in the initial DOM.

The **credit report page** itself is an AngularJS page that renders the full report into the main document — not into an iframe, which is what we initially feared based on early reconnaissance. It takes about 5 seconds for the AngularJS app to fully populate after the page first loads. Account History uses an `infinite-scroll` directive: only the first 6 accounts render initially, with more loaded as the user scrolls. We scroll to the bottom in a loop until the account count stabilizes.

Once on the rendered page, every section (Personal Info, Credit Score, Summary, Account History, Inquiries, Creditor Contacts) uses the **same HTML pattern**: a wrapping `<div class="rpt_content_wrapper">` with an `<h3>`-equivalent header div inside, and a 4-column table with `<td class="label">` for the field name and three `<td class="info">` cells for TransUnion, Experian, and Equifax values. This uniformity is what made the parser tractable.

Without going through this discovery, we would have spent days writing code against assumptions that turned out to be wrong. The three remaining diagnostic scripts in `Statements/creditpull/tools/` are the cleaned-up survivors — re-run them if IdentityIQ ever changes their HTML and our scraper breaks.

## 5. Database schema

We store credit report data in **10 normalized Postgres tables**. The design choice between "one JSONB column per section" and "fully relational" was deliberate. We chose relational for two reasons: first, the data shape is stable enough that a schema is realistic to maintain (we're not dealing with wildly varying vendor payloads); second, future analytics across reports — average score across approved applicants, distribution of account types, queries on payment history patterns — become natural SQL rather than awkward JSONB digging.

The tables form a strict parent-child hierarchy with cascading deletes from `credit_subjects` all the way down. Deleting a subject row cleans up everything — every report they have, every account in every report, every payment-history cell — automatically. The hierarchy looks like this in text:

A **credit_subjects** row represents a loan applicant. The CRM creates one of these when an underwriter onboards a new applicant (first name, last name, DOB, SSN last 4, email, phone). One subject can have many pulls over time, so each pull becomes a **credit_reports** row linked back to its subject. The credit_reports row carries the IdentityIQ reference number, the date the report data was generated, our internal status (more on this below), and any error message if the pull failed.

Each report has child rows in the section tables. **credit_report_personal_info** holds rows of `(field_name, bureau, value)` — for example, three rows per report for "Name" (one for TransUnion, Experian, Equifax). **credit_report_scores** holds one row per bureau with the score, lender rank, and score scale. **credit_report_risk_factors** holds variable-length lists of factor strings per bureau, with a `position` column to preserve display order. **credit_report_summary** holds aggregate stats per bureau (total accounts, open accounts, balances, etc.) as typed columns rather than rows.

The largest child table family is for **accounts**. **credit_report_accounts** holds one row per account a report mentions, with `creditor_name` and `position`. Each account in turn has rows in **credit_report_account_details** — one per bureau, with all 17 IIQ account fields as typed columns (account number, type, status, balance, credit limit, dates, etc.). And finally, **credit_report_payment_history** holds one row per `(account, bureau, year, month)` cell of the 24-month grid, with the status code (`OK`, `30`, `60`, `90`, `CO`).

The **credit_report_questions** table is operational — it tracks the security questions the scraper saw during a pull, and the answers the user submitted. It has an `attempt_count` column to support the wrong-answer retry behavior.

The full schema is in `schema.sql`. Migrations are in `migrations/` — `002` adds the `awaiting_credentials` status and `003` adds the `attempt_count` column. We verified the schema works on local Postgres 18.3 with all CHECK constraints and CASCADEs firing correctly before deploying to Supabase.

## 6. Component walkthrough

### parser.py — HTML to dict

The parser takes the raw HTML of the credit report page and returns a Python dict structured to match what the frontend renders. It uses BeautifulSoup with the lxml backend. The core technique is a single generic helper, `_parse_three_bureau_table`, that takes any `<table class="rpt_content_table">` element and extracts rows in `{field, transunion, experian, equifax}` shape. This works for Personal Info, Credit Score, Summary, and every individual account's field table — because they all share the same HTML pattern (this was the big insight from discovery).

The parser includes a `_clean` function that handles two annoying quirks of AngularJS rendering. First, the page renders both the real value and a fallback placeholder `-` for empty fields, so we strip trailing `-` characters. Second, whitespace is inconsistent because of the nested `<ng-if>` and `<ng-repeat>` elements, so we collapse runs of whitespace.

For sections that don't fit the standard table pattern — risk factors (which are variable-length bullet lists per bureau, formatted with `<b>` tags inside per-bureau `<td>` cells) and payment history grids (which are pivoted: rows are bureaus, columns are months) — the parser has dedicated functions: `_parse_risk_factors` and `_parse_payment_history`. The payment history function maps each `(month, year)` cell to a `YYYY-MM` key for downstream storage.

Verified against a real 32-account fixture (`fixtures/sample_credit_report.html` and the parsed output in `fixtures/sample_parsed_report.json`). All 32 accounts parse, all 17 fields per account come through, and payment history grids of varying length (1 month vs 24 months depending on account) all map correctly.

### persistence.py — dict to DB and back

This module has two public functions: `save_parsed_report(sb, report_id, parsed)` and `load_full_report(sb, report_id)`.

The save function takes the parser output and writes it to all 10 tables in dependency order — the `credit_reports` row is updated first with header info and status set to `done`, then the per-bureau and per-section child rows are bulk-inserted. The biggest piece of work in this module is **type coercion**: the parser produces strings like `'$127,765.00'` and `'08/29/2023'`, but the schema expects `NUMERIC(14,2)` and `DATE`. Helper functions `_to_decimal`, `_to_int`, and `_to_date` strip dollar signs and commas, parse `MM/DD/YYYY`, and convert IIQ's `-` placeholders to SQL `NULL`. These conversions happen at insert time; the parser output stays string-based, which keeps the parser logic clean and lets the persistence layer own DB type concerns.

The load function reverses the process. It reads from all 10 tables and re-pivots the data into the same dict shape the parser produces, so the frontend's `/report/<id>` response is shape-identical whether the data was just freshly parsed or loaded from the database after a year.

There's a mapping concern worth mentioning: the database uses snake_case column names (`total_accounts`, `account_number`, `date_opened`) but the parser/UI uses human-readable field names (`Total Accounts`, `Account #`, `Date Opened`). Two dictionaries — `SUMMARY_FIELD_MAP` and `ACCOUNT_FIELD_MAP` — handle the translation between the two. If IdentityIQ ever adds a new summary field, this is the place to add a mapping entry; the parser will just include it in its output and the persistence layer will write it to the right column.

### scraper.py — the Playwright driver

This is the heart of the module. It's a single function, `run_credit_pull(sb, report_id)`, that runs in a background thread per pull. It opens a headless Chromium browser, navigates to IdentityIQ, drives the entire flow, and writes results back to the database. The function takes only the report ID and a database client — credentials are looked up from the in-memory `credentials_store`, not passed as arguments, because that's how the retry flow works (more on that in section 8).

The scraper is organized as a sequence of phases. It first navigates to the login page, dismisses any cookie banner, and then enters the login retry loop. The login loop is `while True` with no max-attempt cap. After each click of the Login button, the scraper waits 15 seconds for the URL to change. If the URL stays on the login page, that's our signal of bad credentials — the scraper flips the report status to `awaiting_credentials` and blocks waiting for new credentials from the in-memory store, then retries.

Once login succeeds, the scraper enters the security question loop. This is where the wrong-answer detection lives. The scraper extracts the question text, type (text input or radio buttons), and any options. It writes a row to `credit_report_questions` and flips status to `awaiting_input`. It then blocks polling that row's `answer` column until the frontend writes a value there (via the `/answer/<question_id>` endpoint). Once it has an answer, it types it into the page and clicks Submit. On the next iteration of the loop, if the same question text reappears, that's our signal of a wrong answer — the scraper increments the question's `attempt_count`, clears the `answer` field to re-prompt the user, and updates the report's `error_message` with a banner like "Wrong answer. Please try again (attempt 2 of 3)." After three wrong attempts, the scraper gives up and fails the report.

If the user takes longer than 20 minutes to answer a security question or re-enter credentials, the scraper times out and fails the report. This is configurable in `scraper.py` constants — the original choice was 5 minutes; we bumped it to 20 because real underwriters might step away briefly.

After security questions are done, the scraper checks for an ad popup that IdentityIQ sometimes shows (we have defensive selectors for "No thanks", "No, thanks", "Not interested", etc.) and dismisses it if present. Then it hovers "Reports & Scores" in the sidebar — falling back to a click if hover doesn't reveal the submenu — and clicks "Credit Reports". The credit report page takes about five seconds to fully render after AngularJS bootstraps; the scraper waits explicitly.

Then comes the scroll loop. Account History uses Angular's `infinite-scroll` directive, so the scraper scrolls to the bottom of the page repeatedly, watching the count of `<table class="crPrint">` elements (one per account). It considers the count "stable" after two consecutive scrolls produce the same number, then captures the rendered HTML.

The HTML goes to the parser, the parsed dict goes to `save_parsed_report`, and `save_parsed_report` flips status to `done`. In the `finally` block, the scraper closes the browser and clears credentials from the in-memory store — guaranteeing creds never outlive a single pull, even if an exception occurs mid-flow.

### credentials_store.py — the in-memory creds bus

This is a small but important module. It exists for one reason: we never want credentials to touch the database, but the scraper (running in a background thread) and the HTTP endpoint (running on a request thread) need a way to share credentials and pass new ones when login retry is needed.

The module is a thread-safe dictionary guarded by a `threading.Lock`. The schema is `{report_id: {username, password, version}}`. The version counter is critical: when the scraper detects bad creds, it records the current version and starts polling for a version greater than that. When the user submits new credentials via `POST /credentials/<report_id>`, the version increments. The scraper sees the new version and retries.

This design has one subtle property: if the user submits the same wrong credentials twice (perhaps because they didn't realize they were wrong and they hit Submit again), the version still increments, so the scraper sees "fresh creds" and retries — even though they're identical. This is the right behavior. It's IdentityIQ's job to enforce account lockout, not ours.

Credentials are cleared from the store in the scraper's `finally` block, so a successful pull, a failed pull, or a Python exception all leave the store empty for that report ID.

### _logging.py — credential-scrubbing filter

A defense-in-depth measure. The filter installs at the root of Python's logging system and rewrites every log record before it reaches a handler, scrubbing values that match patterns associated with sensitive keys (`iiq_password`, `iiq_username`, `password`, `answer`). It handles JSON-style strings (`"key": "value"`), Python dict reprs (`'key': 'value'`), and kwarg-style assignments (`key='value'`).

It also scrubs exception tracebacks. This is the most important case in practice, because if an exception happens with credentials in scope, Python's traceback formatter will include the locals' dict in the formatted output. Without this filter, a crash during scraping could leak the user's password into log aggregation systems like Sentry or Railway's log viewer. With the filter, the password is replaced with `***REDACTED***` before the trace ever hits a handler.

The filter is installed at import time by `creditpull_api.py`, so the moment the blueprint is loaded, all logging is scrubbed. It's a few-dozen lines of regex and a `logging.Filter` subclass.

### creditpull_api.py — the Flask Blueprint

This is the HTTP layer. It registers a Flask Blueprint at `/api/creditpull` with nine endpoints, summarized in `MICROSERVICE_PLAN.md` and `HANDOFF.md`. The implementation is intentionally thin — each endpoint does the minimum amount of work to validate input, talk to the database, and return JSON. The heavy lifting (scraping, parsing, persistence) is handled by the modules described above.

The endpoint worth understanding is `POST /run`. It takes a subject ID and IdentityIQ credentials in the body. It validates the subject exists, creates a new `credit_reports` row in `pending` state, writes the credentials to the in-memory store, and spawns a Python thread running `run_credit_pull` with that report ID. The endpoint returns immediately with a 202 Accepted and the report ID — the actual scrape happens entirely in the background. This is a deliberate choice. A credit pull takes 60–90 seconds end-to-end (longer if there are security questions); blocking the HTTP request that long would time out against Railway's reverse proxy and feel terrible to the underwriter watching a spinner.

The `GET /status/<report_id>` endpoint is what the frontend polls every 2 seconds. It returns the current status, any pending question payload (if `awaiting_input`), or any error message. The frontend uses this to drive its state machine — showing a "pulling..." indicator, prompting for security questions, re-prompting for credentials, or finally fetching and rendering the completed report.

The other endpoints (`POST /answer/<question_id>`, `POST /credentials/<report_id>`) write user input back to the database, which the scraper thread is polling. This is how the asynchronous request/response pattern works — the HTTP layer and the scraper thread coordinate through database rows, not through in-process messaging.

### Frontend — modules/creditpull.js and supporting files

The frontend is a single JavaScript module that wires up a 5-view panel in the CRM's main HTML. The five views are mutually exclusive — only one is visible at a time, controlled by a `showView(viewId)` function that hides the others.

View 1 is the **subject list** — fetched from `/subjects`, rendered as rows with a Pull Report button each. View 2 is the **new subject form** — name, DOB, SSN last 4, email, phone — submitted to `POST /subjects`. View 3 is the **pull form** — IdentityIQ username and password — submitted to `POST /run` to start a pull. View 4 is the **status view**, which also hosts the security question form when status is `awaiting_input`. View 5 is the **report view**, which renders the full parsed report after status flips to `done`.

There's also a subtle handler for `awaiting_credentials` status: when the scraper signals bad credentials, the frontend re-shows the pull form (view 3) with a red error banner explaining the failure, and pre-clears the password field. Submitting the form again POSTs to `/credentials/<report_id>` instead of `/run` — the distinction is tracked by an `isRetryingCreds` flag in module state.

The report rendering itself is straightforward template-string construction. For each section, the frontend builds an HTML table with a header row (TransUnion / Experian / Equifax colored columns) and one row per field. For Account History, each account is its own card with its own table plus the 24-month payment history grid below. Status cells in the payment grid are color-coded: green for "OK", yellow for late payments (30/60/90/120 days), red for charge-offs.

The frontend depends on three other files: `creditpull.css` (styling), `utils.js` (shared `$`, `show`, `hide` helpers used across the CRM), and the standard CRM `main.js` integration which adds the mode-switching logic for the new sidebar item.

## 7. End-to-end data flow

Let's walk through what happens when an underwriter pulls a report, end to end, including the failure branches.

The underwriter opens the CRM, clicks Credit Pull in the sidebar. The frontend's `activateCreditPull()` fires, which hides all other panels, shows the credit pull panel, and fetches the subject list. The list renders.

The underwriter clicks "+ New Subject", types the applicant's details, clicks Save. The frontend POSTs to `/api/creditpull/subjects` and the backend inserts a `credit_subjects` row. The list re-renders with the new subject visible.

The underwriter clicks "Pull Report" on the new subject. The frontend switches to the pull form (view 3) with the subject name displayed. The underwriter types in the applicant's IdentityIQ username and password and clicks Start Pull.

The frontend POSTs to `/api/creditpull/run`. The backend validates the subject, creates a new `credit_reports` row in `pending` state, writes the credentials to the in-memory `credentials_store` (versioned, never written to disk), and spawns a `threading.Thread` running `run_credit_pull(supabase_client, report_id)`. The HTTP response returns immediately with `{ "report_id": "...", "status": "pending" }`.

The frontend stashes the report ID, switches to the status view (view 4), and starts a 2-second polling loop hitting `/api/creditpull/status/<report_id>`.

In the background thread, the scraper opens a Chromium browser. It navigates to `https://gcpstage.identityiq.com/`. It waits for `#txtUsername` to appear. It dismisses the cookie banner if visible. It enters the login retry loop. It reads the username and password from the credentials store (version 1), types them in, waits for the submit button to enable, clicks. It waits up to 15 seconds for the URL to change off the login page.

Branch A: the URL changes. Login succeeded. The scraper continues to the security question loop.

Branch B: the URL doesn't change. Bad credentials. The scraper sets the report status to `awaiting_credentials` with an error message "Invalid IIQ credentials. Please re-enter and try again." It calls `credentials_store.wait_for_new_credentials(report_id, since_version=1, timeout_s=1200)` and blocks for up to 20 minutes. Meanwhile, the frontend's poll sees `awaiting_credentials`, re-shows the pull form with a red error banner, and clears the password field. The underwriter retypes credentials and clicks Start Pull. The frontend recognizes the retry state (`isRetryingCreds = true`) and POSTs to `/api/creditpull/credentials/<report_id>` instead of `/run`. The backend writes the new credentials to the store, bumping the version to 2. The scraper's poll loop sees version 2 (greater than 1), retrieves the new credentials, and the login loop iterates: type, click, wait for URL change.

This can repeat indefinitely — there's no max-attempts on our side. The scraper exits the loop only when the URL finally changes (login success) or when the 20-minute wait times out (giving up).

Once logged in, the scraper enters the security question loop. It checks whether the current URL contains `/security-question`. If not, it skips this loop entirely (no security questions this session). If yes, it extracts the question, inserts a `credit_report_questions` row, flips status to `awaiting_input`, and blocks polling that row's `answer` column. The frontend sees `awaiting_input`, displays the question (text input or radio buttons depending on type), and waits for the underwriter to answer. When the underwriter submits, the frontend POSTs to `/api/creditpull/answer/<question_id>`, which updates the row. The scraper's poll sees the answer, types it into the page, clicks Submit, and loops back to the top of the security question loop.

If the same question reappears (because the answer was wrong), the scraper detects this — its loop tracks `current_q_text` and compares — and increments `attempts_on_curr_q`. It updates the existing question row (clearing the previous answer, bumping `attempt_count`) and sets the report status back to `awaiting_input` with an error message like "Wrong answer. Please try again (attempt 2 of 3)." The frontend re-renders the question form with the error banner. After three wrong attempts, the scraper gives up and fails the report.

Assuming the underwriter answers correctly, the scraper exits the security question loop. It checks for and dismisses any ad popup. It locates the "Reports & Scores" sidebar item, hovers it (falling back to a click), waits for the "Credit Reports" submenu item to appear, and clicks it.

The page navigates to `/CreditReport.aspx`. The scraper waits 5 seconds for AngularJS to render the report. It then enters the scroll loop, calling `window.scrollTo(0, document.body.scrollHeight)` and waiting 2.5 seconds between iterations, counting `<table class="crPrint">` elements (one per account). When the count is stable for two consecutive iterations, the loop exits.

The scraper calls `page.content()` to grab the full rendered HTML, passes it to `parse_credit_report()`, gets back a structured dict, and passes that to `save_parsed_report()`. The persistence function updates the `credit_reports` row with the header info and sets status to `done`, then bulk-inserts rows into all the child tables.

In the `finally` block, the scraper closes the browser and calls `credentials_store.clear_credentials(report_id)`, wiping the in-memory password.

Back in the frontend, the next poll sees `status: "done"`. The polling loop stops. The frontend hits `/api/creditpull/report/<report_id>`, gets the full parsed report (loaded back from the database via `load_full_report`), and calls `renderReport()` to populate the four section cards. The underwriter sees the report. Done.

## 8. Security and credentials handling

This module handles some of the most sensitive data in our system — credit reports, SSN fragments, IdentityIQ passwords. The design choices around credentials handling are deliberate.

Credentials are submitted from the frontend in the JSON body of `POST /run` or `POST /credentials/<report_id>`. They go directly into the in-memory `credentials_store`, never touching the filesystem and never being written to Supabase. The scraper thread reads them, uses them to log in, and the `finally` block clears them. If the Python process restarts (deployment, crash), in-flight credentials are lost — which is the correct behavior, because the underwriter would need to re-initiate the pull anyway.

The credential-scrubbing logging filter (`_logging.py`) catches anywhere a credential might leak into logs — exception tracebacks, debug output from Playwright, request body logging if anyone ever adds it. It's defense-in-depth; the primary defense is just not logging credentials in the first place, which our code does.

The frontend clears password fields immediately after submission and on every retry — the password never sits in the DOM beyond the moment of the form submit.

What we do *not* do: encrypt credentials in transit beyond what Railway's HTTPS termination provides. We rely on the entire stack being HTTPS-only end-to-end. We also do not currently rate-limit credential submissions, which means a misconfigured frontend could spam the endpoint; this is mitigated by the version counter (re-submitting identical credentials still counts as a new version, but doesn't bypass IdentityIQ's own rate limiting).

## 9. Failure modes and retry behavior

We made explicit decisions about how the system should behave when things go wrong. Summarized:

**Bad credentials at login.** No max-attempts cap on our side. The user can retry forever; we rely on IdentityIQ to lock the account when appropriate. The wait timeout for new credentials is 20 minutes.

**Wrong security question answer.** Maximum 3 attempts per question (counting the first try as attempt 1, with 2 retries allowed). After 3 wrong attempts, the report fails with a clear error message. We chose this cap because IdentityIQ doesn't make wrong-answer behavior very predictable — sometimes it shows the same question again, sometimes it kicks back to login, sometimes it locks the account. Three attempts seemed like a reasonable balance between user-friendliness and not exhausting IdentityIQ's patience.

**User abandons (no answer in N minutes).** 20-minute timeout on both security-question waits and credential-retry waits. After that, the report fails with a timeout message.

**Page selector errors / IIQ changes their HTML.** Hard fail with a generic error message. This is the failure mode that requires re-running the diagnostic tools in `tools/` to find new selectors and updating `scraper.py`. We chose not to add automatic retry here because the underlying problem is a code-side bug, not a transient condition.

**Browser crashes.** Caught in the scraper's outer `try/except/finally`. The `except` block records the error message and sets status to `failed`. The `finally` block closes the browser and clears credentials. The thread exits cleanly.

**Database errors during persistence.** Currently propagate up to the scraper's outer exception handler, which records the error and fails the report. We considered partial-rollback logic but decided against it — if persistence fails, the most useful state is "failed, with the original error message" rather than "partially saved, in an unknown state."

## 10. Dependencies

The Python dependencies are: `playwright` (for browser automation), `beautifulsoup4` and `lxml` (for HTML parsing), `supabase` (Python client), `flask` (already part of the CRM). Also indirectly: `psycopg2-binary` (only for local testing tools, not production). The `yarl` package is a transitive dependency of `supabase` that we had to install explicitly during local setup due to a venv-level packaging issue.

Playwright additionally requires the Chromium browser binary, installed via `playwright install chromium`. This runs at deploy time on Railway via the Dockerfile (when we have one) or via a build step.

The JavaScript depends on the CRM's existing `utils.js` for `$`, `show`, and `hide` helpers. The CSS is standalone.

External services: our Supabase project, IdentityIQ (or whatever vendor portal we're scraping — currently `gcpstage.identityiq.com` for staging).

## 11. Configuration

Configuration is via environment variables. The relevant ones:

`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE` — connect to the database. These are shared with the rest of the CRM since we use the same Supabase project.

`IIQ_URL` — the base URL of the IdentityIQ portal. Defaults to `https://gcpstage.identityiq.com/` (staging). Override to point at production when ready.

`IIQ_HEADLESS` (optional, future) — would let us run the browser headed for local debugging. Not currently wired up; the scraper hardcodes `headless=True`. Easy to add.

We deliberately do *not* expose credentials, customer keys, or any business-sensitive data via environment variables. Per-user credentials are entered at runtime; secrets like Supabase service-role keys are managed in Railway's env-var dashboard.

## 12. Deployment notes

The module deploys as part of the existing CRM Flask app on Railway. There's no separate deployment process today — when the CRM is pushed, the credit-pull module ships with it. The Dockerfile for the CRM needs `playwright install chromium` to run during the build, and the Python dependencies need to be added to `requirements.txt` (currently a TODO — they're pip-installed locally but not committed). Without those, the credit-pull endpoints will fail at first invocation when Playwright tries to launch a browser that doesn't exist.

For Supabase, run `schema.sql` once for a fresh setup, or run the migrations in `migrations/` in order if the base schema is already in place. Migrations are idempotent (`IF NOT EXISTS`, `DROP CONSTRAINT IF EXISTS`) so re-running them is safe.

There's an extraction plan in `MICROSERVICE_PLAN.md` for moving this module out of the CRM into its own Railway service. It's roughly 5–6 hours of work and unlocks reuse across other applications (Loan Applications, Website). Not started yet.

## 13. Debugging guide

When something breaks, the debugging path depends on what's failing.

If a pull is **stuck in `pending` or `running` and never progresses**, the scraper thread is likely blocked or crashed silently. Check Railway logs for the report ID — every phase prints a log line. Most common cause is a selector that no longer matches because IIQ changed their HTML.

If the **scraper fails at the login step** even with correct credentials, run `Statements/creditpull/tools/diagnose_login_page.py` against the same URL. It dumps the current form structure. Compare against the selectors in `scraper.py` (`#txtUsername`, etc.). Update if they've changed.

If the **scraper fails at the menu step** (dashboard reached but Reports & Scores not findable), run `Statements/creditpull/tools/diagnose_dashboard.py`. Same idea — dumps current sidebar structure.

If the **scraper reaches the credit report page but parses zero data**, run `Statements/creditpull/tools/diagnose_credit_report_iframes.py` to confirm the report is still in the main frame and the same section IDs exist. If everything looks right but the parser still fails, capture the live HTML via the diagnostic and run `tools/test_parser.py` against it.

If **persistence fails** (parser succeeded but the database errored), check the Railway logs for the SQL error. The most common cause is a NUMERIC overflow (a value larger than 999,999,999,999.99) or an unexpected string in a typed column — the type-coercion helpers in `persistence.py` should catch these but might miss new edge cases.

If the **frontend shows stale data or doesn't update**, check the browser DevTools Network tab to see what `/status` is returning. The frontend trusts the backend's status field absolutely — if the scraper is wedged in `running` forever, the frontend will poll forever too.

For local testing without Playwright or Supabase, the `tools/test_persistence_e2e.py` script verifies the data layer against local Postgres. For UI verification without any backend at all, generate `tools/_preview/report.html` via `tools/preview_report_ui.py` and open it in a browser — it renders the fixture using production CSS and rendering code.

## 14. Known limitations and deferred items

There are three categories of "not done" items.

**Deliberate v1 scope cuts.** No FCRA audit log (every read/write of credit data should be logged with who/when/why/IP for legal compliance; not built yet). No explicit consent capture (timestamped consumer authorization required for FCRA; not built yet). These are blocking issues before going to production with real consumer data — not just internal testing.

**Acknowledged technical debt.** The local-venv supabase install is broken (a packaging issue with `storage3`); we work around it by deploying to Railway for live testing. The Python dependencies (`playwright`, `beautifulsoup4`, `lxml`, `yarl`) aren't yet in `requirements.txt` and need to be added before deploying. Playwright Chromium isn't yet wired into the Dockerfile.

**Future work explicitly deferred.** Microservice extraction (see `MICROSERVICE_PLAN.md` for the full plan) — currently lives in the CRM monolith. Embeddable widget for cross-app use — could ship after the microservice extraction. Multi-tenant database — currently shares the CRM's Supabase project. Rate limiting on API endpoints — currently none.

## 15. Where to find what

```
Statements/creditpull/
├── HANDOFF.md                  ← snapshot of current state, what's deferred
├── MODULE_OVERVIEW.md          ← this document
├── MICROSERVICE_PLAN.md        ← architect-facing extraction plan
├── schema.sql                  ← fresh-install schema for all 10 tables
├── parser.py                   ← HTML → structured dict
├── persistence.py              ← dict → 10 tables (and back)
├── scraper.py                  ← Playwright driver, the heart of it
├── credentials_store.py        ← in-memory creds, never persisted
├── _logging.py                 ← credential-scrubbing logging filter
├── creditpull_api.py           ← Flask Blueprint, 9 endpoints
├── __init__.py
├── fixtures/                   ← test fixtures
│   ├── sample_credit_report.html       (1.7MB, 32 accounts)
│   └── sample_parsed_report.json       (parser output for UI preview)
├── migrations/                 ← schema deltas for existing DBs
│   ├── 002_add_awaiting_credentials_status.sql
│   └── 003_add_question_attempt_count.sql
└── tools/                      ← diagnostic + test scripts
    ├── diagnose_login_page.py              (re-run if login selectors break)
    ├── diagnose_dashboard.py               (re-run if dashboard menu changes)
    ├── diagnose_credit_report_iframes.py   (if report moves into an iframe)
    ├── test_parser.py                      (parser sanity check)
    ├── test_persistence_e2e.py             (e2e against local Postgres)
    ├── postgres_shim.py                    (psycopg shim mimicking Supabase API)
    └── preview_report_ui.py                (generate standalone UI preview)

Statements/pub/
├── underwrite.html             ← sidebar item + 5-view #creditpull panel
├── js/config.js                ← MODES.CREDITPULL constant
├── js/main.js                  ← imports + mode switching for credit pull
├── js/modules/creditpull.js    ← the frontend module
└── css/creditpull.css          ← styling for tables and payment grid

Statements/app1.py              ← blueprint registered (lines 59 and 82)
```

---

## Reading order if you're new

1. This document, top to bottom.
2. `HANDOFF.md` for a quick state snapshot.
3. `scraper.py` — the most opinionated file, where most of the design pays off.
4. `parser.py` and `persistence.py` together — the data layer.
5. `creditpull_api.py` and `creditpull.js` together — the request/response loop.
6. `MICROSERVICE_PLAN.md` if extraction is the goal.
7. Re-run the diagnostic tools if scraping breaks.

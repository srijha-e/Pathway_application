"""
Credit Pull scraper — production version of the Path B discovery scripts.

Drives Playwright through:
  1. Login (#txtUsername / #txtPassword / #imgBtnLogin)
  2. Security question loop (writes Q to DB, polls for answer, applies)
  3. Optional ad popup dismiss
  4. Hover Reports & Scores → click Credit Reports
  5. Wait for AngularJS render, scroll-to-bottom for infinite-scroll accounts
  6. Capture HTML, parse it, persist to relational schema

The scraper coordinates with the rest of the system through `credit_reports.status`:
    pending → running → [awaiting_input ↔ running] → done | failed

Credentials are passed in-memory only; never logged, never persisted.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime
from typing import Optional

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from .parser import parse_credit_report
from .persistence import save_parsed_report
from . import credentials_store

log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────
IIQ_URL = os.environ.get("IIQ_URL", "https://gcpstage.identityiq.com/")
# Default headless (production); set IIQ_HEADLESS=false locally to watch the browser
IIQ_HEADLESS = os.environ.get("IIQ_HEADLESS", "true").lower() != "false"

# Selectors (confirmed via Path B discovery)
SEL_USERNAME      = "#txtUsername"
SEL_PASSWORD      = "#txtPassword"
SEL_LOGIN_BTN     = "#imgBtnLogin"
SEL_COOKIE_ACCEPT = "#accept-recommended-btn-handler"
SEL_SEC_ANSWER    = "#FBfbforcechangesecurityanswer_txtSecurityAnswer"
SEL_SEC_SUBMIT    = "#FBfbforcechangesecurityanswer_ibtSubmit"
SEL_SEC_HEADING   = "h2:has-text('SECURITY QUESTION')"
SEL_REPORTS_SCORES = 'div.css-txkp1t:has-text("Reports & Scores")'

# Timeouts (seconds)
LOGIN_BTN_TIMEOUT      = 10
NAV_AFTER_LOGIN        = 15
SECURITY_Q_TIMEOUT     = 10
ANSWER_WAIT_TIMEOUT    = 1200       # 20 min for user to answer a security Q
DASHBOARD_SETTLE       = 3
REPORT_RENDER_WAIT     = 5
SCROLL_SETTLE_MS       = 4000
MAX_SCROLL_ATTEMPTS    = 30
SCROLL_STABILITY_NEEDED = 3
FIRST_ACCOUNT_TIMEOUT_MS = 30000
MAX_SECURITY_QS        = 10         # safety cap on TOTAL security Q rounds (covers multi-Q flows + retries)
MAX_QUESTION_ATTEMPTS  = 3          # wrong-answer retries on the same question before failing


# ── Status helpers ────────────────────────────────────────────────────
def _set_status(sb, report_id: str, status: str, error_message: Optional[str] = None) -> None:
    """
    Set status. If error_message is given, store it; otherwise CLEAR it
    (so transitioning back to 'running' wipes any stale error text).
    """
    payload = {
        "status":        status,
        "error_message": error_message[:1000] if error_message else None,
    }
    sb.table("credit_reports").update(payload).eq("id", report_id).execute()


def _fail(sb, report_id: str, message: str) -> None:
    log.warning(f"creditpull[{report_id}] FAIL: {message}")
    _set_status(sb, report_id, "failed", error_message=message)


# ── Security Q handling ───────────────────────────────────────────────
def _is_on_security_question(page) -> bool:
    if "/security-question" in page.url:
        return True
    try:
        return page.locator(SEL_SEC_HEADING).is_visible(timeout=500)
    except Exception:
        return False


def _extract_security_question(page) -> dict:
    """Return {text, type, options} for the current security question screen."""
    radios = [r for r in page.locator("input[type=radio]").all() if r.is_visible()]
    text = ""
    for lab in page.locator("label").all():
        try:
            if not lab.is_visible():
                continue
            t = (lab.text_content() or "").strip()
            if t and "checkbox" not in t.lower():
                text = t
                break
        except Exception:
            continue

    if radios:
        options = []
        for r in radios:
            rid = r.get_attribute("id")
            opt = ""
            if rid:
                lab_for = page.locator(f'label[for="{rid}"]').first
                if lab_for.count() > 0:
                    opt = (lab_for.inner_text() or "").strip()
            options.append(opt)
        return {"text": text, "type": "choice", "options": options}
    return {"text": text, "type": "text", "options": None}


def _persist_question(sb, report_id: str, position: int, q: dict) -> str:
    """Insert a NEW question row, set status to awaiting_input, return question id."""
    result = sb.table("credit_report_questions").insert({
        "report_id":     report_id,
        "position":      position,
        "question_text": q["text"],
        "question_type": q["type"],
        "options":       q.get("options"),
    }).execute()
    qid = result.data[0]["id"]
    _set_status(sb, report_id, "awaiting_input")
    return qid


def _reset_question_for_retry(sb, question_id: str, new_attempt: int) -> None:
    """Clear the answer on an existing question so the user is re-prompted."""
    sb.table("credit_report_questions").update({
        "answer":       None,
        "answered_at":  None,
        "attempt_count": new_attempt,
    }).eq("id", question_id).execute()


def _wait_for_answer(sb, question_id: str, timeout_s: int = ANSWER_WAIT_TIMEOUT) -> Optional[str]:
    """Poll the questions table until the answer field is set (or timeout)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            row = (
                sb.table("credit_report_questions")
                .select("answer")
                .eq("id", question_id)
                .single()
                .execute()
            )
            answer = row.data.get("answer") if row and row.data else None
            if answer:
                return answer
        except Exception as e:
            log.warning(f"poll for answer failed: {e}")
        time.sleep(1.5)
    return None


def _apply_answer(page, q: dict, answer: str) -> None:
    if q["type"] == "text":
        page.locator(SEL_SEC_ANSWER).fill(answer)
    else:
        page.locator(f'label:has-text("{answer}") input[type=radio]').check()
    page.wait_for_function(
        f"() => !document.querySelector('{SEL_SEC_SUBMIT}')?.disabled",
        timeout=SECURITY_Q_TIMEOUT * 1000,
    )
    page.locator(SEL_SEC_SUBMIT).click()


# ── Ad popup ──────────────────────────────────────────────────────────
def _dismiss_ad_if_present(page) -> bool:
    """Look for a 'No thanks' / 'Decline' / 'Skip' button; click if visible."""
    for sel in ("text=No thanks", "text=No, thanks", "text=Not interested",
                "text=Maybe later", "text=Decline", "text=Skip"):
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=400):
                log.info(f"creditpull: dismissed ad via {sel}")
                loc.click()
                page.wait_for_timeout(800)
                return True
        except Exception:
            continue
    return False


# ── Reports & Scores menu ─────────────────────────────────────────────
def _open_reports_menu(page) -> bool:
    page.wait_for_timeout(DASHBOARD_SETTLE * 1000)
    target = page.locator(SEL_REPORTS_SCORES).first
    try:
        target.wait_for(state="visible", timeout=20000)
    except PWTimeout:
        target = page.get_by_text("Reports & Scores", exact=True).first
        try:
            target.wait_for(state="visible", timeout=10000)
        except PWTimeout:
            return False

    target.hover()
    page.wait_for_timeout(1500)
    if page.get_by_text("Credit Reports", exact=False).first.is_visible():
        return True

    target.click()
    page.wait_for_timeout(1500)
    return page.get_by_text("Credit Reports", exact=False).first.is_visible()


# ── Subscription / "report not available" detection ──────────────────
def _check_subscription_required(page) -> bool:
    """
    Returns True if the credit report page is showing the upgrade prompt
    instead of the actual report (i.e. user's subscription doesn't include
    credit reports).

    Detection logic:
      1. If #CreditScore section is visible → report rendered normally
      2. Otherwise, look for IIQ's upgrade UI markers
    """
    # Happy case: the actual report sections are present
    try:
        if page.locator("#CreditScore").is_visible(timeout=2000):
            return False
    except Exception:
        pass

    # Report didn't render. Check for upgrade-prompt indicators.
    upgrade_indicators = (
        "#ucCreditReport_UpgradeHolder",
        ".upgradeHolderLayout",
    )
    for sel in upgrade_indicators:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=500):
                text = (loc.inner_text() or "").strip()
                if text:  # not just an empty placeholder
                    return True
        except Exception:
            continue

    # Text-based fallback (catches cases where IIQ changes IDs/classes)
    for text_sel in ("text=Upgrade Your Plan", "text=Subscribe to view"):
        try:
            if page.locator(text_sel).first.is_visible(timeout=500):
                return True
        except Exception:
            continue

    return False


# ── Scroll loop for infinite-scroll accounts ──────────────────────────
def _scroll_until_stable(page) -> int:
    log.info("creditpull: starting scroll loop for account history")

    # Wait for the first account table to actually render before counting
    # stability — otherwise the loop can latch onto the empty initial state
    # (e.g. 2 in 2 in 2 → stable, exit with 2) in slower headless renders.
    try:
        page.locator("table.crPrint").first.wait_for(timeout=FIRST_ACCOUNT_TIMEOUT_MS)
    except PWTimeout:
        log.warning("creditpull: no account tables appeared within timeout")
        return 0

    prev = -1
    stable = 0
    for attempt in range(1, MAX_SCROLL_ATTEMPTS + 1):
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(SCROLL_SETTLE_MS)
        current = page.locator("table.crPrint").count()
        log.info(f"creditpull scroll: attempt {attempt} → {current} accounts")
        if current == prev:
            stable += 1
            if stable >= SCROLL_STABILITY_NEEDED:
                return current
        else:
            stable = 0
            prev = current
    return prev


# ── Main entry point (runs in a background thread) ────────────────────
def run_credit_pull(sb, report_id: str) -> None:
    """
    Drive the full IIQ flow → parse → persist.

    Credentials come from credentials_store (in-memory, never persisted).
    If login fails, status flips to 'awaiting_credentials' and the scraper
    blocks until new creds arrive (no max-retry on our end — let IIQ's own
    lockout decide).

    On success: status='done' and all child rows populated.
    On failure: status='failed' with error_message.
    """
    log.info(f"creditpull[{report_id}] starting scrape")
    _set_status(sb, report_id, "running")

    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=IIQ_HEADLESS,   # set IIQ_HEADLESS=false to watch the browser
                slow_mo=0,
            )
            context = browser.new_context(viewport={"width": 1400, "height": 900})
            page = context.new_page()

            # ── Navigate to login (once) ─────────────────────────────
            log.info(f"creditpull[{report_id}] navigating to {IIQ_URL}")
            page.goto(IIQ_URL, wait_until="domcontentloaded")
            page.wait_for_selector(SEL_USERNAME, timeout=15000)
            page.wait_for_timeout(800)

            # Cookie banner (once)
            try:
                if page.locator(SEL_COOKIE_ACCEPT).is_visible(timeout=1500):
                    page.locator(SEL_COOKIE_ACCEPT).click()
                    page.wait_for_timeout(400)
            except Exception:
                pass

            # ── Login retry loop (no max — IIQ lockout governs) ──────
            initial = credentials_store.get_credentials(report_id)
            if not initial:
                _fail(sb, report_id, "No credentials available in store.")
                return
            username = initial["username"]
            password = initial["password"]
            last_version = initial["version"]

            login_attempt = 0
            while True:
                login_attempt += 1
                log.info(f"creditpull[{report_id}] login attempt #{login_attempt}")

                # Fill (fill() replaces existing value)
                page.locator(SEL_USERNAME).fill(username)
                page.locator(SEL_PASSWORD).fill(password)
                try:
                    page.wait_for_function(
                        f"() => !document.querySelector('{SEL_LOGIN_BTN}')?.disabled",
                        timeout=LOGIN_BTN_TIMEOUT * 1000,
                    )
                except PWTimeout:
                    _fail(sb, report_id, "Login button never enabled. Page may have changed.")
                    return

                page.locator(SEL_LOGIN_BTN).click()

                # Did the URL change? Success indicator.
                try:
                    page.wait_for_url(lambda u: u != IIQ_URL, timeout=NAV_AFTER_LOGIN * 1000)
                    log.info(f"creditpull[{report_id}] login OK on attempt #{login_attempt}")
                    break
                except PWTimeout:
                    log.info(f"creditpull[{report_id}] login attempt #{login_attempt} failed (URL unchanged)")

                # Bad creds → flip to awaiting_credentials and wait for new ones
                _set_status(
                    sb, report_id, "awaiting_credentials",
                    error_message="Invalid IIQ credentials. Please re-enter and try again.",
                )

                new_creds = credentials_store.wait_for_new_credentials(
                    report_id, since_version=last_version
                )
                if not new_creds:
                    _fail(sb, report_id, "Timed out waiting for new credentials after login failure.")
                    return

                username = new_creds["username"]
                password = new_creds["password"]
                last_version = new_creds["version"]
                _set_status(sb, report_id, "running")
                # Loop to try again

            page.wait_for_timeout(1500)

            # ── Security Q loop ──────────────────────────────────────
            # Tracks both the multi-question case (IIQ asks several Qs in a row)
            # and the wrong-answer retry case (IIQ shows the SAME Q again).
            total_rounds       = 0
            position_counter   = 0
            current_qid        = None
            current_q_text     = None
            attempts_on_curr_q = 0

            while True:
                total_rounds += 1
                if total_rounds > MAX_SECURITY_QS:
                    _fail(sb, report_id, "Exceeded maximum security question rounds.")
                    return

                if not _is_on_security_question(page):
                    break

                q = _extract_security_question(page)

                # Wrong-answer retry: same question text reappeared after submit
                if current_qid and q["text"] == current_q_text:
                    attempts_on_curr_q += 1
                    if attempts_on_curr_q > MAX_QUESTION_ATTEMPTS:
                        _fail(
                            sb, report_id,
                            f"Too many incorrect answers to security question: {q['text']!r}",
                        )
                        return
                    log.info(
                        f"creditpull[{report_id}] security Q retry: "
                        f"attempt {attempts_on_curr_q}/{MAX_QUESTION_ATTEMPTS}"
                    )
                    _reset_question_for_retry(sb, current_qid, attempts_on_curr_q)
                    _set_status(
                        sb, report_id, "awaiting_input",
                        error_message=(
                            f"Wrong answer. Please try again "
                            f"(attempt {attempts_on_curr_q} of {MAX_QUESTION_ATTEMPTS})."
                        ),
                    )
                    qid = current_qid
                else:
                    # Brand-new question
                    position_counter += 1
                    attempts_on_curr_q = 1
                    current_q_text = q["text"]
                    log.info(
                        f"creditpull[{report_id}] security Q #{position_counter}: "
                        f"{q['text']!r} (type={q['type']})"
                    )
                    qid = _persist_question(sb, report_id, position_counter, q)
                    current_qid = qid

                answer = _wait_for_answer(sb, qid)
                if not answer:
                    _fail(sb, report_id, "Timeout waiting for answer to security question.")
                    return

                try:
                    _apply_answer(page, q, answer)
                except Exception as e:
                    _fail(sb, report_id, f"Could not apply answer to security Q: {e}")
                    return

                _set_status(sb, report_id, "running")

                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except PWTimeout:
                    pass
                page.wait_for_timeout(1500)

            # ── Dashboard reached, check for ad ──────────────────────
            log.info(f"creditpull[{report_id}] on dashboard at {page.url}")
            _dismiss_ad_if_present(page)

            # ── Navigate to Credit Reports ───────────────────────────
            if not _open_reports_menu(page):
                _fail(sb, report_id, "Could not open Reports & Scores menu.")
                return

            log.info(f"creditpull[{report_id}] clicking Credit Reports")
            page.get_by_text("Credit Reports", exact=False).first.click()

            # Wait for report shell to render
            try:
                page.wait_for_load_state("networkidle", timeout=30000)
            except PWTimeout:
                pass
            page.wait_for_timeout(REPORT_RENDER_WAIT * 1000)

            # Maybe an ad on this page too
            _dismiss_ad_if_present(page)

            # ── Subscription gate: if the user's plan doesn't include
            # credit reports, IIQ shows an upgrade prompt instead of the
            # report. Detect and fail clearly so the underwriter knows
            # the applicant needs to upgrade before retrying.
            if _check_subscription_required(page):
                _fail(
                    sb, report_id,
                    "User requires upgrade in subscription. Please try after upgrade.",
                )
                return

            # ── Scroll to load all accounts ──────────────────────────
            final_count = _scroll_until_stable(page)
            log.info(f"creditpull[{report_id}] loaded {final_count} accounts")

            # ── Capture rendered HTML ────────────────────────────────
            html = page.content()
            log.info(f"creditpull[{report_id}] captured {len(html):,} chars of HTML")

            # ── Parse + persist ──────────────────────────────────────
            try:
                parsed = parse_credit_report(html)
            except Exception as e:
                _fail(sb, report_id, f"Parser raised: {e}")
                return

            try:
                save_parsed_report(sb, report_id, parsed)
                # save_parsed_report sets status='done'
                log.info(f"creditpull[{report_id}] DONE — persisted {len(parsed.get('accounts', []))} accounts")
            except Exception as e:
                _fail(sb, report_id, f"Persistence raised: {e}")
                return

    except Exception as e:
        log.exception(f"creditpull[{report_id}] unexpected error")
        _fail(sb, report_id, f"Unexpected error: {e}")
    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        # Always wipe creds from in-memory store, even on failure
        try:
            credentials_store.clear_credentials(report_id)
        except Exception:
            pass

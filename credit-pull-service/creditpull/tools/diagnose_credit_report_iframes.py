"""
Diagnostic — login + nav to credit report, enumerate ALL frames, dump
each frame's structure. Use this if the report ever moves into an
iframe (currently it's in the main frame).

Run with:
    ./venv/bin/python Statements/creditpull/tools/diagnose_credit_report_iframes.py
"""
import os
import sys
from getpass import getpass
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

DEBUG_DIR = Path(__file__).resolve().parent / "_debug"
DEBUG_DIR.mkdir(exist_ok=True)

URL = os.environ.get("IIQ_URL", "https://gcpstage.identityiq.com/")

SEL_USERNAME      = "#txtUsername"
SEL_PASSWORD      = "#txtPassword"
SEL_LOGIN_BTN     = "#imgBtnLogin"
SEL_COOKIE_ACCEPT = "#accept-recommended-btn-handler"
SEL_SEC_ANSWER    = "#FBfbforcechangesecurityanswer_txtSecurityAnswer"
SEL_SEC_SUBMIT    = "#FBfbforcechangesecurityanswer_ibtSubmit"
SEL_SEC_HEADING   = "h2:has-text('SECURITY QUESTION')"
SEL_REPORTS_SCORES = 'div.css-txkp1t:has-text("Reports & Scores")'

SECTIONS_TO_FIND = (
    "Personal Information", "Credit Score", "Summary",
    "Account History", "Inquiries", "Creditor Contacts",
)


# ── Reused helpers ────────────────────────────────────────────────────
def is_on_security_question(page) -> bool:
    if "/security-question" in page.url:
        return True
    try:
        return page.locator(SEL_SEC_HEADING).is_visible(timeout=500)
    except Exception:
        return False


def extract_security_question(page) -> dict:
    radios = [r for r in page.locator("input[type=radio]").all() if r.is_visible()]
    text = ""
    for lab in page.locator("label").all():
        if not lab.is_visible():
            continue
        t = (lab.text_content() or "").strip()
        if t and "checkbox" not in t.lower():
            text = t
            break
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


def answer_security_question(page, q: dict, answer: str) -> None:
    if q["type"] == "text":
        page.locator(SEL_SEC_ANSWER).fill(answer)
    else:
        page.locator(f'label:has-text("{answer}") input[type=radio]').check()
    page.wait_for_function(
        f"() => !document.querySelector('{SEL_SEC_SUBMIT}')?.disabled",
        timeout=5000,
    )
    page.locator(SEL_SEC_SUBMIT).click()


def open_reports_menu(page) -> bool:
    print("[menu] looking for Reports & Scores")
    # Give the dashboard React app time to fully hydrate
    page.wait_for_timeout(3000)

    target = page.locator(SEL_REPORTS_SCORES).first
    try:
        target.wait_for(state="visible", timeout=20000)
        print("[menu] found via class selector")
    except PWTimeout:
        print("[menu] class selector failed, trying text-based")
        target = page.get_by_text("Reports & Scores", exact=True).first
        try:
            target.wait_for(state="visible", timeout=10000)
            print("[menu] found via text selector")
        except PWTimeout:
            print("[ERROR] Reports & Scores element never appeared")
            page.screenshot(path=str(DEBUG_DIR / "menu_not_found.png"), full_page=True)
            print(f"[saved] {DEBUG_DIR / 'menu_not_found.png'}")
            return False

    print("[menu] hovering")
    target.hover()
    page.wait_for_timeout(1500)
    if page.get_by_text("Credit Reports", exact=False).first.count() > 0 and \
       page.get_by_text("Credit Reports", exact=False).first.is_visible():
        print("[menu] hover opened submenu")
        return True

    print("[menu] hover did not open submenu, trying click")
    target.click()
    page.wait_for_timeout(1500)
    if page.get_by_text("Credit Reports", exact=False).first.count() > 0 and \
       page.get_by_text("Credit Reports", exact=False).first.is_visible():
        print("[menu] click opened submenu")
        return True

    print("[ERROR] submenu did not appear after hover and click")
    page.screenshot(path=str(DEBUG_DIR / "menu_no_submenu.png"), full_page=True)
    print(f"[saved] {DEBUG_DIR / 'menu_no_submenu.png'}")
    return False


# ── Frame inspection ──────────────────────────────────────────────────
def inspect_frame(frame, depth: int = 0) -> None:
    """Dump everything interesting about a single frame."""
    indent = "  " * depth
    print(f"\n{indent}╔══ FRAME at depth {depth} ══")
    print(f"{indent}║ url:  {frame.url}")
    print(f"{indent}║ name: {frame.name!r}")

    # Try to read content size to confirm it's not empty
    try:
        body_text_len = len((frame.locator("body").text_content() or "").strip())
        print(f"{indent}║ body text length: {body_text_len} chars")
    except Exception as e:
        print(f"{indent}║ body text: <error: {e}>")

    # Headings
    try:
        headings_found = []
        for tag in ("h1", "h2", "h3"):
            for h in frame.locator(tag).all():
                if h.is_visible():
                    t = (h.text_content() or "").strip()
                    if t:
                        headings_found.append(f"<{tag}> {t!r}")
        if headings_found:
            print(f"{indent}║ headings ({len(headings_found)}):")
            for h in headings_found[:15]:
                print(f"{indent}║   {h}")
    except Exception as e:
        print(f"{indent}║ headings: <error: {e}>")

    # Section anchor matches
    try:
        section_hits = {}
        for label in SECTIONS_TO_FIND:
            count = sum(1 for el in frame.get_by_text(label, exact=False).all() if el.is_visible())
            if count > 0:
                section_hits[label] = count
        if section_hits:
            print(f"{indent}║ section text matches:")
            for label, count in section_hits.items():
                print(f"{indent}║   '{label}': {count}")
    except Exception as e:
        print(f"{indent}║ section search: <error: {e}>")

    # Save HTML if it looks meaty
    try:
        body_text_len = len((frame.locator("body").text_content() or "").strip())
        if body_text_len > 200:
            safe_url = frame.url.replace("/", "_").replace(":", "")[:80]
            path = str(DEBUG_DIR / f"frame_d{depth}_{safe_url}.html")
            try:
                with open(path, "w") as fh:
                    fh.write(frame.content())
                print(f"{indent}║ saved: {path}")
            except Exception as e:
                print(f"{indent}║ save failed: {e}")
    except Exception:
        pass

    print(f"{indent}╚══")


def main():
    username = input("IIQ username: ").strip()
    password = getpass("IIQ password (hidden): ")
    if not username or not password:
        sys.exit("Username and password required.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=300)
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()

        # ── Login ─────────────────────────────────────────────────────
        print(f"\n[opening] {URL}")
        page.goto(URL, wait_until="domcontentloaded")
        page.wait_for_selector(SEL_USERNAME, timeout=15000)
        page.wait_for_timeout(800)

        try:
            if page.locator(SEL_COOKIE_ACCEPT).is_visible(timeout=1500):
                page.locator(SEL_COOKIE_ACCEPT).click()
                page.wait_for_timeout(400)
        except Exception:
            pass

        page.locator(SEL_USERNAME).fill(username)
        page.locator(SEL_PASSWORD).fill(password)
        page.wait_for_function(
            f"() => !document.querySelector('{SEL_LOGIN_BTN}')?.disabled",
            timeout=5000,
        )
        print("[clicking] Login")
        page.locator(SEL_LOGIN_BTN).click()
        try:
            page.wait_for_url(lambda u: u != URL, timeout=15000)
        except PWTimeout:
            pass
        page.wait_for_timeout(1500)

        # ── Security Q loop ───────────────────────────────────────────
        for attempt in range(5):
            if not is_on_security_question(page):
                break
            q = extract_security_question(page)
            print(f"\n══════ SECURITY QUESTION #{attempt + 1} ══════")
            print(f"  Text: {q['text']!r}")
            answer = input("  Your answer: ").strip()
            answer_security_question(page, q, answer)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeout:
                pass
            page.wait_for_timeout(1000)

        # ── Navigate to Credit Reports ────────────────────────────────
        if not open_reports_menu(page):
            print("[ERROR] couldn't open Reports & Scores menu")
            input("Inspect & press Enter to close.")
            return

        print("[clicking] Credit Reports")
        page.get_by_text("Credit Reports", exact=False).first.click()

        # ── Wait for report — give it MORE time, watch network ────────
        try:
            page.wait_for_load_state("networkidle", timeout=45000)
        except PWTimeout:
            print("[note] networkidle not reached in 45s; continuing")
        # Iframes load lazily; give them time
        page.wait_for_timeout(5000)

        # ── Enumerate all frames (this is the key step) ───────────────
        print("\n══════ ALL FRAMES ON CREDIT REPORT PAGE ══════")
        frames = page.frames
        print(f"Total frames: {len(frames)}")
        for i, f in enumerate(frames):
            print(f"  [{i}] url={f.url}  name={f.name!r}")

        # Inspect each frame in detail
        for f in frames:
            try:
                inspect_frame(f, depth=0)
            except Exception as e:
                print(f"  <frame inspection failed: {e}>")

        # Save the main page HTML and screenshot too
        with open(DEBUG_DIR / "credit_report_main_page.html", "w") as fh:
            fh.write(page.content())
        page.screenshot(path=str(DEBUG_DIR / "credit_report_main_page.png"), full_page=True)
        print(f"\n[saved] {DEBUG_DIR / 'credit_report_main_page.html'}")
        print(f"[saved] {DEBUG_DIR / 'credit_report_main_page.png'}")

        print()
        input(">>> Browser stays open. Inspect — especially look for iframes in DevTools. Press Enter to close.\n")
        browser.close()


if __name__ == "__main__":
    main()

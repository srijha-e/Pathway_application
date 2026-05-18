"""
Diagnostic — login + handle security Q + land on dashboard, dump structure only.

Re-run if IIQ changes the dashboard sidebar and our `Reports & Scores`
selector (`div.css-txkp1t`) stops matching.

Run with:
    ./venv/bin/python Statements/creditpull/tools/diagnose_dashboard.py
"""
import os
import sys
from getpass import getpass
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = os.environ.get("IIQ_URL", "https://gcpstage.identityiq.com/")
DEBUG_DIR = Path(__file__).resolve().parent / "_debug"
DEBUG_DIR.mkdir(exist_ok=True)

# Selectors confirmed in earlier steps
SEL_USERNAME      = "#txtUsername"
SEL_PASSWORD      = "#txtPassword"
SEL_LOGIN_BTN     = "#imgBtnLogin"
SEL_COOKIE_ACCEPT = "#accept-recommended-btn-handler"
SEL_SEC_ANSWER    = "#FBfbforcechangesecurityanswer_txtSecurityAnswer"
SEL_SEC_SUBMIT    = "#FBfbforcechangesecurityanswer_ibtSubmit"
SEL_SEC_HEADING   = "h2:has-text('SECURITY QUESTION')"


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


def dump_dashboard(page) -> None:
    """Dump everything we might use to find 'Reports & Scores' and its submenu."""
    print("\n══════ DASHBOARD STRUCTURE DUMP ══════")
    print(f"[url]   {page.url}")
    print(f"[title] {page.title()}")

    # All visible links
    print("\n── ALL VISIBLE LINKS ──")
    for a in page.locator("a").all():
        if not a.is_visible():
            continue
        text = (a.text_content() or "").strip()
        if not text:
            continue
        attrs = {
            "id":    a.get_attribute("id"),
            "class": a.get_attribute("class"),
            "href":  a.get_attribute("href"),
        }
        print(f"  text={text!r}  {attrs}")

    # All visible buttons
    print("\n── ALL VISIBLE BUTTONS ──")
    for b in page.locator("button").all():
        if not b.is_visible():
            continue
        text = (b.text_content() or "").strip()
        if not text:
            continue
        attrs = {
            "id":       b.get_attribute("id"),
            "class":    b.get_attribute("class"),
            "type":     b.get_attribute("type"),
        }
        print(f"  text={text!r}  {attrs}")

    # Any element containing "Reports & Scores" text specifically
    print("\n── ELEMENTS WITH TEXT 'Reports & Scores' ──")
    rs_elements = page.get_by_text("Reports & Scores").all()
    print(f"Found {len(rs_elements)} match(es)")
    for i, el in enumerate(rs_elements):
        try:
            tag = el.evaluate("el => el.tagName")
            cls = el.get_attribute("class")
            elid = el.get_attribute("id")
            href = el.get_attribute("href")
            visible = el.is_visible()
            print(f"  [{i}] <{tag.lower()}> id={elid!r} class={cls!r} href={href!r} visible={visible}")
        except Exception as e:
            print(f"  [{i}] error inspecting: {e}")

    # Same for "Credit Reports" — but it's hidden until hover
    print("\n── ELEMENTS WITH TEXT 'Credit Reports' (may be hidden) ──")
    cr_elements = page.get_by_text("Credit Reports").all()
    print(f"Found {len(cr_elements)} match(es) (any visibility)")
    for i, el in enumerate(cr_elements):
        try:
            tag = el.evaluate("el => el.tagName")
            cls = el.get_attribute("class")
            elid = el.get_attribute("id")
            href = el.get_attribute("href")
            visible = el.is_visible()
            print(f"  [{i}] <{tag.lower()}> id={elid!r} class={cls!r} href={href!r} visible={visible}")
        except Exception as e:
            print(f"  [{i}] error inspecting: {e}")

    # Sidebar / aside / nav containers
    print("\n── SIDEBAR / NAV CONTAINERS ──")
    for sel in ("aside", "nav", "[role=navigation]", ".sidebar"):
        for i, el in enumerate(page.locator(sel).all()):
            if not el.is_visible():
                continue
            elid = el.get_attribute("id") or ""
            cls = el.get_attribute("class") or ""
            print(f"  <{sel}> id={elid!r} class={cls[:80]!r}")


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

        # ── Security Q loop (handles 0, 1, or many) ───────────────────
        for attempt in range(5):
            if not is_on_security_question(page):
                if attempt == 0:
                    print("[note] no security question this time")
                break
            q = extract_security_question(page)
            print(f"\n══════ SECURITY QUESTION #{attempt + 1} ══════")
            print(f"  Text: {q['text']!r}")
            print(f"  Type: {q['type']}")
            if q["options"]:
                print(f"  Options: {q['options']}")
            answer = input("  Your answer: ").strip()
            answer_security_question(page, q, answer)
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except PWTimeout:
                pass
            page.wait_for_timeout(1000)

        # ── On dashboard now ──────────────────────────────────────────
        print(f"\n[dashboard URL] {page.url}")

        # Save dashboard fixture
        with open(DEBUG_DIR / "dashboard_page.html", "w") as fh:
            fh.write(page.content())
        page.screenshot(path=str(DEBUG_DIR / "dashboard_page.png"), full_page=True)
        print(f"[saved] {DEBUG_DIR / 'dashboard_page.html'}")
        print(f"[saved] {DEBUG_DIR / 'dashboard_page.png'}")

        # ── Dump structure ────────────────────────────────────────────
        dump_dashboard(page)

        print()
        input(
            ">>> Browser stays open. Hover over 'Reports & Scores' in the sidebar\n"
            ">>> and inspect the submenu in DevTools if you want. Then press Enter to close.\n"
        )
        browser.close()


if __name__ == "__main__":
    main()

"""
Diagnostic script — no fill, no click.
Opens the login page, waits for it to render, dumps the form structure
so we can pick the right selectors.

Re-run this if IIQ changes their login HTML and our scraper selectors
(`#txtUsername`, `#txtPassword`, `#imgBtnLogin`) stop matching.

Run with:
    ./venv/bin/python Statements/creditpull/tools/diagnose_login_page.py
"""
import os
from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

URL = os.environ.get("IIQ_URL", "https://gcpstage.identityiq.com/")
DEBUG_DIR = Path(__file__).resolve().parent / "_debug"
DEBUG_DIR.mkdir(exist_ok=True)


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=200)
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()

        print(f"\n[opening] {URL}")
        page.goto(URL, wait_until="domcontentloaded")

        # Wait for the form to actually render (React SPA)
        print("[waiting for form to render...]")
        try:
            page.wait_for_selector("form", timeout=15000)
            print("[✓] form element found")
        except PWTimeout:
            print("[✗] no <form> appeared in 15s — page may use a different structure")

        # Give React a moment past the initial render
        page.wait_for_timeout(1500)

        print(f"\n[final URL] {page.url}")
        print(f"[title]     {page.title()}")

        # ── Dump all inputs on the page ───────────────────────────────
        print("\n── ALL INPUT ELEMENTS ──")
        inputs = page.locator("input").all()
        print(f"Found {len(inputs)} input(s)")
        for i, inp in enumerate(inputs):
            attrs = {
                "type":         inp.get_attribute("type"),
                "name":         inp.get_attribute("name"),
                "id":           inp.get_attribute("id"),
                "placeholder":  inp.get_attribute("placeholder"),
                "autocomplete": inp.get_attribute("autocomplete"),
                "aria-label":   inp.get_attribute("aria-label"),
                "visible":      inp.is_visible(),
            }
            print(f"  [{i}] {attrs}")

        # ── Dump all labels ───────────────────────────────────────────
        print("\n── ALL LABEL ELEMENTS ──")
        labels = page.locator("label").all()
        print(f"Found {len(labels)} label(s)")
        for i, lab in enumerate(labels):
            text = (lab.text_content() or "").strip()
            for_attr = lab.get_attribute("for")
            print(f"  [{i}] text={text!r}  for={for_attr!r}")

        # ── Dump all buttons ──────────────────────────────────────────
        print("\n── ALL BUTTON ELEMENTS ──")
        buttons = page.locator("button").all()
        for i, btn in enumerate(buttons):
            text = (btn.text_content() or "").strip()
            attrs = {
                "id":       btn.get_attribute("id"),
                "name":     btn.get_attribute("name"),
                "type":     btn.get_attribute("type"),
                "disabled": btn.is_disabled(),
            }
            print(f"  [{i}] text={text!r}  {attrs}")

        # ── Detect iframes (form might be inside one) ────────────────
        print("\n── IFRAMES ──")
        frames = page.frames
        print(f"Total frames on page: {len(frames)}")
        for i, f in enumerate(frames):
            print(f"  [{i}] url={f.url}")

        # ── Save full HTML for offline inspection ────────────────────
        html_path = str(DEBUG_DIR / "login_page_dump.html")
        with open(html_path, "w") as fh:
            fh.write(page.content())
        print(f"\n[saved] {html_path}")

        # ── Screenshot ────────────────────────────────────────────────
        page.screenshot(path=str(DEBUG_DIR / "login_page_dump.png"), full_page=True)
        print(f"[saved] {DEBUG_DIR / 'login_page_dump.png'}")

        print()
        input(">>> Inspect the page however you want, then press Enter to close.\n")

        browser.close()


if __name__ == "__main__":
    main()

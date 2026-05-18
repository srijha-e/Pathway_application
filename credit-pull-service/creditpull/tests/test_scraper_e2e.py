"""
Full end-to-end scraper test against the local Postgres test DB.

You'll watch a real Chrome window navigate IdentityIQ. Security questions
prompt you in this terminal. Re-credentials (after a bad-creds rejection)
prompt you in this terminal. Data lands in the local `creditpull_test`
database via the Supabase-API shim.

Recommended invocation (browser visible):
    IIQ_HEADLESS=false ./venv/bin/python Statements/creditpull/tests/test_scraper_e2e.py

Or invisibly (faster, but you can't watch):
    ./venv/bin/python Statements/creditpull/tests/test_scraper_e2e.py
"""
import os
import sys
import time
import threading
import uuid
from datetime import datetime
from getpass import getpass
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_PARENT = HERE.parent.parent.parent
sys.path.insert(0, str(PKG_PARENT))

from Statements.creditpull import credentials_store                    # noqa: E402
from Statements.creditpull.scraper import run_credit_pull              # noqa: E402
from Statements.creditpull.persistence import load_full_report         # noqa: E402
from Statements.creditpull.tests.postgres_shim import connect          # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────
def prompt_credentials() -> tuple[str, str]:
    print("\n── IIQ test credentials ───────────────────────")
    username = input("IIQ username: ").strip()
    password = getpass("IIQ password (hidden): ")
    return username, password


def monitor_loop(sb, report_id: str) -> bool:
    """
    Watch the credit_reports row and answer prompts as needed.
    Returns True on status='done', False on status='failed'.
    """
    last_status = None
    last_question_id = None
    last_question_attempt = 0

    while True:
        try:
            r = (
                sb.table("credit_reports")
                .select("status, error_message")
                .eq("id", report_id)
                .single()
                .execute()
            )
        except Exception as e:
            print(f"[monitor] DB read failed, retrying: {e}")
            time.sleep(2)
            continue

        status = r.data["status"]
        err = r.data.get("error_message")

        # Print transitions
        if status != last_status:
            err_str = f" — {err}" if err else ""
            print(f"\n[status] {status}{err_str}")
            last_status = status
            if status != "awaiting_input":
                last_question_id = None
                last_question_attempt = 0

        # Terminal states
        if status == "done":
            return True
        if status == "failed":
            return False

        # Bad creds → re-prompt
        if status == "awaiting_credentials":
            print("\n>>> IIQ rejected the credentials. Please re-enter:")
            user, pwd = prompt_credentials()
            credentials_store.set_credentials(report_id, user, pwd)
            print("[ok] new credentials submitted; scraper will retry")
            time.sleep(2)
            continue

        # Security question → answer in terminal
        if status == "awaiting_input":
            try:
                qres = (
                    sb.table("credit_report_questions")
                    .select("*")
                    .eq("report_id", report_id)
                    .is_("answered_at", "null")
                    .order("position", desc=True)
                    .limit(1)
                    .execute()
                )
            except Exception as e:
                print(f"[monitor] read question failed: {e}")
                time.sleep(2)
                continue

            if not qres.data:
                # Scraper might have just inserted the question row but DB
                # commit hasn't propagated yet; wait briefly and retry
                time.sleep(1)
                continue

            q = qres.data[0]
            qid = q["id"]
            attempt = q.get("attempt_count", 1)

            # Only re-prompt if it's a new question OR a retry of the same Q
            if qid != last_question_id or attempt != last_question_attempt:
                last_question_id = qid
                last_question_attempt = attempt
                print(f"\n>>> Security Question (attempt {attempt})")
                print(f"    Q: {q['question_text']}")
                if q["question_type"] == "choice" and q.get("options"):
                    print("    Options:")
                    for opt in q["options"]:
                        print(f"      - {opt}")
                if err and attempt > 1:
                    print(f"    ⚠ {err}")
                answer = input("    Your answer: ").strip()
                try:
                    sb.table("credit_report_questions").update({
                        "answer": answer,
                        "answered_at": datetime.utcnow().isoformat(),
                    }).eq("id", qid).execute()
                    print("[ok] answer submitted; scraper will continue")
                except Exception as e:
                    print(f"[monitor] failed to write answer: {e}")

        time.sleep(1)


def print_summary(sb, report_id: str) -> None:
    print("\n══════ STORED DATA SUMMARY ══════")

    cur = sb.conn.cursor()
    for tbl, where in (
        ("credit_reports",                  "WHERE id = %s"),
        ("credit_report_questions",         "WHERE report_id = %s"),
        ("credit_report_personal_info",     "WHERE report_id = %s"),
        ("credit_report_scores",            "WHERE report_id = %s"),
        ("credit_report_risk_factors",      "WHERE report_id = %s"),
        ("credit_report_summary",           "WHERE report_id = %s"),
        ("credit_report_accounts",          "WHERE report_id = %s"),
    ):
        cur.execute(f"SELECT count(*) FROM {tbl} {where}", (report_id,))
        n = cur.fetchone()[0]
        print(f"  {tbl:<35} {n:>5} rows")

    for tbl in ("credit_report_account_details", "credit_report_payment_history"):
        cur.execute(f"""
            SELECT count(*) FROM {tbl}
            WHERE account_id IN (SELECT id FROM credit_report_accounts WHERE report_id = %s)
        """, (report_id,))
        n = cur.fetchone()[0]
        print(f"  {tbl:<35} {n:>5} rows")
    cur.close()

    # Round-trip via load_full_report (simulates what the CRM frontend gets)
    report = load_full_report(sb, report_id)
    if report:
        print(f"\n  reference_number: {report['header'].get('reference_number')}")
        print(f"  report_date:      {report['header'].get('report_date')}")
        print(f"  subject_name:     {report['header'].get('subject_name')}")
        scores = report["credit_score"]["scores"]
        for b in ("transunion", "experian", "equifax"):
            s = scores.get(b, {})
            if s.get("score"):
                print(f"  {b:>11} score: {s['score']} ({s.get('lender_rank') or '-'})")
        print(f"  accounts:        {len(report.get('accounts', []))}")


# ── Main ──────────────────────────────────────────────────────────────
def main():
    print("══════ Credit Pull — End-to-End Scraper Test ══════")
    print("This will:")
    print("  1. Open a real Chrome window (headed) and log in to IIQ")
    print("  2. Handle security questions via terminal prompts")
    print("  3. Scrape the credit report, parse it, store it in creditpull_test")
    print("  4. Print a summary of what landed in the database")
    print()

    if os.environ.get("IIQ_HEADLESS", "true").lower() != "false":
        print("⚠ Browser will run HEADLESS (you won't see it).")
        print("  To watch: IIQ_HEADLESS=false ./venv/bin/python Statements/creditpull/tests/test_scraper_e2e.py")
        print()

    username, password = prompt_credentials()
    if not username or not password:
        sys.exit("Username and password required.")

    print("\n[connect] connecting to creditpull_test ...")
    sb = connect("creditpull_test")

    test_subject_id = str(uuid.uuid4())
    test_report_id = str(uuid.uuid4())

    print(f"[setup] inserting test subject {test_subject_id}")
    sb.table("credit_subjects").insert({
        "id": test_subject_id,
        "full_name": "E2E TEST SUBJECT",
    }).execute()

    print(f"[setup] inserting credit_reports row {test_report_id} (status=pending)")
    sb.table("credit_reports").insert({
        "id": test_report_id,
        "subject_id": test_subject_id,
        "status": "pending",
    }).execute()

    credentials_store.set_credentials(test_report_id, username, password)

    print("\n[scraper] spawning background thread; check the browser window for activity")
    scraper_error: list[Exception] = []

    def _run():
        try:
            run_credit_pull(sb, test_report_id)
        except Exception as e:
            scraper_error.append(e)

    t = threading.Thread(target=_run, name="creditpull-test", daemon=True)
    t.start()

    print("[monitor] watching status; will prompt you for security Qs / new creds as needed\n")
    success = monitor_loop(sb, test_report_id)

    t.join(timeout=10)

    if scraper_error:
        print(f"\n[scraper] thread crashed with: {scraper_error[0]}")

    if success:
        print("\n✓ SCRAPE COMPLETED")
        print_summary(sb, test_report_id)
    else:
        print("\n✗ SCRAPE FAILED")

    print()
    keep = input("Keep this test data in creditpull_test? [y/N]: ").strip().lower()
    if keep != "y":
        print(f"[cleanup] deleting subject {test_subject_id} (CASCADE wipes everything)")
        cur = sb.conn.cursor()
        cur.execute("DELETE FROM credit_subjects WHERE id = %s", (test_subject_id,))
        sb.conn.commit()
        cur.close()
        print("[cleanup] done")
    else:
        print(f"[keep] subject_id={test_subject_id}")
        print(f"[keep] report_id={test_report_id}")
        print(f"  Inspect with:  psql -d creditpull_test")

    sb.conn.close()


if __name__ == "__main__":
    main()

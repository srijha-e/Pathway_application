"""
End-to-end persistence test (no scraping, no browser).

Loads the saved parsed credit report fixture, inserts a subject + report,
runs save_parsed_report() against local Postgres, then runs load_full_report()
and compares the round-trip.

Run with:
    ./venv/bin/python Statements/creditpull/tools/test_persistence_e2e.py
"""
import json
import sys
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG_PARENT = HERE.parent.parent.parent
sys.path.insert(0, str(PKG_PARENT))

from Statements.creditpull.persistence import save_parsed_report, load_full_report  # noqa: E402
from Statements.creditpull.tests.postgres_shim import connect                       # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "sample_parsed_report.json"


def main():
    print(f"Loading parsed fixture: {FIXTURE.name}")
    parsed = json.loads(FIXTURE.read_text())

    sb = connect("creditpull_test")
    test_subject_id = str(uuid.uuid4())
    test_report_id  = str(uuid.uuid4())

    try:
        # 1. Insert subject
        print(f"[1] inserting subject {test_subject_id}")
        sb.table("credit_subjects").insert({
            "id":            test_subject_id,
            "full_name":     parsed["header"].get("subject_name") or "TEST SUBJECT",
            "date_of_birth": "1985-11-01",
            "ssn_last4":     "0000",
        }).execute()

        # 2. Insert report row (status=running, will be updated to done by save_parsed_report)
        print(f"[2] inserting credit_reports row {test_report_id}")
        sb.table("credit_reports").insert({
            "id":         test_report_id,
            "subject_id": test_subject_id,
            "status":     "running",
        }).execute()

        # 3. Run persistence
        print("[3] calling save_parsed_report() …")
        save_parsed_report(sb, test_report_id, parsed)
        print("    ✓ save_parsed_report completed")

        # 4. Verify row counts
        print("\n[4] row counts in tables:")
        for tbl in ("credit_reports", "credit_report_personal_info", "credit_report_scores",
                    "credit_report_risk_factors", "credit_report_summary",
                    "credit_report_accounts", "credit_report_account_details",
                    "credit_report_payment_history"):
            cur = sb.conn.cursor()
            if tbl == "credit_reports":
                cur.execute(f"SELECT count(*) FROM {tbl} WHERE id = %s", (test_report_id,))
            elif tbl in ("credit_report_account_details", "credit_report_payment_history"):
                cur.execute(f"""
                    SELECT count(*) FROM {tbl}
                    WHERE account_id IN (SELECT id FROM credit_report_accounts WHERE report_id = %s)
                """, (test_report_id,))
            else:
                cur.execute(f"SELECT count(*) FROM {tbl} WHERE report_id = %s", (test_report_id,))
            n = cur.fetchone()[0]
            cur.close()
            print(f"    {tbl:<35} {n:>5} rows")

        # 5. Round-trip via load_full_report
        print("\n[5] calling load_full_report() …")
        loaded = load_full_report(sb, test_report_id)
        print(f"    accounts loaded: {len(loaded['accounts'])}")
        print(f"    personal_info rows: {len(loaded['personal_information'])}")
        print(f"    summary rows: {len(loaded['summary'])}")
        print(f"    credit_score TU: {loaded['credit_score']['scores']['transunion']}")
        print(f"    risk_factors TU: {len(loaded['credit_score']['risk_factors']['transunion'])} factors")

        # Cross-check key counts
        assert len(loaded["accounts"]) == len(parsed["accounts"]), \
            f"account count mismatch: {len(loaded['accounts'])} vs {len(parsed['accounts'])}"
        assert loaded["credit_score"]["scores"]["transunion"]["score"] == \
               parsed["credit_score"]["scores"]["transunion"]["score"], "TU score mismatch"

        print("\n✓ ALL CHECKS PASSED — parser/persistence round-trip works against local Postgres")

    finally:
        # Cleanup
        print(f"\n[cleanup] deleting test subject {test_subject_id} (CASCADE wipes everything)")
        cur = sb.conn.cursor()
        cur.execute("DELETE FROM credit_subjects WHERE id = %s", (test_subject_id,))
        sb.conn.commit()
        cur.close()
        sb.conn.close()
        print("[cleanup] done")


if __name__ == "__main__":
    main()

"""
Run the parser against the saved HTML fixture and pretty-print the
structured output. Verifies each section parses correctly without
needing Supabase or the scraper.

Run with:
    ./venv/bin/python Statements/creditpull/tools/test_parser.py
"""
import json
import sys
from pathlib import Path

# Make the creditpull package importable when running this script directly
HERE = Path(__file__).resolve().parent          # .../creditpull/tools
PKG_PARENT = HERE.parent.parent.parent          # repo root
sys.path.insert(0, str(PKG_PARENT))

from Statements.creditpull.parser import parse_credit_report  # noqa: E402

FIXTURE = HERE.parent / "fixtures" / "sample_credit_report.html"
OUT_JSON = HERE.parent / "fixtures" / "sample_parsed_report.json"


def summary_line(label, value):
    print(f"  {label:<30} {value}")


def main():
    if not FIXTURE.exists():
        print(f"Fixture not found: {FIXTURE}")
        sys.exit(1)

    print(f"Loading fixture: {FIXTURE.name} ({FIXTURE.stat().st_size:,} bytes)")
    html = FIXTURE.read_text()

    print("Parsing...\n")
    result = parse_credit_report(html)

    print("══════ PARSE SUMMARY ══════")
    print("\nheader:")
    summary_line("reference_number", result["header"]["reference_number"])
    summary_line("report_date",      result["header"]["report_date"])
    summary_line("subject_name",     result["header"]["subject_name"])

    print(f"\npersonal_information: {len(result['personal_information'])} rows")
    for row in result["personal_information"]:
        summary_line(row["field"], f"TU={row['transunion']!r}  EX={row['experian']!r}  EQ={row['equifax']!r}")

    print("\ncredit_score:")
    for bureau, score_data in result["credit_score"]["scores"].items():
        summary_line(bureau, score_data)
    print("  risk_factors:")
    for bureau, factors in result["credit_score"]["risk_factors"].items():
        summary_line(f"  {bureau} ({len(factors)})", factors[:3] if factors else "[]")
        if len(factors) > 3:
            print(f"    ... and {len(factors) - 3} more")

    print(f"\nsummary: {len(result['summary'])} rows")
    for row in result["summary"]:
        summary_line(row["field"], f"TU={row['transunion']!r}  EX={row['experian']!r}  EQ={row['equifax']!r}")

    print(f"\naccounts: {len(result['accounts'])} accounts parsed")
    for i, acct in enumerate(result["accounts"][:3]):
        print(f"\n  [{i}] {acct['creditor_name']}")
        print(f"      fields: {len(acct['fields'])} rows")
        for f in acct["fields"][:5]:
            print(f"        {f['field']:<22} TU={f['transunion']!r}")
        if len(acct["fields"]) > 5:
            print(f"        ... and {len(acct['fields']) - 5} more fields")
        ph = acct["payment_history"]
        print(f"      payment_history: TU={len(ph['transunion'])} months, "
              f"EX={len(ph['experian'])} months, EQ={len(ph['equifax'])} months")
        if ph["transunion"]:
            sample = list(ph["transunion"].items())[:3]
            print(f"        sample TU: {sample}")

    if len(result["accounts"]) > 3:
        print(f"\n  ... and {len(result['accounts']) - 3} more accounts")

    OUT_JSON.write_text(json.dumps(result, indent=2, default=str))
    print(f"\n[saved] full parsed JSON: {OUT_JSON}")


if __name__ == "__main__":
    main()

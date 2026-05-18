"""
Persistence helpers for the Credit Pull module.

Two main functions:
    save_parsed_report(sb, report_id, parsed)  → writes parser output to 10 tables
    load_full_report(sb, report_id)            → reads back as a parser-shaped dict

The parser produces strings ('$127,765.00', '08/29/2023', '32'); these helpers
convert to typed Postgres values (NUMERIC, DATE, INTEGER) so the schema
constraints accept them.
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Optional


# ── Type coercion helpers ─────────────────────────────────────────────
def _to_decimal(s: Optional[str]) -> Optional[Decimal]:
    if s is None or s == "":
        return None
    cleaned = re.sub(r"[\$,\s]", "", str(s))
    if cleaned in ("", "-"):
        return None
    try:
        return Decimal(cleaned)
    except InvalidOperation:
        return None


def _to_int(s: Any) -> Optional[int]:
    if s is None:
        return None
    if isinstance(s, int):
        return s
    cleaned = re.sub(r"[,\s]", "", str(s))
    if cleaned in ("", "-"):
        return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _to_date(s: Optional[str]) -> Optional[str]:
    """Return ISO-format YYYY-MM-DD string (or None) for DB DATE columns."""
    if s is None or not str(s).strip():
        return None
    raw = str(s).strip()
    # IIQ dates come as MM/DD/YYYY or M/D/YYYY
    m = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", raw)
    if m:
        mm, dd, yyyy = m.groups()
        try:
            return date(int(yyyy), int(mm), int(dd)).isoformat()
        except ValueError:
            return None
    # Already ISO?
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    return None


# ── Field-name normalization ──────────────────────────────────────────
# Parser yields free-text like 'Total Accounts'; map to schema column names
SUMMARY_FIELD_MAP = {
    "total accounts":     "total_accounts",
    "open accounts":      "open_accounts",
    "closed accounts":    "closed_accounts",
    "delinquent":         "delinquent",
    "derogatory":         "derogatory",
    "collection":         "collection",
    "balances":           "balances",
    "payments":           "payments",
    "public records":     "public_records",
    "inquiries(2 years)": "inquiries_2yr",
    "inquiries (2 years)": "inquiries_2yr",
}

ACCOUNT_FIELD_MAP = {
    "account #":             "account_number",
    "account type":          "account_type",
    "account type - detail": "account_type_detail",
    "bureau code":           "bureau_code",
    "account status":        "account_status",
    "monthly payment":       "monthly_payment",
    "date opened":           "date_opened",
    "balance":               "balance",
    "no. of months (terms)": "no_of_months_terms",
    "high credit":           "high_credit",
    "credit limit":          "credit_limit",
    "past due":              "past_due",
    "payment status":        "payment_status",
    "last reported":         "last_reported",
    "comments":              "comments",
    "date last active":      "date_last_active",
    "date of last payment":  "date_of_last_payment",
}

# Which account-detail columns get which type
ACCOUNT_NUMERIC = {"monthly_payment", "balance", "high_credit", "credit_limit", "past_due"}
ACCOUNT_INT     = {"no_of_months_terms"}
ACCOUNT_DATE    = {"date_opened", "last_reported", "date_last_active", "date_of_last_payment"}


# ── Save parsed → DB ──────────────────────────────────────────────────
def save_parsed_report(sb, report_id: str, parsed: dict) -> None:
    """
    Insert all child rows for a credit_reports row that already exists.
    The report row itself (and its subject_id) must be created beforehand.

    Updates the report row with reference_number, report_date, status='done'.
    """
    # ── Update the report header ──────────────────────────────────────
    header = parsed.get("header", {})
    sb.table("credit_reports").update({
        "reference_number": header.get("reference_number"),
        "report_date":      _to_date(header.get("report_date")),
        "status":           "done",
    }).eq("id", report_id).execute()

    # ── Personal Info ────────────────────────────────────────────────
    pi_rows = []
    for row in parsed.get("personal_information", []):
        for bureau in ("transunion", "experian", "equifax"):
            pi_rows.append({
                "report_id":  report_id,
                "field_name": row["field"],
                "bureau":     bureau,
                "value":      row.get(bureau),
            })
    if pi_rows:
        sb.table("credit_report_personal_info").insert(pi_rows).execute()

    # ── Credit Scores ────────────────────────────────────────────────
    score_rows = []
    for bureau, data in parsed.get("credit_score", {}).get("scores", {}).items():
        score_rows.append({
            "report_id":   report_id,
            "bureau":      bureau,
            "score":       _to_int(data.get("score")),
            "lender_rank": data.get("lender_rank"),
            "score_scale": data.get("score_scale"),
        })
    if score_rows:
        sb.table("credit_report_scores").insert(score_rows).execute()

    # ── Risk Factors ─────────────────────────────────────────────────
    rf_rows = []
    for bureau, factors in parsed.get("credit_score", {}).get("risk_factors", {}).items():
        for i, factor in enumerate(factors, start=1):
            rf_rows.append({
                "report_id": report_id,
                "bureau":    bureau,
                "position":  i,
                "factor":    factor,
            })
    if rf_rows:
        sb.table("credit_report_risk_factors").insert(rf_rows).execute()

    # ── Summary (one row per bureau, columns from SUMMARY_FIELD_MAP) ─
    summary_per_bureau: dict[str, dict] = {
        "transunion": {"report_id": report_id, "bureau": "transunion"},
        "experian":   {"report_id": report_id, "bureau": "experian"},
        "equifax":    {"report_id": report_id, "bureau": "equifax"},
    }
    money_cols = {"balances", "payments"}
    for row in parsed.get("summary", []):
        col = SUMMARY_FIELD_MAP.get(row["field"].lower())
        if not col:
            continue
        for bureau in ("transunion", "experian", "equifax"):
            raw = row.get(bureau)
            value = _to_decimal(raw) if col in money_cols else _to_int(raw)
            # DB layer accepts None; only set when not None
            if value is not None:
                summary_per_bureau[bureau][col] = value
    summary_rows = [r for r in summary_per_bureau.values() if len(r) > 2]
    if summary_rows:
        sb.table("credit_report_summary").insert(summary_rows).execute()

    # ── Accounts + Account Details + Payment History ─────────────────
    for position, account in enumerate(parsed.get("accounts", []), start=1):
        # Insert account header
        a_resp = sb.table("credit_report_accounts").insert({
            "report_id":     report_id,
            "position":      position,
            "creditor_name": account["creditor_name"],
        }).execute()
        account_id = a_resp.data[0]["id"]

        # Insert account details (per bureau row)
        per_bureau: dict[str, dict] = {
            "transunion": {"account_id": account_id, "bureau": "transunion"},
            "experian":   {"account_id": account_id, "bureau": "experian"},
            "equifax":    {"account_id": account_id, "bureau": "equifax"},
        }
        for row in account.get("fields", []):
            col = ACCOUNT_FIELD_MAP.get(row["field"].lower())
            if not col:
                continue
            for bureau in ("transunion", "experian", "equifax"):
                raw = row.get(bureau)
                if raw is None:
                    continue
                if col in ACCOUNT_NUMERIC:
                    value = _to_decimal(raw)
                elif col in ACCOUNT_INT:
                    value = _to_int(raw)
                elif col in ACCOUNT_DATE:
                    value = _to_date(raw)
                else:
                    value = raw
                if value is not None:
                    per_bureau[bureau][col] = value
        details_rows = [r for r in per_bureau.values() if len(r) > 2]
        if details_rows:
            sb.table("credit_report_account_details").insert(details_rows).execute()

        # Insert payment history
        ph_rows = []
        for bureau, months in account.get("payment_history", {}).items():
            for ym, status in months.items():
                m = re.match(r"^(\d{4})-(\d{2})$", ym)
                if not m:
                    continue
                ph_rows.append({
                    "account_id": account_id,
                    "bureau":     bureau,
                    "year":       int(m.group(1)),
                    "month":      int(m.group(2)),
                    "status":     status,
                })
        if ph_rows:
            sb.table("credit_report_payment_history").insert(ph_rows).execute()


# ── Load DB → parser-shaped dict (for reads) ──────────────────────────
def load_full_report(sb, report_id: str) -> Optional[dict]:
    """Read all tables for a report and return a dict shaped like parser output."""
    rep = sb.table("credit_reports").select("*").eq("id", report_id).execute()
    if not rep.data:
        return None
    r = rep.data[0]

    pi = sb.table("credit_report_personal_info").select("*").eq("report_id", report_id).execute().data
    scores = sb.table("credit_report_scores").select("*").eq("report_id", report_id).execute().data
    rf = sb.table("credit_report_risk_factors").select("*").eq("report_id", report_id).execute().data
    summary = sb.table("credit_report_summary").select("*").eq("report_id", report_id).execute().data
    accounts = sb.table("credit_report_accounts").select("*").eq("report_id", report_id).order("position").execute().data

    # Re-pivot pi rows back into [{field, transunion, experian, equifax}]
    pi_grouped: dict[str, dict] = {}
    for row in pi:
        d = pi_grouped.setdefault(row["field_name"], {"field": row["field_name"]})
        d[row["bureau"]] = row["value"]
    personal_information = list(pi_grouped.values())

    # Scores by bureau
    score_map = {b: {"score": None, "lender_rank": None, "score_scale": None} for b in ("transunion","experian","equifax")}
    for s in scores:
        score_map[s["bureau"]] = {
            "score":       s.get("score"),
            "lender_rank": s.get("lender_rank"),
            "score_scale": s.get("score_scale"),
        }

    rf_map = {b: [] for b in ("transunion", "experian", "equifax")}
    for f in sorted(rf, key=lambda x: x["position"]):
        rf_map[f["bureau"]].append(f["factor"])

    # Summary back to row format — preserve IIQ's display order
    summary_back: list[dict] = []
    if summary:
        reverse = {v: k.title() for k, v in SUMMARY_FIELD_MAP.items()}
        # Iterate field map in declaration order (NOT a set!) so rows appear
        # in the same order IIQ displays them.
        ordered_cols = list(dict.fromkeys(SUMMARY_FIELD_MAP.values()))
        for col in ordered_cols:
            row = {"field": reverse.get(col, col), "transunion": None, "experian": None, "equifax": None}
            for s in summary:
                if col in s:
                    row[s["bureau"]] = s.get(col)
            summary_back.append(row)

    # Accounts: details + payment history
    accounts_out: list[dict] = []
    for a in accounts:
        details = sb.table("credit_report_account_details").select("*").eq("account_id", a["id"]).execute().data
        history = sb.table("credit_report_payment_history").select("*").eq("account_id", a["id"]).execute().data

        # Re-pivot details — preserve IIQ's display order (Account #, Account Type, ...)
        reverse_acct = {v: k.title() for k, v in ACCOUNT_FIELD_MAP.items()}
        ordered_acct_cols = list(dict.fromkeys(ACCOUNT_FIELD_MAP.values()))
        fields_back = []
        for col in ordered_acct_cols:
            row = {"field": reverse_acct.get(col, col), "transunion": None, "experian": None, "equifax": None}
            for d in details:
                if col in d:
                    row[d["bureau"]] = d.get(col)
            fields_back.append(row)

        ph_map = {b: {} for b in ("transunion", "experian", "equifax")}
        for h in history:
            key = f"{h['year']}-{h['month']:02d}"
            ph_map[h["bureau"]][key] = h["status"]

        accounts_out.append({
            "creditor_name":   a["creditor_name"],
            "fields":          fields_back,
            "payment_history": ph_map,
        })

    return {
        "report_id":    report_id,
        "subject_id":   r["subject_id"],
        "status":       r["status"],
        "header": {
            "reference_number": r.get("reference_number"),
            "report_date":      str(r["report_date"]) if r.get("report_date") else None,
            "subject_name":     None,  # populated from Personal Info "Name" row below
        },
        "personal_information": personal_information,
        "credit_score":         {"scores": score_map, "risk_factors": rf_map},
        "summary":              summary_back,
        "accounts":             accounts_out,
    }

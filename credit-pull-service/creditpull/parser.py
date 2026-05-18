"""
Credit report parser for IdentityIQ /CreditReport.aspx HTML.

Public API:
    parse_credit_report(html: str) -> dict

The IdentityIQ report is rendered by AngularJS but the parser works
against the final rendered HTML (no Angular template logic). Each
section uses the same 4-column table pattern: a label cell + 3 bureau
columns (TransUnion, Experian, Equifax).
"""
from __future__ import annotations

import re
from typing import Optional

from bs4 import BeautifulSoup, Tag

BUREAUS = ("transunion", "experian", "equifax")


# ── Helpers ───────────────────────────────────────────────────────────
def _clean(text: str) -> Optional[str]:
    """
    Collapse whitespace; convert IIQ's '-' placeholder to None.

    Angular renders BOTH the real value and the '-' fallback span; the
    fallback is hidden via CSS but still present in get_text(). Strip
    trailing/leading '-' tokens introduced by that.
    """
    if text is None:
        return None
    cleaned = re.sub(r"\s+", " ", text).strip()
    # Remove a trailing/leading bare '-' that comes from the Angular fallback
    cleaned = re.sub(r"\s*-\s*$", "", cleaned).strip()
    cleaned = re.sub(r"^\s*-\s+", "", cleaned).strip()
    if cleaned in ("", "-"):
        return None
    return cleaned


def _label_text(cell: Tag) -> str:
    """Extract a label like 'Date of Birth:' → 'Date of Birth'."""
    raw = _clean(cell.get_text()) or ""
    return raw.rstrip(":").strip()


def _parse_three_bureau_table(table: Tag) -> list[dict]:
    """
    Extract rows from a standard `rpt_content_table rpt_table4column`.

    Returns list of {field, transunion, experian, equifax}.
    """
    rows = []
    for tr in table.select("tbody > tr") or table.select("tr"):
        cells = tr.find_all("td", recursive=False)
        if len(cells) < 4:
            continue  # header row (uses <th>) or malformed
        label = _label_text(cells[0])
        if not label:
            continue
        rows.append({
            "field":      label,
            "transunion": _clean(cells[1].get_text(" ", strip=True)),
            "experian":   _clean(cells[2].get_text(" ", strip=True)),
            "equifax":    _clean(cells[3].get_text(" ", strip=True)),
        })
    return rows


def _find_section_wrapper(soup: BeautifulSoup, header_text: str) -> Optional[Tag]:
    """
    Find the `<div class="rpt_content_wrapper">` whose first child header
    contains `header_text` (case-insensitive substring match).
    """
    for wrapper in soup.select("div.rpt_content_wrapper"):
        header = wrapper.find("div", class_="rpt_fullReport_header")
        if header and header_text.lower() in header.get_text(" ", strip=True).lower():
            return wrapper
    return None


def _section_data_table(wrapper: Tag) -> Optional[Tag]:
    """The first .rpt_content_table inside a section that's NOT a help_text table."""
    for tbl in wrapper.find_all("table", recursive=False):
        cls = " ".join(tbl.get("class") or [])
        if "help_text" in cls:
            continue
        if "rpt_content_table" in cls:
            return tbl
    # Some sections nest the table deeper
    for tbl in wrapper.select("table.rpt_content_table"):
        cls = " ".join(tbl.get("class") or [])
        if "help_text" in cls or "addr_hsrty" in cls or "riskfactors" in cls:
            continue
        return tbl
    return None


# ── Top-level header (Reference #, Report Date, Subject Name) ─────────
def _parse_header(soup: BeautifulSoup) -> dict:
    out = {"reference_number": None, "report_date": None, "subject_name": None}

    # The header lives in <div class="re-data"> with h3+p pairs
    re_data = soup.find("div", class_="re-data")
    if re_data:
        for inner in re_data.find_all("div", recursive=False):
            h3 = inner.find("h3")
            p = inner.find("p")
            if not h3 or not p:
                continue
            label = (h3.get_text() or "").lower()
            if "reference" in label:
                out["reference_number"] = _clean(p.get_text())
            elif "report date" in label:
                out["report_date"] = _clean(p.get_text())

    # Subject name comes from Personal Information → "Name" row
    pi_wrapper = _find_section_wrapper(soup, "Personal Information")
    if pi_wrapper:
        table = _section_data_table(pi_wrapper)
        if table:
            for row in _parse_three_bureau_table(table):
                if row["field"].lower() == "name":
                    out["subject_name"] = row["transunion"] or row["experian"] or row["equifax"]
                    break

    return out


# ── Section: Personal Information ─────────────────────────────────────
def _parse_personal_information(soup: BeautifulSoup) -> list[dict]:
    wrapper = _find_section_wrapper(soup, "Personal Information")
    if not wrapper:
        return []
    table = _section_data_table(wrapper)
    if not table:
        return []
    return _parse_three_bureau_table(table)


# ── Section: Credit Score (scores + risk factors) ─────────────────────
def _parse_credit_score(soup: BeautifulSoup) -> dict:
    out = {
        "scores": {b: {"score": None, "lender_rank": None, "score_scale": None} for b in BUREAUS},
        "risk_factors": {b: [] for b in BUREAUS},
    }

    wrapper = soup.select_one("div.rpt_content_wrapper:has(#CreditScore)") \
              or _find_section_wrapper(soup, "Credit Score")
    if not wrapper:
        return out

    # Scores table (first 3-bureau table)
    table = _section_data_table(wrapper)
    if table:
        for row in _parse_three_bureau_table(table):
            field = row["field"].lower().replace(" ", "_")
            if field in ("credit_score", "lender_rank", "score_scale"):
                key = "score" if field == "credit_score" else field
                out["scores"]["transunion"][key] = row["transunion"]
                out["scores"]["experian"][key]   = row["experian"]
                out["scores"]["equifax"][key]    = row["equifax"]

        # Coerce score to int if possible
        for b in BUREAUS:
            v = out["scores"][b]["score"]
            if v and v.isdigit():
                out["scores"][b]["score"] = int(v)

    # Risk factors table (separate `.riskfactors` table inside the wrapper)
    rf_table = wrapper.select_one("table.riskfactors")
    if rf_table:
        bureau_class_map = {
            "tuc_header": "transunion",
            "exp_header": "experian",
            "eqf_header": "equifax",
        }
        for tr in rf_table.find_all("tr"):
            label_td = tr.find("td", class_=re.compile(r"(tuc|exp|eqf)_header"))
            if not label_td:
                continue
            cls = " ".join(label_td.get("class") or [])
            bureau = next((v for k, v in bureau_class_map.items() if k in cls), None)
            if not bureau:
                continue
            info_td = label_td.find_next_sibling("td", class_="info")
            if not info_td:
                continue
            # Each factor is a <b> inside a div.descriptionTxt
            seen = set()
            factors = []
            for b in info_td.find_all("b"):
                txt = _clean(b.get_text())
                if txt and txt not in seen:
                    factors.append(txt)
                    seen.add(txt)
            out["risk_factors"][bureau] = factors

    return out


# ── Section: Summary ──────────────────────────────────────────────────
def _parse_summary(soup: BeautifulSoup) -> list[dict]:
    wrapper = soup.select_one("div.rpt_content_wrapper:has(#Summary)") \
              or _find_section_wrapper(soup, "Summary")
    if not wrapper:
        return []
    table = _section_data_table(wrapper)
    if not table:
        return []
    return _parse_three_bureau_table(table)


# ── Section: Account History (multiple accounts, each with payment grid)
def _parse_payment_history(history_table: Tag) -> dict:
    """
    Parse a `table.addr_hsrty` payment history grid.

    Returns: { 'transunion': {'YYYY-MM': 'OK', ...}, 'experian': {...}, 'equifax': {...} }
    """
    out = {b: {} for b in BUREAUS}
    if not history_table:
        return out

    rows = history_table.find_all("tr", recursive=False) or history_table.find_all("tr")
    if len(rows) < 3:
        return out

    months: list[str] = []
    years: list[str] = []
    bureau_rows: dict[str, list[Optional[str]]] = {}

    for tr in rows:
        cells = tr.find_all("td", recursive=False)
        if not cells:
            continue
        first_text = _clean(cells[0].get_text()) or ""
        first_lower = first_text.lower()

        # Months row
        if first_lower == "month":
            months = []
            for td in cells[1:]:
                # Use the long-view span if present, else cell text
                span = td.find("span", class_="lg-view")
                months.append(_clean((span or td).get_text()) or "")
            continue
        # Year row
        if first_lower == "year":
            years = [_clean(td.get_text()) or "" for td in cells[1:]]
            continue
        # Bureau status rows (TransUnion / Experian / Equifax)
        for bureau_name in ("TransUnion", "Experian", "Equifax"):
            if first_lower == bureau_name.lower():
                bureau_rows[bureau_name.lower()] = [
                    _clean(td.get_text()) for td in cells[1:]
                ]
                break

    # Combine months + years into YYYY-MM keys
    if not months or not years:
        return out

    month_map = {
        "Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
        "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
        "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12",
    }

    keys: list[Optional[str]] = []
    for m, y in zip(months, years):
        m_short = m.strip()[:3].title()
        mm = month_map.get(m_short)
        yy = y.strip()
        if mm and yy:
            full_year = f"20{yy}" if len(yy) == 2 else yy
            keys.append(f"{full_year}-{mm}")
        else:
            keys.append(None)

    # 'transunion' uses key 'transunion' in our output
    name_map = {"transunion": "transunion", "experian": "experian", "equifax": "equifax"}
    for bureau_name, statuses in bureau_rows.items():
        bureau_key = name_map[bureau_name]
        for k, status in zip(keys, statuses):
            if k and status:
                out[bureau_key][k] = status

    return out


def _parse_account_history(soup: BeautifulSoup) -> list[dict]:
    wrapper = soup.select_one("div.rpt_content_wrapper:has(#AccountHistory)") \
              or _find_section_wrapper(soup, "Account History")
    if not wrapper:
        return []

    accounts = []
    # Each account is wrapped in a `<table class="crPrint">`
    for crprint in wrapper.select("table.crPrint"):
        # Creditor name
        sub_header = crprint.select_one("div.sub_header")
        creditor = _clean(sub_header.get_text()) if sub_header else None
        if not creditor:
            continue

        # Fields table — the rpt_content_table that's NOT addr_hsrty
        fields_table = None
        for tbl in crprint.select("table.rpt_content_table"):
            cls = " ".join(tbl.get("class") or [])
            if "addr_hsrty" not in cls:
                fields_table = tbl
                break

        fields = _parse_three_bureau_table(fields_table) if fields_table else []

        # Payment history grid
        history_table = crprint.select_one("table.addr_hsrty")
        payment_history = _parse_payment_history(history_table) if history_table else {b: {} for b in BUREAUS}

        accounts.append({
            "creditor_name":   creditor,
            "fields":          fields,
            "payment_history": payment_history,
        })

    return accounts


# ── Public entry point ────────────────────────────────────────────────
def parse_credit_report(html: str) -> dict:
    """
    Parse a full credit report HTML page into structured data.

    Returns a dict with keys:
        header: {reference_number, report_date, subject_name}
        personal_information: [{field, transunion, experian, equifax}, ...]
        credit_score: {scores: {bureau: {score, lender_rank, score_scale}}, risk_factors: {bureau: [str]}}
        summary: [{field, transunion, experian, equifax}, ...]
        accounts: [{creditor_name, fields: [...], payment_history: {bureau: {YYYY-MM: status}}}, ...]
    """
    soup = BeautifulSoup(html, "lxml")
    return {
        "header":               _parse_header(soup),
        "personal_information": _parse_personal_information(soup),
        "credit_score":         _parse_credit_score(soup),
        "summary":              _parse_summary(soup),
        "accounts":             _parse_account_history(soup),
    }

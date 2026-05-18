"""
Credit Pull API blueprint.

Endpoints:
  POST   /api/creditpull/subjects                — create a new subject (applicant)
  GET    /api/creditpull/subjects                — list subjects
  GET    /api/creditpull/subjects/<id>           — single subject
  POST   /api/creditpull/run                     — start a credit pull (stub for now; scraper wired in Task 11)
  GET    /api/creditpull/status/<report_id>      — poll job status / pending question
  POST   /api/creditpull/answer/<question_id>    — submit security question answer
  GET    /api/creditpull/report/<report_id>      — read full parsed report
  GET    /api/creditpull/reports                 — list reports (optionally filter by subject)

Mirrors the leadgen blueprint pattern:
  - Lazy Supabase client init (`_get_sb`) to avoid startup crash
  - Auth via `g.user_email`
  - Background-thread job tracking (`_pull_jobs`)
"""
import os
import logging
import threading
from datetime import datetime

from flask import Blueprint, request, jsonify, g
from supabase import create_client, Client

from . import _logging as _cp_logging
_cp_logging.install()   # scrub credentials from logs (defense-in-depth)

log = logging.getLogger(__name__)

bp = Blueprint("creditpull", __name__, url_prefix="/api/creditpull")

SUPABASE_URL          = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE = os.environ.get("SUPABASE_SERVICE_ROLE")

_sb: Client | None = None


def _get_sb() -> Client:
    """Lazy-init Supabase client (avoids startup crash if env vars missing)."""
    global _sb
    if _sb is None:
        if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE:
            raise RuntimeError(
                "SUPABASE_URL and SUPABASE_SERVICE_ROLE must be set in env "
                "before calling creditpull endpoints."
            )
        _sb = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE)
    return _sb


# Active background scrape jobs: {report_id: threading.Thread}
_pull_jobs: dict[str, threading.Thread] = {}


# ─── Helpers ─────────────────────────────────────────────────────────
def _user_email() -> str:
    return getattr(g, "user_email", "") or ""


def _error(msg: str, status: int = 400):
    return jsonify({"error": msg}), status


# ─── Subjects ────────────────────────────────────────────────────────
@bp.route("/subjects", methods=["POST"])
def create_subject():
    """
    Create a new applicant.
    Body: { full_name, date_of_birth?, ssn_last4?, email?, phone?, notes? }
    """
    data = request.get_json(silent=True) or {}
    full_name = (data.get("full_name") or "").strip()
    if not full_name:
        return _error("full_name is required")

    payload = {
        "full_name":     full_name,
        "date_of_birth": data.get("date_of_birth") or None,
        "ssn_last4":     data.get("ssn_last4") or None,
        "email":         data.get("email") or None,
        "phone":         data.get("phone") or None,
        "notes":         data.get("notes") or None,
    }
    try:
        result = _get_sb().table("credit_subjects").insert(payload).execute()
    except Exception as e:
        log.exception("create_subject failed")
        return _error(f"db error: {e}", status=500)
    return jsonify(result.data[0]), 201


@bp.route("/subjects", methods=["GET"])
def list_subjects():
    """List all subjects, newest first."""
    try:
        result = (
            _get_sb().table("credit_subjects")
            .select("id, full_name, date_of_birth, ssn_last4, email, phone, created_at")
            .order("created_at", desc=True)
            .execute()
        )
    except Exception as e:
        log.exception("list_subjects failed")
        return _error(f"db error: {e}", status=500)
    return jsonify({"subjects": result.data})


@bp.route("/subjects/<subject_id>", methods=["GET"])
def get_subject(subject_id):
    try:
        result = (
            _get_sb().table("credit_subjects")
            .select("*")
            .eq("id", subject_id)
            .single()
            .execute()
        )
    except Exception:
        return _error("subject not found", status=404)
    return jsonify(result.data)


# ─── Reports ─────────────────────────────────────────────────────────
@bp.route("/run", methods=["POST"])
def run_pull():
    """
    Start a credit pull for a subject.
    Body: {
        subject_id:    "uuid",
        iiq_username:  "...",
        iiq_password:  "..."   (in-memory only; never persisted)
    }
    Returns: { report_id, status }
    Status flow: pending → running → [awaiting_input ↔ running] → done | failed
    """
    data = request.get_json(silent=True) or {}
    subject_id   = data.get("subject_id")
    iiq_username = data.get("iiq_username")
    iiq_password = data.get("iiq_password")

    if not (subject_id and iiq_username and iiq_password):
        return _error("subject_id, iiq_username, and iiq_password are required")

    sb = _get_sb()

    # Verify subject exists
    try:
        sb.table("credit_subjects").select("id").eq("id", subject_id).single().execute()
    except Exception:
        return _error("subject not found", status=404)

    # Create report row in 'pending'
    try:
        result = sb.table("credit_reports").insert({
            "subject_id": subject_id,
            "status":     "pending",
        }).execute()
    except Exception as e:
        log.exception("run_pull failed creating report row")
        return _error(f"db error: {e}", status=500)

    report_id = result.data[0]["id"]

    # ── Stash creds in the in-memory store (never touches DB / disk) ──
    from . import credentials_store
    from .scraper import run_credit_pull
    credentials_store.set_credentials(report_id, iiq_username, iiq_password)

    # Background scraper reads creds from the store, retries on bad-creds
    # via the awaiting_credentials state machine.
    def _job():
        try:
            run_credit_pull(_get_sb(), report_id)
        except Exception:
            log.exception(f"creditpull background job crashed for report {report_id}")

    thread = threading.Thread(target=_job, name=f"creditpull-{report_id}", daemon=True)
    _pull_jobs[report_id] = thread
    thread.start()

    log.info(f"creditpull.run: spawned scraper thread for report_id={report_id}")
    return jsonify({"report_id": report_id, "status": "pending"}), 202


@bp.route("/status/<report_id>", methods=["GET"])
def get_status(report_id):
    """
    Poll for the current state of a credit pull.
    Returns:
      { status: 'pending'|'running'|'awaiting_input'|'done'|'failed',
        question?: { id, text, type, options },   # only when awaiting_input
        error_message?: str                        # only when failed
      }
    """
    sb = _get_sb()
    try:
        result = (
            sb.table("credit_reports")
            .select("id, status, error_message")
            .eq("id", report_id)
            .single()
            .execute()
        )
    except Exception:
        return _error("report not found", status=404)

    out = {"status": result.data["status"]}

    # Always expose error_message when set (e.g. for awaiting_credentials retries)
    if result.data.get("error_message"):
        out["error_message"] = result.data["error_message"]

    if out["status"] == "awaiting_input":
        q = (
            sb.table("credit_report_questions")
            .select("id, question_text, question_type, options, attempt_count")
            .eq("report_id", report_id)
            .is_("answered_at", "null")
            .order("position", desc=True)
            .limit(1)
            .execute()
        )
        if q.data:
            out["question"] = {
                "id":            q.data[0]["id"],
                "text":          q.data[0]["question_text"],
                "type":          q.data[0]["question_type"],
                "options":       q.data[0]["options"],
                "attempt_count": q.data[0].get("attempt_count") or 1,
            }

    return jsonify(out)


@bp.route("/credentials/<report_id>", methods=["POST"])
def update_credentials(report_id):
    """
    Submit new IIQ credentials when the scraper is in 'awaiting_credentials'.
    Body: { iiq_username, iiq_password }

    Creds go into the in-memory credentials_store; the running scraper thread
    polls for a new version and resumes login.
    """
    data = request.get_json(silent=True) or {}
    username = (data.get("iiq_username") or "").strip()
    password = data.get("iiq_password") or ""
    if not username or not password:
        return _error("iiq_username and iiq_password are required")

    sb = _get_sb()
    try:
        result = (
            sb.table("credit_reports")
            .select("id, status")
            .eq("id", report_id)
            .single()
            .execute()
        )
    except Exception:
        return _error("report not found", status=404)

    if result.data["status"] != "awaiting_credentials":
        return _error(
            f"report is not awaiting credentials (current status: {result.data['status']})",
            status=409,
        )

    from . import credentials_store
    credentials_store.set_credentials(report_id, username, password)
    log.info(f"creditpull.credentials: new creds provided for report_id={report_id}")
    return jsonify({"ok": True})


@bp.route("/answer/<question_id>", methods=["POST"])
def submit_answer(question_id):
    """
    Submit the user's answer to a security question.
    Body: { answer: "..." }
    The scraper thread polls credit_report_questions for this row's
    `answer` field and resumes when it appears.
    """
    data = request.get_json(silent=True) or {}
    answer = (data.get("answer") or "").strip()
    if not answer:
        return _error("answer is required")

    sb = _get_sb()
    try:
        result = (
            sb.table("credit_report_questions")
            .update({"answer": answer, "answered_at": datetime.utcnow().isoformat()})
            .eq("id", question_id)
            .execute()
        )
    except Exception as e:
        log.exception("submit_answer failed")
        return _error(f"db error: {e}", status=500)

    if not result.data:
        return _error("question not found", status=404)

    return jsonify({"ok": True})


@bp.route("/report/<report_id>", methods=["GET"])
def get_report(report_id):
    """Return the full parsed credit report for display."""
    from .persistence import load_full_report
    report = load_full_report(_get_sb(), report_id)
    if report is None:
        return _error("report not found", status=404)
    return jsonify(report)


@bp.route("/reports", methods=["GET"])
def list_reports():
    """
    List reports, newest first.
    Optional ?subject_id=... to filter.
    """
    subject_id = request.args.get("subject_id")
    sb = _get_sb()
    q = (
        sb.table("credit_reports")
        .select("id, subject_id, reference_number, report_date, pulled_at, status")
        .order("pulled_at", desc=True)
    )
    if subject_id:
        q = q.eq("subject_id", subject_id)
    try:
        result = q.execute()
    except Exception as e:
        log.exception("list_reports failed")
        return _error(f"db error: {e}", status=500)
    return jsonify({"reports": result.data})

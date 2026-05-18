"""
In-memory credentials store for the scraper thread.

Keeps `{report_id: {username, password, version}}` in a process-local dict,
guarded by a lock. Never written to disk, never sent to Supabase.

The version counter increments on every write, letting the scraper distinguish
"the creds I already tried" from "fresh creds the user just submitted" — so
re-submitting the same wrong values still triggers a retry, and the scraper
won't burn through attempts polling stale values.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

_lock = threading.Lock()
_creds: dict[str, dict] = {}


def set_credentials(report_id: str, username: str, password: str) -> int:
    """Write/overwrite credentials. Returns the new version number."""
    with _lock:
        entry = _creds.get(report_id, {"version": 0})
        entry["username"] = username
        entry["password"] = password
        entry["version"] = entry.get("version", 0) + 1
        _creds[report_id] = entry
        return entry["version"]


def get_credentials(report_id: str) -> Optional[dict]:
    """Return a copy of {username, password, version} or None."""
    with _lock:
        entry = _creds.get(report_id)
        if entry is None:
            return None
        return {
            "username": entry["username"],
            "password": entry["password"],
            "version":  entry["version"],
        }


def wait_for_new_credentials(
    report_id: str,
    since_version: int,
    timeout_s: float = 1200,        # 20 min — matches security-Q answer timeout
    poll_interval_s: float = 1.5,
) -> Optional[dict]:
    """
    Poll until credentials.version > since_version, or timeout.
    Returns the new entry, or None on timeout.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        entry = get_credentials(report_id)
        if entry and entry["version"] > since_version:
            return entry
        time.sleep(poll_interval_s)
    return None


def clear_credentials(report_id: str) -> None:
    """Remove credentials from memory (call when scrape finishes)."""
    with _lock:
        _creds.pop(report_id, None)

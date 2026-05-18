"""
X-API-Key authentication for the Credit Pull microservice.

Per-consumer keys configured via env var:
    CREDIT_PULL_API_KEYS=key1:CRM,key2:LoanApplications,key3:Website

Each request to /api/creditpull/* must include:
    X-API-Key: <one of the configured keys>

On success, g.consumer_label is set to the matching label (used for logs).
On failure, returns 401.

Public paths (no auth required):
    GET /healthz   — Railway health check
    GET /          — service info
    /static/*      — UI assets
    /ui            — UI entry point
    /ui/*
"""
from __future__ import annotations

import logging
import os
from flask import request, g, jsonify

log = logging.getLogger(__name__)

PUBLIC_PREFIXES = ("/healthz", "/static/", "/ui")
PUBLIC_EXACT = {"/", "/favicon.ico"}


def _parse_api_keys() -> dict[str, str]:
    """Parse env var into {key: label} dict."""
    raw = os.environ.get("CREDIT_PULL_API_KEYS", "")
    keys: dict[str, str] = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" in entry:
            key, label = entry.split(":", 1)
            keys[key.strip()] = label.strip()
        else:
            keys[entry] = "unknown"
    return keys


# Parsed once at module load (env vars don't change without restart anyway)
_API_KEYS = _parse_api_keys()


def require_api_key():
    """Flask before_request hook. Validates X-API-Key for protected paths."""
    path = request.path or "/"

    # Allow public paths
    if path in PUBLIC_EXACT:
        return
    for prefix in PUBLIC_PREFIXES:
        if path.startswith(prefix):
            return

    # Only protect API paths beyond this point
    if not path.startswith("/api/"):
        return

    api_key = (request.headers.get("X-API-Key") or "").strip()
    if not api_key:
        log.warning(f"missing X-API-Key on {path}")
        return jsonify({"error": "missing X-API-Key header"}), 401

    label = _API_KEYS.get(api_key)
    if not label:
        log.warning(f"invalid X-API-Key on {path}")
        return jsonify({"error": "invalid X-API-Key"}), 401

    g.consumer_label = label
    log.info(f"authenticated request: consumer={label} path={path}")

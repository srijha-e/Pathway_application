"""
Credit Pull Microservice — standalone Flask app.

Hosts:
  /api/creditpull/*    — REST endpoints (X-API-Key protected)
  /ui                  — standalone web UI (consumed by CRM, Loan Apps, Website)
  /static/*            — UI assets
  /healthz             — health check (Railway requires)

Run locally:
    cp .env.example .env  # then edit
    pip install -r requirements.txt
    playwright install chromium
    python app.py
"""
from __future__ import annotations

import logging
import os

from flask import Flask, send_from_directory, jsonify
from flask_cors import CORS

from creditpull.creditpull_api import bp as creditpull_bp
from creditpull.auth import require_api_key

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("credit-pull-service")


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", static_url_path="/static")

    # CORS allow-list from env var (comma-separated origins)
    raw_origins = os.environ.get("CORS_ALLOWED_ORIGINS", "")
    cors_origins = [o.strip() for o in raw_origins.split(",") if o.strip()]
    if cors_origins:
        CORS(
            app,
            supports_credentials=True,
            resources={r"/api/*": {"origins": cors_origins}},
        )
        log.info(f"CORS allowed origins: {cors_origins}")
    else:
        log.warning(
            "CORS_ALLOWED_ORIGINS not set — cross-origin browser requests will be blocked"
        )

    # API-key auth runs before every request (skips public paths internally)
    app.before_request(require_api_key)

    # Register the credit-pull endpoints
    app.register_blueprint(creditpull_bp)

    # Health check (Railway hits this)
    @app.get("/healthz")
    def healthz():
        return {"ok": True, "service": "credit-pull"}

    # Service info at root
    @app.get("/")
    def root():
        return {
            "service": "credit-pull",
            "version": "1.0",
            "ui_at": "/ui",
            "api_root": "/api/creditpull",
            "health": "/healthz",
        }

    # ── Standalone UI ────────────────────────────────────────────────
    # Serve index.html at /ui (or /ui/) so consumers can deep-link there
    # with their API key in the URL: /ui?key=<consumer-key>
    @app.get("/ui")
    @app.get("/ui/")
    def ui_root():
        return send_from_directory(app.static_folder, "index.html")

    return app


app = create_app()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=os.environ.get("FLASK_DEBUG") == "1")

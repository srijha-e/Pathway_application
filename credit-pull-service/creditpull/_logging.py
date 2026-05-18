"""
Credential-scrubbing logging filter.

Defense-in-depth: scans every log record for known-sensitive keys
(iiq_password, iiq_username, password, answer) and replaces the
associated value with '***REDACTED***' before it reaches any handler.

This is NOT a substitute for "don't log credentials in the first place"
— our own code already avoids it. The filter catches accidental leaks
from:
  - exception tracebacks that include local variables in the message
  - third-party library DEBUG output (e.g. playwright fill() values)
  - future regressions where someone logs request.json() bodies

Install once at app startup by calling `install()`.
"""
from __future__ import annotations

import logging
import re

REDACTED = "***REDACTED***"

# Keys we always redact. Case-insensitive on the key, value can be anything
# inside paired quotes (json/dict) or following = (kwargs).
_KEYS = r"(iiq_password|iiq_username|password|answer)"

# JSON / dict-with-double-quotes:  "key": "value"
_JSON_PAT = re.compile(rf'"{_KEYS}"\s*:\s*"([^"]*)"', re.IGNORECASE)
# Python-dict-repr-with-single-quotes:  'key': 'value'  or  'key': "value"
_DICT_PAT = re.compile(rf"'{_KEYS}'\s*:\s*['\"]([^'\"]*)['\"]", re.IGNORECASE)
# kwargs / assignment:  key='value'  or  key="value"
# (group 1 is the keyword from _KEYS; group 2 is the opening quote; \2 closes it)
_KWARG_PAT = re.compile(rf'\b{_KEYS}\s*=\s*([\'"])([^\'"]*)\2', re.IGNORECASE)


def _scrub(text: str) -> str:
    """Redact the value-side of any sensitive key=value pattern in `text`."""
    if not text or not isinstance(text, str):
        return text

    def _json_sub(m: re.Match) -> str:
        return f'"{m.group(1)}": "{REDACTED}"'

    def _dict_sub(m: re.Match) -> str:
        return f"'{m.group(1)}': '{REDACTED}'"

    def _kwarg_sub(m: re.Match) -> str:
        q = m.group(2)
        return f"{m.group(1)}={q}{REDACTED}{q}"

    text = _JSON_PAT.sub(_json_sub, text)
    text = _DICT_PAT.sub(_dict_sub, text)
    text = _KWARG_PAT.sub(_kwarg_sub, text)
    return text


class CredentialScrubFilter(logging.Filter):
    """Rewrites record.msg AND any exception traceback to remove sensitive values."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # 1. Scrub the formatted message body
            original = record.getMessage()
            scrubbed = _scrub(original)
            if scrubbed != original:
                record.msg = scrubbed
                record.args = None  # already substituted

            # 2. Scrub the exception traceback (if any).
            # Tracebacks are formatted by the Handler's Formatter, not from msg.
            # Pre-format here so we can scrub before it reaches the handler.
            if record.exc_info and not record.exc_text:
                import traceback
                etype, evalue, etb = record.exc_info
                tb_text = "".join(traceback.format_exception(etype, evalue, etb))
                record.exc_text = _scrub(tb_text).rstrip("\n")
            elif record.exc_text:
                record.exc_text = _scrub(record.exc_text)
        except Exception:
            # Filter must never raise — failsafe to let records through
            pass
        return True


def install() -> None:
    """Attach the scrub filter to the root logger and all existing handlers."""
    root = logging.getLogger()
    if not any(isinstance(f, CredentialScrubFilter) for f in root.filters):
        root.addFilter(CredentialScrubFilter())
    for handler in root.handlers:
        if not any(isinstance(f, CredentialScrubFilter) for f in handler.filters):
            handler.addFilter(CredentialScrubFilter())

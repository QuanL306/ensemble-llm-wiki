"""
Structured JSON logging for the Knowledge Base Cloud Platform.

Usage
-----
In gateway or MCP server:

    from log import setup_logging, get_logger

    setup_logging("gateway")          # call once at startup
    log = get_logger(__name__)

    log.info("auth_ok", extra={"req_id": req_id, "user_id": uid})
    log.warning("rate_limit_exceeded", extra={"req_id": req_id, "user_id": uid})
    log.error("proxy_error", extra={"req_id": req_id, "error": str(e)}, exc_info=True)

Each log line is a single JSON object on stdout:

    {"ts":"2026-04-17T10:23:45.123Z","level":"INFO","component":"gateway",
     "event":"auth_ok","req_id":"a3f2b1c4","user_id":"alice"}

Sensitive fields
----------------
API keys are NEVER logged in full.  Use `redact_key(k)` to produce a safe
prefix:  "kb_live_abc..." → "kb_live_ab..."
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Optional

# Fields copied from LogRecord.extra into the JSON output.
_EXTRA_FIELDS = (
    "req_id", "user_id", "kb_id", "tool",
    "duration_ms", "status", "method", "path",
    "error", "key_id",
)

_COMPONENT = "unknown"


class _JsonFormatter(logging.Formatter):
    """Format every log record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        ts = (
            datetime.fromtimestamp(record.created, tz=timezone.utc)
            .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        )
        doc: dict = {
            "ts":        ts,
            "level":     record.levelname,
            "component": _COMPONENT,
            "event":     record.getMessage(),
        }
        for field in _EXTRA_FIELDS:
            val = getattr(record, field, None)
            if val is not None:
                doc[field] = val

        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)

        return json.dumps(doc, ensure_ascii=False, default=str)


def setup_logging(component: str, level: str = "INFO") -> None:
    """
    Configure root logger to emit JSON to stdout.

    Call once at application startup, before any other log calls.
    The `component` string identifies the service in every log line.

    Parameters
    ----------
    component : str
        Short service name, e.g. "gateway" or "mcp_server".
    level : str
        Minimum log level (default "INFO").  Override with LOG_LEVEL env var.
    """
    global _COMPONENT
    _COMPONENT = component

    import os
    effective_level = os.getenv("LOG_LEVEL", level).upper()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, effective_level, logging.INFO))

    # Silence noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger (component is set globally by setup_logging)."""
    return logging.getLogger(name)


def redact_key(api_key: Optional[str]) -> str:
    """Return a safe, non-reversible prefix of an API key for logging."""
    if not api_key:
        return "(none)"
    return api_key[:12] + "..."

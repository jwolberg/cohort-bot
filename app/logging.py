"""Structured JSON logging for Cloud Logging.

Cloud Logging parses each stdout line as JSON and promotes known fields
(``severity``, ``message``). ``get_logger`` returns a stdlib logger whose
records are emitted as single-line JSON; ``log_event`` is a convenience for
attaching structured fields (used by the digest pipeline in U12).
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

# Cloud Logging severity names line up with stdlib level names except WARNING.
_LEVEL_TO_SEVERITY = {
    "DEBUG": "DEBUG",
    "INFO": "INFO",
    "WARNING": "WARNING",
    "ERROR": "ERROR",
    "CRITICAL": "CRITICAL",
}

_RESERVED = frozenset(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    """Render a log record as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "severity": _LEVEL_TO_SEVERITY.get(record.levelname, record.levelname),
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Merge any structured fields passed via `extra=`.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


_configured = False


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    global _configured
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger, configuring root logging on first use."""
    if not _configured:
        configure_logging()
    return logging.getLogger(name)


def log_event(logger: logging.Logger, event: str, /, level: int = logging.INFO, **fields: Any) -> None:
    """Emit a structured event: ``{"message": event, ...fields}``."""
    logger.log(level, event, extra=fields)

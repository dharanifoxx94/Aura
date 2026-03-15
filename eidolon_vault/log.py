"""
Eidolon Vault — Structured Logging
==========================
Configures JSON logging with context injection and redaction.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

# Redaction patterns for common secrets
SECRET_PATTERNS: List[Tuple[str, str]] = [
    ("api[_-]?key", "***"),
    ("secret",       "***"),
    ("bearer",       "***"),
    ("password", "***"),
    ("token", "***"),
    ("authorization", "***"),
]

class RedactingJsonFormatter(logging.Formatter):
    """JSON formatter that redacts sensitive fields."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._patterns = SECRET_PATTERNS

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Redact sensitive keys in any dictionary field
        self._redact_dict(log_entry)

        return json.dumps(log_entry, default=str)

    def _redact_dict(self, obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in list(obj.items()):
                if any(pattern in key.lower() for pattern, _ in self._patterns):
                    obj[key] = "***"
                else:
                    self._redact_dict(value)
        elif isinstance(obj, list):
            for item in obj:
                self._redact_dict(item)


def setup_logging(verbose: bool = False, json_output: bool = False) -> None:
    """
    Configure root logger.

    :param verbose:     If True, set level to DEBUG; otherwise WARNING.
    :param json_output: If True, use JSON formatter; else plain text.
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG if verbose else logging.WARNING)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)

    if json_output:
        handler.setFormatter(RedactingJsonFormatter())
    else:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)

    root_logger.addHandler(handler)

    # Suppress overly verbose third‑party libraries
    logging.getLogger("litellm").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

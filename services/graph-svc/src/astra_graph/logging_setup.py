"""Structured logging.

JSON lines, so Azure Monitor / OpenTelemetry ingestion (spec §19) does not have to parse
prose. Nothing here logs property values: a graph write can carry a custom SQL literal or
a field name that the client classifies as restricted (spec §18.3), so the log records
what was rejected and why, never the value that was rejected.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "context", {}).items():
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    # asyncpg logs the query text on some errors; a query can carry property values.
    logging.getLogger("asyncpg").setLevel(max(logging.WARNING, root.level))

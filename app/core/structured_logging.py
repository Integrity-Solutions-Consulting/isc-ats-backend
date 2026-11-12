"""Structured container logs for application and worker events."""

import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname, "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("task_id", "action", "entity_id", "attempt", "status", "error_code"):
            value = getattr(record, key, None)
            if value is not None:
                data[key] = value
        if record.exc_info:
            data["exception_type"] = record.exc_info[0].__name__
        return json.dumps(data, ensure_ascii=False, default=str)


def configure_app_logging() -> None:
    logger = logging.getLogger("app")
    if not any(getattr(handler, "_isc_json", False) for handler in logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        handler._isc_json = True
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

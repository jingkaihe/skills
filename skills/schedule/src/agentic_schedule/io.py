from __future__ import annotations

import json
import logging
from typing import Any

import structlog

_LOGGING_CONFIGURED = False


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def emit_error(message: str) -> int:
    emit_json({"error": message})
    return 1


def configure_json_logging() -> None:
    global _LOGGING_CONFIGURED
    if _LOGGING_CONFIGURED:
        return
    logging.basicConfig(format="%(message)s", level=logging.INFO)
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso", utc=True, key="timestamp"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
    _LOGGING_CONFIGURED = True


def logger() -> Any:
    configure_json_logging()
    return structlog.get_logger("agentic_schedule")


def load_payload(raw_input: str) -> dict[str, Any]:
    if not raw_input.strip():
        return {}
    payload = json.loads(raw_input)
    if not isinstance(payload, dict):
        raise ValueError("input must be a JSON object")
    return payload


def optional_bool(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def required_string(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} is required and must be a non-empty string")
    return value.strip()


def optional_string(
    payload: dict[str, Any], key: str, default: str | None = None
) -> str | None:
    value = payload.get(key, default)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    value = value.strip()
    return value if value else default


def reject_unknown_keys(payload: dict[str, Any], allowed_keys: set[str]) -> None:
    unknown_keys = sorted(set(payload) - allowed_keys)
    if unknown_keys:
        raise ValueError(f"unsupported parameter(s): {', '.join(unknown_keys)}")

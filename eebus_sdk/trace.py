"""Structured JSONL trace logging for live sessions and fixtures."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SENSITIVE_KEYS = {
    "key_path",
    "private_key",
    "privateKey",
    "private_key_pem",
    "certificate_pem",
    "client_key",
}


def _sanitize(value: Any, key: str | None = None) -> Any:
    if key in SENSITIVE_KEYS:
        return "<redacted>"
    if isinstance(value, str) and "PRIVATE KEY" in value:
        return "<redacted>"
    if isinstance(value, dict):
        return {child_key: _sanitize(child_value, child_key) for child_key, child_value in value.items()}
    if isinstance(value, list):
        return [_sanitize(child) for child in value]
    return value


@dataclass(slots=True)
class TraceLogger:
    path: Path | None = None

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path) if path is not None else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: str, **data: Any) -> None:
        if self.path is None:
            return
        payload = {"ts": time.time(), "event": event, **_sanitize(data)}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True, separators=(",", ":")) + "\n")

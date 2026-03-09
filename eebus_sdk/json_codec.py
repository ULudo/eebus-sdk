"""Helpers for the EEBUS-specific JSON array encoding."""

from __future__ import annotations

import json
from typing import Any


def _encode_node(value: Any) -> Any:
    if isinstance(value, dict):
        return [{key: _encode_node(child)} for key, child in value.items()]
    if isinstance(value, list):
        return [_encode_node(child) for child in value]
    return value


def to_eebus_json_bytes(payload: dict[str, Any]) -> bytes:
    """Encode a normal JSON object into the EEBUS JSON array form."""
    if not isinstance(payload, dict):
        raise TypeError("EEBUS JSON payload must be a dict at the top level")
    encoded = {key: _encode_node(value) for key, value in payload.items()}
    return json.dumps(encoded, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def _decode_node(value: Any) -> Any:
    if isinstance(value, list):
        if not value:
            return []
        if all(isinstance(item, dict) and len(item) == 1 for item in value):
            result: dict[str, Any] = {}
            for item in value:
                key, child = next(iter(item.items()))
                result[key] = _decode_node(child)
            return result
        return [_decode_node(child) for child in value]
    if isinstance(value, dict):
        return {key: _decode_node(child) for key, child in value.items()}
    return value


def from_eebus_json_bytes(payload: bytes) -> dict[str, Any]:
    """Decode the EEBUS JSON array form into a normal JSON object."""
    cleaned = payload.rstrip(b"\x00")
    decoded = json.loads(cleaned.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("EEBUS JSON payload did not decode to a dict")
    return {key: _decode_node(value) for key, value in decoded.items()}

"""Replay helpers for recorded JSONL traces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .exceptions import ReplayError
from .spine import SpineDatagram, extract_discovery_payloads


def load_trace(path: str | Path) -> list[dict[str, Any]]:
    trace_path = Path(path)
    try:
        lines = trace_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError as exc:
        raise ReplayError(f"trace file not found: {trace_path}") from exc

    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ReplayError(f"{trace_path}:{line_number} is not valid JSON") from exc
        if not isinstance(event, dict):
            raise ReplayError(f"{trace_path}:{line_number} is not a JSON object")
        events.append(event)
    return events


def summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    summary = {
        "events": len(events),
        "tls_connected": False,
        "handshake_complete": False,
        "pairing_rejected": False,
        "remote_ship_id": None,
        "peer_ski": None,
        "spine_datagrams": 0,
        "discovery_payloads": 0,
        "close_code": None,
    }

    for event in events:
        name = event.get("event")
        if name == "tls_connected":
            summary["tls_connected"] = True
            summary["peer_ski"] = event.get("peer_ski")
        elif name == "ship_handshake_complete":
            summary["handshake_complete"] = True
            summary["remote_ship_id"] = event.get("remote_ship_id")
        elif name == "rx_control":
            payload = event.get("payload", {})
            access_methods = payload.get("accessMethods")
            if isinstance(access_methods, dict) and access_methods.get("id"):
                summary["remote_ship_id"] = access_methods["id"]
        elif name == "rx_data":
            summary["spine_datagrams"] += 1
            payload = event.get("payload")
            if isinstance(payload, dict):
                try:
                    datagram = SpineDatagram.from_ship_payload(payload)
                except ValueError:
                    continue
                summary["discovery_payloads"] += len(extract_discovery_payloads(datagram))
        elif name == "rx_close":
            summary["close_code"] = event.get("code")
            if event.get("code") == 4452:
                summary["pairing_rejected"] = True

    if summary["remote_ship_id"]:
        summary["handshake_complete"] = True
    return summary

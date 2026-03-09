"""Helpers for SPINE datagrams carried by SHIP."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

PROTOCOL_ID = "ee1.0"


@dataclass(slots=True)
class SpineDatagram:
    payload: dict[str, Any]
    header: dict[str, Any] = field(default_factory=lambda: {"protocolId": PROTOCOL_ID})

    def as_ship_payload(self) -> dict[str, Any]:
        return {
            "data": {
                "header": self.header,
                "payload": self.payload,
            }
        }

    @classmethod
    def from_ship_payload(cls, payload: dict[str, Any]) -> "SpineDatagram":
        data = payload.get("data")
        if not isinstance(data, dict):
            raise ValueError("SHIP data payload does not contain a 'data' object")
        header = data.get("header", {})
        body = data.get("payload")
        if not isinstance(body, dict):
            raise ValueError("SHIP data payload does not contain a SPINE payload dict")
        return cls(payload=body, header=header)


def _find_by_key(value: Any, key: str) -> Iterable[Any]:
    if isinstance(value, dict):
        for child_key, child_value in value.items():
            if child_key == key:
                yield child_value
            yield from _find_by_key(child_value, key)
    elif isinstance(value, list):
        for child in value:
            yield from _find_by_key(child, key)


def extract_discovery_payloads(datagram: SpineDatagram | dict[str, Any]) -> list[Any]:
    payload = datagram.payload if isinstance(datagram, SpineDatagram) else datagram
    results = list(_find_by_key(payload, "nodeManagementDetailedDiscoveryData"))
    results.extend(_find_by_key(payload, "nodeManagementUseCaseData"))
    return results


def is_measurement_datagram(datagram: SpineDatagram | dict[str, Any]) -> bool:
    payload = datagram.payload if isinstance(datagram, SpineDatagram) else datagram
    measurement_markers = (
        "measurement",
        "power",
        "energy",
        "temperature",
        "voltage",
        "current",
    )

    def visit(value: Any) -> bool:
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                key = child_key.lower()
                if any(marker in key for marker in measurement_markers):
                    return True
                if visit(child_value):
                    return True
        elif isinstance(value, list):
            for child in value:
                if visit(child):
                    return True
        return False

    return visit(payload)

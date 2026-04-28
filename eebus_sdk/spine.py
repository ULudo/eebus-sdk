"""Helpers for SPINE datagrams carried by SHIP."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

PROTOCOL_ID = "ee1.0"
SPECIFICATION_VERSION = "1.3.0"


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


def extract_inner_datagram(datagram: SpineDatagram | dict[str, Any]) -> dict[str, Any]:
    payload = datagram.payload if isinstance(datagram, SpineDatagram) else datagram
    inner = payload.get("datagram")
    if not isinstance(inner, dict):
        raise ValueError("SPINE payload does not contain a 'datagram' object")
    return inner


def extract_header(datagram: SpineDatagram | dict[str, Any]) -> dict[str, Any]:
    inner = extract_inner_datagram(datagram)
    header = inner.get("header")
    if not isinstance(header, dict):
        raise ValueError("SPINE datagram does not contain a header object")
    return header


def extract_commands(datagram: SpineDatagram | dict[str, Any]) -> list[dict[str, Any]]:
    inner = extract_inner_datagram(datagram)
    payload = inner.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("SPINE datagram does not contain a payload object")
    commands = payload.get("cmd", [])
    if not isinstance(commands, list):
        raise ValueError("SPINE datagram cmd payload is not a list")
    return [command for command in commands if isinstance(command, dict)]


def build_datagram(
    *,
    source: dict[str, Any],
    destination: dict[str, Any],
    cmd_classifier: str,
    msg_counter: int,
    commands: list[dict[str, Any]],
    specification_version: str = SPECIFICATION_VERSION,
    msg_counter_reference: int | None = None,
    ack_request: bool | None = None,
) -> SpineDatagram:
    header: dict[str, Any] = {
        "specificationVersion": specification_version,
        "addressSource": source,
        "addressDestination": destination,
        "msgCounter": msg_counter,
    }
    if msg_counter_reference is not None:
        header["msgCounterReference"] = msg_counter_reference
    header["cmdClassifier"] = cmd_classifier
    if ack_request is not None:
        header["ackRequest"] = ack_request
    return SpineDatagram(payload={"datagram": {"header": header, "payload": {"cmd": commands}}})


def build_reply_datagram(
    request: SpineDatagram | dict[str, Any],
    *,
    source: dict[str, Any],
    msg_counter: int,
    commands: list[dict[str, Any]],
) -> SpineDatagram:
    header = extract_header(request)
    return build_datagram(
        source=source,
        destination=header.get("addressSource", {}),
        cmd_classifier="reply",
        msg_counter=msg_counter,
        commands=commands,
        specification_version=header.get("specificationVersion", SPECIFICATION_VERSION),
        msg_counter_reference=header.get("msgCounter"),
    )


def build_result_datagram(
    request: SpineDatagram | dict[str, Any],
    *,
    source: dict[str, Any],
    msg_counter: int,
    error_number: int = 0,
    description: str | None = None,
) -> SpineDatagram:
    header = extract_header(request)
    result_data: dict[str, Any] = {"errorNumber": error_number}
    if description:
        result_data["description"] = description
    return build_datagram(
        source=source,
        destination=header.get("addressSource", {}),
        cmd_classifier="result",
        msg_counter=msg_counter,
        commands=[{"resultData": result_data}],
        specification_version=header.get("specificationVersion", SPECIFICATION_VERSION),
        msg_counter_reference=header.get("msgCounter"),
    )


def build_read_datagram(
    *,
    source: dict[str, Any],
    destination: dict[str, Any],
    msg_counter: int,
    function_name: str,
    selectors: Any = None,
    specification_version: str = SPECIFICATION_VERSION,
    ack_request: bool | None = True,
) -> SpineDatagram:
    command = {function_name: [] if selectors is None else selectors}
    return build_datagram(
        source=source,
        destination=destination,
        cmd_classifier="read",
        msg_counter=msg_counter,
        commands=[command],
        specification_version=specification_version,
        ack_request=ack_request,
    )


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


def extract_measurement_descriptions(datagram: SpineDatagram | dict[str, Any]) -> list[Any]:
    payload = datagram.payload if isinstance(datagram, SpineDatagram) else datagram
    return list(_find_by_key(payload, "measurementDescriptionListData"))


def extract_measurement_payloads(datagram: SpineDatagram | dict[str, Any]) -> list[Any]:
    payload = datagram.payload if isinstance(datagram, SpineDatagram) else datagram
    return list(_find_by_key(payload, "measurementListData"))


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

"""Internal LoadControl helpers for LPC/LPP payloads and state extraction."""

from __future__ import annotations

from typing import Any

from ._spine_helpers import format_duration


def build_limit_payload(
    *,
    watts: int,
    duration_seconds: int | None,
    limit_id: int,
    is_active: bool,
) -> dict[str, Any]:
    limit_data: dict[str, Any] = {
        "limitId": limit_id,
        "isLimitActive": is_active,
    }
    if duration_seconds is not None:
        limit_data["timePeriod"] = {"endTime": format_duration(duration_seconds)}
    limit_data["value"] = {"number": encode_limit_watts(watts, limit_id), "scale": 0}
    return {"loadControlLimitListData": {"loadControlLimitData": [limit_data]}}


def encode_limit_watts(watts: int, limit_id: int | None) -> int:
    """Convert public positive LP watts to the SPINE sign convention."""
    if limit_id == 0:
        return abs(watts)
    if limit_id == 1:
        return -abs(watts)
    return watts


def decode_limit_watts(protocol_watts: int, limit_id: int | None) -> int:
    """Convert a SPINE LoadControl value to public positive LP watts."""
    if limit_id in {0, 1}:
        return abs(protocol_watts)
    return protocol_watts


def limit_direction_for_id(limit_id: int | None) -> str | None:
    if limit_id == 0:
        return "consume"
    if limit_id == 1:
        return "produce"
    return None


def extract_limit_state(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    limit_data = payload.get("loadControlLimitData")
    if not isinstance(limit_data, list):
        return None
    for entry in limit_data:
        if not isinstance(entry, dict):
            continue
        state: dict[str, Any] = {"raw": entry}
        limit_id = entry.get("limitId")
        if isinstance(limit_id, int):
            state["limit_id"] = limit_id
            direction = limit_direction_for_id(limit_id)
            if direction is not None:
                state["direction"] = direction
        is_limit_active = entry.get("isLimitActive")
        if isinstance(is_limit_active, bool):
            state["is_active"] = is_limit_active
        value = entry.get("value")
        if isinstance(value, dict):
            number = value.get("number")
            scale = value.get("scale")
            if isinstance(number, int):
                state["protocol_watts"] = number
                state["watts"] = decode_limit_watts(
                    number,
                    limit_id if isinstance(limit_id, int) else None,
                )
            if isinstance(scale, int):
                state["scale"] = scale
        time_period = entry.get("timePeriod")
        if isinstance(time_period, dict):
            end_time = time_period.get("endTime")
            if isinstance(end_time, str):
                state["duration"] = end_time
        return state
    return None


def extract_limit_states(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    limit_data = payload.get("loadControlLimitData")
    if not isinstance(limit_data, list):
        return []
    states: list[dict[str, Any]] = []
    for entry in limit_data:
        state = extract_limit_state({"loadControlLimitData": [entry]})
        if state is not None:
            states.append(state)
    return states


def extract_preferred_load_power_state(payload: Any) -> dict[str, Any] | None:
    states = extract_limit_states(payload)
    if not states:
        return None
    for state in states:
        if state.get("limit_id") == 0:
            return state
    return states[0]


def extract_preferred_lpc_state(payload: Any) -> dict[str, Any] | None:
    return extract_preferred_load_power_state(payload)

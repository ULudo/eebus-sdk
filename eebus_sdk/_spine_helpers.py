"""Internal SPINE helper functions shared by client and server profiles."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def scaled_number(value: int, scale: int = 0) -> dict[str, int]:
    return {"number": value, "scale": scale}


def format_duration(seconds: int) -> str:
    minutes, remaining_seconds = divmod(seconds, 60)
    hours, remaining_minutes = divmod(minutes, 60)
    parts = ["PT"]
    if hours:
        parts.append(f"{hours}H")
    if remaining_minutes:
        parts.append(f"{remaining_minutes}M")
    if remaining_seconds or len(parts) == 1:
        parts.append(f"{remaining_seconds}S")
    return "".join(parts)


def supported_read(function_name: str) -> dict[str, Any]:
    return {"function": function_name, "possibleOperations": {"read": {}}}


def supported_read_write(function_name: str) -> dict[str, Any]:
    return {"function": function_name, "possibleOperations": {"read": {}, "write": {}}}


def supported_read_partial_write(function_name: str) -> dict[str, Any]:
    return {"function": function_name, "possibleOperations": {"read": {}, "write": {"partial": {}}}}


def supported_partial_read(function_name: str) -> dict[str, Any]:
    return {"function": function_name, "possibleOperations": {"read": {"partial": {}}}}


def supported_partial_read_write(function_name: str) -> dict[str, Any]:
    return {"function": function_name, "possibleOperations": {"read": {"partial": {}}, "write": {"partial": {}}}}


def feature_description(
    *,
    device: str,
    entity: int,
    feature: int,
    feature_type: str,
    role: str,
    supported_function: list[dict[str, Any]] | None = None,
    description: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "featureAddress": {"device": device, "entity": [entity], "feature": feature},
        "featureType": feature_type,
        "role": role,
    }
    if supported_function:
        payload["supportedFunction"] = supported_function
    if description:
        payload["description"] = description
    return {"description": payload}


def entity_description(
    *,
    device: str,
    entity: int,
    entity_type: str,
    description: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "entityAddress": {"device": device, "entity": [entity]},
        "entityType": entity_type,
    }
    if description:
        payload["description"] = description
    return {"description": payload}


def normalize_feature_address(
    address: dict[str, Any],
    *,
    default_device: str | None,
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    device = address.get("device", default_device)
    if device is not None:
        normalized["device"] = device
    if "entity" in address:
        normalized["entity"] = address["entity"]
    if "feature" in address:
        normalized["feature"] = address["feature"]
    for key, value in address.items():
        if key not in normalized:
            normalized[key] = value
    return normalized


def feature_address_tuple(address: dict[str, Any]) -> tuple[str | None, tuple[int, ...], int | None]:
    entity = address.get("entity")
    return (
        address.get("device"),
        tuple(entity) if isinstance(entity, list) else (),
        address.get("feature"),
    )


def feature_address_string(address: dict[str, Any]) -> str:
    device, entity, feature = feature_address_tuple(address)
    return f"{device}|{entity}|{feature}"


def feature_addresses(
    discovery_payload: dict[str, Any],
    *,
    feature_type: str,
    role: str | None = None,
    default_device: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for feature in discovery_payload.get("featureInformation", []):
        if not isinstance(feature, dict):
            continue
        description = feature.get("description")
        if not isinstance(description, dict):
            continue
        if description.get("featureType") != feature_type:
            continue
        if role is not None and description.get("role") != role:
            continue
        address = description.get("featureAddress")
        if not isinstance(address, dict):
            continue
        results.append(normalize_feature_address(address, default_device=default_device))
    return results


def preferred_feature_address(addresses: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not addresses:
        return None

    def sort_key(address: dict[str, Any]) -> tuple[int, int]:
        entity = address.get("entity")
        entity_depth = len(entity) if isinstance(entity, list) else 0
        feature = address.get("feature")
        feature_value = feature if isinstance(feature, int) else -1
        return (entity_depth, feature_value)

    return min(addresses, key=sort_key)


def local_source_for_destination(destination: Any, *, local_device: str, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(destination, dict):
        return fallback
    return normalize_feature_address(
        {
            "entity": destination.get("entity"),
            "feature": destination.get("feature"),
        },
        default_device=local_device,
    )


def merge_keyed_list_payload(
    current: dict[str, Any] | list[Any] | None,
    incoming: Any,
    *,
    list_key: str,
    id_key: str,
) -> Any:
    if not isinstance(incoming, dict):
        return incoming
    incoming_items = incoming.get(list_key)
    if not isinstance(incoming_items, list):
        return incoming
    current_items = current.get(list_key) if isinstance(current, dict) else []
    if not isinstance(current_items, list):
        current_items = []

    merged_by_id: dict[int, dict[str, Any]] = {}
    ordered_ids: list[int] = []
    unkeyed_items: list[Any] = []
    for item in current_items:
        if not isinstance(item, dict):
            unkeyed_items.append(item)
            continue
        item_id = item.get(id_key)
        if isinstance(item_id, int):
            merged_by_id[item_id] = dict(item)
            ordered_ids.append(item_id)
        else:
            unkeyed_items.append(item)

    for item in incoming_items:
        if not isinstance(item, dict):
            unkeyed_items.append(item)
            continue
        item_id = item.get(id_key)
        if not isinstance(item_id, int):
            unkeyed_items.append(item)
            continue
        if item_id not in merged_by_id:
            ordered_ids.append(item_id)
        merged = dict(merged_by_id.get(item_id, {}))
        merged.update(item)
        merged_by_id[item_id] = merged

    return {list_key: [merged_by_id[item_id] for item_id in ordered_ids] + unkeyed_items}

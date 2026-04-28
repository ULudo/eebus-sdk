"""Local SHIP server and mDNS advertisement helpers."""

from __future__ import annotations

import asyncio
import contextlib
import ssl
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from ._load_power import (
    build_limit_payload,
    extract_limit_state,
    extract_limit_states,
    extract_preferred_load_power_state,
    limit_direction_for_id,
)
from ._server_profile import ServerSpineProfile
from ._spine_helpers import (
    feature_address_tuple,
    feature_addresses,
    format_duration,
    local_source_for_destination,
    merge_keyed_list_payload,
    normalize_feature_address,
    preferred_feature_address,
)
from ._websocket_server import ServerWebSocketConnection
from .identity import IdentityMaterial, extract_ski_from_peer_cert, normalize_ski
from .json_codec import from_eebus_json_bytes, to_eebus_json_bytes
from .ship import SHIP_MSG_CONTROL, SHIP_MSG_DATA, SHIP_MSG_INIT
from .spine import (
    SpineDatagram,
    build_datagram,
    build_read_datagram,
    build_reply_datagram,
    build_result_datagram,
    extract_discovery_payloads,
    extract_commands,
    extract_header,
)
from .trace import TraceLogger


@dataclass(slots=True)
class ShipServerEvent:
    kind: str
    payload: Any


@dataclass(slots=True)
class _ActivePeerSession:
    peer_ski: str
    connection: ServerWebSocketConnection
    session_state: "_LocalSessionState"


@dataclass(slots=True)
class _LocalSessionState:
    peer_ski: str | None = None
    subscriptions: list[dict[str, Any]] = field(default_factory=list)
    bindings: list[dict[str, Any]] = field(default_factory=list)
    last_remote_discovery: dict[str, Any] | None = None
    remote_node_management_address: dict[str, Any] | None = None
    remote_discovery_requested: bool = False
    binding_bootstrap_sent: bool = False
    post_binding_bootstrap_sent: bool = False
    pending_binding_requests: dict[int, str] = field(default_factory=dict)
    binding_feature_bootstrap_sent: set[str] = field(default_factory=set)
    load_power_command_sent: bool = False
    load_power_write_msg_counter: int | None = None
    load_power_readback_msg_counter: int | None = None
    pending_load_power_writes: dict[int, dict[str, Any]] = field(default_factory=dict)
    pending_load_power_readbacks: dict[int, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class ShipServerConfig:
    identity: IdentityMaterial
    ship_id: str
    bind_host: str = "0.0.0.0"
    port: int = 4712
    path: str = "/ship/"
    device_id: str = "EEBUS-SDK-SERVER"
    peer_trust_anchors: tuple[str, ...] = ()
    trusted_client_skis: tuple[str, ...] = ()
    heartbeat_interval_seconds: float = 2.0
    heartbeat_timeout: str = "PT4S"
    ship_handshake_mode: str = "compatibility"
    spine_profile: str = "default"
    send_load_power_limit_watts: int | None = None
    send_load_power_duration_seconds: int | None = None
    send_load_power_limit_id: int = 0
    send_lpc_limit_watts: int | None = None
    send_lpc_duration_seconds: int | None = None
    send_lpc_limit_id: int | None = None

    def __post_init__(self) -> None:
        if self.send_load_power_limit_watts is None and self.send_lpc_limit_watts is not None:
            self.send_load_power_limit_watts = self.send_lpc_limit_watts
        if self.send_load_power_duration_seconds is None and self.send_lpc_duration_seconds is not None:
            self.send_load_power_duration_seconds = self.send_lpc_duration_seconds
        if self.send_lpc_limit_id is not None and self.send_load_power_limit_id == 0:
            self.send_load_power_limit_id = self.send_lpc_limit_id


class ShipServer:
    def __init__(self, config: ShipServerConfig, *, trace_logger: TraceLogger | None = None) -> None:
        self.config = config
        self.trace = trace_logger or TraceLogger(None)
        self.server: asyncio.AbstractServer | None = None
        self._events: asyncio.Queue[ShipServerEvent] = asyncio.Queue()
        self._trusted_client_skis = {
            normalized
            for normalized in (normalize_ski(value) for value in self.config.trusted_client_skis)
            if normalized
        }
        self._active_sessions: dict[str, _ActivePeerSession] = {}
        self._msg_counter = 1000
        self._profile = ServerSpineProfile(
            identity=self.config.identity,
            ship_id=self.config.ship_id,
            device_id=self.config.device_id,
            profile=self.config.spine_profile,
            heartbeat_timeout=self.config.heartbeat_timeout,
        )

    async def start(self) -> None:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.maximum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(self.config.identity.cert_path, self.config.identity.key_path)
        if self.config.peer_trust_anchors:
            context.verify_mode = ssl.CERT_REQUIRED
            for trust_anchor in self.config.peer_trust_anchors:
                context.load_verify_locations(cafile=trust_anchor)
        else:
            context.verify_mode = ssl.CERT_NONE
        with contextlib.suppress(Exception):
            context.set_ciphers("ECDHE-ECDSA-AES128-SHA256:ECDHE-ECDSA-AES128-CCM8:ECDHE-ECDSA-AES128-GCM-SHA256")
        self.server = await asyncio.start_server(self._handle_client, self.config.bind_host, self.config.port, ssl=context)

    async def stop(self) -> None:
        if self.server is None:
            return
        self.server.close()
        await self.server.wait_closed()
        self.server = None

    async def events(self) -> AsyncIterator[ShipServerEvent]:
        while True:
            yield await self._events.get()

    async def _emit(self, kind: str, payload: Any) -> None:
        await self._events.put(ShipServerEvent(kind=kind, payload=payload))

    def _local_device(self) -> str:
        return self._profile.local_device()

    def _local_nm_address(self) -> dict[str, Any]:
        return {
            "device": self._local_device(),
            "entity": [0],
            "feature": 0,
        }

    def _local_device_diagnosis_client_address(self) -> dict[str, Any]:
        return {"device": self._local_device(), "entity": [1], "feature": 1}

    def _local_load_control_client_address(self) -> dict[str, Any]:
        return {"device": self._local_device(), "entity": [1], "feature": 6}

    def _local_device_configuration_client_address(self) -> dict[str, Any]:
        return {"device": self._local_device(), "entity": [1], "feature": 7}

    def _local_electrical_connection_client_address(self) -> dict[str, Any]:
        return {"device": self._local_device(), "entity": [1], "feature": 8}

    def _local_measurement_client_address(self) -> dict[str, Any]:
        return {"device": self._local_device(), "entity": [1], "feature": 9}

    def _build_local_detailed_discovery(self) -> dict[str, Any]:
        return self._profile.detailed_discovery()

    def _build_local_use_case_data(self) -> dict[str, Any]:
        return self._profile.use_case_data()

    def _build_local_device_classification_data(self) -> dict[str, Any]:
        return self._profile.device_classification_data()

    def _build_local_load_control_limit_description_data(self) -> dict[str, Any]:
        return self._profile.load_control_limit_description_data()

    def _build_default_load_control_limit_payload(self) -> dict[str, Any]:
        return self._profile.default_load_control_limit_payload()

    def _build_local_device_configuration_description_data(self) -> dict[str, Any]:
        return self._profile.device_configuration_description_data()

    def _build_default_device_configuration_payload(self) -> dict[str, Any]:
        return self._profile.default_device_configuration_payload()

    def _build_local_device_diagnosis_heartbeat_data(self) -> dict[str, Any]:
        return self._profile.device_diagnosis_heartbeat_data()

    @staticmethod
    def _format_duration(seconds: int) -> str:
        return format_duration(seconds)

    def _build_load_power_write_payload_for_values(
        self,
        *,
        watts: int,
        duration_seconds: int | None,
        limit_id: int,
        is_active: bool,
    ) -> dict[str, Any]:
        return build_limit_payload(
            watts=watts,
            duration_seconds=duration_seconds,
            limit_id=limit_id,
            is_active=is_active,
        )

    def _build_lpc_write_payload_for_values(
        self,
        *,
        watts: int,
        duration_seconds: int | None,
        limit_id: int,
        is_active: bool,
    ) -> dict[str, Any]:
        return self._build_load_power_write_payload_for_values(
            watts=watts,
            duration_seconds=duration_seconds,
            limit_id=limit_id,
            is_active=is_active,
        )

    def _build_load_power_write_datagram(self, destination: dict[str, Any]) -> SpineDatagram:
        if self.config.send_load_power_limit_watts is None:
            raise ValueError("no load-power write configured")
        return self._build_load_power_write_datagram_for_values(
            destination,
            watts=self.config.send_load_power_limit_watts,
            duration_seconds=self.config.send_load_power_duration_seconds,
            limit_id=self.config.send_load_power_limit_id,
            is_active=True,
        )

    def _build_lpc_write_datagram(self, destination: dict[str, Any]) -> SpineDatagram:
        return self._build_load_power_write_datagram(destination)

    def _build_load_power_write_datagram_for_values(
        self,
        destination: dict[str, Any],
        *,
        watts: int,
        duration_seconds: int | None,
        limit_id: int,
        is_active: bool,
    ) -> SpineDatagram:
        return build_datagram(
            source=self._local_load_control_client_address(),
            destination=destination,
            cmd_classifier="write",
            msg_counter=self._next_msg_counter(),
            commands=[
                self._build_load_power_write_payload_for_values(
                    watts=watts,
                    duration_seconds=duration_seconds,
                    limit_id=limit_id,
                    is_active=is_active,
                )
            ],
            ack_request=True,
        )

    def _build_lpc_write_datagram_for_values(
        self,
        destination: dict[str, Any],
        *,
        watts: int,
        duration_seconds: int | None,
        limit_id: int,
        is_active: bool,
    ) -> SpineDatagram:
        return self._build_load_power_write_datagram_for_values(
            destination,
            watts=watts,
            duration_seconds=duration_seconds,
            limit_id=limit_id,
            is_active=is_active,
        )

    def _build_load_control_read_datagram(self, destination: dict[str, Any]) -> SpineDatagram:
        return build_read_datagram(
            source=self._local_load_control_client_address(),
            destination=destination,
            msg_counter=self._next_msg_counter(),
            function_name="loadControlLimitListData",
        )

    @staticmethod
    def _merge_keyed_list_payload(
        current: dict[str, Any] | list[Any] | None,
        incoming: Any,
        *,
        list_key: str,
        id_key: str,
    ) -> Any:
        return merge_keyed_list_payload(current, incoming, list_key=list_key, id_key=id_key)

    @staticmethod
    def _limit_direction_for_id(limit_id: int | None) -> str | None:
        return limit_direction_for_id(limit_id)

    @staticmethod
    def _extract_load_control_limit_state(payload: Any) -> dict[str, Any] | None:
        return extract_limit_state(payload)

    @staticmethod
    def _extract_load_control_limit_states(payload: Any) -> list[dict[str, Any]]:
        return extract_limit_states(payload)

    @staticmethod
    def _extract_preferred_load_power_limit_state(payload: Any) -> dict[str, Any] | None:
        return extract_preferred_load_power_state(payload)

    @staticmethod
    def _extract_preferred_lpc_limit_state(payload: Any) -> dict[str, Any] | None:
        return extract_preferred_load_power_state(payload)

    def _remote_load_control_server_address(self, session_state: _LocalSessionState) -> dict[str, Any] | None:
        discovery = session_state.last_remote_discovery
        if not isinstance(discovery, dict):
            return None
        return self._preferred_remote_feature_address(
            self._remote_feature_addresses(discovery, feature_type="LoadControl", role="server")
        )

    async def send_load_power_limit_to_peer(
        self,
        peer_ski: str,
        *,
        watts: int,
        duration_seconds: int | None,
        limit_id: int = 0,
        is_active: bool = True,
    ) -> dict[str, Any]:
        normalized_peer_ski = normalize_ski(peer_ski)
        if normalized_peer_ski is None:
            raise ValueError("peer SKI is invalid")
        active_session = self._active_sessions.get(normalized_peer_ski)
        if active_session is None:
            raise ValueError(f"peer {normalized_peer_ski} is not currently connected")

        destination = self._remote_load_control_server_address(active_session.session_state)
        if destination is None:
            raise ValueError(f"peer {normalized_peer_ski} has not completed LoadControl discovery yet")

        write_datagram = self._build_load_power_write_datagram_for_values(
            destination,
            watts=watts,
            duration_seconds=duration_seconds,
            limit_id=limit_id,
            is_active=is_active,
        )
        readback_datagram = self._build_load_control_read_datagram(destination)
        write_msg_counter = extract_header(write_datagram).get("msgCounter")
        readback_msg_counter = extract_header(readback_datagram).get("msgCounter")
        metadata = {
            "peer_ski": active_session.peer_ski,
            "limit_id": limit_id,
            "watts": watts,
            "duration_seconds": duration_seconds,
            "duration": format_duration(duration_seconds) if duration_seconds is not None else None,
            "is_active": is_active,
            "msg_counter": write_msg_counter,
            "readback_msg_counter": readback_msg_counter,
        }
        if isinstance(write_msg_counter, int):
            active_session.session_state.pending_load_power_writes[write_msg_counter] = metadata
        if isinstance(readback_msg_counter, int):
            active_session.session_state.pending_load_power_readbacks[readback_msg_counter] = metadata

        await self._send_data(active_session.connection, write_datagram)
        await self._send_data(active_session.connection, readback_datagram)
        await self._emit("load_power_write_sent", metadata)
        return metadata

    async def send_lpc_limit_to_peer(
        self,
        peer_ski: str,
        *,
        watts: int,
        duration_seconds: int | None,
        limit_id: int = 0,
        is_active: bool = True,
    ) -> dict[str, Any]:
        return await self.send_load_power_limit_to_peer(
            peer_ski,
            watts=watts,
            duration_seconds=duration_seconds,
            limit_id=limit_id,
            is_active=is_active,
        )

    @staticmethod
    def _should_skip_result_for_read(datagram: SpineDatagram, commands: list[dict[str, Any]]) -> bool:
        header = extract_header(datagram)
        if header.get("cmdClassifier") != "read":
            return False
        return True

    def _build_local_electrical_connection_characteristic_data(self) -> dict[str, Any]:
        return self._profile.electrical_connection_characteristic_data()

    def _build_local_electrical_connection_description_data(self) -> dict[str, Any]:
        return self._profile.electrical_connection_description_data()

    def _build_local_electrical_connection_parameter_description_data(self) -> dict[str, Any]:
        return self._profile.electrical_connection_parameter_description_data()

    def _build_local_measurement_description_data(self) -> dict[str, Any]:
        return self._profile.measurement_description_data()

    def _build_local_measurement_constraints_data(self) -> dict[str, Any]:
        return self._profile.measurement_constraints_data()

    def _build_local_measurement_data(self) -> dict[str, Any]:
        return self._profile.measurement_data()

    @staticmethod
    def _feature_address_key(address: dict[str, Any]) -> tuple[str | None, tuple[int, ...], int | None]:
        return feature_address_tuple(address)

    def _next_msg_counter(self) -> int:
        self._msg_counter += 1
        return self._msg_counter

    def _local_source_for_destination(self, destination: Any) -> dict[str, Any]:
        return local_source_for_destination(
            destination,
            local_device=self._local_device(),
            fallback=self._local_nm_address(),
        )

    def _normalize_feature_address(
        self,
        address: dict[str, Any],
        *,
        default_device: str | None,
    ) -> dict[str, Any]:
        return normalize_feature_address(address, default_device=default_device)

    @staticmethod
    def _subscription_key(subscription: dict[str, Any]) -> tuple[tuple[str | None, tuple[int, ...], int | None], str | None]:
        client_address = subscription.get("clientAddress", {})
        return (
            ShipServer._feature_address_key(client_address if isinstance(client_address, dict) else {}),
            subscription.get("serverFeatureType"),
        )

    @staticmethod
    def _binding_key(binding: dict[str, Any]) -> tuple[tuple[str | None, tuple[int, ...], int | None], tuple[str | None, tuple[int, ...], int | None]]:
        client_address = binding.get("clientAddress", {})
        server_address = binding.get("serverAddress", {})
        return (
            ShipServer._feature_address_key(client_address if isinstance(client_address, dict) else {}),
            ShipServer._feature_address_key(server_address if isinstance(server_address, dict) else {}),
        )

    @staticmethod
    def _discovery_entity_key(entry: dict[str, Any]) -> tuple[tuple[int, ...], ...] | None:
        description = entry.get("description")
        if not isinstance(description, dict):
            return None
        entity_address = description.get("entityAddress")
        if not isinstance(entity_address, dict):
            return None
        entity = entity_address.get("entity")
        if not isinstance(entity, list):
            return None
        return (tuple(entity),)

    @staticmethod
    def _discovery_feature_key(entry: dict[str, Any]) -> tuple[tuple[int, ...], int | None] | None:
        description = entry.get("description")
        if not isinstance(description, dict):
            return None
        feature_address = description.get("featureAddress")
        if not isinstance(feature_address, dict):
            return None
        entity = feature_address.get("entity")
        if not isinstance(entity, list):
            return None
        return (tuple(entity), feature_address.get("feature"))

    @staticmethod
    def _merge_discovery_entries(
        existing: Any,
        update: Any,
        *,
        key_func: Any,
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        index_by_key: dict[Any, int] = {}

        for entry in existing if isinstance(existing, list) else []:
            if not isinstance(entry, dict):
                continue
            key = key_func(entry)
            if key is None:
                merged.append(entry)
                continue
            index_by_key[key] = len(merged)
            merged.append(entry)

        for entry in update if isinstance(update, list) else []:
            if not isinstance(entry, dict):
                continue
            key = key_func(entry)
            if key is None or key not in index_by_key:
                if key is not None:
                    index_by_key[key] = len(merged)
                merged.append(entry)
                continue
            merged[index_by_key[key]] = entry

        return merged

    def _merge_remote_discovery(
        self,
        existing: dict[str, Any] | None,
        update: dict[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(existing, dict):
            return update

        merged = dict(existing)
        for key, value in update.items():
            if key == "entityInformation":
                merged[key] = self._merge_discovery_entries(
                    merged.get(key),
                    value,
                    key_func=self._discovery_entity_key,
                )
                continue
            if key == "featureInformation":
                merged[key] = self._merge_discovery_entries(
                    merged.get(key),
                    value,
                    key_func=self._discovery_feature_key,
                )
                continue
            merged[key] = value
        return merged

    def _record_node_management_calls(
        self,
        commands: list[dict[str, Any]],
        *,
        source_device: str | None,
        session_state: _LocalSessionState,
    ) -> None:
        for command in commands:
            if "nodeManagementSubscriptionDeleteCall" in command:
                session_state.subscriptions.clear()
                continue
            if "nodeManagementBindingDeleteCall" in command:
                session_state.bindings.clear()
                continue
            if "nodeManagementSubscriptionRequestCall" in command:
                request = command["nodeManagementSubscriptionRequestCall"]
                if not isinstance(request, dict):
                    continue
                entry = request.get("subscriptionRequest")
                if not isinstance(entry, dict):
                    continue
                normalized = {
                    "subscriptionId": len(session_state.subscriptions),
                    "clientAddress": self._normalize_feature_address(
                        entry.get("clientAddress", {}),
                        default_device=source_device,
                    ),
                    "serverAddress": self._normalize_feature_address(
                        entry.get("serverAddress", {}),
                        default_device=self._local_device(),
                    ),
                    "serverFeatureType": entry.get("serverFeatureType"),
                }
                key = self._subscription_key(normalized)
                if any(self._subscription_key(existing) == key for existing in session_state.subscriptions):
                    continue
                session_state.subscriptions.append(normalized)
                continue
            if "nodeManagementBindingRequestCall" not in command:
                continue
            request = command["nodeManagementBindingRequestCall"]
            if not isinstance(request, dict):
                continue
            entry = request.get("bindingRequest")
            if not isinstance(entry, dict):
                continue
            normalized = {
                "bindingId": len(session_state.bindings),
                "clientAddress": self._normalize_feature_address(
                    entry.get("clientAddress", {}),
                    default_device=source_device,
                ),
                "serverAddress": self._normalize_feature_address(
                    entry.get("serverAddress", {}),
                    default_device=self._local_device(),
                ),
            }
            key = self._binding_key(normalized)
            if any(self._binding_key(existing) == key for existing in session_state.bindings):
                continue
            session_state.bindings.append(normalized)

    def _build_local_subscription_data(self, session_state: _LocalSessionState) -> dict[str, Any]:
        return self._profile.subscription_data(session_state.subscriptions)

    def _build_local_destination_list(self) -> dict[str, Any]:
        return self._profile.destination_list()

    def _build_local_binding_data(self, session_state: _LocalSessionState) -> dict[str, Any]:
        return self._profile.binding_data(session_state.bindings)

    def _build_subscription_request_call(
        self,
        *,
        destination: dict[str, Any],
        client_address: dict[str, Any],
        server_address: dict[str, Any],
        server_feature_type: str,
    ) -> SpineDatagram:
        return build_datagram(
            source=self._local_nm_address(),
            destination=destination,
            cmd_classifier="call",
            msg_counter=self._next_msg_counter(),
            ack_request=True,
            commands=[
                {
                    "nodeManagementSubscriptionRequestCall": {
                        "subscriptionRequest": {
                            "clientAddress": client_address,
                            "serverAddress": server_address,
                            "serverFeatureType": server_feature_type,
                        }
                    }
                }
            ],
        )

    def _build_binding_request_call(
        self,
        *,
        destination: dict[str, Any],
        client_address: dict[str, Any],
        server_address: dict[str, Any],
        server_feature_type: str,
    ) -> SpineDatagram:
        return build_datagram(
            source=self._local_nm_address(),
            destination=destination,
            cmd_classifier="call",
            msg_counter=self._next_msg_counter(),
            ack_request=True,
            commands=[
                {
                    "nodeManagementBindingRequestCall": {
                        "bindingRequest": {
                            "clientAddress": client_address,
                            "serverAddress": server_address,
                            "serverFeatureType": server_feature_type,
                        }
                    }
                }
            ],
        )

    def _remote_feature_addresses(
        self,
        discovery_payload: dict[str, Any],
        *,
        feature_type: str,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        return feature_addresses(discovery_payload, feature_type=feature_type, role=role)

    @staticmethod
    def _preferred_remote_feature_address(addresses: list[dict[str, Any]]) -> dict[str, Any] | None:
        return preferred_feature_address(addresses)

    def _bootstrap_from_remote_discovery(self, session_state: _LocalSessionState) -> list[SpineDatagram]:
        if session_state.binding_bootstrap_sent:
            return []
        remote_nm_address = session_state.remote_node_management_address
        discovery = session_state.last_remote_discovery
        if not isinstance(remote_nm_address, dict) or not isinstance(discovery, dict):
            return []

        session_state.binding_bootstrap_sent = True
        outgoing: list[SpineDatagram] = []
        binding_targets = (
            ("DeviceDiagnosis", self._local_device_diagnosis_client_address()),
            ("LoadControl", self._local_load_control_client_address()),
            ("DeviceConfiguration", self._local_device_configuration_client_address()),
            ("ElectricalConnection", self._local_electrical_connection_client_address()),
            ("Measurement", self._local_measurement_client_address()),
        )
        for feature_type, client_address in binding_targets:
            server_address = self._preferred_remote_feature_address(
                self._remote_feature_addresses(discovery, feature_type=feature_type, role="server")
            )
            if server_address is None:
                continue
            binding_request = self._build_binding_request_call(
                destination=remote_nm_address,
                client_address=client_address,
                server_address=server_address,
                server_feature_type=feature_type,
            )
            msg_counter = extract_header(binding_request).get("msgCounter")
            if isinstance(msg_counter, int):
                session_state.pending_binding_requests[msg_counter] = feature_type
            outgoing.append(binding_request)

        outgoing.extend(
            [
                build_read_datagram(
                    source=self._local_nm_address(),
                    destination=remote_nm_address,
                    msg_counter=self._next_msg_counter(),
                    function_name="nodeManagementBindingData",
                ),
            ]
        )
        return outgoing

    def _bootstrap_common_after_binding(self, session_state: _LocalSessionState) -> list[SpineDatagram]:
        if session_state.post_binding_bootstrap_sent:
            return []
        remote_nm_address = session_state.remote_node_management_address
        discovery = session_state.last_remote_discovery
        if not isinstance(remote_nm_address, dict) or not isinstance(discovery, dict):
            return []

        session_state.post_binding_bootstrap_sent = True
        return [
            self._build_subscription_request_call(
                destination=remote_nm_address,
                client_address=self._local_nm_address(),
                server_address=remote_nm_address,
                server_feature_type="NodeManagement",
            ),
        ]

    def _bootstrap_feature_after_binding(
        self,
        session_state: _LocalSessionState,
        feature_type: str,
    ) -> list[SpineDatagram]:
        remote_nm_address = session_state.remote_node_management_address
        discovery = session_state.last_remote_discovery
        if not isinstance(remote_nm_address, dict) or not isinstance(discovery, dict):
            return []
        if feature_type in session_state.binding_feature_bootstrap_sent:
            return []

        outgoing: list[SpineDatagram] = []

        if feature_type == "DeviceDiagnosis":
            diag_server = self._preferred_remote_feature_address(
                self._remote_feature_addresses(discovery, feature_type="DeviceDiagnosis", role="server")
            )
            if diag_server is None:
                return []
            session_state.binding_feature_bootstrap_sent.add(feature_type)
            outgoing.extend(
                [
                    self._build_subscription_request_call(
                        destination=remote_nm_address,
                        client_address=self._local_device_diagnosis_client_address(),
                        server_address=diag_server,
                        server_feature_type="DeviceDiagnosis",
                    ),
                    build_read_datagram(
                        source=self._local_device_diagnosis_client_address(),
                        destination=diag_server,
                        msg_counter=self._next_msg_counter(),
                        function_name="deviceDiagnosisHeartbeatData",
                    ),
                ]
            )
            return outgoing

        if feature_type == "LoadControl":
            load_control_server = self._preferred_remote_feature_address(
                self._remote_feature_addresses(discovery, feature_type="LoadControl", role="server")
            )
            if load_control_server is None:
                return []
            session_state.binding_feature_bootstrap_sent.add(feature_type)
            outgoing.extend(
                [
                    build_read_datagram(
                        source=self._local_load_control_client_address(),
                        destination=load_control_server,
                        msg_counter=self._next_msg_counter(),
                        function_name="loadControlLimitDescriptionListData",
                    ),
                    build_read_datagram(
                        source=self._local_load_control_client_address(),
                        destination=load_control_server,
                        msg_counter=self._next_msg_counter(),
                        function_name="loadControlLimitListData",
                    ),
                ]
            )
            return outgoing

        if feature_type == "DeviceConfiguration":
            config_server = self._preferred_remote_feature_address(
                self._remote_feature_addresses(discovery, feature_type="DeviceConfiguration", role="server")
            )
            if config_server is None:
                return []
            session_state.binding_feature_bootstrap_sent.add(feature_type)
            outgoing.extend(
                [
                    build_read_datagram(
                        source=self._local_device_configuration_client_address(),
                        destination=config_server,
                        msg_counter=self._next_msg_counter(),
                        function_name="deviceConfigurationKeyValueDescriptionListData",
                    ),
                    build_read_datagram(
                        source=self._local_device_configuration_client_address(),
                        destination=config_server,
                        msg_counter=self._next_msg_counter(),
                        function_name="deviceConfigurationKeyValueListData",
                    ),
                ]
            )
            return outgoing

        if feature_type == "ElectricalConnection":
            electrical_server = self._preferred_remote_feature_address(
                self._remote_feature_addresses(discovery, feature_type="ElectricalConnection", role="server")
            )
            if electrical_server is None:
                return []
            session_state.binding_feature_bootstrap_sent.add(feature_type)
            outgoing.append(
                build_read_datagram(
                    source=self._local_electrical_connection_client_address(),
                    destination=electrical_server,
                    msg_counter=self._next_msg_counter(),
                    function_name="electricalConnectionCharacteristicListData",
                )
            )
            return outgoing

        if feature_type == "Measurement":
            measurement_server = self._preferred_remote_feature_address(
                self._remote_feature_addresses(discovery, feature_type="Measurement", role="server")
            )
            if measurement_server is None:
                return []
            session_state.binding_feature_bootstrap_sent.add(feature_type)
            outgoing.extend(
                [
                    build_read_datagram(
                        source=self._local_measurement_client_address(),
                        destination=measurement_server,
                        msg_counter=self._next_msg_counter(),
                        function_name="measurementDescriptionListData",
                    ),
                    build_read_datagram(
                        source=self._local_measurement_client_address(),
                        destination=measurement_server,
                        msg_counter=self._next_msg_counter(),
                        function_name="measurementListData",
                    ),
                ]
            )
            return outgoing

        return []

    def _bootstrap_after_binding(self, session_state: _LocalSessionState) -> list[SpineDatagram]:
        outgoing = self._bootstrap_common_after_binding(session_state)
        for feature_type in (
            "DeviceDiagnosis",
            "LoadControl",
            "DeviceConfiguration",
            "ElectricalConnection",
            "Measurement",
        ):
            outgoing.extend(self._bootstrap_feature_after_binding(session_state, feature_type))
        return outgoing

    async def _publish_device_diagnosis_heartbeats(
        self,
        connection: ServerWebSocketConnection,
        session_state: _LocalSessionState,
    ) -> None:
        interval = max(0.1, self.config.heartbeat_interval_seconds)
        while True:
            await asyncio.sleep(interval)
            for subscription in list(session_state.subscriptions):
                if subscription.get("serverFeatureType") != "DeviceDiagnosis":
                    continue
                client_address = subscription.get("clientAddress")
                server_address = subscription.get("serverAddress")
                if not isinstance(client_address, dict) or not isinstance(server_address, dict):
                    continue
                await self._send_data(
                    connection,
                    build_datagram(
                        source=server_address,
                        destination=client_address,
                        cmd_classifier="notify",
                        msg_counter=self._next_msg_counter(),
                        commands=[
                            {
                                "deviceDiagnosisHeartbeatData": self._build_local_device_diagnosis_heartbeat_data()
                            }
                        ],
                    ),
                )

    async def _send_control(self, connection: ServerWebSocketConnection, payload: dict[str, Any]) -> None:
        encoded = bytes([SHIP_MSG_CONTROL]) + to_eebus_json_bytes(payload)
        await connection.send_binary(encoded)
        self.trace.log("server_tx_control", payload=payload)

    async def _send_data(self, connection: ServerWebSocketConnection, payload: SpineDatagram | dict[str, Any]) -> None:
        datagram = payload if isinstance(payload, SpineDatagram) else SpineDatagram(payload=payload)
        encoded = bytes([SHIP_MSG_DATA]) + to_eebus_json_bytes(datagram.as_ship_payload())
        await connection.send_binary(encoded)
        self.trace.log("server_tx_data", payload=datagram.as_ship_payload())

    async def _receive_message(self, connection: ServerWebSocketConnection) -> tuple[int, Any]:
        frame = await connection.receive_frame()
        if frame.opcode == 0x8:
            code = int.from_bytes(frame.payload[:2], "big") if len(frame.payload) >= 2 else None
            reason = frame.payload[2:].decode("utf-8", "replace") if len(frame.payload) > 2 else ""
            raise ConnectionError(f"peer closed the server session: code={code} reason={reason!r}")
        if frame.opcode != 0x2 or not frame.payload:
            raise ConnectionError(f"unexpected websocket frame opcode={frame.opcode}")
        msg_type = frame.payload[0]
        body = frame.payload[1:]
        if msg_type == SHIP_MSG_INIT:
            return msg_type, body
        return msg_type, from_eebus_json_bytes(body)

    @staticmethod
    def _control_payload_as_mapping(payload: Any) -> dict[str, Any]:
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _extract_request_id(payload: Any) -> str | int | None:
        mapping = payload if isinstance(payload, dict) else {}
        request_id = mapping.get("requestId")
        if isinstance(request_id, (str, int)):
            return request_id
        return None

    @classmethod
    def _extract_user_trust_levels(cls, payload: Any) -> list[int]:
        mapping = cls._control_payload_as_mapping(payload)
        methods = mapping.get("methods", mapping)
        candidates = methods if isinstance(methods, list) else [methods]
        levels: list[int] = []

        for candidate in candidates:
            candidate_mapping = cls._control_payload_as_mapping(candidate)
            user_trust = candidate_mapping.get("userTrust", candidate_mapping)
            trust_entries = user_trust if isinstance(user_trust, list) else [user_trust]
            for trust_entry in trust_entries:
                trust_mapping = cls._control_payload_as_mapping(trust_entry)
                raw_levels = trust_mapping.get("levels")
                if isinstance(raw_levels, list):
                    levels.extend(level for level in raw_levels if isinstance(level, int))
                raw_level = trust_mapping.get("level")
                if isinstance(raw_level, int):
                    levels.append(raw_level)

        normalized: list[int] = []
        for level in levels:
            if level not in normalized:
                normalized.append(level)
        return normalized

    def _build_access_methods_response(self, request_payload: Any) -> dict[str, Any]:
        response: dict[str, Any] = {"methods": {"userTrust": {"levels": [1]}}}
        request_id = self._extract_request_id(request_payload)
        if request_id is not None:
            response["requestId"] = request_id
        return {"accessMethodsResponse": response}

    def _build_access_request(self, response_payload: Any) -> dict[str, Any] | None:
        levels = self._extract_user_trust_levels(response_payload)
        if not levels:
            return None

        request: dict[str, Any] = {"methods": {"userTrust": {"level": min(levels)}}}
        request_id = self._extract_request_id(response_payload)
        if request_id is not None:
            request["requestId"] = request_id
        return {"accessRequest": request}

    def _build_local_access_methods(self) -> dict[str, Any]:
        return {"accessMethods": {"id": self.config.ship_id}}

    @staticmethod
    def _access_status_allows_progress(payload: Any) -> bool:
        mapping = payload if isinstance(payload, dict) else {}
        for key in ("status", "accessStatus", "state", "result"):
            value = mapping.get(key)
            if isinstance(value, str) and value.lower() in {"granted", "accepted", "confirmed", "ready", "ok"}:
                return True
            if value is True:
                return True
        return False

    async def _receive_control(
        self,
        connection: ServerWebSocketConnection,
        *,
        stage: str,
        pending_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if pending_payload is not None:
            payload = pending_payload
        else:
            msg_type, payload = await self._receive_message(connection)
            if msg_type != SHIP_MSG_CONTROL:
                raise ConnectionError(f"expected SHIP control during {stage}, got type={msg_type}")
        self.trace.log("server_rx_control", payload=payload, stage=stage)
        return self._control_payload_as_mapping(payload)

    async def _perform_protocol_handshake(
        self,
        connection: ServerWebSocketConnection,
        *,
        initial_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if self.config.ship_handshake_mode == "standard":
            payload = await self._receive_control(
                connection,
                stage="protocol-announce",
                pending_payload=initial_payload,
            )
            protocol = self._control_payload_as_mapping(payload.get("messageProtocolHandshake"))
            if protocol.get("handshakeType") != "announceMax":
                raise ConnectionError(f"expected messageProtocolHandshake announceMax, got {payload!r}")
            await self._send_control(
                connection,
                {
                    "messageProtocolHandshake": {
                        "handshakeType": "select",
                        "version": {"major": 1, "minor": 0},
                        "formats": {"format": ["JSON-UTF8"]},
                    }
                },
            )
            payload = await self._receive_control(connection, stage="protocol-select")
            protocol = self._control_payload_as_mapping(payload.get("messageProtocolHandshake"))
            if protocol.get("handshakeType") != "select":
                raise ConnectionError(f"expected messageProtocolHandshake select, got {payload!r}")
            return None

        local_announce_sent = False
        local_select_sent = False
        payload = initial_payload
        for _ in range(8):
            if payload is None:
                try:
                    payload = await asyncio.wait_for(
                        self._receive_control(connection, stage="protocol"),
                        timeout=0.5 if not local_announce_sent else 2.0,
                    )
                except asyncio.TimeoutError:
                    if local_announce_sent:
                        raise ConnectionError("timed out waiting for protocol handshake")
                    await self._send_control(
                        connection,
                        {
                            "messageProtocolHandshake": {
                                "handshakeType": "announceMax",
                                "version": {"major": 1, "minor": 0},
                                "formats": {"format": ["JSON-UTF8"]},
                            }
                        },
                    )
                    local_announce_sent = True
                    continue
            else:
                payload = await self._receive_control(
                    connection,
                    stage="protocol",
                    pending_payload=payload,
                )

            protocol = self._control_payload_as_mapping(payload.get("messageProtocolHandshake"))
            handshake_type = protocol.get("handshakeType")
            if handshake_type == "announceMax":
                if not local_select_sent:
                    await self._send_control(
                        connection,
                        {
                            "messageProtocolHandshake": {
                                "handshakeType": "select",
                                "version": {"major": 1, "minor": 0},
                                "formats": {"format": ["JSON-UTF8"]},
                            }
                        },
                    )
                    local_select_sent = True
                payload = None
                continue
            if handshake_type == "select":
                if not local_select_sent:
                    await self._send_control(
                        connection,
                        {
                            "messageProtocolHandshake": {
                                "handshakeType": "select",
                                "version": {"major": 1, "minor": 0},
                                "formats": {"format": ["JSON-UTF8"]},
                            }
                        },
                    )
                    local_select_sent = True
                return None
            if "messageProtocolHandshakeError" in payload:
                raise ConnectionError(f"peer rejected protocol handshake {payload!r}")
            if "connectionHello" in payload:
                payload = None
                continue
            if not local_announce_sent:
                await self._send_control(
                    connection,
                    {
                        "messageProtocolHandshake": {
                            "handshakeType": "announceMax",
                            "version": {"major": 1, "minor": 0},
                            "formats": {"format": ["JSON-UTF8"]},
                        }
                    },
                )
                local_announce_sent = True
            if local_select_sent:
                return payload
            self.trace.log("server_protocol_waiting_for_select", payload=payload)
            payload = None

        raise ConnectionError("protocol handshake did not complete")

    async def _perform_hello_handshake(
        self,
        connection: ServerWebSocketConnection,
    ) -> dict[str, Any] | None:
        payload = await self._receive_control(connection, stage="hello")
        hello = self._control_payload_as_mapping(payload.get("connectionHello"))
        phase = hello.get("phase")
        if phase not in {"ready", "pending", None}:
            raise ConnectionError(f"unexpected connectionHello phase {phase!r}")
        if phase == "aborted":
            raise ConnectionError("peer aborted SHIP hello phase")
        await self._send_control(connection, {"connectionHello": {"phase": "ready", "waiting": 60000}})

        for _ in range(4):
            try:
                payload = await asyncio.wait_for(
                    self._receive_control(connection, stage="hello-followup"),
                    timeout=0.2,
                )
            except asyncio.TimeoutError:
                return None

            if "connectionHello" not in payload:
                return payload

            hello = self._control_payload_as_mapping(payload.get("connectionHello"))
            phase = hello.get("phase")
            if phase == "aborted":
                raise ConnectionError("peer aborted SHIP hello phase")
            if phase not in {"ready", "pending", None}:
                raise ConnectionError(f"unexpected connectionHello phase {phase!r}")

        return None

    async def _perform_pin_handshake(
        self,
        connection: ServerWebSocketConnection,
        *,
        initial_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if self.config.ship_handshake_mode == "standard":
            payload = await self._receive_control(connection, stage="pin", pending_payload=initial_payload)
            pin_state = self._control_payload_as_mapping(payload.get("connectionPinState"))
            if pin_state.get("pinState") != "none":
                raise ConnectionError(f"expected connectionPinState none, got {payload!r}")
            await self._send_control(connection, {"connectionPinState": {"pinState": "none"}})
            return None

        await self._send_control(connection, {"connectionPinState": {"pinState": "none"}})
        saw_pin_state = False
        payload = initial_payload
        for _ in range(6):
            payload = await self._receive_control(connection, stage="pin", pending_payload=payload)
            pin_state = self._control_payload_as_mapping(payload.get("connectionPinState"))
            if pin_state:
                if pin_state.get("pinState") != "none":
                    raise ConnectionError(f"unsupported connectionPinState {payload!r}")
                saw_pin_state = True
                payload = None
                continue
            if saw_pin_state:
                return payload
            if any(key in payload for key in ("accessMethodsRequest", "accessMethodsResponse", "accessRequest", "accessMethods", "accessStatus")):
                return payload
            raise ConnectionError(f"unexpected pin handshake payload {payload!r}")
        return None

    async def _perform_access_methods_handshake(
        self,
        connection: ServerWebSocketConnection,
        *,
        initial_payload: dict[str, Any] | None = None,
    ) -> str | None:
        await self._send_control(connection, {"accessMethodsRequest": {}})
        sent_local_access_methods = False
        saw_access_progress = False
        payload = initial_payload

        for _ in range(12):
            if payload is None:
                try:
                    payload = await asyncio.wait_for(
                        self._receive_control(connection, stage="access-methods"),
                        timeout=2.0,
                    )
                except asyncio.TimeoutError:
                    if saw_access_progress:
                        break
                    raise ConnectionError("timed out waiting for access methods handshake")
            else:
                payload = await self._receive_control(
                    connection,
                    stage="access-methods",
                    pending_payload=payload,
                )

            if "accessMethodsRequest" in payload:
                if not sent_local_access_methods:
                    await self._send_control(connection, self._build_local_access_methods())
                    sent_local_access_methods = True
                saw_access_progress = True
                payload = None
                continue
            if "accessMethodsResponse" in payload:
                access_request = self._build_access_request(payload["accessMethodsResponse"])
                if access_request is not None:
                    await self._send_control(connection, access_request)
                saw_access_progress = True
                payload = None
                continue
            if "accessRequest" in payload:
                if not sent_local_access_methods:
                    await self._send_control(connection, self._build_local_access_methods())
                    sent_local_access_methods = True
                saw_access_progress = True
                payload = None
                continue
            if "accessStatus" in payload:
                saw_access_progress = saw_access_progress or self._access_status_allows_progress(payload["accessStatus"])
                payload = None
                continue
            if "accessMethods" in payload:
                access_methods = self._control_payload_as_mapping(payload["accessMethods"])
                remote_id = access_methods.get("id")
                if not sent_local_access_methods:
                    await self._send_control(connection, self._build_local_access_methods())
                    sent_local_access_methods = True
                return remote_id if isinstance(remote_id, str) else None
            if sent_local_access_methods:
                return None
            raise ConnectionError(f"unexpected access methods payload {payload!r}")

        if not sent_local_access_methods:
            await self._send_control(connection, self._build_local_access_methods())
        return None

    async def _handle_spine_data(
        self,
        connection: ServerWebSocketConnection,
        datagram: SpineDatagram,
        *,
        session_state: _LocalSessionState,
    ) -> None:
        await self._emit("datagram", datagram)
        self.trace.log("server_rx_data", payload=datagram.as_ship_payload())

        header = extract_header(datagram)
        source = header.get("addressSource", {})
        destination = header.get("addressDestination", {})
        local_source = self._local_source_for_destination(destination)
        responses: list[SpineDatagram] = []
        post_send_events: list[tuple[str, Any]] = []
        commands = extract_commands(datagram)
        needs_result = bool(header.get("ackRequest")) or header.get("cmdClassifier") == "call"
        if needs_result and not self._should_skip_result_for_read(datagram, commands):
            responses.append(
                build_result_datagram(
                    datagram,
                    source=local_source,
                    msg_counter=self._next_msg_counter(),
                )
            )

        if header.get("cmdClassifier") == "read":
            reply_commands: list[dict[str, Any]] = []
            for command in commands:
                if "nodeManagementDetailedDiscoveryData" in command:
                    reply_commands.append({"nodeManagementDetailedDiscoveryData": self._build_local_detailed_discovery()})
                if "nodeManagementUseCaseData" in command:
                    reply_commands.append({"nodeManagementUseCaseData": self._build_local_use_case_data()})
                if "nodeManagementSubscriptionData" in command:
                    reply_commands.append({"nodeManagementSubscriptionData": self._build_local_subscription_data(session_state)})
                if "nodeManagementDestinationListData" in command:
                    reply_commands.append({"nodeManagementDestinationListData": self._build_local_destination_list()})
                if "nodeManagementBindingData" in command:
                    reply_commands.append({"nodeManagementBindingData": self._build_local_binding_data(session_state)})
                if "deviceClassificationManufacturerData" in command:
                    reply_commands.append(
                        {"deviceClassificationManufacturerData": self._build_local_device_classification_data()}
                    )
                if "loadControlLimitDescriptionListData" in command:
                    reply_commands.append(
                        {"loadControlLimitDescriptionListData": self._build_local_load_control_limit_description_data()}
                    )
                if "loadControlLimitListData" in command:
                    reply_commands.append({"loadControlLimitListData": self._profile.load_control_limit_payload})
                if "deviceConfigurationKeyValueDescriptionListData" in command:
                    reply_commands.append(
                        {
                            "deviceConfigurationKeyValueDescriptionListData": (
                                self._build_local_device_configuration_description_data()
                            )
                        }
                    )
                if "deviceConfigurationKeyValueListData" in command:
                    reply_commands.append(
                        {"deviceConfigurationKeyValueListData": self._profile.device_configuration_payload}
                    )
                if "deviceDiagnosisHeartbeatData" in command:
                    reply_commands.append({"deviceDiagnosisHeartbeatData": self._build_local_device_diagnosis_heartbeat_data()})
                if "electricalConnectionCharacteristicListData" in command:
                    reply_commands.append(
                        {
                            "electricalConnectionCharacteristicListData": (
                                self._build_local_electrical_connection_characteristic_data()
                            )
                        }
                    )
                if "electricalConnectionDescriptionListData" in command:
                    reply_commands.append(
                        {
                            "electricalConnectionDescriptionListData": (
                                self._build_local_electrical_connection_description_data()
                            )
                        }
                    )
                if "electricalConnectionParameterDescriptionListData" in command:
                    reply_commands.append(
                        {
                            "electricalConnectionParameterDescriptionListData": (
                                self._build_local_electrical_connection_parameter_description_data()
                            )
                        }
                    )
                if "measurementDescriptionListData" in command:
                    reply_commands.append({"measurementDescriptionListData": self._build_local_measurement_description_data()})
                if "measurementConstraintsListData" in command:
                    reply_commands.append({"measurementConstraintsListData": self._build_local_measurement_constraints_data()})
                if "measurementListData" in command:
                    reply_commands.append({"measurementListData": self._build_local_measurement_data()})
            if reply_commands:
                responses.append(
                    build_reply_datagram(
                        datagram,
                        source=local_source,
                        msg_counter=self._next_msg_counter(),
                        commands=reply_commands,
                    )
                )
            if any("nodeManagementDetailedDiscoveryData" in command for command in commands):
                if isinstance(source, dict):
                    session_state.remote_node_management_address = source
                if (
                    isinstance(session_state.remote_node_management_address, dict)
                    and not session_state.remote_discovery_requested
                ):
                    session_state.remote_discovery_requested = True
                    responses.extend(
                        [
                            build_read_datagram(
                                source=self._local_nm_address(),
                                destination=session_state.remote_node_management_address,
                                msg_counter=self._next_msg_counter(),
                                function_name="nodeManagementDetailedDiscoveryData",
                            ),
                        ]
                    )

        if header.get("cmdClassifier") == "write":
            notify_commands: list[dict[str, Any]] = []
            if not responses:
                responses.append(
                    build_result_datagram(
                        datagram,
                        source=local_source,
                        msg_counter=self._next_msg_counter(),
                    )
            )
            for command in commands:
                if "loadControlLimitListData" in command:
                    self._profile.load_control_limit_payload = self._merge_keyed_list_payload(
                        self._profile.load_control_limit_payload,
                        command["loadControlLimitListData"],
                        list_key="loadControlLimitData",
                        id_key="limitId",
                    )
                    notify_commands.append({"loadControlLimitListData": self._profile.load_control_limit_payload})
                    preferred_limit = self._extract_preferred_load_power_limit_state(
                        command["loadControlLimitListData"]
                    )
                    if preferred_limit is not None:
                        await self._emit(
                            "inbound_load_power_write",
                            {
                                **preferred_limit,
                                "peer_ski": session_state.peer_ski,
                                "limits": self._extract_load_control_limit_states(command["loadControlLimitListData"]),
                                "raw": command["loadControlLimitListData"],
                            },
                        )
                if "deviceConfigurationKeyValueListData" in command:
                    self._profile.device_configuration_payload = self._merge_keyed_list_payload(
                        self._profile.device_configuration_payload,
                        command["deviceConfigurationKeyValueListData"],
                        list_key="deviceConfigurationKeyValueData",
                        id_key="keyId",
                    )
                    notify_commands.append(
                        {"deviceConfigurationKeyValueListData": self._profile.device_configuration_payload}
                    )
            if notify_commands:
                responses.append(
                    build_datagram(
                        source=local_source,
                        destination=header.get("addressSource", {}),
                        cmd_classifier="notify",
                        msg_counter=self._next_msg_counter(),
                        commands=notify_commands,
                        specification_version=header.get("specificationVersion", "1.3.0"),
                    )
                )

        if header.get("cmdClassifier") == "call":
            self._record_node_management_calls(
                commands,
                source_device=source.get("device") if isinstance(source, dict) else None,
                session_state=session_state,
            )

        for payload in extract_discovery_payloads(datagram):
            if not isinstance(payload, dict):
                continue
            if "featureInformation" in payload:
                session_state.last_remote_discovery = self._merge_remote_discovery(
                    session_state.last_remote_discovery,
                    payload,
                )
                if isinstance(source, dict):
                    session_state.remote_node_management_address = source
                responses.extend(self._bootstrap_from_remote_discovery(session_state))

        if header.get("cmdClassifier") in {"reply", "result"}:
            for command in commands:
                binding_data = command.get("nodeManagementBindingData")
                if not isinstance(binding_data, dict):
                    result = command.get("resultData")
                    if not isinstance(result, dict):
                        load_control_state = command.get("loadControlLimitListData")
                        if header.get("cmdClassifier") == "reply":
                            msg_counter_reference = header.get("msgCounterReference")
                            if isinstance(msg_counter_reference, int):
                                pending_readback = session_state.pending_load_power_readbacks.pop(
                                    msg_counter_reference, None
                                )
                                if (
                                    pending_readback is None
                                    and msg_counter_reference == session_state.load_power_readback_msg_counter
                                ):
                                    pending_readback = {
                                        "msg_counter_reference": session_state.load_power_readback_msg_counter,
                                        "peer_ski": session_state.peer_ski,
                                    }
                                if pending_readback is not None:
                                    state = self._extract_load_control_limit_state(load_control_state)
                                    if state is not None:
                                        await self._emit(
                                            "load_power_readback",
                                            {
                                                **pending_readback,
                                                **state,
                                                "msg_counter_reference": msg_counter_reference,
                                            },
                                        )
                        continue
                    msg_counter_reference = header.get("msgCounterReference")
                    if not isinstance(msg_counter_reference, int):
                        continue
                    pending_write = session_state.pending_load_power_writes.pop(msg_counter_reference, None)
                    if pending_write is not None:
                        await self._emit(
                            "load_power_write_result",
                            {
                                **pending_write,
                                "msg_counter_reference": msg_counter_reference,
                                "error_number": result.get("errorNumber"),
                                "description": result.get("description"),
                            },
                        )
                    elif msg_counter_reference == session_state.load_power_write_msg_counter:
                        await self._emit(
                            "load_power_write_result",
                            {
                                "peer_ski": session_state.peer_ski,
                                "msg_counter_reference": msg_counter_reference,
                                "error_number": result.get("errorNumber"),
                                "description": result.get("description"),
                            },
                        )
                    feature_type = session_state.pending_binding_requests.pop(msg_counter_reference, None)
                    if feature_type is None or result.get("errorNumber") != 0:
                        continue
                    responses.extend(self._bootstrap_common_after_binding(session_state))
                    responses.extend(self._bootstrap_feature_after_binding(session_state, feature_type))
                    continue
                binding_entries = binding_data.get("bindingEntry")
                if isinstance(binding_entries, list) and binding_entries:
                    responses.extend(self._bootstrap_after_binding(session_state))
                continue

        if header.get("cmdClassifier") == "reply":
            for command in commands:
                if "loadControlLimitListData" not in command:
                    continue
                if self.config.send_load_power_limit_watts is None or session_state.load_power_command_sent:
                    continue
                if not isinstance(source, dict):
                    continue
                write_datagram = self._build_load_power_write_datagram(source)
                readback_datagram = self._build_load_control_read_datagram(source)
                session_state.load_power_write_msg_counter = extract_header(write_datagram).get("msgCounter")
                session_state.load_power_readback_msg_counter = extract_header(readback_datagram).get("msgCounter")
                responses.append(write_datagram)
                responses.append(readback_datagram)
                session_state.load_power_command_sent = True
                post_send_events.append(
                    (
                        "load_power_write_sent",
                        {
                            "limit_id": self.config.send_load_power_limit_id,
                            "watts": self.config.send_load_power_limit_watts,
                            "duration_seconds": self.config.send_load_power_duration_seconds,
                            "msg_counter": session_state.load_power_write_msg_counter,
                            "readback_msg_counter": session_state.load_power_readback_msg_counter,
                        },
                    )
                )

        for response in responses:
            await self._send_data(connection, response)
        for kind, payload in post_send_events:
            await self._emit(kind, payload)
        await self._emit("summary", {"commands": [key for cmd in commands for key in cmd]})

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        connection = ServerWebSocketConnection(reader, writer)
        session_state = _LocalSessionState()
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            await connection.handshake(expected_path=self.config.path)
            peer_ski = None
            with contextlib.suppress(Exception):
                peer_ski = extract_ski_from_peer_cert(connection.peer_certificate_der())
            normalized_peer_ski = normalize_ski(peer_ski)
            self.trace.log("server_tls_connected", peer_ski=peer_ski)
            if self._trusted_client_skis:
                if normalized_peer_ski is None:
                    raise ConnectionError("incoming peer certificate SKI unavailable")
                if normalized_peer_ski not in self._trusted_client_skis:
                    raise ConnectionError(
                        f"untrusted incoming peer SKI {normalized_peer_ski}"
                    )
            session_state.peer_ski = normalized_peer_ski or peer_ski
            await self._emit("connected", {"peer_ski": peer_ski})

            msg_type, body = await self._receive_message(connection)
            if msg_type != SHIP_MSG_INIT:
                raise ConnectionError("expected initial CMI message")
            await connection.send_binary(bytes([SHIP_MSG_INIT, 0x00]))

            pending_payload = await self._perform_hello_handshake(connection)
            pending_payload = await self._perform_protocol_handshake(
                connection,
                initial_payload=pending_payload,
            )
            pending_payload = await self._perform_pin_handshake(
                connection,
                initial_payload=pending_payload,
            )
            remote_ship_id = await self._perform_access_methods_handshake(
                connection,
                initial_payload=pending_payload,
            )
            await self._emit(
                "ready",
                {
                    "ship_id": self.config.ship_id,
                    "peer_ski": peer_ski,
                    "remote_ship_id": remote_ship_id,
                },
            )
            if normalized_peer_ski is not None:
                self._active_sessions[normalized_peer_ski] = _ActivePeerSession(
                    peer_ski=normalized_peer_ski,
                    connection=connection,
                    session_state=session_state,
                )
            heartbeat_task = asyncio.create_task(
                self._publish_device_diagnosis_heartbeats(connection, session_state)
            )

            while True:
                msg_type, payload = await self._receive_message(connection)
                if msg_type == SHIP_MSG_DATA:
                    await self._handle_spine_data(
                        connection,
                        SpineDatagram.from_ship_payload(payload),
                        session_state=session_state,
                    )
                    continue
                if msg_type == SHIP_MSG_CONTROL:
                    self.trace.log("server_rx_control", payload=payload)
                    await self._emit("control", payload)
        except Exception as exc:
            self.trace.log("server_connection_closed", error=str(exc))
            await self._emit("closed", {"error": str(exc), "peer_ski": session_state.peer_ski})
        finally:
            if session_state.peer_ski is not None:
                self._active_sessions.pop(session_state.peer_ski, None)
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
            await connection.close()

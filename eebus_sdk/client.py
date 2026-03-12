"""High-level HEMS client built on top of a SHIP session."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

from .discovery import ShipService, discover_ship_services
from .identity import IdentityMaterial
from .ship import ShipConnectionConfig, ShipEvent, ShipSession
from .spine import (
    SpineDatagram,
    build_read_datagram,
    build_reply_datagram,
    build_result_datagram,
    extract_commands,
    extract_discovery_payloads,
    extract_header,
    extract_measurement_descriptions,
    extract_measurement_payloads,
    is_measurement_datagram,
)
from .trace import TraceLogger
from .trust import TrustStore


def _sanitize_identifier(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return sanitized or "HEMS"


@dataclass(slots=True)
class HemsClient:
    session: ShipSession
    service: ShipService
    identity: IdentityMaterial
    trust: TrustStore
    interface_ip: str | None = None
    _spine_msg_counter: int = field(default=1, init=False, repr=False)
    _remote_device_address: str | None = field(default=None, init=False, repr=False)

    @classmethod
    async def connect(
        cls,
        service: ShipService,
        identity: IdentityMaterial,
        trust: TrustStore,
        *,
        interface_ip: str | None = None,
        trace_logger: TraceLogger | None = None,
        pairing_wait_seconds: int = 60,
        timeout: float = 10.0,
    ) -> "HemsClient":
        if service.port is None:
            raise ValueError(f"{service.service_name} does not advertise a port")
        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=service.preferred_host(),
                port=service.port,
                path=service.path,
                server_name=service.server_name(),
                timeout=timeout,
                pairing_wait_seconds=pairing_wait_seconds,
            ),
            identity,
            trust,
            trace_logger=trace_logger,
        )
        return cls(session=session, service=service, identity=identity, trust=trust, interface_ip=interface_ip)

    async def close(self) -> None:
        await self.session.close()

    async def reconnect(self, *, timeout: float | None = None) -> None:
        await self.close()
        service = self.service
        if self.interface_ip is not None:
            services = await asyncio.to_thread(discover_ship_services, self.interface_ip, timeout=timeout or 3.0)
            for candidate in services:
                if candidate.service_name == self.service.service_name:
                    service = candidate
                    break
        refreshed = await self.connect(
            service,
            self.identity,
            self.trust,
            interface_ip=self.interface_ip,
            pairing_wait_seconds=self.session.config.pairing_wait_seconds,
            timeout=timeout or self.session.config.timeout,
        )
        self.session = refreshed.session
        self.service = refreshed.service

    def local_device_address(self) -> str:
        suffix = _sanitize_identifier(self.identity.device_id)[:48]
        return f"d:_n:HEMS_PythonSDK-{suffix}"

    def local_node_management_address(self) -> dict[str, Any]:
        return {"device": self.local_device_address(), "entity": [0], "feature": 0}

    def local_measurement_client_address(self) -> dict[str, Any]:
        return {"device": self.local_device_address(), "entity": [1], "feature": 2}

    def local_electrical_connection_client_address(self) -> dict[str, Any]:
        return {"device": self.local_device_address(), "entity": [1], "feature": 1}

    def _next_msg_counter(self) -> int:
        value = self._spine_msg_counter
        self._spine_msg_counter += 1
        return value

    def _local_source_for_destination(self, destination: dict[str, Any]) -> dict[str, Any]:
        source: dict[str, Any] = {"device": self.local_device_address()}
        if "entity" in destination:
            source["entity"] = destination["entity"]
        if "feature" in destination:
            source["feature"] = destination["feature"]
        return source

    def build_local_detailed_discovery(self) -> dict[str, Any]:
        local_device = self.local_device_address()
        return {
            "specificationVersionList": {"specificationVersion": ["1.3.0"]},
            "deviceInformation": {
                "description": {
                    "deviceAddress": {"device": local_device},
                    "deviceType": "EnergyManagementSystem",
                    "networkFeatureSet": "smart",
                }
            },
            "entityInformation": [
                {
                    "description": {
                        "entityAddress": {"device": local_device, "entity": [0]},
                        "entityType": "DeviceInformation",
                    }
                },
                {
                    "description": {
                        "entityAddress": {"device": local_device, "entity": [1]},
                        "entityType": "CEM",
                    }
                },
            ],
            "featureInformation": [
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [0], "feature": 0},
                        "featureType": "NodeManagement",
                        "role": "special",
                        "supportedFunction": [
                            {
                                "function": "nodeManagementDetailedDiscoveryData",
                                "possibleOperations": {"read": {}},
                            },
                            {
                                "function": "nodeManagementUseCaseData",
                                "possibleOperations": {"read": {}},
                            },
                        ],
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [0], "feature": 1},
                        "featureType": "DeviceClassification",
                        "role": "server",
                        "supportedFunction": [
                            {
                                "function": "deviceClassificationManufacturerData",
                                "possibleOperations": {"read": {}},
                            }
                        ],
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 1},
                        "featureType": "ElectricalConnection",
                        "role": "client",
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 2},
                        "featureType": "Measurement",
                        "role": "client",
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 3},
                        "featureType": "LoadControl",
                        "role": "client",
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 4},
                        "featureType": "DeviceDiagnosis",
                        "role": "client",
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": local_device, "entity": [1], "feature": 5},
                        "featureType": "DeviceConfiguration",
                        "role": "client",
                    }
                },
            ],
        }

    async def _receive_and_process(self, *, timeout: float | None = None) -> SpineDatagram:
        datagram = await self.session.receive_datagram(timeout=timeout)
        await self.handle_incoming_datagram(datagram)
        return datagram

    async def handle_incoming_datagram(self, datagram: SpineDatagram) -> list[SpineDatagram]:
        header = extract_header(datagram)
        source = header.get("addressSource", {})
        destination = header.get("addressDestination", {})
        if isinstance(source, dict) and isinstance(source.get("device"), str):
            self._remote_device_address = source["device"]

        outgoing: list[SpineDatagram] = []
        local_source = self._local_source_for_destination(destination if isinstance(destination, dict) else {})
        if header.get("ackRequest"):
            outgoing.append(
                build_result_datagram(
                    datagram,
                    source=local_source,
                    msg_counter=self._next_msg_counter(),
                )
            )

        if header.get("cmdClassifier") == "read":
            for command in extract_commands(datagram):
                if "nodeManagementDetailedDiscoveryData" in command:
                    outgoing.append(
                        build_reply_datagram(
                            datagram,
                            source=self.local_node_management_address(),
                            msg_counter=self._next_msg_counter(),
                            commands=[
                                {
                                    "nodeManagementDetailedDiscoveryData": self.build_local_detailed_discovery()
                                }
                            ],
                        )
                    )

        for response in outgoing:
            await self.session.send_spine(response)
        return outgoing

    async def bootstrap_spine(self, *, timeout: float = 3.0) -> list[SpineDatagram]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        received: list[SpineDatagram] = []
        while loop.time() < deadline:
            remaining = max(0.1, deadline - loop.time())
            try:
                datagram = await self._receive_and_process(timeout=remaining)
            except asyncio.TimeoutError:
                break
            received.append(datagram)
        return received

    async def _collect_matching_payloads(
        self,
        *,
        extractor: Callable[[SpineDatagram], list[Any]],
        timeout: float,
    ) -> list[dict[str, Any]]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        matches: list[dict[str, Any]] = []
        while loop.time() < deadline:
            remaining = max(0.1, deadline - loop.time())
            datagram = await self._receive_and_process(timeout=remaining)
            for payload in extractor(datagram):
                if isinstance(payload, dict):
                    matches.append(payload)
            if matches:
                return matches
        return matches

    async def request_remote_detailed_discovery(self, *, timeout: float = 5.0) -> list[dict[str, Any]]:
        if self._remote_device_address is None:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + min(timeout, 1.0)
            while self._remote_device_address is None and loop.time() < deadline:
                remaining = max(0.1, deadline - loop.time())
                await self._receive_and_process(timeout=remaining)
        if self._remote_device_address is None:
            raise ValueError("remote device address is unknown; no SPINE datagram received from peer yet")

        await self.session.send_spine(
            build_read_datagram(
                source=self.local_node_management_address(),
                destination={"device": self._remote_device_address, "entity": [0], "feature": 0},
                msg_counter=self._next_msg_counter(),
                function_name="nodeManagementDetailedDiscoveryData",
            )
        )
        return await self._collect_matching_payloads(
            extractor=lambda datagram: extract_discovery_payloads(datagram),
            timeout=timeout,
        )

    def _feature_addresses(
        self,
        discovery_payload: dict[str, Any],
        *,
        feature_type: str,
        role: str | None = None,
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
            normalized = dict(address)
            if "device" not in normalized and self._remote_device_address is not None:
                normalized["device"] = self._remote_device_address
            results.append(normalized)
        return results

    async def _request_function_data(
        self,
        *,
        source: dict[str, Any],
        destination: dict[str, Any],
        function_name: str,
        extractor: Callable[[SpineDatagram], list[Any]],
        timeout: float,
    ) -> list[dict[str, Any]]:
        await self.session.send_spine(
            build_read_datagram(
                source=source,
                destination=destination,
                msg_counter=self._next_msg_counter(),
                function_name=function_name,
            )
        )
        return await self._collect_matching_payloads(extractor=extractor, timeout=timeout)

    async def discover_nodes(self, *, timeout: float = 5.0) -> list[dict]:
        return await self.request_remote_detailed_discovery(timeout=timeout)

    async def read_measurements(self, *, timeout: float = 5.0, limit: int = 1) -> list[SpineDatagram]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        measurements: list[SpineDatagram] = []
        while loop.time() < deadline and len(measurements) < limit:
            remaining = max(0.1, deadline - loop.time())
            datagram = await self._receive_and_process(timeout=remaining)
            if is_measurement_datagram(datagram):
                measurements.append(datagram)
        return measurements

    async def read_remote_measurements(self, *, timeout: float = 10.0) -> dict[str, Any]:
        discovery_payloads = await self.request_remote_detailed_discovery(timeout=max(1.0, timeout / 3))
        discovery = discovery_payloads[-1] if discovery_payloads else {}
        measurement_features = self._feature_addresses(discovery, feature_type="Measurement", role="server")

        descriptions: list[dict[str, Any]] = []
        values: list[dict[str, Any]] = []
        for address in measurement_features:
            if not descriptions:
                descriptions = await self._request_function_data(
                    source=self.local_measurement_client_address(),
                    destination=address,
                    function_name="measurementDescriptionListData",
                    extractor=lambda datagram: extract_measurement_descriptions(datagram),
                    timeout=max(1.0, timeout / 3),
                )
            if not values:
                values = await self._request_function_data(
                    source=self.local_measurement_client_address(),
                    destination=address,
                    function_name="measurementListData",
                    extractor=lambda datagram: extract_measurement_payloads(datagram),
                    timeout=max(1.0, timeout / 3),
                )
            if values:
                break

        return {
            "remote_device_address": self._remote_device_address,
            "discovery": discovery,
            "measurement_features": measurement_features,
            "measurement_descriptions": descriptions,
            "measurement_payloads": values,
        }

    async def subscribe_updates(self) -> AsyncIterator[SpineDatagram]:
        async for event in self.session.events():
            if event.kind == "datagram":
                yield event.payload

    async def send_control_command(self, payload: SpineDatagram | dict) -> None:
        await self.session.send_spine(payload)

    async def session_events(self) -> AsyncIterator[ShipEvent]:
        async for event in self.session.events():
            yield event

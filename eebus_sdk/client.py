"""High-level HEMS client built on top of a SHIP session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import AsyncIterator

from .discovery import ShipService, discover_ship_services
from .identity import IdentityMaterial
from .ship import ShipConnectionConfig, ShipEvent, ShipSession
from .spine import SpineDatagram, extract_discovery_payloads, is_measurement_datagram
from .trace import TraceLogger
from .trust import TrustStore


@dataclass(slots=True)
class HemsClient:
    session: ShipSession
    service: ShipService
    identity: IdentityMaterial
    trust: TrustStore
    interface_ip: str | None = None

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

    async def discover_nodes(self, *, timeout: float = 5.0) -> list[dict]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        results: list[dict] = []
        while loop.time() < deadline:
            remaining = max(0.1, deadline - loop.time())
            datagram = await self.session.receive_datagram(timeout=remaining)
            for payload in extract_discovery_payloads(datagram):
                if isinstance(payload, dict):
                    results.append(payload)
            if results:
                return results
        return results

    async def read_measurements(self, *, timeout: float = 5.0, limit: int = 1) -> list[SpineDatagram]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        measurements: list[SpineDatagram] = []
        while loop.time() < deadline and len(measurements) < limit:
            remaining = max(0.1, deadline - loop.time())
            datagram = await self.session.receive_datagram(timeout=remaining)
            if is_measurement_datagram(datagram):
                measurements.append(datagram)
        return measurements

    async def subscribe_updates(self) -> AsyncIterator[SpineDatagram]:
        async for event in self.session.events():
            if event.kind == "datagram":
                yield event.payload

    async def send_control_command(self, payload: SpineDatagram | dict) -> None:
        await self.session.send_spine(payload)

    async def session_events(self) -> AsyncIterator[ShipEvent]:
        async for event in self.session.events():
            yield event

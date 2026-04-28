"""mDNS advertisement helpers for local SHIP services."""

from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass, field

from zeroconf import IPVersion, ServiceInfo, Zeroconf


def build_ship_txt_properties(
    *,
    ski: str,
    ship_id: str,
    path: str = "/ship/",
    brand: str = "EEBUS-SDK",
    model: str = "ReferenceHEMS",
    device_type: str = "DeviceTypeTypeEnergyManagementSystem",
    category: str = "DeviceCategoryTypeEnergyManagementSystem",
    register: bool = True,
    extra: dict[str, str] | None = None,
) -> dict[bytes, bytes]:
    txt: dict[str, str] = {
        "txtvers": "1",
        "path": path,
        "id": ship_id,
        "ski": ski,
        "brand": brand,
        "model": model,
        "type": device_type,
        "category": category,
        "register": "true" if register else "false",
    }
    if extra:
        txt.update(extra)
    return {key.encode("utf-8"): value.encode("utf-8") for key, value in txt.items()}


@dataclass(slots=True)
class ShipServiceAdvertisement:
    interface_ip: str
    port: int
    ski: str
    ship_id: str
    device_id: str = "EEBUS-SDK-SERVER"
    instance_name: str | None = None
    server_name: str | None = None
    path: str = "/ship/"
    brand: str = "EEBUS-SDK"
    model: str = "ReferenceHEMS"
    device_type: str = "DeviceTypeTypeEnergyManagementSystem"
    category: str = "DeviceCategoryTypeEnergyManagementSystem"
    register: bool = True
    extra_txt: dict[str, str] = field(default_factory=dict)

    def service_name(self) -> str:
        name = self.instance_name or self.device_id
        if name.endswith("._ship._tcp.local."):
            return name
        return f"{name}._ship._tcp.local."

    def host_name(self) -> str:
        if self.server_name:
            return self.server_name if self.server_name.endswith(".") else f"{self.server_name}."
        return f"{socket.gethostname()}.local."


class ShipServiceAdvertiser:
    def __init__(self, config: ShipServiceAdvertisement) -> None:
        self.config = config
        self._zeroconf: Zeroconf | None = None
        self._service_info: ServiceInfo | None = None

    async def start(self) -> None:
        info = ServiceInfo(
            "_ship._tcp.local.",
            self.config.service_name(),
            addresses=[socket.inet_aton(self.config.interface_ip)],
            port=self.config.port,
            properties=build_ship_txt_properties(
                ski=self.config.ski,
                ship_id=self.config.ship_id,
                path=self.config.path,
                brand=self.config.brand,
                model=self.config.model,
                device_type=self.config.device_type,
                category=self.config.category,
                register=self.config.register,
                extra=self.config.extra_txt,
            ),
            server=self.config.host_name(),
        )
        zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        await asyncio.to_thread(zeroconf.register_service, info, allow_name_change=False)
        self._zeroconf = zeroconf
        self._service_info = info

    async def stop(self) -> None:
        if self._zeroconf is None or self._service_info is None:
            return
        await asyncio.to_thread(self._zeroconf.unregister_service, self._service_info)
        self._zeroconf.close()
        self._zeroconf = None
        self._service_info = None

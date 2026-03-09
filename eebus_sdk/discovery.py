"""mDNS / DNS-SD discovery for SHIP services."""

from __future__ import annotations

import re
import shutil
import socket
import struct
import subprocess
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from .exceptions import DiscoveryError
from .identity import normalize_ski

MCAST_GRP = "224.0.0.251"
MCAST_PORT = 5353
SERVICE_TYPE = "_ship._tcp.local"

TYPE_A = 1
TYPE_PTR = 12
TYPE_TXT = 16
TYPE_AAAA = 28
TYPE_SRV = 33


@dataclass(slots=True)
class ShipService:
    service_name: str
    target: str | None = None
    port: int | None = None
    path: str = "/ship/"
    ship_id: str | None = None
    ski: str | None = None
    brand: str | None = None
    model: str | None = None
    device_type: str | None = None
    register: bool | None = None
    addresses: dict[str, list[str]] = field(default_factory=lambda: {"ipv4": [], "ipv6": []})
    txt: dict[str, str] = field(default_factory=dict)
    tls_probe: dict[str, Any] | None = None

    def preferred_host(self) -> str:
        if self.addresses["ipv4"]:
            return self.addresses["ipv4"][0]
        if self.target:
            return self.target
        raise DiscoveryError(f"{self.service_name} does not advertise a usable host")

    def server_name(self) -> str:
        return self.target or self.preferred_host()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def encode_name(name: str) -> bytes:
    return b"".join(bytes([len(label)]) + label.encode("utf-8") for label in name.split(".")) + b"\x00"


def read_name(packet: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    jumped = False
    next_offset = offset

    while True:
        length = packet[offset]
        if length == 0:
            offset += 1
            if not jumped:
                next_offset = offset
            break

        if length & 0xC0 == 0xC0:
            pointer = ((length & 0x3F) << 8) | packet[offset + 1]
            if not jumped:
                next_offset = offset + 2
            jumped = True
            offset = pointer
            continue

        offset += 1
        labels.append(packet[offset : offset + length].decode("utf-8", "replace"))
        offset += length
        if not jumped:
            next_offset = offset

    return ".".join(labels), next_offset


def parse_txt_rdata(rdata: bytes) -> dict[str, str]:
    values: dict[str, str] = {}
    index = 0
    while index < len(rdata):
        length = rdata[index]
        index += 1
        value = rdata[index : index + length].decode("utf-8", "replace")
        index += length
        if "=" in value:
            key, raw = value.split("=", 1)
            values[key] = raw
        else:
            values[value] = ""
    return values


def parse_rrs(packet: bytes) -> list[dict[str, Any]]:
    _, _, qd_count, an_count, ns_count, ar_count = struct.unpack("!HHHHHH", packet[:12])
    offset = 12
    for _ in range(qd_count):
        _, offset = read_name(packet, offset)
        offset += 4

    records: list[dict[str, Any]] = []
    for _ in range(an_count + ns_count + ar_count):
        name, offset = read_name(packet, offset)
        rr_type, rr_class, ttl, rdlength = struct.unpack("!HHIH", packet[offset : offset + 10])
        offset += 10
        rdata_offset = offset
        rdata = packet[offset : offset + rdlength]
        offset += rdlength

        if rr_type == TYPE_PTR:
            data, _ = read_name(packet, rdata_offset)
        elif rr_type == TYPE_SRV:
            priority, weight, port = struct.unpack("!HHH", rdata[:6])
            target, _ = read_name(packet, rdata_offset + 6)
            data = {
                "priority": priority,
                "weight": weight,
                "port": port,
                "target": target,
            }
        elif rr_type == TYPE_TXT:
            data = parse_txt_rdata(rdata)
        elif rr_type == TYPE_A:
            data = socket.inet_ntoa(rdata)
        elif rr_type == TYPE_AAAA:
            data = socket.inet_ntop(socket.AF_INET6, rdata)
        else:
            data = rdata.hex()

        records.append(
            {
                "name": name,
                "type": rr_type,
                "class": rr_class,
                "ttl": ttl,
                "data": data,
            }
        )

    return records


def detect_interface_ip() -> str:
    for host, port in ((MCAST_GRP, MCAST_PORT), ("1.1.1.1", 53)):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((host, port))
            local_ip = sock.getsockname()[0]
            if local_ip and not local_ip.startswith("127."):
                return local_ip
        except OSError:
            pass
        finally:
            sock.close()
    raise DiscoveryError("could not detect an IPv4 interface automatically; pass an explicit interface IP")


def build_query(questions: list[tuple[str, int]]) -> bytes:
    header = b"\x00\x00\x00\x00" + struct.pack("!H", len(questions)) + b"\x00\x00\x00\x00\x00\x00"
    body = b"".join(encode_name(name) + struct.pack("!HH", rr_type, 1) for name, rr_type in questions)
    return header + body


def create_mdns_socket(interface_ip: str) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, "SO_REUSEPORT"):
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        except OSError:
            pass
    sock.bind(("", MCAST_PORT))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(interface_ip))
    membership = socket.inet_aton(MCAST_GRP) + socket.inet_aton(interface_ip)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
    return sock


def mdns_query(interface_ip: str, questions: list[tuple[str, int]], timeout: float) -> list[tuple[str, bytes]]:
    sock = create_mdns_socket(interface_ip)
    sock.settimeout(timeout)
    try:
        sock.sendto(build_query(questions), (MCAST_GRP, MCAST_PORT))
        responses: list[tuple[str, bytes]] = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            remaining = max(0.05, deadline - time.time())
            sock.settimeout(remaining)
            try:
                data, addr = sock.recvfrom(9000)
            except socket.timeout:
                break
            if addr[0] == interface_ip:
                continue
            responses.append((addr[0], data))
        return responses
    finally:
        sock.close()


def _extract_first_pem(text: str) -> str | None:
    match = re.search(
        r"(-----BEGIN CERTIFICATE-----\s+.*?-----END CERTIFICATE-----)",
        text,
        flags=re.DOTALL,
    )
    return match.group(1) if match else None


def _probe_tls(host: str, port: int, server_name: str | None, txt_ski: str | None, timeout: float) -> dict[str, Any]:
    if shutil.which("openssl") is None:
        return {"available": False, "error": "openssl not found"}

    cmd = [
        "openssl",
        "s_client",
        "-connect",
        f"{host}:{port}",
        "-servername",
        server_name or host,
        "-showcerts",
    ]
    result = subprocess.run(
        cmd,
        input=b"",
        capture_output=True,
        timeout=max(3.0, timeout),
        check=False,
    )
    combined = (result.stdout + result.stderr).decode("utf-8", "replace")
    pem = _extract_first_pem(combined)
    info: dict[str, Any] = {
        "available": True,
        "openssl_exit_code": result.returncode,
        "client_cert_requested": "Client Certificate Types:" in combined,
        "handshake_failure": "alert handshake failure" in combined.lower(),
    }
    if pem is None:
        info["error"] = "no certificate returned by peer"
        return info

    cert = subprocess.run(
        ["openssl", "x509", "-noout", "-subject", "-issuer", "-dates", "-fingerprint", "-sha256", "-text"],
        input=pem.encode("utf-8"),
        capture_output=True,
        timeout=max(3.0, timeout),
        check=False,
    )
    cert_text = cert.stdout.decode("utf-8", "replace")
    ski_match = re.search(r"Subject Key Identifier:\s*\n\s*([0-9A-F:]+)", cert_text)
    subject_match = re.search(r"^subject=(.+)$", cert_text, flags=re.MULTILINE)
    issuer_match = re.search(r"^issuer=(.+)$", cert_text, flags=re.MULTILINE)
    not_before_match = re.search(r"^notBefore=(.+)$", cert_text, flags=re.MULTILINE)
    not_after_match = re.search(r"^notAfter=(.+)$", cert_text, flags=re.MULTILINE)
    fingerprint_match = re.search(r"^sha256 Fingerprint=(.+)$", cert_text, flags=re.MULTILINE)
    cert_ski = normalize_ski(ski_match.group(1) if ski_match else None)
    info.update(
        {
            "subject": subject_match.group(1).strip() if subject_match else None,
            "issuer": issuer_match.group(1).strip() if issuer_match else None,
            "not_before": not_before_match.group(1).strip() if not_before_match else None,
            "not_after": not_after_match.group(1).strip() if not_after_match else None,
            "sha256_fingerprint": fingerprint_match.group(1).strip() if fingerprint_match else None,
            "cert_ski": cert_ski,
            "txt_ski_matches_cert_ski": cert_ski == normalize_ski(txt_ski),
        }
    )
    return info


def discover_ship_services(
    interface_ip: str | None = None,
    *,
    timeout: float = 3.0,
    tls_check: bool = False,
) -> list[ShipService]:
    interface_ip = interface_ip or detect_interface_ip()
    services: dict[str, dict[str, Any]] = {}
    host_records: dict[str, dict[str, set[str]]] = {}

    def ensure_service(service_name: str) -> dict[str, Any]:
        return services.setdefault(service_name, {"service_name": service_name, "txt": {}})

    def ensure_host(target: str) -> dict[str, set[str]]:
        return host_records.setdefault(target, {"ipv4": set(), "ipv6": set()})

    def consume_records(records: list[dict[str, Any]]) -> None:
        for rr in records:
            if rr["type"] == TYPE_PTR and rr["name"] == SERVICE_TYPE:
                ensure_service(str(rr["data"]))

        for rr in records:
            service = services.get(rr["name"])
            if service is None:
                continue
            if rr["type"] == TYPE_TXT:
                service["txt"] = rr["data"]
            elif rr["type"] == TYPE_SRV:
                service.update(rr["data"])
                ensure_host(rr["data"]["target"])

        for rr in records:
            host = host_records.get(rr["name"])
            if host is None:
                continue
            if rr["type"] == TYPE_A:
                host["ipv4"].add(rr["data"])
            elif rr["type"] == TYPE_AAAA:
                host["ipv6"].add(rr["data"])

    for _, packet in mdns_query(interface_ip, [(SERVICE_TYPE, TYPE_PTR)], timeout):
        consume_records(parse_rrs(packet))

    if not services:
        return []

    instance_questions: list[tuple[str, int]] = []
    for service_name in services:
        instance_questions.extend([(service_name, TYPE_TXT), (service_name, TYPE_SRV)])

    for _, packet in mdns_query(interface_ip, instance_questions, timeout):
        consume_records(parse_rrs(packet))

    if host_records:
        host_questions: list[tuple[str, int]] = []
        for target in host_records:
            host_questions.extend([(target, TYPE_A), (target, TYPE_AAAA)])
        for _, packet in mdns_query(interface_ip, host_questions, timeout):
            consume_records(parse_rrs(packet))

    discovered: list[ShipService] = []
    for service_name, raw in sorted(services.items()):
        target = raw.get("target")
        addresses = host_records.get(target, {"ipv4": set(), "ipv6": set()}) if target else {"ipv4": set(), "ipv6": set()}
        txt = raw.get("txt", {})
        service = ShipService(
            service_name=service_name,
            target=target,
            port=raw.get("port"),
            path=txt.get("path", "/ship/"),
            ship_id=txt.get("id"),
            ski=normalize_ski(txt.get("ski")),
            brand=txt.get("brand"),
            model=txt.get("model"),
            device_type=txt.get("type"),
            register=txt.get("register", "").lower() == "true" if "register" in txt else None,
            addresses={"ipv4": sorted(addresses["ipv4"]), "ipv6": sorted(addresses["ipv6"])},
            txt=txt,
        )
        if tls_check and service.port is not None:
            try:
                service.tls_probe = _probe_tls(
                    host=service.preferred_host(),
                    port=service.port,
                    server_name=service.target,
                    txt_ski=service.ski,
                    timeout=timeout + 5.0,
                )
            except Exception as exc:
                service.tls_probe = {"available": False, "error": str(exc)}
        discovered.append(service)

    return discovered

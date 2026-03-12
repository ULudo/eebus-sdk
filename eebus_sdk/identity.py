"""Identity and certificate helpers for SHIP / EEBus clients."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from .exceptions import IdentityError


def default_device_id() -> str:
    hostname = socket.gethostname().upper().replace(".", "-")
    sanitized = re.sub(r"[^A-Z0-9_-]", "", hostname)
    return sanitized or "HEMSLAB"


def default_ship_id(device_id: str) -> str:
    return f"i:32266_u:{device_id}_r:HEMS"


def normalize_ski(value: str | None) -> str | None:
    if value is None:
        return None
    return value.replace(":", "").replace(" ", "").strip().lower()


def openssl_available() -> bool:
    return shutil.which("openssl") is not None


def extract_ski_from_cert(cert_path: str) -> str:
    result = subprocess.run(
        ["openssl", "x509", "-in", cert_path, "-noout", "-text"],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"Subject Key Identifier:\s*\n\s*([0-9A-F:]+)", result.stdout)
    if not match:
        raise IdentityError(f"could not extract SKI from {cert_path}")
    return normalize_ski(match.group(1)) or ""


def extract_common_name_from_cert(cert_path: str) -> str:
    result = subprocess.run(
        ["openssl", "x509", "-in", cert_path, "-noout", "-subject"],
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"CN\s*=\s*([^,\n/]+)", result.stdout)
    if not match:
        raise IdentityError(f"could not extract Common Name from {cert_path}")
    return match.group(1).strip()


def extract_ski_from_peer_cert(der_cert: bytes) -> str:
    pem = ssl.DER_cert_to_PEM_cert(der_cert)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".pem") as handle:
        handle.write(pem)
        temp_path = handle.name
    try:
        return extract_ski_from_cert(temp_path)
    finally:
        os.unlink(temp_path)


def build_qr_payload(ship_id: str, ski: str, brand: str, model: str, device_type: str) -> str:
    grouped = " ".join(ski[i : i + 4].upper() for i in range(0, len(ski), 4))
    return f"ID:{ship_id};SKI:{grouped};BRAND:{brand};TYPE:{device_type};MODEL:{model};"


@dataclass(slots=True)
class IdentityMaterial:
    ship_id: str
    device_id: str
    common_name: str
    ski: str
    cert_path: str
    key_path: str
    qr_payload: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class IdentityStore:
    """Create, persist, and load SHIP client identities."""

    @staticmethod
    def create(
        out_dir: str,
        device_id: str | None = None,
        ship_id: str | None = None,
        country: str = "DE",
        brand: str = "HEMS",
        model: str = "LabClient",
        device_type: str = "EnergyManagementSystem",
        serial_number: int = 1,
        overwrite: bool = False,
    ) -> IdentityMaterial:
        if not openssl_available():
            raise IdentityError("openssl is required to generate SHIP certificates")

        device_id = device_id or default_device_id()
        ship_id = ship_id or default_ship_id(device_id)
        common_name = f"{device_id}.cls"
        out = Path(out_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)

        key_path = out / "client.key.pem"
        cert_path = out / "client.crt.pem"
        openssl_cfg = out / "openssl.cnf"
        identity_path = out / "identity.json"

        if not overwrite and identity_path.exists():
            raise IdentityError(
                f"{identity_path} already exists; explicit rollover is required, pass overwrite=True to replace it"
            )

        openssl_cfg.write_text(
            f"""
[req]
distinguished_name = dn
x509_extensions = v3_req
prompt = no

[dn]
C = {country}
CN = {common_name}
serialNumber = {serial_number}
O = {brand}
OU = HEMS

[v3_req]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature
extendedKeyUsage = clientAuth,serverAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always
""".strip()
            + "\n",
            encoding="ascii",
        )

        subprocess.run(
            ["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key_path)],
            check=True,
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-x509",
                "-sha256",
                "-days",
                "3650",
                "-key",
                str(key_path),
                "-config",
                str(openssl_cfg),
                "-extensions",
                "v3_req",
                "-out",
                str(cert_path),
            ],
            check=True,
        )

        ski = extract_ski_from_cert(str(cert_path))
        identity = IdentityMaterial(
            ship_id=ship_id,
            device_id=device_id,
            common_name=common_name,
            ski=ski,
            cert_path=str(cert_path),
            key_path=str(key_path),
            qr_payload=build_qr_payload(ship_id, ski, brand=brand, model=model, device_type=device_type),
        )
        identity_path.write_text(json.dumps(identity.as_dict(), indent=2), encoding="utf-8")
        return identity

    @staticmethod
    def import_existing(
        out_dir: str,
        *,
        cert_path: str,
        key_path: str,
        ship_id: str,
        device_id: str | None = None,
        common_name: str | None = None,
        ski: str | None = None,
        brand: str = "HEMS",
        model: str = "ImportedClient",
        device_type: str = "EnergyManagementSystem",
        copy_files: bool = False,
        overwrite: bool = False,
    ) -> IdentityMaterial:
        if not openssl_available():
            raise IdentityError("openssl is required to import SHIP certificates")

        source_cert = Path(cert_path).resolve()
        source_key = Path(key_path).resolve()
        if not source_cert.exists():
            raise IdentityError(f"cert_path does not exist: {source_cert}")
        if not source_key.exists():
            raise IdentityError(f"key_path does not exist: {source_key}")

        resolved_common_name = common_name or extract_common_name_from_cert(str(source_cert))
        resolved_device_id = device_id or resolved_common_name.removesuffix(".cls")
        resolved_ski = normalize_ski(ski) or extract_ski_from_cert(str(source_cert))

        out = Path(out_dir).resolve()
        out.mkdir(parents=True, exist_ok=True)
        identity_path = out / "identity.json"
        target_cert = out / "client.crt.pem"
        target_key = out / "client.key.pem"

        if not overwrite and identity_path.exists():
            raise IdentityError(
                f"{identity_path} already exists; explicit rollover is required, pass overwrite=True to replace it"
            )

        if copy_files:
            shutil.copy2(source_cert, target_cert)
            shutil.copy2(source_key, target_key)
            final_cert = str(target_cert)
            final_key = str(target_key)
        else:
            final_cert = str(source_cert)
            final_key = str(source_key)

        identity = IdentityMaterial(
            ship_id=ship_id,
            device_id=resolved_device_id,
            common_name=resolved_common_name,
            ski=resolved_ski,
            cert_path=final_cert,
            key_path=final_key,
            qr_payload=build_qr_payload(
                ship_id,
                resolved_ski,
                brand=brand,
                model=model,
                device_type=device_type,
            ),
        )
        identity_path.write_text(json.dumps(identity.as_dict(), indent=2), encoding="utf-8")
        return identity

    @staticmethod
    def load(path: str | Path) -> IdentityMaterial:
        identity_path = Path(path).resolve()
        try:
            data = json.loads(identity_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise IdentityError(f"identity file not found: {identity_path}") from exc
        except json.JSONDecodeError as exc:
            raise IdentityError(f"identity file is invalid JSON: {identity_path}") from exc

        for key in ("cert_path", "key_path"):
            raw = Path(data[key])
            if not raw.is_absolute():
                data[key] = str((identity_path.parent / raw).resolve())

        identity = IdentityMaterial(**data)
        for key in ("cert_path", "key_path"):
            if not Path(getattr(identity, key)).exists():
                raise IdentityError(f"{key} does not exist: {getattr(identity, key)}")
        return identity

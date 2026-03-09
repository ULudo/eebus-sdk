"""Public SDK surface for Python EEBus / SHIP integrations."""

from .client import HemsClient
from .discovery import ShipService, discover_ship_services
from .exceptions import (
    CertificateMismatchError,
    DiscoveryError,
    EebusError,
    IdentityError,
    PairingRejectedError,
    ReplayError,
    ShipError,
    ShipHandshakeError,
    TransportError,
    TrustError,
    WebSocketProtocolError,
)
from .identity import IdentityMaterial, IdentityStore
from .json_codec import from_eebus_json_bytes, to_eebus_json_bytes
from .replay import load_trace, summarize_trace
from .selftest import LoopbackResult, run_loopback_selftest
from .ship import ShipConnectionConfig, ShipEvent, ShipSession
from .spine import SpineDatagram, extract_discovery_payloads, is_measurement_datagram
from .trace import TraceLogger
from .trust import CertificatePins, TrustStore

__all__ = [
    "CertificateMismatchError",
    "CertificatePins",
    "DiscoveryError",
    "EebusError",
    "HemsClient",
    "IdentityError",
    "IdentityMaterial",
    "IdentityStore",
    "LoopbackResult",
    "PairingRejectedError",
    "ReplayError",
    "ShipConnectionConfig",
    "ShipError",
    "ShipEvent",
    "ShipHandshakeError",
    "ShipService",
    "ShipSession",
    "SpineDatagram",
    "TraceLogger",
    "TransportError",
    "TrustError",
    "TrustStore",
    "WebSocketProtocolError",
    "discover_ship_services",
    "extract_discovery_payloads",
    "from_eebus_json_bytes",
    "is_measurement_datagram",
    "load_trace",
    "run_loopback_selftest",
    "summarize_trace",
    "to_eebus_json_bytes",
]

__version__ = "0.1.0a0"

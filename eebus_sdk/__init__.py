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
from .selftest import LoopbackResult, run_loopback_selftest
from .server import ShipServer, ShipServerConfig
from .ship import ShipConnectionConfig, ShipEvent, ShipSession
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
    "ShipServer",
    "ShipServerConfig",
    "ShipConnectionConfig",
    "ShipError",
    "ShipEvent",
    "ShipHandshakeError",
    "ShipService",
    "ShipSession",
    "TransportError",
    "TrustError",
    "TrustStore",
    "WebSocketProtocolError",
    "discover_ship_services",
    "run_loopback_selftest",
]

__version__ = "0.1.0a0"

"""Structured exceptions for the EEBus SDK."""


class EebusError(Exception):
    """Base class for all SDK exceptions."""


class DiscoveryError(EebusError):
    """Raised when SHIP discovery fails."""


class IdentityError(EebusError):
    """Raised when identity material is invalid or cannot be created."""


class TrustError(EebusError):
    """Raised when trust validation fails."""


class CertificateMismatchError(TrustError):
    """Raised when a peer certificate or SKI does not match expectations."""


class TransportError(EebusError):
    """Raised for TCP/TLS/WebSocket transport failures."""


class WebSocketProtocolError(TransportError):
    """Raised when the WebSocket peer violates RFC 6455 expectations."""


class ShipError(EebusError):
    """Raised for SHIP-specific failures."""


class ShipHandshakeError(ShipError):
    """Raised when the SHIP control handshake fails."""


class PairingRejectedError(ShipHandshakeError):
    """Raised when the remote peer rejects the local identity."""


class ReplayError(EebusError):
    """Raised when a recorded trace cannot be parsed or validated."""

"""Trust configuration for SHIP / EEBus sessions."""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from pathlib import Path

from .exceptions import CertificateMismatchError, TrustError
from .identity import IdentityMaterial, normalize_ski


@dataclass(slots=True)
class CertificatePins:
    expected_server_ski: str | None = None
    trusted_client_skis: tuple[str, ...] = ()

    def normalized(self) -> "CertificatePins":
        return CertificatePins(
            expected_server_ski=normalize_ski(self.expected_server_ski),
            trusted_client_skis=tuple(filter(None, (normalize_ski(value) for value in self.trusted_client_skis))),
        )


@dataclass(slots=True)
class TrustStore:
    pins: CertificatePins = field(default_factory=CertificatePins)
    trust_anchors: tuple[str, ...] = ()
    verify_tls: bool = False

    def __post_init__(self) -> None:
        self.pins = self.pins.normalized()

    @classmethod
    def from_server_ski(
        cls,
        expected_server_ski: str | None,
        *,
        verify_tls: bool = False,
        trust_anchors: tuple[str, ...] = (),
    ) -> "TrustStore":
        return cls(
            pins=CertificatePins(expected_server_ski=expected_server_ski),
            trust_anchors=trust_anchors,
            verify_tls=verify_tls,
        )

    def create_client_ssl_context(self, identity: IdentityMaterial) -> ssl.SSLContext:
        context = ssl.create_default_context()
        context.load_cert_chain(identity.cert_path, identity.key_path)

        if self.verify_tls:
            if self.trust_anchors:
                context.load_verify_locations(cafile=str(Path(self.trust_anchors[0]).resolve()))
                for anchor in self.trust_anchors[1:]:
                    context.load_verify_locations(cafile=str(Path(anchor).resolve()))
        else:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        return context

    def validate_peer_ski(self, peer_ski: str | None) -> None:
        expected = self.pins.expected_server_ski
        if expected is None:
            return
        if peer_ski is None:
            raise TrustError("peer certificate SKI is unavailable")
        if normalize_ski(peer_ski) != expected:
            raise CertificateMismatchError(
                f"server SKI mismatch: expected {expected}, got {normalize_ski(peer_ski)}"
            )

    def describe(self) -> dict[str, object]:
        return {
            "verify_tls": self.verify_tls,
            "trust_anchors": list(self.trust_anchors),
            "expected_server_ski": self.pins.expected_server_ski,
            "trusted_client_skis": list(self.pins.trusted_client_skis),
        }

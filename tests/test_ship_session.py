from __future__ import annotations

import asyncio
import struct
import unittest
from dataclasses import dataclass

from eebus_sdk.exceptions import PairingRejectedError
from eebus_sdk.identity import IdentityMaterial
from eebus_sdk.json_codec import to_eebus_json_bytes
from eebus_sdk.ship import ShipConnectionConfig, ShipSession
from eebus_sdk.spine import extract_discovery_payloads
from eebus_sdk.trace import TraceLogger
from eebus_sdk.trust import TrustStore
from eebus_sdk.websocket import WebSocketFrame


def _binary_frame(payload: bytes) -> WebSocketFrame:
    return WebSocketFrame(opcode=0x2, payload=payload)


def _control_frame(payload: dict) -> WebSocketFrame:
    return _binary_frame(bytes([1]) + to_eebus_json_bytes(payload))


def _data_frame(payload: dict) -> WebSocketFrame:
    return _binary_frame(bytes([2]) + to_eebus_json_bytes(payload))


@dataclass
class ScriptedTransport:
    frames: list[WebSocketFrame]
    peer_ski: str = "00112233445566778899aabbccddeeff00112233"

    def __post_init__(self) -> None:
        self.sent_payloads: list[bytes] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def receive_frame(self) -> WebSocketFrame:
        if not self.frames:
            raise AssertionError("no more frames available")
        return self.frames.pop(0)

    async def send_binary(self, payload: bytes) -> None:
        self.sent_payloads.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.connected = False

    def peer_certificate_der(self) -> bytes:
        return b""


class ShipSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.identity = IdentityMaterial(
            ship_id="i:32266_u:LOCAL_r:HEMS",
            device_id="LOCAL",
            common_name="LOCAL.cls",
            ski="11223344556677889900aabbccddeeff00112233",
            cert_path="/tmp/client.crt.pem",
            key_path="/tmp/client.key.pem",
            qr_payload="",
        )
        self.trust = TrustStore.from_server_ski("00112233445566778899aabbccddeeff00112233", verify_tls=False)
        self.config = ShipConnectionConfig(
            host="127.0.0.1",
            port=23292,
            path="/ship/",
            server_name="peer.local",
        )

    async def test_successful_handshake_and_receive_discovery_datagram(self) -> None:
        transport = ScriptedTransport(
            [
                _binary_frame(b"\x00\x00"),
                _control_frame({"connectionHello": {"phase": "ready", "waiting": 60000}}),
                _control_frame(
                    {
                        "messageProtocolHandshake": {
                            "handshakeType": "select",
                            "version": {"major": 1, "minor": 0},
                            "formats": {"format": ["JSON-UTF8"]},
                        }
                    }
                ),
                _control_frame({"connectionPinState": {"pinState": "none"}}),
                _control_frame({"accessMethodsRequest": {}}),
                _control_frame({"accessMethods": {"id": "i:32266_u:REMOTE_r:EnergyManager"}}),
                _data_frame(
                    {
                        "data": {
                            "header": {"protocolId": "ee1.0"},
                            "payload": {
                                "datagram": {
                                    "payload": {
                                        "nodeManagementDetailedDiscoveryData": {"node": [{"id": 1}]}
                                    }
                                }
                            },
                        }
                    }
                ),
            ]
        )

        session = await ShipSession.connect(
            self.config,
            self.identity,
            self.trust,
            trace_logger=TraceLogger(None),
            transport=transport,
        )
        self.assertEqual(session.remote_ship_id, "i:32266_u:REMOTE_r:EnergyManager")
        self.assertGreaterEqual(len(transport.sent_payloads), 5)

        datagram = await session.receive_datagram()
        self.assertEqual(len(extract_discovery_payloads(datagram)), 1)
        await session.close()

    async def test_pairing_rejection_raises_specific_error(self) -> None:
        transport = ScriptedTransport(
            [
                _binary_frame(b"\x00\x00"),
                _control_frame({"connectionHello": {"phase": "pending", "waiting": 60000}}),
                WebSocketFrame(
                    opcode=0x8,
                    payload=struct.pack("!H", 4452) + b"Node rejected by application.",
                ),
            ]
        )

        with self.assertRaises(PairingRejectedError):
            await ShipSession.connect(
                self.config,
                self.identity,
                self.trust,
                trace_logger=TraceLogger(None),
                transport=transport,
            )

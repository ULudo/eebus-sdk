from __future__ import annotations

import struct
import unittest
from dataclasses import dataclass

from eebus_sdk.client import HemsClient
from eebus_sdk.discovery import ShipService
from eebus_sdk.identity import IdentityMaterial
from eebus_sdk.json_codec import from_eebus_json_bytes, to_eebus_json_bytes
from eebus_sdk.ship import ShipConnectionConfig, ShipSession
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


class HemsClientSpineFlowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.identity = IdentityMaterial(
            ship_id="Demo-HEMS-123456789",
            device_id="LEGACY-HEMS",
            common_name="EEBUS End-Entity",
            ski="8a49e1e01d740ad461cc63ab8590f252f2917a47",
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

    async def test_bootstrap_and_measurement_read(self) -> None:
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
                _control_frame({"accessMethods": {"id": "i:32266_u:REMOTE_r:Steuereinrichtung"}}),
                _data_frame(
                    {
                        "data": {
                            "header": {"protocolId": "ee1.0"},
                            "payload": {
                                "datagram": {
                                    "header": {
                                        "specificationVersion": "1.3.0",
                                        "addressSource": {
                                            "device": "d:_i:REMOTE_PPC",
                                            "entity": [0],
                                            "feature": 0,
                                        },
                                        "addressDestination": {"entity": [0], "feature": 0},
                                        "msgCounter": 2,
                                        "cmdClassifier": "read",
                                        "ackRequest": True,
                                    },
                                    "payload": {"cmd": [{"nodeManagementDetailedDiscoveryData": []}]},
                                }
                            },
                        }
                    }
                ),
                _data_frame(
                    {
                        "data": {
                            "header": {"protocolId": "ee1.0"},
                            "payload": {
                                "datagram": {
                                    "header": {
                                        "specificationVersion": "1.3.0",
                                        "addressSource": {
                                            "device": "d:_i:REMOTE_PPC",
                                            "entity": [0],
                                            "feature": 0,
                                        },
                                        "addressDestination": {
                                            "device": "d:_n:HEMS_PythonSDK-LEGACY-HEMS",
                                            "entity": [0],
                                            "feature": 0,
                                        },
                                        "msgCounter": 3,
                                        "msgCounterReference": 1,
                                        "cmdClassifier": "reply",
                                    },
                                    "payload": {
                                        "cmd": [
                                            {
                                                "nodeManagementDetailedDiscoveryData": {
                                                    "featureInformation": [
                                                        {
                                                            "description": {
                                                                "featureAddress": {
                                                                    "device": "d:_i:REMOTE_PPC",
                                                                    "entity": [1],
                                                                    "feature": 11,
                                                                },
                                                                "featureType": "Measurement",
                                                                "role": "server",
                                                            }
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    },
                                }
                            },
                        }
                    }
                ),
                _data_frame(
                    {
                        "data": {
                            "header": {"protocolId": "ee1.0"},
                            "payload": {
                                "datagram": {
                                    "header": {
                                        "specificationVersion": "1.3.0",
                                        "addressSource": {
                                            "device": "d:_i:REMOTE_PPC",
                                            "entity": [1],
                                            "feature": 11,
                                        },
                                        "addressDestination": {
                                            "device": "d:_n:HEMS_PythonSDK-LEGACY-HEMS",
                                            "entity": [1],
                                            "feature": 2,
                                        },
                                        "msgCounter": 4,
                                        "msgCounterReference": 2,
                                        "cmdClassifier": "reply",
                                    },
                                    "payload": {
                                        "cmd": [
                                            {
                                                "measurementDescriptionListData": {
                                                    "measurementDescriptionData": [
                                                        {
                                                            "measurementId": 4,
                                                            "measurementType": "power",
                                                            "commodityType": "electricity",
                                                            "unit": "W",
                                                            "scopeType": "acPower",
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    },
                                }
                            },
                        }
                    }
                ),
                _data_frame(
                    {
                        "data": {
                            "header": {"protocolId": "ee1.0"},
                            "payload": {
                                "datagram": {
                                    "header": {
                                        "specificationVersion": "1.3.0",
                                        "addressSource": {
                                            "device": "d:_i:REMOTE_PPC",
                                            "entity": [1],
                                            "feature": 11,
                                        },
                                        "addressDestination": {
                                            "device": "d:_n:HEMS_PythonSDK-LEGACY-HEMS",
                                            "entity": [1],
                                            "feature": 2,
                                        },
                                        "msgCounter": 5,
                                        "cmdClassifier": "notify",
                                        "ackRequest": True,
                                    },
                                    "payload": {
                                        "cmd": [
                                            {
                                                "measurementListData": {
                                                    "measurementData": [
                                                        {
                                                            "measurementId": 4,
                                                            "valueType": "value",
                                                            "timestamp": "2026-03-12T12:00:00Z",
                                                            "value": {"number": 1234, "scale": 0},
                                                            "valueSource": "measuredValue",
                                                        }
                                                    ]
                                                }
                                            }
                                        ]
                                    },
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
        client = HemsClient(
            session=session,
            service=ShipService(service_name="peer.local", target="peer.local", port=23292),
            identity=self.identity,
            trust=self.trust,
        )

        result = await client.read_remote_measurements(timeout=5.0)
        self.assertEqual(result["remote_device_address"], "d:_i:REMOTE_PPC")
        self.assertEqual(
            result["measurement_descriptions"][0]["measurementDescriptionData"][0]["measurementType"],
            "power",
        )
        self.assertEqual(
            result["measurement_payloads"][0]["measurementData"][0]["value"]["number"],
            1234,
        )

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]

        self.assertTrue(
            any(
                message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "reply"
                and "nodeManagementDetailedDiscoveryData"
                in message["data"]["payload"]["datagram"]["payload"]["cmd"][0]
                for message in decoded
            )
        )
        self.assertTrue(
            any(
                message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "result"
                for message in decoded
            )
        )
        self.assertTrue(
            any(
                "measurementDescriptionListData"
                in message["data"]["payload"]["datagram"]["payload"]["cmd"][0]
                for message in decoded
            )
        )
        self.assertTrue(
            any(
                "measurementListData" in message["data"]["payload"]["datagram"]["payload"]["cmd"][0]
                for message in decoded
            )
        )

        await session.close()

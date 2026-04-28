from __future__ import annotations

import struct
import unittest
from dataclasses import dataclass

from eebus_sdk.client import HemsClient
from eebus_sdk.discovery import ShipService
from eebus_sdk.identity import IdentityMaterial
from eebus_sdk.json_codec import from_eebus_json_bytes, to_eebus_json_bytes
from eebus_sdk.ship import ShipConnectionConfig, ShipSession
from eebus_sdk.spine import SpineDatagram
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
            ship_id="Interop-HEMS-123456789",
            device_id="LEGACY-HEMS",
            common_name="EEBUS End-Entity",
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

    def test_client_feature_address_normalization_uses_shared_wire_order(self) -> None:
        client = HemsClient(
            session=object(),  # type: ignore[arg-type]
            service=ShipService(service_name="peer.local", target="peer.local", port=23292),
            identity=self.identity,
            trust=self.trust,
        )

        normalized = client._normalize_feature_address(
            {"entity": [1], "feature": 2, "extra": "kept"},
            default_device="d:_n:peer",
        )

        self.assertEqual(list(normalized), ["device", "entity", "feature", "extra"])

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
                                            "device": "d:_i:REMOTE_PEER",
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
                                            "device": "d:_i:REMOTE_PEER",
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
                                                                    "device": "d:_i:REMOTE_PEER",
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
                                            "device": "d:_i:REMOTE_PEER",
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
                                            "device": "d:_i:REMOTE_PEER",
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
        self.assertEqual(result["remote_device_address"], "d:_i:REMOTE_PEER")
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

    async def test_hems_reference_profile_uses_paired_addresses(self) -> None:
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
            profile="hems-reference",
        )

        self.assertEqual(client.local_device_address(), "d:_n:Interop_HEMS-123456789")
        self.assertEqual(client.local_electrical_connection_client_address()["feature"], 8)
        self.assertEqual(client.local_measurement_client_address()["feature"], 9)
        discovery = client.build_local_detailed_discovery()
        self.assertEqual(len(discovery["entityInformation"]), 7)
        self.assertEqual(discovery["featureInformation"][0]["description"]["featureAddress"]["feature"], 0)
        self.assertTrue(
            any(
                feature["description"]["featureAddress"]["entity"] == [6]
                and feature["description"]["featureAddress"]["feature"] == 3
                and feature["description"]["featureType"] == "Measurement"
                for feature in discovery["featureInformation"]
            )
        )
        load_control_server = next(
            feature
            for feature in discovery["featureInformation"]
            if feature["description"]["featureAddress"]["entity"] == [1]
            and feature["description"]["featureAddress"]["feature"] == 2
        )
        supported = load_control_server["description"]["supportedFunction"]
        self.assertIn(
            {
                "function": "loadControlLimitListData",
                "possibleOperations": {"read": {}, "write": {}},
            },
            supported,
        )

        await session.close()

    async def test_hems_reference_profile_can_request_remote_discovery_without_remote_device(self) -> None:
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
                                            "device": "d:_i:REMOTE_PEER",
                                            "entity": [0],
                                            "feature": 0,
                                        },
                                        "addressDestination": {
                                            "device": "d:_n:Interop_HEMS-123456789",
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
            profile="hems-reference",
        )

        discovery = await client.request_remote_detailed_discovery(timeout=1.0)
        self.assertEqual(discovery[-1]["featureInformation"][0]["description"]["featureAddress"]["feature"], 11)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        read_messages = [
            message
            for message in decoded
            if "nodeManagementDetailedDiscoveryData"
            in message["data"]["payload"]["datagram"]["payload"]["cmd"][0]
        ]
        self.assertEqual(
            read_messages[0]["data"]["payload"]["datagram"]["header"]["addressDestination"],
            {"entity": [0], "feature": 0},
        )
        self.assertNotIn("ackRequest", read_messages[0]["data"]["payload"]["datagram"]["header"])

        await session.close()

    async def test_hems_reference_profile_uses_remote_device_in_node_management_destination_once_known(self) -> None:
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
            profile="hems-reference",
        )
        client._remote_device_address = "d:_i:REMOTE_PEER"

        self.assertEqual(
            client._remote_node_management_destination(),
            {"device": "d:_i:REMOTE_PEER", "entity": [0], "feature": 0},
        )

        await session.close()

    async def test_hems_reference_profile_replies_to_remote_discovery_without_result(self) -> None:
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
            profile="hems-reference",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [0],
                            "feature": 0,
                        },
                        "addressDestination": {"entity": [0], "feature": 0},
                        "msgCounter": 99,
                        "cmdClassifier": "read",
                        "ackRequest": True,
                    },
                    "payload": {"cmd": [{"nodeManagementDetailedDiscoveryData": []}]},
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 2)
        classifiers = [message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] for message in decoded]
        self.assertEqual(classifiers, ["reply", "call"])
        self.assertNotIn(
            "result",
            classifiers,
        )
        self.assertEqual(
            next(iter(decoded[1]["data"]["payload"]["datagram"]["payload"]["cmd"][0])),
            "nodeManagementSubscriptionRequestCall",
        )

        await session.close()

    async def test_hems_reference_profile_avoids_duplicate_node_management_subscription_bootstrap(self) -> None:
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
            profile="hems-reference",
        )

        discovery_read = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [0],
                            "feature": 0,
                        },
                        "addressDestination": {"entity": [0], "feature": 0},
                        "msgCounter": 99,
                        "cmdClassifier": "read",
                        "ackRequest": True,
                    },
                    "payload": {"cmd": [{"nodeManagementDetailedDiscoveryData": []}]},
                }
            },
        )
        discovery_reply = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [0],
                            "feature": 0,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [0],
                            "feature": 0,
                        },
                        "msgCounter": 102,
                        "cmdClassifier": "reply",
                    },
                    "payload": {
                        "cmd": [
                            {
                                "nodeManagementDetailedDiscoveryData": {
                                    "featureInformation": [
                                        {
                                            "description": {
                                                "featureAddress": {"entity": [2], "feature": 1000},
                                                "featureType": "DeviceDiagnosis",
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
        )

        await client.handle_incoming_datagram(discovery_read)
        await client.handle_incoming_datagram(discovery_reply)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        command_names = [
            next(iter(message["data"]["payload"]["datagram"]["payload"]["cmd"][0]))
            for message in decoded
        ]
        node_management_subscriptions = [
            message
            for message in decoded
            if next(iter(message["data"]["payload"]["datagram"]["payload"]["cmd"][0]))
            == "nodeManagementSubscriptionRequestCall"
            and message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
                "nodeManagementSubscriptionRequestCall"
            ]["subscriptionRequest"].get("serverFeatureType")
            == "NodeManagement"
        ]
        self.assertEqual(len(node_management_subscriptions), 1)
        self.assertIn("nodeManagementUseCaseData", command_names)
        self.assertIn("deviceDiagnosisHeartbeatData", command_names)

        await session.close()

    async def test_hems_reference_profile_replies_to_load_control_write(self) -> None:
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
            profile="hems-reference",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [1],
                            "feature": 3,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [1],
                            "feature": 2,
                        },
                        "msgCounter": 100,
                        "cmdClassifier": "write",
                        "ackRequest": True,
                    },
                    "payload": {
                        "cmd": [
                            {
                                "loadControlLimitListData": [
                                    {
                                        "loadControlLimitData": [
                                            [
                                                {"limitId": 0},
                                                {"isLimitActive": False},
                                                {"timePeriod": [{"endTime": "PT15M"}]},
                                                {"value": [{"number": 6000}, {"scale": 0}]},
                                            ]
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 2)
        classifiers = [message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] for message in decoded]
        self.assertIn("result", classifiers)
        self.assertIn("reply", classifiers)
        reply_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "reply"
        )
        self.assertIn(
            "loadControlLimitListData",
            reply_message["data"]["payload"]["datagram"]["payload"]["cmd"][0],
        )

        await session.close()

    async def test_hems_reference_profile_replies_to_load_control_description_read(self) -> None:
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
            profile="hems-reference",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [2],
                            "feature": 1,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [1],
                            "feature": 2,
                        },
                        "msgCounter": 101,
                        "cmdClassifier": "read",
                        "ackRequest": True,
                    },
                    "payload": {"cmd": [{"loadControlLimitDescriptionListData": []}]},
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 1)
        reply_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "reply"
        )
        descriptions = reply_message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
            "loadControlLimitDescriptionListData"
        ]["loadControlLimitDescriptionData"]
        self.assertEqual(len(descriptions), 2)
        self.assertEqual(descriptions[0]["limitDirection"], "consume")
        self.assertEqual(descriptions[1]["limitDirection"], "produce")
        self.assertEqual(descriptions[0]["measurementId"], 0)

        await session.close()

    async def test_cls_adapter_profile_replies_with_consumption_and_production_limits(self) -> None:
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
            profile="cls-adapter",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [2],
                            "feature": 1,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [1],
                            "feature": 2,
                        },
                        "msgCounter": 101,
                        "cmdClassifier": "read",
                        "ackRequest": True,
                    },
                    "payload": {"cmd": [{"loadControlLimitDescriptionListData": []}]},
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 1)
        reply_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "reply"
        )
        descriptions = reply_message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
            "loadControlLimitDescriptionListData"
        ]["loadControlLimitDescriptionData"]
        self.assertEqual(len(descriptions), 2)
        self.assertEqual(descriptions[0]["limitDirection"], "consume")
        self.assertEqual(descriptions[1]["limitDirection"], "produce")
        self.assertEqual(descriptions[0]["measurementId"], 50)
        self.assertEqual(descriptions[1]["measurementId"], 50)

        await session.close()

    async def test_cls_adapter_profile_exposes_lpc_and_mgcp_topology(self) -> None:
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
            profile="cls-adapter",
        )

        discovery = client.build_local_detailed_discovery()
        self.assertEqual(
            discovery["deviceInformation"]["description"]["networkFeatureSet"],
            "smart",
        )
        self.assertEqual(len(discovery["entityInformation"]), 2)
        entity_types = {
            entry["description"]["entityAddress"]["entity"][0]: entry["description"]["entityType"]
            for entry in discovery["entityInformation"]
        }
        self.assertEqual(entity_types[0], "DeviceInformation")
        self.assertEqual(entity_types[1], "CEM")

        use_cases = client._profile_use_case_data()["useCaseInformation"]
        self.assertEqual(
            {entry["actor"] for entry in use_cases},
            {"ControllableSystem", "EnergyGuard", "MonitoringAppliance", "CEM"},
        )
        self.assertTrue(all(entry["address"]["entity"] == [1] for entry in use_cases))
        use_cases_by_actor = {entry["actor"]: entry for entry in use_cases}
        controllable_use_cases = {
            entry["useCaseName"] for entry in use_cases_by_actor["ControllableSystem"]["useCaseSupport"]
        }
        self.assertEqual(
            controllable_use_cases,
            {"limitationOfPowerConsumption", "limitationOfPowerProduction"},
        )
        energy_guard_use_cases = {
            entry["useCaseName"] for entry in use_cases_by_actor["EnergyGuard"]["useCaseSupport"]
        }
        self.assertEqual(energy_guard_use_cases, controllable_use_cases)
        monitoring_use_cases = {
            entry["useCaseName"] for entry in use_cases_by_actor["MonitoringAppliance"]["useCaseSupport"]
        }
        self.assertEqual(monitoring_use_cases, {"monitoringOfGridConnectionPoint"})
        cem_use_cases = {
            entry["useCaseName"] for entry in use_cases_by_actor["CEM"]["useCaseSupport"]
        }
        self.assertEqual(
            cem_use_cases,
            {
                "visualizationOfAggregatedBatteryData",
                "visualizationOfAggregatedPhotovoltaicData",
            },
        )

        feature_descriptions = {
            (
                entry["description"]["featureAddress"]["entity"][0],
                entry["description"]["featureAddress"]["feature"],
            ): entry["description"]
            for entry in discovery["featureInformation"]
        }
        feature_addresses = {
            address: description["featureType"] for address, description in feature_descriptions.items()
        }
        self.assertEqual(feature_addresses[(1, 2)], "LoadControl")
        self.assertEqual(feature_addresses[(1, 3)], "DeviceConfiguration")
        self.assertEqual(feature_addresses[(1, 5)], "ElectricalConnection")
        self.assertEqual(feature_addresses[(1, 6)], "LoadControl")
        self.assertEqual(feature_addresses[(1, 7)], "DeviceConfiguration")
        self.assertEqual(feature_addresses[(1, 8)], "ElectricalConnection")
        self.assertEqual(feature_addresses[(1, 9)], "Measurement")
        self.assertEqual(max(address[0] for address in feature_addresses), 1)
        load_control_operations = {
            item["function"]: item["possibleOperations"]
            for item in feature_descriptions[(1, 2)]["supportedFunction"]
        }
        self.assertEqual(load_control_operations["loadControlLimitDescriptionListData"], {"read": {}})
        self.assertEqual(
            load_control_operations["loadControlLimitListData"],
            {"read": {}, "write": {"partial": {}}},
        )
        device_config_operations = {
            item["function"]: item["possibleOperations"]
            for item in feature_descriptions[(1, 3)]["supportedFunction"]
        }
        self.assertEqual(
            device_config_operations["deviceConfigurationKeyValueDescriptionListData"],
            {"read": {}},
        )
        self.assertEqual(
            device_config_operations["deviceConfigurationKeyValueListData"],
            {"read": {}, "write": {"partial": {}}},
        )

        load_control = client._profile_load_control_limit_description_data()["loadControlLimitDescriptionData"]
        self.assertEqual(load_control[0]["measurementId"], 50)
        self.assertEqual(load_control[1]["measurementId"], 50)
        self.assertEqual(load_control[1]["limitDirection"], "produce")
        self.assertNotIn("label", load_control[0])
        self.assertEqual(len(load_control), 2)

        key_values = client._profile_device_configuration_key_value_description_data()[
            "deviceConfigurationKeyValueDescriptionData"
        ]
        self.assertEqual([item["keyId"] for item in key_values], [0, 1, 2])

        client._ensure_profile_runtime_defaults()
        load_control_payload = client._profile_load_control_limit_payload["loadControlLimitData"]
        self.assertEqual(load_control_payload[0]["value"]["number"], 4200)
        self.assertNotIn("timePeriod", load_control_payload[0])
        self.assertEqual(load_control_payload[1]["value"]["number"], -10000)

        config_payload = client._profile_device_configuration_payload["deviceConfigurationKeyValueData"]
        self.assertEqual(config_payload[0]["value"]["scaledNumber"]["number"], 4200)
        self.assertEqual(config_payload[1]["value"]["duration"], "PT7200S")
        characteristics = client._profile_electrical_connection_characteristic_data()[
            "electricalConnectionCharacteristicData"
        ]
        self.assertEqual(characteristics[0]["value"]["number"], 32000)
        self.assertEqual(len(characteristics), 2)
        self.assertEqual(characteristics[0]["characteristicType"], "contractualConsumptionNominalMax")
        self.assertEqual(characteristics[1]["characteristicType"], "contractualProductionNominalMax")
        self.assertEqual(client._profile_measurement_id({"entity": [1]}), 50)

        heartbeat = client._profile_device_diagnosis_heartbeat_data()
        self.assertEqual(heartbeat["heartbeatTimeout"], "PT4S")

        classification = client._profile_device_classification_data()
        self.assertEqual(classification["deviceName"], self.identity.ship_id)
        self.assertEqual(classification["deviceCode"], self.identity.ship_id)
        self.assertEqual(classification["manufacturerNodeIdentification"], self.identity.common_name)
        self.assertEqual(classification["powerSource"], "mains3Phase")

        await session.close()

    async def test_cls_adapter_profile_sends_initial_notify_after_load_control_subscription(self) -> None:
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
            profile="cls-adapter",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [4],
                            "feature": 1,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [0],
                            "feature": 0,
                        },
                        "msgCounter": 101,
                        "cmdClassifier": "call",
                        "ackRequest": True,
                    },
                    "payload": {
                        "cmd": [
                            {
                                "nodeManagementSubscriptionRequestCall": {
                                    "subscriptionRequest": {
                                        "clientAddress": {
                                            "device": "d:_i:REMOTE_PEER",
                                            "entity": [4],
                                            "feature": 1,
                                        },
                                        "serverAddress": {
                                            "device": "d:_n:Interop_HEMS-123456789",
                                            "entity": [1],
                                            "feature": 2,
                                        },
                                        "serverFeatureType": "LoadControl",
                                    }
                                }
                            }
                        ]
                    },
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 2)
        notify_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "notify"
        )
        load_control_limits = notify_message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
            "loadControlLimitListData"
        ]["loadControlLimitData"]
        self.assertEqual(
            notify_message["data"]["payload"]["datagram"]["header"]["addressSource"]["entity"],
            [1],
        )
        self.assertEqual(load_control_limits[0]["limitId"], 0)
        self.assertEqual(load_control_limits[0]["value"]["number"], 4200)
        self.assertNotIn("timePeriod", load_control_limits[0])
        self.assertEqual(load_control_limits[1]["value"]["number"], -10000)
        self.assertEqual(len(load_control_limits), 2)

        await session.close()

    async def test_cls_adapter_profile_sends_initial_notify_after_configuration_subscription(self) -> None:
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
            profile="cls-adapter",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [4],
                            "feature": 1,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [0],
                            "feature": 0,
                        },
                        "msgCounter": 101,
                        "cmdClassifier": "call",
                        "ackRequest": True,
                    },
                    "payload": {
                        "cmd": [
                            {
                                "nodeManagementSubscriptionRequestCall": {
                                    "subscriptionRequest": {
                                        "clientAddress": {
                                            "device": "d:_i:REMOTE_PEER",
                                            "entity": [4],
                                            "feature": 1,
                                        },
                                        "serverAddress": {
                                            "device": "d:_n:Interop_HEMS-123456789",
                                            "entity": [1],
                                            "feature": 3,
                                        },
                                        "serverFeatureType": "DeviceConfiguration",
                                    }
                                }
                            }
                        ]
                    },
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        notify_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "notify"
        )
        key_values = notify_message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
            "deviceConfigurationKeyValueListData"
        ]["deviceConfigurationKeyValueData"]
        self.assertEqual(
            notify_message["data"]["payload"]["datagram"]["header"]["addressSource"]["entity"],
            [1],
        )
        self.assertEqual([item["keyId"] for item in key_values], [0, 1, 2])
        self.assertEqual(key_values[0]["value"]["scaledNumber"]["number"], 4200)
        self.assertEqual(key_values[1]["value"]["duration"], "PT7200S")

        await session.close()

    async def test_cls_adapter_profile_sends_initial_notify_after_electrical_connection_subscription(self) -> None:
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
            profile="cls-adapter",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [4],
                            "feature": 1,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [0],
                            "feature": 0,
                        },
                        "msgCounter": 101,
                        "cmdClassifier": "call",
                        "ackRequest": True,
                    },
                    "payload": {
                        "cmd": [
                            {
                                "nodeManagementSubscriptionRequestCall": {
                                    "subscriptionRequest": {
                                        "clientAddress": {
                                            "device": "d:_i:REMOTE_PEER",
                                            "entity": [4],
                                            "feature": 1,
                                        },
                                        "serverAddress": {
                                            "device": "d:_n:Interop_HEMS-123456789",
                                            "entity": [1],
                                            "feature": 5,
                                        },
                                        "serverFeatureType": "ElectricalConnection",
                                    }
                                }
                            }
                        ]
                    },
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 2)
        notify_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "notify"
        )
        commands = notify_message["data"]["payload"]["datagram"]["payload"]["cmd"]
        self.assertEqual(
            notify_message["data"]["payload"]["datagram"]["header"]["addressSource"]["entity"],
            [1],
        )
        self.assertEqual(len(commands), 1)
        characteristics = commands[0]["electricalConnectionCharacteristicListData"][
            "electricalConnectionCharacteristicData"
        ]
        self.assertEqual(characteristics[0]["characteristicType"], "contractualConsumptionNominalMax")
        self.assertEqual(characteristics[0]["value"]["number"], 32000)
        self.assertEqual(characteristics[1]["characteristicType"], "contractualProductionNominalMax")

        await session.close()

    async def test_cls_adapter_profile_replies_to_electrical_characteristic_reads(self) -> None:
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
            profile="cls-adapter",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [4],
                            "feature": 1,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [1],
                            "feature": 5,
                        },
                        "msgCounter": 111,
                        "cmdClassifier": "read",
                        "ackRequest": True,
                    },
                    "payload": {"cmd": [{"electricalConnectionCharacteristicListData": []}]},
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        reply_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "reply"
        )
        characteristics = reply_message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
            "electricalConnectionCharacteristicListData"
        ]["electricalConnectionCharacteristicData"]
        self.assertEqual(len(characteristics), 2)
        self.assertEqual(characteristics[0]["value"]["number"], 32000)
        self.assertEqual(characteristics[1]["value"]["number"], 10000)

        await session.close()

    async def test_cls_adapter_profile_stays_passive_after_remote_discovery_read(self) -> None:
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
            profile="cls-adapter",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [0],
                            "feature": 0,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [0],
                            "feature": 0,
                        },
                        "msgCounter": 101,
                        "cmdClassifier": "read",
                        "ackRequest": True,
                    },
                    "payload": {"cmd": [{"nodeManagementDetailedDiscoveryData": []}]},
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(
            [message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] for message in decoded],
            ["reply"],
        )

        await session.close()

    async def test_hems_reference_profile_replies_to_destination_list_read(self) -> None:
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
            profile="hems-reference",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [0],
                            "feature": 0,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [0],
                            "feature": 0,
                        },
                        "msgCounter": 101,
                        "cmdClassifier": "read",
                        "ackRequest": True,
                    },
                    "payload": {"cmd": [{"nodeManagementDestinationListData": []}]},
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 1)
        reply_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "reply"
        )
        destination_data = reply_message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
            "nodeManagementDestinationListData"
        ]["nodeManagementDestinationData"]
        self.assertEqual(
            destination_data[0]["deviceDescription"]["deviceAddress"]["device"],
            "d:_n:Interop_HEMS-123456789",
        )
        self.assertEqual(
            destination_data[0]["deviceDescription"]["networkFeatureSet"],
            "smart",
        )

        await session.close()

    async def test_hems_reference_profile_replies_to_electrical_connection_description_read(self) -> None:
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
            profile="hems-reference",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [3],
                            "feature": 1,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [2],
                            "feature": 1,
                        },
                        "msgCounter": 110,
                        "cmdClassifier": "read",
                        "ackRequest": True,
                    },
                    "payload": {"cmd": [{"electricalConnectionDescriptionListData": []}]},
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 1)
        reply_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "reply"
        )
        descriptions = reply_message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
            "electricalConnectionDescriptionListData"
        ]["electricalConnectionDescriptionData"]
        self.assertEqual(descriptions[0]["electricalConnectionId"], 0)
        self.assertEqual(descriptions[0]["positiveEnergyDirection"], "consume")

        await session.close()

    async def test_hems_reference_profile_replies_to_electrical_connection_parameter_read(self) -> None:
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
            profile="hems-reference",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [3],
                            "feature": 1,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [2],
                            "feature": 1,
                        },
                        "msgCounter": 111,
                        "cmdClassifier": "read",
                        "ackRequest": True,
                    },
                    "payload": {"cmd": [{"electricalConnectionParameterDescriptionListData": []}]},
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 1)
        reply_message = next(
            message for message in decoded if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] == "reply"
        )
        parameters = reply_message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
            "electricalConnectionParameterDescriptionListData"
        ]["electricalConnectionParameterDescriptionData"]
        self.assertEqual(parameters[0]["measurementId"], 0)
        self.assertEqual(parameters[0]["voltageType"], "ac")

        await session.close()

    async def test_hems_reference_profile_bootstraps_after_remote_discovery_reply(self) -> None:
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
            profile="hems-reference",
        )

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [0],
                            "feature": 0,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [0],
                            "feature": 0,
                        },
                        "msgCounter": 102,
                        "cmdClassifier": "reply",
                    },
                    "payload": {
                        "cmd": [
                            {
                                "nodeManagementDetailedDiscoveryData": {
                                    "featureInformation": [
                                        {
                                            "description": {
                                                "featureAddress": {"entity": [2], "feature": 1000},
                                                "featureType": "DeviceDiagnosis",
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
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 4)
        command_names = [
            next(iter(message["data"]["payload"]["datagram"]["payload"]["cmd"][0]))
            for message in decoded
        ]
        self.assertIn("nodeManagementSubscriptionRequestCall", command_names)
        self.assertIn("nodeManagementUseCaseData", command_names)
        self.assertIn("deviceDiagnosisHeartbeatData", command_names)

        await session.close()

    async def test_hems_reference_profile_bootstraps_device_diagnosis_after_binding_request(self) -> None:
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
            profile="hems-reference",
        )

        client._last_remote_discovery = {
            "featureInformation": [
                {
                    "description": {
                        "featureAddress": {"device": "d:_i:REMOTE_PEER", "entity": [2], "feature": 1000},
                        "featureType": "DeviceDiagnosis",
                        "role": "server",
                    }
                },
                {
                    "description": {
                        "featureAddress": {"device": "d:_i:REMOTE_PEER", "entity": [3], "feature": 1000},
                        "featureType": "DeviceDiagnosis",
                        "role": "server",
                    }
                },
            ]
        }

        incoming = SpineDatagram(
            header={"protocolId": "ee1.0"},
            payload={
                "datagram": {
                    "header": {
                        "specificationVersion": "1.3.0",
                        "addressSource": {
                            "device": "d:_i:REMOTE_PEER",
                            "entity": [0],
                            "feature": 0,
                        },
                        "addressDestination": {
                            "device": "d:_n:Interop_HEMS-123456789",
                            "entity": [0],
                            "feature": 0,
                        },
                        "msgCounter": 103,
                        "cmdClassifier": "call",
                    },
                    "payload": {
                        "cmd": [
                            {
                                "nodeManagementBindingRequestCall": {
                                    "bindingRequest": {
                                        "clientAddress": {"entity": [2], "feature": 11},
                                        "serverAddress": {"entity": [1], "feature": 2},
                                        "serverFeatureType": "LoadControl",
                                    }
                                }
                            }
                        ]
                    },
                }
            },
        )

        await client.handle_incoming_datagram(incoming)

        sent_data_frames = [payload for payload in transport.sent_payloads if payload and payload[0] == 2]
        decoded = [from_eebus_json_bytes(payload[1:]) for payload in sent_data_frames]
        self.assertEqual(len(decoded), 3)
        command_names = [
            next(iter(message["data"]["payload"]["datagram"]["payload"]["cmd"][0]))
            for message in decoded
            if message["data"]["payload"]["datagram"]["header"]["cmdClassifier"] != "result"
        ]
        self.assertIn("nodeManagementSubscriptionRequestCall", command_names)
        self.assertIn("deviceDiagnosisHeartbeatData", command_names)
        subscription_message = next(
            message
            for message in decoded
            if message["data"]["payload"]["datagram"]["payload"]["cmd"][0].get(
                "nodeManagementSubscriptionRequestCall"
            )
        )
        subscription_request = subscription_message["data"]["payload"]["datagram"]["payload"]["cmd"][0][
            "nodeManagementSubscriptionRequestCall"
        ]["subscriptionRequest"]
        self.assertEqual(subscription_request["serverAddress"]["entity"], [2])
        self.assertEqual(subscription_request["serverAddress"]["feature"], 1000)

        await session.close()

from __future__ import annotations

import asyncio
import struct
import unittest
from dataclasses import dataclass

from eebus_sdk.exceptions import PairingRejectedError
from eebus_sdk.identity import IdentityMaterial
from eebus_sdk.json_codec import from_eebus_json_bytes, to_eebus_json_bytes
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
    frames: list[WebSocketFrame | Exception]
    peer_ski: str = "00112233445566778899aabbccddeeff00112233"

    def __post_init__(self) -> None:
        self.sent_payloads: list[bytes] = []
        self.sent_pings: list[bytes] = []
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def receive_frame(self) -> WebSocketFrame:
        if not self.frames:
            raise AssertionError("no more frames available")
        next_item = self.frames.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    async def send_binary(self, payload: bytes) -> None:
        self.sent_payloads.append(payload)

    async def send_ping(self, payload: bytes = b"") -> None:
        self.sent_pings.append(payload)

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

        control_payloads = [
            from_eebus_json_bytes(payload[1:])
            for payload in transport.sent_payloads
            if payload and payload[0] == 1
        ]
        self.assertIn({"accessMethodsResponse": {"methods": {"userTrust": {"levels": [1]}}}}, control_payloads)
        self.assertIn({"accessMethods": {"id": self.identity.ship_id}}, control_payloads)

    async def test_access_methods_response_triggers_access_request(self) -> None:
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
                _control_frame(
                    {
                        "accessMethodsResponse": {
                            "methods": {"userTrust": {"levels": [1]}},
                            "requestId": "access-1",
                        }
                    }
                ),
                _control_frame({"accessMethods": {"id": "i:32266_u:REMOTE_r:EnergyManager"}}),
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
        await session.close()

        control_payloads = [
            from_eebus_json_bytes(payload[1:])
            for payload in transport.sent_payloads
            if payload and payload[0] == 1
        ]
        self.assertIn({"accessMethodsRequest": []}, control_payloads)
        self.assertIn(
            {"accessRequest": {"methods": {"userTrust": {"level": 1}}, "requestId": "access-1"}},
            control_payloads,
        )

    async def test_response_only_access_skips_local_access_methods(self) -> None:
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
            ]
        )

        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=self.config.host,
                port=self.config.port,
                path=self.config.path,
                server_name=self.config.server_name,
                send_local_access_methods=False,
            ),
            self.identity,
            self.trust,
            trace_logger=TraceLogger(None),
            transport=transport,
        )
        self.assertEqual(session.remote_ship_id, "i:32266_u:REMOTE_r:EnergyManager")
        await session.close()

        control_payloads = [
            from_eebus_json_bytes(payload[1:])
            for payload in transport.sent_payloads
            if payload and payload[0] == 1
        ]
        self.assertIn({"accessMethodsResponse": {"methods": {"userTrust": {"levels": [1]}}}}, control_payloads)
        self.assertNotIn({"accessMethods": {"id": self.identity.ship_id}}, control_payloads)

    async def test_can_skip_access_methods_response_and_send_only_local_access_methods(self) -> None:
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
            ]
        )

        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=self.config.host,
                port=self.config.port,
                path=self.config.path,
                server_name=self.config.server_name,
                send_access_methods_response=False,
            ),
            self.identity,
            self.trust,
            trace_logger=TraceLogger(None),
            transport=transport,
        )
        self.assertEqual(session.remote_ship_id, "i:32266_u:REMOTE_r:EnergyManager")
        await session.close()

        control_payloads = [
            from_eebus_json_bytes(payload[1:])
            for payload in transport.sent_payloads
            if payload and payload[0] == 1
        ]
        self.assertNotIn({"accessMethodsResponse": {"methods": {"userTrust": {"levels": [1]}}}}, control_payloads)
        self.assertIn({"accessMethods": {"id": self.identity.ship_id}}, control_payloads)

    async def test_local_access_methods_can_include_mdns_marker(self) -> None:
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
            ]
        )

        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=self.config.host,
                port=self.config.port,
                path=self.config.path,
                server_name=self.config.server_name,
                send_local_access_methods_mdns=True,
            ),
            self.identity,
            self.trust,
            trace_logger=TraceLogger(None),
            transport=transport,
        )
        self.assertEqual(session.remote_ship_id, "i:32266_u:REMOTE_r:EnergyManager")
        await session.close()

        control_payloads = [
            from_eebus_json_bytes(payload[1:])
            for payload in transport.sent_payloads
            if payload and payload[0] == 1
        ]
        self.assertIn({"accessMethods": {"id": self.identity.ship_id, "dnsSd_mDns": []}}, control_payloads)

    async def test_compatibility_access_sequence_matches_cls_adapter_shape(self) -> None:
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
                _control_frame({"accessMethodsRequest": {"requestId": "compat-1"}}),
                _control_frame({"accessMethods": {"id": "i:32266_u:REMOTE_r:EnergyManager"}}),
            ]
        )

        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=self.config.host,
                port=self.config.port,
                path=self.config.path,
                server_name=self.config.server_name,
                send_local_access_methods=False,
                access_handshake_mode="compatibility",
                compatibility_access_select_delay_seconds=0.0,
            ),
            self.identity,
            self.trust,
            trace_logger=TraceLogger(None),
            transport=transport,
        )
        self.assertEqual(session.remote_ship_id, "i:32266_u:REMOTE_r:EnergyManager")
        await asyncio.sleep(0)
        await session.close()

        control_payloads = [
            from_eebus_json_bytes(payload[1:])
            for payload in transport.sent_payloads
            if payload and payload[0] == 1
        ]
        self.assertIn({"accessMethodsRequest": []}, control_payloads)
        self.assertIn(
            {"accessMethodsResponse": [{"methods": [{"userTrust": [{"level": [1]}]}], "requestId": "compat-1"}]},
            control_payloads,
        )
        self.assertIn(
            {"accessMethods": [{"methods": [{"userTrust": [{"level": [1]}]}], "requestId": "compat-1"}]},
            control_payloads,
        )
        self.assertIn(
            {"accessRequest": [{"methods": [{"userTrust": [{"level": [1]}]}], "requestId": "compat-1"}]},
            control_payloads,
        )
        self.assertIn(
            {"methodsSelect": [{"userTrust": [{"level": 1}], "requestId": "compat-1"}]},
            control_payloads,
        )

    async def test_compatibility_access_can_use_levels_key(self) -> None:
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
                _control_frame({"accessMethodsRequest": {"requestId": "compat-levels"}}),
                _control_frame({"accessMethods": {"id": "i:32266_u:REMOTE_r:EnergyManager"}}),
            ]
        )

        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=self.config.host,
                port=self.config.port,
                path=self.config.path,
                server_name=self.config.server_name,
                send_local_access_methods=False,
                access_handshake_mode="compatibility",
                compatibility_access_send_alternative_methods=False,
                compatibility_access_send_proactive_request=False,
                compatibility_access_send_method_selection=False,
                compatibility_access_user_trust_key="levels",
            ),
            self.identity,
            self.trust,
            trace_logger=TraceLogger(None),
            transport=transport,
        )
        await session.close()

        control_payloads = [
            from_eebus_json_bytes(payload[1:])
            for payload in transport.sent_payloads
            if payload and payload[0] == 1
        ]
        self.assertIn(
            {"accessMethodsResponse": [{"methods": [{"userTrust": [{"levels": [1]}]}], "requestId": "compat-levels"}]},
            control_payloads,
        )

    async def test_compatibility_access_can_also_send_local_id(self) -> None:
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
                _control_frame({"accessMethodsRequest": {"requestId": "compat-id"}}),
                _control_frame({"accessMethods": {"id": "i:32266_u:REMOTE_r:EnergyManager"}}),
            ]
        )

        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=self.config.host,
                port=self.config.port,
                path=self.config.path,
                server_name=self.config.server_name,
                send_local_access_methods=True,
                access_handshake_mode="compatibility",
                compatibility_access_send_alternative_methods=False,
                compatibility_access_send_proactive_request=False,
                compatibility_access_send_method_selection=False,
                compatibility_access_send_local_id=True,
            ),
            self.identity,
            self.trust,
            trace_logger=TraceLogger(None),
            transport=transport,
        )
        await session.close()

        control_payloads = [
            from_eebus_json_bytes(payload[1:])
            for payload in transport.sent_payloads
            if payload and payload[0] == 1
        ]
        self.assertIn({"accessMethods": {"id": self.identity.ship_id}}, control_payloads)

    async def test_events_ignore_idle_timeouts(self) -> None:
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
                asyncio.TimeoutError(),
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
        try:
            event = await anext(session.events())
        finally:
            await session.close()

        self.assertEqual(event.kind, "datagram")

    async def test_keepalive_waits_for_interval_before_first_send(self) -> None:
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
            ]
        )

        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=self.config.host,
                port=self.config.port,
                path=self.config.path,
                server_name=self.config.server_name,
                keepalive_interval_seconds=0.01,
                send_local_access_methods=False,
            ),
            self.identity,
            self.trust,
            trace_logger=TraceLogger(None),
            transport=transport,
        )
        try:
            immediate_control_payloads = [
                from_eebus_json_bytes(payload[1:])
                for payload in transport.sent_payloads
                if payload and payload[0] == 1
            ]
            self.assertNotIn({"connectionKeepAlive": {"timeout": 20010}}, immediate_control_payloads)

            await asyncio.sleep(0.03)

            later_control_payloads = [
                from_eebus_json_bytes(payload[1:])
                for payload in transport.sent_payloads
                if payload and payload[0] == 1
            ]
            self.assertIn({"connectionKeepAlive": {"timeout": 20010}}, later_control_payloads)
        finally:
            await session.close()

    async def test_websocket_ping_waits_for_interval_before_first_send(self) -> None:
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
            ]
        )

        session = await ShipSession.connect(
            ShipConnectionConfig(
                host=self.config.host,
                port=self.config.port,
                path=self.config.path,
                server_name=self.config.server_name,
                websocket_ping_interval_seconds=0.01,
                send_local_access_methods=False,
            ),
            self.identity,
            self.trust,
            trace_logger=TraceLogger(None),
            transport=transport,
        )
        try:
            self.assertEqual(transport.sent_pings, [])
            await asyncio.sleep(0.03)
            self.assertGreaterEqual(len(transport.sent_pings), 1)
        finally:
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

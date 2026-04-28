from __future__ import annotations

import json
import unittest

from eebus_sdk.json_codec import from_eebus_json_bytes, to_eebus_json_bytes
from eebus_sdk.spine import build_reply_datagram, build_result_datagram


class EEBusJsonTests(unittest.TestCase):
    def test_round_trip_control_message(self) -> None:
        payload = {"connectionHello": {"phase": "ready", "waiting": 60000}}
        encoded = to_eebus_json_bytes(payload)
        self.assertEqual(
            encoded.decode("utf-8"),
            '{"connectionHello":[{"phase":"ready"},{"waiting":60000}]}',
        )
        self.assertEqual(from_eebus_json_bytes(encoded), payload)

    def test_round_trip_data_message(self) -> None:
        payload = {
            "data": {
                "header": {"protocolId": "ee1.0"},
                "payload": {
                    "datagram": {
                        "header": {"specificationVersionList": {"specificationVersion": []}}
                    }
                },
            }
        }
        encoded = to_eebus_json_bytes(payload)
        wire = json.loads(encoded.decode("utf-8"))
        self.assertIsInstance(wire["data"], list)
        self.assertIsInstance(wire["data"][0]["header"], list)
        self.assertIsInstance(wire["data"][1]["payload"], dict)
        self.assertIsInstance(wire["data"][1]["payload"]["datagram"], list)
        decoded = from_eebus_json_bytes(encoded)
        self.assertEqual(decoded, payload)

    def test_reply_and_result_header_place_msg_counter_reference_before_cmd_classifier(self) -> None:
        request = {
            "datagram": {
                "header": {
                    "specificationVersion": "1.3.0",
                    "addressSource": {"device": "REMOTE", "entity": [0], "feature": 0},
                    "addressDestination": {"device": "LOCAL", "entity": [0], "feature": 0},
                    "msgCounter": 17,
                    "cmdClassifier": "read",
                    "ackRequest": True,
                },
                "payload": {"cmd": [{"nodeManagementDetailedDiscoveryData": []}]},
            }
        }

        reply = build_reply_datagram(
            request,
            source={"device": "LOCAL", "entity": [0], "feature": 0},
            msg_counter=18,
            commands=[{"nodeManagementDetailedDiscoveryData": {}}],
        )
        result = build_result_datagram(
            request,
            source={"device": "LOCAL", "entity": [0], "feature": 0},
            msg_counter=19,
        )

        reply_wire = to_eebus_json_bytes({"data": {"header": {"protocolId": "ee1.0"}, "payload": reply.payload}}).decode(
            "utf-8"
        )
        result_wire = to_eebus_json_bytes(
            {"data": {"header": {"protocolId": "ee1.0"}, "payload": result.payload}}
        ).decode("utf-8")

        self.assertLess(reply_wire.index('"msgCounter":18'), reply_wire.index('"msgCounterReference":17'))
        self.assertLess(reply_wire.index('"msgCounterReference":17'), reply_wire.index('"cmdClassifier":"reply"'))
        self.assertLess(result_wire.index('"msgCounter":19'), result_wire.index('"msgCounterReference":17'))
        self.assertLess(result_wire.index('"msgCounterReference":17'), result_wire.index('"cmdClassifier":"result"'))


if __name__ == "__main__":
    unittest.main()

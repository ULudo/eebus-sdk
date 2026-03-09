from __future__ import annotations

import unittest

from eebus_sdk.json_codec import from_eebus_json_bytes, to_eebus_json_bytes


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
        decoded = from_eebus_json_bytes(encoded)
        self.assertEqual(decoded, payload)


if __name__ == "__main__":
    unittest.main()

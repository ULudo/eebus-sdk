import unittest

from eebus_sdk._load_power import build_limit_payload, extract_limit_states, extract_preferred_load_power_state
from eebus_sdk._spine_helpers import (
    feature_addresses,
    format_duration,
    merge_keyed_list_payload,
    normalize_feature_address,
)


class InternalSpineHelperTests(unittest.TestCase):
    def test_format_duration_uses_spine_iso_duration_shape(self) -> None:
        self.assertEqual(format_duration(0), "PT0S")
        self.assertEqual(format_duration(65), "PT1M5S")
        self.assertEqual(format_duration(7200), "PT2H")

    def test_feature_addresses_filters_and_applies_default_device(self) -> None:
        discovery = {
            "featureInformation": [
                {
                    "description": {
                        "featureType": "LoadControl",
                        "role": "server",
                        "featureAddress": {"entity": [1], "feature": 2},
                    }
                },
                {
                    "description": {
                        "featureType": "LoadControl",
                        "role": "client",
                        "featureAddress": {"entity": [1], "feature": 6},
                    }
                },
            ]
        }

        self.assertEqual(
            feature_addresses(discovery, feature_type="LoadControl", role="server", default_device="d:_n:peer"),
            [{"device": "d:_n:peer", "entity": [1], "feature": 2}],
        )

    def test_normalize_feature_address_uses_device_first_wire_order(self) -> None:
        normalized = normalize_feature_address(
            {"entity": [1], "feature": 2, "extra": "kept"},
            default_device="d:_n:peer",
        )

        self.assertEqual(list(normalized), ["device", "entity", "feature", "extra"])
        self.assertEqual(
            normalized,
            {"device": "d:_n:peer", "entity": [1], "feature": 2, "extra": "kept"},
        )

    def test_merge_keyed_list_payload_keeps_existing_order_and_updates_by_id(self) -> None:
        current = {"loadControlLimitData": [{"limitId": 0, "isLimitActive": False}, {"limitId": 1}]}
        incoming = {"loadControlLimitData": [{"limitId": 0, "isLimitActive": True}]}

        self.assertEqual(
            merge_keyed_list_payload(
                current,
                incoming,
                list_key="loadControlLimitData",
                id_key="limitId",
            ),
            {"loadControlLimitData": [{"limitId": 0, "isLimitActive": True}, {"limitId": 1}]},
        )

    def test_load_power_helpers_cover_lpc_and_lpp_limits(self) -> None:
        payload = build_limit_payload(watts=1000, duration_seconds=600, limit_id=1, is_active=True)

        self.assertEqual(
            payload,
            {
                "loadControlLimitListData": {
                    "loadControlLimitData": [
                        {
                            "limitId": 1,
                            "isLimitActive": True,
                            "timePeriod": {"endTime": "PT10M"},
                            "value": {"number": -1000, "scale": 0},
                        }
                    ]
                }
            },
        )
        states = extract_limit_states(payload["loadControlLimitListData"])
        self.assertEqual(states[0]["direction"], "produce")
        self.assertEqual(states[0]["protocol_watts"], -1000)
        self.assertEqual(states[0]["watts"], 1000)
        self.assertEqual(extract_preferred_load_power_state(payload["loadControlLimitListData"])["limit_id"], 1)

        scaled_states = extract_limit_states(
            {
                "loadControlLimitData": [
                    {"limitId": 0, "value": {"number": 50, "scale": 3}},
                    {"limitId": 1, "value": {"number": -6, "scale": 3}},
                ]
            }
        )
        self.assertEqual(scaled_states[0]["watts"], 50000)
        self.assertEqual(scaled_states[1]["watts"], 6000)


if __name__ == "__main__":
    unittest.main()

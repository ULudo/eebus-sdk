from __future__ import annotations

import unittest
from pathlib import Path

from eebus_sdk.replay import load_trace, summarize_trace


FIXTURES = Path(__file__).resolve().parents[1] / "interop_fixtures" / "ship"


class ReplayFixtureTests(unittest.TestCase):
    def test_pairing_rejected_fixture(self) -> None:
        summary = summarize_trace(load_trace(FIXTURES / "ppc_pairing_rejected.jsonl"))
        self.assertTrue(summary["tls_connected"])
        self.assertTrue(summary["pairing_rejected"])
        self.assertEqual(summary["close_code"], 4452)

    def test_spine_discovery_fixture(self) -> None:
        summary = summarize_trace(load_trace(FIXTURES / "spine_discovery_success.jsonl"))
        self.assertTrue(summary["handshake_complete"])
        self.assertEqual(summary["spine_datagrams"], 1)
        self.assertEqual(summary["discovery_payloads"], 1)
        self.assertEqual(summary["remote_ship_id"], "i:32266_u:REMOTE_r:EnergyManager")

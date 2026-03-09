from __future__ import annotations

import tempfile
import unittest

from eebus_sdk.identity import openssl_available
from eebus_sdk.selftest import run_loopback_selftest


@unittest.skipUnless(openssl_available(), "openssl is required for loopback self-test")
class LoopbackSelftestTests(unittest.IsolatedAsyncioTestCase):
    async def test_loopback_selftest_exercises_both_roles(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            try:
                result = await run_loopback_selftest(work_dir=temp_dir, verify_tls=True)
            except OSError as exc:
                self.skipTest(f"local socket binding is not permitted in this environment: {exc}")

        self.assertTrue(result.server_port > 0)
        self.assertIn("LOOPBACK-CLIENT", result.client_ship_id)
        self.assertIn("LOOPBACK-SERVER", result.server_ship_id)
        self.assertEqual(result.received_discovery_payloads, 1)
        self.assertEqual(len(result.peer_client_ski), 40)

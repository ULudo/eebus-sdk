from __future__ import annotations

import tempfile
import unittest

from eebus_sdk.exceptions import CertificateMismatchError
from eebus_sdk.identity import IdentityStore, openssl_available
from eebus_sdk.trust import TrustStore


@unittest.skipUnless(openssl_available(), "openssl is required for identity generation tests")
class IdentityAndTrustTests(unittest.TestCase):
    def test_identity_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            created = IdentityStore.create(temp_dir, device_id="HEMS-TEST-DEVICE")
            loaded = IdentityStore.load(f"{temp_dir}/identity.json")

        self.assertEqual(created.ship_id, loaded.ship_id)
        self.assertEqual(created.ski, loaded.ski)
        self.assertTrue(loaded.cert_path.endswith("client.crt.pem"))
        self.assertIn("HEMS-TEST-DEVICE", loaded.common_name)

    def test_trust_store_validates_server_ski(self) -> None:
        trust = TrustStore.from_server_ski("aa:bb:cc", verify_tls=False)
        trust.validate_peer_ski("aabbcc")
        with self.assertRaises(CertificateMismatchError):
            trust.validate_peer_ski("ddeeff")

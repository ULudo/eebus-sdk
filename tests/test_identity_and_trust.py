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

    def test_import_existing_identity_without_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = IdentityStore.create(f"{temp_dir}/source", device_id="HEMS-IMPORT-SRC")
            imported = IdentityStore.import_existing(
                f"{temp_dir}/imported",
                cert_path=source.cert_path,
                key_path=source.key_path,
                ship_id="Demo-HEMS-123456789",
                brand="HEMS",
                model="ImportedClient",
            )
            loaded = IdentityStore.load(f"{temp_dir}/imported/identity.json")

        self.assertEqual(imported.ship_id, "Demo-HEMS-123456789")
        self.assertEqual(imported.cert_path, source.cert_path)
        self.assertEqual(imported.key_path, source.key_path)
        self.assertEqual(loaded.ski, source.ski)
        self.assertIn("Demo-HEMS-123456789", loaded.qr_payload)

    def test_import_existing_identity_with_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = IdentityStore.create(f"{temp_dir}/source", device_id="HEMS-IMPORT-COPY")
            imported = IdentityStore.import_existing(
                f"{temp_dir}/imported",
                cert_path=source.cert_path,
                key_path=source.key_path,
                ship_id="Demo-HEMS-123456789",
                copy_files=True,
            )
            loaded = IdentityStore.load(f"{temp_dir}/imported/identity.json")

        self.assertNotEqual(imported.cert_path, source.cert_path)
        self.assertNotEqual(imported.key_path, source.key_path)
        self.assertTrue(imported.cert_path.endswith("client.crt.pem"))
        self.assertTrue(imported.key_path.endswith("client.key.pem"))
        self.assertEqual(loaded.ship_id, "Demo-HEMS-123456789")

from __future__ import annotations

import hashlib
import unittest

from clients.desktop.crypto import (
    E2EE_BLOB_ID_PREFIX,
    POLY1305_TAG_BYTES,
    E2EEDesktopBlobCryptoProvider,
    build_placeholder_blob_id,
    is_e2ee_crypto_available,
)


class DesktopCryptoTests(unittest.TestCase):
    def test_e2ee_blob_id_is_keyed_by_vault_and_content_hash(self) -> None:
        payload = b"# Meeting notes\n"
        content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        provider = E2EEDesktopBlobCryptoProvider(
            vault_id="vault-001",
            vault_key=b"\x01" * 32,
        )
        same_provider = E2EEDesktopBlobCryptoProvider(
            vault_id="vault-001",
            vault_key=b"\x01" * 32,
        )
        other_vault = E2EEDesktopBlobCryptoProvider(
            vault_id="vault-002",
            vault_key=b"\x01" * 32,
        )
        other_key = E2EEDesktopBlobCryptoProvider(
            vault_id="vault-001",
            vault_key=b"\x02" * 32,
        )

        blob_id = provider.build_blob_id(content_hash)

        self.assertTrue(blob_id.startswith(E2EE_BLOB_ID_PREFIX))
        self.assertEqual(blob_id, same_provider.build_blob_id(content_hash))
        self.assertNotEqual(blob_id, other_vault.build_blob_id(content_hash))
        self.assertNotEqual(blob_id, other_key.build_blob_id(content_hash))
        self.assertNotEqual(blob_id, build_placeholder_blob_id(content_hash))

    def test_e2ee_provider_rejects_invalid_vault_key_length(self) -> None:
        with self.assertRaisesRegex(ValueError, "vault_key must be 32 bytes"):
            E2EEDesktopBlobCryptoProvider(vault_id="vault-001", vault_key=b"short")

    @unittest.skipUnless(is_e2ee_crypto_available(), "PyNaCl is not installed")
    def test_e2ee_encrypt_decrypt_round_trip_validates_content_hash(self) -> None:
        payload = b"# Encrypted\n"
        content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        provider = E2EEDesktopBlobCryptoProvider(
            vault_id="vault-001",
            vault_key=b"\x01" * 32,
        )

        encrypted = provider.encrypt_payload(payload)
        decrypted = provider.decrypt_payload(encrypted, content_hash=content_hash)

        self.assertNotEqual(encrypted[:-POLY1305_TAG_BYTES], payload)
        self.assertEqual(len(encrypted), len(payload) + POLY1305_TAG_BYTES)
        self.assertEqual(decrypted, payload)

    @unittest.skipUnless(is_e2ee_crypto_available(), "PyNaCl is not installed")
    def test_e2ee_decrypt_rejects_wrong_key(self) -> None:
        payload = b"# Secret\n"
        content_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
        provider = E2EEDesktopBlobCryptoProvider(
            vault_id="vault-001",
            vault_key=b"\x01" * 32,
        )
        wrong_provider = E2EEDesktopBlobCryptoProvider(
            vault_id="vault-001",
            vault_key=b"\x02" * 32,
        )

        encrypted = provider.encrypt_payload(payload)

        with self.assertRaisesRegex(ValueError, "authentication failed"):
            wrong_provider.decrypt_payload(encrypted, content_hash=content_hash)


if __name__ == "__main__":
    unittest.main()

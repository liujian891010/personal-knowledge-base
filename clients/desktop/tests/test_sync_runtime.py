from __future__ import annotations

import unittest

from clients.desktop import (
    DesktopSyncHttpConfig,
    build_desktop_sync_runtime,
    build_desktop_sync_session,
)


class DesktopSyncHttpConfigTests(unittest.TestCase):
    def test_rejects_blank_required_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "base_url"):
            DesktopSyncHttpConfig(
                base_url=" ",
                vault_id="vault-001",
                device_id="desktop-shanghai",
            )
        with self.assertRaisesRegex(ValueError, "vault_id"):
            DesktopSyncHttpConfig(
                base_url="https://sync.example.com",
                vault_id=" ",
                device_id="desktop-shanghai",
            )
        with self.assertRaisesRegex(ValueError, "device_id"):
            DesktopSyncHttpConfig(
                base_url="https://sync.example.com",
                vault_id="vault-001",
                device_id=" ",
            )

    def test_rejects_non_positive_timeouts(self) -> None:
        with self.assertRaisesRegex(ValueError, "request_timeout_seconds"):
            DesktopSyncHttpConfig(
                base_url="https://sync.example.com",
                vault_id="vault-001",
                device_id="desktop-shanghai",
                request_timeout_seconds=0,
            )
        with self.assertRaisesRegex(ValueError, "blob_timeout_seconds"):
            DesktopSyncHttpConfig(
                base_url="https://sync.example.com",
                vault_id="vault-001",
                device_id="desktop-shanghai",
                blob_timeout_seconds=-1,
            )


class DesktopSyncRuntimeTests(unittest.TestCase):
    def test_builds_runtime_with_shared_opener_by_default(self) -> None:
        config = DesktopSyncHttpConfig(
            base_url="https://sync.example.com/api",
            vault_id="vault-001",
            device_id="desktop-shanghai",
            bearer_token="token-123",
            request_timeout_seconds=12.5,
            blob_timeout_seconds=48.0,
            user_agent="pkb-desktop/1.0",
        )

        def opener(request, timeout):  # pragma: no cover - identity only
            raise AssertionError(f"unexpected network request: {request.full_url} timeout={timeout}")

        runtime = build_desktop_sync_runtime(config, api_opener=opener)

        self.assertEqual(runtime.vault_id, "vault-001")
        self.assertEqual(runtime.device_id, "desktop-shanghai")
        self.assertIs(runtime.session.transport, runtime.transport)
        self.assertIs(runtime.session.uploader, runtime.uploader)
        self.assertIs(runtime.session.downloader, runtime.downloader)
        self.assertEqual(runtime.transport.base_url, "https://sync.example.com/api")
        self.assertEqual(runtime.transport.bearer_token, "token-123")
        self.assertEqual(runtime.transport.timeout_seconds, 12.5)
        self.assertEqual(runtime.transport.user_agent, "pkb-desktop/1.0")
        self.assertIs(runtime.transport.opener, opener)
        self.assertEqual(runtime.uploader.timeout_seconds, 48.0)
        self.assertEqual(runtime.downloader.timeout_seconds, 48.0)
        self.assertEqual(runtime.uploader.user_agent, "pkb-desktop/1.0")
        self.assertEqual(runtime.downloader.user_agent, "pkb-desktop/1.0")
        self.assertIs(runtime.uploader.opener, opener)
        self.assertIs(runtime.downloader.opener, opener)

    def test_allows_distinct_blob_opener(self) -> None:
        config = DesktopSyncHttpConfig(
            base_url="https://sync.example.com/api",
            vault_id="vault-001",
            device_id="desktop-shanghai",
        )

        def api_opener(request, timeout):  # pragma: no cover - identity only
            raise AssertionError(f"unexpected api request: {request.full_url} timeout={timeout}")

        def blob_opener(request, timeout):  # pragma: no cover - identity only
            raise AssertionError(f"unexpected blob request: {request.full_url} timeout={timeout}")

        runtime = build_desktop_sync_runtime(
            config,
            api_opener=api_opener,
            blob_opener=blob_opener,
        )

        self.assertIs(runtime.transport.opener, api_opener)
        self.assertIs(runtime.uploader.opener, blob_opener)
        self.assertIs(runtime.downloader.opener, blob_opener)

    def test_build_desktop_sync_session_returns_wired_session(self) -> None:
        config = DesktopSyncHttpConfig(
            base_url="https://sync.example.com/api",
            vault_id="vault-001",
            device_id="desktop-shanghai",
        )

        session = build_desktop_sync_session(config)

        self.assertEqual(session.transport.base_url, "https://sync.example.com/api")
        self.assertIsNotNone(session.uploader)
        self.assertIsNotNone(session.downloader)


if __name__ == "__main__":
    unittest.main()

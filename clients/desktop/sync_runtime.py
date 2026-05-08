from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from vault_core import (
    CapabilityBlobDownloader,
    CapabilityBlobUploader,
    JsonHttpSyncTransport,
    VaultSyncSession,
)
from vault_core.sync_http import UrlopenLike


@dataclass(frozen=True)
class DesktopSyncHttpConfig:
    """Desktop-side HTTP sync settings for a single vault."""

    base_url: str
    vault_id: str
    device_id: str
    bearer_token: Optional[str] = None
    request_timeout_seconds: float = 30.0
    blob_timeout_seconds: float = 60.0
    user_agent: str = "pkb-desktop-sync/0.1"

    def __post_init__(self) -> None:
        if not self.base_url.strip():
            raise ValueError("base_url must be non-empty")
        if not self.vault_id.strip():
            raise ValueError("vault_id must be non-empty")
        if not self.device_id.strip():
            raise ValueError("device_id must be non-empty")
        if not self.user_agent.strip():
            raise ValueError("user_agent must be non-empty")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.blob_timeout_seconds <= 0:
            raise ValueError("blob_timeout_seconds must be positive")


@dataclass(frozen=True)
class DesktopSyncRuntime:
    """Concrete desktop wiring for AG05 sync operations."""

    config: DesktopSyncHttpConfig
    transport: JsonHttpSyncTransport
    uploader: CapabilityBlobUploader
    downloader: CapabilityBlobDownloader
    session: VaultSyncSession

    @property
    def vault_id(self) -> str:
        return self.config.vault_id

    @property
    def device_id(self) -> str:
        return self.config.device_id


def build_desktop_sync_runtime(
    config: DesktopSyncHttpConfig,
    *,
    api_opener: Optional[UrlopenLike] = None,
    blob_opener: Optional[UrlopenLike] = None,
) -> DesktopSyncRuntime:
    resolved_blob_opener = blob_opener if blob_opener is not None else api_opener

    transport_kwargs = {
        "base_url": config.base_url,
        "bearer_token": config.bearer_token,
        "timeout_seconds": config.request_timeout_seconds,
        "user_agent": config.user_agent,
    }
    if api_opener is not None:
        transport_kwargs["opener"] = api_opener

    blob_kwargs = {
        "timeout_seconds": config.blob_timeout_seconds,
        "user_agent": config.user_agent,
    }
    if resolved_blob_opener is not None:
        blob_kwargs["opener"] = resolved_blob_opener

    transport = JsonHttpSyncTransport(**transport_kwargs)
    uploader = CapabilityBlobUploader(**blob_kwargs)
    downloader = CapabilityBlobDownloader(**blob_kwargs)
    session = VaultSyncSession(
        transport=transport,
        uploader=uploader,
        downloader=downloader,
    )
    return DesktopSyncRuntime(
        config=config,
        transport=transport,
        uploader=uploader,
        downloader=downloader,
        session=session,
    )


def build_desktop_sync_session(
    config: DesktopSyncHttpConfig,
    *,
    api_opener: Optional[UrlopenLike] = None,
    blob_opener: Optional[UrlopenLike] = None,
) -> VaultSyncSession:
    return build_desktop_sync_runtime(
        config,
        api_opener=api_opener,
        blob_opener=blob_opener,
    ).session

import { useEffect, useMemo, useState } from 'react';

import bundledExampleSnapshot from '../fixtures/local-settings-snapshot.example.json';
import {
  parseLocalSettingsSnapshot,
  summarizeLocalSettingsSnapshot,
  type LocalSettingsSnapshot,
  type LocalSettingsSummary,
  type LocalSettingsWritePayload,
} from './settingsSnapshot';
import { syncBridgeUrl } from './syncBridgeConfig';

const fallbackSnapshot = parseLocalSettingsSnapshot(bundledExampleSnapshot);

type LocalSettingsSnapshotSource = 'loading' | 'bridge' | 'live-fixture' | 'example';
let cachedSnapshot: LocalSettingsSnapshot | null = null;
let cachedSource: LocalSettingsSnapshotSource | null = null;

export function invalidateLocalSettingsCache(): void {
  cachedSnapshot = null;
  cachedSource = null;
}

interface SettingsSnapshotLoadResult {
  snapshot: LocalSettingsSnapshot;
  source: LocalSettingsSnapshotSource;
  error: string | null;
}

export interface LocalSettingsController {
  snapshot: LocalSettingsSnapshot;
  summary: LocalSettingsSummary;
  source: LocalSettingsSnapshotSource;
  lastError: string | null;
  isRefreshing: boolean;
  isSaving: boolean;
  isCryptoMutating: boolean;
  savedAtMs: number | null;
  refresh: () => Promise<void>;
  saveSettings: (payload: LocalSettingsWritePayload) => Promise<void>;
  unlockCrypto: (vaultKeyBase64: string) => Promise<void>;
  lockCrypto: () => Promise<void>;
  exportCryptoRecoveryPackage: (recoveryPhrase: string) => Promise<string | null>;
  importCryptoRecoveryPackage: (recoveryPhrase: string, recoveryPackageJson: string) => Promise<boolean>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isErrorPayload(payload: unknown): payload is { code?: string; message?: string } {
  return typeof payload === 'object' && payload !== null;
}

function canUseLiveFixtureFallback(): boolean {
  return typeof window !== 'undefined' && window.location.protocol !== 'file:';
}

async function responseErrorMessage(response: Response, source: string): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (isErrorPayload(payload) && typeof payload.message === 'string') {
      const code = typeof payload.code === 'string' ? ` (${payload.code})` : '';
      return `${source} returned ${response.status}${code}: ${payload.message}`;
    }
  } catch {
    // Ignore non-JSON responses and fall back to the HTTP status.
  }
  return `${source} returned ${response.status}`;
}

async function loadSnapshot(): Promise<SettingsSnapshotLoadResult> {
  let bridgeError: string | null = null;
  let fixtureError: string | null = null;

  try {
    const bridgeResponse = await fetch(`${syncBridgeUrl}/api/settings/snapshot`, { cache: 'no-store' });
    if (bridgeResponse.ok) {
      return {
        snapshot: parseLocalSettingsSnapshot(await bridgeResponse.json()),
        source: 'bridge',
        error: null,
      };
    }
    bridgeError = await responseErrorMessage(bridgeResponse, 'settings bridge');
  } catch (error) {
    bridgeError = `settings bridge unavailable: ${errorMessage(error)}`;
  }

  if (!canUseLiveFixtureFallback()) {
    return {
      snapshot: fallbackSnapshot,
      source: 'example',
      error: bridgeError,
    };
  }

  try {
    const fixtureResponse = await fetch('./fixtures/local-settings-snapshot.json', { cache: 'no-store' });
    if (fixtureResponse.ok) {
      return {
        snapshot: parseLocalSettingsSnapshot(await fixtureResponse.json()),
        source: 'live-fixture',
        error: bridgeError,
      };
    }
    fixtureError = await responseErrorMessage(fixtureResponse, 'settings fixture');
  } catch (error) {
    fixtureError = `settings fixture unavailable: ${errorMessage(error)}`;
  }

  return {
    snapshot: fallbackSnapshot,
    source: 'example',
    error: [bridgeError, fixtureError].filter(Boolean).join('; '),
  };
}

async function saveBridgeSettings(payload: LocalSettingsWritePayload): Promise<LocalSettingsSnapshot> {
  const response = await fetch(`${syncBridgeUrl}/api/settings/snapshot`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
    },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'settings save'));
  }
  return parseLocalSettingsSnapshot(await response.json());
}

export function useLocalSettingsController(): LocalSettingsController {
  const [snapshot, setSnapshot] = useState<LocalSettingsSnapshot>(cachedSnapshot ?? fallbackSnapshot);
  const [source, setSource] = useState<LocalSettingsSnapshotSource>(cachedSource ?? 'loading');
  const [lastError, setLastError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(!cachedSnapshot);
  const [isSaving, setIsSaving] = useState(false);
  const [isCryptoMutating, setIsCryptoMutating] = useState(false);
  const [savedAtMs, setSavedAtMs] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    setIsRefreshing(true);
    loadSnapshot()
      .then((result) => {
        if (cancelled) {
          return;
        }
        cachedSnapshot = result.snapshot;
        cachedSource = result.source;
        setSnapshot(result.snapshot);
        setSource(result.source);
        setLastError(result.error);
      })
      .finally(() => {
        if (!cancelled) {
          setIsRefreshing(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = async () => {
    setIsRefreshing(true);
    try {
      const result = await loadSnapshot();
      cachedSnapshot = result.snapshot;
      cachedSource = result.source;
      setSnapshot(result.snapshot);
      setSource(result.source);
      setLastError(result.error);
    } finally {
      setIsRefreshing(false);
    }
  };

  const saveSettings = async (payload: LocalSettingsWritePayload) => {
    setIsSaving(true);
    try {
      const nextSnapshot = await saveBridgeSettings(payload);
      cachedSnapshot = nextSnapshot;
      cachedSource = 'bridge';
      setSnapshot(nextSnapshot);
      setSource('bridge');
      setLastError(null);
      setSavedAtMs(Date.now());
    } catch (error) {
      setLastError(errorMessage(error));
    } finally {
      setIsSaving(false);
    }
  };

  const unlockCrypto = async (vaultKeyBase64: string) => {
    setIsCryptoMutating(true);
    try {
      const response = await fetch(`${syncBridgeUrl}/api/crypto/unlock`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
        },
        body: JSON.stringify({ vault_key_base64: vaultKeyBase64 }),
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response, 'crypto unlock'));
      }
      await refresh();
      setLastError(null);
      setSavedAtMs(Date.now());
    } catch (error) {
      setLastError(errorMessage(error));
    } finally {
      setIsCryptoMutating(false);
    }
  };

  const lockCrypto = async () => {
    setIsCryptoMutating(true);
    try {
      const response = await fetch(`${syncBridgeUrl}/api/crypto/lock`, {
        method: 'POST',
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response, 'crypto lock'));
      }
      await refresh();
      setLastError(null);
      setSavedAtMs(Date.now());
    } catch (error) {
      setLastError(errorMessage(error));
    } finally {
      setIsCryptoMutating(false);
    }
  };

  const exportCryptoRecoveryPackage = async (recoveryPhrase: string) => {
    setIsCryptoMutating(true);
    try {
      const response = await fetch(`${syncBridgeUrl}/api/crypto/recovery/export`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
        },
        body: JSON.stringify({ recovery_phrase: recoveryPhrase }),
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response, 'crypto recovery export'));
      }
      const payload = await response.json();
      if (!payload || typeof payload.recovery_package_json !== 'string') {
        throw new Error('crypto recovery export returned no recovery_package_json');
      }
      setLastError(null);
      setSavedAtMs(Date.now());
      return payload.recovery_package_json as string;
    } catch (error) {
      setLastError(errorMessage(error));
      return null;
    } finally {
      setIsCryptoMutating(false);
    }
  };

  const importCryptoRecoveryPackage = async (recoveryPhrase: string, recoveryPackageJson: string) => {
    setIsCryptoMutating(true);
    try {
      const response = await fetch(`${syncBridgeUrl}/api/crypto/recovery/import`, {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          recovery_phrase: recoveryPhrase,
          recovery_package_json: recoveryPackageJson,
        }),
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response, 'crypto recovery import'));
      }
      await refresh();
      setLastError(null);
      setSavedAtMs(Date.now());
      return true;
    } catch (error) {
      setLastError(errorMessage(error));
      return false;
    } finally {
      setIsCryptoMutating(false);
    }
  };

  const summary = useMemo(() => summarizeLocalSettingsSnapshot(snapshot), [snapshot]);
  return {
    snapshot,
    summary,
    source,
    lastError,
    isRefreshing,
    isSaving,
    isCryptoMutating,
    savedAtMs,
    refresh,
    saveSettings,
    unlockCrypto,
    lockCrypto,
    exportCryptoRecoveryPackage,
    importCryptoRecoveryPackage,
  };
}

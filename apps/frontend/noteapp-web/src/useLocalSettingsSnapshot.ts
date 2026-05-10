import { useEffect, useMemo, useState } from 'react';

import bundledExampleSnapshot from '../fixtures/local-settings-snapshot.example.json';
import {
  parseLocalSettingsSnapshot,
  summarizeLocalSettingsSnapshot,
  type LocalSettingsSnapshot,
  type LocalSettingsSummary,
  type LocalSettingsWritePayload,
} from './settingsSnapshot';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');
const fallbackSnapshot = parseLocalSettingsSnapshot(bundledExampleSnapshot);

type LocalSettingsSnapshotSource = 'bridge' | 'live-fixture' | 'example';

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
  savedAtMs: number | null;
  refresh: () => Promise<void>;
  saveSettings: (payload: LocalSettingsWritePayload) => Promise<void>;
  saveWorkspaceRoot: (vaultRoot: string) => Promise<void>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isErrorPayload(payload: unknown): payload is { code?: string; message?: string } {
  return typeof payload === 'object' && payload !== null;
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

  try {
    const fixtureResponse = await fetch('/fixtures/local-settings-snapshot.json', { cache: 'no-store' });
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

async function saveBridgeWorkspaceRoot(vaultRoot: string): Promise<LocalSettingsSnapshot> {
  const response = await fetch(`${syncBridgeUrl}/api/workspace/root`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
    },
    body: JSON.stringify({ vault_root: vaultRoot }),
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'workspace root save'));
  }
  return parseLocalSettingsSnapshot(await response.json());
}

export function useLocalSettingsController(): LocalSettingsController {
  const [snapshot, setSnapshot] = useState<LocalSettingsSnapshot>(fallbackSnapshot);
  const [source, setSource] = useState<LocalSettingsSnapshotSource>('example');
  const [lastError, setLastError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [savedAtMs, setSavedAtMs] = useState<number | null>(null);

  useEffect(() => {
    let cancelled = false;

    setIsRefreshing(true);
    loadSnapshot()
      .then((result) => {
        if (cancelled) {
          return;
        }
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
      setSnapshot(await saveBridgeSettings(payload));
      setSource('bridge');
      setLastError(null);
      setSavedAtMs(Date.now());
    } catch (error) {
      setLastError(errorMessage(error));
    } finally {
      setIsSaving(false);
    }
  };

  const saveWorkspaceRoot = async (vaultRoot: string) => {
    setIsSaving(true);
    try {
      setSnapshot(await saveBridgeWorkspaceRoot(vaultRoot));
      setSource('bridge');
      setLastError(null);
      setSavedAtMs(Date.now());
    } catch (error) {
      setLastError(errorMessage(error));
    } finally {
      setIsSaving(false);
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
    savedAtMs,
    refresh,
    saveSettings,
    saveWorkspaceRoot,
  };
}

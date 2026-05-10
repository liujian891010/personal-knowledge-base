import { useEffect, useMemo, useState } from 'react';

import bundledExampleSnapshot from '../fixtures/live-sync-shell.example.json';
import {
  parseSyncShellSnapshot,
  summarizeSyncShellSnapshot,
  type SyncShellSnapshot,
  type SyncShellSummary,
} from './syncShell';

const syncBridgeUrl = 'http://127.0.0.1:3187';
const fallbackSnapshot = parseSyncShellSnapshot(bundledExampleSnapshot);

type SyncShellSource = 'bridge' | 'live-fixture' | 'example';

interface SnapshotLoadResult {
  snapshot: SyncShellSnapshot;
  source: SyncShellSource;
  error: string | null;
}

export interface SyncShellController {
  summary: SyncShellSummary;
  source: SyncShellSource;
  lastError: string | null;
  isRefreshing: boolean;
  isExecuting: boolean;
  refresh: () => Promise<void>;
  executePrimaryAction: () => Promise<void>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function loadSnapshot(): Promise<SnapshotLoadResult> {
  let bridgeError: string | null = null;
  let fixtureError: string | null = null;

  try {
    const bridgeResponse = await fetch(`${syncBridgeUrl}/api/sync/snapshot`, { cache: 'no-store' });
    if (bridgeResponse.ok) {
      return {
        snapshot: parseSyncShellSnapshot(await bridgeResponse.json()),
        source: 'bridge',
        error: null,
      };
    }
    bridgeError = `bridge returned ${bridgeResponse.status}`;
  } catch (error) {
    bridgeError = `bridge unavailable: ${errorMessage(error)}`;
  }

  try {
    const fixtureResponse = await fetch('/fixtures/live-sync-shell.json', { cache: 'no-store' });
    if (fixtureResponse.ok) {
      return {
        snapshot: parseSyncShellSnapshot(await fixtureResponse.json()),
        source: 'live-fixture',
        error: bridgeError,
      };
    }
    fixtureError = `live fixture returned ${fixtureResponse.status}`;
  } catch (error) {
    fixtureError = `live fixture unavailable: ${errorMessage(error)}`;
  }

  return {
    snapshot: fallbackSnapshot,
    source: 'example',
    error: [bridgeError, fixtureError].filter(Boolean).join('; '),
  };
}

async function executeAction(actionId: string): Promise<SyncShellSnapshot> {
  const response = await fetch(`${syncBridgeUrl}/api/sync/actions/${encodeURIComponent(actionId)}`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(`sync action failed: ${response.status}`);
  }
  return parseSyncShellSnapshot(await response.json());
}

export function useSyncShellController(): SyncShellController {
  const [snapshot, setSnapshot] = useState<SyncShellSnapshot>(fallbackSnapshot);
  const [source, setSource] = useState<SyncShellSource>('example');
  const [lastError, setLastError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);

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

  const executePrimaryAction = async () => {
    const action = snapshot.sync_center.panel.primary_action;
    if (!action.enabled || isExecuting) {
      return;
    }
    setIsExecuting(true);
    try {
      setSnapshot(await executeAction(action.action_id));
      setSource('bridge');
      setLastError(null);
    } catch (error) {
      setLastError(errorMessage(error));
    } finally {
      setIsExecuting(false);
    }
  };

  const summary = useMemo(() => summarizeSyncShellSnapshot(snapshot), [snapshot]);
  return {
    summary,
    source,
    lastError,
    isRefreshing,
    isExecuting,
    refresh,
    executePrimaryAction,
  };
}

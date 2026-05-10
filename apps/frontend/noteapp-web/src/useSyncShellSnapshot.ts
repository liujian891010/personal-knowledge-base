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

export interface SyncShellController {
  summary: SyncShellSummary;
  isRefreshing: boolean;
  isExecuting: boolean;
  refresh: () => Promise<void>;
  executePrimaryAction: () => Promise<void>;
}

async function loadSnapshot(): Promise<SyncShellSnapshot> {
  try {
    const bridgeResponse = await fetch(`${syncBridgeUrl}/api/sync/snapshot`, { cache: 'no-store' });
    if (bridgeResponse.ok) {
      return parseSyncShellSnapshot(await bridgeResponse.json());
    }
  } catch {
    // Fall through to static live fixture and bundled example.
  }

  try {
    const fixtureResponse = await fetch('/fixtures/live-sync-shell.json', { cache: 'no-store' });
    if (fixtureResponse.ok) {
      return parseSyncShellSnapshot(await fixtureResponse.json());
    }
  } catch {
    // Fall through to bundled example.
  }

  return fallbackSnapshot;
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
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);

  useEffect(() => {
    let cancelled = false;

    setIsRefreshing(true);
    loadSnapshot()
      .then((loaded) => {
        if (cancelled) {
          return;
        }
        setSnapshot(loaded);
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
      setSnapshot(await loadSnapshot());
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
    } finally {
      setIsExecuting(false);
    }
  };

  const summary = useMemo(() => summarizeSyncShellSnapshot(snapshot), [snapshot]);
  return {
    summary,
    isRefreshing,
    isExecuting,
    refresh,
    executePrimaryAction,
  };
}

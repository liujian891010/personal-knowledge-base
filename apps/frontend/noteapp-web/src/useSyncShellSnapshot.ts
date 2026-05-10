import { useEffect, useMemo, useState } from 'react';

import bundledExampleSnapshot from '../fixtures/live-sync-shell.example.json';
import {
  parseSyncShellSnapshot,
  summarizeSyncShellSnapshot,
  type SyncShellSnapshot,
  type SyncShellSummary,
} from './syncShell';

const fallbackSnapshot = parseSyncShellSnapshot(bundledExampleSnapshot);

export function useSyncShellSummary(): SyncShellSummary {
  const [snapshot, setSnapshot] = useState<SyncShellSnapshot>(fallbackSnapshot);

  useEffect(() => {
    let cancelled = false;

    fetch('/fixtures/live-sync-shell.json', { cache: 'no-store' })
      .then((response) => {
        if (!response.ok) {
          return null;
        }
        return response.json();
      })
      .then((payload) => {
        if (cancelled || payload === null) {
          return;
        }
        setSnapshot(parseSyncShellSnapshot(payload));
      })
      .catch(() => {
        if (!cancelled) {
          setSnapshot(fallbackSnapshot);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  return useMemo(() => summarizeSyncShellSnapshot(snapshot), [snapshot]);
}

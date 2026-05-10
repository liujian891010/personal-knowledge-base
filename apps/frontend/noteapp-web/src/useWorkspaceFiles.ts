import { useEffect, useMemo, useState } from 'react';

import bundledExampleSnapshot from '../fixtures/workspace-files.example.json';
import {
  parseWorkspaceFilesSnapshot,
  summarizeWorkspaceFilesSnapshot,
  type WorkspaceFileEntry,
  type WorkspaceFilesSnapshot,
  type WorkspaceFilesSummary,
} from './workspaceFiles';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');
const fallbackSnapshot = parseWorkspaceFilesSnapshot(bundledExampleSnapshot);

type WorkspaceFilesSource = 'bridge' | 'live-fixture' | 'example';

interface WorkspaceFilesLoadResult {
  snapshot: WorkspaceFilesSnapshot;
  source: WorkspaceFilesSource;
  error: string | null;
}

export interface WorkspaceFilesController {
  summary: WorkspaceFilesSummary;
  files: WorkspaceFileEntry[];
  source: WorkspaceFilesSource;
  lastError: string | null;
  isRefreshing: boolean;
  refresh: () => Promise<void>;
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

async function loadSnapshot(): Promise<WorkspaceFilesLoadResult> {
  let bridgeError: string | null = null;
  let fixtureError: string | null = null;

  try {
    const bridgeResponse = await fetch(`${syncBridgeUrl}/api/workspace/files`, { cache: 'no-store' });
    if (bridgeResponse.ok) {
      return {
        snapshot: parseWorkspaceFilesSnapshot(await bridgeResponse.json()),
        source: 'bridge',
        error: null,
      };
    }
    bridgeError = await responseErrorMessage(bridgeResponse, 'workspace bridge');
  } catch (error) {
    bridgeError = `workspace bridge unavailable: ${errorMessage(error)}`;
  }

  try {
    const fixtureResponse = await fetch('/fixtures/workspace-files.json', { cache: 'no-store' });
    if (fixtureResponse.ok) {
      return {
        snapshot: parseWorkspaceFilesSnapshot(await fixtureResponse.json()),
        source: 'live-fixture',
        error: bridgeError,
      };
    }
    fixtureError = await responseErrorMessage(fixtureResponse, 'workspace fixture');
  } catch (error) {
    fixtureError = `workspace fixture unavailable: ${errorMessage(error)}`;
  }

  return {
    snapshot: fallbackSnapshot,
    source: 'example',
    error: [bridgeError, fixtureError].filter(Boolean).join('; '),
  };
}

export function useWorkspaceFilesController(): WorkspaceFilesController {
  const [snapshot, setSnapshot] = useState<WorkspaceFilesSnapshot>(fallbackSnapshot);
  const [source, setSource] = useState<WorkspaceFilesSource>('example');
  const [lastError, setLastError] = useState<string | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);

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

  const summary = useMemo(() => summarizeWorkspaceFilesSnapshot(snapshot), [snapshot]);
  return {
    summary,
    files: snapshot.files,
    source,
    lastError,
    isRefreshing,
    refresh,
  };
}

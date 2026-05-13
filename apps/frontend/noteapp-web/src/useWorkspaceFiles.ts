import { useCallback, useEffect, useMemo, useState } from 'react';

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
let cachedLoadResult: WorkspaceFilesLoadResult | null = null;

type WorkspaceFilesSource = 'bridge' | 'live-fixture' | 'example';

interface WorkspaceFilesLoadResult {
  snapshot: WorkspaceFilesSnapshot;
  source: WorkspaceFilesSource;
  error: string | null;
}

export function invalidateWorkspaceFilesCache(): void {
  cachedLoadResult = null;
}

export interface WorkspaceFilesController {
  summary: WorkspaceFilesSummary;
  files: WorkspaceFileEntry[];
  source: WorkspaceFilesSource;
  lastError: string | null;
  isRefreshing: boolean;
  refresh: () => Promise<void>;
  createNote: (path: string, text?: string) => Promise<WorkspaceFileEntry>;
  renameNote: (fileId: string, path: string) => Promise<WorkspaceFileEntry>;
  deleteNote: (fileId: string) => Promise<WorkspaceFileEntry>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function canUseLiveFixtureFallback(): boolean {
  return typeof window !== 'undefined' && window.location.protocol !== 'file:';
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

  if (!canUseLiveFixtureFallback()) {
    return {
      snapshot: fallbackSnapshot,
      source: 'example',
      error: bridgeError,
    };
  }

  try {
    const fixtureResponse = await fetch('./fixtures/workspace-files.json', { cache: 'no-store' });
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

function parseMutationPayload(payload: unknown): { file: WorkspaceFileEntry; files: WorkspaceFilesSnapshot } {
  if (!isErrorPayload(payload) || !('file' in payload) || !('files' in payload)) {
    throw new Error('workspace mutation response must include file and files');
  }
  const files = parseWorkspaceFilesSnapshot(payload.files);
  if (!isErrorPayload(payload.file)) {
    throw new Error('workspace mutation response file must be an object');
  }
  const file = files.files.find((item) => item.file_id === payload.file?.['file_id']);
  if (!file) {
    throw new Error('workspace mutation response file was not found in files snapshot');
  }
  return { file, files };
}

async function mutateWorkspace(
  endpoint: string,
  init: RequestInit,
  source: string,
  fallbackEndpoint?: string,
): Promise<{ file: WorkspaceFileEntry; files: WorkspaceFilesSnapshot }> {
  const response = await fetch(`${syncBridgeUrl}${endpoint}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init.headers ?? {}),
    },
  });
  if (response.status === 404 && fallbackEndpoint) {
    return mutateWorkspace(fallbackEndpoint, init, source);
  }
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, source));
  }
  return parseMutationPayload(await response.json());
}

export function useWorkspaceFilesController(enabled = true): WorkspaceFilesController {
  const [snapshot, setSnapshot] = useState<WorkspaceFilesSnapshot>(
    () => cachedLoadResult?.snapshot ?? fallbackSnapshot,
  );
  const [source, setSource] = useState<WorkspaceFilesSource>(
    () => cachedLoadResult?.source ?? 'example',
  );
  const [lastError, setLastError] = useState<string | null>(() => cachedLoadResult?.error ?? null);
  const [isRefreshing, setIsRefreshing] = useState(false);

  useEffect(() => {
    if (!enabled) {
      setIsRefreshing(false);
      setLastError(null);
      return;
    }
    if (cachedLoadResult) {
      return;
    }
    let cancelled = false;

    setIsRefreshing(true);
    loadSnapshot()
      .then((result) => {
        if (cancelled) {
          return;
        }
        cachedLoadResult = result;
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
  }, [enabled]);

  const refresh = useCallback(async () => {
    if (!enabled) {
      setIsRefreshing(false);
      return;
    }
    setIsRefreshing(true);
    try {
      const result = await loadSnapshot();
      cachedLoadResult = result;
      setSnapshot(result.snapshot);
      setSource(result.source);
      setLastError(result.error);
    } finally {
      setIsRefreshing(false);
    }
  }, [enabled]);

  const createNote = useCallback(async (path: string, text = '') => {
    if (!enabled) {
      throw new Error('workspace is not available');
    }
    setIsRefreshing(true);
    try {
      const result = await mutateWorkspace(
        '/api/workspace/notes',
        {
          method: 'POST',
          body: JSON.stringify({ path, text }),
        },
        'workspace note create',
        '/api/workspace/files',
      );
      setSnapshot(result.files);
      cachedLoadResult = {
        snapshot: result.files,
        source: 'bridge',
        error: null,
      };
      setSource('bridge');
      setLastError(null);
      return result.file;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsRefreshing(false);
    }
  }, [enabled]);

  const renameNote = useCallback(async (fileId: string, path: string) => {
    if (!enabled) {
      throw new Error('workspace is not available');
    }
    setIsRefreshing(true);
    try {
      const result = await mutateWorkspace(
        `/api/workspace/notes/${encodeURIComponent(fileId)}`,
        {
          method: 'PATCH',
          body: JSON.stringify({ path }),
        },
        'workspace note rename',
        `/api/workspace/files/${encodeURIComponent(fileId)}`,
      );
      setSnapshot(result.files);
      cachedLoadResult = {
        snapshot: result.files,
        source: 'bridge',
        error: null,
      };
      setSource('bridge');
      setLastError(null);
      return result.file;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsRefreshing(false);
    }
  }, [enabled]);

  const deleteNote = useCallback(async (fileId: string) => {
    if (!enabled) {
      throw new Error('workspace is not available');
    }
    setIsRefreshing(true);
    try {
      const result = await mutateWorkspace(
        `/api/workspace/notes/${encodeURIComponent(fileId)}`,
        {
          method: 'DELETE',
        },
        'workspace note delete',
        `/api/workspace/files/${encodeURIComponent(fileId)}`,
      );
      setSnapshot(result.files);
      cachedLoadResult = {
        snapshot: result.files,
        source: 'bridge',
        error: null,
      };
      setSource('bridge');
      setLastError(null);
      return result.file;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsRefreshing(false);
    }
  }, [enabled]);

  const summary = useMemo(() => summarizeWorkspaceFilesSnapshot(snapshot), [snapshot]);
  return {
    summary,
    files: snapshot.files,
    source,
    lastError,
    isRefreshing,
    refresh,
    createNote,
    renameNote,
    deleteNote,
  };
}

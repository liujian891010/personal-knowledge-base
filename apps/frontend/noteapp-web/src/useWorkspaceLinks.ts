import { useCallback, useState } from 'react';

import {
  parseWorkspaceNoteLinksSnapshot,
  type WorkspaceNoteLinksSnapshot,
} from './workspaceLinks';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

export interface WorkspaceLinksController {
  links: WorkspaceNoteLinksSnapshot | null;
  lastError: string | null;
  isLoading: boolean;
  loadLinks: (fileId: string) => Promise<void>;
  clearLinks: () => void;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function isErrorPayload(payload: unknown): payload is { code?: string; message?: string } {
  return typeof payload === 'object' && payload !== null;
}

async function responseErrorMessage(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (isErrorPayload(payload) && typeof payload.message === 'string') {
      const code = typeof payload.code === 'string' ? ` (${payload.code})` : '';
      return `workspace links returned ${response.status}${code}: ${payload.message}`;
    }
  } catch {
    // Fall through to the status-only error.
  }
  return `workspace links returned ${response.status}`;
}

async function fetchWorkspaceLinks(fileId: string): Promise<WorkspaceNoteLinksSnapshot> {
  const response = await fetch(
    `${syncBridgeUrl}/api/workspace/notes/${encodeURIComponent(fileId)}/links`,
    { cache: 'no-store' },
  );
  if (response.status === 404) {
    const legacyResponse = await fetch(
      `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/links`,
      { cache: 'no-store' },
    );
    if (!legacyResponse.ok) {
      throw new Error(await responseErrorMessage(legacyResponse));
    }
    return parseWorkspaceNoteLinksSnapshot(await legacyResponse.json());
  }
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }
  return parseWorkspaceNoteLinksSnapshot(await response.json());
}

export function useWorkspaceLinksController(): WorkspaceLinksController {
  const [links, setLinks] = useState<WorkspaceNoteLinksSnapshot | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const loadLinks = useCallback(async (fileId: string) => {
    setIsLoading(true);
    try {
      setLinks(await fetchWorkspaceLinks(fileId));
      setLastError(null);
    } catch (error) {
      setLinks(null);
      setLastError(errorMessage(error));
    } finally {
      setIsLoading(false);
    }
  }, []);

  const clearLinks = useCallback(() => {
    setLinks(null);
    setLastError(null);
  }, []);

  return {
    links,
    lastError,
    isLoading,
    loadLinks,
    clearLinks,
  };
}

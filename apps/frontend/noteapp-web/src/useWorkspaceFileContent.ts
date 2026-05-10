import { useCallback, useState } from 'react';

import {
  parseWorkspaceFileContent,
  type WorkspaceFileContent,
} from './workspaceFileContent';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

export interface WorkspaceFileContentController {
  content: WorkspaceFileContent | null;
  lastError: string | null;
  isLoading: boolean;
  loadContent: (fileId: string) => Promise<void>;
  clearContent: () => void;
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

async function fetchWorkspaceFileContent(fileId: string): Promise<WorkspaceFileContent> {
  const response = await fetch(
    `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/content`,
    { cache: 'no-store' },
  );
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'workspace file content'));
  }
  return parseWorkspaceFileContent(await response.json());
}

export function useWorkspaceFileContentController(): WorkspaceFileContentController {
  const [content, setContent] = useState<WorkspaceFileContent | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const loadContent = useCallback(async (fileId: string) => {
    setIsLoading(true);
    try {
      setContent(await fetchWorkspaceFileContent(fileId));
      setLastError(null);
    } catch (error) {
      setLastError(errorMessage(error));
      setContent(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const clearContent = useCallback(() => {
    setContent(null);
    setLastError(null);
  }, []);

  return {
    content,
    lastError,
    isLoading,
    loadContent,
    clearContent,
  };
}

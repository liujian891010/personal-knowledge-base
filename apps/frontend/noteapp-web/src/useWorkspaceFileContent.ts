import { useCallback, useState } from 'react';

import {
  parseWorkspaceFileContent,
  parseWorkspaceFileDraft,
  type WorkspaceFileDraft,
  type WorkspaceFileContent,
} from './workspaceFileContent';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

const cachedContentByFileId = new Map<string, WorkspaceFileContent>();
const cachedDraftByFileId = new Map<string, WorkspaceFileDraft>();

export interface WorkspaceFileContentController {
  content: WorkspaceFileContent | null;
  lastError: string | null;
  isLoading: boolean;
  isSaving: boolean;
  savedAtMs: number | null;
  draft: WorkspaceFileDraft | null;
  isDraftLoading: boolean;
  isDraftSaving: boolean;
  loadContent: (fileId: string) => Promise<void>;
  saveContent: (fileId: string, text: string) => Promise<void>;
  loadDraft: (fileId: string) => Promise<WorkspaceFileDraft>;
  saveDraft: (fileId: string, text: string) => Promise<WorkspaceFileDraft>;
  clearDraft: (fileId: string) => Promise<WorkspaceFileDraft>;
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

async function putWorkspaceFileContent(
  fileId: string,
  text: string,
): Promise<WorkspaceFileContent> {
  const response = await fetch(
    `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/content`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ text }),
    },
  );
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'workspace file content save'));
  }
  return parseWorkspaceFileContent(await response.json());
}

async function fetchWorkspaceFileDraft(fileId: string): Promise<WorkspaceFileDraft> {
  const response = await fetch(
    `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/draft`,
    { cache: 'no-store' },
  );
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'workspace file draft'));
  }
  return parseWorkspaceFileDraft(await response.json());
}

async function putWorkspaceFileDraft(fileId: string, text: string): Promise<WorkspaceFileDraft> {
  const response = await fetch(
    `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/draft`,
    {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ text }),
    },
  );
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'workspace file draft save'));
  }
  return parseWorkspaceFileDraft(await response.json());
}

async function deleteWorkspaceFileDraft(fileId: string): Promise<WorkspaceFileDraft> {
  const response = await fetch(
    `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/draft`,
    { method: 'DELETE' },
  );
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'workspace file draft clear'));
  }
  return parseWorkspaceFileDraft(await response.json());
}

export function useWorkspaceFileContentController(): WorkspaceFileContentController {
  const [content, setContent] = useState<WorkspaceFileContent | null>(null);
  const [draft, setDraft] = useState<WorkspaceFileDraft | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isDraftLoading, setIsDraftLoading] = useState(false);
  const [isDraftSaving, setIsDraftSaving] = useState(false);
  const [savedAtMs, setSavedAtMs] = useState<number | null>(null);

  const loadContent = useCallback(async (fileId: string) => {
    const cachedContent = cachedContentByFileId.get(fileId);
    if (cachedContent) {
      setContent(cachedContent);
      setLastError(null);
      setSavedAtMs(null);
      return;
    }

    setIsLoading(true);
    try {
      const nextContent = await fetchWorkspaceFileContent(fileId);
      cachedContentByFileId.set(fileId, nextContent);
      setContent(nextContent);
      setLastError(null);
      setSavedAtMs(null);
    } catch (error) {
      setLastError(errorMessage(error));
      setContent(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const saveContent = useCallback(async (fileId: string, text: string) => {
    setIsSaving(true);
    try {
      const nextContent = await putWorkspaceFileContent(fileId, text);
      cachedContentByFileId.set(fileId, nextContent);
      cachedDraftByFileId.delete(fileId);
      setContent(nextContent);
      setDraft(null);
      setLastError(null);
      setSavedAtMs(Date.now());
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsSaving(false);
    }
  }, []);

  const loadDraft = useCallback(async (fileId: string) => {
    const cachedDraft = cachedDraftByFileId.get(fileId);
    if (cachedDraft) {
      setDraft(cachedDraft.has_draft ? cachedDraft : null);
      setLastError(null);
      return cachedDraft;
    }

    setIsDraftLoading(true);
    try {
      const nextDraft = await fetchWorkspaceFileDraft(fileId);
      cachedDraftByFileId.set(fileId, nextDraft);
      setDraft(nextDraft.has_draft ? nextDraft : null);
      setLastError(null);
      return nextDraft;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsDraftLoading(false);
    }
  }, []);

  const saveDraft = useCallback(async (fileId: string, text: string) => {
    setIsDraftSaving(true);
    try {
      const nextDraft = await putWorkspaceFileDraft(fileId, text);
      cachedDraftByFileId.set(fileId, nextDraft);
      setDraft(nextDraft.has_draft ? nextDraft : null);
      setLastError(null);
      return nextDraft;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsDraftSaving(false);
    }
  }, []);

  const clearDraft = useCallback(async (fileId: string) => {
    setIsDraftSaving(true);
    try {
      const nextDraft = await deleteWorkspaceFileDraft(fileId);
      cachedDraftByFileId.set(fileId, nextDraft);
      setDraft(null);
      setLastError(null);
      return nextDraft;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsDraftSaving(false);
    }
  }, []);

  const clearContent = useCallback(() => {
    setContent(null);
    setDraft(null);
    setLastError(null);
    setSavedAtMs(null);
  }, []);

  return {
    content,
    draft,
    lastError,
    isLoading,
    isSaving,
    isDraftLoading,
    isDraftSaving,
    savedAtMs,
    loadContent,
    saveContent,
    loadDraft,
    saveDraft,
    clearDraft,
    clearContent,
  };
}

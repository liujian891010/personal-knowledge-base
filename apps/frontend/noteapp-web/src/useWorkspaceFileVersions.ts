import { useCallback, useState } from 'react';

import {
  parseWorkspaceFileVersionContent,
  parseWorkspaceFileVersionDiff,
  parseWorkspaceFileVersionList,
  parseWorkspaceFileVersionRecord,
  parseWorkspaceFileVersionRestoreResult,
  type WorkspaceFileVersionContent,
  type WorkspaceFileVersionDiff,
  type WorkspaceFileVersionList,
  type WorkspaceFileVersionRecord,
  type WorkspaceFileVersionRestoreResult,
} from './workspaceFileVersions';
import { syncBridgeUrl } from './syncBridgeConfig';

export interface WorkspaceFileVersionsController {
  versions: WorkspaceFileVersionRecord[];
  selectedVersion: WorkspaceFileVersionRecord | null;
  preview: WorkspaceFileVersionContent | null;
  diff: WorkspaceFileVersionDiff | null;
  lastError: string | null;
  isLoading: boolean;
  isPreviewLoading: boolean;
  isDiffLoading: boolean;
  isMutating: boolean;
  loadVersions: (fileId: string) => Promise<WorkspaceFileVersionList>;
  loadPreview: (fileId: string, versionId: string) => Promise<WorkspaceFileVersionContent>;
  loadDiff: (fileId: string, versionId: string) => Promise<WorkspaceFileVersionDiff>;
  updateVersion: (
    versionId: string,
    payload: { version_label?: string; change_note?: string; is_pinned?: boolean },
  ) => Promise<WorkspaceFileVersionRecord>;
  restoreVersion: (
    fileId: string,
    versionId: string,
    options?: { version_label?: string; change_note?: string; is_pinned?: boolean },
  ) => Promise<WorkspaceFileVersionRestoreResult>;
  clear: () => void;
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
    // Fall back to the HTTP status.
  }
  return `${source} returned ${response.status}`;
}

async function readJsonResponse(response: Response, source: string): Promise<unknown> {
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, source));
  }
  return response.json();
}

export function useWorkspaceFileVersionsController(): WorkspaceFileVersionsController {
  const [versions, setVersions] = useState<WorkspaceFileVersionRecord[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<WorkspaceFileVersionRecord | null>(null);
  const [preview, setPreview] = useState<WorkspaceFileVersionContent | null>(null);
  const [diff, setDiff] = useState<WorkspaceFileVersionDiff | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const [isDiffLoading, setIsDiffLoading] = useState(false);
  const [isMutating, setIsMutating] = useState(false);

  const loadVersions = useCallback(async (fileId: string) => {
    setIsLoading(true);
    try {
      const payload = await readJsonResponse(
        await fetch(`${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/versions`, {
          cache: 'no-store',
        }),
        'workspace file versions',
      );
      const list = parseWorkspaceFileVersionList(payload);
      setVersions(list.versions);
      setSelectedVersion((current) => {
        if (current && list.versions.some((item) => item.version_id === current.version_id)) {
          return current;
        }
        return list.versions[0] ?? null;
      });
      setLastError(null);
      return list;
    } catch (error) {
      setLastError(errorMessage(error));
      setVersions([]);
      setSelectedVersion(null);
      throw error;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const loadPreview = useCallback(async (fileId: string, versionId: string) => {
    setIsPreviewLoading(true);
    try {
      const payload = await readJsonResponse(
        await fetch(
          `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/content`,
          { cache: 'no-store' },
        ),
        'workspace file version preview',
      );
      const content = parseWorkspaceFileVersionContent(payload);
      setSelectedVersion(content.version);
      setPreview(content);
      setLastError(null);
      return content;
    } catch (error) {
      setLastError(errorMessage(error));
      setPreview(null);
      throw error;
    } finally {
      setIsPreviewLoading(false);
    }
  }, []);

  const loadDiff = useCallback(async (fileId: string, versionId: string) => {
    setIsDiffLoading(true);
    try {
      const payload = await readJsonResponse(
        await fetch(
          `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/diff`,
          { cache: 'no-store' },
        ),
        'workspace file version diff',
      );
      const nextDiff = parseWorkspaceFileVersionDiff(payload);
      setSelectedVersion(nextDiff.version);
      setDiff(nextDiff);
      setLastError(null);
      return nextDiff;
    } catch (error) {
      setLastError(errorMessage(error));
      setDiff(null);
      throw error;
    } finally {
      setIsDiffLoading(false);
    }
  }, []);

  const updateVersion = useCallback(async (
    versionId: string,
    payload: { version_label?: string; change_note?: string; is_pinned?: boolean },
  ) => {
    setIsMutating(true);
    try {
      const responsePayload = await readJsonResponse(
        await fetch(`${syncBridgeUrl}/api/workspace/file-versions/${encodeURIComponent(versionId)}`, {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(payload),
        }),
        'workspace file version update',
      );
      const responseObject = (
        typeof responsePayload === 'object' && responsePayload !== null
      )
        ? responsePayload as Record<string, unknown>
        : {};
      const responseField = responseObject.response;
      const recordPayload = (
        isErrorPayload(responseField)
        && 'version' in responseField
      )
        ? responseField.version
        : responsePayload;
      const updated = parseWorkspaceFileVersionRecord(recordPayload);
      setVersions((current) => current.map((item) => (
        item.version_id === updated.version_id ? updated : item
      )));
      setSelectedVersion((current) => (
        current?.version_id === updated.version_id ? updated : current
      ));
      setPreview((current) => (
        current?.version.version_id === updated.version_id
          ? { ...current, version: updated }
          : current
      ));
      setDiff((current) => (
        current?.version.version_id === updated.version_id
          ? { ...current, version: updated }
          : current
      ));
      setLastError(null);
      return updated;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsMutating(false);
    }
  }, []);

  const restoreVersion = useCallback(async (
    fileId: string,
    versionId: string,
    options: { version_label?: string; change_note?: string; is_pinned?: boolean } = {},
  ) => {
    setIsMutating(true);
    try {
      const payload = await readJsonResponse(
        await fetch(
          `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/versions/${encodeURIComponent(versionId)}/restore`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
            },
            body: JSON.stringify(options),
          },
        ),
        'workspace file version restore',
      );
      const restored = parseWorkspaceFileVersionRestoreResult(payload);
      setLastError(null);
      return restored;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsMutating(false);
    }
  }, []);

  const clear = useCallback(() => {
    setVersions([]);
    setSelectedVersion(null);
    setPreview(null);
    setDiff(null);
    setLastError(null);
    setIsLoading(false);
    setIsPreviewLoading(false);
    setIsDiffLoading(false);
    setIsMutating(false);
  }, []);

  return {
    versions,
    selectedVersion,
    preview,
    diff,
    lastError,
    isLoading,
    isPreviewLoading,
    isDiffLoading,
    isMutating,
    loadVersions,
    loadPreview,
    loadDiff,
    updateVersion,
    restoreVersion,
    clear,
  };
}

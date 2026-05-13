import { useCallback, useEffect, useMemo, useState } from 'react';

import { selectDesktopWorkspaceFolder } from './desktop';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

export interface RegisteredWorkspace {
  id: string;
  name: string;
  vault_root: string;
  created_at_ms: number;
  last_opened_at_ms: number;
  exists: boolean;
  initialized: boolean;
  is_active: boolean;
}

interface WorkspaceRegistryPayload {
  schema_version: string;
  active_workspace_id: string | null;
  config_path: string;
  workspaces: RegisteredWorkspace[];
}

export interface WorkspaceRegistryController {
  workspaces: RegisteredWorkspace[];
  activeWorkspace: RegisteredWorkspace | null;
  activeWorkspaceId: string | null;
  isLoading: boolean;
  isMutating: boolean;
  lastError: string | null;
  refresh: () => Promise<void>;
  activateWorkspace: (workspaceId: string) => Promise<string | null>;
  selectWorkspaceFolder: () => Promise<string | null>;
  removeWorkspace: (workspaceId: string) => Promise<string | null>;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function responseErrorMessage(response: Response, source: string): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (isObject(payload) && typeof payload.message === 'string') {
      return `${source} returned ${response.status}: ${payload.message}`;
    }
  } catch {
    // Ignore and fall through.
  }
  return `${source} returned ${response.status}`;
}

function parseWorkspaceRegistryPayload(payload: unknown): WorkspaceRegistryPayload {
  if (!isObject(payload) || !Array.isArray(payload.workspaces)) {
    throw new Error('workspace registry payload must include workspaces');
  }
  return {
    schema_version: String(payload.schema_version ?? 'v1'),
    active_workspace_id: typeof payload.active_workspace_id === 'string' ? payload.active_workspace_id : null,
    config_path: String(payload.config_path ?? ''),
    workspaces: payload.workspaces.map((item) => {
      if (!isObject(item)) {
        throw new Error('workspace entry must be an object');
      }
      return {
        id: String(item.id ?? ''),
        name: String(item.name ?? ''),
        vault_root: String(item.vault_root ?? ''),
        created_at_ms: Number(item.created_at_ms ?? 0),
        last_opened_at_ms: Number(item.last_opened_at_ms ?? 0),
        exists: Boolean(item.exists),
        initialized: Boolean(item.initialized),
        is_active: Boolean(item.is_active),
      };
    }),
  };
}

async function fetchWorkspaceRegistry(path: string, init?: RequestInit): Promise<WorkspaceRegistryPayload> {
  const response = await fetch(`${syncBridgeUrl}${path}`, {
    cache: 'no-store',
    ...init,
    headers: {
      ...(init?.body ? { 'content-type': 'application/json' } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, path));
  }
  const payload = await response.json();
  if (isObject(payload) && 'registry' in payload) {
    return parseWorkspaceRegistryPayload(payload.registry);
  }
  return parseWorkspaceRegistryPayload(payload);
}

async function registerWorkspaceByPath(vaultRoot: string): Promise<WorkspaceRegistryPayload> {
  return fetchWorkspaceRegistry('/api/workspaces', {
    method: 'POST',
    body: JSON.stringify({
      vault_root: vaultRoot,
      activate: true,
    }),
  });
}

export function useWorkspaceRegistryController(enabled = true): WorkspaceRegistryController {
  const [registry, setRegistry] = useState<WorkspaceRegistryPayload | null>(null);
  const [isLoading, setIsLoading] = useState(enabled);
  const [isMutating, setIsMutating] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const payload = await fetchWorkspaceRegistry('/api/workspaces');
      setRegistry(payload);
      setLastError(null);
    } catch (error) {
      setLastError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!enabled) {
      setRegistry(null);
      setLastError(null);
      setIsLoading(false);
      return;
    }
    void refresh();
  }, [enabled, refresh]);

  const activateWorkspace = useCallback(async (workspaceId: string) => {
    setIsMutating(true);
    try {
      const payload = await fetchWorkspaceRegistry(`/api/workspaces/${encodeURIComponent(workspaceId)}/activate`, {
        method: 'POST',
      });
      setRegistry(payload);
      setLastError(null);
      return payload.active_workspace_id;
    } catch (error) {
      setLastError(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setIsMutating(false);
    }
  }, []);

  const selectWorkspaceFolder = useCallback(async () => {
    setIsMutating(true);
    try {
      const desktopSelection = await selectDesktopWorkspaceFolder();
      if (desktopSelection?.canceled) {
        setLastError(null);
        return null;
      }
      const payload = desktopSelection?.path
        ? await registerWorkspaceByPath(desktopSelection.path)
        : await fetchWorkspaceRegistry('/api/workspaces/select-folder', {
            method: 'POST',
          });
      setRegistry(payload);
      setLastError(null);
      return payload.active_workspace_id;
    } catch (error) {
      setLastError(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setIsMutating(false);
    }
  }, []);

  const removeWorkspace = useCallback(async (workspaceId: string) => {
    setIsMutating(true);
    try {
      const payload = await fetchWorkspaceRegistry(`/api/workspaces/${encodeURIComponent(workspaceId)}`, {
        method: 'DELETE',
      });
      setRegistry(payload);
      setLastError(null);
      return payload.active_workspace_id;
    } catch (error) {
      setLastError(error instanceof Error ? error.message : String(error));
      return null;
    } finally {
      setIsMutating(false);
    }
  }, []);

  const activeWorkspace = useMemo(
    () => registry?.workspaces.find((workspace) => workspace.id === registry.active_workspace_id) ?? null,
    [registry],
  );

  return {
    workspaces: registry?.workspaces ?? [],
    activeWorkspace,
    activeWorkspaceId: registry?.active_workspace_id ?? null,
    isLoading,
    isMutating,
    lastError,
    refresh,
    activateWorkspace,
    selectWorkspaceFolder,
    removeWorkspace,
  };
}

import { useCallback, useState } from 'react';

import {
  parseWorkspaceVaultDeviceHeartbeat,
  parseWorkspaceVaultDeviceList,
  parseWorkspaceVaultDeviceRevokeResult,
  type WorkspaceVaultDeviceHeartbeat,
  type WorkspaceVaultDeviceList,
  type WorkspaceVaultDeviceRecord,
  type WorkspaceVaultDeviceRevokeResult,
} from './workspaceDevices';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

export interface WorkspaceDevicesController {
  deviceList: WorkspaceVaultDeviceList | null;
  devices: WorkspaceVaultDeviceRecord[];
  lastHeartbeat: WorkspaceVaultDeviceHeartbeat | null;
  lastError: string | null;
  isLoading: boolean;
  isMutating: boolean;
  loadDevices: () => Promise<WorkspaceVaultDeviceList>;
  heartbeatDevice: () => Promise<WorkspaceVaultDeviceHeartbeat>;
  revokeDevice: (deviceId: string) => Promise<WorkspaceVaultDeviceRevokeResult>;
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
    // Fall back to HTTP status.
  }
  return `${source} returned ${response.status}`;
}

async function readJsonResponse(response: Response, source: string): Promise<unknown> {
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, source));
  }
  return response.json();
}

export function useWorkspaceDevicesController(): WorkspaceDevicesController {
  const [deviceList, setDeviceList] = useState<WorkspaceVaultDeviceList | null>(null);
  const [devices, setDevices] = useState<WorkspaceVaultDeviceRecord[]>([]);
  const [lastHeartbeat, setLastHeartbeat] = useState<WorkspaceVaultDeviceHeartbeat | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isMutating, setIsMutating] = useState(false);

  const loadDevices = useCallback(async () => {
    setIsLoading(true);
    try {
      const payload = await readJsonResponse(
        await fetch(`${syncBridgeUrl}/api/devices`, { cache: 'no-store' }),
        'workspace devices',
      );
      const list = parseWorkspaceVaultDeviceList(payload);
      setDeviceList(list);
      setDevices(list.devices);
      setLastError(null);
      return list;
    } catch (error) {
      setLastError(errorMessage(error));
      setDeviceList(null);
      setDevices([]);
      throw error;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const heartbeatDevice = useCallback(async () => {
    setIsMutating(true);
    try {
      const payload = await readJsonResponse(
        await fetch(`${syncBridgeUrl}/api/devices/heartbeat`, { method: 'POST' }),
        'workspace device heartbeat',
      );
      const heartbeat = parseWorkspaceVaultDeviceHeartbeat(payload);
      setLastHeartbeat(heartbeat);
      setDevices((current) => current.map((device) => (
        device.device_id === heartbeat.device_id
          ? {
            ...device,
            last_seen_at_ms: heartbeat.last_seen_at_ms,
            acked_revision: heartbeat.acked_revision,
            is_inactive_candidate: false,
          }
          : device
      )));
      setDeviceList((current) => (
        current
          ? { ...current, head_revision: heartbeat.head_revision }
          : current
      ));
      setLastError(null);
      return heartbeat;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsMutating(false);
    }
  }, []);

  const revokeDevice = useCallback(async (deviceId: string) => {
    setIsMutating(true);
    try {
      const payload = await readJsonResponse(
        await fetch(`${syncBridgeUrl}/api/devices/${encodeURIComponent(deviceId)}`, { method: 'DELETE' }),
        'workspace device revoke',
      );
      const result = parseWorkspaceVaultDeviceRevokeResult(payload);
      setDevices((current) => current.map((device) => (
        device.device_id === result.device_id
          ? { ...device, is_revoked: true, is_inactive_candidate: false }
          : device
      )));
      setDeviceList((current) => (
        current
          ? {
            ...current,
            devices: current.devices.map((device) => (
              device.device_id === result.device_id
                ? { ...device, is_revoked: true, is_inactive_candidate: false }
                : device
            )),
          }
          : current
      ));
      setLastError(null);
      return result;
    } catch (error) {
      setLastError(errorMessage(error));
      throw error;
    } finally {
      setIsMutating(false);
    }
  }, []);

  const clear = useCallback(() => {
    setDeviceList(null);
    setDevices([]);
    setLastHeartbeat(null);
    setLastError(null);
    setIsLoading(false);
    setIsMutating(false);
  }, []);

  return {
    deviceList,
    devices,
    lastHeartbeat,
    lastError,
    isLoading,
    isMutating,
    loadDevices,
    heartbeatDevice,
    revokeDevice,
    clear,
  };
}

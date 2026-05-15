export interface WorkspaceVaultDeviceRecord {
  device_id: string;
  device_name: string;
  platform: string;
  app_version: string | null;
  protocol_version: string;
  registered_at_ms: number | null;
  last_seen_at_ms: number | null;
  acked_revision: number;
  is_current_device: boolean;
  is_revoked: boolean;
  is_inactive_candidate: boolean;
}

export interface WorkspaceVaultDeviceList {
  vault_id: string;
  head_revision: number;
  inactive_after_ms: number;
  devices: WorkspaceVaultDeviceRecord[];
}

export interface WorkspaceVaultDeviceHeartbeat {
  vault_id: string;
  device_id: string;
  last_seen_at_ms: number;
  acked_revision: number;
  head_revision: number;
}

export interface WorkspaceVaultDeviceRevokeResult {
  device_id: string;
  revoked: boolean;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`workspace device payload is missing string field: ${key}`);
  }
  return value;
}

function optionalString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === 'string' ? value : null;
}

function requireNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`workspace device payload is missing numeric field: ${key}`);
  }
  return value;
}

function optionalNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function requireArray(payload: Record<string, unknown>, key: string): unknown[] {
  const value = payload[key];
  if (!Array.isArray(value)) {
    throw new Error(`workspace device payload is missing array field: ${key}`);
  }
  return value;
}

function unwrapResponse(payload: unknown): unknown {
  if (isObject(payload) && isObject(payload.response)) {
    return payload.response;
  }
  return payload;
}

export function parseWorkspaceVaultDeviceRecord(payload: unknown): WorkspaceVaultDeviceRecord {
  if (!isObject(payload)) {
    throw new Error('workspace device record must be an object');
  }
  return {
    device_id: requireString(payload, 'device_id'),
    device_name: requireString(payload, 'device_name'),
    platform: requireString(payload, 'platform'),
    app_version: optionalString(payload, 'app_version'),
    protocol_version: requireString(payload, 'protocol_version'),
    registered_at_ms: optionalNumber(payload, 'registered_at_ms'),
    last_seen_at_ms: optionalNumber(payload, 'last_seen_at_ms'),
    acked_revision: requireNumber(payload, 'acked_revision'),
    is_current_device: Boolean(payload.is_current_device),
    is_revoked: Boolean(payload.is_revoked),
    is_inactive_candidate: Boolean(payload.is_inactive_candidate),
  };
}

export function parseWorkspaceVaultDeviceList(payload: unknown): WorkspaceVaultDeviceList {
  const response = unwrapResponse(payload);
  if (!isObject(response)) {
    throw new Error('workspace device list must be an object');
  }
  return {
    vault_id: requireString(response, 'vault_id'),
    head_revision: requireNumber(response, 'head_revision'),
    inactive_after_ms: requireNumber(response, 'inactive_after_ms'),
    devices: requireArray(response, 'devices').map(parseWorkspaceVaultDeviceRecord),
  };
}

export function parseWorkspaceVaultDeviceHeartbeat(payload: unknown): WorkspaceVaultDeviceHeartbeat {
  const response = unwrapResponse(payload);
  if (!isObject(response)) {
    throw new Error('workspace device heartbeat must be an object');
  }
  return {
    vault_id: requireString(response, 'vault_id'),
    device_id: requireString(response, 'device_id'),
    last_seen_at_ms: requireNumber(response, 'last_seen_at_ms'),
    acked_revision: requireNumber(response, 'acked_revision'),
    head_revision: requireNumber(response, 'head_revision'),
  };
}

export function parseWorkspaceVaultDeviceRevokeResult(payload: unknown): WorkspaceVaultDeviceRevokeResult {
  if (!isObject(payload)) {
    throw new Error('workspace device revoke result must be an object');
  }
  return {
    device_id: requireString(payload, 'device_id'),
    revoked: Boolean(payload.revoked),
  };
}

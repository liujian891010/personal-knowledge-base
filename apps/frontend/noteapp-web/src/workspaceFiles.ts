export type WorkspaceFileStatus = 'active' | 'deleted' | 'conflict_copy';

export interface WorkspaceFileEntry {
  file_id: string;
  path: string;
  type: string;
  status: WorkspaceFileStatus;
  updated_at: number;
  exists_on_disk: boolean;
  size_bytes: number | null;
  content_hash?: string | null;
  last_known_revision?: number | null;
  conflict_source_file_id?: string | null;
}

export interface WorkspaceFilesSnapshot {
  schema_version: string;
  vault_id: string;
  device_id: string;
  vault_root: string;
  files: WorkspaceFileEntry[];
  total_count: number;
  active_count: number;
  missing_count: number;
}

export interface WorkspaceFilesSummary {
  schemaVersion: string;
  vaultId: string;
  deviceId: string;
  vaultRoot: string;
  totalCount: number;
  activeCount: number;
  missingCount: number;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`workspace files snapshot is missing string field: ${key}`);
  }
  return value;
}

function requireNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`workspace files snapshot is missing numeric field: ${key}`);
  }
  return value;
}

function requireArray(payload: Record<string, unknown>, key: string): unknown[] {
  const value = payload[key];
  if (!Array.isArray(value)) {
    throw new Error(`workspace files snapshot is missing array field: ${key}`);
  }
  return value;
}

function normalizeStatus(value: string): WorkspaceFileStatus {
  if (value === 'active' || value === 'deleted' || value === 'conflict_copy') {
    return value;
  }
  throw new Error(`unsupported workspace file status: ${value}`);
}

function optionalString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === 'string' ? value : null;
}

function optionalNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function parseFileEntry(payload: unknown, context: string): WorkspaceFileEntry {
  if (!isObject(payload)) {
    throw new Error(`workspace files snapshot is missing file object: ${context}`);
  }
  const sizeBytes = payload.size_bytes;
  return {
    file_id: requireString(payload, 'file_id'),
    path: requireString(payload, 'path'),
    type: requireString(payload, 'type'),
    status: normalizeStatus(requireString(payload, 'status')),
    updated_at: requireNumber(payload, 'updated_at'),
    exists_on_disk: Boolean(payload.exists_on_disk),
    size_bytes: typeof sizeBytes === 'number' && Number.isFinite(sizeBytes) ? sizeBytes : null,
    content_hash: optionalString(payload, 'content_hash'),
    last_known_revision: optionalNumber(payload, 'last_known_revision'),
    conflict_source_file_id: optionalString(payload, 'conflict_source_file_id'),
  };
}

export function parseWorkspaceFilesSnapshot(payload: unknown): WorkspaceFilesSnapshot {
  if (!isObject(payload)) {
    throw new Error('workspace files snapshot must be an object');
  }
  return {
    schema_version: requireString(payload, 'schema_version'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    vault_root: requireString(payload, 'vault_root'),
    files: requireArray(payload, 'files').map((file, index) => parseFileEntry(file, `files[${index}]`)),
    total_count: requireNumber(payload, 'total_count'),
    active_count: requireNumber(payload, 'active_count'),
    missing_count: requireNumber(payload, 'missing_count'),
  };
}

export function summarizeWorkspaceFilesSnapshot(snapshot: WorkspaceFilesSnapshot): WorkspaceFilesSummary {
  return {
    schemaVersion: snapshot.schema_version,
    vaultId: snapshot.vault_id,
    deviceId: snapshot.device_id,
    vaultRoot: snapshot.vault_root,
    totalCount: snapshot.total_count,
    activeCount: snapshot.active_count,
    missingCount: snapshot.missing_count,
  };
}

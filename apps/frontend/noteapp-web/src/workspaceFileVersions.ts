export type WorkspaceFileVersionSource =
  | 'commit_success'
  | 'manual_checkpoint'
  | 'manual_meeting_checkpoint'
  | 'restore';

export interface WorkspaceFileVersionRecord {
  version_id: string;
  file_id: string;
  path_at_revision: string;
  revision: number;
  content_hash: string;
  blob_id: string;
  size: number;
  mtime: number;
  created_at: number;
  created_by_device: string;
  source: WorkspaceFileVersionSource;
  version_label: string | null;
  change_note: string | null;
  is_pinned: boolean;
}

export interface WorkspaceFileVersionList {
  file_id: string;
  versions: WorkspaceFileVersionRecord[];
  next_cursor: string | null;
}

export interface WorkspaceFileVersionContent {
  schema_version: string;
  vault_id: string;
  device_id: string;
  vault_root: string;
  version: WorkspaceFileVersionRecord;
  size_bytes: number;
  content_hash: string;
  content_base64: string;
  text: string | null;
  encoding: string | null;
}

export interface WorkspaceFileVersionDiff {
  schema_version: string;
  vault_id: string;
  device_id: string;
  vault_root: string;
  version: WorkspaceFileVersionRecord;
  current_file_id: string;
  current_path: string;
  current_content_hash: string;
  version_content_hash: string;
  is_binary: boolean;
  diff_text: string;
}

export interface WorkspaceFileVersionRestoreResult {
  schema_version: string;
  vault_id: string;
  device_id: string;
  vault_root: string;
  version: WorkspaceFileVersionRecord;
  file_id: string;
  path: string;
  restored_content_hash: string;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`workspace file version is missing string field: ${key}`);
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
    throw new Error(`workspace file version is missing numeric field: ${key}`);
  }
  return value;
}

function requireArray(payload: Record<string, unknown>, key: string): unknown[] {
  const value = payload[key];
  if (!Array.isArray(value)) {
    throw new Error(`workspace file version is missing array field: ${key}`);
  }
  return value;
}

function normalizeSource(value: string): WorkspaceFileVersionSource {
  if (
    value === 'commit_success'
    || value === 'manual_checkpoint'
    || value === 'manual_meeting_checkpoint'
    || value === 'restore'
  ) {
    return value;
  }
  throw new Error(`unsupported workspace file version source: ${value}`);
}

export function parseWorkspaceFileVersionRecord(payload: unknown): WorkspaceFileVersionRecord {
  if (!isObject(payload)) {
    throw new Error('workspace file version record must be an object');
  }
  return {
    version_id: requireString(payload, 'version_id'),
    file_id: requireString(payload, 'file_id'),
    path_at_revision: requireString(payload, 'path_at_revision'),
    revision: requireNumber(payload, 'revision'),
    content_hash: requireString(payload, 'content_hash'),
    blob_id: requireString(payload, 'blob_id'),
    size: requireNumber(payload, 'size'),
    mtime: requireNumber(payload, 'mtime'),
    created_at: requireNumber(payload, 'created_at'),
    created_by_device: requireString(payload, 'created_by_device'),
    source: normalizeSource(requireString(payload, 'source')),
    version_label: optionalString(payload, 'version_label'),
    change_note: optionalString(payload, 'change_note'),
    is_pinned: Boolean(payload.is_pinned),
  };
}

export function parseWorkspaceFileVersionList(payload: unknown): WorkspaceFileVersionList {
  if (!isObject(payload)) {
    throw new Error('workspace file version list must be an object');
  }
  const response = isObject(payload.response) ? payload.response : payload;
  return {
    file_id: requireString(response, 'file_id'),
    versions: requireArray(response, 'versions').map(parseWorkspaceFileVersionRecord),
    next_cursor: optionalString(response, 'next_cursor'),
  };
}

export function parseWorkspaceFileVersionContent(payload: unknown): WorkspaceFileVersionContent {
  if (!isObject(payload)) {
    throw new Error('workspace file version content must be an object');
  }
  return {
    schema_version: requireString(payload, 'schema_version'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    vault_root: requireString(payload, 'vault_root'),
    version: parseWorkspaceFileVersionRecord(payload.version),
    size_bytes: requireNumber(payload, 'size_bytes'),
    content_hash: requireString(payload, 'content_hash'),
    content_base64: requireString(payload, 'content_base64'),
    text: optionalString(payload, 'text'),
    encoding: optionalString(payload, 'encoding'),
  };
}

export function parseWorkspaceFileVersionDiff(payload: unknown): WorkspaceFileVersionDiff {
  if (!isObject(payload)) {
    throw new Error('workspace file version diff must be an object');
  }
  return {
    schema_version: requireString(payload, 'schema_version'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    vault_root: requireString(payload, 'vault_root'),
    version: parseWorkspaceFileVersionRecord(payload.version),
    current_file_id: requireString(payload, 'current_file_id'),
    current_path: requireString(payload, 'current_path'),
    current_content_hash: requireString(payload, 'current_content_hash'),
    version_content_hash: requireString(payload, 'version_content_hash'),
    is_binary: Boolean(payload.is_binary),
    diff_text: typeof payload.diff_text === 'string' ? payload.diff_text : '',
  };
}

export function parseWorkspaceFileVersionRestoreResult(payload: unknown): WorkspaceFileVersionRestoreResult {
  if (!isObject(payload)) {
    throw new Error('workspace file version restore result must be an object');
  }
  return {
    schema_version: requireString(payload, 'schema_version'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    vault_root: requireString(payload, 'vault_root'),
    version: parseWorkspaceFileVersionRecord(payload.version),
    file_id: requireString(payload, 'file_id'),
    path: requireString(payload, 'path'),
    restored_content_hash: requireString(payload, 'restored_content_hash'),
  };
}

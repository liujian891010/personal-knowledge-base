export interface WorkspaceFileContent {
  schema_version: string;
  vault_id: string;
  device_id: string;
  vault_root: string;
  file_id: string;
  path: string;
  type: string;
  status: string;
  updated_at: number;
  size_bytes: number;
  content_hash?: string | null;
  tracked_content_hash?: string | null;
  text: string;
  encoding: 'utf-8';
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`workspace file content is missing string field: ${key}`);
  }
  return value;
}

function requireNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`workspace file content is missing numeric field: ${key}`);
  }
  return value;
}

export function parseWorkspaceFileContent(payload: unknown): WorkspaceFileContent {
  if (!isObject(payload)) {
    throw new Error('workspace file content must be an object');
  }
  const encoding = requireString(payload, 'encoding');
  if (encoding !== 'utf-8') {
    throw new Error(`unsupported workspace file content encoding: ${encoding}`);
  }
  return {
    schema_version: requireString(payload, 'schema_version'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    vault_root: requireString(payload, 'vault_root'),
    file_id: requireString(payload, 'file_id'),
    path: requireString(payload, 'path'),
    type: requireString(payload, 'type'),
    status: requireString(payload, 'status'),
    updated_at: requireNumber(payload, 'updated_at'),
    size_bytes: requireNumber(payload, 'size_bytes'),
    content_hash: typeof payload.content_hash === 'string' ? payload.content_hash : null,
    tracked_content_hash:
      typeof payload.tracked_content_hash === 'string' ? payload.tracked_content_hash : null,
    text: requireString(payload, 'text'),
    encoding,
  };
}

export interface WorkspaceNoteLink {
  source_file_id: string;
  source_path: string;
  link_text: string;
  target_file_id: string | null;
  target_path: string | null;
  ordinal: number;
}

export interface WorkspaceNoteLinksSnapshot {
  schema_version: string;
  vault_id: string;
  device_id: string;
  vault_root: string;
  file_id: string;
  path: string;
  outgoing: WorkspaceNoteLink[];
  backlinks: WorkspaceNoteLink[];
  outgoing_count: number;
  backlink_count: number;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string') {
    throw new Error(`workspace links is missing string field: ${key}`);
  }
  return value;
}

function requireNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`workspace links is missing numeric field: ${key}`);
  }
  return value;
}

function optionalString(payload: Record<string, unknown>, key: string): string | null {
  return typeof payload[key] === 'string' ? payload[key] : null;
}

function parseNoteLink(payload: unknown, context: string): WorkspaceNoteLink {
  if (!isObject(payload)) {
    throw new Error(`workspace links is missing link object: ${context}`);
  }
  return {
    source_file_id: requireString(payload, 'source_file_id'),
    source_path: requireString(payload, 'source_path'),
    link_text: requireString(payload, 'link_text'),
    target_file_id: optionalString(payload, 'target_file_id'),
    target_path: optionalString(payload, 'target_path'),
    ordinal: requireNumber(payload, 'ordinal'),
  };
}

export function parseWorkspaceNoteLinksSnapshot(payload: unknown): WorkspaceNoteLinksSnapshot {
  if (!isObject(payload)) {
    throw new Error('workspace links snapshot must be an object');
  }
  if (!Array.isArray(payload.outgoing) || !Array.isArray(payload.backlinks)) {
    throw new Error('workspace links outgoing/backlinks must be arrays');
  }
  return {
    schema_version: requireString(payload, 'schema_version'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    vault_root: requireString(payload, 'vault_root'),
    file_id: requireString(payload, 'file_id'),
    path: requireString(payload, 'path'),
    outgoing: payload.outgoing.map((item, index) => parseNoteLink(item, `outgoing[${index}]`)),
    backlinks: payload.backlinks.map((item, index) => parseNoteLink(item, `backlinks[${index}]`)),
    outgoing_count: requireNumber(payload, 'outgoing_count'),
    backlink_count: requireNumber(payload, 'backlink_count'),
  };
}

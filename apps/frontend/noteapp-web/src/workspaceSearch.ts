export interface WorkspaceSearchResult {
  file_id: string;
  path: string;
  title: string;
  snippet: string;
}

export interface WorkspaceSearchSnapshot {
  schema_version: string;
  vault_id: string;
  device_id: string;
  vault_root: string;
  query: string;
  total_count: number;
  results: WorkspaceSearchResult[];
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string') {
    throw new Error(`workspace search is missing string field: ${key}`);
  }
  return value;
}

function requireNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`workspace search is missing numeric field: ${key}`);
  }
  return value;
}

function parseSearchResult(payload: unknown, context: string): WorkspaceSearchResult {
  if (!isObject(payload)) {
    throw new Error(`workspace search is missing result object: ${context}`);
  }
  return {
    file_id: requireString(payload, 'file_id'),
    path: requireString(payload, 'path'),
    title: requireString(payload, 'title'),
    snippet: requireString(payload, 'snippet'),
  };
}

export function parseWorkspaceSearchSnapshot(payload: unknown): WorkspaceSearchSnapshot {
  if (!isObject(payload)) {
    throw new Error('workspace search snapshot must be an object');
  }
  const results = payload.results;
  if (!Array.isArray(results)) {
    throw new Error('workspace search results must be an array');
  }
  return {
    schema_version: requireString(payload, 'schema_version'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    vault_root: requireString(payload, 'vault_root'),
    query: requireString(payload, 'query'),
    total_count: requireNumber(payload, 'total_count'),
    results: results.map((item, index) => parseSearchResult(item, `results[${index}]`)),
  };
}

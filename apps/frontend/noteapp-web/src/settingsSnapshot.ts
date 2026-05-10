export type LocalSettingsSource = 'default' | 'file';

export interface LocalSyncSettings {
  base_url: string;
  bearer_token_configured: boolean;
  request_timeout_seconds: number;
  blob_timeout_seconds: number;
  user_agent: string;
}

export interface LocalAppearanceSettings {
  theme: string;
}

export interface LocalAiSettings {
  local_model_status: string;
  embedding_status: string;
}

export interface LocalSettingsSnapshot {
  schema_version: string;
  source: LocalSettingsSource;
  settings_path: string;
  vault_root: string;
  vault_id: string;
  device_id: string;
  sync: LocalSyncSettings;
  appearance: LocalAppearanceSettings;
  ai: LocalAiSettings;
}

export interface LocalSettingsWritePayload {
  appearance: LocalAppearanceSettings;
  ai: LocalAiSettings;
}

export interface LocalSettingsSummary {
  schemaVersion: string;
  source: LocalSettingsSource;
  settingsPath: string;
  vaultRoot: string;
  vaultId: string;
  deviceId: string;
  syncBaseUrl: string;
  bearerTokenConfigured: boolean;
  requestTimeoutSeconds: number;
  blobTimeoutSeconds: number;
  userAgent: string;
  theme: string;
  localModelStatus: string;
  embeddingStatus: string;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`local settings snapshot is missing string field: ${key}`);
  }
  return value;
}

function requireNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`local settings snapshot is missing numeric field: ${key}`);
  }
  return value;
}

function requireObject(payload: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = payload[key];
  if (!isObject(value)) {
    throw new Error(`local settings snapshot is missing object field: ${key}`);
  }
  return value;
}

function normalizeSource(value: string): LocalSettingsSource {
  if (value === 'default' || value === 'file') {
    return value;
  }
  throw new Error(`unsupported local settings source: ${value}`);
}

export function parseLocalSettingsSnapshot(payload: unknown): LocalSettingsSnapshot {
  if (!isObject(payload)) {
    throw new Error('local settings snapshot must be an object');
  }

  const sync = requireObject(payload, 'sync');
  const appearance = requireObject(payload, 'appearance');
  const ai = requireObject(payload, 'ai');

  return {
    schema_version: requireString(payload, 'schema_version'),
    source: normalizeSource(requireString(payload, 'source')),
    settings_path: requireString(payload, 'settings_path'),
    vault_root: requireString(payload, 'vault_root'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    sync: {
      base_url: requireString(sync, 'base_url'),
      bearer_token_configured: Boolean(sync.bearer_token_configured),
      request_timeout_seconds: requireNumber(sync, 'request_timeout_seconds'),
      blob_timeout_seconds: requireNumber(sync, 'blob_timeout_seconds'),
      user_agent: requireString(sync, 'user_agent'),
    },
    appearance: {
      theme: requireString(appearance, 'theme'),
    },
    ai: {
      local_model_status: requireString(ai, 'local_model_status'),
      embedding_status: requireString(ai, 'embedding_status'),
    },
  };
}

export function summarizeLocalSettingsSnapshot(snapshot: LocalSettingsSnapshot): LocalSettingsSummary {
  return {
    schemaVersion: snapshot.schema_version,
    source: snapshot.source,
    settingsPath: snapshot.settings_path,
    vaultRoot: snapshot.vault_root,
    vaultId: snapshot.vault_id,
    deviceId: snapshot.device_id,
    syncBaseUrl: snapshot.sync.base_url,
    bearerTokenConfigured: snapshot.sync.bearer_token_configured,
    requestTimeoutSeconds: snapshot.sync.request_timeout_seconds,
    blobTimeoutSeconds: snapshot.sync.blob_timeout_seconds,
    userAgent: snapshot.sync.user_agent,
    theme: snapshot.appearance.theme,
    localModelStatus: snapshot.ai.local_model_status,
    embeddingStatus: snapshot.ai.embedding_status,
  };
}

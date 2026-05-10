export type SyncShellLevel = 'success' | 'info' | 'warning' | 'danger';
export type SyncShellActionEmphasis = 'normal' | 'primary' | 'warning';
export type SyncShellActivityStatus = 'executed' | 'blocked' | 'disabled' | 'unsupported' | 'failed';

export interface SyncShellAction {
  action_id: string;
  label: string;
  enabled: boolean;
  emphasis: SyncShellActionEmphasis;
  command: string;
  argv: string[];
  reason?: string | null;
  requires_confirmation?: boolean;
}

export interface SyncShellPanel {
  level: SyncShellLevel;
  headline: string;
  detail: string;
  conflict_badge_count: number;
  change_badge_count: number;
  primary_action: SyncShellAction;
  secondary_actions: SyncShellAction[];
}

export interface SyncShellCard {
  card_id: string;
  kind: string;
  level: SyncShellLevel;
  title: string;
  body: string;
  badge_count: number;
  actions: SyncShellAction[];
}

export interface SyncShellActivityRecord {
  activity_id: string;
  occurred_at_ms: number;
  level: SyncShellLevel;
  action_id: string;
  command: string;
  status: SyncShellActivityStatus;
  source: string;
  message?: string | null;
}

export interface SyncShellActivityFeed {
  records: SyncShellActivityRecord[];
  total_count: number;
}

export interface SyncShellSnapshot {
  generated_at_ms: number;
  vault_id: string;
  device_id: string;
  vault_root: string;
  sync_center: {
    cards: SyncShellCard[];
    panel: SyncShellPanel;
    recent_activity: SyncShellActivityFeed;
  };
  activity_feed: SyncShellActivityFeed;
}

export interface SyncShellSummary {
  generatedAtMs: number;
  vaultId: string;
  deviceId: string;
  vaultRoot: string;
  level: SyncShellLevel;
  headline: string;
  detail: string;
  conflictBadgeCount: number;
  changeBadgeCount: number;
  cardCount: number;
  recentActivityCount: number;
  primaryActionId: string;
  primaryActionLabel: string;
  primaryActionEnabled: boolean;
  primaryActionRequiresConfirmation: boolean;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`sync shell snapshot is missing string field: ${key}`);
  }
  return value;
}

function requireNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`sync shell snapshot is missing numeric field: ${key}`);
  }
  return value;
}

function requireObject(payload: Record<string, unknown>, key: string): Record<string, unknown> {
  const value = payload[key];
  if (!isObject(value)) {
    throw new Error(`sync shell snapshot is missing object field: ${key}`);
  }
  return value;
}

function requireArray(payload: Record<string, unknown>, key: string): unknown[] {
  const value = payload[key];
  if (!Array.isArray(value)) {
    throw new Error(`sync shell snapshot is missing array field: ${key}`);
  }
  return value;
}

function requireStringArray(payload: Record<string, unknown>, key: string): string[] {
  return requireArray(payload, key).map((value, index) => {
    if (typeof value !== 'string') {
      throw new Error(`sync shell snapshot is missing string field: ${key}[${index}]`);
    }
    return value;
  });
}

function normalizeLevel(value: string): SyncShellLevel {
  if (value === 'success' || value === 'info' || value === 'warning' || value === 'danger') {
    return value;
  }
  throw new Error(`unsupported sync shell level: ${value}`);
}

function normalizeActionEmphasis(value: string): SyncShellActionEmphasis {
  if (value === 'normal' || value === 'primary' || value === 'warning') {
    return value;
  }
  throw new Error(`unsupported sync action emphasis: ${value}`);
}

function normalizeActivityStatus(value: string): SyncShellActivityStatus {
  if (
    value === 'executed' ||
    value === 'blocked' ||
    value === 'disabled' ||
    value === 'unsupported' ||
    value === 'failed'
  ) {
    return value;
  }
  throw new Error(`unsupported sync activity status: ${value}`);
}

function parseAction(payload: unknown, context: string): SyncShellAction {
  if (!isObject(payload)) {
    throw new Error(`sync shell snapshot is missing action object: ${context}`);
  }
  return {
    action_id: requireString(payload, 'action_id'),
    label: requireString(payload, 'label'),
    enabled: Boolean(payload.enabled),
    emphasis: normalizeActionEmphasis(requireString(payload, 'emphasis')),
    command: requireString(payload, 'command'),
    argv: requireStringArray(payload, 'argv'),
    reason: typeof payload.reason === 'string' ? payload.reason : null,
    requires_confirmation: Boolean(payload.requires_confirmation),
  };
}

function parseCard(payload: unknown, context: string): SyncShellCard {
  if (!isObject(payload)) {
    throw new Error(`sync shell snapshot is missing card object: ${context}`);
  }
  return {
    card_id: requireString(payload, 'card_id'),
    kind: requireString(payload, 'kind'),
    level: normalizeLevel(requireString(payload, 'level')),
    title: requireString(payload, 'title'),
    body: requireString(payload, 'body'),
    badge_count: requireNumber(payload, 'badge_count'),
    actions: requireArray(payload, 'actions').map((action, index) =>
      parseAction(action, `${context}.actions[${index}]`),
    ),
  };
}

function parseActivityRecord(payload: unknown, context: string): SyncShellActivityRecord {
  if (!isObject(payload)) {
    throw new Error(`sync shell snapshot is missing activity record object: ${context}`);
  }
  return {
    activity_id: requireString(payload, 'activity_id'),
    occurred_at_ms: requireNumber(payload, 'occurred_at_ms'),
    level: normalizeLevel(requireString(payload, 'level')),
    action_id: requireString(payload, 'action_id'),
    command: requireString(payload, 'command'),
    status: normalizeActivityStatus(requireString(payload, 'status')),
    source: requireString(payload, 'source'),
    message: typeof payload.message === 'string' ? payload.message : null,
  };
}

function parseActivityFeed(payload: Record<string, unknown>, context: string): SyncShellActivityFeed {
  return {
    records: requireArray(payload, 'records').map((record, index) =>
      parseActivityRecord(record, `${context}.records[${index}]`),
    ),
    total_count: requireNumber(payload, 'total_count'),
  };
}

export function parseSyncShellSnapshot(payload: unknown): SyncShellSnapshot {
  if (!isObject(payload)) {
    throw new Error('sync shell snapshot must be an object');
  }

  const syncCenter = requireObject(payload, 'sync_center');
  const panel = requireObject(syncCenter, 'panel');
  const primaryAction = requireObject(panel, 'primary_action');
  const recentActivity = requireObject(syncCenter, 'recent_activity');
  const activityFeed = requireObject(payload, 'activity_feed');
  const cards = requireArray(syncCenter, 'cards').map((card, index) =>
    parseCard(card, `cards[${index}]`),
  );
  const secondaryActions = requireArray(panel, 'secondary_actions').map((action, index) =>
    parseAction(action, `secondary_actions[${index}]`),
  );

  return {
    generated_at_ms: requireNumber(payload, 'generated_at_ms'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    vault_root: requireString(payload, 'vault_root'),
    sync_center: {
      cards,
      panel: {
        level: normalizeLevel(requireString(panel, 'level')),
        headline: requireString(panel, 'headline'),
        detail: requireString(panel, 'detail'),
        conflict_badge_count: requireNumber(panel, 'conflict_badge_count'),
        change_badge_count: requireNumber(panel, 'change_badge_count'),
        primary_action: parseAction(primaryAction, 'primary_action'),
        secondary_actions: secondaryActions,
      },
      recent_activity: parseActivityFeed(recentActivity, 'recent_activity'),
    },
    activity_feed: parseActivityFeed(activityFeed, 'activity_feed'),
  };
}

export function summarizeSyncShellSnapshot(snapshot: SyncShellSnapshot): SyncShellSummary {
  return {
    generatedAtMs: snapshot.generated_at_ms,
    vaultId: snapshot.vault_id,
    deviceId: snapshot.device_id,
    vaultRoot: snapshot.vault_root,
    level: snapshot.sync_center.panel.level,
    headline: snapshot.sync_center.panel.headline,
    detail: snapshot.sync_center.panel.detail,
    conflictBadgeCount: snapshot.sync_center.panel.conflict_badge_count,
    changeBadgeCount: snapshot.sync_center.panel.change_badge_count,
    cardCount: snapshot.sync_center.cards.length,
    recentActivityCount: snapshot.activity_feed.total_count,
    primaryActionId: snapshot.sync_center.panel.primary_action.action_id,
    primaryActionLabel: snapshot.sync_center.panel.primary_action.label,
    primaryActionEnabled: snapshot.sync_center.panel.primary_action.enabled,
    primaryActionRequiresConfirmation: snapshot.sync_center.panel.primary_action.requires_confirmation ?? false,
  };
}

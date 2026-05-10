export type SyncShellLevel = 'success' | 'info' | 'warning' | 'danger';

export interface SyncShellAction {
  action_id: string;
  label: string;
  enabled: boolean;
  emphasis: string;
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
  status: string;
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

function normalizeLevel(value: string): SyncShellLevel {
  if (value === 'success' || value === 'info' || value === 'warning' || value === 'danger') {
    return value;
  }
  throw new Error(`unsupported sync shell level: ${value}`);
}

function parseAction(payload: unknown, context: string): SyncShellAction {
  if (!isObject(payload)) {
    throw new Error(`sync shell snapshot is missing action object: ${context}`);
  }
  return {
    action_id: requireString(payload, 'action_id'),
    label: requireString(payload, 'label'),
    enabled: Boolean(payload.enabled),
    emphasis: requireString(payload, 'emphasis'),
    command: requireString(payload, 'command'),
    argv: requireArray(payload, 'argv') as string[],
    reason: typeof payload.reason === 'string' ? payload.reason : null,
    requires_confirmation: Boolean(payload.requires_confirmation),
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
  const secondaryActions = requireArray(panel, 'secondary_actions').map((action, index) =>
    parseAction(action, `secondary_actions[${index}]`),
  );

  return {
    generated_at_ms: requireNumber(payload, 'generated_at_ms'),
    vault_id: requireString(payload, 'vault_id'),
    device_id: requireString(payload, 'device_id'),
    vault_root: requireString(payload, 'vault_root'),
    sync_center: {
      cards: requireArray(syncCenter, 'cards') as SyncShellCard[],
      panel: {
        level: normalizeLevel(requireString(panel, 'level')),
        headline: requireString(panel, 'headline'),
        detail: requireString(panel, 'detail'),
        conflict_badge_count: requireNumber(panel, 'conflict_badge_count'),
        change_badge_count: requireNumber(panel, 'change_badge_count'),
        primary_action: parseAction(primaryAction, 'primary_action'),
        secondary_actions: secondaryActions,
      },
      recent_activity: {
        records: requireArray(recentActivity, 'records') as SyncShellActivityRecord[],
        total_count: requireNumber(recentActivity, 'total_count'),
      },
    },
    activity_feed: {
      records: requireArray(activityFeed, 'records') as SyncShellActivityRecord[],
      total_count: requireNumber(activityFeed, 'total_count'),
    },
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
    primaryActionLabel: snapshot.sync_center.panel.primary_action.label,
    primaryActionEnabled: snapshot.sync_center.panel.primary_action.enabled,
    primaryActionRequiresConfirmation: snapshot.sync_center.panel.primary_action.requires_confirmation ?? false,
  };
}

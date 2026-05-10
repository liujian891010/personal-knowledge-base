import { useEffect, useMemo, useState } from 'react';

import bundledExampleSnapshot from '../fixtures/live-sync-shell.example.json';
import {
  parseSyncShellSnapshot,
  summarizeSyncShellSnapshot,
  type SyncShellAction,
  type SyncShellActivityFeed,
  type SyncShellCard,
  type SyncShellSnapshot,
  type SyncShellSummary,
} from './syncShell';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');
const fallbackSnapshot = parseSyncShellSnapshot(bundledExampleSnapshot);

type SyncShellSource = 'bridge' | 'live-fixture' | 'example';
type SyncActionNoticeLevel = 'success' | 'info' | 'warning' | 'danger';

export interface SyncActionNotice {
  level: SyncActionNoticeLevel;
  title: string;
  detail: string;
  actionId: string;
  occurredAtMs: number;
}

interface SnapshotLoadResult {
  snapshot: SyncShellSnapshot;
  source: SyncShellSource;
  error: string | null;
}

export interface SyncShellController {
  summary: SyncShellSummary;
  cards: SyncShellCard[];
  activityFeed: SyncShellActivityFeed;
  secondaryActions: SyncShellAction[];
  source: SyncShellSource;
  lastError: string | null;
  lastActionNotice: SyncActionNotice | null;
  isRefreshing: boolean;
  isExecuting: boolean;
  executingActionId: string | null;
  refresh: () => Promise<void>;
  executePrimaryAction: () => Promise<void>;
  executeSyncAction: (action: SyncShellAction) => Promise<void>;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

function actionResultNotice(action: SyncShellAction, snapshot: SyncShellSnapshot): SyncActionNotice {
  const summary = summarizeSyncShellSnapshot(snapshot);
  const changeCount = summary.changeBadgeCount;
  if (action.action_id === 'detect-local-changes') {
    return changeCount > 0
      ? {
          level: 'info',
          title: '已检查本地变更',
          detail: `发现 ${changeCount} 个待同步文件。`,
          actionId: action.action_id,
          occurredAtMs: snapshot.generated_at_ms,
        }
      : {
          level: 'success',
          title: '没有需要同步的本地变更',
          detail: '本地工作区当前没有检测到待提交内容。',
          actionId: action.action_id,
          occurredAtMs: snapshot.generated_at_ms,
        };
  }
  if (action.action_id === 'submit-detected-commit') {
    return changeCount > 0
      ? {
          level: 'warning',
          title: '本地变更提交后仍有待同步内容',
          detail: `仍检测到 ${changeCount} 个本地变更，请再次检查后继续提交。`,
          actionId: action.action_id,
          occurredAtMs: snapshot.generated_at_ms,
        }
      : {
          level: 'success',
          title: '本地变更已提交',
          detail: '本地工作区当前没有待同步文件。',
          actionId: action.action_id,
          occurredAtMs: snapshot.generated_at_ms,
        };
  }
  if (action.action_id === 'pull') {
    return {
      level: summary.level === 'success' ? 'success' : 'info',
      title: '已检查远端变更',
      detail: summary.detail,
      actionId: action.action_id,
      occurredAtMs: snapshot.generated_at_ms,
    };
  }
  return {
    level: summary.level === 'danger' ? 'danger' : summary.level,
    title: '同步操作已完成',
    detail: summary.detail,
    actionId: action.action_id,
    occurredAtMs: snapshot.generated_at_ms,
  };
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
    // Ignore non-JSON responses and fall back to the HTTP status.
  }
  return `${source} returned ${response.status}`;
}

async function loadSnapshot(): Promise<SnapshotLoadResult> {
  let bridgeError: string | null = null;
  let fixtureError: string | null = null;

  try {
    const bridgeResponse = await fetch(`${syncBridgeUrl}/api/sync/snapshot`, { cache: 'no-store' });
    if (bridgeResponse.ok) {
      return {
        snapshot: parseSyncShellSnapshot(await bridgeResponse.json()),
        source: 'bridge',
        error: null,
      };
    }
    bridgeError = await responseErrorMessage(bridgeResponse, 'bridge');
  } catch (error) {
    bridgeError = `bridge unavailable: ${errorMessage(error)}`;
  }

  try {
    const fixtureResponse = await fetch('/fixtures/live-sync-shell.json', { cache: 'no-store' });
    if (fixtureResponse.ok) {
      return {
        snapshot: parseSyncShellSnapshot(await fixtureResponse.json()),
        source: 'live-fixture',
        error: bridgeError,
      };
    }
    fixtureError = await responseErrorMessage(fixtureResponse, 'live fixture');
  } catch (error) {
    fixtureError = `live fixture unavailable: ${errorMessage(error)}`;
  }

  return {
    snapshot: fallbackSnapshot,
    source: 'example',
    error: [bridgeError, fixtureError].filter(Boolean).join('; '),
  };
}

async function executeBridgeAction(actionId: string): Promise<SyncShellSnapshot> {
  const response = await fetch(`${syncBridgeUrl}/api/sync/actions/${encodeURIComponent(actionId)}`, {
    method: 'POST',
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'sync action'));
  }
  return parseSyncShellSnapshot(await response.json());
}

function confirmAction(action: SyncShellAction): boolean {
  if (!action.requires_confirmation) {
    return true;
  }
  return window.confirm('确定执行此同步操作吗？');
}

export function useSyncShellController(): SyncShellController {
  const [snapshot, setSnapshot] = useState<SyncShellSnapshot>(fallbackSnapshot);
  const [source, setSource] = useState<SyncShellSource>('example');
  const [lastError, setLastError] = useState<string | null>(null);
  const [lastActionNotice, setLastActionNotice] = useState<SyncActionNotice | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const [executingActionId, setExecutingActionId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    setIsRefreshing(true);
    loadSnapshot()
      .then((result) => {
        if (cancelled) {
          return;
        }
        setSnapshot(result.snapshot);
        setSource(result.source);
        setLastError(result.error);
      })
      .finally(() => {
        if (!cancelled) {
          setIsRefreshing(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = async () => {
    setIsRefreshing(true);
    try {
      const result = await loadSnapshot();
      setSnapshot(result.snapshot);
      setSource(result.source);
      setLastError(result.error);
      setLastActionNotice(null);
    } finally {
      setIsRefreshing(false);
    }
  };

  const executeSyncAction = async (action: SyncShellAction) => {
    if (!action.enabled || isExecuting) {
      return;
    }
    if (!confirmAction(action)) {
      return;
    }
    setIsExecuting(true);
    setExecutingActionId(action.action_id);
    try {
      const nextSnapshot = await executeBridgeAction(action.action_id);
      setSnapshot(nextSnapshot);
      setSource('bridge');
      setLastError(null);
      setLastActionNotice(actionResultNotice(action, nextSnapshot));
    } catch (error) {
      const message = errorMessage(error);
      setLastError(message);
      setLastActionNotice({
        level: 'danger',
        title: '同步操作失败',
        detail: message,
        actionId: action.action_id,
        occurredAtMs: Date.now(),
      });
    } finally {
      setIsExecuting(false);
      setExecutingActionId(null);
    }
  };

  const executePrimaryAction = async () => {
    await executeSyncAction(snapshot.sync_center.panel.primary_action);
  };

  const summary = useMemo(() => summarizeSyncShellSnapshot(snapshot), [snapshot]);
  return {
    summary,
    cards: snapshot.sync_center.cards,
    activityFeed: snapshot.activity_feed,
    secondaryActions: snapshot.sync_center.panel.secondary_actions,
    source,
    lastError,
    lastActionNotice,
    isRefreshing,
    isExecuting,
    executingActionId,
    refresh,
    executePrimaryAction,
    executeSyncAction,
  };
}

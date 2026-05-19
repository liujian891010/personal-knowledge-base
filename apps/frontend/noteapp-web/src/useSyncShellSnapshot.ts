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
import { syncBridgeUrl } from './syncBridgeConfig';

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

function canUseLiveFixtureFallback(): boolean {
  return typeof window !== 'undefined' && window.location.protocol !== 'file:';
}

function latestActionActivity(action: SyncShellAction, snapshot: SyncShellSnapshot) {
  return [...snapshot.activity_feed.records]
    .reverse()
    .find((record) => record.action_id === action.action_id);
}

function actionResultNotice(action: SyncShellAction, snapshot: SyncShellSnapshot): SyncActionNotice {
  const summary = summarizeSyncShellSnapshot(snapshot);
  const changeCount = summary.changeBadgeCount;
  const activity = latestActionActivity(action, snapshot);
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
    if (activity?.status === 'blocked') {
      return {
        level: 'warning',
        title: '远端版本已更新，需要先拉取',
        detail: activity.message
          ? `提交被远端拒绝：${activity.message}`
          : '提交被远端拒绝。请先执行拉取，刷新本地同步基线后再提交。',
        actionId: action.action_id,
        occurredAtMs: snapshot.generated_at_ms,
      };
    }
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
  if (action.command === 'resolve-conflicts') {
    return summary.conflictBadgeCount > 0
      ? {
          level: 'warning',
          title: '仍有本地冲突需要处理',
          detail: `还剩 ${summary.conflictBadgeCount} 个冲突副本，请继续检查或清理。`,
          actionId: action.action_id,
          occurredAtMs: snapshot.generated_at_ms,
        }
      : {
          level: 'success',
          title: '冲突副本已清理',
          detail: '本地冲突副本已移除，同步状态已重新开放。',
          actionId: action.action_id,
          occurredAtMs: snapshot.generated_at_ms,
        };
  }
  if (action.action_id === 'pull') {
    if (summary.conflictBadgeCount > 0) {
      return {
        level: 'warning',
        title: '已应用远端内容，请处理本地冲突副本',
        detail: `检测到 ${summary.conflictBadgeCount} 个本地冲突副本，请检查后保留或清理。`,
        actionId: action.action_id,
        occurredAtMs: snapshot.generated_at_ms,
      };
    }
    if (changeCount > 0) {
      return {
        level: 'info',
        title: '已拉取远端基线，可继续提交',
        detail: `仍有 ${changeCount} 个本地变更，提交路径已重新打开。`,
        actionId: action.action_id,
        occurredAtMs: snapshot.generated_at_ms,
      };
    }
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
      return `${source}返回 ${response.status}${code}：${payload.message}`;
    }
  } catch {
    // Ignore non-JSON responses and fall back to the HTTP status.
  }
  return `${source}返回 ${response.status}`;
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
    bridgeError = await responseErrorMessage(bridgeResponse, '同步桥接');
  } catch (error) {
    bridgeError = `同步桥接不可用：${errorMessage(error)}`;
  }

  if (!canUseLiveFixtureFallback()) {
    return {
      snapshot: fallbackSnapshot,
      source: 'example',
      error: bridgeError,
    };
  }

  try {
    const fixtureResponse = await fetch('./fixtures/live-sync-shell.json', { cache: 'no-store' });
    if (fixtureResponse.ok) {
      return {
        snapshot: parseSyncShellSnapshot(await fixtureResponse.json()),
        source: 'live-fixture',
        error: bridgeError,
      };
    }
    fixtureError = await responseErrorMessage(fixtureResponse, '本地快照');
  } catch (error) {
    fixtureError = `本地快照不可用：${errorMessage(error)}`;
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
    throw new Error(await responseErrorMessage(response, '同步操作'));
  }
  return parseSyncShellSnapshot(await response.json());
}

function confirmAction(action: SyncShellAction): boolean {
  if (!action.requires_confirmation) {
    return true;
  }
  return window.confirm('确定执行此同步操作吗？');
}

export function useSyncShellController(enabled = true): SyncShellController {
  const [snapshot, setSnapshot] = useState<SyncShellSnapshot>(fallbackSnapshot);
  const [source, setSource] = useState<SyncShellSource>('example');
  const [lastError, setLastError] = useState<string | null>(null);
  const [lastActionNotice, setLastActionNotice] = useState<SyncActionNotice | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [isExecuting, setIsExecuting] = useState(false);
  const [executingActionId, setExecutingActionId] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled) {
      setIsRefreshing(false);
      setLastError(null);
      setLastActionNotice(null);
      return;
    }

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
  }, [enabled]);

  const refresh = async () => {
    if (!enabled) {
      setIsRefreshing(false);
      return;
    }
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
    if (!enabled) {
      return;
    }
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
        detail: `操作未完成：${message}`,
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

import React, { useEffect, useState } from 'react';
import {
  Activity,
  Bot,
  ChevronRight,
  CheckCircle2,
  Cloud,
  GitCommitHorizontal,
  ListChecks,
  Palette,
  RefreshCw,
  Save,
  Settings,
} from 'lucide-react';

import type { SyncShellAction, SyncShellActionEmphasis, SyncShellLevel } from '../syncShell';
import { useLocalSettingsController } from '../useLocalSettingsSnapshot';
import { useSyncShellController } from '../useSyncShellSnapshot';

type SettingsTab = 'general' | 'sync' | 'appearance' | 'ai';

type SettingsViewProps = {
  initialTab?: SettingsTab;
};

const syncLevelClasses: Record<SyncShellLevel, string> = {
  success: 'text-emerald-300 bg-emerald-400/10 border-emerald-400/30',
  info: 'text-[#a9c8fc] bg-[#0f3460]/30 border-[#0f3460]',
  warning: 'text-[#ffb782] bg-[#ffb782]/10 border-[#ffb782]/30',
  danger: 'text-[#e94560] bg-[#e94560]/10 border-[#e94560]/30',
};

const tabs: Array<{ id: SettingsTab; label: string; icon: React.ComponentType<{ size?: number }> }> = [
  { id: 'general', label: '通用', icon: Settings },
  { id: 'sync', label: '同步', icon: Cloud },
  { id: 'appearance', label: '外观', icon: Palette },
  { id: 'ai', label: 'AI 模型', icon: Bot },
];

const actionButtonClasses: Record<SyncShellActionEmphasis, string> = {
  normal: 'bg-[#121316] border-[#0f3460] text-slate-300 hover:text-white',
  primary: 'bg-[#0f3460]/30 border-[#0f3460] text-[#a9c8fc] hover:text-white',
  warning: 'bg-[#ffb782]/10 border-[#ffb782]/30 text-[#ffb782] hover:text-white',
};

const themeOptions = ['dark', 'light', 'system'];
const localModelStatusOptions = ['not_configured', 'available', 'unavailable', 'disabled', 'error'];
const embeddingStatusOptions = ['not_configured', 'ready', 'indexing', 'disabled', 'error'];

const valueLabels: Record<string, string> = {
  bridge: '本机桥接',
  'live-fixture': '实时快照',
  example: '示例数据',
  file: '配置文件',
  default: '默认配置',
  panel: '状态面板',
  success: '正常',
  info: '提示',
  warning: '警告',
  danger: '危险',
  changes: '变更',
  conflicts: '冲突',
  recovery: '恢复',
  baseline: '基线',
  overview: '概览',
  activity: '活动',
  'background-sync': '后台同步',
  executed: '已执行',
  disabled: '已禁用',
  unsupported: '不支持',
  failed: '失败',
  dark: '深色',
  light: '浅色',
  system: '跟随系统',
  not_configured: '未配置',
  available: '可用',
  unavailable: '不可用',
  ready: '就绪',
  indexing: '索引中',
  error: '错误',
};

const actionLabels: Record<string, string> = {
  'show-vault-summary': '打开摘要',
  'list-conflicts': '查看冲突',
  'worker-health': '后台状态',
  'resolve-conflicts-all': '解决全部本地冲突',
  'recover-pull-apply': '恢复拉取',
  recover: '恢复提交',
  pull: '检查远端变更',
  'detect-local-changes': '检查本地变更',
  'submit-detected-commit': '提交本地变更',
  'sync-activity': '查看活动记录',
};

const textLabels: Record<string, string> = {
  'Vault is in sync': '知识库已同步',
  'No unresolved conflicts, no pending local changes, and no blocked sync state detected.':
    '未检测到未解决冲突、本地待同步变更或阻塞状态。',
  'Local changes pending': '本地变更待同步',
  'Local edits are ready for the next submit or sync cycle.':
    '本地编辑已准备好提交或进入下一次同步周期。',
  'Full pull required': '需要完整拉取',
  'The local manifest baseline is stale. Run pull/reconcile before creating a new commit.':
    '本地清单基线已过期，请先拉取并协调后再创建新提交。',
  'Background sync needs attention': '后台同步需要处理',
  'The latest worker run did not finish cleanly. Review worker health before relying on background sync.':
    '最近一次后台任务未正常完成，请先查看后台状态。',
  'Local changes are waiting': '本地变更等待同步',
  'Tracked modifications, missing files, or untracked files are ready for the next submit or sync cycle.':
    '已跟踪修改、缺失文件或未跟踪文件已准备好进入下一次提交或同步周期。',
  'Vault sync center is clear': '同步中心状态正常',
  'No unresolved conflicts, no blocked recovery state, and no pending local changes were detected.':
    '未检测到未解决冲突、阻塞恢复状态或本地待同步变更。',
};

function formatActivityTime(ms: number): string {
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ms));
}

function localizeValue(value: string): string {
  if (value.startsWith('card:')) {
    return `卡片：${localizeValue(value.slice('card:'.length))}`;
  }
  return valueLabels[value] ?? value;
}

function localizeActionLabel(action: SyncShellAction): string {
  return actionLabels[action.action_id] ?? actionLabels[action.command] ?? action.label;
}

function localizeText(value: string): string {
  const exact = textLabels[value];
  if (exact) {
    return exact;
  }
  return value
    .replace(/(\d+) local changes pending/g, '$1 个本地变更待同步')
    .replace(/(\d+) local changes are waiting/g, '$1 个本地变更等待同步')
    .replace(/(\d+) unresolved conflict artifacts/g, '$1 个未解决冲突')
    .replace(/(\d+) recent sync actions recorded/g, '$1 条最近同步活动');
}

function SettingsSelect({
  label,
  value,
  options,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label className="flex flex-col gap-2 min-w-0">
      <span className="text-[11px] uppercase tracking-wider text-slate-500">{label}</span>
      <select
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded border border-[#0f3460] bg-[#121316] px-3 py-2 text-[13px] text-[#e3e2e6] disabled:opacity-50 focus:outline-none focus:border-[#e94560]"
      >
        {options.map((option) => (
          <option key={option} value={option}>
            {localizeValue(option)}
          </option>
        ))}
      </select>
    </label>
  );
}

function SettingsDetailRow({
  label,
  value,
  mono = false,
}: {
  label: string;
  value: string | number | boolean;
  mono?: boolean;
}) {
  const renderedValue = typeof value === 'boolean'
    ? (value ? '已配置' : '未配置')
    : localizeValue(String(value));
  return (
    <div className="flex flex-col gap-1 py-3 border-b border-[#0f3460]/70 last:border-b-0 min-w-0">
      <span className="text-[11px] uppercase tracking-wider text-slate-500">{label}</span>
      <span
        title={String(renderedValue)}
        className={`${mono ? 'font-mono' : ''} text-[13px] text-[#e3e2e6] break-words`}
      >
        {renderedValue}
      </span>
    </div>
  );
}

export default function SettingsView({ initialTab = 'sync' }: SettingsViewProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);
  const {
    summary: syncSummary,
    cards: syncCards,
    activityFeed,
    secondaryActions,
    source: syncSource,
    lastError,
    isExecuting,
    executingActionId,
    isRefreshing,
    refresh: refreshSync,
    executePrimaryAction,
    executeSyncAction,
  } = useSyncShellController();
  const {
    summary: settingsSummary,
    source: settingsSource,
    lastError: settingsError,
    isRefreshing: isSettingsRefreshing,
    isSaving: isSettingsSaving,
    savedAtMs,
    refresh: refreshSettings,
    saveSettings,
  } = useLocalSettingsController();
  const [themeDraft, setThemeDraft] = useState(settingsSummary.theme);
  const [localModelStatusDraft, setLocalModelStatusDraft] = useState(settingsSummary.localModelStatus);
  const [embeddingStatusDraft, setEmbeddingStatusDraft] = useState(settingsSummary.embeddingStatus);

  useEffect(() => {
    setThemeDraft(settingsSummary.theme);
    setLocalModelStatusDraft(settingsSummary.localModelStatus);
    setEmbeddingStatusDraft(settingsSummary.embeddingStatus);
  }, [settingsSummary.theme, settingsSummary.localModelStatus, settingsSummary.embeddingStatus]);

  const saveLocalSettings = async () => {
    await saveSettings({
      schema_version: 'v1',
      appearance: {
        theme: themeDraft,
      },
      ai: {
        local_model_status: localModelStatusDraft,
        embedding_status: embeddingStatusDraft,
      },
    });
  };
  const hasSettingsDraftChanges = (
    themeDraft !== settingsSummary.theme
    || localModelStatusDraft !== settingsSummary.localModelStatus
    || embeddingStatusDraft !== settingsSummary.embeddingStatus
  );
  const savedAtLabel = savedAtMs ? `已保存 ${formatActivityTime(savedAtMs)}` : null;
  const localChangesCard = syncCards.find((card) => card.card_id === 'local-changes') ?? null;
  const inspectLocalChangesAction = localChangesCard?.actions.find(
    (action) => action.action_id === 'detect-local-changes',
  );
  const submitLocalChangesAction = localChangesCard?.actions.find(
    (action) => action.action_id === 'submit-detected-commit',
  );
  const hasPendingLocalChanges = syncSummary.changeBadgeCount > 0;

  const renderActionButton = (
    action: SyncShellAction,
    emphasis: SyncShellActionEmphasis = action.emphasis,
  ) => (
    <button
      key={action.action_id}
      disabled={!action.enabled || isExecuting || isRefreshing}
      onClick={() => executeSyncAction(action)}
      title={action.reason ?? (action.requires_confirmation ? '需要确认' : undefined)}
      className={`max-w-full truncate px-3 py-1.5 rounded border text-[13px] font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors ${actionButtonClasses[emphasis]}`}
    >
      {executingActionId === action.action_id ? '处理中...' : localizeActionLabel(action)}
    </button>
  );

  return (
    <div className="flex flex-col h-full bg-[#1a1a2e] overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 md:p-8 lg:p-12 flex justify-center pb-20">
        <div className="w-full max-w-5xl flex flex-col md:flex-row gap-8">
          <aside className="w-full md:w-64 flex-shrink-0">
            <h1 className="text-3xl font-bold text-[#e3e2e6] mb-8">设置</h1>
            <nav className="flex flex-col gap-2">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`w-full px-4 py-3 rounded-lg text-[13px] font-medium flex items-center gap-3 transition-colors ${
                    activeTab === tab.id
                      ? 'bg-[#0f3460]/20 text-[#e94560] border-l-2 border-[#e94560]'
                      : 'text-slate-300 hover:bg-[#1f2b4a]'
                  }`}
                >
                  <tab.icon size={20} />
                  {tab.label}
                </button>
              ))}
            </nav>
          </aside>

          <div className="flex-1 flex flex-col gap-8">
            {activeTab === 'sync' && (
              <>
                <header className="border-b border-[#0f3460] pb-4">
                  <h2 className="text-2xl font-bold text-[#e3e2e6]">同步状态</h2>
                  <p className="text-base text-slate-400 mt-2">
                    查看本机同步状态、可执行操作和最近活动。
                  </p>
                </header>

                <div className="bg-[#16213e] rounded-xl p-4 md:p-5 border border-[#0f3460] shadow-lg shadow-black/20 flex flex-col lg:flex-row lg:items-center gap-4">
                  <div className="w-11 h-11 rounded-lg bg-[#121316] border border-[#0f3460] flex items-center justify-center flex-shrink-0">
                    <Activity className="text-[#a9c8fc]" size={22} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded border font-mono text-[11px] uppercase tracking-wider font-bold ${syncLevelClasses[syncSummary.level]}`}>
                        {localizeValue(syncSummary.level)}
                      </span>
                      <span className="font-mono text-[11px] text-slate-500 truncate">
                        {syncSummary.vaultId} / {syncSummary.deviceId}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500 uppercase tracking-wider">
                        {localizeValue(syncSource)}
                      </span>
                    </div>
                    <h3 className="text-[15px] font-bold text-[#e3e2e6] truncate">{localizeText(syncSummary.headline)}</h3>
                    <p className="text-[13px] text-slate-400 mt-1 line-clamp-2">{localizeText(syncSummary.detail)}</p>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 font-mono text-[11px] text-slate-500">
                      <span>生成于 {formatActivityTime(syncSummary.generatedAtMs)}</span>
                      <span className="truncate" title={syncSummary.vaultRoot}>
                        {syncSummary.vaultRoot}
                      </span>
                    </div>
                    {lastError && (
                      <p className="text-[12px] text-[#ffb782] mt-2 line-clamp-2">{lastError}</p>
                    )}
                  </div>
                  <div className="flex flex-wrap lg:flex-nowrap items-center gap-2">
                    <span className="px-2 py-1 rounded bg-[#121316] border border-[#0f3460] font-mono text-[11px] text-slate-400">
                      {syncSummary.changeBadgeCount} 个变更
                    </span>
                    <span className="px-2 py-1 rounded bg-[#121316] border border-[#0f3460] font-mono text-[11px] text-slate-400">
                      {syncSummary.conflictBadgeCount} 个冲突
                    </span>
                    <button
                      disabled={isRefreshing || isExecuting}
                      onClick={refreshSync}
                      title="刷新同步状态"
                      className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#121316] border border-[#0f3460] text-slate-400 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      <RefreshCw size={15} className={isRefreshing ? 'animate-spin' : ''} />
                    </button>
                    <button
                      disabled={!syncSummary.primaryActionEnabled || isExecuting || isRefreshing}
                      onClick={executePrimaryAction}
                      title={syncSummary.primaryActionRequiresConfirmation ? '需要确认' : undefined}
                      className="px-3 py-1.5 rounded bg-[#0f3460]/30 border border-[#0f3460] text-[13px] font-medium text-[#a9c8fc] disabled:opacity-50 disabled:cursor-not-allowed hover:text-white transition-colors"
                    >
                      {executingActionId === syncSummary.primaryActionId
                        ? '处理中...'
                        : (actionLabels[syncSummary.primaryActionId] ?? syncSummary.primaryActionLabel)}
                    </button>
                    {secondaryActions.map((action) => renderActionButton(action))}
                  </div>
                </div>

                {hasPendingLocalChanges && (
                  <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-4 md:p-5 shadow-lg shadow-black/20">
                    <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 mb-2">
                          <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded border border-[#ffb782]/30 bg-[#ffb782]/10 text-[#ffb782] font-mono text-[11px] uppercase tracking-wider font-bold">
                            <Cloud size={13} />
                            待同步
                          </span>
                          <span className="font-mono text-[11px] text-slate-500">
                            {syncSummary.changeBadgeCount} 个本地变更
                          </span>
                        </div>
                        <h3 className="text-[15px] font-bold text-[#e3e2e6] truncate">
                          本地编辑已准备提交
                        </h3>
                        <p className="text-[13px] text-slate-400 mt-1 line-clamp-2">
                          {localChangesCard?.body
                            ? localizeText(localChangesCard.body)
                            : '已保存的工作区编辑正在等待检查和提交。'}
                        </p>
                      </div>
                      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 lg:min-w-[420px]">
                        <div className="rounded border border-[#0f3460] bg-[#121316] px-3 py-2 min-w-0">
                          <div className="flex items-center gap-2 text-emerald-300">
                            <CheckCircle2 size={15} />
                            <span className="text-[12px] font-semibold">已保存</span>
                          </div>
                          <p className="font-mono text-[11px] text-slate-500 mt-1 truncate">工作区文件</p>
                        </div>
                        <div className="rounded border border-[#0f3460] bg-[#121316] px-3 py-2 min-w-0">
                          <div className="flex items-center gap-2 text-[#a9c8fc]">
                            <ListChecks size={15} />
                            <span className="text-[12px] font-semibold">检查</span>
                          </div>
                          <div className="mt-2">
                            {inspectLocalChangesAction
                              ? renderActionButton(inspectLocalChangesAction)
                              : <span className="font-mono text-[11px] text-slate-500">不可用</span>}
                          </div>
                        </div>
                        <div className="rounded border border-[#0f3460] bg-[#121316] px-3 py-2 min-w-0">
                          <div className="flex items-center gap-2 text-[#e94560]">
                            <GitCommitHorizontal size={15} />
                            <span className="text-[12px] font-semibold">提交</span>
                          </div>
                          <div className="mt-2">
                            {submitLocalChangesAction
                              ? renderActionButton(submitLocalChangesAction, 'primary')
                              : <span className="font-mono text-[11px] text-slate-500">不可用</span>}
                          </div>
                        </div>
                      </div>
                    </div>
                  </section>
                )}

                <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                  <div className="xl:col-span-2 flex flex-col gap-3">
                    <div className="flex items-center justify-between">
                      <h3 className="text-[15px] font-bold text-[#e3e2e6]">同步卡片</h3>
                      <span className="font-mono text-[11px] text-slate-500">{syncSummary.cardCount} 张卡片</span>
                    </div>
                    {syncCards.length === 0 ? (
                      <div className="rounded-lg border border-[#0f3460] bg-[#16213e] p-4 text-[13px] text-slate-400">
                        暂无活动同步卡片。
                      </div>
                    ) : (
                      syncCards.map((card) => (
                        <div
                          key={card.card_id}
                          className="rounded-lg border border-[#0f3460] bg-[#16213e] p-4 shadow-lg shadow-black/20"
                        >
                          <div className="flex flex-col md:flex-row md:items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2 mb-2">
                                <span className={`inline-flex items-center px-2 py-0.5 rounded border font-mono text-[11px] uppercase tracking-wider font-bold ${syncLevelClasses[card.level]}`}>
                                  {localizeValue(card.level)}
                                </span>
                                <span className="font-mono text-[11px] text-slate-500">{localizeValue(card.kind)}</span>
                                {card.badge_count > 0 && (
                                  <span className="font-mono text-[11px] text-[#ffb782]">{card.badge_count}</span>
                                )}
                              </div>
                              <h4 className="text-[14px] font-bold text-[#e3e2e6] truncate">{localizeText(card.title)}</h4>
                              <p className="text-[13px] leading-relaxed text-slate-400 mt-1">{localizeText(card.body)}</p>
                            </div>
                            {card.actions.length > 0 && (
                              <div className="flex flex-wrap md:justify-end gap-2 md:max-w-xs">
                                {card.actions.map((action) => renderActionButton(action))}
                              </div>
                            )}
                          </div>
                        </div>
                      ))
                    )}
                  </div>

                  <div className="rounded-lg border border-[#0f3460] bg-[#16213e] p-4 shadow-lg shadow-black/20">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-[15px] font-bold text-[#e3e2e6]">活动记录</h3>
                      <span className="font-mono text-[11px] text-slate-500">共 {activityFeed.total_count} 条</span>
                    </div>
                    <div className="flex flex-col divide-y divide-[#0f3460]">
                      {activityFeed.records.length === 0 ? (
                        <p className="text-[13px] text-slate-400">暂无同步活动。</p>
                      ) : (
                        activityFeed.records.map((record) => (
                          <div key={record.activity_id} className="py-3 first:pt-0 last:pb-0">
                            <div className="flex items-center justify-between gap-2">
                              <span className={`inline-flex items-center px-2 py-0.5 rounded border font-mono text-[10px] uppercase tracking-wider font-bold ${syncLevelClasses[record.level]}`}>
                                {localizeValue(record.status)}
                              </span>
                              <span className="font-mono text-[10px] text-slate-500">
                                {formatActivityTime(record.occurred_at_ms)}
                              </span>
                            </div>
                            <p className="text-[13px] text-[#e3e2e6] mt-2 truncate">
                              {actionLabels[record.action_id] ?? record.action_id}
                            </p>
                            <div className="flex items-center gap-1 text-[11px] text-slate-500 font-mono mt-1 min-w-0">
                              <span className="truncate">{actionLabels[record.command] ?? record.command}</span>
                              <ChevronRight size={12} className="flex-shrink-0" />
                              <span className="truncate">{localizeValue(record.source)}</span>
                            </div>
                            {record.message && (
                              <p className="text-[12px] text-[#ffb782] mt-1 line-clamp-2">{record.message}</p>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>

                <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-5 shadow-lg shadow-black/20">
                  <div className="flex items-center justify-between gap-3 mb-2">
                    <h3 className="text-[15px] font-bold text-[#e3e2e6]">连接</h3>
                    <span className="font-mono text-[11px] text-slate-500">{localizeValue(settingsSource)}</span>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
                    <SettingsDetailRow label="基础地址" value={settingsSummary.syncBaseUrl} mono />
                    <SettingsDetailRow label="访问令牌" value={settingsSummary.bearerTokenConfigured} />
                    <SettingsDetailRow label="请求超时" value={`${settingsSummary.requestTimeoutSeconds}s`} mono />
                    <SettingsDetailRow label="文件超时" value={`${settingsSummary.blobTimeoutSeconds}s`} mono />
                    <SettingsDetailRow label="客户端标识" value={settingsSummary.userAgent} mono />
                    <SettingsDetailRow label="设置来源" value={settingsSummary.source} mono />
                  </div>
                  {settingsError && (
                    <p className="text-[12px] text-[#ffb782] mt-4 line-clamp-3">{settingsError}</p>
                  )}
                </section>
              </>
            )}

            {activeTab === 'appearance' && (
              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-6 shadow-lg shadow-black/20">
                <div className="flex items-center justify-between gap-3 mb-5">
                  <div>
                    <h2 className="text-2xl font-bold text-[#e3e2e6]">外观</h2>
                    <p className="font-mono text-[11px] text-slate-500 mt-1">{settingsSummary.settingsPath}</p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      disabled={isSettingsRefreshing || isSettingsSaving}
                      onClick={refreshSettings}
                      title="刷新设置"
                      className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#121316] border border-[#0f3460] text-slate-400 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      <RefreshCw size={15} className={isSettingsRefreshing ? 'animate-spin' : ''} />
                    </button>
                    <button
                      disabled={isSettingsSaving || isSettingsRefreshing || !hasSettingsDraftChanges}
                      onClick={saveLocalSettings}
                      title="保存设置"
                      className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#0f3460]/30 border border-[#0f3460] text-[#a9c8fc] hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      <Save size={15} />
                    </button>
                  </div>
                </div>
                <div className="flex flex-col gap-5">
                  <div className="flex flex-col gap-2">
                    <span className="text-[11px] uppercase tracking-wider text-slate-500">主题</span>
                    <div className="inline-grid grid-cols-3 rounded border border-[#0f3460] bg-[#121316] p-1 w-full max-w-md">
                      {themeOptions.map((option) => (
                        <button
                          key={option}
                          disabled={isSettingsSaving || isSettingsRefreshing}
                          onClick={() => setThemeDraft(option)}
                          className={`px-3 py-2 rounded text-[13px] font-medium transition-colors disabled:opacity-50 ${
                            themeDraft === option
                              ? 'bg-[#0f3460]/50 text-white'
                              : 'text-slate-400 hover:text-white'
                          }`}
                        >
                          {localizeValue(option)}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
                    <SettingsDetailRow label="已保存主题" value={settingsSummary.theme} />
                    <SettingsDetailRow label="设置来源" value={settingsSummary.source} mono />
                    <SettingsDetailRow label="快照来源" value={settingsSource} mono />
                    <SettingsDetailRow label="数据结构版本" value={settingsSummary.schemaVersion} mono />
                  </div>
                </div>
                {settingsError && (
                  <p className="text-[12px] text-[#ffb782] mt-4 line-clamp-3">{settingsError}</p>
                )}
                {!settingsError && savedAtLabel && (
                  <p className="text-[12px] text-emerald-300 mt-4">{savedAtLabel}</p>
                )}
              </section>
            )}

            {activeTab === 'ai' && (
              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-6 shadow-lg shadow-black/20">
                <div className="flex items-center justify-between gap-3 mb-5">
                  <div>
                    <h2 className="text-2xl font-bold text-[#e3e2e6]">AI 模型</h2>
                    <p className="font-mono text-[11px] text-slate-500 mt-1">
                      {settingsSummary.vaultId} / {settingsSummary.deviceId}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <button
                      disabled={isSettingsRefreshing || isSettingsSaving}
                      onClick={refreshSettings}
                      title="刷新设置"
                      className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#121316] border border-[#0f3460] text-slate-400 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      <RefreshCw size={15} className={isSettingsRefreshing ? 'animate-spin' : ''} />
                    </button>
                    <button
                      disabled={isSettingsSaving || isSettingsRefreshing || !hasSettingsDraftChanges}
                      onClick={saveLocalSettings}
                      title="保存设置"
                      className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#0f3460]/30 border border-[#0f3460] text-[#a9c8fc] hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      <Save size={15} />
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <SettingsSelect
                    label="本地模型"
                    value={localModelStatusDraft}
                    options={localModelStatusOptions}
                    disabled={isSettingsSaving || isSettingsRefreshing}
                    onChange={setLocalModelStatusDraft}
                  />
                  <SettingsSelect
                    label="向量索引"
                    value={embeddingStatusDraft}
                    options={embeddingStatusOptions}
                    disabled={isSettingsSaving || isSettingsRefreshing}
                    onChange={setEmbeddingStatusDraft}
                  />
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 mt-5">
                  <SettingsDetailRow label="已保存本地模型" value={settingsSummary.localModelStatus} />
                  <SettingsDetailRow label="已保存向量索引" value={settingsSummary.embeddingStatus} />
                  <SettingsDetailRow label="设置来源" value={settingsSummary.source} mono />
                  <SettingsDetailRow label="快照来源" value={settingsSource} mono />
                </div>
                {settingsError && (
                  <p className="text-[12px] text-[#ffb782] mt-4 line-clamp-3">{settingsError}</p>
                )}
                {!settingsError && savedAtLabel && (
                  <p className="text-[12px] text-emerald-300 mt-4">{savedAtLabel}</p>
                )}
              </section>
            )}

            {activeTab === 'general' && (
              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-6 shadow-lg shadow-black/20">
                <div className="flex items-start justify-between gap-3 mb-5">
                  <div className="min-w-0">
                    <h2 className="text-2xl font-bold text-[#e3e2e6]">通用</h2>
                    <p className="font-mono text-[11px] text-slate-500 mt-1 truncate" title={settingsSummary.vaultRoot}>
                      {settingsSummary.vaultRoot}
                    </p>
                  </div>
                  <button
                    disabled={isSettingsRefreshing || isSettingsSaving}
                    onClick={refreshSettings}
                    title="刷新设置"
                    className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#121316] border border-[#0f3460] text-slate-400 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                  >
                    <RefreshCw size={15} className={isSettingsRefreshing ? 'animate-spin' : ''} />
                  </button>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6">
                  <SettingsDetailRow label="知识库 ID" value={settingsSummary.vaultId} mono />
                  <SettingsDetailRow label="设备 ID" value={settingsSummary.deviceId} mono />
                  <SettingsDetailRow label="设置路径" value={settingsSummary.settingsPath} mono />
                  <SettingsDetailRow label="设置来源" value={settingsSummary.source} mono />
                  <SettingsDetailRow label="快照来源" value={settingsSource} mono />
                  <SettingsDetailRow label="数据结构版本" value={settingsSummary.schemaVersion} mono />
                </div>
                {settingsError && (
                  <p className="text-[12px] text-[#ffb782] mt-4 line-clamp-3">{settingsError}</p>
                )}
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

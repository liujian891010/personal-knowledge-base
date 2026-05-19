import React, { useEffect, useState } from 'react';
import {
  Activity,
  Bot,
  ChevronRight,
  CheckCircle2,
  Cloud,
  Copy,
  FolderOpen,
  GitCommitHorizontal,
  Eye,
  EyeOff,
  ListChecks,
  Loader2,
  Palette,
  RefreshCw,
  Save,
  Settings,
  Trash2,
  X,
} from 'lucide-react';

import { aiModelOptions, type AiModelOption } from '../runtimeConfig';
import { syncBridgeUrl } from '../syncBridgeConfig';
import type { SyncShellAction, SyncShellActionEmphasis, SyncShellLevel } from '../syncShell';
import { useLocalSettingsController } from '../useLocalSettingsSnapshot';
import { useSyncShellController } from '../useSyncShellSnapshot';
import { useWorkspaceDevicesController } from '../useWorkspaceDevices';
import type { RegisteredWorkspace } from '../useWorkspaceRegistry';
import type { WorkspaceVaultDeviceRecord } from '../workspaceDevices';

type SettingsTab = 'general' | 'sync' | 'appearance' | 'ai';

type SettingsViewProps = {
  initialTab?: SettingsTab;
  workspaces?: RegisteredWorkspace[];
  activeWorkspace?: RegisteredWorkspace | null;
  isWorkspaceLoading?: boolean;
  isWorkspaceMutating?: boolean;
  isWorkspaceSwitching?: boolean;
  workspaceError?: string | null;
  onSelectWorkspaceFolder?: () => void | Promise<void>;
  onActivateWorkspace?: (workspaceId: string) => void | Promise<void>;
  onDeleteWorkspace?: (workspaceId: string) => void | Promise<void>;
};

type AiProviderHealthResult = {
  configured: boolean;
  status: string;
  provider_api: string | null;
  model_id: string | null;
  message: string;
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
  { id: 'appearance', label: '外观与安全', icon: Palette },
  { id: 'ai', label: 'AI 模型', icon: Bot },
];
const visibleTabs = tabs;

const actionButtonClasses: Record<SyncShellActionEmphasis, string> = {
  normal: 'bg-[#121316] border-[#0f3460] text-slate-300 hover:text-white',
  primary: 'bg-[#0f3460]/30 border-[#0f3460] text-[#a9c8fc] hover:text-white',
  warning: 'bg-[#ffb782]/10 border-[#ffb782]/30 text-[#ffb782] hover:text-white',
};

const noticeClasses: Record<SyncShellLevel, string> = {
  success: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200',
  info: 'border-[#0f3460] bg-[#0f3460]/30 text-[#a9c8fc]',
  warning: 'border-[#ffb782]/30 bg-[#ffb782]/10 text-[#ffb782]',
  danger: 'border-[#e94560]/30 bg-[#e94560]/10 text-[#ffb2b7]',
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
  blocked: '已阻止',
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

function localizeMessage(value: string): string {
  return value
    .replace(/submit conflict:/g, '提交冲突：')
    .replace(/base_revision_conflict/g, '远端基线版本冲突')
    .replace(/remote head revision (\d+)/g, '远端最新版本 $1')
    .replace(/run pull before retrying/g, '请先拉取后重试')
    .replace(/^bridge unavailable:/, '本机桥接不可用：')
    .replace(/^sync action returned/, '同步操作返回')
    .replace(/^bridge returned/, '本机桥接返回')
    .replace(/sync action not found/g, '未找到同步操作')
    .replace(/action is currently disabled/g, '当前操作不可用')
    .replace(/requires_full_pull/g, '需要先完整拉取')
    .replace(/unresolved_conflicts/g, '存在未解决冲突')
    .replace(/commit_in_progress/g, '提交仍在进行中')
    .replace(/no_local_changes/g, '没有本地变更')
    .replace(/Connection refused/g, '连接被拒绝')
    .replace(/Failed to fetch/g, '请求失败');
}

function aiModelOptionKey(option: AiModelOption): string {
  return `${option.providerApi}|${option.baseUrl}|${option.modelId}`;
}

function findAiModelOption(providerApi: string, baseUrl: string, modelId: string) {
  return aiModelOptions.find(
    (option) => option.providerApi === providerApi
      && option.baseUrl === baseUrl
      && option.modelId === modelId,
  ) ?? aiModelOptions[0];
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

function formatNullableDeviceTime(ms: number | null): string {
  return ms ? formatActivityTime(ms) : '从未上报';
}

function formatDeviceInactiveDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms <= 0) {
    return '未配置';
  }
  const days = Math.floor(ms / 86_400_000);
  if (days >= 1) {
    return `${days} 天`;
  }
  const hours = Math.floor(ms / 3_600_000);
  if (hours >= 1) {
    return `${hours} 小时`;
  }
  const minutes = Math.floor(ms / 60_000);
  if (minutes >= 1) {
    return `${minutes} 分钟`;
  }
  return `${ms} ms`;
}

function deviceLifecycleInfo(device: WorkspaceVaultDeviceRecord): {
  label: string;
  className: string;
  description: string;
} {
  if (device.is_revoked) {
    return {
      label: '已移除',
      className: 'border-[#e94560]/40 bg-[#e94560]/10 text-[#ffb3c0]',
      description: '该设备已被撤销同步资格，不再参与新提交或 tombstone 回收判断。',
    };
  }
  if (device.is_inactive_candidate) {
    return {
      label: '疑似失活',
      className: 'border-[#ffb782]/30 bg-[#ffb782]/10 text-[#ffb782]',
      description: '超过失活阈值未上报 heartbeat，后续可由用户确认移除。',
    };
  }
  return {
    label: '活跃',
    className: 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300',
    description: '最近仍在上报 heartbeat，参与同步 ack 与回收安全判断。',
  };
}

export default function SettingsView({
  initialTab = 'sync',
  workspaces = [],
  activeWorkspace = null,
  isWorkspaceLoading = false,
  isWorkspaceMutating = false,
  isWorkspaceSwitching = false,
  workspaceError = null,
  onSelectWorkspaceFolder,
  onActivateWorkspace,
  onDeleteWorkspace,
}: SettingsViewProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>(
    visibleTabs.some((tab) => tab.id === initialTab) ? initialTab : 'general',
  );
  const {
    summary: syncSummary,
    cards: syncCards,
    activityFeed,
    secondaryActions,
    source: syncSource,
    lastError,
    lastActionNotice,
    isExecuting,
    executingActionId,
    isRefreshing,
    refresh: refreshSync,
    executePrimaryAction,
    executeSyncAction,
  } = useSyncShellController(activeTab === 'sync' && Boolean(activeWorkspace));
  const {
    summary: settingsSummary,
    source: settingsSource,
    lastError: settingsError,
    isRefreshing: isSettingsRefreshing,
    isSaving: isSettingsSaving,
    isCryptoMutating,
    savedAtMs,
    refresh: refreshSettings,
    saveSettings,
    unlockCrypto,
    lockCrypto,
    exportCryptoRecoveryPackage,
    importCryptoRecoveryPackage,
  } = useLocalSettingsController();
  const {
    deviceList,
    devices: workspaceDevices,
    lastHeartbeat: deviceHeartbeat,
    lastError: deviceLastError,
    isLoading: isDevicesLoading,
    isMutating: isDevicesMutating,
    loadDevices,
    heartbeatDevice,
    revokeDevice,
    clear: clearDevices,
  } = useWorkspaceDevicesController();
  const [themeDraft, setThemeDraft] = useState(settingsSummary.theme);
  const [localModelStatusDraft, setLocalModelStatusDraft] = useState(settingsSummary.localModelStatus);
  const [embeddingStatusDraft, setEmbeddingStatusDraft] = useState(settingsSummary.embeddingStatus);
  const [aiProviderApiDraft, setAiProviderApiDraft] = useState(settingsSummary.aiProviderApi);
  const [aiBaseUrlDraft, setAiBaseUrlDraft] = useState(settingsSummary.aiBaseUrl);
  const [aiModelIdDraft, setAiModelIdDraft] = useState(settingsSummary.aiModelId);
  const [aiKeyDraft, setAiKeyDraft] = useState('');
  const [confirmedAiKeyDraft, setConfirmedAiKeyDraft] = useState('');
  const [isAiKeyVisible, setIsAiKeyVisible] = useState(false);
  const [isTestingAiProvider, setIsTestingAiProvider] = useState(false);
  const [aiProviderHealth, setAiProviderHealth] = useState<AiProviderHealthResult | null>(null);
  const [vaultKeyDraft, setVaultKeyDraft] = useState('');
  const [isVaultKeyVisible, setIsVaultKeyVisible] = useState(false);
  const [recoveryPhraseDraft, setRecoveryPhraseDraft] = useState('');
  const [recoveryPackageDraft, setRecoveryPackageDraft] = useState('');
  const [exportedRecoveryPackageJson, setExportedRecoveryPackageJson] = useState('');
  const [cryptoNotice, setCryptoNotice] = useState<string | null>(null);
  const [workspaceToDelete, setWorkspaceToDelete] = useState<RegisteredWorkspace | null>(null);
  const [workspaceToActivate, setWorkspaceToActivate] = useState<RegisteredWorkspace | null>(null);
  const [deviceToRevoke, setDeviceToRevoke] = useState<WorkspaceVaultDeviceRecord | null>(null);
  const activeWorkspaceId = activeWorkspace?.id ?? null;

  useEffect(() => {
    setThemeDraft(settingsSummary.theme);
    setLocalModelStatusDraft(settingsSummary.localModelStatus);
    setEmbeddingStatusDraft(settingsSummary.embeddingStatus);
    setAiProviderApiDraft(settingsSummary.aiProviderApi);
    setAiBaseUrlDraft(settingsSummary.aiBaseUrl);
    setAiModelIdDraft(settingsSummary.aiModelId);
    setAiKeyDraft(settingsSummary.aiKey);
    setConfirmedAiKeyDraft(settingsSummary.aiKey);
    setIsAiKeyVisible(false);
    setAiProviderHealth(null);
    setVaultKeyDraft('');
    setIsVaultKeyVisible(false);
    setRecoveryPhraseDraft('');
    setRecoveryPackageDraft('');
    setExportedRecoveryPackageJson('');
    setCryptoNotice(null);
  }, [
    settingsSummary.theme,
    settingsSummary.localModelStatus,
    settingsSummary.embeddingStatus,
    settingsSummary.aiProviderApi,
    settingsSummary.aiBaseUrl,
    settingsSummary.aiModelId,
    settingsSummary.aiKey,
    settingsSummary.cryptoUnlocked,
  ]);

  useEffect(() => {
    if (activeTab !== 'sync' || !activeWorkspaceId) {
      clearDevices();
      setDeviceToRevoke(null);
      return;
    }
    void loadDevices().catch(() => undefined);
  }, [activeTab, activeWorkspaceId, clearDevices, loadDevices]);

  const saveLocalSettings = async () => {
    await saveSettings({
      schema_version: 'v1',
      appearance: {
        theme: themeDraft,
      },
      ai: {
        local_model_status: localModelStatusDraft,
        embedding_status: embeddingStatusDraft,
        provider_api: aiProviderApiDraft,
        base_url: aiBaseUrlDraft,
        model_id: aiModelIdDraft,
        ...(confirmedAiKeyDraft ? { api_key: confirmedAiKeyDraft } : {}),
      },
    });
  };
  const hasSettingsDraftChanges = (
    themeDraft !== settingsSummary.theme
    || localModelStatusDraft !== settingsSummary.localModelStatus
    || embeddingStatusDraft !== settingsSummary.embeddingStatus
    || aiProviderApiDraft !== settingsSummary.aiProviderApi
    || aiBaseUrlDraft !== settingsSummary.aiBaseUrl
    || aiModelIdDraft !== settingsSummary.aiModelId
    || confirmedAiKeyDraft !== settingsSummary.aiKey
  );
  const selectedAiModelOption = findAiModelOption(aiProviderApiDraft, aiBaseUrlDraft, aiModelIdDraft);
  const testAiProvider = async () => {
    setIsTestingAiProvider(true);
    try {
      if (hasSettingsDraftChanges) {
        await saveLocalSettings();
      }
      const response = await fetch(`${syncBridgeUrl}/api/ai/provider/health`, {
        method: 'POST',
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(String(payload.message ?? `AI provider health returned ${response.status}`));
      }
      setAiProviderHealth(payload as AiProviderHealthResult);
    } catch (error) {
      setAiProviderHealth({
        configured: false,
        status: 'error',
        provider_api: aiProviderApiDraft,
        model_id: aiModelIdDraft,
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setIsTestingAiProvider(false);
    }
  };
  const saveWorkspaceFolder = async () => {
    await onSelectWorkspaceFolder?.();
  };
  const savedAtLabel = savedAtMs ? `已保存 ${formatActivityTime(savedAtMs)}` : null;
  const normalizeVaultKeyBase64 = (value: string) => {
    const trimmed = value.trim();
    if (!trimmed) {
      return '';
    }
    if (/^[0-9a-fA-F]{64}$/.test(trimmed)) {
      const bytes = trimmed.match(/.{1,2}/g)?.map((item) => Number.parseInt(item, 16)) ?? [];
      return btoa(String.fromCharCode(...bytes));
    }
    return trimmed;
  };
  const unlockVaultKey = async () => {
    const normalized = normalizeVaultKeyBase64(vaultKeyDraft);
    if (!normalized) {
      return;
    }
    await unlockCrypto(normalized);
    setVaultKeyDraft('');
    setIsVaultKeyVisible(false);
  };
  const lockVaultKey = async () => {
    await lockCrypto();
    setVaultKeyDraft('');
    setIsVaultKeyVisible(false);
  };
  const exportRecoveryPackage = async () => {
    if (!recoveryPhraseDraft.trim()) {
      setCryptoNotice('请输入恢复短语后再生成恢复包。');
      return;
    }
    const recoveryPackageJson = await exportCryptoRecoveryPackage(recoveryPhraseDraft.trim());
    if (recoveryPackageJson) {
      setExportedRecoveryPackageJson(recoveryPackageJson);
      setCryptoNotice('恢复包已生成。请把恢复包 JSON 和恢复短语分开保存。');
    }
  };
  const importRecoveryPackage = async () => {
    if (!recoveryPhraseDraft.trim()) {
      setCryptoNotice('请输入恢复短语。');
      return;
    }
    if (!recoveryPackageDraft.trim()) {
      setCryptoNotice('请提供恢复包文件，或使用一台已解锁设备进行本地配对。');
      return;
    }
    const imported = await importCryptoRecoveryPackage(
      recoveryPhraseDraft.trim(),
      recoveryPackageDraft.trim(),
    );
    if (imported) {
      setRecoveryPhraseDraft('');
      setRecoveryPackageDraft('');
      setExportedRecoveryPackageJson('');
      setCryptoNotice('恢复包已导入，本机已解锁 e2ee-v1。');
    }
  };
  const copyExportedRecoveryPackage = async () => {
    if (!exportedRecoveryPackageJson) {
      return;
    }
    try {
      await navigator.clipboard.writeText(exportedRecoveryPackageJson);
      setCryptoNotice('恢复包 JSON 已复制。');
    } catch {
      setCryptoNotice('浏览器拒绝剪贴板写入，请手动复制恢复包 JSON。');
    }
  };
  const refreshDevices = async () => {
    try {
      await loadDevices();
    } catch {
      // Error state is stored in the device controller.
    }
  };
  const sendDeviceHeartbeat = async () => {
    try {
      await heartbeatDevice();
    } catch {
      // Error state is stored in the device controller.
    }
  };
  const confirmRevokeWorkspaceDevice = async () => {
    if (!deviceToRevoke || deviceToRevoke.is_current_device || deviceToRevoke.is_revoked) {
      return;
    }
    try {
      await revokeDevice(deviceToRevoke.device_id);
      setDeviceToRevoke(null);
    } catch {
      // Keep the dialog open and show the controller error.
    }
  };
  const localChangesCard = syncCards.find((card) => card.card_id === 'local-changes') ?? null;
  const inspectLocalChangesAction = localChangesCard?.actions.find(
    (action) => action.action_id === 'detect-local-changes',
  );
  const submitLocalChangesAction = localChangesCard?.actions.find(
    (action) => action.action_id === 'submit-detected-commit',
  );
  const hasPendingLocalChanges = syncSummary.changeBadgeCount > 0;
  const isSettingsInitialLoading = settingsSource === 'loading' && isSettingsRefreshing;

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
              {visibleTabs.map((tab) => (
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
            {isSettingsInitialLoading ? (
              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-6 shadow-lg shadow-black/20">
                <div className="flex items-center gap-3 text-slate-300">
                  <RefreshCw size={16} className="animate-spin text-[#a9c8fc]" />
                  <span className="text-[13px] font-semibold">正在读取本地设置...</span>
                </div>
              </section>
            ) : activeTab === 'sync' && (
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
                      <p className="text-[12px] text-[#ffb782] mt-2 line-clamp-2">{localizeMessage(lastError)}</p>
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

                {lastActionNotice && (
                  <section className={`rounded-xl border p-4 shadow-lg shadow-black/20 ${noticeClasses[lastActionNotice.level]}`}>
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 mb-1">
                          <span className="font-mono text-[11px] uppercase tracking-wider">
                            {localizeValue(lastActionNotice.level)}
                          </span>
                          <span className="font-mono text-[11px] opacity-80">
                            {formatActivityTime(lastActionNotice.occurredAtMs)}
                          </span>
                        </div>
                        <h3 className="text-[15px] font-bold truncate">{lastActionNotice.title}</h3>
                        <p className="text-[13px] mt-1 opacity-90 line-clamp-2">
                          {localizeText(localizeMessage(lastActionNotice.detail))}
                        </p>
                      </div>
                      <span className="font-mono text-[11px] opacity-70">
                        {actionLabels[lastActionNotice.actionId] ?? lastActionNotice.actionId}
                      </span>
                    </div>
                  </section>
                )}

                <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-4 md:p-5 shadow-lg shadow-black/20">
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-[#e3e2e6]">
                        <Cloud size={18} className="text-[#a9c8fc]" />
                        <h3 className="text-[15px] font-bold">设备管理</h3>
                      </div>
                      <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
                        设备列表用于解释每台设备的 heartbeat、ack 进度和失活状态；移除只撤销该设备的云端同步资格，不删除本机工作区文件。
                      </p>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <button
                        type="button"
                        disabled={!activeWorkspace || isDevicesLoading || isDevicesMutating}
                        onClick={() => void refreshDevices()}
                        className="inline-flex h-9 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 text-[12px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        <RefreshCw size={14} className={isDevicesLoading ? 'animate-spin' : ''} />
                        刷新
                      </button>
                      <button
                        type="button"
                        disabled={!activeWorkspace || isDevicesLoading || isDevicesMutating}
                        onClick={() => void sendDeviceHeartbeat()}
                        className="inline-flex h-9 items-center justify-center gap-2 rounded border border-[#2a5ea3] bg-[#0f3460]/30 px-3 text-[12px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {isDevicesMutating ? <Loader2 size={14} className="animate-spin" /> : <Activity size={14} />}
                        发送心跳
                      </button>
                    </div>
                  </div>

                  <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
                    <div className="rounded-lg border border-[#0f3460] bg-[#121316] px-3 py-2">
                      <div className="text-[11px] uppercase tracking-wider text-slate-500">Vault</div>
                      <div className="mt-1 truncate font-mono text-[12px] text-[#e3e2e6]" title={deviceList?.vault_id ?? syncSummary.vaultId}>
                        {deviceList?.vault_id ?? syncSummary.vaultId}
                      </div>
                    </div>
                    <div className="rounded-lg border border-[#0f3460] bg-[#121316] px-3 py-2">
                      <div className="text-[11px] uppercase tracking-wider text-slate-500">Head revision</div>
                      <div className="mt-1 font-mono text-[12px] text-[#e3e2e6]">
                        {deviceList ? deviceList.head_revision : '-'}
                      </div>
                    </div>
                    <div className="rounded-lg border border-[#0f3460] bg-[#121316] px-3 py-2">
                      <div className="text-[11px] uppercase tracking-wider text-slate-500">失活阈值</div>
                      <div className="mt-1 font-mono text-[12px] text-[#e3e2e6]">
                        {deviceList ? formatDeviceInactiveDuration(deviceList.inactive_after_ms) : '-'}
                      </div>
                    </div>
                  </div>

                  <div className="mt-3 rounded-lg border border-[#0f3460] bg-[#0d0e11] p-3 text-[12px] leading-5 text-slate-400">
                    状态说明：活跃表示设备仍在上报 heartbeat；疑似失活表示超过阈值未上报，可人工移除；已移除表示设备不再参与同步和 tombstone 回收安全判断。当前设备不可从本机移除。
                  </div>

                  {deviceHeartbeat && (
                    <div className="mt-3 rounded-lg border border-emerald-400/30 bg-emerald-400/10 p-3 text-[12px] text-emerald-200">
                      心跳已上报：{deviceHeartbeat.device_id} · ack revision {deviceHeartbeat.acked_revision} · {formatActivityTime(deviceHeartbeat.last_seen_at_ms)}
                    </div>
                  )}

                  {deviceLastError && (
                    <div className="mt-3 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] leading-5 text-[#ffb782]">
                      {localizeMessage(deviceLastError)}
                    </div>
                  )}

                  {!activeWorkspace && (
                    <div className="mt-4 rounded-lg border border-dashed border-[#0f3460] px-3 py-4 text-[12px] text-slate-500">
                      请先在通用设置中添加或切换到一个工作区，再查看设备列表。
                    </div>
                  )}

                  {activeWorkspace && isDevicesLoading && workspaceDevices.length === 0 && (
                    <div className="mt-4 flex items-center gap-2 rounded-lg border border-[#0f3460] bg-[#121316] px-3 py-4 text-[12px] text-slate-400">
                      <Loader2 size={14} className="animate-spin text-[#a9c8fc]" />
                      正在读取设备列表...
                    </div>
                  )}

                  {activeWorkspace && !isDevicesLoading && workspaceDevices.length === 0 && !deviceLastError && (
                    <div className="mt-4 rounded-lg border border-dashed border-[#0f3460] px-3 py-4 text-[12px] text-slate-500">
                      暂无已加入此 vault 的设备。完成一次提交或拉取 ack 后会生成当前设备记录；心跳只刷新已加入设备的在线时间。
                    </div>
                  )}

                  {activeWorkspace && workspaceDevices.length > 0 && (
                    <div className="mt-4 grid grid-cols-1 gap-3">
                      {workspaceDevices.map((device) => {
                        const lifecycle = deviceLifecycleInfo(device);
                        return (
                          <div
                            key={device.device_id}
                            className="rounded-xl border border-[#0f3460] bg-[#121316] p-4"
                          >
                            <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                              <div className="min-w-0">
                                <div className="flex flex-wrap items-center gap-2">
                                  {device.is_current_device && (
                                    <span className="rounded border border-[#2a5ea3] bg-[#0f3460]/30 px-2 py-0.5 text-[10px] font-bold text-[#a9c8fc]">
                                      当前设备
                                    </span>
                                  )}
                                  <span className={`rounded border px-2 py-0.5 text-[10px] font-bold ${lifecycle.className}`}>
                                    {lifecycle.label}
                                  </span>
                                  <span className="font-mono text-[10px] text-slate-500">{device.platform}</span>
                                </div>
                                <h4 className="mt-2 truncate text-[14px] font-bold text-[#e3e2e6]" title={device.device_name}>
                                  {device.device_name}
                                </h4>
                                <p className="mt-1 break-all font-mono text-[11px] text-slate-500">{device.device_id}</p>
                                <p className="mt-2 text-[12px] leading-5 text-slate-400">{lifecycle.description}</p>
                              </div>
                              {!device.is_current_device && !device.is_revoked && (
                                <button
                                  type="button"
                                  disabled={isDevicesMutating}
                                  onClick={() => setDeviceToRevoke(device)}
                                  className="inline-flex h-9 shrink-0 items-center justify-center gap-2 rounded-lg border border-[#e94560]/40 bg-[#e94560]/10 px-3 text-[12px] font-semibold text-[#ffb3c0] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                                >
                                  <Trash2 size={14} />
                                  移除
                                </button>
                              )}
                            </div>
                            <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-4">
                              <div className="rounded border border-[#0f3460] bg-[#0d0e11] px-3 py-2">
                                <div className="text-[10px] uppercase tracking-wider text-slate-500">ack revision</div>
                                <div className="mt-1 font-mono text-[12px] text-[#e3e2e6]">{device.acked_revision}</div>
                              </div>
                              <div className="rounded border border-[#0f3460] bg-[#0d0e11] px-3 py-2">
                                <div className="text-[10px] uppercase tracking-wider text-slate-500">last seen</div>
                                <div className="mt-1 font-mono text-[12px] text-[#e3e2e6]">{formatNullableDeviceTime(device.last_seen_at_ms)}</div>
                              </div>
                              <div className="rounded border border-[#0f3460] bg-[#0d0e11] px-3 py-2">
                                <div className="text-[10px] uppercase tracking-wider text-slate-500">registered</div>
                                <div className="mt-1 font-mono text-[12px] text-[#e3e2e6]">{formatNullableDeviceTime(device.registered_at_ms)}</div>
                              </div>
                              <div className="rounded border border-[#0f3460] bg-[#0d0e11] px-3 py-2">
                                <div className="text-[10px] uppercase tracking-wider text-slate-500">version</div>
                                <div className="mt-1 truncate font-mono text-[12px] text-[#e3e2e6]" title={`${device.app_version ?? 'unknown'} / ${device.protocol_version}`}>
                                  {device.app_version ?? 'unknown'} / {device.protocol_version}
                                </div>
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  )}
                </section>

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
                              <p className="text-[12px] text-[#ffb782] mt-1 line-clamp-2">
                                {localizeMessage(record.message)}
                              </p>
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

                {deviceToRevoke && (
                  <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
                    <div className="w-full max-w-md rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 shadow-2xl shadow-black/50">
                      <div className="flex items-center justify-between gap-3">
                        <h3 className="text-lg font-bold text-[#e3e2e6]">移除设备</h3>
                        <button
                          type="button"
                          disabled={isDevicesMutating}
                          onClick={() => setDeviceToRevoke(null)}
                          className="rounded border border-[#0f3460] bg-[#121316] p-2 text-slate-400 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <X size={16} />
                        </button>
                      </div>
                      <p className="mt-3 text-[13px] leading-6 text-slate-400">
                        确认移除该设备的云端同步资格：{deviceToRevoke.device_name}
                      </p>
                      <p className="mt-2 break-all font-mono text-[11px] text-slate-500">{deviceToRevoke.device_id}</p>
                      <p className="mt-3 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] leading-5 text-[#ffb782]">
                        移除后，该设备不会再计入 ack 和 tombstone 回收判断。若该设备仍在使用，需要重新接入或恢复同步身份。
                      </p>
                      {deviceLastError && (
                        <p className="mt-3 text-[12px] leading-5 text-[#ffb782]">{localizeMessage(deviceLastError)}</p>
                      )}
                      <div className="mt-5 flex justify-end gap-2">
                        <button
                          type="button"
                          disabled={isDevicesMutating}
                          onClick={() => setDeviceToRevoke(null)}
                          className="rounded border border-[#0f3460] bg-[#121316] px-4 py-2 text-[13px] text-slate-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          取消
                        </button>
                        <button
                          type="button"
                          disabled={isDevicesMutating || deviceToRevoke.is_current_device || deviceToRevoke.is_revoked}
                          onClick={() => void confirmRevokeWorkspaceDevice()}
                          className="inline-flex items-center justify-center gap-2 rounded border border-[#e94560]/40 bg-[#e94560]/20 px-4 py-2 text-[13px] font-semibold text-[#ffb3c0] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isDevicesMutating && <Loader2 size={14} className="animate-spin" />}
                          确认移除
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </>
            )}

            {!isSettingsInitialLoading && activeTab === 'appearance' && (
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
                <div className="mt-5 rounded-lg border border-[#0f3460] bg-[#121316] p-4">
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className={`inline-flex rounded border px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider ${
                          settingsSummary.cryptoUnlocked
                            ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-300'
                            : 'border-[#ffb782]/30 bg-[#ffb782]/10 text-[#ffb782]'
                        }`}
                        >
                          {settingsSummary.cryptoUnlocked ? 'E2EE unlocked' : 'E2EE locked'}
                        </span>
                        <span className="font-mono text-[11px] text-slate-500">{settingsSummary.cryptoScheme}</span>
                        <span className="font-mono text-[11px] text-slate-500">{settingsSummary.cryptoStorageProvider}</span>
                      </div>
                      <h3 className="mt-2 text-[15px] font-bold text-[#e3e2e6]">同步加密密钥</h3>
                      <p className="mt-1 text-[12px] leading-relaxed text-slate-400">
                        vault key 只写入系统安全存储，不写入 .noteapp/settings.json。解锁后同步命令会自动使用 e2ee-v1。
                      </p>
                      <p className="mt-2 break-all font-mono text-[11px] text-slate-500">{settingsSummary.cryptoMessage}</p>
                      {settingsSummary.cryptoError && (
                        <p className="mt-2 text-[12px] text-[#ffb782]">{settingsSummary.cryptoError}</p>
                      )}
                    </div>
                    <div className="w-full lg:w-[360px]">
                      <div className="flex overflow-hidden rounded border border-[#0f3460] bg-[#0d0e11] focus-within:border-[#e94560]">
                        <input
                          value={vaultKeyDraft}
                          type={isVaultKeyVisible ? 'text' : 'password'}
                          autoComplete="off"
                          placeholder="32-byte vault key: base64 or 64 hex chars"
                          disabled={isCryptoMutating}
                          onChange={(event) => setVaultKeyDraft(event.target.value)}
                          className="min-w-0 flex-1 bg-transparent px-3 py-2 text-[13px] text-[#e3e2e6] placeholder:text-slate-600 disabled:opacity-50 focus:outline-none"
                        />
                        <button
                          type="button"
                          disabled={isCryptoMutating || !vaultKeyDraft}
                          onClick={() => setIsVaultKeyVisible((value) => !value)}
                          className="inline-flex w-10 items-center justify-center border-l border-[#0f3460] text-slate-400 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isVaultKeyVisible ? <EyeOff size={15} /> : <Eye size={15} />}
                        </button>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <button
                          type="button"
                          disabled={isCryptoMutating || !vaultKeyDraft.trim()}
                          onClick={() => void unlockVaultKey()}
                          className="inline-flex h-9 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-4 text-[12px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isCryptoMutating && <Loader2 size={14} className="animate-spin" />}
                          解锁并保存
                        </button>
                        <button
                          type="button"
                          disabled={isCryptoMutating || !settingsSummary.cryptoKeyAvailable}
                          onClick={() => void lockVaultKey()}
                          className="inline-flex h-9 items-center justify-center rounded border border-[#e94560]/40 bg-[#e94560]/10 px-4 text-[12px] font-semibold text-[#ffb3c0] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          锁定本机
                        </button>
                      </div>
                    </div>
                  </div>
                </div>
                <div className="mt-5 rounded-lg border border-[#0f3460] bg-[#121316] p-4">
                  <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
                    <div className="min-w-0">
                      <div className="inline-flex rounded border border-[#0f3460] bg-[#0f3460]/30 px-2 py-0.5 font-mono text-[11px] font-bold uppercase tracking-wider text-[#a9c8fc]">
                        e2ee-recovery-v1
                      </div>
                      <h3 className="mt-2 text-[15px] font-bold text-[#e3e2e6]">恢复包与恢复短语</h3>
                      <p className="mt-1 text-[12px] leading-relaxed text-slate-400">
                        恢复包是离线 JSON 文件，恢复短语只负责解开恢复包。两者缺一不可；恢复短语不会上传到云端。
                      </p>
                      {cryptoNotice && (
                        <p className="mt-2 text-[12px] text-[#a9c8fc]">{cryptoNotice}</p>
                      )}
                    </div>
                    <div className="w-full lg:w-[460px]">
                      <input
                        value={recoveryPhraseDraft}
                        type="password"
                        autoComplete="off"
                        placeholder="恢复短语"
                        disabled={isCryptoMutating}
                        onChange={(event) => setRecoveryPhraseDraft(event.target.value)}
                        className="w-full rounded border border-[#0f3460] bg-[#0d0e11] px-3 py-2 text-[13px] text-[#e3e2e6] placeholder:text-slate-600 disabled:opacity-50 focus:border-[#e94560] focus:outline-none"
                      />
                      <textarea
                        value={recoveryPackageDraft}
                        placeholder="粘贴恢复包 JSON；只输入恢复短语会被拒绝"
                        disabled={isCryptoMutating}
                        onChange={(event) => setRecoveryPackageDraft(event.target.value)}
                        className="mt-2 h-28 w-full resize-y rounded border border-[#0f3460] bg-[#0d0e11] px-3 py-2 font-mono text-[12px] text-[#e3e2e6] placeholder:text-slate-600 disabled:opacity-50 focus:border-[#e94560] focus:outline-none"
                      />
                      <div className="mt-2 flex flex-wrap gap-2">
                        <button
                          type="button"
                          disabled={isCryptoMutating || !settingsSummary.cryptoKeyAvailable || !recoveryPhraseDraft.trim()}
                          onClick={() => void exportRecoveryPackage()}
                          className="inline-flex h-9 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-4 text-[12px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isCryptoMutating && <Loader2 size={14} className="animate-spin" />}
                          生成恢复包
                        </button>
                        <button
                          type="button"
                          disabled={isCryptoMutating || !recoveryPhraseDraft.trim() || !recoveryPackageDraft.trim()}
                          onClick={() => void importRecoveryPackage()}
                          className="inline-flex h-9 items-center justify-center rounded border border-emerald-400/30 bg-emerald-400/10 px-4 text-[12px] font-semibold text-emerald-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          导入并解锁
                        </button>
                      </div>
                      {exportedRecoveryPackageJson && (
                        <div className="mt-3 rounded border border-[#0f3460] bg-[#0d0e11] p-3">
                          <div className="mb-2 flex items-center justify-between gap-2">
                            <span className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">恢复包 JSON</span>
                            <button
                              type="button"
                              onClick={() => void copyExportedRecoveryPackage()}
                              className="inline-flex h-7 items-center justify-center gap-1 rounded border border-[#0f3460] px-2 text-[11px] font-semibold text-[#a9c8fc] hover:text-white"
                            >
                              <Copy size={12} />
                              复制
                            </button>
                          </div>
                          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all font-mono text-[11px] leading-relaxed text-slate-300">
                            {exportedRecoveryPackageJson}
                          </pre>
                        </div>
                      )}
                    </div>
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

            {!isSettingsInitialLoading && activeTab === 'ai' && (
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
                    <button
                      disabled={isSettingsSaving || isSettingsRefreshing || isTestingAiProvider}
                      onClick={() => void testAiProvider()}
                      title="Test AI provider"
                      className="inline-flex h-8 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 text-[12px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <CheckCircle2 size={14} />
                      {isTestingAiProvider ? 'Testing' : 'Test'}
                    </button>
                  </div>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                  <label className="flex flex-col gap-2 min-w-0 md:col-span-2">
                    <span className="text-[11px] uppercase tracking-wider text-slate-500">模型名称</span>
                    <select
                      value={aiModelOptionKey(selectedAiModelOption)}
                      disabled={isSettingsSaving || isSettingsRefreshing}
                      onChange={(event) => {
                        const option = aiModelOptions.find((item) => aiModelOptionKey(item) === event.target.value)
                          ?? aiModelOptions[0];
                        setAiProviderApiDraft(option.providerApi);
                        setAiBaseUrlDraft(option.baseUrl);
                        setAiModelIdDraft(option.modelId);
                        setLocalModelStatusDraft('available');
                      }}
                      className="w-full rounded border border-[#0f3460] bg-[#121316] px-3 py-2 text-[13px] text-[#e3e2e6] disabled:opacity-50 focus:outline-none focus:border-[#e94560]"
                    >
                      {aiModelOptions.map((option) => (
                        <option key={aiModelOptionKey(option)} value={aiModelOptionKey(option)}>
                          {option.label} / {option.environment}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col gap-2 min-w-0 md:col-span-2">
                    <span className="text-[11px] uppercase tracking-wider text-slate-500">AI Key</span>
                    <div className="flex flex-col gap-2 md:flex-row">
                      <div className="flex min-w-0 flex-1 overflow-hidden rounded border border-[#0f3460] bg-[#121316] focus-within:border-[#e94560]">
                        <input
                          value={aiKeyDraft}
                          type={isAiKeyVisible ? 'text' : 'password'}
                          autoComplete="off"
                          placeholder="请输入......"
                          disabled={isSettingsSaving || isSettingsRefreshing}
                          onChange={(event) => {
                            setAiKeyDraft(event.target.value);
                            setConfirmedAiKeyDraft('');
                          }}
                          className="min-w-0 flex-1 bg-transparent px-3 py-2 text-[13px] text-[#e3e2e6] placeholder:text-slate-600 disabled:opacity-50 focus:outline-none"
                        />
                        <button
                          type="button"
                          disabled={isSettingsSaving || isSettingsRefreshing || !aiKeyDraft}
                          onClick={() => setIsAiKeyVisible((value) => !value)}
                          className="inline-flex w-10 items-center justify-center border-l border-[#0f3460] text-slate-400 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                          title={isAiKeyVisible ? '隐藏 AI Key' : '显示 AI Key'}
                        >
                          {isAiKeyVisible ? <EyeOff size={15} /> : <Eye size={15} />}
                        </button>
                      </div>
                      <button
                        type="button"
                        disabled={isSettingsSaving || isSettingsRefreshing || !aiKeyDraft.trim()}
                        onClick={() => setConfirmedAiKeyDraft(aiKeyDraft.trim())}
                        className="inline-flex h-9 items-center justify-center rounded border border-[#0f3460] bg-[#0f3460]/30 px-4 text-[12px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        确定
                      </button>
                    </div>
                    {aiKeyDraft.trim() && !confirmedAiKeyDraft && (
                      <span className="text-[11px] text-[#ffb782]">请先点击“确定”，再保存或测试模型。</span>
                    )}
                    {confirmedAiKeyDraft && (
                      <span className="text-[11px] text-emerald-300">AI Key 已确认，保存后生效。</span>
                    )}
                    <span className="text-[12px] text-slate-500">
                      Key 只保存在本机 `.noteapp/settings.json`，不会写入同步数据；保存后 AI Wiki 问答会直接使用该模型。
                    </span>
                  </label>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-x-6 mt-5">
                  <SettingsDetailRow label="模型 ID" value={settingsSummary.aiModelId} mono />
                  <SettingsDetailRow label="协议" value={settingsSummary.aiProviderApi} mono />
                  <SettingsDetailRow label="模型地址" value={settingsSummary.aiBaseUrl} mono />
                  <SettingsDetailRow label="AI Key" value={settingsSummary.aiKeyConfigured ? 'configured' : 'not_configured'} />
                </div>
                {aiProviderHealth && (
                  <div className={`mt-4 rounded-lg border p-3 text-[12px] ${
                    aiProviderHealth.status === 'available'
                      ? 'border-emerald-400/30 bg-emerald-400/10 text-emerald-200'
                      : 'border-[#ffb782]/30 bg-[#ffb782]/10 text-[#ffb782]'
                  }`}
                  >
                    <div className="mb-1 flex flex-wrap items-center gap-2">
                      <span className="font-mono uppercase">{aiProviderHealth.status}</span>
                      {aiProviderHealth.model_id && (
                        <span className="font-mono text-slate-400">{aiProviderHealth.model_id}</span>
                      )}
                    </div>
                    <p className="leading-relaxed">{aiProviderHealth.message}</p>
                  </div>
                )}
                {settingsError && (
                  <p className="text-[12px] text-[#ffb782] mt-4 line-clamp-3">{settingsError}</p>
                )}
                {!settingsError && savedAtLabel && (
                  <p className="text-[12px] text-emerald-300 mt-4">{savedAtLabel}</p>
                )}
              </section>
            )}

            {!isSettingsInitialLoading && activeTab === 'general' && (
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
                <div className="mb-5 rounded-lg border border-[#0f3460] bg-[#121316] p-4">
                  <div className="mb-3 flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2 text-[#e3e2e6]">
                        <FolderOpen size={18} className="text-[#a9c8fc]" />
                        <h3 className="text-[15px] font-bold">工作区</h3>
                      </div>
                      <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
                        工作区切换、添加与移除统一放在这里。移除只会从列表移除，不会删除原始文件。
                      </p>
                    </div>
                    <button
                      disabled={isWorkspaceLoading || isWorkspaceMutating || isWorkspaceSwitching}
                      onClick={saveWorkspaceFolder}
                      className="inline-flex h-10 shrink-0 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-4 text-[13px] font-semibold text-[#a9c8fc] transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {(isWorkspaceLoading || isWorkspaceMutating) && <Loader2 size={15} className="animate-spin" />}
                      <FolderOpen size={15} />
                      添加工作区
                    </button>
                  </div>
                  {workspaceError && (
                    <div className="mb-3 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] leading-5 text-[#ffb782]">
                      {workspaceError}
                    </div>
                  )}
                  <div className="hidden">
                    <div className="text-[11px] uppercase tracking-wider text-slate-500">当前工作区</div>
                    <div className="mt-2 truncate text-[13px] font-semibold text-[#e3e2e6]">
                      {activeWorkspace?.name ?? '未选择工作区'}
                    </div>
                    <div className="mt-1 break-all font-mono text-[11px] text-slate-500">
                      {activeWorkspace?.vault_root ?? settingsSummary.vaultRoot}
                    </div>
                  </div>
                  <div className="grid gap-3">
                    {workspaces.length === 0 && (
                      <div className="rounded-lg border border-dashed border-[#0f3460] px-3 py-4 text-[12px] text-slate-500">
                        暂无工作区，请先添加一个 Markdown 工作区。
                      </div>
                    )}
                    {workspaces.map((workspace) => (
                      <div
                        key={workspace.id}
                        className={`grid grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-xl border p-4 ${
                          workspace.is_active
                            ? 'border-[#e94560]/40 bg-[#0f3460]/30'
                            : 'border-[#0f3460] bg-[#0d0e11]'
                        }`}
                      >
                        <button
                          type="button"
                          disabled={isWorkspaceSwitching}
                          onClick={() => {
                            if (!workspace.is_active) {
                              setWorkspaceToActivate(workspace);
                            }
                          }}
                          className="min-w-0 text-left disabled:cursor-not-allowed"
                        >
                          <div className="flex items-center gap-2">
                            <span className="truncate text-[13px] font-semibold text-[#e3e2e6]">{workspace.name}</span>
                            {workspace.is_active && (
                              <span className="rounded border border-[#e94560]/40 bg-[#e94560]/10 px-2 py-0.5 text-[10px] font-bold text-[#ffb3c0]">
                                当前
                              </span>
                            )}
                          </div>
                          <p className="mt-1 break-all font-mono text-[11px] text-slate-500">{workspace.vault_root}</p>
                          <p className="mt-2 text-[11px] text-slate-500">
                            {workspace.initialized ? '已初始化' : '待初始化'} · {workspace.exists ? '路径存在' : '路径缺失'}
                          </p>
                        </button>
                        <div className="flex items-center gap-2">
                          {!workspace.is_active && (
                            <button
                              type="button"
                              disabled={isWorkspaceSwitching}
                              onClick={() => setWorkspaceToActivate(workspace)}
                              className="inline-flex h-9 items-center justify-center rounded-lg border border-[#2a5ea3] bg-[#0f3460]/30 px-3 text-[12px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                            >
                              切换
                            </button>
                          )}
                          <button
                            type="button"
                            disabled={isWorkspaceMutating || isWorkspaceSwitching}
                            onClick={() => setWorkspaceToDelete(workspace)}
                            className="inline-flex h-9 items-center justify-center rounded-lg border border-[#0f3460] bg-[#121316] px-3 text-[12px] text-slate-400 hover:text-[#ffb782] disabled:cursor-not-allowed disabled:opacity-50"
                            title="移除工作区"
                          >
                            <Trash2 size={14} />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
                <div className="hidden">
                  <div className="mb-3 flex items-center gap-2 text-[#e3e2e6]">
                    <FolderOpen size={18} className="text-[#a9c8fc]" />
                    <h3 className="text-[15px] font-bold">工作区文件夹</h3>
                  </div>
                  <div className="flex flex-col gap-3 lg:flex-row">
                    <div
                      title={settingsSummary.vaultRoot}
                      className="min-w-0 flex-1 truncate rounded border border-[#0f3460] bg-[#0d0e11] px-3 py-2 font-mono text-[13px] text-[#e3e2e6]"
                    >
                      {settingsSummary.vaultRoot}
                    </div>
                    <button
                      disabled={isSettingsSaving || isSettingsRefreshing}
                      onClick={saveWorkspaceFolder}
                      className="inline-flex h-10 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-4 text-[13px] font-semibold text-[#a9c8fc] transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <FolderOpen size={15} />
                      选择文件夹
                    </button>
                  </div>
                  <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
                    打开系统文件夹选择框，选择已有文件夹作为当前工作区。应用后会初始化缺失的 `.noteapp` 数据，并刷新笔记库、同步状态和设置。
                  </p>
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
                {workspaceToDelete && (
                  <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
                    <div className="w-full max-w-md rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 shadow-2xl shadow-black/50">
                      <div className="flex items-center justify-between gap-3">
                        <h3 className="text-lg font-bold text-[#e3e2e6]">移除工作区</h3>
                        <button
                          type="button"
                          onClick={() => setWorkspaceToDelete(null)}
                          className="rounded border border-[#0f3460] bg-[#121316] p-2 text-slate-400 hover:text-white"
                        >
                          <X size={16} />
                        </button>
                      </div>
                      <p className="mt-3 text-[13px] leading-6 text-slate-400">
                        将从列表移除：{workspaceToDelete.name}
                      </p>
                      <p className="mt-2 break-all font-mono text-[11px] text-slate-500">{workspaceToDelete.vault_root}</p>
                      <div className="mt-5 flex justify-end gap-2">
                        <button
                          type="button"
                          onClick={() => setWorkspaceToDelete(null)}
                          className="rounded border border-[#0f3460] bg-[#121316] px-4 py-2 text-[13px] text-slate-300 hover:text-white"
                        >
                          取消
                        </button>
                        <button
                          type="button"
                          disabled={isWorkspaceMutating || isWorkspaceSwitching}
                          onClick={() => {
                            void onDeleteWorkspace?.(workspaceToDelete.id);
                            setWorkspaceToDelete(null);
                          }}
                          className="inline-flex items-center justify-center gap-2 rounded border border-[#e94560]/40 bg-[#e94560]/20 px-4 py-2 text-[13px] font-semibold text-[#ffb3c0] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {(isWorkspaceMutating || isWorkspaceSwitching) && <Loader2 size={14} className="animate-spin" />}
                          移除
                        </button>
                      </div>
                    </div>
                  </div>
                )}
                {workspaceToActivate && (
                  <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
                    <div className="w-full max-w-md rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 shadow-2xl shadow-black/50">
                      <div className="flex items-center justify-between gap-3">
                        <h3 className="text-lg font-bold text-[#e3e2e6]">切换工作区</h3>
                        <button
                          type="button"
                          disabled={isWorkspaceSwitching}
                          onClick={() => setWorkspaceToActivate(null)}
                          className="rounded border border-[#0f3460] bg-[#121316] p-2 text-slate-400 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          <X size={16} />
                        </button>
                      </div>
                      <p className="mt-3 text-[13px] leading-6 text-slate-400">
                        确认切换到：{workspaceToActivate.name}
                      </p>
                      <p className="mt-2 break-all font-mono text-[11px] text-slate-500">{workspaceToActivate.vault_root}</p>
                      <div className="mt-5 flex justify-end gap-2">
                        <button
                          type="button"
                          disabled={isWorkspaceSwitching}
                          onClick={() => setWorkspaceToActivate(null)}
                          className="rounded border border-[#0f3460] bg-[#121316] px-4 py-2 text-[13px] text-slate-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          取消
                        </button>
                        <button
                          type="button"
                          disabled={isWorkspaceSwitching}
                          onClick={() => {
                            void onActivateWorkspace?.(workspaceToActivate.id);
                            setWorkspaceToActivate(null);
                          }}
                          className="inline-flex items-center justify-center gap-2 rounded border border-[#2a5ea3] bg-[#0f3460]/40 px-4 py-2 text-[13px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                        >
                          {isWorkspaceSwitching && <Loader2 size={14} className="animate-spin" />}
                          确认切换
                        </button>
                      </div>
                    </div>
                  </div>
                )}
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

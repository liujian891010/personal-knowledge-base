import {
  executeBridgeAction,
  fetchSampleAppSession,
  fetchBridgeStatus,
  refreshAppSession,
  refreshBridgeSnapshot,
  startBridgeStatusPolling,
} from "./bridge-client.js";
import {
  buildDraftRecoveryEntry,
  createRuntimeSessionId,
  DRAFT_RECOVERY_STORAGE_KEY,
  listRecoverableDrafts,
  parseDraftRecoveryStore,
  removeDraftRecoveryEntry,
  serializeDraftRecoveryStore,
  upsertDraftRecoveryEntry,
} from "./draft-recovery.js";

const SAMPLE_PATH = "./fixtures/sync-shell-snapshot.sample.json";
const LIVE_SNAPSHOT_PATH = "./fixtures/live-sync-shell.json";
const WORKSPACE_SAMPLE_PATH = "./fixtures/workspace-shell.sample.json";
const DEFAULT_ACTION_COMMAND = "pkb-desktop-sync";
const LOCAL_UI_SETTINGS_STORAGE_KEY = "noteapp.local-ui-settings";
const LOCAL_WORKSPACE_SESSION_STORAGE_KEY = "noteapp.local-workspace-session";
const DEFAULT_LOCAL_UI_SETTINGS = {
  aiRawFileLimitMb: 2,
  aiRawTotalLimitMb: 500,
  aiRawCleanupPolicy: "prompt",
  includeAiRawInExport: false,
  expertMode: false,
};
const draftAutosaveTimers = new Map();
const WORKSPACE_SAMPLE = {
  sections: [
    {
      id: "notes",
      label: "笔记",
      items: [
        {
          id: "desktop-bridge",
          title: "桌面桥接推进",
          path: "Notes/Engineering/Desktop Bridge Rollout.md",
          status: "已修改",
        },
        {
          id: "sync-recovery",
          title: "同步恢复检查清单",
          path: "Notes/Engineering/Sync Recovery Checklist.md",
          status: "待复核",
        },
        {
          id: "release-cadence",
          title: "发布节奏",
          path: "Notes/Product/Release Cadence.md",
          status: "稳定",
        },
      ],
    },
    {
      id: "ai-wiki",
      label: ".ai/wiki",
      items: [
        {
          id: "desktop-shell",
          title: "桌面壳层规格",
          path: ".ai/wiki/shell/Desktop Shell Spec.md",
          status: "AI 草稿",
        },
        {
          id: "bridge-diagnostics",
          title: "桥接诊断",
          path: ".ai/wiki/ops/Bridge Diagnostics.md",
          status: "待确认",
        },
      ],
    },
  ],
  notes: {
    "desktop-bridge": {
      title: "桌面桥接推进",
      path: "Notes/Engineering/Desktop Bridge Rollout.md",
      statusTone: "warning",
      statusLabel: "本地有修改",
      lastSaved: "6 分钟前保存",
      tags: ["sync", "desktop", "bridge", "noteapp-web"],
      syncContext: {
        watchActionIds: ["pull", "show-vault-summary"],
        watchCardKinds: ["baseline", "activity"],
        watchBlockingReasons: ["requires_full_pull"],
      },
      body: `# 桌面桥接推进

## 当前阶段
- 通过桌面 CLI 导出同步壳层快照。
- 通过本地 bridge 转发可执行动作，而不是在浏览器里重复实现桌面逻辑。
- 让浏览器侧能直接看见本地配置缺失与桥接诊断。

## 下一步
1. 用轻量测试固定 bridge 契约。
2. 把 Web 壳层从同步页扩成完整工作台。
3. 逐步替换成真实文件树与编辑器契约。`,
      ai: {
        queueDepth: 2,
        warnings: 1,
        relatedEntities: ["桌面 CLI", "同步中心", "本地桥接配置"],
        suggestions: [
          "把 bridge 状态载荷提升成前端共享契约。",
          "把当前选中文档上下文暴露给 AI 面板，支撑后续工作流。",
        ],
        lint: ["桥接状态需要明确展示配置优先级和失败代码。"],
      },
    },
    "sync-recovery": {
      title: "同步恢复检查清单",
      path: "Notes/Engineering/Sync Recovery Checklist.md",
      statusTone: "danger",
      statusLabel: "需要冲突核查",
      lastSaved: "昨天保存",
      tags: ["sync", "recovery", "conflicts"],
      syncContext: {
        watchActionIds: ["pull", "submit-detected-commit"],
        watchCardKinds: ["baseline", "local-changes", "activity"],
        watchBlockingReasons: ["requires_full_pull"],
      },
      body: `# 同步恢复检查清单

## 重试前
- 确认失败尝试留下的 staging 文件已经清理。
- 确认未解决的冲突副本仍然能在仓库中定位。
- 从新的工作树扫描重新构建下一轮快照。

## 操作提示
不要从过期的明文快照继续提交。下一轮必须基于最新源版本重新构建。`,
      ai: {
        queueDepth: 1,
        warnings: 3,
        relatedEntities: ["commit_intent_journal", ".noteapp/staging", "conflict_copies"],
        suggestions: [
          "当需要清理 staging 时，在同步区增加显眼的恢复徽标。",
          "未来从文件树契约直接链接到冲突工件。",
        ],
        lint: [
          "缺少 blob staging 清理的回滚说明。",
          "需要补一个快照漂移中止的操作时间线示例。",
        ],
      },
    },
    "release-cadence": {
      title: "发布节奏",
      path: "Notes/Product/Release Cadence.md",
      statusTone: "success",
      statusLabel: "可发布",
      lastSaved: "今天上午保存",
      tags: ["product", "delivery", "weekly"],
      syncContext: {
        watchActionIds: ["show-vault-summary"],
        watchCardKinds: ["local-changes"],
        watchBlockingReasons: [],
      },
      body: `# 发布节奏

## 交付规则
- 每个有意义阶段结束后都推送代码。
- 保证静态壳层在每一步都可运行。
- 除非强相关，否则不要把实验性 UI 与 bridge 修复混在同一个提交里。

## 每周节奏
周一：同步与诊断
周三：桌面壳层边界
周五：工作区体验打磨`,
      ai: {
        queueDepth: 0,
        warnings: 0,
        relatedEntities: ["周回顾", "里程碑看板", "发布说明"],
        suggestions: ["把最近三次推送整理成一版更新说明草稿。"],
        lint: [],
      },
    },
    "desktop-shell": {
      title: "桌面壳层规格",
      path: ".ai/wiki/shell/Desktop Shell Spec.md",
      statusTone: "info",
      statusLabel: "AI 草稿",
      lastSaved: "18 分钟前编译",
      tags: ["ai", "shell", "spec"],
      syncContext: {
        watchActionIds: ["sync-activity"],
        watchCardKinds: ["activity"],
        watchBlockingReasons: [],
      },
      body: `# 桌面壳层规格

## 目标
沉淀桌面客户端暴露给静态 Web 层的壳层契约。

## 覆盖范围
- sync-shell-snapshot
- sync-center 摘要与卡片
- activity feed
- 可执行动作转发

## 缺口
当前 Web 壳层仍缺少一等文件树与编辑器契约。`,
      ai: {
        queueDepth: 4,
        warnings: 2,
        relatedEntities: ["sync-shell-snapshot", "activity_feed", "execute-sync-action-and-snapshot"],
        suggestions: ["等编辑器和文件树接入真实契约后，再确认这版 AI 草稿。"],
        lint: ["规格里引用了未来 UI 面板，但还没有对应样例契约。"],
      },
    },
    "bridge-diagnostics": {
      title: "桥接诊断",
      path: ".ai/wiki/ops/Bridge Diagnostics.md",
      statusTone: "warning",
      statusLabel: "待确认",
      lastSaved: "2 小时前编译",
      tags: ["ai", "ops", "diagnostics"],
      syncContext: {
        watchActionIds: ["pull", "sync-activity"],
        watchCardKinds: ["activity", "baseline"],
        watchBlockingReasons: ["requires_full_pull"],
      },
      body: `# 桥接诊断

## 已覆盖信号
- 配置来源优先级
- 必填 bridge 配置缺失
- 桌面 CLI 拉起失败
- 桌面边界返回非法 JSON

## 待补
把诊断统一收敛到共享前端模型，同时保持浏览器侧副本足够轻量。`,
      ai: {
        queueDepth: 1,
        warnings: 1,
        relatedEntities: ["configSource", "sourceByField", "desktop_cli_invalid_json"],
        suggestions: ["补一个适合演示截图的状态面板。"],
        lint: ["需要明确写出 bearer token 只保留在本地。"],
      },
    },
  },
};

const state = {
  syncCenter: null,
  activityFeed: null,
  snapshotMetadata: null,
  workspaceShell: null,
  workspaceSourceLabel: "未加载",
  selectedWorkspaceNoteId: "desktop-bridge",
  selectedAction: null,
  selectedActionSource: null,
  bridgeStatus: null,
  bridgeCheckedAtMs: null,
  lastBridgeError: null,
  lastExecution: null,
  lastExecutionAtMs: null,
  sourceLabel: "未加载",
  searchQuery: "",
  activeNavView: "overview",
  appSession: null,
  sessionHistory: [],
  editorDrafts: {},
  draftRecoveryMeta: {},
  recoveryDrafts: [],
  localUiSettings: DEFAULT_LOCAL_UI_SETTINGS,
  runtimeSessionId: createRuntimeSessionId(),
};

const elements = {
  searchInput: document.getElementById("search-input"),
  quickCaptureButton: document.getElementById("quick-capture-button"),
  newNoteButton: document.getElementById("new-note-button"),
  navButtons: Array.from(document.querySelectorAll("[data-nav-view]")),
  payloadKind: document.getElementById("payload-kind"),
  payloadDetail: document.getElementById("payload-detail"),
  controlCenterCard: document.getElementById("control-center-card"),
  toggleAdvancedControlsButton: document.getElementById("toggle-advanced-controls-button"),
  controlModeNote: document.getElementById("control-mode-note"),
  workspaceShellRoot: document.getElementById("workspace-shell-root"),
  viewModeCard: document.getElementById("view-mode-card"),
  viewDetailGrid: document.getElementById("view-detail-grid"),
  panelCard: document.getElementById("panel-card"),
  summaryGrid: document.getElementById("summary-grid"),
  cardsGrid: document.getElementById("cards-grid"),
  activityCard: document.getElementById("activity-card"),
  actionContractOutput: document.getElementById("action-contract-output"),
  actionExecutionCard: document.getElementById("action-execution-card"),
  actionContractHelp: document.getElementById("action-contract-help"),
  actionResultOutput: document.getElementById("action-result-output"),
  payloadInput: document.getElementById("payload-input"),
  loadAppSessionButton: document.getElementById("load-app-session-button"),
  refreshAppSessionButton: document.getElementById("refresh-app-session-button"),
  loadSampleButton: document.getElementById("load-sample-button"),
  loadLiveButton: document.getElementById("load-live-button"),
  refreshLocalButton: document.getElementById("refresh-local-button"),
  reloadBridgeStatusButton: document.getElementById("reload-bridge-status-button"),
  applyInputButton: document.getElementById("apply-input-button"),
  clearInputButton: document.getElementById("clear-input-button"),
  fileInput: document.getElementById("file-input"),
  bridgeStatus: document.getElementById("bridge-status"),
  bridgeDiagnosticsOutput: document.getElementById("bridge-diagnostics-output"),
  bridgeErrorOutput: document.getElementById("bridge-error-output"),
  executeSelectedButton: document.getElementById("execute-selected-button"),
  actionExecutionStatus: document.getElementById("action-execution-status"),
  actionChipTemplate: document.getElementById("action-chip-template"),
  workspaceSignalTemplate: document.getElementById("workspace-signal-template"),
  loadWorkspaceSampleButton: document.getElementById("load-workspace-sample-button"),
  workspaceFileInput: document.getElementById("workspace-file-input"),
  workspaceStatus: document.getElementById("workspace-status"),
  workspaceInput: document.getElementById("workspace-input"),
  applyWorkspaceInputButton: document.getElementById("apply-workspace-input-button"),
  workspaceTree: document.getElementById("workspace-tree"),
  workspaceRail: document.getElementById("workspace-rail"),
  workspaceEditor: document.getElementById("workspace-editor"),
  workspaceAiPanel: document.getElementById("workspace-ai-panel"),
};

function formatDateTime(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "未知时间";
  }

  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function cloneJson(value) {
  return JSON.parse(JSON.stringify(value));
}

function readDraftRecoveryStore() {
  try {
    return parseDraftRecoveryStore(window.localStorage.getItem(DRAFT_RECOVERY_STORAGE_KEY));
  } catch {
    return parseDraftRecoveryStore(null);
  }
}

function writeDraftRecoveryStore(store) {
  try {
    window.localStorage.setItem(DRAFT_RECOVERY_STORAGE_KEY, serializeDraftRecoveryStore(store));
    return true;
  } catch {
    return false;
  }
}

function parseLocalUiSettings(raw) {
  if (!raw) {
    return { ...DEFAULT_LOCAL_UI_SETTINGS };
  }

  try {
    const parsed = JSON.parse(raw);
    const fileLimit = Number(parsed?.aiRawFileLimitMb);
    const totalLimit = Number(parsed?.aiRawTotalLimitMb);
    const cleanupPolicies = new Set(["manual", "prompt", "prune-oldest"]);
    return {
      aiRawFileLimitMb: Number.isFinite(fileLimit) && fileLimit > 0 ? Math.min(fileLimit, 100) : DEFAULT_LOCAL_UI_SETTINGS.aiRawFileLimitMb,
      aiRawTotalLimitMb:
        Number.isFinite(totalLimit) && totalLimit > 0 ? Math.min(totalLimit, 5000) : DEFAULT_LOCAL_UI_SETTINGS.aiRawTotalLimitMb,
      aiRawCleanupPolicy: cleanupPolicies.has(parsed?.aiRawCleanupPolicy)
        ? parsed.aiRawCleanupPolicy
        : DEFAULT_LOCAL_UI_SETTINGS.aiRawCleanupPolicy,
      includeAiRawInExport: Boolean(parsed?.includeAiRawInExport),
      expertMode: Boolean(parsed?.expertMode),
    };
  } catch {
    return { ...DEFAULT_LOCAL_UI_SETTINGS };
  }
}

function persistLocalUiSettings(nextSettings) {
  state.localUiSettings = {
    ...DEFAULT_LOCAL_UI_SETTINGS,
    ...state.localUiSettings,
    ...nextSettings,
  };
  try {
    window.localStorage.setItem(LOCAL_UI_SETTINGS_STORAGE_KEY, JSON.stringify(state.localUiSettings));
  } catch {
    return false;
  }
  return true;
}

function loadLocalUiSettings() {
  try {
    state.localUiSettings = parseLocalUiSettings(window.localStorage.getItem(LOCAL_UI_SETTINGS_STORAGE_KEY));
  } catch {
    state.localUiSettings = { ...DEFAULT_LOCAL_UI_SETTINGS };
  }
}

function readLocalWorkspaceSession() {
  try {
    const raw = window.localStorage.getItem(LOCAL_WORKSPACE_SESSION_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    const workspaceShell = validateWorkspaceShell(parsed.workspaceShell);
    return {
      workspaceShell,
      selectedWorkspaceNoteId:
        typeof parsed.selectedWorkspaceNoteId === "string" ? parsed.selectedWorkspaceNoteId : "desktop-bridge",
      workspaceSourceLabel:
        typeof parsed.workspaceSourceLabel === "string" ? parsed.workspaceSourceLabel : "浏览器本地草稿",
      savedAtMs: typeof parsed.savedAtMs === "number" ? parsed.savedAtMs : Date.now(),
    };
  } catch {
    return null;
  }
}

function clearLocalWorkspaceSession() {
  try {
    window.localStorage.removeItem(LOCAL_WORKSPACE_SESSION_STORAGE_KEY);
    return true;
  } catch {
    return false;
  }
}

function persistLocalWorkspaceSession() {
  if (!state.workspaceShell || state.workspaceSourceLabel !== "浏览器本地草稿") {
    clearLocalWorkspaceSession();
    return false;
  }
  try {
    window.localStorage.setItem(
      LOCAL_WORKSPACE_SESSION_STORAGE_KEY,
      JSON.stringify({
        workspaceShell: state.workspaceShell,
        selectedWorkspaceNoteId: state.selectedWorkspaceNoteId,
        workspaceSourceLabel: state.workspaceSourceLabel,
        savedAtMs: Date.now(),
      }),
    );
    return true;
  } catch {
    return false;
  }
}

function normalizeSearchQuery(value = "") {
  return String(value).trim().toLocaleLowerCase("zh-CN");
}

function resetSearchQuery() {
  state.searchQuery = "";
  elements.searchInput.value = "";
}

function applySearchQuery(value = "") {
  state.searchQuery = String(value || "");
  elements.searchInput.value = state.searchQuery;
}

function matchesSearchQuery(query, ...values) {
  if (!query) {
    return true;
  }

  return values.some((value) => String(value || "").toLocaleLowerCase("zh-CN").includes(query));
}

function detectPayloadKind(payload) {
  if (
    payload &&
    typeof payload === "object" &&
    payload.sync_center &&
    payload.activity_feed &&
    typeof payload.generated_at_ms === "number"
  ) {
    return "sync-shell-snapshot";
  }

  if (
    payload &&
    typeof payload === "object" &&
    Array.isArray(payload.cards) &&
    payload.panel &&
    payload.summary
  ) {
    return "sync-center";
  }

  if (
    payload &&
    typeof payload === "object" &&
    Array.isArray(payload.records) &&
    typeof payload.total_count === "number"
  ) {
    return "sync-activity";
  }

  throw new Error(
    "不支持的同步载荷结构。请提供 sync-shell-snapshot、sync-center 或 sync-activity JSON。",
  );
}

function validateWorkspaceShell(payload) {
  if (
    payload &&
    typeof payload === "object" &&
    !Array.isArray(payload) &&
    Array.isArray(payload.sections) &&
    payload.notes &&
    typeof payload.notes === "object" &&
    !Array.isArray(payload.notes)
  ) {
    return payload;
  }

  throw new Error("不支持的工作区壳层结构。期望格式为 { sections: [], notes: {} }。");
}

function validateAppSession(payload) {
  if (
    payload &&
    typeof payload === "object" &&
    payload.syncPayload &&
    payload.workspaceShell &&
    typeof payload.source === "string" &&
    typeof payload.loadedAtMs === "number"
  ) {
    return payload;
  }

  throw new Error("不支持的应用会话结构。");
}

function renderExecutionResult(execution) {
  if (!execution) {
    elements.actionResultOutput.textContent = "尚未执行任何动作。";
    return;
  }
  elements.actionResultOutput.textContent = JSON.stringify(execution, null, 2);
}

function renderActionExecutionPanel() {
  if (!elements.actionExecutionCard) {
    return;
  }

  const selectedAction = state.selectedAction;
  const lastExecution = buildLastExecutionSummary();
  const bridgeReady = Boolean(state.bridgeStatus?.available);
  const selectedSource = state.selectedActionSource
    ? formatActionSourceLabel(state.selectedActionSource)
    : "尚未选择";
  const readinessLabel = selectedAction
    ? selectedAction.enabled === false
      ? "当前不可执行"
      : bridgeReady
        ? "可以执行"
        : "等待本地桥接"
    : "等待选择动作";
  const readinessTone = selectedAction
    ? selectedAction.enabled === false
      ? "warning"
      : bridgeReady
        ? "success"
        : "warning"
    : "info";
  const nextStep = !selectedAction
    ? "先在总览、仓库浏览或冲突处理中选中一个动作。"
    : selectedAction.enabled === false
      ? selectedAction.reason
        ? `先处理：${formatActionReason(selectedAction.reason)}`
        : "当前动作暂不可执行，请先处理前置条件。"
      : bridgeReady
        ? "可以直接执行当前动作，执行后结果会回写到这里。"
        : "先连接本地桌面桥接，再执行当前动作。";

  elements.actionExecutionCard.innerHTML = `
    <article class="session-rail-item">
      <span class="session-rail-title">当前动作</span>
      <strong>${escapeHtml(selectedAction?.label || "尚未选择动作")}</strong>
      <p class="session-rail-copy">${escapeHtml(selectedAction ? `${selectedAction.action_id} · ${selectedSource}` : "从工作台中挑选一个动作后，这里会进入可执行状态。")}</p>
      <div class="session-rail-pills">
        <span class="mini-pill tone-${readinessTone}">${escapeHtml(readinessLabel)}</span>
        ${
          selectedAction?.requires_confirmation
            ? '<span class="mini-pill tone-warning">需要确认</span>'
            : ""
        }
      </div>
    </article>
    <article class="session-rail-item">
      <span class="session-rail-title">执行条件</span>
      <strong>${escapeHtml(bridgeReady ? "本地桥接已就绪" : "本地桥接未就绪")}</strong>
      <p class="session-rail-copy">${escapeHtml(nextStep)}</p>
      ${
        selectedAction?.command
          ? `<p class="session-rail-copy">${escapeHtml(`动作命令：${selectedAction.command}`)}</p>`
          : ""
      }
    </article>
    <article class="session-rail-item">
      <span class="session-rail-title">最近结果</span>
      <strong>${escapeHtml(lastExecution ? `${lastExecution.statusLabel} · ${lastExecution.actionId}` : "还没有执行记录")}</strong>
      <p class="session-rail-copy">${escapeHtml(lastExecution ? `${lastExecution.atLabel} · ${lastExecution.commandLine || "通过本地桥接执行"}` : "执行后的结果会在这里汇总展示。")}</p>
      ${
        state.lastBridgeError
          ? `<p class="session-rail-copy tone-warning-inline">${escapeHtml(`最近错误：${state.lastBridgeError.message}`)}</p>`
          : ""
      }
    </article>
  `;
}

function normalizeBridgeError(error) {
  if (!error) {
    return null;
  }
  if (error instanceof Error) {
    return {
      code: "client_error",
      message: error.message,
      details: null,
    };
  }
  if (typeof error === "object") {
    return {
      code: typeof error.code === "string" ? error.code : "bridge_error",
      message:
        typeof error.message === "string" ? error.message : "桥接请求发生了未预期错误。",
      details: "details" in error ? error.details : null,
    };
  }
  return {
    code: "bridge_error",
    message: String(error),
    details: null,
  };
}

function buildOfflineBridgeStatus(error = null) {
  return {
    mode: "dev-server-unreachable",
    available: false,
    missing: ["dev_server_bridge"],
    config: null,
    diagnostics: [
      {
        level: "danger",
        code: "dev_server_bridge_unreachable",
        message: "无法连接本地开发桥接服务。",
        details: error ? { reason: error.message } : null,
      },
    ],
  };
}

function renderBridgeError(error) {
  if (!error) {
    elements.bridgeErrorOutput.textContent = "当前没有桥接错误。";
    return;
  }
  elements.bridgeErrorOutput.textContent = JSON.stringify(error, null, 2);
}

function renderBridgeDiagnostics(status) {
  if (!status) {
    elements.bridgeDiagnosticsOutput.textContent = "等待 /api/bridge/status 返回...";
    return;
  }
  elements.bridgeDiagnosticsOutput.textContent = JSON.stringify(
    {
      checkedAt: state.bridgeCheckedAtMs ? formatDateTime(state.bridgeCheckedAtMs) : "尚未检查",
      mode: status.mode || "未知",
      available: Boolean(status.available),
      missing: status.missing || [],
      config: status.config || null,
      diagnostics: Array.isArray(status.diagnostics) ? status.diagnostics : [],
    },
    null,
    2,
  );
}

function renderBridgeStatus() {
  const status = state.bridgeStatus;
  if (!status) {
    elements.bridgeStatus.textContent = "正在检查本地桌面桥接状态...";
    elements.refreshLocalButton.disabled = true;
    elements.executeSelectedButton.disabled = true;
    elements.reloadBridgeStatusButton.disabled = true;
    renderBridgeDiagnostics(null);
    renderBridgeError(state.lastBridgeError);
    return;
  }

  if (status.available) {
    const sourceLabel = formatBridgeConfigSource(status.config?.configSource || "env");
    elements.bridgeStatus.textContent =
      `本地桌面桥接已就绪：${status.config.vaultId} · ${status.config.vaultRoot} · ${sourceLabel}`;
    elements.refreshLocalButton.disabled = false;
    elements.executeSelectedButton.disabled = !state.selectedAction;
    elements.reloadBridgeStatusButton.disabled = false;
    renderBridgeDiagnostics(status);
    renderBridgeError(state.lastBridgeError);
    return;
  }

  elements.bridgeStatus.textContent = `本地桌面桥接不可用：缺少 ${status.missing.join(", ")}`;
  elements.refreshLocalButton.disabled = true;
  elements.executeSelectedButton.disabled = true;
  elements.reloadBridgeStatusButton.disabled = false;
  renderBridgeDiagnostics(status);
  renderBridgeError(state.lastBridgeError);
}

function renderControlCenterMode() {
  const expertMode = Boolean(state.localUiSettings.expertMode);
  if (elements.toggleAdvancedControlsButton) {
    elements.toggleAdvancedControlsButton.textContent = expertMode ? "返回普通工作台模式" : "开启高级调试";
  }
  if (elements.controlModeNote) {
    elements.controlModeNote.textContent = expertMode
      ? "当前为高级调试模式，原始 JSON、桥接诊断和手动契约覆盖入口已展开到设置页。"
      : "当前为普通工作台模式，原始 JSON 覆盖和桥接诊断默认收起，只保留主工作链路。";
  }
  if (elements.controlCenterCard) {
    elements.controlCenterCard
      .querySelectorAll("[data-advanced-control]")
      .forEach((node) => {
        node.hidden = !expertMode;
      });
  }
}

function setSelectedAction(action, source = "手动选择") {
  state.selectedAction = action;
  state.selectedActionSource = action ? source : null;

  if (!action) {
    elements.actionContractHelp.textContent =
      "请先从总览、仓库浏览或冲突处理中选中一个动作，再回到这里执行。";
    elements.actionContractOutput.textContent = "尚未选择任何动作。";
    elements.actionExecutionStatus.textContent =
      "只有本地桌面桥接可用，且动作本身未被门禁阻塞时，才允许执行。";
    elements.executeSelectedButton.disabled = true;
    renderActionExecutionPanel();
    refreshActionChipSelection();
    return;
  }

  const commandLine = [DEFAULT_ACTION_COMMAND, action.command, ...(action.argv || [])].join(" ");
  const lines = [
    `来源：${formatActionSourceLabel(source)}`,
    `动作 ID：${action.action_id}`,
    `可执行：${action.enabled !== false ? "是" : "否"}`,
    `命令：${action.command}`,
    `参数：${JSON.stringify(action.argv || [])}`,
    `需要确认：${Boolean(action.requires_confirmation) ? "是" : "否"}`,
    `Shell 契约：${commandLine}`,
  ];

  if (action.reason) {
    lines.push(`原因：${formatActionReason(action.reason)}`);
  }

  elements.actionContractHelp.textContent =
    "普通模式下这里优先展示执行条件与结果；如需核对原始命令契约，可切到高级调试模式查看。";
  elements.actionContractOutput.textContent = lines.join("\n");
  elements.actionExecutionStatus.textContent =
    "点击“执行当前动作”后，会通过本地桌面 bridge 执行并把结果回写到当前面板。";
  elements.executeSelectedButton.disabled = !state.bridgeStatus?.available;
  pushSessionHistory({
    type: "sync_action_selected",
    level: action.enabled === false ? "warning" : "info",
    detail: `${action.action_id} · ${formatActionSourceLabel(source)}`,
  });
  renderActionExecutionPanel();
  refreshActionChipSelection();
}

function createActionChip(action, source) {
  const fragment = elements.actionChipTemplate.content.cloneNode(true);
  const button = fragment.querySelector(".action-chip");
  const label = fragment.querySelector(".action-label");
  const command = fragment.querySelector(".action-command");

  label.textContent = action.label;
  command.textContent = [action.command, ...(action.argv || [])].join(" ");
  button.dataset.actionId = action.action_id;

  if (action.enabled === false) {
    button.classList.add("is-disabled");
  }

  button.addEventListener("click", () => setSelectedAction(action, source));
  return fragment;
}

function refreshActionChipSelection() {
  const selectedActionId = state.selectedAction?.action_id || null;
  const lastExecutedActionId = state.lastExecution?.action?.action_id || null;
  for (const button of document.querySelectorAll(".action-chip[data-action-id]")) {
    button.classList.toggle("is-selected", button.dataset.actionId === selectedActionId);
    button.classList.toggle("is-last-executed", button.dataset.actionId === lastExecutedActionId);
  }
}

function resolveTone(level) {
  return ["success", "warning", "danger", "info"].includes(level) ? level : "info";
}

function formatLevelLabel(level) {
  return {
    success: "正常",
    warning: "注意",
    danger: "风险",
    info: "信息",
  }[level] || level;
}

function formatStatusLabel(status) {
  return {
    executed: "已执行",
    disabled: "不可执行",
    failed: "失败",
    healthy: "健康",
    degraded: "降级",
    stale: "过期",
    unsupported: "不支持",
    unknown: "未知",
  }[status] || status;
}

function formatPayloadKindLabel(kind) {
  return {
    "sync-shell-snapshot": "同步壳层快照",
    "sync-center": "同步中心",
    "sync-activity": "同步活动流",
  }[kind] || kind;
}

function formatCardKindLabel(kind) {
  return {
    baseline: "基线",
    changes: "变更",
    activity: "活动",
    conflicts: "冲突",
  }[kind] || kind;
}

function formatBridgeConfigSource(source) {
  return {
    env: "环境变量",
    cli: "命令行",
    local_file: "本地配置文件",
    desktop_bridge: "桌面桥接",
    sample: "演示数据",
  }[source] || source;
}

function formatBlockingReason(reason) {
  return {
    unresolved_conflicts: "存在未解决冲突",
    requires_full_pull: "需要先完整拉取",
  }[reason] || reason;
}

function formatManifestStatusLabel(status) {
  return {
    valid: "有效",
    stale: "过期",
  }[status] || status;
}

function formatCardSourceLabel(cardId) {
  return {
    baseline: "同步基线",
    "local-changes": "本地变更",
    activity: "活动流",
    conflicts: "冲突",
    healthy: "健康检查",
    "worker-health": "同步线程",
  }[cardId] || cardId;
}

function formatSyncActionLabel(actionId, fallback = "") {
  return {
    pull: "执行 Pull",
    "show-vault-summary": "查看仓库摘要",
    "submit-detected-commit": "提交本地变更",
    "detect-local-changes": "查看本地变更",
    "sync-activity": "打开活动流",
    "vault-summary": "查看仓库摘要",
  }[actionId] || fallback || actionId;
}

function formatActionSourceLabel(source) {
  if (!source) {
    return "未知来源";
  }
  if (source === "panel") {
    return "顶部面板";
  }
  if (source.startsWith("workspace:")) {
    return "总览工作台";
  }
  if (source.startsWith("card:")) {
    return `同步卡片 / ${formatCardSourceLabel(source.slice(5))}`;
  }
  return source;
}

function formatActionReason(reason) {
  if (!reason) {
    return "无";
  }
  return String(reason)
    .split(",")
    .map((part) => formatBlockingReason(part.trim()))
    .join("，");
}

function formatActivityMessage(message) {
  if (!message) {
    return "";
  }
  if (message === "requires_full_pull") {
    return "需要先完整拉取，再继续当前动作。";
  }
  return message;
}

function formatSessionSourceLabel(source) {
  return {
    sample: "演示会话",
    "desktop-bridge": "桌面桥接",
  }[source] || source;
}

function buildAppSessionState(session) {
  const workspaceShell = validateWorkspaceShell(session.workspaceShell);
  const payloadKind =
    session.meta?.payloadKind ||
    detectPayloadKind(session.syncPayload);
  const workspaceSummary = session.meta?.workspace || {
    sectionCount: Array.isArray(workspaceShell.sections) ? workspaceShell.sections.length : 0,
    noteCount: Object.keys(workspaceShell.notes || {}).length,
  };

  return {
    source: session.source,
    loadedAtMs: session.loadedAtMs,
    bridgeStatus: session.bridgeStatus || null,
    sessionId: session.meta?.sessionId || `${session.source}-${session.loadedAtMs}`,
    payloadKind,
    workspaceSummary,
  };
}

function formatNavViewLabel(view) {
  return {
    overview: "总览",
    graph: "知识图谱",
    repository: "仓库浏览",
    conflicts: "冲突处理",
    settings: "设置",
  }[view] || view;
}

function formatBridgeMode(mode) {
  return {
    "desktop-cli-local": "本地桌面桥接",
    "dev-server-unreachable": "开发桥接不可达",
  }[mode] || mode || "未知模式";
}

function formatSessionEventLabel(type) {
  return {
    app_session_loaded: "应用会话已加载",
    bridge_snapshot_refreshed: "桥接快照已刷新",
    sync_action_selected: "同步动作已选中",
    sync_action_executed: "同步动作已执行",
    quick_capture_created: "快速记录已创建",
    sync_payload_loaded: "同步载荷已更新",
    workspace_contract_loaded: "工作区契约已更新",
    workspace_note_saved: "工作区文档已保存",
    workspace_edit_blocked: "编辑切换已拦截",
    workspace_sync_blocked: "同步执行已拦截",
    workspace_draft_autosaved: "恢复草稿已自动保存",
    workspace_draft_restored: "恢复草稿已恢复",
    workspace_draft_discarded: "恢复草稿已放弃",
    workspace_ai_applied: "AI 建议已写入草稿",
    workspace_followup_created: "跟进笔记已创建",
    workspace_note_moved: "工作区文档已归档",
    workspace_session_restored: "本地工作区已恢复",
  }[type] || type;
}

function pushSessionHistory(entry) {
  state.sessionHistory = [
    {
      id: `${entry.type}-${Date.now()}-${state.sessionHistory.length}`,
      atMs: Date.now(),
      level: resolveTone(entry.level || "info"),
      ...entry,
    },
    ...state.sessionHistory,
  ].slice(0, 12);
}

function createSignal(level, label) {
  return {
    level: resolveTone(level),
    label,
  };
}

function getCurrentWorkspaceShell() {
  return state.workspaceShell || WORKSPACE_SAMPLE;
}

function buildGraphSnapshot() {
  const workspaceShell = getCurrentWorkspaceShell();
  const notes = collectWorkspaceNotes().map((entry) => ({
    id: entry.id,
    title: entry.title,
    path: entry.path,
    sectionId: entry.sectionId,
    sectionLabel: entry.sectionLabel,
    statusTone: entry.note?.statusTone || "info",
    statusLabel: entry.note?.statusLabel || entry.status,
    tags: Array.isArray(entry.note?.tags) ? entry.note.tags : [],
    entities: Array.isArray(entry.note?.ai?.relatedEntities) ? entry.note.ai.relatedEntities : [],
  }));
  const entityUsage = new Map();

  for (const note of notes) {
    for (const entity of [...note.tags, ...note.entities]) {
      const key = String(entity || "").trim();
      if (!key) {
        continue;
      }
      if (!entityUsage.has(key)) {
        entityUsage.set(key, {
          entity: key,
          count: 0,
          notes: new Set(),
        });
      }
      const bucket = entityUsage.get(key);
      bucket.count += 1;
      bucket.notes.add(note.title);
    }
  }

  const topEntities = [...entityUsage.values()]
    .sort((left, right) => right.notes.size - left.notes.size || right.count - left.count)
    .slice(0, 6)
    .map((item) => ({
      entity: item.entity,
      noteCount: item.notes.size,
      notes: [...item.notes].slice(0, 3),
    }));

  const edges = [];
  for (let index = 0; index < notes.length; index += 1) {
    for (let inner = index + 1; inner < notes.length; inner += 1) {
      const left = notes[index];
      const right = notes[inner];
      const shared = [...new Set([...left.tags, ...left.entities])]
        .filter((token) => [...right.tags, ...right.entities].includes(token))
        .slice(0, 3);
      if (!shared.length) {
        continue;
      }
      edges.push({
        leftId: left.id,
        left: left.title,
        rightId: right.id,
        right: right.title,
        shared,
        sharedCount: shared.length,
      });
    }
  }

  const focusNote = notes.find((note) => note.id === state.selectedWorkspaceNoteId) || notes[0] || null;
  const focusTokens = focusNote ? [...new Set([...focusNote.tags, ...focusNote.entities])] : [];
  const focusEntities = focusTokens
    .map((entity) => {
      const usage = entityUsage.get(entity);
      return {
        entity,
        noteCount: usage?.notes.size || 1,
        relatedNotes: [...(usage?.notes || [])].filter((title) => title !== focusNote.title).slice(0, 3),
      };
    })
    .sort((left, right) => right.noteCount - left.noteCount || left.entity.localeCompare(right.entity, "zh-CN"))
    .slice(0, 8);

  const relatedNotes = focusNote
    ? notes
        .filter((note) => note.id !== focusNote.id)
        .map((note) => {
          const shared = [...new Set([...note.tags, ...note.entities])].filter((token) => focusTokens.includes(token));
          return {
            id: note.id,
            title: note.title,
            path: note.path,
            sectionLabel: note.sectionLabel,
            statusTone: note.statusTone,
            statusLabel: note.statusLabel,
            shared,
          };
        })
        .filter((item) => item.shared.length)
        .sort((left, right) => right.shared.length - left.shared.length || left.title.localeCompare(right.title, "zh-CN"))
        .slice(0, 6)
    : [];

  return {
    noteCount: notes.length,
    entityCount: entityUsage.size,
    topEntities,
    topEdges: edges
      .sort((left, right) => right.sharedCount - left.sharedCount || left.left.localeCompare(right.left, "zh-CN"))
      .slice(0, 6),
    focusNote,
    focusEntities,
    relatedNotes,
    connectionCount: edges.length,
  };
}

function buildRepositorySnapshot() {
  const workspaceShell = getCurrentWorkspaceShell();
  const visibleIds = new Set(getVisibleWorkspaceNoteIds(workspaceShell));
  const allEntries = collectWorkspaceNotes().sort((left, right) => right.rank - left.rank);
  const visibleEntries = allEntries.filter((entry) => visibleIds.has(entry.id));
  const selectedEntry = visibleEntries.find((entry) => entry.id === state.selectedWorkspaceNoteId) || visibleEntries[0] || null;
  const draftEntries = visibleEntries.filter((entry) => {
    const draft = getEditorDraftByNoteId(entry.id);
    return (
      entry.note.statusLabel?.includes("草稿") ||
      entry.note.tags?.includes("draft") ||
      (draft && isEditorDraftDirty(entry.note, draft))
    );
  });
  const riskEntries = visibleEntries.filter(
    (entry) => entry.note.statusTone === "warning" || entry.note.statusTone === "danger",
  );
  const sectionCards = (workspaceShell.sections || []).map((section) => {
    const sectionEntries = allEntries.filter((entry) => entry.sectionId === section.id);
    const sectionVisibleEntries = visibleEntries.filter((entry) => entry.sectionId === section.id);
    const stats = summarizeSectionActivity(section, workspaceShell);
    return {
      id: section.id,
      label: section.label,
      totalCount: sectionEntries.length,
      visibleCount: sectionVisibleEntries.length,
      draftCount: stats.draftCount,
      riskCount: stats.riskCount,
      leadEntry: sectionVisibleEntries[0] || sectionEntries[0] || null,
    };
  });

  return {
    selectedEntry,
    visibleEntries,
    draftEntries,
    riskEntries,
    sectionCards,
  };
}

function buildConflictSnapshot() {
  const summary = state.syncCenter?.summary || null;
  const conflicts = summary?.conflicts || {};
  const activityRecords = Array.isArray(state.activityFeed?.records)
    ? state.activityFeed.records
    : Array.isArray(state.syncCenter?.recent_activity?.records)
      ? state.syncCenter.recent_activity.records
      : [];
  const blockingReasons = Array.isArray(summary?.commit_gate?.blocking_reasons)
    ? summary.commit_gate.blocking_reasons.map(formatBlockingReason)
    : [];
  const rawBlockingReasons = Array.isArray(summary?.commit_gate?.blocking_reasons)
    ? summary.commit_gate.blocking_reasons
    : [];
  const lastFailure = [...activityRecords].reverse().find((record) => record.status === "failed") || null;
  const workerHealth = summary?.worker_health || null;
  const impactedNotes = collectWorkspaceNotes()
    .map((entry) => {
      const syncContext = deriveWorkspaceSyncContext(entry.note);
      const recommendation = findRecommendedSyncActionForNote(entry.note);
      const watchReasons = Array.isArray(entry.note?.syncContext?.watchBlockingReasons)
        ? entry.note.syncContext.watchBlockingReasons
        : [];
      const watchActionIds = Array.isArray(entry.note?.syncContext?.watchActionIds)
        ? entry.note.syncContext.watchActionIds
        : [];
      const reasonHits = rawBlockingReasons.filter((reason) => watchReasons.includes(reason)).map(formatBlockingReason);
      const actionHit =
        lastFailure && watchActionIds.includes(lastFailure.action_id)
          ? `${lastFailure.action_id} · ${formatStatusLabel(lastFailure.status)}`
          : null;
      const riskSignals = syncContext.signals
        .filter((signal) => signal.level === "danger" || signal.level === "warning")
        .map((signal) => signal.label)
        .slice(0, 3);
      return {
        ...entry,
        recommendation,
        syncContext,
        reasonHits,
        actionHit,
        riskSignals,
      };
    })
    .filter(
      (entry) =>
        entry.reasonHits.length ||
        entry.actionHit ||
        entry.note.statusTone === "warning" ||
        entry.note.statusTone === "danger" ||
        entry.riskSignals.length,
    )
    .sort(
      (left, right) =>
        right.reasonHits.length - left.reasonHits.length ||
        right.riskSignals.length - left.riskSignals.length ||
        right.rank - left.rank,
    )
    .slice(0, 6);
  const recoverySteps = [];
  if (summary?.commit_gate?.requires_full_pull) {
    recoverySteps.push({
      label: "先执行 Pull 重建同步基线",
      detail: "当前提交门禁要求先完整拉取，否则本地变更和远端基线无法对齐。",
    });
  }
  if (summary?.commit_gate?.has_active_sync_apply_journal) {
    recoverySteps.push({
      label: "优先完成 sync apply journal 恢复",
      detail: "说明上次远端应用流程未完成，应该先恢复 journal，再继续新的同步动作。",
    });
  }
  if (summary?.commit_gate?.has_active_commit_journal) {
    recoverySteps.push({
      label: "检查 commit journal 是否仍在占用",
      detail: "如果本地仍有未完成提交流程，需要先恢复或清理后再进入下一次提交。",
    });
  }
  if (conflicts.actual_has_unresolved_conflicts || (conflicts.conflict_copies?.length || 0) || (conflicts.conflict_orphans?.length || 0)) {
    recoverySteps.push({
      label: "逐个处理本地冲突工件",
      detail: "确认冲突副本、孤立冲突文件和原文档去向，再决定保留、整理还是放弃。",
    });
  }
  if (lastFailure) {
    recoverySteps.push({
      label: `回看最近失败动作：${lastFailure.action_id}`,
      detail: `${formatActionSourceLabel(lastFailure.source)} · ${lastFailure.message || formatStatusLabel(lastFailure.status)}`,
    });
  }
  if (!recoverySteps.length) {
    recoverySteps.push({
      label: "当前没有显式冲突工件",
      detail: "可以重点检查最近活动、工作区草稿和下一步同步动作是否仍然一致。",
    });
  }
  return {
    blockingReasons,
    rawBlockingReasons,
    copies: Array.isArray(conflicts.conflict_copies) ? conflicts.conflict_copies : [],
    orphans: Array.isArray(conflicts.conflict_orphans) ? conflicts.conflict_orphans : [],
    canSubmit: Boolean(summary?.commit_gate?.can_submit_commit),
    workerHealth,
    lastFailure,
    impactedNotes,
    recoverySteps,
    hasActiveCommitJournal: Boolean(summary?.commit_gate?.has_active_commit_journal),
    hasActiveSyncApplyJournal: Boolean(summary?.commit_gate?.has_active_sync_apply_journal),
    requiresFullPull: Boolean(summary?.commit_gate?.requires_full_pull),
    activityRecords,
  };
}

function buildSettingsSnapshot() {
  const status = state.bridgeStatus;
  const workspaceShell = getCurrentWorkspaceShell();
  const selectedNote = getSelectedWorkspaceNote();
  return {
    appSession: state.appSession,
    bridgeAvailable: Boolean(status?.available),
    bridgeMode: formatBridgeMode(status?.mode),
    bridgeSource: status?.config?.configSource ? formatBridgeConfigSource(status.config.configSource) : "未配置",
    vaultRoot: status?.config?.vaultRoot || "未连接",
    vaultId: status?.config?.vaultId || "未连接",
    missing: Array.isArray(status?.missing) ? status.missing : [],
    diagnostics: Array.isArray(status?.diagnostics) ? status.diagnostics : [],
    selectedNote,
    visibleNoteCount: getVisibleWorkspaceNoteIds(workspaceShell).length,
    totalNoteCount: countWorkspaceNotes(workspaceShell),
    dirtyDraftCount: collectDirtyEditorDrafts().length,
    recoveryCount: state.recoveryDrafts.length,
    lastExecution: buildLastExecutionSummary(),
    localUiSettings: state.localUiSettings,
    expertMode: Boolean(state.localUiSettings.expertMode),
  };
}

function formatAiRawCleanupPolicy(policy) {
  if (policy === "manual") {
    return "仅手动清理";
  }
  if (policy === "prune-oldest") {
    return "超限后优先清理最旧内容";
  }
  return "超限时先提醒";
}

function collectConflictActions() {
  if (!state.syncCenter) {
    return [];
  }

  const actions = [];
  const seen = new Set();
  const pushAction = (action, source) => {
    if (!action?.action_id || seen.has(action.action_id)) {
      return;
    }
    seen.add(action.action_id);
    actions.push({ action, source });
  };

  pushAction(state.syncCenter.panel?.primary_action, "panel.primary_action");
  for (const action of state.syncCenter.panel?.secondary_actions || []) {
    pushAction(action, "panel.secondary_actions");
  }
  for (const card of state.syncCenter.cards || []) {
    if (card.kind !== "conflicts" && card.kind !== "activity" && !card.card_id.includes("conflict")) {
      continue;
    }
    for (const action of card.actions || []) {
      pushAction(action, `card:${card.card_id}`);
    }
  }

  return actions;
}

function renderNavigation() {
  for (const button of elements.navButtons) {
    button.classList.toggle("is-active", button.dataset.navView === state.activeNavView);
  }
}

function renderViewModeCard() {
  renderNavigation();
  const dirtyDraftCount = collectDirtyEditorDrafts().length;
  const recoveryCount = state.recoveryDrafts.length;

  if (state.activeNavView === "overview") {
    elements.viewModeCard.hidden = false;
    elements.viewModeCard.innerHTML = `
      <div class="panel-topline">
        <span class="level-pill tone-info">总览</span>
        <span class="mini-pill tone-info">未保存草稿：${escapeHtml(dirtyDraftCount)}</span>
        <span class="mini-pill tone-${recoveryCount ? "warning" : "success"}">恢复队列：${escapeHtml(recoveryCount)}</span>
        <span class="mini-pill tone-info">同步详情：冲突处理视图</span>
      </div>
      <h2 class="panel-headline">工作台总览</h2>
      <p class="summary-copy">这里汇总当前会话的编辑、收件箱、同步前准备以及异常恢复入口；详细同步卡片与活动流收敛到“冲突处理”视图，首页只保留高频动作。</p>
      ${
        recoveryCount
          ? `
            <div class="detail-row detail-row-block">
              <strong>检测到上次未完成会话留下的恢复草稿</strong>
              <span>根据本地恢复区数据，当前有 ${escapeHtml(recoveryCount)} 份草稿可恢复。你可以先恢复全部，再继续当前工作流。</span>
            </div>
            <div class="detail-actions">
              <button class="solid detail-inline-button" data-view-mode-command="restore-all-recovery" type="button">恢复全部草稿</button>
              <button class="ghost detail-inline-button" data-view-mode-command="discard-all-recovery" type="button">放弃这些草稿</button>
            </div>
          `
          : `
            <div class="detail-row">
              <span>本地恢复区</span>
              <strong>当前没有待恢复草稿</strong>
            </div>
          `
      }
    `;
    for (const button of elements.viewModeCard.querySelectorAll("[data-view-mode-command]")) {
      button.addEventListener("click", () => {
        if (button.dataset.viewModeCommand === "restore-all-recovery") {
          restoreAllRecoveryDrafts();
          return;
        }
        if (button.dataset.viewModeCommand === "discard-all-recovery") {
          discardAllRecoveryDrafts();
        }
      });
    }
    return;
  }

  const summary = state.syncCenter?.summary || null;
  const conflictCount = summary
    ? (summary.conflicts?.conflict_copies?.length || 0) + (summary.conflicts?.conflict_orphans?.length || 0)
    : 0;
  const workspaceShell = getCurrentWorkspaceShell();
  const visibleCount = getVisibleWorkspaceNoteIds(workspaceShell).length;
  const totalCount = countWorkspaceNotes(workspaceShell);
  const graphSnapshot = state.activeNavView === "graph" ? buildGraphSnapshot() : null;
  const viewConfigs = {
    repository: {
      tone: "info",
      title: "仓库浏览视图",
      detail: "这里围绕工作区目录、处理队列和当前焦点文档展开，适合连续整理内容、切换分区和进入编辑。",
      pills: [`当前可见：${visibleCount}/${totalCount}`, `搜索：${state.searchQuery ? `“${state.searchQuery}”` : "未启用"}`],
    },
    conflicts: {
      tone: "warning",
      title: "冲突处理视图",
      detail: "当前优先显示同步冲突与相关活动，便于在进入下一次提交前先消化阻塞项。",
      pills: [`冲突工件：${conflictCount}`, `阻塞项：${summary?.commit_gate?.blocking_reasons?.length || 0}`],
    },
    graph: {
      tone: "info",
      title: "知识图谱视图",
      detail: "这里收敛当前工作区里的高频实体、关联文档和跨文档线索，可直接反推到搜索、定位或编辑动作。",
      pills: [`实体：${graphSnapshot?.entityCount || 0}`, `连接：${graphSnapshot?.connectionCount || 0}`],
    },
    settings: {
      tone: "info",
      title: "本地设置视图",
      detail: "这里集中管理本地桥接、工作区来源和调试入口，把低频接入动作与主工作台隔离开。",
      pills: [`工作区：${state.workspaceSourceLabel}`, `同步：${state.sourceLabel}`],
    },
  };
  const config = viewConfigs[state.activeNavView];

  elements.viewModeCard.hidden = false;
  elements.viewModeCard.innerHTML = `
    <div class="panel-topline">
      <span class="level-pill tone-${config.tone}">${formatNavViewLabel(state.activeNavView)}</span>
      ${config.pills.map((pill) => `<span class="mini-pill tone-info">${escapeHtml(pill)}</span>`).join("")}
    </div>
    <h2 class="panel-headline">${escapeHtml(config.title)}</h2>
    <p class="summary-copy">${escapeHtml(config.detail)}</p>
  `;
}

function renderViewDetailGrid() {
  if (state.activeNavView === "overview") {
    const notes = collectWorkspaceNotes().sort((left, right) => right.rank - left.rank);
    const inboxNotes = notes.filter((entry) => entry.sectionId === "inbox").slice(0, 4);
    const draftNotes = notes
      .filter((entry) => entry.note.statusLabel?.includes("草稿") || entry.note.tags?.includes("draft"))
      .slice(0, 4);
    const recoveryDrafts = state.recoveryDrafts.slice(0, 4);
    const recentNotes = notes.slice(0, 4);
    const actionableDraft = draftNotes.find((entry) => entry.note.statusLabel?.includes("更新")) || draftNotes[0] || null;
    const recommendedDraftAction = actionableDraft ? findRecommendedSyncActionForNote(actionableDraft.note) : null;
    const summary = state.syncCenter?.summary || null;
    const recentActivity = state.activityFeed?.records || state.syncCenter?.recent_activity?.records || [];
    const latestActivity = recentActivity.length ? recentActivity[recentActivity.length - 1] : null;
    const lastExecution = buildLastExecutionSummary();
    const conflictCount = summary
      ? (summary.conflicts?.conflict_copies?.length || 0) + (summary.conflicts?.conflict_orphans?.length || 0)
      : 0;
    const blockingReasons = Array.isArray(summary?.commit_gate?.blocking_reasons)
      ? summary.commit_gate.blocking_reasons.map(formatBlockingReason)
      : [];
    const recentSessionEntries = state.sessionHistory.slice(0, 4);

    elements.viewDetailGrid.hidden = false;
    elements.viewDetailGrid.innerHTML = `
      <article class="view-detail-card">
        <p class="card-section-label">同步速览</p>
        <h3>当前同步状态</h3>
        <div class="detail-metric-grid">
          <div class="detail-metric">
            <span class="metric-label">本地变更</span>
            <strong>${escapeHtml(summary?.changes?.change_count ?? 0)}</strong>
          </div>
          <div class="detail-metric">
            <span class="metric-label">冲突工件</span>
            <strong>${escapeHtml(conflictCount)}</strong>
          </div>
        </div>
        <div class="view-stack">
          <div class="detail-row detail-row-block">
            <strong>${escapeHtml(state.bridgeStatus?.available ? "桌面桥接已连接" : "桌面桥接未连接")}</strong>
            <span>${escapeHtml(state.bridgeStatus?.available ? "当前可以从工作台直接触发同步动作。" : "仍可整理本地草稿，但暂时无法直接执行桥接动作。")}</span>
          </div>
          <div class="detail-row detail-row-block">
            <strong>${escapeHtml(blockingReasons.length ? `存在 ${blockingReasons.length} 个阻塞项` : "当前无提交阻塞项")}</strong>
            <span>${escapeHtml(blockingReasons.length ? blockingReasons.join("，") : "可以在完成草稿整理后，进入冲突处理视图检查详细卡片。")}</span>
          </div>
          ${
            lastExecution
              ? `
                <div class="detail-row detail-row-block">
                  <strong>${escapeHtml(`最近执行：${lastExecution.actionId}`)}</strong>
                  <span>${escapeHtml(`${lastExecution.statusLabel} · ${lastExecution.atLabel}`)}</span>
                </div>
              `
              : ""
          }
        </div>
        <div class="detail-actions">
          <button class="solid detail-inline-button" data-overview-nav="conflicts" type="button">查看同步详情</button>
          <button class="ghost detail-inline-button" data-overview-command="refresh-session" type="button">刷新实时会话</button>
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">最近处理</p>
        <h3>会话节奏</h3>
        <div class="view-stack">
          ${
            recentSessionEntries.length
              ? recentSessionEntries
                  .map(
                    (entry) => `
                      <div class="detail-row detail-row-block">
                        <strong>${escapeHtml(formatSessionEventLabel(entry.type))}</strong>
                        <span>${escapeHtml(entry.detail || "无附加说明")}</span>
                        <span>${escapeHtml(formatDateTime(entry.atMs))}</span>
                      </div>
                    `,
                  )
                  .join("")
              : '<div class="empty-state"><p>当前还没有前端会话记录。</p></div>'
          }
          ${
            latestActivity
              ? `
                <div class="detail-row detail-row-block">
                  <strong>${escapeHtml(`最新同步活动：${latestActivity.action_id}`)}</strong>
                  <span>${escapeHtml(`${formatStatusLabel(latestActivity.status)} · ${formatDateTime(latestActivity.occurred_at_ms)}`)}</span>
                </div>
              `
              : ""
          }
        </div>
        <div class="detail-actions">
          <button class="ghost detail-inline-button" data-overview-nav="conflicts" type="button">打开活动流</button>
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">异常恢复</p>
        <h3>草稿恢复</h3>
        <div class="view-stack">
          ${
            recoveryDrafts.length
              ? recoveryDrafts
                  .map(
                    (entry) => `
                      <article class="detail-row detail-row-block overview-row">
                        <div class="overview-row-main">
                          <div class="overview-row-copy">
                            <strong>${escapeHtml(entry.title || "未命名草稿")}</strong>
                            <span>${escapeHtml([entry.sectionLabel || "未知分区", entry.path || "未记录路径"].join(" · "))}</span>
                            <span>${escapeHtml(entry.noteMissing ? "原文档当前未加载，将恢复到收件箱。" : "检测到上次会话遗留的未保存内容。")}</span>
                          </div>
                          <div class="overview-row-meta">
                            <span class="mini-pill tone-warning">待恢复</span>
                            <span class="mini-pill tone-info">${escapeHtml(formatDateTime(entry.updatedAtMs))}</span>
                          </div>
                        </div>
                        <div class="detail-actions overview-row-actions">
                          <button class="solid detail-inline-button" data-recovery-restore="${escapeHtml(entry.noteId)}" type="button">恢复</button>
                          <button class="ghost detail-inline-button" data-recovery-discard="${escapeHtml(entry.noteId)}" type="button">放弃</button>
                        </div>
                      </article>
                    `,
                  )
                  .join("")
              : '<div class="empty-state"><p>当前没有等待恢复的本地草稿。</p></div>'
          }
        </div>
        <div class="detail-actions">
          ${
            state.recoveryDrafts.length
              ? `<button class="solid detail-inline-button" data-overview-command="restore-all-recovery" type="button">恢复全部</button>`
              : ""
          }
          ${
            state.recoveryDrafts.length
              ? `<button class="ghost detail-inline-button" data-overview-command="discard-all-recovery" type="button">清空恢复区</button>`
              : ""
          }
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">工作台入口</p>
        <h3>最近编辑</h3>
        <div class="view-stack">
          ${
            recentNotes.length
              ? recentNotes
                  .map(
                    (entry) =>
                      buildOverviewNoteRow(entry, {
                        pills: [
                          entry.id === state.selectedWorkspaceNoteId ? "当前查看" : null,
                          entry.note.lastSaved || entry.status,
                        ],
                        buttons: [
                          `<button class="ghost detail-inline-button" data-note-open="${escapeHtml(entry.id)}" type="button">打开</button>`,
                        ],
                      }),
                  )
                  .join("")
              : '<div class="empty-state"><p>当前还没有可展示的最近编辑文档。</p></div>'
          }
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">收件箱</p>
        <h3>待整理记录</h3>
        <div class="view-stack">
          ${
            inboxNotes.length
              ? inboxNotes
                  .map(
                    (entry) =>
                      buildOverviewNoteRow(entry, {
                        summary: "建议先补充上下文，再决定是否进入同步周期。",
                        pills: [entry.status],
                        buttons: [
                          `<button class="ghost detail-inline-button" data-note-open="${escapeHtml(entry.id)}" type="button">打开</button>`,
                          `<button class="solid detail-inline-button" data-note-edit="${escapeHtml(entry.id)}" type="button">继续编辑</button>`,
                        ],
                      }),
                  )
                  .join("")
              : '<div class="empty-state"><p>收件箱目前为空，可以直接点顶部“快速记录”。</p></div>'
          }
        </div>
        <div class="detail-actions">
          <button class="solid detail-inline-button" data-overview-command="quick-capture" type="button">新建快速记录</button>
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">同步前准备</p>
        <h3>待同步草稿</h3>
        <div class="view-stack">
          ${
            draftNotes.length
              ? draftNotes
                  .map(
                    (entry) => {
                      const recommendation = findRecommendedSyncActionForNote(entry.note);
                      return buildOverviewNoteRow(entry, {
                        summary: recommendation
                          ? `推荐动作：${recommendation.action.label}`
                          : "当前没有匹配到可继续的同步动作。",
                        pills: [entry.note.statusLabel || entry.status, recommendation?.action?.command || null],
                        buttons: [
                          `<button class="ghost detail-inline-button" data-note-open="${escapeHtml(entry.id)}" type="button">打开</button>`,
                          recommendation
                            ? `<button class="ghost detail-inline-button" data-note-select-action="${escapeHtml(entry.id)}" type="button">选中动作</button>`
                            : "",
                        ],
                      });
                    },
                  )
                  .join("")
              : '<div class="empty-state"><p>当前没有处于草稿态的文档。</p></div>'
          }
        </div>
        <div class="detail-actions">
          ${
            actionableDraft
              ? `<button class="ghost detail-inline-button" data-note-open="${escapeHtml(actionableDraft.id)}" type="button">打开当前草稿</button>`
              : ""
          }
          ${
            actionableDraft && recommendedDraftAction
              ? `<button class="solid detail-inline-button" data-note-execute-action="${escapeHtml(actionableDraft.id)}" type="button">执行「${escapeHtml(recommendedDraftAction.action.label)}」</button>`
              : ""
          }
          <button class="ghost detail-inline-button" data-overview-command="refresh-session" type="button">刷新实时会话</button>
        </div>
      </article>
    `;

    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-open]")) {
      button.addEventListener("click", () => {
        state.selectedWorkspaceNoteId = button.dataset.noteOpen || state.selectedWorkspaceNoteId;
        state.activeNavView = "overview";
        render();
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-edit]")) {
      button.addEventListener("click", () => {
        focusWorkspaceNoteForEdit(button.dataset.noteEdit || state.selectedWorkspaceNoteId);
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-select-action]")) {
      button.addEventListener("click", () => {
        selectRecommendedSyncActionForNote(button.dataset.noteSelectAction || state.selectedWorkspaceNoteId);
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-execute-action]")) {
      button.addEventListener("click", async () => {
        await executeRecommendedSyncActionForNote(button.dataset.noteExecuteAction || state.selectedWorkspaceNoteId);
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-recovery-restore]")) {
      button.addEventListener("click", () => {
        restoreRecoveryDraft(button.dataset.recoveryRestore || "");
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-recovery-discard]")) {
      button.addEventListener("click", () => {
        discardRecoveryDraft(button.dataset.recoveryDiscard || "");
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-overview-nav]")) {
      button.addEventListener("click", () => {
        state.activeNavView = button.dataset.overviewNav || "overview";
        render();
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-overview-command]")) {
      button.addEventListener("click", async () => {
        if (button.dataset.overviewCommand === "quick-capture") {
          createQuickCaptureNote();
          return;
        }
        if (button.dataset.overviewCommand === "restore-all-recovery") {
          restoreAllRecoveryDrafts();
          return;
        }
        if (button.dataset.overviewCommand === "discard-all-recovery") {
          discardAllRecoveryDrafts();
          return;
        }
        if (button.dataset.overviewCommand === "refresh-session") {
          try {
            await refreshFullAppSession();
          } catch (error) {
            state.lastBridgeError = normalizeBridgeError(error);
            elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
            renderBridgeError(state.lastBridgeError);
          }
        }
      });
    }
    return;
  }

  if (state.activeNavView === "repository") {
    const repository = buildRepositorySnapshot();
    const dirtyDraftCount = collectDirtyEditorDrafts().length;
    const selectedEntry = repository.selectedEntry;
    const selectedRecommendation = selectedEntry ? findRecommendedSyncActionForNote(selectedEntry.note) : null;
    const repositoryMoveTargets = (getCurrentWorkspaceShell().sections || []).filter(
      (section) => section.id !== selectedEntry?.sectionId && ["inbox", "notes", "ai-wiki"].includes(section.id),
    );
    elements.viewDetailGrid.hidden = false;
    elements.viewDetailGrid.innerHTML = `
      <article class="view-detail-card">
        <p class="card-section-label">仓库结构</p>
        <h3>分区入口</h3>
        <div class="view-stack">
          ${repository.sectionCards
            .map(
              (section) => `
                <article class="detail-row detail-row-block overview-row">
                  <div class="overview-row-main">
                    <div class="overview-row-copy">
                      <strong>${escapeHtml(section.label)}</strong>
                      <span>${escapeHtml(`当前可见 ${section.visibleCount} / 总计 ${section.totalCount} 篇`)}</span>
                      <span>${escapeHtml(section.leadEntry ? `当前焦点：${section.leadEntry.title}` : "当前分区还没有可展示文档。")}</span>
                    </div>
                    <div class="overview-row-meta">
                      <span class="mini-pill tone-info">${escapeHtml(section.totalCount)} 篇</span>
                      ${
                        section.draftCount
                          ? `<span class="mini-pill tone-warning">${escapeHtml(section.draftCount)} 草稿</span>`
                          : ""
                      }
                      ${
                        section.riskCount
                          ? `<span class="mini-pill tone-danger">${escapeHtml(section.riskCount)} 风险</span>`
                          : ""
                      }
                    </div>
                  </div>
                  <div class="detail-actions overview-row-actions">
                    <button class="ghost detail-inline-button" data-section-focus="${escapeHtml(section.label)}" type="button">只看这一分区</button>
                    ${
                      section.leadEntry
                        ? `<button class="solid detail-inline-button" data-note-open="${escapeHtml(section.leadEntry.id)}" type="button">打开焦点文档</button>`
                        : ""
                    }
                  </div>
                </article>
              `,
            )
            .join("")}
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">当前焦点</p>
        <h3>仓库处理队列</h3>
        <div class="view-stack">
          <div class="detail-row detail-row-block">
            <strong>${escapeHtml(selectedEntry ? selectedEntry.title : "当前没有命中文档")}</strong>
            <span>${escapeHtml(selectedEntry ? `${selectedEntry.sectionLabel} · ${selectedEntry.path}` : "请调整搜索词或切换分区。")}</span>
            <span>${escapeHtml(selectedEntry ? selectedEntry.note.statusLabel : "当前搜索结果为空。")}</span>
          </div>
          <div class="detail-metric-grid">
            <div class="detail-metric">
              <span class="metric-label">可见文档</span>
              <strong>${escapeHtml(repository.visibleEntries.length)}</strong>
            </div>
            <div class="detail-metric">
              <span class="metric-label">未保存草稿</span>
              <strong>${escapeHtml(dirtyDraftCount)}</strong>
            </div>
          </div>
          <div class="detail-row">
            <span>工作区来源</span>
            <strong>${escapeHtml(state.workspaceSourceLabel)}</strong>
          </div>
          <div class="detail-row">
            <span>同步来源</span>
            <strong>${escapeHtml(state.sourceLabel)}</strong>
          </div>
        </div>
        <div class="detail-actions">
          ${
            selectedEntry
              ? `<button class="solid detail-inline-button" data-note-edit="${escapeHtml(selectedEntry.id)}" type="button">继续编辑当前文档</button>`
              : ""
          }
          ${
            selectedRecommendation
              ? `<button class="ghost detail-inline-button" data-note-select-action="${escapeHtml(selectedEntry.id)}" type="button">选中推荐动作</button>`
              : ""
          }
          ${
            selectedEntry
              ? repositoryMoveTargets
                  .map(
                    (section) =>
                      `<button class="ghost detail-inline-button" data-note-move="${escapeHtml(section.id)}" type="button">整理到 ${escapeHtml(section.label)}</button>`,
                  )
                  .join("")
              : ""
          }
          <button class="ghost detail-inline-button" data-view-command="clear-search" type="button">清空搜索</button>
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">待处理草稿</p>
        <h3>进入同步前</h3>
        <div class="view-stack">
          ${
            repository.draftEntries.length
              ? repository.draftEntries
                  .slice(0, 4)
                  .map(
                    (entry) => {
                      const draft = getEditorDraftByNoteId(entry.id);
                      const recommendation = findRecommendedSyncActionForNote(entry.note);
                      return buildOverviewNoteRow(entry, {
                        summary: draft && isEditorDraftDirty(entry.note, draft)
                          ? "本地草稿仍有未保存修改，建议先保存。"
                          : recommendation
                            ? `推荐动作：${recommendation.action.label}`
                            : "当前还没有匹配到下一步同步动作。",
                        pills: [
                          draft && isEditorDraftDirty(entry.note, draft) ? "未保存修改" : null,
                          recommendation?.action?.command || null,
                        ],
                        buttons: [
                          `<button class="ghost detail-inline-button" data-note-open="${escapeHtml(entry.id)}" type="button">打开</button>`,
                          `<button class="solid detail-inline-button" data-note-edit="${escapeHtml(entry.id)}" type="button">编辑</button>`,
                        ],
                      });
                    },
                  )
                  .join("")
              : '<div class="empty-state"><p>当前没有挂起的草稿队列，可以继续整理仓库内容。</p></div>'
          }
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">风险与阻塞</p>
        <h3>需要优先关注</h3>
        <div class="view-stack">
          ${
            repository.riskEntries.length
              ? repository.riskEntries
                  .slice(0, 4)
                  .map(
                    (entry) =>
                      {
                        const recommendation = findRecommendedSyncActionForNote(entry.note);
                        return buildOverviewNoteRow(entry, {
                          summary: `${entry.note.lastSaved || entry.status} · ${entry.note.path || entry.path}`,
                          pills: [entry.note.statusLabel || entry.status, recommendation?.action?.command || null],
                          buttons: [
                            `<button class="ghost detail-inline-button" data-note-open="${escapeHtml(entry.id)}" type="button">打开</button>`,
                            recommendation
                              ? `<button class="ghost detail-inline-button" data-note-execute-action="${escapeHtml(entry.id)}" type="button">执行推荐动作</button>`
                              : "",
                          ],
                        });
                      },
                  )
                  .join("")
              : '<div class="empty-state"><p>当前可见文档里没有高风险条目。</p></div>'
          }
        </div>
      </article>
    `;
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-section-focus]")) {
      button.addEventListener("click", () => {
        applySearchQuery(button.dataset.sectionFocus || "");
        render();
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-open]")) {
      button.addEventListener("click", () => {
        state.selectedWorkspaceNoteId = button.dataset.noteOpen || state.selectedWorkspaceNoteId;
        render();
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-edit]")) {
      button.addEventListener("click", () => {
        focusWorkspaceNoteForEdit(button.dataset.noteEdit || state.selectedWorkspaceNoteId);
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-select-action]")) {
      button.addEventListener("click", () => {
        selectRecommendedSyncActionForNote(button.dataset.noteSelectAction || state.selectedWorkspaceNoteId);
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-move]")) {
      button.addEventListener("click", () => {
        moveWorkspaceNoteToSection(
          selectedEntry?.id || state.selectedWorkspaceNoteId,
          button.dataset.noteMove || "",
        );
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-execute-action]")) {
      button.addEventListener("click", async () => {
        await executeRecommendedSyncActionForNote(button.dataset.noteExecuteAction || state.selectedWorkspaceNoteId);
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-view-command='clear-search']")) {
      button.addEventListener("click", () => {
        resetSearchQuery();
        render();
      });
    }
    return;
  }

  if (state.activeNavView === "graph") {
    const graph = buildGraphSnapshot();
    elements.viewDetailGrid.hidden = false;
    elements.viewDetailGrid.innerHTML = `
      <article class="view-detail-card">
        <p class="card-section-label">当前焦点</p>
        <h3>关联文档</h3>
        <div class="detail-metric-grid">
          <div class="detail-metric">
            <span class="metric-label">文档数</span>
            <strong>${escapeHtml(graph.noteCount)}</strong>
          </div>
          <div class="detail-metric">
            <span class="metric-label">实体数</span>
            <strong>${escapeHtml(graph.entityCount)}</strong>
          </div>
        </div>
        <div class="view-stack">
          ${
            graph.focusNote
              ? `
                <article class="detail-row detail-row-block overview-row">
                  <div class="overview-row-main">
                    <div class="overview-row-copy">
                      <strong>${escapeHtml(graph.focusNote.title)}</strong>
                      <span>${escapeHtml(`${graph.focusNote.sectionLabel} · ${graph.focusNote.path}`)}</span>
                      <span>${escapeHtml(`当前图谱焦点共提取 ${graph.focusEntities.length} 个高频实体线索。`)}</span>
                    </div>
                    <div class="overview-row-meta">
                      <span class="mini-pill tone-${resolveTone(graph.focusNote.statusTone)}">${escapeHtml(graph.focusNote.statusLabel)}</span>
                      <span class="mini-pill tone-info">${escapeHtml(graph.relatedNotes.length)} 篇关联文档</span>
                    </div>
                  </div>
                  <div class="detail-actions overview-row-actions">
                    <button class="ghost detail-inline-button" data-note-open="${escapeHtml(graph.focusNote.id)}" type="button">打开文档</button>
                    <button class="solid detail-inline-button" data-note-edit="${escapeHtml(graph.focusNote.id)}" type="button">进入编辑</button>
                  </div>
                </article>
              `
              : '<div class="empty-state"><p>当前还没有可分析的文档焦点。</p></div>'
          }
          ${
            graph.focusEntities.length
              ? `
                <div class="editor-outline-list">
                  ${graph.focusEntities
                    .map(
                      (entity) => `
                        <button class="ghost detail-inline-button" data-entity-filter="${escapeHtml(entity.entity)}" type="button">
                          ${escapeHtml(`${entity.entity} · ${entity.noteCount} 篇`)}
                        </button>
                      `,
                    )
                    .join("")}
                </div>
              `
              : ""
          }
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">知识连接</p>
        <h3>实体热区</h3>
        <div class="token-grid">
          ${graph.topEntities
            .map(
              (entity) => `
                <button class="token-card entity-filter-button" data-entity-filter="${escapeHtml(entity.entity)}" type="button">
                  <strong>${escapeHtml(entity.entity)}</strong>
                  <span>${escapeHtml(entity.noteCount)} 篇文档引用</span>
                  <p>${escapeHtml(entity.notes.join(" · "))}</p>
                </button>
              `,
            )
            .join("")}
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">关系回流</p>
        <h3>焦点文档带出的关联项</h3>
        <div class="view-stack">
          ${
            graph.relatedNotes.length
              ? graph.relatedNotes
                  .map(
                    (entry) => `
                      <article class="detail-row detail-row-block overview-row">
                        <div class="overview-row-main">
                          <div class="overview-row-copy">
                            <strong>${escapeHtml(entry.title)}</strong>
                            <span>${escapeHtml(`${entry.sectionLabel} · ${entry.path}`)}</span>
                            <span>${escapeHtml(`共享线索：${entry.shared.join(" · ")}`)}</span>
                          </div>
                          <div class="overview-row-meta">
                            <span class="mini-pill tone-${resolveTone(entry.statusTone)}">${escapeHtml(entry.statusLabel)}</span>
                            <span class="mini-pill tone-info">${escapeHtml(entry.shared.length)} 个重叠点</span>
                          </div>
                        </div>
                        <div class="detail-actions overview-row-actions">
                          <button class="ghost detail-inline-button" data-note-open="${escapeHtml(entry.id)}" type="button">打开</button>
                          <button class="solid detail-inline-button" data-note-edit="${escapeHtml(entry.id)}" type="button">编辑</button>
                        </div>
                      </article>
                    `,
                  )
                  .join("")
              : '<div class="empty-state"><p>当前焦点文档还没有命中其他关联文档。</p></div>'
          }
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">关系草图</p>
        <h3>跨文档连接</h3>
        <div class="view-stack">
          ${
            graph.topEdges.length
              ? graph.topEdges
                  .map(
                    (edge) => `
                      <div class="relation-row">
                        <strong>${escapeHtml(edge.left)} ↔ ${escapeHtml(edge.right)}</strong>
                        <span>${escapeHtml(`${edge.sharedCount} 个共享线索`)}</span>
                        <p>${escapeHtml(edge.shared.join(" · "))}</p>
                        <div class="detail-actions">
                          <button class="ghost detail-inline-button" data-note-open="${escapeHtml(edge.leftId)}" type="button">打开左侧</button>
                          <button class="ghost detail-inline-button" data-note-open="${escapeHtml(edge.rightId)}" type="button">打开右侧</button>
                        </div>
                      </div>
                    `,
                  )
                  .join("")
              : '<div class="empty-state"><p>当前数据里还没有足够的共享实体来生成连接。</p></div>'
          }
        </div>
      </article>
    `;
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-open]")) {
      button.addEventListener("click", () => {
        state.selectedWorkspaceNoteId = button.dataset.noteOpen || state.selectedWorkspaceNoteId;
        render();
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-edit]")) {
      button.addEventListener("click", () => {
        focusWorkspaceNoteForEdit(button.dataset.noteEdit || state.selectedWorkspaceNoteId);
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-entity-filter]")) {
      button.addEventListener("click", () => {
        applySearchQuery(button.dataset.entityFilter || "");
        render();
      });
    }
    return;
  }

  if (state.activeNavView === "conflicts") {
    const conflictSnapshot = buildConflictSnapshot();
    const conflictActions = collectConflictActions();
    elements.viewDetailGrid.hidden = false;
    elements.viewDetailGrid.innerHTML = `
      <article class="view-detail-card">
        <p class="card-section-label">处理优先级</p>
        <h3>提交阻塞</h3>
        <div class="detail-metric-grid">
          <div class="detail-metric">
            <span class="metric-label">可提交</span>
            <strong>${conflictSnapshot.canSubmit ? "是" : "否"}</strong>
          </div>
          <div class="detail-metric">
            <span class="metric-label">阻塞项</span>
            <strong>${escapeHtml(conflictSnapshot.blockingReasons.length)}</strong>
          </div>
          <div class="detail-metric">
            <span class="metric-label">同步线程</span>
            <strong>${escapeHtml(formatStatusLabel(conflictSnapshot.workerHealth?.status || "unknown"))}</strong>
          </div>
          <div class="detail-metric">
            <span class="metric-label">最近失败</span>
            <strong>${escapeHtml(conflictSnapshot.lastFailure ? conflictSnapshot.lastFailure.action_id : "无")}</strong>
          </div>
        </div>
        <div class="token-grid">
          ${conflictSnapshot.blockingReasons.length
            ? conflictSnapshot.blockingReasons
                .map((reason) => `<span class="mini-pill tone-danger">${escapeHtml(reason)}</span>`)
                .join("")
            : '<span class="mini-pill tone-success">当前无阻塞原因</span>'}
          ${
            conflictSnapshot.requiresFullPull
              ? '<span class="mini-pill tone-warning">需要先完整拉取</span>'
              : ""
          }
          ${
            conflictSnapshot.hasActiveCommitJournal
              ? '<span class="mini-pill tone-warning">存在 commit journal</span>'
              : ""
          }
          ${
            conflictSnapshot.hasActiveSyncApplyJournal
              ? '<span class="mini-pill tone-warning">存在 sync apply journal</span>'
              : ""
          }
        </div>
        <div class="detail-actions">
          <button class="ghost detail-inline-button" data-conflict-command="refresh-session" type="button">刷新实时会话</button>
          <button class="ghost detail-inline-button" data-conflict-command="refresh-bridge" type="button">刷新桥接状态</button>
          ${
            conflictSnapshot.lastFailure
              ? `<button class="solid detail-inline-button" data-conflict-select-action="${escapeHtml(conflictSnapshot.lastFailure.action_id)}" type="button">选中最近失败动作</button>`
              : ""
          }
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">恢复顺序</p>
        <h3>建议处置步骤</h3>
        <div class="view-stack">
          ${conflictSnapshot.recoverySteps
            .map(
              (step, index) => `
                <div class="detail-row detail-row-block">
                  <strong>${escapeHtml(`${index + 1}. ${step.label}`)}</strong>
                  <span>${escapeHtml(step.detail)}</span>
                </div>
              `,
            )
            .join("")}
        </div>
        <div class="detail-actions" id="conflict-detail-actions"></div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">受影响文档</p>
        <h3>建议优先检查</h3>
        <div class="view-stack">
          ${
            conflictSnapshot.impactedNotes.length
              ? conflictSnapshot.impactedNotes
                  .map(
                    (entry) =>
                      buildOverviewNoteRow(entry, {
                        summary: [
                          entry.reasonHits[0] || null,
                          entry.actionHit ? `最近失败：${entry.actionHit}` : null,
                          entry.syncContext.actions[0] || null,
                        ]
                          .filter(Boolean)
                          .join(" · "),
                        pills: [
                          entry.note.statusLabel || entry.status,
                          ...entry.riskSignals.slice(0, 2),
                        ],
                        buttons: [
                          `<button class="ghost detail-inline-button" data-note-open="${escapeHtml(entry.id)}" type="button">打开</button>`,
                          `<button class="solid detail-inline-button" data-note-edit="${escapeHtml(entry.id)}" type="button">编辑</button>`,
                          entry.recommendation
                            ? `<button class="ghost detail-inline-button" data-note-select-action="${escapeHtml(entry.id)}" type="button">选中推荐动作</button>`
                            : "",
                        ],
                      }),
                  )
                  .join("")
              : '<div class="empty-state"><p>当前没有直接命中的高优先级文档，可先按阻塞项和最近失败动作处理。</p></div>'
          }
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">冲突清单</p>
        <h3>本地工件与最近失败</h3>
        <div class="view-stack">
          ${
            [...conflictSnapshot.copies, ...conflictSnapshot.orphans].length
              ? [...conflictSnapshot.copies, ...conflictSnapshot.orphans]
                  .map(
                    (item) => `
                      <div class="detail-row detail-row-block">
                        <strong>${escapeHtml(item.kind === "conflict_copy" ? "冲突副本" : "孤立冲突文件")}</strong>
                        <span>${escapeHtml(item.path || "未知路径")}</span>
                      </div>
                    `,
                  )
                  .join("")
              : '<div class="detail-row detail-row-block"><strong>当前没有具体冲突文件</strong><span>这说明当前问题更偏向同步基线、动作失败或门禁阻塞，而不是文件级冲突副本。</span></div>'
          }
          ${
            conflictSnapshot.lastFailure
              ? `
                <div class="detail-row detail-row-block">
                  <strong>${escapeHtml(`最近失败动作：${conflictSnapshot.lastFailure.action_id}`)}</strong>
                  <span>${escapeHtml(`${formatDateTime(conflictSnapshot.lastFailure.occurred_at_ms)} · ${formatActionSourceLabel(conflictSnapshot.lastFailure.source)}`)}</span>
                  <span>${escapeHtml(conflictSnapshot.lastFailure.message || formatStatusLabel(conflictSnapshot.lastFailure.status))}</span>
                </div>
              `
              : ""
          }
        </div>
      </article>
    `;
    const conflictActionsHost = elements.viewDetailGrid.querySelector("#conflict-detail-actions");
    for (const { action, source } of conflictActions) {
      conflictActionsHost?.appendChild(createActionChip(action, source));
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-open]")) {
      button.addEventListener("click", () => {
        state.selectedWorkspaceNoteId = button.dataset.noteOpen || state.selectedWorkspaceNoteId;
        state.activeNavView = "repository";
        render();
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-edit]")) {
      button.addEventListener("click", () => {
        focusWorkspaceNoteForEdit(button.dataset.noteEdit || state.selectedWorkspaceNoteId);
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-note-select-action]")) {
      button.addEventListener("click", () => {
        selectRecommendedSyncActionForNote(button.dataset.noteSelectAction || state.selectedWorkspaceNoteId);
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-conflict-select-action]")) {
      button.addEventListener("click", () => {
        const matched = conflictActions.find(({ action }) => action.action_id === button.dataset.conflictSelectAction);
        if (!matched) {
          return;
        }
        setSelectedAction(matched.action, matched.source);
        render();
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-conflict-command]")) {
      button.addEventListener("click", async () => {
        const command = button.dataset.conflictCommand;
        if (command === "refresh-bridge") {
          try {
            await requestBridgeStatus();
            render();
          } catch (error) {
            state.lastBridgeError = normalizeBridgeError(error);
            renderBridgeError(state.lastBridgeError);
            render();
          }
          return;
        }
        if (command === "refresh-session") {
          try {
            await refreshFullAppSession();
          } catch (error) {
            state.lastBridgeError = normalizeBridgeError(error);
            elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
            renderBridgeError(state.lastBridgeError);
          }
        }
      });
    }
    return;
  }

  if (state.activeNavView === "settings") {
    const settings = buildSettingsSnapshot();
    elements.viewDetailGrid.hidden = false;
    elements.viewDetailGrid.innerHTML = `
      <article class="view-detail-card">
        <p class="card-section-label">工作台边界</p>
        <h3>当前模式</h3>
        <div class="detail-metric-grid">
          <div class="detail-metric">
            <span class="metric-label">当前模式</span>
            <strong>${settings.expertMode ? "高级调试" : "普通工作台"}</strong>
          </div>
          <div class="detail-metric">
            <span class="metric-label">高级入口</span>
            <strong>${settings.expertMode ? "已显示" : "已收起"}</strong>
          </div>
        </div>
        <div class="view-stack">
          <div class="detail-row detail-row-block">
            <strong>${settings.expertMode ? "当前保留所有调试入口" : "当前只保留主链操作"}</strong>
            <span>${escapeHtml(settings.expertMode ? "你现在可以直接查看桥接诊断、手动覆盖同步 JSON 和手动粘贴工作区契约。" : "原始 JSON、桥接诊断和手动契约输入已被收起，避免打断普通工作流。")}</span>
          </div>
        </div>
        <div class="detail-actions">
          <button class="solid detail-inline-button" data-settings-command="toggle-expert-mode" type="button">${settings.expertMode ? "切回普通工作台" : "开启高级调试"}</button>
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">本地桥接</p>
        <h3>连接状态</h3>
        <div class="detail-metric-grid">
          <div class="detail-metric">
            <span class="metric-label">可用</span>
            <strong>${settings.bridgeAvailable ? "已连接" : "未连接"}</strong>
          </div>
          <div class="detail-metric">
            <span class="metric-label">模式</span>
            <strong>${escapeHtml(settings.bridgeMode)}</strong>
          </div>
        </div>
        <div class="view-stack">
          <div class="detail-row">
            <span>配置来源</span>
            <strong>${escapeHtml(settings.bridgeSource)}</strong>
          </div>
          <div class="detail-row">
            <span>Vault ID</span>
            <strong>${escapeHtml(settings.vaultId)}</strong>
          </div>
          <div class="detail-row">
            <span>Vault Root</span>
            <strong>${escapeHtml(settings.vaultRoot)}</strong>
          </div>
        </div>
        <div class="token-grid">
          ${
            settings.missing.length
              ? settings.missing
                  .map((item) => `<span class="mini-pill tone-warning">${escapeHtml(item)}</span>`)
                  .join("")
              : '<span class="mini-pill tone-success">桥接配置已齐全</span>'
          }
          ${
            settings.diagnostics.slice(0, 4).length
              ? settings.diagnostics
                  .slice(0, 4)
                  .map((item) => `<span class="mini-pill tone-${resolveTone(item.level)}">${escapeHtml(item.code)}</span>`)
                  .join("")
              : ""
          }
        </div>
        <div class="detail-actions">
          <button class="ghost detail-inline-button" data-view-command="refresh-bridge" type="button">刷新桥接状态</button>
          <button class="solid detail-inline-button" data-view-command="refresh-session" type="button">刷新实时会话</button>
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">当前工作区</p>
        <h3>本地工作台状态</h3>
        <div class="detail-metric-grid">
          <div class="detail-metric">
            <span class="metric-label">可见文档</span>
            <strong>${escapeHtml(`${settings.visibleNoteCount}/${settings.totalNoteCount}`)}</strong>
          </div>
          <div class="detail-metric">
            <span class="metric-label">恢复队列</span>
            <strong>${escapeHtml(settings.recoveryCount)}</strong>
          </div>
        </div>
        <div class="view-stack">
          <div class="detail-row">
            <span>当前焦点</span>
            <strong>${escapeHtml(settings.selectedNote?.title || "当前没有命中文档")}</strong>
          </div>
          <div class="detail-row">
            <span>工作区来源</span>
            <strong>${escapeHtml(state.workspaceSourceLabel)}</strong>
          </div>
          <div class="detail-row">
            <span>同步来源</span>
            <strong>${escapeHtml(state.sourceLabel)}</strong>
          </div>
          <div class="detail-row">
            <span>未保存草稿</span>
            <strong>${escapeHtml(settings.dirtyDraftCount)}</strong>
          </div>
          <div class="detail-row">
            <span>会话 ID</span>
            <strong>${escapeHtml(settings.appSession?.sessionId || "未进入应用会话")}</strong>
          </div>
          <div class="detail-row">
            <span>载荷类型</span>
            <strong>${escapeHtml(settings.appSession ? formatPayloadKindLabel(settings.appSession.payloadKind) : "无")}</strong>
          </div>
          <div class="detail-row">
            <span>加载时间</span>
            <strong>${escapeHtml(settings.appSession ? formatDateTime(settings.appSession.loadedAtMs) : "无")}</strong>
          </div>
          <div class="detail-row">
            <span>工作区摘要</span>
            <strong>${escapeHtml(settings.appSession ? `${settings.appSession.workspaceSummary.sectionCount} 个分区 / ${settings.appSession.workspaceSummary.noteCount} 篇文档` : "无")}</strong>
          </div>
        </div>
        <div class="detail-actions">
          <button class="ghost detail-inline-button" data-settings-nav="overview" type="button">回到总览</button>
          <button class="ghost detail-inline-button" data-settings-nav="repository" type="button">打开仓库浏览</button>
          <button class="ghost detail-inline-button" data-settings-nav="conflicts" type="button">查看冲突处理</button>
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">常用接入</p>
        <h3>本地操作入口</h3>
        <div class="view-stack">
          <div class="detail-row detail-row-block">
            <strong>当前建议</strong>
            <span>${escapeHtml(settings.bridgeAvailable ? "桥接可用，优先刷新实时会话并在工作区中继续推进。" : "桥接暂不可用，可先加载演示会话或整理本地草稿。")}</span>
          </div>
          <div class="detail-row detail-row-block">
            <strong>演示工作区</strong>
            <span>用于快速确认当前工作台、树形结构、编辑区与 AI 面板是否都能联动。</span>
          </div>
        </div>
        <div class="detail-actions">
          <button class="ghost detail-inline-button" data-settings-command="load-sample-session" type="button">加载演示会话</button>
          <button class="ghost detail-inline-button" data-settings-command="load-workspace-sample" type="button">加载演示工作区</button>
          <button class="solid detail-inline-button" data-settings-command="quick-capture" type="button">新建快速记录</button>
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">AI 中间产物</p>
        <h3>`.ai/raw` 本地策略</h3>
        <div class="editor-sync-checklist">
          <div class="editor-sync-item">
            <span>单文件上限</span>
            <strong>${escapeHtml(`${settings.localUiSettings.aiRawFileLimitMb} MB`)}</strong>
          </div>
          <div class="editor-sync-item">
            <span>目录总量上限</span>
            <strong>${escapeHtml(`${settings.localUiSettings.aiRawTotalLimitMb} MB`)}</strong>
          </div>
          <div class="editor-sync-item">
            <span>清理策略</span>
            <strong>${escapeHtml(formatAiRawCleanupPolicy(settings.localUiSettings.aiRawCleanupPolicy))}</strong>
          </div>
        </div>
        <p class="summary-copy">这些设置只保存在当前浏览器本地，用于管理 `.ai/raw` 体积、清理和导出附带行为，不会把 `.ai/raw` 纳入常规同步。</p>
        <div class="editor-draft-panel">
          <label class="editor-field">
            <span class="metric-label">单个抽取文本文件上限（MB）</span>
            <input
              id="settings-ai-raw-file-limit"
              class="editor-title-input"
              type="number"
              min="1"
              max="100"
              step="1"
              value="${escapeHtml(settings.localUiSettings.aiRawFileLimitMb)}"
            />
          </label>
          <label class="editor-field">
            <span class="metric-label">`.ai/raw` 目录总量上限（MB）</span>
            <input
              id="settings-ai-raw-total-limit"
              class="editor-title-input"
              type="number"
              min="50"
              max="5000"
              step="50"
              value="${escapeHtml(settings.localUiSettings.aiRawTotalLimitMb)}"
            />
          </label>
          <label class="editor-field">
            <span class="metric-label">超限时处理方式</span>
            <select id="settings-ai-raw-cleanup-policy" class="editor-title-input">
              <option value="prompt" ${settings.localUiSettings.aiRawCleanupPolicy === "prompt" ? "selected" : ""}>超限时先提醒</option>
              <option value="prune-oldest" ${settings.localUiSettings.aiRawCleanupPolicy === "prune-oldest" ? "selected" : ""}>超限后清理最旧内容</option>
              <option value="manual" ${settings.localUiSettings.aiRawCleanupPolicy === "manual" ? "selected" : ""}>仅手动清理</option>
            </select>
          </label>
          <label class="editor-field">
            <span class="metric-label">完整 Vault 导出时是否附带 `.ai/raw`</span>
            <select id="settings-ai-raw-export-mode" class="editor-title-input">
              <option value="exclude" ${settings.localUiSettings.includeAiRawInExport ? "" : "selected"}>默认不附带</option>
              <option value="include" ${settings.localUiSettings.includeAiRawInExport ? "selected" : ""}>按本机偏好附带</option>
            </select>
          </label>
        </div>
        <div class="detail-actions">
          <button class="solid detail-inline-button" data-settings-command="save-ai-raw-settings" type="button">保存到本机</button>
          <button class="ghost detail-inline-button" data-settings-command="reset-ai-raw-settings" type="button">恢复默认</button>
        </div>
      </article>
      <article class="view-detail-card">
        <p class="card-section-label">恢复与执行</p>
        <h3>最近操作</h3>
        <div class="view-stack">
          ${
            settings.lastExecution
              ? `
                <div class="detail-row detail-row-block">
                  <strong>${escapeHtml(settings.lastExecution.actionId)}</strong>
                  <span>${escapeHtml(`${settings.lastExecution.statusLabel} · ${settings.lastExecution.atLabel}`)}</span>
                  <span>${escapeHtml(settings.lastExecution.commandLine || "通过桌面桥接执行")}</span>
                </div>
              `
              : '<div class="detail-row detail-row-block"><strong>最近还没有桥接执行记录</strong><span>可以先在工作区选中文档，再通过推荐动作进入同步流程。</span></div>'
          }
          <div class="detail-row detail-row-block">
            <strong>${escapeHtml(settings.recoveryCount ? `有 ${settings.recoveryCount} 份本地恢复草稿` : "当前没有待恢复草稿")}</strong>
            <span>${escapeHtml(settings.recoveryCount ? "这些内容仅保留在浏览器本地，可以恢复后再决定是否保存正式草稿。" : "本地恢复区处于干净状态。")}</span>
          </div>
        </div>
        <div class="detail-actions">
          ${
            settings.recoveryCount
              ? `<button class="solid detail-inline-button" data-settings-command="restore-all-recovery" type="button">恢复全部草稿</button>`
              : ""
          }
          ${
            settings.recoveryCount
              ? `<button class="ghost detail-inline-button" data-settings-command="discard-all-recovery" type="button">放弃恢复区</button>`
              : ""
          }
        </div>
      </article>
    `;
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-settings-nav]")) {
      button.addEventListener("click", () => {
        state.activeNavView = button.dataset.settingsNav || "overview";
        render();
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-settings-command]")) {
      button.addEventListener("click", async () => {
        const command = button.dataset.settingsCommand;
        if (command === "load-sample-session") {
          try {
            await loadSampleAppSession();
          } catch (error) {
            renderEmptyDashboard(error instanceof Error ? error.message : String(error));
          }
          return;
        }
        if (command === "load-workspace-sample") {
          try {
            await loadWorkspaceShellFromPath(WORKSPACE_SAMPLE_PATH, "演示工作区");
          } catch (error) {
            elements.workspaceStatus.textContent = error instanceof Error ? error.message : String(error);
          }
          return;
        }
        if (command === "quick-capture") {
          createQuickCaptureNote();
          return;
        }
        if (command === "toggle-expert-mode") {
          persistLocalUiSettings({
            ...state.localUiSettings,
            expertMode: !state.localUiSettings.expertMode,
          });
          elements.workspaceStatus.textContent = state.localUiSettings.expertMode
            ? "已开启高级调试模式。"
            : "已切回普通工作台模式。";
          render();
          return;
        }
        if (command === "save-ai-raw-settings") {
          const fileLimitInput = elements.viewDetailGrid.querySelector("#settings-ai-raw-file-limit");
          const totalLimitInput = elements.viewDetailGrid.querySelector("#settings-ai-raw-total-limit");
          const cleanupPolicyInput = elements.viewDetailGrid.querySelector("#settings-ai-raw-cleanup-policy");
          const exportModeInput = elements.viewDetailGrid.querySelector("#settings-ai-raw-export-mode");
          const didPersist = persistLocalUiSettings({
            aiRawFileLimitMb: Number(fileLimitInput?.value || DEFAULT_LOCAL_UI_SETTINGS.aiRawFileLimitMb),
            aiRawTotalLimitMb: Number(totalLimitInput?.value || DEFAULT_LOCAL_UI_SETTINGS.aiRawTotalLimitMb),
            aiRawCleanupPolicy: cleanupPolicyInput?.value || DEFAULT_LOCAL_UI_SETTINGS.aiRawCleanupPolicy,
            includeAiRawInExport: exportModeInput?.value === "include",
          });
          elements.workspaceStatus.textContent = didPersist
            ? "已保存 `.ai/raw` 本地策略；它只影响本机，不会改变常规同步边界。"
            : "保存 `.ai/raw` 本地策略失败，请检查浏览器本地存储是否可用。";
          render();
          return;
        }
        if (command === "reset-ai-raw-settings") {
          persistLocalUiSettings(DEFAULT_LOCAL_UI_SETTINGS);
          elements.workspaceStatus.textContent = "已恢复 `.ai/raw` 本地默认策略。";
          render();
          return;
        }
        if (command === "restore-all-recovery") {
          restoreAllRecoveryDrafts();
          return;
        }
        if (command === "discard-all-recovery") {
          discardAllRecoveryDrafts();
        }
      });
    }
    for (const button of elements.viewDetailGrid.querySelectorAll("[data-view-command]")) {
      button.addEventListener("click", async () => {
        if (button.dataset.viewCommand === "refresh-bridge") {
          await requestBridgeStatus();
          render();
          return;
        }
        if (button.dataset.viewCommand === "refresh-session") {
          try {
            await refreshFullAppSession();
          } catch (error) {
            state.lastBridgeError = normalizeBridgeError(error);
            elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
            renderBridgeError(state.lastBridgeError);
          }
        }
      });
    }
  }
}

function renderNavViewVisibility() {
  const showSyncSections = state.activeNavView === "conflicts";
  elements.panelCard.hidden = !showSyncSections;
  elements.summaryGrid.hidden = !showSyncSections;
  elements.cardsGrid.hidden = !showSyncSections;
  elements.activityCard.hidden = !showSyncSections;
  elements.viewDetailGrid.hidden = false;
  elements.workspaceShellRoot.hidden = state.activeNavView === "settings";
  elements.controlCenterCard.hidden = state.activeNavView !== "settings";
}

function deriveWorkspaceSyncContext(note) {
  const signals = [];
  const summary = state.syncCenter?.summary || null;
  const panel = state.syncCenter?.panel || null;
  const cards = Array.isArray(state.syncCenter?.cards) ? state.syncCenter.cards : [];
  const activityRecords = Array.isArray(state.activityFeed?.records) ? state.activityFeed.records : [];
  const syncContext = note.syncContext || {};
  const watchActionIds = Array.isArray(syncContext.watchActionIds) ? syncContext.watchActionIds : [];
  const watchCardKinds = Array.isArray(syncContext.watchCardKinds) ? syncContext.watchCardKinds : [];
  const watchBlockingReasons = Array.isArray(syncContext.watchBlockingReasons)
    ? syncContext.watchBlockingReasons
    : [];

  if (!summary || !panel) {
    return {
      headline: "当前工作区文档还没有关联任何同步载荷。",
      signals: [createSignal("info", "没有同步载荷")],
      actions: ["先加载 sync-shell-snapshot 或 sync-center，再让工作区与同步状态联动。"],
      relatedActivity: [],
    };
  }

  if (summary.changes?.change_count > 0) {
    signals.push(createSignal("warning", `本地变更 ${summary.changes.change_count} 项`));
  }
  if (summary.commit_gate?.requires_full_pull) {
    signals.push(createSignal("danger", "需要先完整拉取"));
  }
  if (summary.conflicts?.actual_has_unresolved_conflicts) {
    signals.push(createSignal("danger", "存在未解决冲突"));
  }
  if (summary.worker_health?.status) {
    signals.push(
      createSignal(
        summary.worker_health.status === "healthy" ? "success" : "warning",
        summary.worker_health.status === "healthy" ? "同步线程正常" : "同步线程异常",
      ),
    );
  }

  const matchedCards = cards.filter((card) => watchCardKinds.includes(card.kind));
  const matchedActivity = activityRecords.filter((record) => watchActionIds.includes(record.action_id));
  const matchedBlockingReasons = (summary.commit_gate?.blocking_reasons || []).filter((reason) =>
    watchBlockingReasons.includes(reason),
  );

  for (const card of matchedCards) {
    signals.push(createSignal(card.level, `${formatCardKindLabel(card.kind)}：${card.title}`));
  }

  for (const reason of matchedBlockingReasons) {
    signals.push(createSignal("danger", `提交门禁：${formatBlockingReason(reason)}`));
  }

  const dedupedSignals = [];
  const seenSignalLabels = new Set();
  for (const signal of signals) {
    const key = `${signal.level}:${signal.label}`;
    if (seenSignalLabels.has(key)) {
      continue;
    }
    seenSignalLabels.add(key);
    dedupedSignals.push(signal);
  }

  const actions = [];
  if (matchedBlockingReasons.includes("requires_full_pull")) {
    actions.push("尝试新的提交前，先执行一次 Pull。");
  }
  if (matchedActivity.some((record) => record.status === "failed")) {
    actions.push("先检查失败的同步活动详情，修复本地条件后再重试。");
  }
  if (summary.changes?.change_count > 0) {
    actions.push("在下一次同步前，确认这篇文档与待提交的本地变更保持一致。");
  }
  if (!actions.length) {
    actions.push("这篇文档当前没有必须立即处理的同步后续动作。");
  }

  const latestRelated = matchedActivity.slice(-2).reverse();
  const headlineParts = [
    panel.headline,
    matchedCards[0]?.title || null,
    latestRelated[0]?.message || latestRelated[0]?.status || null,
  ].filter(Boolean);

  return {
    headline: headlineParts.join(" | "),
    signals: dedupedSignals.length ? dedupedSignals : [createSignal("info", "没有命中同步信号")],
    actions,
    relatedActivity: latestRelated,
  };
}

function buildFilteredWorkspaceSections(workspaceShell) {
  const query = normalizeSearchQuery(state.searchQuery);
  return workspaceShell.sections
    .map((section) => {
      const items = section.items.filter((item) => {
        const note = workspaceShell.notes[item.id];
        return matchesSearchQuery(
          query,
          section.label,
          item.title,
          item.path,
          item.status,
          note?.body,
          note?.tags?.join(" "),
        );
      });

      return {
        ...section,
        items,
      };
    })
    .filter((section) => section.items.length > 0);
}

function getVisibleWorkspaceNoteIds(workspaceShell) {
  const ids = [];
  for (const section of buildFilteredWorkspaceSections(workspaceShell)) {
    for (const item of section.items) {
      ids.push(item.id);
    }
  }
  return ids;
}

function syncSelectedWorkspaceNoteToSearch(workspaceShell) {
  const visibleIds = getVisibleWorkspaceNoteIds(workspaceShell);
  if (!visibleIds.length) {
    return;
  }
  if (!visibleIds.includes(state.selectedWorkspaceNoteId)) {
    state.selectedWorkspaceNoteId = visibleIds[0];
  }
}

function findWorkspaceItemById(workspaceShell, noteId) {
  for (const section of workspaceShell.sections || []) {
    for (const item of section.items || []) {
      if (item.id === noteId) {
        return item;
      }
    }
  }
  return null;
}

function findWorkspaceSectionByNoteId(workspaceShell, noteId) {
  for (const section of workspaceShell.sections || []) {
    for (const item of section.items || []) {
      if (item.id === noteId) {
        return section;
      }
    }
  }
  return null;
}

function formatSectionLabel(sectionId) {
  const workspaceShell = getCurrentWorkspaceShell();
  return workspaceShell.sections.find((section) => section.id === sectionId)?.label || sectionId;
}

function resolveWorkspaceNotePathForSection(sectionId, title) {
  const safeTitle = (String(title || "未命名笔记").trim() || "未命名笔记").replace(/[\\/:*?"<>|]/g, "-");
  if (sectionId === "inbox") {
    return `Inbox/${safeTitle}.md`;
  }
  if (sectionId === "ai-wiki") {
    return `.ai/wiki/${safeTitle}.md`;
  }
  return `Notes/${safeTitle}.md`;
}

function ensureWorkspaceSectionById(workspaceShell, sectionId) {
  const existing = workspaceShell.sections.find((section) => section.id === sectionId);
  if (existing) {
    return existing;
  }
  const label = sectionId === "inbox" ? "收件箱" : sectionId === "ai-wiki" ? ".ai/wiki" : "笔记";
  const created = {
    id: sectionId,
    label,
    items: [],
  };
  workspaceShell.sections.push(created);
  return created;
}

function moveWorkspaceNoteToSection(noteId, targetSectionId) {
  const workspaceShell = ensureEditableWorkspaceShell();
  const note = workspaceShell.notes?.[noteId];
  const currentSection = findWorkspaceSectionByNoteId(workspaceShell, noteId);
  if (!note || !currentSection || currentSection.id === targetSectionId) {
    return false;
  }

  const targetSection = ensureWorkspaceSectionById(workspaceShell, targetSectionId);
  const itemIndex = currentSection.items.findIndex((item) => item.id === noteId);
  if (itemIndex < 0) {
    return false;
  }

  const [item] = currentSection.items.splice(itemIndex, 1);
  const nextPath = resolveWorkspaceNotePathForSection(targetSectionId, item.title || note.title);
  item.path = nextPath;
  item.status = targetSectionId === "inbox" ? "本地草稿" : "已整理待保存";
  note.path = nextPath;
  note.statusTone = "info";
  note.statusLabel = targetSectionId === "inbox" ? "本地草稿" : "已整理待保存";
  note.lastSaved = "刚刚整理";
  if (!Array.isArray(note.tags)) {
    note.tags = [];
  }
  note.tags = note.tags.filter((tag) => tag !== "inbox");
  if (targetSectionId === "inbox" && !note.tags.includes("inbox")) {
    note.tags.unshift("inbox");
  }
  if (targetSectionId === "ai-wiki" && !note.tags.includes("ai")) {
    note.tags.unshift("ai");
  }
  targetSection.items.unshift(item);

  state.workspaceSourceLabel = "浏览器本地草稿";
  elements.workspaceInput.value = JSON.stringify(workspaceShell, null, 2);
  persistLocalWorkspaceSession();
  elements.workspaceStatus.textContent = `已将《${item.title || note.title}》整理到 ${targetSection.label}`;
  pushSessionHistory({
    type: "workspace_note_moved",
    level: "success",
    detail: `${item.title || note.title} · ${currentSection.label} -> ${targetSection.label}`,
  });
  render();
  return true;
}

function getSelectedWorkspaceNote() {
  const workspaceShell = state.workspaceShell || WORKSPACE_SAMPLE;
  const visibleIds = getVisibleWorkspaceNoteIds(workspaceShell);
  if (normalizeSearchQuery(state.searchQuery) && !visibleIds.length) {
    return null;
  }

  const selectedId = visibleIds.includes(state.selectedWorkspaceNoteId)
    ? state.selectedWorkspaceNoteId
    : visibleIds[0] || state.selectedWorkspaceNoteId;

  return workspaceShell.notes[selectedId] || workspaceShell.notes["desktop-bridge"] || null;
}

function getEditorDraftByNoteId(noteId) {
  if (!noteId) {
    return null;
  }
  return state.editorDrafts[noteId] || null;
}

function getActiveEditorDraft() {
  const note = getSelectedWorkspaceNote();
  if (!note) {
    return null;
  }
  return getEditorDraftByNoteId(state.selectedWorkspaceNoteId);
}

function setEditorDraftForNote(noteId, draft) {
  if (!noteId || !draft) {
    return;
  }
  state.editorDrafts = {
    ...state.editorDrafts,
    [noteId]: {
      noteId,
      title: draft.title,
      body: draft.body,
    },
  };
}

function getDraftRecoveryMeta(noteId) {
  if (!noteId) {
    return null;
  }
  return state.draftRecoveryMeta[noteId] || null;
}

function setDraftRecoveryMeta(noteId, patch) {
  if (!noteId || !patch) {
    return;
  }
  state.draftRecoveryMeta = {
    ...state.draftRecoveryMeta,
    [noteId]: {
      ...(state.draftRecoveryMeta[noteId] || {}),
      ...patch,
    },
  };
}

function removeDraftRecoveryMeta(noteId) {
  if (!noteId || !state.draftRecoveryMeta[noteId]) {
    return;
  }
  const nextMeta = { ...state.draftRecoveryMeta };
  delete nextMeta[noteId];
  state.draftRecoveryMeta = nextMeta;
}

function removeEditorDraftForNote(noteId) {
  if (!noteId || !state.editorDrafts[noteId]) {
    return;
  }
  const nextDrafts = { ...state.editorDrafts };
  delete nextDrafts[noteId];
  state.editorDrafts = nextDrafts;
}

function pruneEditorDrafts(workspaceShell) {
  const nextDrafts = {};
  const nextRecoveryMeta = {};
  for (const [noteId, draft] of Object.entries(state.editorDrafts)) {
    const note = workspaceShell.notes?.[noteId];
    if (!note) {
      continue;
    }
    nextDrafts[noteId] = {
      noteId,
      title: typeof draft.title === "string" ? draft.title : note.title,
      body: typeof draft.body === "string" ? draft.body : note.body,
    };
    if (state.draftRecoveryMeta[noteId]) {
      nextRecoveryMeta[noteId] = { ...state.draftRecoveryMeta[noteId] };
    }
  }
  state.editorDrafts = nextDrafts;
  state.draftRecoveryMeta = nextRecoveryMeta;
}

function collectDirtyEditorDrafts() {
  const workspaceShell = getCurrentWorkspaceShell();
  return Object.entries(state.editorDrafts)
    .map(([noteId, draft]) => {
      const note = workspaceShell.notes?.[noteId];
      if (!note || !isEditorDraftDirty(note, draft)) {
        return null;
      }
      return {
        noteId,
        title: draft.title?.trim() || note.title || noteId,
      };
    })
    .filter(Boolean);
}

function refreshRecoveryDrafts() {
  state.recoveryDrafts = listRecoverableDrafts(readDraftRecoveryStore(), {
    currentSessionId: state.runtimeSessionId,
    workspaceShell: getCurrentWorkspaceShell(),
  });
}

function clearDraftAutosaveTimer(noteId) {
  const timerId = draftAutosaveTimers.get(noteId);
  if (!timerId) {
    return;
  }
  window.clearTimeout(timerId);
  draftAutosaveTimers.delete(noteId);
}

function persistRecoveryDraftForNote(noteId, options = {}) {
  const workspaceShell = getCurrentWorkspaceShell();
  const note = workspaceShell.notes?.[noteId];
  const draft = getEditorDraftByNoteId(noteId);
  if (!note || !draft || !isEditorDraftDirty(note, draft)) {
    discardPersistedRecoveryDraft(noteId, { refresh: options.refresh });
    return false;
  }

  const section = findWorkspaceSectionByNoteId(workspaceShell, noteId);
  const entry = buildDraftRecoveryEntry(
    {
      noteId,
      title: draft.title,
      body: draft.body,
      path: note.path,
      sectionId: section?.id || "",
      sectionLabel: section?.label || "",
      workspaceSourceLabel: state.workspaceSourceLabel,
    },
    {
      sessionId: state.runtimeSessionId,
      updatedAtMs: Date.now(),
    },
  );

  const store = upsertDraftRecoveryEntry(readDraftRecoveryStore(), entry);
  const persisted = writeDraftRecoveryStore(store);
  if (persisted) {
    setDraftRecoveryMeta(noteId, {
      pending: false,
      lastPersistedAtMs: entry.updatedAtMs,
    });
  }
  if (options.refresh !== false) {
    refreshRecoveryDrafts();
  }
  return persisted;
}

function discardPersistedRecoveryDraft(noteId, options = {}) {
  clearDraftAutosaveTimer(noteId);
  const store = removeDraftRecoveryEntry(readDraftRecoveryStore(), noteId);
  const persisted = writeDraftRecoveryStore(store);
  removeDraftRecoveryMeta(noteId);
  if (options.refresh !== false) {
    refreshRecoveryDrafts();
  }
  return persisted;
}

function scheduleRecoveryDraftAutosave(noteId) {
  clearDraftAutosaveTimer(noteId);
  setDraftRecoveryMeta(noteId, {
    pending: true,
  });
  const timerId = window.setTimeout(() => {
    const persisted = persistRecoveryDraftForNote(noteId, { refresh: false });
    draftAutosaveTimers.delete(noteId);
    if (!persisted) {
      return;
    }
    setDraftRecoveryMeta(noteId, {
      pending: false,
      lastPersistedAtMs: Date.now(),
      restoredFromPreviousSession: false,
    });
    elements.workspaceStatus.textContent = `已自动保存恢复草稿：${getEditorDraftByNoteId(noteId)?.title || noteId}`;
  }, 1000);
  draftAutosaveTimers.set(noteId, timerId);
}

function createRecoveredNoteFromEntry(entry) {
  const workspaceShell = ensureEditableWorkspaceShell();
  const recoveredId = `recovered-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`;
  const title = entry.title?.trim() || "恢复草稿";
  const recoveredTitle = title.startsWith("恢复 - ") ? title : `恢复 - ${title}`;
  const inboxPath = `Inbox/${recoveredTitle}.md`;
  let inboxSection = workspaceShell.sections.find((section) => section.id === "inbox");

  if (!inboxSection) {
    inboxSection = {
      id: "inbox",
      label: "收件箱",
      items: [],
    };
    workspaceShell.sections.unshift(inboxSection);
  }

  inboxSection.items.unshift({
    id: recoveredId,
    title: recoveredTitle,
    path: inboxPath,
    status: "恢复草稿",
  });
  workspaceShell.notes[recoveredId] = {
    title: recoveredTitle,
    path: inboxPath,
    statusTone: "warning",
    statusLabel: "恢复草稿",
    lastSaved: "刚刚恢复",
    tags: ["recovered", "draft", "inbox"],
    syncContext: {
      watchActionIds: ["detect-local-changes", "submit-detected-commit"],
      watchCardKinds: ["changes", "activity"],
      watchBlockingReasons: ["requires_full_pull"],
    },
    body: entry.body || "",
    ai: {
      queueDepth: 0,
      warnings: 1,
      relatedEntities: ["恢复草稿", "本地恢复区", "收件箱"],
      suggestions: ["先确认恢复内容，再决定是否保存回正式知识库。"],
      lint: ["该草稿来自未完成会话，建议先人工复核。"],
    },
  };

  state.selectedWorkspaceNoteId = recoveredId;
  state.workspaceSourceLabel = "浏览器本地草稿";
  elements.workspaceInput.value = JSON.stringify(workspaceShell, null, 2);
  return recoveredId;
}

function restoreRecoveryDraft(noteId) {
  const recoveryEntry = state.recoveryDrafts.find((entry) => entry.noteId === noteId);
  if (!recoveryEntry) {
    return;
  }

  let targetNoteId = recoveryEntry.noteId;
  if (recoveryEntry.noteMissing || !getCurrentWorkspaceShell().notes?.[recoveryEntry.noteId]) {
    targetNoteId = createRecoveredNoteFromEntry(recoveryEntry);
  }

  setEditorDraftForNote(targetNoteId, {
    noteId: targetNoteId,
    title: recoveryEntry.title,
    body: recoveryEntry.body,
  });
  setDraftRecoveryMeta(targetNoteId, {
    pending: false,
    lastPersistedAtMs: Date.now(),
    restoredFromPreviousSession: true,
  });
  state.selectedWorkspaceNoteId = targetNoteId;
  state.activeNavView = "overview";
  persistRecoveryDraftForNote(targetNoteId);
  if (targetNoteId !== recoveryEntry.noteId) {
    discardPersistedRecoveryDraft(recoveryEntry.noteId, { refresh: false });
  }
  refreshRecoveryDrafts();
  persistLocalWorkspaceSession();
  elements.workspaceStatus.textContent = `已恢复草稿：${recoveryEntry.title}`;
  pushSessionHistory({
    type: "workspace_draft_restored",
    level: "success",
    detail: `${recoveryEntry.title} · 已恢复到当前工作区`,
  });
  render();
}

function discardRecoveryDraft(noteId) {
  const recoveryEntry = state.recoveryDrafts.find((entry) => entry.noteId === noteId);
  discardPersistedRecoveryDraft(noteId, { refresh: true });
  removeDraftRecoveryMeta(noteId);
  if (recoveryEntry) {
    elements.workspaceStatus.textContent = `已放弃恢复草稿：${recoveryEntry.title}`;
    pushSessionHistory({
      type: "workspace_draft_discarded",
      level: "warning",
      detail: `${recoveryEntry.title} · 已从本地恢复区删除`,
    });
  }
  render();
}

function restoreAllRecoveryDrafts() {
  const noteIds = state.recoveryDrafts.map((entry) => entry.noteId);
  for (const noteId of noteIds) {
    restoreRecoveryDraft(noteId);
  }
}

function discardAllRecoveryDrafts() {
  const noteIds = state.recoveryDrafts.map((entry) => entry.noteId);
  for (const noteId of noteIds) {
    discardPersistedRecoveryDraft(noteId, { refresh: false });
    removeDraftRecoveryMeta(noteId);
  }
  refreshRecoveryDrafts();
  elements.workspaceStatus.textContent = "已清空本地恢复区中的未恢复草稿。";
  pushSessionHistory({
    type: "workspace_draft_discarded",
    level: "warning",
    detail: `已批量放弃 ${noteIds.length} 份恢复草稿`,
  });
  render();
}

function isEditorDraftDirty(note, draft) {
  if (!note || !draft) {
    return false;
  }
  return draft.title !== note.title || draft.body !== note.body;
}

function countDraftCharacters(text) {
  return String(text || "").replace(/\s+/g, "").length;
}

function countDraftLines(text) {
  const normalized = String(text || "");
  if (!normalized) {
    return 0;
  }
  return normalized.split(/\r?\n/).length;
}

function resolveExecutionTone(status) {
  if (status === "executed") {
    return "success";
  }
  if (status === "failed") {
    return "danger";
  }
  if (status === "disabled" || status === "unsupported") {
    return "warning";
  }
  return "info";
}

function buildEditorDraftInsight(note, draft) {
  if (!note || !draft) {
    return null;
  }

  const savedChars = countDraftCharacters(note.body);
  const draftChars = countDraftCharacters(draft.body);
  const delta = draftChars - savedChars;
  return {
    dirty: isEditorDraftDirty(note, draft),
    titleChanged: draft.title !== note.title,
    savedChars,
    draftChars,
    delta,
    lines: countDraftLines(draft.body),
  };
}

function summarizeRichText(text, maxLength = 110) {
  const normalized = String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith("#") && !line.startsWith("- ") && !/^\d+\.\s/.test(line));
  const source = normalized[0] || "当前文档还没有可提炼的摘要，可继续补充正文内容。";
  return source.length > maxLength ? `${source.slice(0, maxLength - 1)}…` : source;
}

function buildMarkdownOutline(text, limit = 4) {
  return String(text || "")
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => /^#{1,6}\s+/.test(line))
    .map((line) => line.replace(/^#{1,6}\s+/, ""))
    .slice(0, limit);
}

function estimateReadingMinutes(text) {
  const characters = countDraftCharacters(text);
  if (!characters) {
    return 1;
  }
  return Math.max(1, Math.ceil(characters / 320));
}

function buildEditorFocusCards({ note, syncContext, draftInsight, draftRecoveryStatus, readingMinutes, outline }) {
  return [
    {
      label: "阅读成本",
      value: `${readingMinutes} 分钟`,
      detail: outline.length ? `当前检测到 ${outline.length} 个结构节点` : "当前还没有检测到明显的小节结构",
    },
    {
      label: "草稿状态",
      value: draftInsight?.dirty ? "待保存修改" : "可继续整理",
      detail: draftRecoveryStatus?.detail || "开始编辑后会自动写入本地恢复区。",
    },
    {
      label: "下一步",
      value: syncContext.actions[0] || "继续补充正文",
      detail: syncContext.signals[0]?.label || "当前没有更高优先级的同步提醒。",
    },
  ];
}

function buildAiBriefing({ note, noteBody, syncContext, draftInsight }) {
  const summary = summarizeRichText(noteBody, 132);
  const topSignal = syncContext.signals[0]?.label || "当前没有命中的同步信号";
  const headline = draftInsight?.dirty
    ? "先定稿当前草稿，再进入后续同步动作。"
    : syncContext.actions[0] || "当前文档适合继续补充内容或进入下一步同步。";
  return {
    summary,
    headline,
    topSignal,
    outline: buildMarkdownOutline(noteBody, 3),
    entityPreview: note.ai.relatedEntities.slice(0, 4),
  };
}

function upsertMarkdownSection(text, heading, content, level = 2) {
  const normalizedBody = String(text || "").trim();
  const normalizedContent = String(content || "").trim();
  const headingLine = `${"#".repeat(level)} ${heading}`;
  const sectionBlock = `${headingLine}\n${normalizedContent}`;
  const sectionPattern = new RegExp(
    `(^|\\n)${escapeRegExp(headingLine)}\\n[\\s\\S]*?(?=\\n#{1,6}\\s+|$)`,
    "m",
  );

  if (!normalizedBody) {
    return `${sectionBlock}\n`;
  }
  if (sectionPattern.test(normalizedBody)) {
    return `${normalizedBody.replace(sectionPattern, `$1${sectionBlock}`)}\n`;
  }
  return `${normalizedBody}\n\n${sectionBlock}\n`;
}

function buildAiOutlineTemplate(note, syncContext) {
  const sections = [
    "## 背景",
    `- 文档目标：${note.title}`,
    `- 当前状态：${note.statusLabel || "待整理"}`,
    "",
    "## 核心信息",
    "- 在这里补充当前主题的关键事实、结论和上下文。",
    "",
    "## 同步关注",
    `- ${syncContext.signals[0]?.label || "当前没有命中的同步信号。"}`,
    "",
    "## 下一步",
    ...syncContext.actions.slice(0, 3).map((item, index) => `${index + 1}. ${item}`),
  ];
  return sections.join("\n");
}

function ensureEditorDraftSession(noteId = state.selectedWorkspaceNoteId) {
  const workspaceShell = ensureEditableWorkspaceShell();
  const note = workspaceShell.notes?.[noteId];
  if (!note) {
    return null;
  }
  if (!getEditorDraftByNoteId(noteId)) {
    setEditorDraftForNote(noteId, {
      noteId,
      title: note.title,
      body: note.body,
    });
  }
  state.selectedWorkspaceNoteId = noteId;
  state.workspaceSourceLabel = "浏览器本地草稿";
  elements.workspaceInput.value = JSON.stringify(workspaceShell, null, 2);
  persistLocalWorkspaceSession();
  return {
    note,
    draft: getEditorDraftByNoteId(noteId),
  };
}

function applyAiDraftMutation(noteId, mutate, options = {}) {
  const session = ensureEditorDraftSession(noteId);
  if (!session?.draft || typeof mutate !== "function") {
    return false;
  }

  const nextDraft = mutate({
    note: session.note,
    draft: session.draft,
  });
  if (!nextDraft || typeof nextDraft.body !== "string") {
    return false;
  }

  setEditorDraftForNote(noteId, {
    noteId,
    title: typeof nextDraft.title === "string" ? nextDraft.title : session.draft.title,
    body: nextDraft.body,
  });
  scheduleRecoveryDraftAutosave(noteId);
  elements.workspaceStatus.textContent = options.statusMessage || `已更新草稿：${session.note.title}`;
  if (options.historyDetail) {
    pushSessionHistory({
      type: "workspace_ai_applied",
      level: "info",
      detail: options.historyDetail,
    });
  }
  render();
  return true;
}

function buildDraftRecoveryStatus(noteId) {
  const meta = getDraftRecoveryMeta(noteId);
  if (!meta) {
    return null;
  }
  if (meta.pending) {
    return {
      tone: "warning",
      label: "等待自动保存",
      detail: "停止输入 1 秒后，会写入本地恢复区。",
    };
  }
  if (typeof meta.lastPersistedAtMs === "number" && Number.isFinite(meta.lastPersistedAtMs)) {
    return {
      tone: meta.restoredFromPreviousSession ? "warning" : "success",
      label: meta.restoredFromPreviousSession ? "已恢复并保护" : "已写入恢复区",
      detail: `${formatDateTime(meta.lastPersistedAtMs)} · 刷新或异常退出后可恢复。`,
    };
  }
  if (meta.restoredFromPreviousSession) {
    return {
      tone: "warning",
      label: "已从上次会话恢复",
      detail: "建议保存一次，确认已回到当前工作区。",
    };
  }
  return null;
}

function summaryHasBlockingSyncWork() {
  const summary = state.syncCenter?.summary || null;
  return Boolean(
    summary?.commit_gate?.blocking_reasons?.length ||
      summary?.conflicts?.actual_has_unresolved_conflicts ||
      summary?.changes?.change_count,
  );
}

function inferNoteRank(note, noteId) {
  if (!note) {
    return 0;
  }
  let rank = typeof note.updatedAtMs === "number" ? note.updatedAtMs : 0;
  if (state.selectedWorkspaceNoteId === noteId) {
    rank += 10 ** 13;
  }
  if (note.lastSaved?.includes("刚刚")) {
    rank += 9 * 10 ** 12;
  } else if (note.lastSaved?.includes("分钟")) {
    rank += 8 * 10 ** 12;
  } else if (note.lastSaved?.includes("小时")) {
    rank += 7 * 10 ** 12;
  } else if (note.lastSaved?.includes("今天")) {
    rank += 6 * 10 ** 12;
  } else if (note.lastSaved?.includes("昨天")) {
    rank += 5 * 10 ** 12;
  }
  if (note.statusLabel?.includes("草稿") || note.tags?.includes("draft")) {
    rank += 2 * 10 ** 12;
  }
  const editorDraft = getEditorDraftByNoteId(noteId);
  if (editorDraft && isEditorDraftDirty(note, editorDraft)) {
    rank += 3 * 10 ** 12;
  }
  return rank;
}

function collectWorkspaceNotes() {
  const workspaceShell = getCurrentWorkspaceShell();
  const entries = [];
  for (const section of workspaceShell.sections || []) {
    for (const item of section.items || []) {
      const note = workspaceShell.notes?.[item.id];
      if (!note) {
        continue;
      }
      entries.push({
        id: item.id,
        sectionId: section.id,
        sectionLabel: section.label,
        title: item.title,
        path: item.path,
        status: item.status,
        note,
        rank: inferNoteRank(note, item.id),
      });
    }
  }
  return entries;
}

function summarizeSectionActivity(section, workspaceShell) {
  let draftCount = 0;
  let riskCount = 0;
  for (const item of section.items || []) {
    const note = workspaceShell.notes?.[item.id];
    const noteDraft = getEditorDraftByNoteId(item.id);
    if (noteDraft && note && isEditorDraftDirty(note, noteDraft)) {
      draftCount += 1;
    }
    if (note?.statusTone === "warning" || note?.statusTone === "danger") {
      riskCount += 1;
    }
  }
  return {
    draftCount,
    riskCount,
  };
}

function buildOverviewNoteRow(entry, options = {}) {
  const pills = (options.pills || []).filter(Boolean);
  const buttons = (options.buttons || []).filter(Boolean);
  const tone = resolveTone(entry.note?.statusTone || "info");
  const metaLine = [entry.sectionLabel, entry.path].filter(Boolean).join(" · ");
  const summaryLine = options.summary || `${entry.note.lastSaved || entry.status} · ${entry.note.tags?.join(" / ") || "无标签"}`;
  return `
    <article class="detail-row detail-row-block overview-row">
      <div class="overview-row-main">
        <div class="overview-row-copy">
          <strong>${escapeHtml(entry.title)}</strong>
          <span>${escapeHtml(metaLine)}</span>
          <span>${escapeHtml(summaryLine)}</span>
        </div>
        <div class="overview-row-meta">
          <span class="mini-pill tone-${tone}">${escapeHtml(entry.note.statusLabel || entry.status)}</span>
          ${pills.map((pill) => `<span class="mini-pill tone-info">${escapeHtml(pill)}</span>`).join("")}
        </div>
      </div>
      ${
        buttons.length
          ? `<div class="detail-actions overview-row-actions">${buttons.join("")}</div>`
          : ""
      }
    </article>
  `;
}

function getDirtyEditorDraft(excludedNoteId = null) {
  return collectDirtyEditorDrafts().find((draft) => draft.noteId !== excludedNoteId) || null;
}

function getSelectedSectionId(workspaceShell) {
  return findWorkspaceSectionByNoteId(workspaceShell, state.selectedWorkspaceNoteId)?.id || null;
}

function findRecommendedSyncActionForNote(note) {
  const actions = collectWorkspaceSyncActions(note);
  return actions.find(({ action }) => action.enabled !== false) || actions[0] || null;
}

function focusWorkspaceNoteForEdit(noteId) {
  const targetId = noteId || state.selectedWorkspaceNoteId;
  const dirtyDraft = getDirtyEditorDraft(targetId);
  state.selectedWorkspaceNoteId = targetId;
  state.activeNavView = "overview";
  if (dirtyDraft) {
    elements.workspaceStatus.textContent =
      `已切换文档，但仍保留未保存草稿：${dirtyDraft.title}。请先保存或取消后再开始新的编辑。`;
    pushSessionHistory({
      type: "workspace_edit_blocked",
      level: "warning",
      detail: `${dirtyDraft.title} · 未保存草稿仍保留`,
    });
    render();
    return;
  }
  startEditingSelectedNote();
}

function selectRecommendedSyncActionForNote(noteId, options = {}) {
  const targetId = noteId || state.selectedWorkspaceNoteId;
  const note = getCurrentWorkspaceShell().notes?.[targetId];
  if (!note) {
    return null;
  }
  const recommendation = findRecommendedSyncActionForNote(note);
  state.selectedWorkspaceNoteId = targetId;
  state.activeNavView = "overview";
  if (!recommendation) {
    render();
    return null;
  }
  setSelectedAction(recommendation.action, `workspace:${targetId}`);
  if (options.renderAfterSelection !== false) {
    render();
  }
  return recommendation;
}

async function executeRecommendedSyncActionForNote(noteId) {
  const recommendation = selectRecommendedSyncActionForNote(noteId, { renderAfterSelection: false });
  if (!recommendation) {
    render();
    return;
  }
  const dirtyDraft = getDirtyEditorDraft();
  if (dirtyDraft) {
    elements.workspaceStatus.textContent =
      `当前仍有未保存草稿：${dirtyDraft.title}。请先保存草稿，再执行同步动作。`;
    pushSessionHistory({
      type: "workspace_sync_blocked",
      level: "warning",
      detail: `${dirtyDraft.title} · 先保存草稿再执行同步`,
    });
    render();
    return;
  }
  try {
    await executeSelectedAction();
  } catch (error) {
    state.lastBridgeError = normalizeBridgeError(error);
    elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
    renderBridgeError(state.lastBridgeError);
  }
}

function buildLastExecutionSummary() {
  if (!state.lastExecution) {
    return null;
  }

  const action = state.lastExecution.action || {};
  const status = state.lastExecution.status || "unknown";
  const detailParts = [
    action.command || null,
    Array.isArray(action.argv) && action.argv.length ? action.argv.join(" ") : null,
  ].filter(Boolean);

  return {
    actionId: action.action_id || "unknown",
    status,
    statusLabel: formatStatusLabel(status),
    tone: resolveExecutionTone(status),
    commandLine: detailParts.join(" "),
    atLabel: state.lastExecutionAtMs ? formatDateTime(state.lastExecutionAtMs) : "刚刚",
  };
}

function collectWorkspaceSyncActions(note) {
  if (!note || !state.syncCenter) {
    return [];
  }

  const actions = [];
  const seen = new Set();
  const syncContext = note.syncContext || {};
  const watchActionIds = Array.isArray(syncContext.watchActionIds) ? syncContext.watchActionIds : [];
  const watchCardKinds = Array.isArray(syncContext.watchCardKinds) ? syncContext.watchCardKinds : [];
  const pushAction = (action, source, force = false) => {
    if (!action?.action_id || seen.has(action.action_id)) {
      return;
    }
    if (!force && watchActionIds.length && !watchActionIds.includes(action.action_id)) {
      return;
    }
    seen.add(action.action_id);
    actions.push({ action, source });
  };

  pushAction(state.syncCenter.panel?.primary_action, "panel.primary_action", true);
  for (const action of state.syncCenter.panel?.secondary_actions || []) {
    pushAction(action, "panel.secondary_actions");
  }

  for (const card of state.syncCenter.cards || []) {
    if (watchCardKinds.length && !watchCardKinds.includes(card.kind)) {
      continue;
    }
    for (const action of card.actions || []) {
      pushAction(action, `card:${card.card_id}`);
    }
  }

  return actions;
}

function countWorkspaceNotes(workspaceShell) {
  return workspaceShell.sections.reduce(
    (total, section) => total + (Array.isArray(section.items) ? section.items.length : 0),
    0,
  );
}

function renderWorkspaceTree() {
  const workspaceShell = state.workspaceShell || WORKSPACE_SAMPLE;
  const filteredSections = buildFilteredWorkspaceSections(workspaceShell);
  const selectedSectionId = getSelectedSectionId(workspaceShell);

  if (!filteredSections.length) {
    elements.workspaceTree.innerHTML = `
      <div class="empty-state">
        <p>没有找到与“${escapeHtml(state.searchQuery)}”匹配的文档。</p>
      </div>
    `;
    return;
  }

  elements.workspaceTree.innerHTML = filteredSections
    .map(
      (section) => {
        const stats = summarizeSectionActivity(section, workspaceShell);
        return `
          <section class="tree-section ${section.id === selectedSectionId ? "is-active" : ""}">
            <div class="tree-section-header">
              <div>
                <h3>${escapeHtml(section.label)}</h3>
                <p class="tree-section-summary">${escapeHtml(`当前可见 ${section.items.length} 篇文档${stats.draftCount ? ` · ${stats.draftCount} 篇有未保存草稿` : ""}`)}</p>
              </div>
              <div class="tree-section-meta">
                <span class="mini-pill tone-info">${escapeHtml(section.items.length)} 篇</span>
                ${
                  stats.draftCount
                    ? `<span class="mini-pill tone-warning">${escapeHtml(stats.draftCount)} 草稿</span>`
                    : ""
                }
                ${
                  stats.riskCount
                    ? `<span class="mini-pill tone-danger">${escapeHtml(stats.riskCount)} 风险</span>`
                    : ""
                }
              </div>
            </div>
            <div class="tree-list">
              ${section.items
                .map((item) => {
                  const noteDraft = getEditorDraftByNoteId(item.id);
                  const note = workspaceShell.notes[item.id];
                  const draftLabel = noteDraft
                    ? isEditorDraftDirty(note, noteDraft)
                      ? "有未保存草稿"
                      : "草稿编辑中"
                    : "";
                  return `
                    <button
                      class="tree-node ${item.id === state.selectedWorkspaceNoteId ? "is-active" : ""}"
                      data-note-id="${escapeHtml(item.id)}"
                      type="button"
                    >
                      <div class="tree-node-main">
                        <span class="tree-node-title">${escapeHtml(item.title)}</span>
                        <span class="mini-pill tone-${resolveTone(note?.statusTone || "info")}">${escapeHtml(item.status)}</span>
                      </div>
                      <span class="tree-node-path">${escapeHtml(item.path)}</span>
                      ${
                        draftLabel
                          ? `<span class="tree-node-path tone-warning-inline">${escapeHtml(draftLabel)}</span>`
                          : ""
                      }
                      <span class="tree-node-signals" data-signal-host="${escapeHtml(item.id)}"></span>
                    </button>
                  `;
                })
                .join("")}
            </div>
          </section>
        `;
      },
    )
    .join("");

  for (const button of elements.workspaceTree.querySelectorAll("[data-note-id]")) {
    button.addEventListener("click", () => {
      state.selectedWorkspaceNoteId = button.dataset.noteId;
      render();
    });
  }

  for (const host of elements.workspaceTree.querySelectorAll("[data-signal-host]")) {
    const note = workspaceShell.notes[host.dataset.signalHost];
    if (!note) {
      continue;
    }
    const syncContext = deriveWorkspaceSyncContext(note);
    for (const signal of syncContext.signals.slice(0, 2)) {
      const fragment = elements.workspaceSignalTemplate.content.cloneNode(true);
      const pill = fragment.querySelector(".workspace-signal-pill");
      pill.className = `mini-pill tone-${signal.level} workspace-signal-pill`;
      pill.textContent = signal.label;
      host.appendChild(fragment);
    }
  }
}

function renderWorkspaceRail() {
  const workspaceShell = state.workspaceShell || WORKSPACE_SAMPLE;
  const note = getSelectedWorkspaceNote();
  const editorDraft = getActiveEditorDraft();
  const dirtyDrafts = collectDirtyEditorDrafts();
  const draftInsight = buildEditorDraftInsight(note, editorDraft);
  const draftRecoveryStatus = note ? buildDraftRecoveryStatus(state.selectedWorkspaceNoteId) : null;
  const lastExecution = buildLastExecutionSummary();
  const visibleCount = getVisibleWorkspaceNoteIds(workspaceShell).length;
  const summary = state.syncCenter?.summary || null;
  const conflictCount = summary
    ? (summary.conflicts?.conflict_copies?.length || 0) + (summary.conflicts?.conflict_orphans?.length || 0)
    : 0;
  const blockingReasons = Array.isArray(summary?.commit_gate?.blocking_reasons)
    ? summary.commit_gate.blocking_reasons.map(formatBlockingReason)
    : [];
  const syncKind = state.snapshotMetadata
    ? formatPayloadKindLabel("sync-shell-snapshot")
    : state.syncCenter
      ? formatPayloadKindLabel("sync-center")
      : state.activityFeed
        ? formatPayloadKindLabel("sync-activity")
        : "未加载";
  const noteBody = editorDraft ? editorDraft.body : note?.body || "";
  const noteSummary = summarizeRichText(noteBody, 96);
  const readingMinutes = estimateReadingMinutes(noteBody);

  if (!note) {
    elements.workspaceRail.innerHTML = `
      <div class="sidebar-card-header">
        <div>
          <p class="card-section-label">当前上下文</p>
          <h2>工作区概览</h2>
        </div>
        <span class="mini-pill tone-warning">搜索中</span>
      </div>
      <div class="empty-state">
        <p>当前搜索没有命中任何工作区文档。</p>
      </div>
    `;
    return;
  }

  elements.workspaceRail.innerHTML = `
    <div class="sidebar-card-header">
      <div>
        <p class="card-section-label">当前上下文</p>
        <h2>工作区概览</h2>
      </div>
      <span class="mini-pill tone-info">${escapeHtml(visibleCount)} / ${escapeHtml(countWorkspaceNotes(workspaceShell))} 篇</span>
    </div>
    <div class="session-rail-grid">
      <article class="session-rail-item rail-highlight-card">
        <span class="session-rail-title">今日焦点</span>
        <strong>${escapeHtml(note.title)}</strong>
        <p class="session-rail-copy">${escapeHtml(noteSummary)}</p>
        <div class="rail-metric-grid">
          <div class="rail-metric">
            <span class="metric-label">路径</span>
            <strong>${escapeHtml(note.path)}</strong>
          </div>
          <div class="rail-metric">
            <span class="metric-label">阅读</span>
            <strong>${escapeHtml(`${readingMinutes} 分钟`)}</strong>
          </div>
        </div>
        <div class="session-rail-pills">
          <span class="mini-pill tone-${resolveTone(note.statusTone)}">${escapeHtml(note.statusLabel)}</span>
          <span class="mini-pill tone-info">${escapeHtml(note.lastSaved)}</span>
          ${
            draftInsight
              ? `<span class="mini-pill ${draftInsight.dirty ? "tone-warning" : "tone-success"}">${draftInsight.dirty ? "有未保存草稿" : "草稿已对齐"}</span>`
              : ""
          }
        </div>
        <div class="detail-actions rail-action-row">
          <button class="solid detail-inline-button" data-rail-command="edit-current" type="button">${editorDraft ? "继续编辑" : "开始编辑"}</button>
          <button class="ghost detail-inline-button" data-rail-nav="repository" type="button">仓库浏览</button>
        </div>
      </article>
      <article class="session-rail-item">
        <span class="session-rail-title">工作区健康</span>
        <strong>${escapeHtml(blockingReasons.length ? "需要继续处理" : "当前可继续")}</strong>
        <div class="rail-metric-grid">
          <div class="rail-metric">
            <span class="metric-label">会话来源</span>
            <strong>${escapeHtml(syncKind)}</strong>
          </div>
          <div class="rail-metric">
            <span class="metric-label">冲突工件</span>
            <strong>${escapeHtml(conflictCount)}</strong>
          </div>
          <div class="rail-metric">
            <span class="metric-label">本地变更</span>
            <strong>${escapeHtml(summary?.changes?.change_count ?? 0)}</strong>
          </div>
        </div>
        <p class="session-rail-copy">${escapeHtml(blockingReasons.length ? blockingReasons.join("，") : "当前没有提交阻塞项，可继续整理内容或进入下一步同步。")}</p>
        <div class="detail-actions rail-action-row">
          <button class="ghost detail-inline-button" data-rail-nav="conflicts" type="button">查看冲突处理</button>
        </div>
      </article>
      <article class="session-rail-item">
        <span class="session-rail-title">草稿与恢复</span>
        <strong>${escapeHtml(dirtyDrafts.length ? `${dirtyDrafts.length} 篇未保存草稿` : "当前草稿稳定")}</strong>
        <div class="rail-metric-grid">
          <div class="rail-metric">
            <span class="metric-label">恢复队列</span>
            <strong>${escapeHtml(state.recoveryDrafts.length)}</strong>
          </div>
          <div class="rail-metric">
            <span class="metric-label">工作区来源</span>
            <strong>${escapeHtml(state.workspaceSourceLabel)}</strong>
          </div>
        </div>
        <p class="session-rail-copy">${escapeHtml(dirtyDrafts.length ? dirtyDrafts.map((draft) => draft.title).join(" · ") : "当前没有挂起的未保存修改。")}</p>
        ${
          draftRecoveryStatus
            ? `<p class="session-rail-copy">${escapeHtml(`${draftRecoveryStatus.label} · ${draftRecoveryStatus.detail}`)}</p>`
            : ""
        }
        <div class="detail-actions rail-action-row">
          ${
            state.recoveryDrafts.length
              ? `<button class="ghost detail-inline-button" data-rail-command="restore-recovery" type="button">恢复全部草稿</button>`
              : ""
          }
        </div>
      </article>
      ${
        state.recoveryDrafts.length
          ? `
            <article class="session-rail-item">
              <span class="session-rail-title">恢复提醒</span>
              <strong>${escapeHtml(state.recoveryDrafts.length)} 份待恢复草稿</strong>
              <p class="session-rail-copy">这些内容来自上一次未完成的前端会话，本次可选择恢复或放弃。</p>
              <p class="session-rail-copy">${escapeHtml(state.recoveryDrafts.slice(0, 3).map((entry) => entry.title || entry.noteId).join(" · "))}</p>
            </article>
          `
          : ""
      }
      ${
        lastExecution
          ? `
            <article class="session-rail-item">
              <span class="session-rail-title">最近执行</span>
              <strong>${escapeHtml(lastExecution.actionId)}</strong>
              <p class="session-rail-copy">${escapeHtml(lastExecution.statusLabel)} · ${escapeHtml(lastExecution.atLabel)}</p>
              <p class="session-rail-copy">${escapeHtml(lastExecution.commandLine || "通过桌面桥接执行")}</p>
            </article>
          `
          : ""
      }
    </div>
  `;

  for (const button of elements.workspaceRail.querySelectorAll("[data-rail-nav]")) {
    button.addEventListener("click", () => {
      state.activeNavView = button.dataset.railNav || "overview";
      render();
    });
  }
  for (const button of elements.workspaceRail.querySelectorAll("[data-rail-command]")) {
    button.addEventListener("click", () => {
      if (button.dataset.railCommand === "edit-current") {
        startEditingSelectedNote();
        return;
      }
      if (button.dataset.railCommand === "restore-recovery") {
        restoreAllRecoveryDrafts();
      }
    });
  }
}

function renderWorkspaceEditor() {
  const note = getSelectedWorkspaceNote();
  if (!note) {
    elements.workspaceEditor.innerHTML = `
      <div class="empty-state">
        <p>当前搜索没有命中任何文档，请调整关键词后再查看编辑区。</p>
      </div>
    `;
    return;
  }
  const syncContext = deriveWorkspaceSyncContext(note);
  const editorDraft = getActiveEditorDraft();
  const draftDirty = isEditorDraftDirty(note, editorDraft);
  const draftWordCount = editorDraft ? countDraftCharacters(editorDraft.body) : 0;
  const draftInsight = buildEditorDraftInsight(note, editorDraft);
  const draftRecoveryStatus = buildDraftRecoveryStatus(state.selectedWorkspaceNoteId);
  const workspaceSyncActions = collectWorkspaceSyncActions(note);
  const canEnterSyncFlow = !draftInsight || !draftInsight.dirty;
  const lastExecution = buildLastExecutionSummary();
  const noteBody = editorDraft ? editorDraft.body : note.body;
  const noteSummary = summarizeRichText(noteBody);
  const noteOutline = buildMarkdownOutline(noteBody);
  const readingMinutes = estimateReadingMinutes(noteBody);
  const currentSection = findWorkspaceSectionByNoteId(getCurrentWorkspaceShell(), state.selectedWorkspaceNoteId);
  const moveTargets = (getCurrentWorkspaceShell().sections || []).filter(
    (section) => section.id !== currentSection?.id && ["inbox", "notes", "ai-wiki"].includes(section.id),
  );
  const editorFocusCards = buildEditorFocusCards({
    note,
    syncContext,
    draftInsight,
    draftRecoveryStatus,
    readingMinutes,
    outline: noteOutline,
  });
  elements.workspaceEditor.innerHTML = `
    <div class="editor-toolbar">
      <div>
        <p class="card-meta">当前文档</p>
        <h2 class="editor-title">${escapeHtml(editorDraft ? editorDraft.title || note.title : note.title)}</h2>
      </div>
      <div class="editor-toolbar-actions">
        <span class="level-pill tone-${resolveTone(note.statusTone)}">${escapeHtml(note.statusLabel)}</span>
        ${
          editorDraft
            ? `
              <span id="editor-dirty-indicator" class="mini-pill ${draftDirty ? "tone-warning" : "tone-success"}">${draftDirty ? "未保存修改" : "已同步草稿"}</span>
              <button class="ghost editor-action-button" data-editor-command="cancel" type="button">取消</button>
              <button class="solid editor-action-button" data-editor-command="save" type="button">保存草稿</button>
            `
            : `<button class="ghost editor-action-button" data-editor-command="edit" type="button">编辑笔记</button>`
        }
      </div>
    </div>
    <div class="editor-meta-row">
      <span class="mini-pill tone-info">${escapeHtml(note.path)}</span>
      <span class="mini-pill tone-info">${escapeHtml(note.lastSaved)}</span>
      <span id="editor-recovery-pill" class="mini-pill tone-${draftRecoveryStatus?.tone || "info"}">${escapeHtml(draftRecoveryStatus?.label || "尚未写入恢复区")}</span>
    </div>
    <ul class="editor-tags">
      ${note.tags.map((tag) => `<li>${escapeHtml(tag)}</li>`).join("")}
    </ul>
    <section class="editor-sync-box">
      <h3>本地归档</h3>
      <p class="summary-copy">${escapeHtml(`当前文档位于 ${currentSection?.label || "未知分区"}，可以先在本地整理分区，再决定是否进入同步周期。`)}</p>
      <div class="editor-sync-checklist">
        <div class="editor-sync-item">
          <span>当前分区</span>
          <strong>${escapeHtml(currentSection?.label || "未知分区")}</strong>
        </div>
        <div class="editor-sync-item">
          <span>当前路径</span>
          <strong>${escapeHtml(note.path)}</strong>
        </div>
        <div class="editor-sync-item">
          <span>整理状态</span>
          <strong>${escapeHtml(note.statusLabel)}</strong>
        </div>
      </div>
      <div class="detail-actions">
        ${
          moveTargets.length
            ? moveTargets
                .map(
                  (section) =>
                    `<button class="ghost detail-inline-button" data-editor-move="${escapeHtml(section.id)}" type="button">整理到 ${escapeHtml(section.label)}</button>`,
                )
                .join("")
            : '<span class="mini-pill tone-info">当前没有其他可用分区</span>'
        }
      </div>
    </section>
    <section class="editor-brief-card">
      <div class="editor-brief-copy">
        <p class="card-section-label">内容概览</p>
        <h3>${escapeHtml(noteSummary)}</h3>
        <p class="summary-copy">当前正文预计阅读 ${escapeHtml(readingMinutes)} 分钟。${escapeHtml(noteOutline.length ? `已检测到 ${noteOutline.length} 个结构节点，可继续沿结构整理内容。` : "建议先补一个明确的小节标题，便于后续 AI 和同步动作理解内容层次。")}</p>
      </div>
      <div class="editor-brief-metrics">
        ${editorFocusCards
          .map(
            (item) => `
              <article class="editor-focus-card">
                <span class="metric-label">${escapeHtml(item.label)}</span>
                <strong>${escapeHtml(item.value)}</strong>
                <p>${escapeHtml(item.detail)}</p>
              </article>
            `,
          )
          .join("")}
      </div>
    </section>
    ${
      noteOutline.length
        ? `
          <section class="editor-outline-card">
            <h3>本页结构</h3>
            <div class="editor-outline-list">
              ${noteOutline.map((item) => `<span class="editor-outline-chip">${escapeHtml(item)}</span>`).join("")}
            </div>
          </section>
        `
        : ""
    }
    <section class="editor-sync-box">
      <h3>同步联动</h3>
      <p class="summary-copy">${escapeHtml(syncContext.headline)}</p>
      <div class="editor-signal-row">
        ${syncContext.signals
          .map(
            (signal) =>
              `<span class="mini-pill tone-${escapeHtml(signal.level)} workspace-signal-pill">${escapeHtml(signal.label)}</span>`,
          )
          .join("")}
      </div>
    </section>
    <section class="editor-sync-box">
      <h3>草稿进入同步</h3>
      <div class="editor-sync-checklist">
        <div class="editor-sync-item">
          <span>草稿保存</span>
          <strong class="${canEnterSyncFlow ? "tone-success-inline" : "tone-warning-inline"}">${canEnterSyncFlow ? "可进入同步" : "请先保存草稿"}</strong>
        </div>
        <div class="editor-sync-item">
          <span>桌面桥接</span>
          <strong class="${state.bridgeStatus?.available ? "tone-success-inline" : "tone-warning-inline"}">${state.bridgeStatus?.available ? "可执行动作" : "当前不可执行"}</strong>
        </div>
        <div class="editor-sync-item">
          <span>同步阻塞</span>
          <strong class="${summaryHasBlockingSyncWork() ? "tone-warning-inline" : "tone-success-inline"}">${summaryHasBlockingSyncWork() ? "仍有阻塞项" : "当前可继续"}</strong>
        </div>
      </div>
      <p class="summary-copy">
        ${
          canEnterSyncFlow
            ? "保存后的草稿可以直接从这里选择下一步同步动作。"
            : "当前正文仍有未保存修改，建议先保存，再进入 Pull / 检测变更 / 提交变更流程。"
        }
      </p>
      <div class="editor-sync-actions" id="editor-sync-actions"></div>
    </section>
    ${
      editorDraft
        ? `
          <section class="editor-sync-box">
            <h3>本地恢复保护</h3>
            <div class="editor-sync-checklist">
              <div class="editor-sync-item">
                <span>离开页面保护</span>
                <strong class="${draftInsight?.dirty ? "tone-warning-inline" : "tone-success-inline"}">${draftInsight?.dirty ? "已开启提醒" : "当前无需提醒"}</strong>
              </div>
              <div class="editor-sync-item">
                <span>恢复区状态</span>
                <strong id="editor-recovery-status" class="tone-${draftRecoveryStatus?.tone || "info"}-inline">${escapeHtml(draftRecoveryStatus?.label || "尚未写入恢复区")}</strong>
              </div>
              <div class="editor-sync-item">
                <span>说明</span>
                <strong id="editor-recovery-detail">${escapeHtml(draftRecoveryStatus?.detail || "开始输入后，系统会在 1 秒静默后写入本地恢复区。")}</strong>
              </div>
            </div>
            <p class="summary-copy">恢复草稿仅保留在本地浏览器，不参与同步。保存正式草稿后，会自动清理对应恢复记录。</p>
          </section>
        `
        : ""
    }
    ${
      lastExecution
        ? `
          <section class="editor-sync-box">
            <h3>最近执行反馈</h3>
            <div class="editor-sync-checklist">
              <div class="editor-sync-item">
                <span>动作</span>
                <strong>${escapeHtml(lastExecution.actionId)}</strong>
              </div>
              <div class="editor-sync-item">
                <span>状态</span>
                <strong class="tone-${lastExecution.tone}-inline">${escapeHtml(lastExecution.statusLabel)}</strong>
              </div>
              <div class="editor-sync-item">
                <span>时间</span>
                <strong>${escapeHtml(lastExecution.atLabel)}</strong>
              </div>
            </div>
            <p class="summary-copy">${escapeHtml(lastExecution.commandLine || "通过桌面桥接完成执行。")}</p>
          </section>
        `
        : ""
    }
    ${
      editorDraft
        ? `
          <section class="editor-draft-panel">
            <label class="editor-field">
              <span class="metric-label">标题</span>
              <input
                id="editor-title-input"
                class="editor-title-input"
                type="text"
                value="${escapeHtml(editorDraft.title)}"
                placeholder="输入文档标题"
              />
            </label>
            <label class="editor-field">
              <span class="metric-label">正文</span>
              <textarea
                id="editor-body-input"
                class="editor-body-input"
                spellcheck="false"
                placeholder="输入正文内容"
              >${escapeHtml(editorDraft.body)}</textarea>
            </label>
            <div class="editor-draft-meta">
              <span id="editor-word-count" class="mini-pill tone-info">字数 ${escapeHtml(draftWordCount)}</span>
              <span class="mini-pill tone-info">快捷键 Ctrl+S 保存</span>
            </div>
            <p class="summary-copy">当前保存到浏览器本地草稿工作区，不会直接写回桌面仓库。</p>
          </section>
        `
        : `<pre class="editor-body">${escapeHtml(note.body)}</pre>`
    }
  `;

  const titleInput = elements.workspaceEditor.querySelector("#editor-title-input");
  const bodyInput = elements.workspaceEditor.querySelector("#editor-body-input");
  const dirtyIndicator = elements.workspaceEditor.querySelector("#editor-dirty-indicator");
  const wordCount = elements.workspaceEditor.querySelector("#editor-word-count");
  const recoveryPill = elements.workspaceEditor.querySelector("#editor-recovery-pill");
  const recoveryStatus = elements.workspaceEditor.querySelector("#editor-recovery-status");
  const recoveryDetail = elements.workspaceEditor.querySelector("#editor-recovery-detail");
  const editorSyncActions = elements.workspaceEditor.querySelector("#editor-sync-actions");
  if (editorSyncActions) {
    if (!canEnterSyncFlow) {
      const saveButton = document.createElement("button");
      saveButton.type = "button";
      saveButton.className = "solid editor-action-button";
      saveButton.textContent = "先保存草稿";
      saveButton.addEventListener("click", () => saveEditingSelectedNote());
      editorSyncActions.appendChild(saveButton);
    }
    for (const { action, source } of workspaceSyncActions) {
      editorSyncActions.appendChild(createActionChip(action, source));
    }
    if (state.selectedAction) {
      const executeButton = document.createElement("button");
      executeButton.type = "button";
      executeButton.className = "solid editor-action-button";
      executeButton.textContent = "执行已选动作";
      executeButton.disabled = !state.bridgeStatus?.available || !canEnterSyncFlow;
      executeButton.addEventListener("click", async () => {
        try {
          await executeSelectedAction();
        } catch (error) {
          state.lastBridgeError = normalizeBridgeError(error);
          elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
          renderBridgeError(state.lastBridgeError);
        }
      });
      editorSyncActions.appendChild(executeButton);
    }
  }
  if (titleInput && bodyInput && editorDraft) {
    const updateDraftMeta = () => {
      const currentNote = getSelectedWorkspaceNote();
      const currentDraft = getActiveEditorDraft();
      if (!currentDraft) {
        return;
      }
      const isDirty = isEditorDraftDirty(currentNote, currentDraft);
      if (dirtyIndicator) {
        dirtyIndicator.className = `mini-pill ${isDirty ? "tone-warning" : "tone-success"}`;
        dirtyIndicator.textContent = isDirty ? "未保存修改" : "已同步草稿";
      }
      if (wordCount) {
        const count = countDraftCharacters(currentDraft.body);
        wordCount.textContent = `字数 ${count}`;
      }
      const currentRecoveryStatus = buildDraftRecoveryStatus(editorDraft.noteId);
      if (recoveryPill && currentRecoveryStatus) {
        recoveryPill.className = `mini-pill tone-${currentRecoveryStatus.tone}`;
        recoveryPill.textContent = currentRecoveryStatus.label;
      }
      if (recoveryStatus) {
        recoveryStatus.className = `tone-${currentRecoveryStatus?.tone || "info"}-inline`;
        recoveryStatus.textContent = currentRecoveryStatus?.label || "尚未写入恢复区";
      }
      if (recoveryDetail) {
        recoveryDetail.textContent =
          currentRecoveryStatus?.detail || "开始输入后，系统会在 1 秒静默后写入本地恢复区。";
      }
    };
    titleInput.addEventListener("input", (event) => {
      setEditorDraftForNote(editorDraft.noteId, {
        ...editorDraft,
        title: event.target.value,
        body: bodyInput.value,
      });
      updateDraftMeta();
      scheduleRecoveryDraftAutosave(editorDraft.noteId);
    });
    bodyInput.addEventListener("input", (event) => {
      setEditorDraftForNote(editorDraft.noteId, {
        ...getActiveEditorDraft(),
        title: titleInput.value,
        body: event.target.value,
      });
      updateDraftMeta();
      scheduleRecoveryDraftAutosave(editorDraft.noteId);
    });
    const saveOnShortcut = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        saveEditingSelectedNote();
      }
    };
    titleInput.addEventListener("keydown", saveOnShortcut);
    bodyInput.addEventListener("keydown", saveOnShortcut);
    bodyInput.addEventListener("focus", () => {
      bodyInput.setSelectionRange(bodyInput.value.length, bodyInput.value.length);
    });
  }

  for (const button of elements.workspaceEditor.querySelectorAll("[data-editor-command]")) {
    button.addEventListener("click", () => {
      const command = button.dataset.editorCommand;
      if (command === "edit") {
        startEditingSelectedNote();
        return;
      }
      if (command === "cancel") {
        cancelEditingSelectedNote();
        return;
      }
      if (command === "save") {
        saveEditingSelectedNote();
      }
    });
  }
  for (const button of elements.workspaceEditor.querySelectorAll("[data-editor-move]")) {
    button.addEventListener("click", () => {
      moveWorkspaceNoteToSection(state.selectedWorkspaceNoteId, button.dataset.editorMove || "");
    });
  }
  refreshActionChipSelection();
}

function renderWorkspaceAiPanel() {
  const note = getSelectedWorkspaceNote();
  if (!note) {
    elements.workspaceAiPanel.innerHTML = `
      <div class="empty-state">
        <p>当前搜索没有命中任何文档，因此 AI 面板暂时没有上下文可展示。</p>
      </div>
    `;
    return;
  }
  const syncContext = deriveWorkspaceSyncContext(note);
  const editorDraft = getActiveEditorDraft();
  const draftInsight = buildEditorDraftInsight(note, editorDraft);
  const noteBody = editorDraft ? editorDraft.body : note.body;
  const aiBriefing = buildAiBriefing({
    note,
    noteBody,
    syncContext,
    draftInsight,
  });
  const draftActions = [];
  if (draftInsight?.dirty) {
    draftActions.push("先保存本地草稿，再决定是否执行同步动作或切换文档。");
  }
  if (draftInsight?.titleChanged) {
    draftActions.push("标题已被修改，保存后需要确认左侧树和知识图谱节点是否仍符合预期。");
  }
  if (draftInsight && summaryHasBlockingSyncWork()) {
    draftActions.push("当前同步仍有阻塞项，草稿保存后建议先处理同步风险，再考虑提交。");
  }
  const aiCommands = [
    {
      id: "start-edit",
      label: editorDraft ? "继续编辑草稿" : "进入编辑",
      kind: "ghost",
    },
    {
      id: "apply-summary",
      label: "写入 AI 摘要",
      kind: "solid",
    },
    {
      id: "apply-outline",
      label: "补结构骨架",
      kind: "ghost",
    },
    {
      id: "apply-next-steps",
      label: "写入下一步",
      kind: "ghost",
    },
    {
      id: "create-followup",
      label: "生成跟进笔记",
      kind: "ghost",
    },
  ];
  elements.workspaceAiPanel.innerHTML = `
    <div class="pane-heading">
      <div>
        <p class="card-meta">AI 助手</p>
        <h2 class="ai-panel-title">${escapeHtml(note.title)} 的上下文</h2>
      </div>
      <span class="mini-pill tone-warning">联动</span>
    </div>
    <section class="ai-brief-card">
      <p class="card-section-label">AI 速览</p>
      <h3>${escapeHtml(aiBriefing.headline)}</h3>
      <p class="ai-copy">${escapeHtml(aiBriefing.summary)}</p>
      <div class="ai-brief-grid">
        <div class="ai-brief-item">
          <span class="metric-label">首要信号</span>
          <strong>${escapeHtml(aiBriefing.topSignal)}</strong>
        </div>
        <div class="ai-brief-item">
          <span class="metric-label">结构提取</span>
          <strong>${escapeHtml(aiBriefing.outline[0] || "待补结构标题")}</strong>
        </div>
      </div>
      ${
        aiBriefing.entityPreview.length
          ? `<div class="editor-tags">${aiBriefing.entityPreview.map((entity) => `<span class="ai-chip">${escapeHtml(entity)}</span>`).join("")}</div>`
          : ""
      }
      <div class="detail-actions">
        ${aiCommands
          .map(
            (command) =>
              `<button class="${command.kind} detail-inline-button" data-ai-command="${escapeHtml(command.id)}" type="button">${escapeHtml(command.label)}</button>`,
          )
          .join("")}
      </div>
    </section>
    <div class="ai-stat-grid">
      <article class="ai-stat">
        <span class="metric-label">队列</span>
        <strong>${escapeHtml(note.ai.queueDepth)}</strong>
      </article>
      <article class="ai-stat">
        <span class="metric-label">告警</span>
        <strong>${escapeHtml(note.ai.warnings)}</strong>
      </article>
      ${
        draftInsight
          ? `
            <article class="ai-stat">
              <span class="metric-label">草稿</span>
              <strong>${escapeHtml(draftInsight.dirty ? "未保存" : "已对齐")}</strong>
            </article>
          `
          : ""
      }
    </div>
    ${
      draftInsight
        ? `
          <section class="ai-sync-box">
            <h3>草稿洞察</h3>
            <div class="ai-draft-grid">
              <div class="ai-draft-item">
                <span class="metric-label">当前字数</span>
                <strong>${escapeHtml(draftInsight.draftChars)}</strong>
              </div>
              <div class="ai-draft-item">
                <span class="metric-label">正文行数</span>
                <strong>${escapeHtml(draftInsight.lines)}</strong>
              </div>
              <div class="ai-draft-item">
                <span class="metric-label">相对变化</span>
                <strong>${escapeHtml(draftInsight.delta >= 0 ? `+${draftInsight.delta}` : draftInsight.delta)}</strong>
              </div>
            </div>
            <div class="detail-actions ai-inline-actions">
              ${
                draftInsight.dirty
                  ? `
                    <button class="solid detail-inline-button" data-ai-command="save-draft" type="button">保存草稿</button>
                    <button class="ghost detail-inline-button" data-ai-command="cancel-edit" type="button">取消编辑</button>
                  `
                  : `<button class="ghost detail-inline-button" data-ai-command="cancel-edit" type="button">结束编辑</button>`
              }
            </div>
          </section>
        `
        : ""
    }
    <section class="ai-sync-box">
      <h3>操作建议</h3>
      <ul class="ai-list">
        ${[...draftActions, ...syncContext.actions].map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </section>
    <section class="ai-section">
      <h3>本轮建议动作</h3>
      <div class="ai-action-list">
        ${note.ai.suggestions
          .map(
            (item, index) => `
              <article class="ai-action-card">
                <strong>${escapeHtml(item)}</strong>
                <p>${escapeHtml(draftInsight?.dirty ? "建议先处理草稿，再执行这条建议。" : "可作为当前文档的下一步处理动作。")}</p>
                <div class="detail-actions">
                  <button class="ghost detail-inline-button" data-ai-command="${index === 0 ? "apply-next-steps" : "apply-summary"}" type="button">${index === 0 ? "写入待办" : "写入摘要"}</button>
                </div>
              </article>
            `,
          )
          .join("")}
      </div>
    </section>
    <section class="ai-section">
      <h3>相关实体</h3>
      <div class="editor-tags">
        ${note.ai.relatedEntities
          .map(
            (entity) =>
              `<button class="ghost detail-inline-button" data-ai-entity="${escapeHtml(entity)}" type="button">${escapeHtml(entity)}</button>`,
          )
          .join("")}
      </div>
    </section>
    <section class="ai-section">
      <h3>风险与校对</h3>
      ${
        note.ai.lint.length
          ? `<div class="ai-action-list">${note.ai.lint
              .map(
                (item) => `
                  <article class="ai-action-card tone-danger-surface">
                    <strong>${escapeHtml(item)}</strong>
                    <p>建议在进入同步前先人工核对这一项。</p>
                  </article>
                `,
              )
              .join("")}</div>`
          : '<p class="ai-copy">当前没有额外的风险提示，可以继续整理内容。</p>'
      }
    </section>
    <section class="ai-section">
      <h3>相关同步活动</h3>
      ${
        syncContext.relatedActivity.length
          ? `<ul class="ai-list">${syncContext.relatedActivity
              .map(
                (record) =>
                  `<li>${escapeHtml(record.action_id)} · ${escapeHtml(formatStatusLabel(record.status))} · ${escapeHtml(formatActionSourceLabel(record.source))}${record.message ? ` · ${escapeHtml(record.message)}` : ""}</li>`,
              )
              .join("")}</ul>`
          : '<p class="ai-copy">这篇文档当前没有匹配到相关同步活动。</p>'
      }
    </section>
  `;

  for (const button of elements.workspaceAiPanel.querySelectorAll("[data-ai-command]")) {
    button.addEventListener("click", () => {
      const command = button.dataset.aiCommand;
      if (command === "start-edit") {
        ensureEditorDraftSession(state.selectedWorkspaceNoteId);
        render();
        return;
      }
      if (command === "apply-summary") {
        applyAiDraftMutation(
          state.selectedWorkspaceNoteId,
          ({ draft }) => ({
            ...draft,
            body: upsertMarkdownSection(
              draft.body,
              "AI 摘要",
              [`- 摘要：${aiBriefing.summary}`, `- 首要信号：${aiBriefing.topSignal}`, `- 当前建议：${aiBriefing.headline}`].join("\n"),
            ),
          }),
          {
            statusMessage: `已把 AI 摘要写入草稿：${note.title}`,
            historyDetail: `${note.title} · 写入 AI 摘要`,
          },
        );
        return;
      }
      if (command === "apply-outline") {
        applyAiDraftMutation(
          state.selectedWorkspaceNoteId,
          ({ draft }) => ({
            ...draft,
            body: buildMarkdownOutline(draft.body).length
              ? upsertMarkdownSection(
                  draft.body,
                  "结构补充",
                  [
                    ...aiBriefing.outline.map((item, index) => `${index + 1}. ${item}`),
                    ...aiBriefing.entityPreview.map((entity) => `- 可补充实体线索：${entity}`),
                  ].join("\n"),
                )
              : `${draft.body.trim()}\n\n${buildAiOutlineTemplate(note, syncContext)}\n`,
          }),
          {
            statusMessage: `已补充结构骨架：${note.title}`,
            historyDetail: `${note.title} · AI 补结构骨架`,
          },
        );
        return;
      }
      if (command === "apply-next-steps") {
        applyAiDraftMutation(
          state.selectedWorkspaceNoteId,
          ({ draft }) => ({
            ...draft,
            body: upsertMarkdownSection(
              draft.body,
              "下一步行动",
              syncContext.actions.slice(0, 4).map((item, index) => `${index + 1}. ${item}`).join("\n"),
            ),
          }),
          {
            statusMessage: `已写入下一步行动：${note.title}`,
            historyDetail: `${note.title} · 写入下一步行动`,
          },
        );
        return;
      }
      if (command === "create-followup") {
        createFollowUpNoteFromCurrent();
        return;
      }
      if (command === "save-draft") {
        saveEditingSelectedNote();
        return;
      }
      if (command === "cancel-edit") {
        cancelEditingSelectedNote();
      }
    });
  }
  for (const button of elements.workspaceAiPanel.querySelectorAll("[data-ai-entity]")) {
    button.addEventListener("click", () => {
      applySearchQuery(button.dataset.aiEntity || "");
      state.activeNavView = "graph";
      render();
    });
  }
}

function renderWorkspaceChrome() {
  elements.workspaceStatus.textContent = `工作区契约来源：${state.workspaceSourceLabel}`;
  syncSelectedWorkspaceNoteToSearch(state.workspaceShell || WORKSPACE_SAMPLE);
  renderWorkspaceTree();
  renderWorkspaceRail();
  renderWorkspaceEditor();
  renderWorkspaceAiPanel();
}

function buildPayloadDetail() {
  if (state.appSession) {
    return [
      formatPayloadKindLabel(state.appSession.payloadKind),
      `${state.appSession.workspaceSummary.noteCount} 篇文档`,
      formatDateTime(state.appSession.loadedAtMs),
    ].join(" | ");
  }

  if (!state.snapshotMetadata) {
    return state.sourceLabel;
  }

  const { generated_at_ms, vault_id, device_id, vault_root } = state.snapshotMetadata;
  return `${state.sourceLabel} | ${vault_id} | ${device_id} | ${vault_root} | ${formatDateTime(generated_at_ms)}`;
}

function renderPanel(syncCenter) {
  const panel = syncCenter.panel;
  const tone = resolveTone(panel.level);
  elements.panelCard.innerHTML = `
    <div class="panel-topline">
      <span class="level-pill tone-${tone}">${escapeHtml(formatLevelLabel(panel.level))}</span>
      <span class="mini-pill tone-info">冲突 ${escapeHtml(panel.conflict_badge_count)}</span>
      <span class="mini-pill tone-info">变更 ${escapeHtml(panel.change_badge_count)}</span>
    </div>
    <h2 class="panel-headline">${escapeHtml(panel.headline)}</h2>
    <p class="summary-copy">${escapeHtml(panel.detail)}</p>
    <div class="panel-actions" id="panel-actions"></div>
  `;

  const panelActions = elements.panelCard.querySelector("#panel-actions");
  panelActions.appendChild(createActionChip(panel.primary_action, "panel.primary_action"));
  for (const action of panel.secondary_actions || []) {
    panelActions.appendChild(createActionChip(action, "panel.secondary_actions"));
  }
}

function renderSummary(syncCenter) {
  const summary = syncCenter.summary;
  const metrics = [
    {
      label: "提交门禁",
      value: summary.commit_gate.can_submit_commit ? "可提交" : "被阻塞",
      kicker:
        summary.commit_gate.blocking_reasons?.map(formatBlockingReason).join("，") || "当前无阻塞原因",
    },
    {
      label: "本地变更",
      value: summary.changes.change_count ?? 0,
      kicker: `已扫描 ${summary.changes.tracked_record_count ?? 0} 条跟踪记录`,
    },
    {
      label: "冲突",
      value:
        (summary.conflicts.conflict_copies?.length || 0) +
        (summary.conflicts.conflict_orphans?.length || 0),
      kicker: summary.conflicts.actual_has_unresolved_conflicts
        ? "仍存在未解决的本地冲突工件"
        : "当前没有未解决的本地冲突工件",
    },
    {
      label: "版本",
      value: `${summary.state.acked_revision}/${summary.state.remote_head_revision}`,
      kicker: `Manifest 状态：${formatManifestStatusLabel(summary.state.last_manifest_summary_status)}`,
    },
  ];

  elements.summaryGrid.innerHTML = metrics
    .map(
      (metric) => `
        <article class="summary-item">
          <span class="metric-label">${escapeHtml(metric.label)}</span>
          <strong>${escapeHtml(metric.value)}</strong>
          <p class="summary-kicker">${escapeHtml(metric.kicker)}</p>
        </article>
      `,
    )
    .join("");
}

function renderCards(syncCenter) {
  const query = normalizeSearchQuery(state.searchQuery);
  const visibleCards = syncCenter.cards.filter((card) => {
    const matchesView =
      state.activeNavView !== "conflicts" ||
      card.kind === "conflicts" ||
      card.kind === "activity" ||
      card.card_id.includes("conflict");

    return (
      matchesView &&
      matchesSearchQuery(
        query,
        card.card_id,
        card.kind,
        card.title,
        card.body,
        card.actions?.map((action) => action.label).join(" "),
      )
    );
  });

  if (!visibleCards.length) {
    elements.cardsGrid.innerHTML = `
      <div class="empty-state">
        <p>${query ? `没有找到与“${escapeHtml(state.searchQuery)}”匹配的同步卡片。` : "当前载荷没有返回同步卡片。"}</p>
      </div>
    `;
    return;
  }

  elements.cardsGrid.innerHTML = "";
  for (const card of visibleCards) {
    const availableActions = (card.actions || []).filter((action) => action.enabled !== false).length;
    const blockedActions = (card.actions || []).filter((action) => action.enabled === false).length;
    const article = document.createElement("article");
    article.className = "sync-card";
    article.innerHTML = `
      <div class="card-title-row">
        <div>
          <p class="card-meta">${escapeHtml(formatCardKindLabel(card.kind))} · ${escapeHtml(formatCardSourceLabel(card.card_id))}</p>
          <h3>${escapeHtml(card.title)}</h3>
        </div>
        <span class="level-pill tone-${resolveTone(card.level)}">${escapeHtml(formatLevelLabel(card.level))}</span>
      </div>
      <p class="card-body">${escapeHtml(card.body)}</p>
      <p class="summary-kicker">${escapeHtml(`可执行动作 ${availableActions} 个${blockedActions ? ` · 暂不可执行 ${blockedActions} 个` : ""} · 提醒 ${card.badge_count} 项`)}</p>
      <div class="card-actions"></div>
    `;

    const actionsNode = article.querySelector(".card-actions");
    for (const action of card.actions || []) {
      actionsNode.appendChild(createActionChip(action, `card:${card.card_id}`));
    }

    elements.cardsGrid.appendChild(article);
  }
}

function renderActivity(feed) {
  const query = normalizeSearchQuery(state.searchQuery);
  const lastExecution = buildLastExecutionSummary();
  const records = Array.isArray(feed?.records)
    ? feed.records.filter((record) =>
        matchesSearchQuery(query, record.action_id, record.command, record.status, record.source, record.message),
      )
    : [];

  if (!records.length) {
    elements.activityCard.innerHTML = `
      <div class="activity-header">
        <div>
          <h2>最近活动</h2>
          <p class="activity-empty">
            ${query ? `没有找到与“${escapeHtml(state.searchQuery)}”匹配的同步活动。` : "当前还没有加载任何同步活动记录。"}
          </p>
        </div>
      </div>
    `;
    return;
  }

  const latestRecord = records[records.length - 1];
  elements.activityCard.innerHTML = `
    <div class="activity-header">
      <div>
        <h2>最近活动</h2>
        <p class="summary-copy">
          已加载 ${escapeHtml(feed.total_count)} 条记录，最近一次发生于
          ${escapeHtml(formatDateTime(latestRecord.occurred_at_ms))}.
        </p>
        ${
          lastExecution
            ? `<p class="summary-copy">最近桥接执行：${escapeHtml(lastExecution.actionId)} · ${escapeHtml(lastExecution.statusLabel)} · ${escapeHtml(lastExecution.atLabel)}</p>`
            : ""
        }
      </div>
      <span class="level-pill tone-${resolveTone(latestRecord.level)}">
        ${escapeHtml(formatStatusLabel(latestRecord.status))}
      </span>
    </div>
    <div class="timeline"></div>
  `;

  const timeline = elements.activityCard.querySelector(".timeline");
  for (const record of [...records].reverse()) {
    const actionLabel = formatSyncActionLabel(record.action_id, record.command);
    const sourceLabel = formatActionSourceLabel(record.source);
    const activityMessage = formatActivityMessage(record.message);
    const item = document.createElement("article");
    item.className = "timeline-item";
    item.innerHTML = `
      <div class="timeline-row">
        <span class="timeline-title">${escapeHtml(actionLabel)}</span>
        <span class="timeline-time">${escapeHtml(formatDateTime(record.occurred_at_ms))}</span>
      </div>
      <div class="timeline-row">
        <span class="mini-pill tone-${resolveTone(record.level)}">${escapeHtml(formatStatusLabel(record.status))}</span>
        <span class="card-meta">${escapeHtml(`${sourceLabel} · ${formatLevelLabel(record.level)}`)}</span>
      </div>
      ${activityMessage ? `<p class="timeline-message">${escapeHtml(activityMessage)}</p>` : ""}
    `;
    timeline.appendChild(item);
  }
}

function renderEmptyDashboard(message) {
  renderViewModeCard();
  renderViewDetailGrid();
  renderNavViewVisibility();
  renderControlCenterMode();
  elements.payloadKind.textContent = "未加载";
  elements.payloadDetail.textContent = message;
  elements.panelCard.innerHTML = `
    <div class="empty-state">
      <p>${escapeHtml(message)}</p>
      <p>${escapeHtml(state.localUiSettings.expertMode ? "你也可以继续留在高级调试模式下手动导入契约，但普通使用建议先从内置会话开始。" : "建议先加载一个工作会话，再进入仓库浏览、编辑器和同步工作台。")}</p>
      <div class="detail-actions">
        <button class="solid detail-inline-button" data-empty-command="load-sample-session" type="button">加载演示会话</button>
        <button class="ghost detail-inline-button" data-empty-command="refresh-session" type="button">刷新本地会话</button>
        <button class="ghost detail-inline-button" data-empty-command="open-settings" type="button">打开设置</button>
      </div>
    </div>
  `;
  elements.summaryGrid.innerHTML = "";
  elements.cardsGrid.innerHTML = "";
  elements.activityCard.innerHTML = `
    <div class="empty-state">
      <p>加载同步载荷后，这里才会显示同步看板。</p>
    </div>
  `;
  renderExecutionResult(state.lastExecution);
  setSelectedAction(null);
  for (const button of elements.panelCard.querySelectorAll("[data-empty-command]")) {
    button.addEventListener("click", async () => {
      const command = button.dataset.emptyCommand;
      if (command === "load-sample-session") {
        try {
          await loadSampleAppSession();
        } catch (error) {
          renderEmptyDashboard(error instanceof Error ? error.message : String(error));
        }
        return;
      }
      if (command === "refresh-session") {
        try {
          await refreshFullAppSession();
        } catch (error) {
          state.lastBridgeError = normalizeBridgeError(error);
          elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
          renderBridgeError(state.lastBridgeError);
        }
        return;
      }
      if (command === "open-settings") {
        state.activeNavView = "settings";
        render();
      }
    });
  }
}

function render() {
  renderViewModeCard();
  renderViewDetailGrid();
  renderNavViewVisibility();
  renderControlCenterMode();
  renderBridgeStatus();
  renderExecutionResult(state.lastExecution);
  renderActionExecutionPanel();
  renderWorkspaceChrome();

  if (!state.syncCenter && !state.activityFeed) {
    renderEmptyDashboard("当前还没有进入任何同步工作会话。");
    return;
  }

  elements.payloadKind.textContent = state.appSession
    ? `${formatSessionSourceLabel(state.appSession.source)}会话`
    : state.snapshotMetadata
      ? formatPayloadKindLabel("sync-shell-snapshot")
      : state.syncCenter
        ? formatPayloadKindLabel("sync-center")
        : formatPayloadKindLabel("sync-activity");
  elements.payloadDetail.textContent = buildPayloadDetail();

  if (state.syncCenter) {
    renderPanel(state.syncCenter);
    renderSummary(state.syncCenter);
    renderCards(state.syncCenter);
    renderActivity(state.activityFeed || state.syncCenter.recent_activity);
  } else {
    elements.panelCard.innerHTML = `
      <div class="empty-state">
        <p>
          当前只加载了活动流。若要继续查看同步总览、阻塞摘要和下一步动作，建议刷新一次完整会话。
        </p>
        <div class="detail-actions">
          <button class="solid detail-inline-button" data-activity-only-command="refresh-session" type="button">刷新完整会话</button>
          <button class="ghost detail-inline-button" data-activity-only-command="open-settings" type="button">打开设置</button>
        </div>
      </div>
    `;
    for (const button of elements.panelCard.querySelectorAll("[data-activity-only-command]")) {
      button.addEventListener("click", async () => {
        if (button.dataset.activityOnlyCommand === "refresh-session") {
          try {
            await refreshFullAppSession();
          } catch (error) {
            state.lastBridgeError = normalizeBridgeError(error);
            elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
            renderBridgeError(state.lastBridgeError);
          }
          return;
        }
        if (button.dataset.activityOnlyCommand === "open-settings") {
          state.activeNavView = "settings";
          render();
        }
      });
    }
    elements.summaryGrid.innerHTML = "";
    elements.cardsGrid.innerHTML = "";
    renderActivity(state.activityFeed);
  }
  refreshActionChipSelection();
}

async function requestBridgeStatus() {
  try {
    state.bridgeStatus = await fetchBridgeStatus();
    state.bridgeCheckedAtMs = Date.now();
  } catch (error) {
    state.bridgeStatus = buildOfflineBridgeStatus(error instanceof Error ? error : null);
    state.bridgeCheckedAtMs = Date.now();
  }
  renderBridgeStatus();
}

function applyPayload(payload, sourceLabel) {
  const kind = detectPayloadKind(payload);
  state.sourceLabel = sourceLabel;
  state.appSession = null;

  if (kind === "sync-shell-snapshot") {
    state.snapshotMetadata = {
      generated_at_ms: payload.generated_at_ms,
      vault_id: payload.vault_id,
      device_id: payload.device_id,
      vault_root: payload.vault_root,
    };
    state.syncCenter = payload.sync_center;
    state.activityFeed = payload.activity_feed || payload.sync_center.recent_activity || null;
  } else if (kind === "sync-center") {
    state.snapshotMetadata = null;
    state.syncCenter = payload;
    state.activityFeed = payload.recent_activity || null;
  } else {
    state.snapshotMetadata = null;
    state.syncCenter = null;
    state.activityFeed = payload;
  }

  render();
}

async function loadPayloadFromPath(path, sourceLabel) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`无法加载同步载荷 ${path}：HTTP ${response.status}`);
  }

  const payload = await response.json();
  elements.payloadInput.value = JSON.stringify(payload, null, 2);
  applyPayload(payload, sourceLabel);
}

function resolveInitialPayloadPath() {
  const search = new URLSearchParams(window.location.search);
  return search.get("payload");
}

async function loadSample() {
  await loadPayloadFromPath(SAMPLE_PATH, "演示同步快照");
}

async function loadLiveSnapshot() {
  await loadPayloadFromPath(LIVE_SNAPSHOT_PATH, "本地导出的实时同步快照");
}

async function loadWorkspaceShellFromPath(path, sourceLabel) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`无法加载工作区壳层 ${path}：HTTP ${response.status}`);
  }

  const payload = await response.json();
  applyWorkspaceShell(payload, sourceLabel);
}

function resolveInitialWorkspacePath() {
  const search = new URLSearchParams(window.location.search);
  return search.get("workspace");
}

function resolveInitialSessionMode() {
  const search = new URLSearchParams(window.location.search);
  return search.get("session");
}

function applyWorkspaceShell(payload, sourceLabel) {
  const workspaceShell = validateWorkspaceShell(payload);
  state.workspaceShell = workspaceShell;
  state.workspaceSourceLabel = sourceLabel;
  state.appSession = null;
  pruneEditorDrafts(workspaceShell);
  refreshRecoveryDrafts();
  if (!workspaceShell.notes[state.selectedWorkspaceNoteId]) {
    state.selectedWorkspaceNoteId = Object.keys(workspaceShell.notes)[0] || "desktop-bridge";
  }
  elements.workspaceInput.value = JSON.stringify(workspaceShell, null, 2);
  persistLocalWorkspaceSession();
  render();
  elements.actionExecutionStatus.textContent = `工作区契约来源：${sourceLabel}`;
}

function applyAppSession(payload, sourceLabel) {
  const session = validateAppSession(payload);
  state.appSession = buildAppSessionState(session);
  applyWorkspaceShell(session.workspaceShell, `${sourceLabel} / workspace`);
  applyPayload(session.syncPayload, `${sourceLabel} / sync`);
  state.appSession = buildAppSessionState(session);
  if (session.bridgeStatus) {
    state.bridgeStatus = session.bridgeStatus;
    state.bridgeCheckedAtMs = session.loadedAtMs;
  }
  state.lastBridgeError = null;
  elements.actionExecutionStatus.textContent =
    `应用会话来源：${formatSessionSourceLabel(session.source)} · ${formatDateTime(session.loadedAtMs)}`;
  pushSessionHistory({
    type: "app_session_loaded",
    level: session.bridgeStatus?.available ? "success" : "warning",
    detail: `${formatSessionSourceLabel(session.source)} · ${formatPayloadKindLabel(state.appSession.payloadKind)} · ${state.appSession.workspaceSummary.noteCount} 篇文档`,
  });
}

async function refreshFromDesktop() {
  const json = await refreshBridgeSnapshot({});
  state.lastBridgeError = null;
  state.lastExecution = null;
  state.lastExecutionAtMs = null;
  elements.payloadInput.value = JSON.stringify(json.snapshot, null, 2);
  applyPayload(json.snapshot, "通过本地桌面桥接刷新同步快照");
  pushSessionHistory({
    type: "bridge_snapshot_refreshed",
    level: "info",
    detail: "已通过本地桌面桥接拉取最新同步快照。",
  });
}

async function loadSampleAppSession() {
  const session = await fetchSampleAppSession();
  applyAppSession(session, "内置应用会话");
}

async function refreshFullAppSession() {
  const session = await refreshAppSession({});
  applyAppSession(session, "桌面桥接应用会话");
}

async function executeSelectedAction() {
  if (!state.selectedAction) {
    throw new Error("执行前请先选择一个动作");
  }

  const actionId = state.selectedAction.action_id;
  const json = await executeBridgeAction(actionId);

  state.lastBridgeError = null;
  state.lastExecution = json.execution;
  state.lastExecutionAtMs = Date.now();
  elements.payloadInput.value = JSON.stringify(json.snapshot, null, 2);
  applyPayload(json.snapshot, `通过本地桌面桥接执行动作：${actionId}`);
  elements.actionExecutionStatus.textContent =
    `动作 ${json.execution.action.action_id} 已执行，状态为 ${json.execution.status}。`;
  pushSessionHistory({
    type: "sync_action_executed",
    level: json.execution.status === "failed" ? "danger" : "success",
    detail: `${json.execution.action.action_id} · ${formatStatusLabel(json.execution.status)}`,
  });
}

async function loadInitialPayload() {
  const explicitPath = resolveInitialPayloadPath();
  if (explicitPath) {
    await loadPayloadFromPath(explicitPath, `从 ${explicitPath} 加载`);
    return;
  }

  try {
    await loadLiveSnapshot();
  } catch {
    await loadSample();
  }
}

async function loadInitialSession() {
  const explicitPayloadPath = resolveInitialPayloadPath();
  const explicitWorkspacePath = resolveInitialWorkspacePath();
  const sessionMode = resolveInitialSessionMode();
  const localWorkspaceSession = readLocalWorkspaceSession();

  if (explicitPayloadPath || explicitWorkspacePath) {
    if (explicitWorkspacePath) {
      await loadWorkspaceShellFromPath(explicitWorkspacePath, explicitWorkspacePath);
    } else {
      await loadWorkspaceShellFromPath(WORKSPACE_SAMPLE_PATH, "演示工作区");
    }

    if (explicitPayloadPath) {
      await loadPayloadFromPath(explicitPayloadPath, `从 ${explicitPayloadPath} 加载`);
    } else {
      await loadInitialPayload();
    }
    return;
  }

  if (localWorkspaceSession) {
    state.selectedWorkspaceNoteId = localWorkspaceSession.selectedWorkspaceNoteId;
    applyWorkspaceShell(localWorkspaceSession.workspaceShell, localWorkspaceSession.workspaceSourceLabel);
    elements.workspaceStatus.textContent =
      `已恢复本地工作区：${formatDateTime(localWorkspaceSession.savedAtMs)} 的浏览器草稿会话`;
    pushSessionHistory({
      type: "workspace_session_restored",
      level: "success",
      detail: `${localWorkspaceSession.workspaceSourceLabel} · ${formatDateTime(localWorkspaceSession.savedAtMs)}`,
    });
    try {
      await loadInitialPayload();
    } catch {
      // Keep the restored local workspace visible even if sync data is unavailable.
    }
    return;
  }

  if (sessionMode === "live") {
    try {
      await refreshFullAppSession();
      return;
    } catch {
      await loadSampleAppSession();
      return;
    }
  }

  await loadSampleAppSession();
}

function applyTextareaPayload() {
  const raw = elements.payloadInput.value.trim();
  if (!raw) {
    renderEmptyDashboard("请先粘贴 sync-shell-snapshot、sync-center 或 sync-activity JSON。");
    return;
  }

  state.lastBridgeError = null;
  state.lastExecution = null;
  state.lastExecutionAtMs = null;
  applyPayload(JSON.parse(raw), "文本框同步 JSON");
}

async function importLocalFile(file) {
  const text = await file.text();
  elements.payloadInput.value = text;
  state.lastBridgeError = null;
  state.lastExecution = null;
  state.lastExecutionAtMs = null;
  applyPayload(JSON.parse(text), `导入文件：${file.name}`);
}

function applyWorkspaceTextareaPayload() {
  const raw = elements.workspaceInput.value.trim();
  if (!raw) {
    throw new Error("请先粘贴工作区壳层 JSON。");
  }
  applyWorkspaceShell(JSON.parse(raw), "文本框工作区 JSON");
}

async function importWorkspaceFile(file) {
  const text = await file.text();
  applyWorkspaceShell(JSON.parse(text), `导入工作区文件：${file.name}`);
}

function ensureEditableWorkspaceShell() {
  if (!state.workspaceShell) {
    state.workspaceShell = cloneJson(WORKSPACE_SAMPLE);
    state.workspaceSourceLabel = "浏览器本地草稿";
  }
  state.appSession = null;
  return state.workspaceShell;
}

function startEditingSelectedNote() {
  const workspaceShell = ensureEditableWorkspaceShell();
  const note = workspaceShell.notes[state.selectedWorkspaceNoteId];
  if (!note) {
    return;
  }
  setEditorDraftForNote(state.selectedWorkspaceNoteId, {
    noteId: state.selectedWorkspaceNoteId,
    title: getEditorDraftByNoteId(state.selectedWorkspaceNoteId)?.title || note.title,
    body: getEditorDraftByNoteId(state.selectedWorkspaceNoteId)?.body || note.body,
  });
  elements.workspaceInput.value = JSON.stringify(workspaceShell, null, 2);
  render();
}

function cancelEditingSelectedNote() {
  discardPersistedRecoveryDraft(state.selectedWorkspaceNoteId);
  removeEditorDraftForNote(state.selectedWorkspaceNoteId);
  render();
}

function saveEditingSelectedNote() {
  const activeDraft = getActiveEditorDraft();
  if (!activeDraft) {
    return;
  }

  const workspaceShell = ensureEditableWorkspaceShell();
  const note = workspaceShell.notes[activeDraft.noteId];
  const item = findWorkspaceItemById(workspaceShell, activeDraft.noteId);
  if (!note) {
    return;
  }

  const trimmedTitle = activeDraft.title.trim() || note.title;
  const trimmedBody = activeDraft.body.trim();
  note.title = trimmedTitle;
  note.body = trimmedBody || note.body;
  note.lastSaved = "刚刚保存";
  note.statusTone = "info";
  note.statusLabel = "本地草稿已更新";
  if (!Array.isArray(note.tags)) {
    note.tags = [];
  }
  if (!note.tags.includes("draft")) {
    note.tags = [...note.tags, "draft"];
  }

  if (item) {
    item.title = trimmedTitle;
    item.status = "本地草稿已更新";
  }

  state.workspaceSourceLabel = "浏览器本地草稿";
  discardPersistedRecoveryDraft(activeDraft.noteId);
  removeEditorDraftForNote(activeDraft.noteId);
  elements.workspaceInput.value = JSON.stringify(workspaceShell, null, 2);
  persistLocalWorkspaceSession();
  elements.workspaceStatus.textContent = `已保存文档：${trimmedTitle}`;
  pushSessionHistory({
    type: "workspace_note_saved",
    level: "success",
    detail: `${trimmedTitle} · 本地草稿已更新`,
  });
  render();
}

function buildQuickCaptureTitle(now = new Date()) {
  const timestamp = new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
    .format(now)
    .replaceAll("/", "-")
    .replaceAll(":", "-")
    .replace(" ", "-");
  return `快速记录 ${timestamp}`;
}

function ensureInboxSection(workspaceShell) {
  let inboxSection = workspaceShell.sections.find((section) => section.id === "inbox");
  if (!inboxSection) {
    inboxSection = {
      id: "inbox",
      label: "收件箱",
      items: [],
    };
    workspaceShell.sections.unshift(inboxSection);
  }
  return inboxSection;
}

function createFollowUpNoteFromCurrent() {
  const currentNote = getSelectedWorkspaceNote();
  if (!currentNote) {
    return;
  }
  const syncContext = deriveWorkspaceSyncContext(currentNote);
  const noteBody = getActiveEditorDraft()?.body || currentNote.body;
  const aiBriefing = buildAiBriefing({
    note: currentNote,
    noteBody,
    syncContext,
    draftInsight: buildEditorDraftInsight(currentNote, getActiveEditorDraft()),
  });
  const workspaceShell = ensureEditableWorkspaceShell();
  const followUpId = `follow-up-${Date.now()}`;
  const title = `跟进：${currentNote.title}`;
  const inboxPath = `Inbox/${title}.md`;
  const inboxSection = ensureInboxSection(workspaceShell);

  inboxSection.items.unshift({
    id: followUpId,
    title,
    path: inboxPath,
    status: "跟进草稿",
  });
  workspaceShell.notes[followUpId] = {
    title,
    path: inboxPath,
    statusTone: "info",
    statusLabel: "跟进草稿",
    lastSaved: "刚刚创建",
    tags: ["follow-up", "draft", "inbox"],
    syncContext: {
      watchActionIds: ["detect-local-changes", "show-vault-summary"],
      watchCardKinds: ["changes", "activity"],
      watchBlockingReasons: ["requires_full_pull"],
    },
    body: `# ${title}

## 来源文档
- 标题：${currentNote.title}
- 路径：${currentNote.path}
- 首要信号：${aiBriefing.topSignal}

## 跟进摘要
${aiBriefing.summary}

## 跟进动作
${syncContext.actions.slice(0, 3).map((item, index) => `${index + 1}. ${item}`).join("\n")}
`,
    ai: {
      queueDepth: 0,
      warnings: 0,
      relatedEntities: ["跟进笔记", currentNote.title, ...(currentNote.ai?.relatedEntities || []).slice(0, 2)],
      suggestions: ["先把跟进项补成可执行清单，再决定是否纳入正式知识页。"],
      lint: [],
    },
  };

  state.selectedWorkspaceNoteId = followUpId;
  state.workspaceSourceLabel = "浏览器本地草稿";
  setEditorDraftForNote(followUpId, {
    noteId: followUpId,
    title,
    body: workspaceShell.notes[followUpId].body,
  });
  resetSearchQuery();
  elements.workspaceInput.value = JSON.stringify(workspaceShell, null, 2);
  persistLocalWorkspaceSession();
  elements.workspaceStatus.textContent = `已创建跟进笔记：${title}`;
  pushSessionHistory({
    type: "workspace_followup_created",
    level: "info",
    detail: `${title} · 来源 ${currentNote.title}`,
  });
  render();
}

function createQuickCaptureNote() {
  const workspaceShell = ensureEditableWorkspaceShell();
  const draftId = `quick-capture-${Date.now()}`;
  const title = buildQuickCaptureTitle();
  const inboxPath = `Inbox/${title}.md`;
  const inboxSection = ensureInboxSection(workspaceShell);

  inboxSection.items.unshift({
    id: draftId,
    title,
    path: inboxPath,
    status: "本地草稿",
  });
  workspaceShell.notes[draftId] = {
    title,
    path: inboxPath,
    statusTone: "info",
    statusLabel: "本地草稿",
    lastSaved: "刚刚创建",
    tags: ["capture", "draft", "inbox"],
    syncContext: {
      watchActionIds: ["detect-local-changes", "submit-detected-commit"],
      watchCardKinds: ["changes", "activity"],
      watchBlockingReasons: ["requires_full_pull"],
    },
    body: `# ${title}

## 记录要点
- 在这里补充新的笔记内容。
- 后续可把这条快速记录整理进正式知识页。

## 下一步
1. 补充上下文。
2. 决定是否进入同步周期。`,
    ai: {
      queueDepth: 0,
      warnings: 0,
      relatedEntities: ["快速记录", "收件箱", "同步周期"],
      suggestions: ["把零散记录整理成结构化笔记，再决定是否提交同步。"],
      lint: [],
    },
  };

  state.selectedWorkspaceNoteId = draftId;
  state.workspaceSourceLabel = "浏览器本地草稿";
  setEditorDraftForNote(draftId, {
    noteId: draftId,
    title,
    body: workspaceShell.notes[draftId].body,
  });
  resetSearchQuery();
  elements.workspaceInput.value = JSON.stringify(workspaceShell, null, 2);
  persistLocalWorkspaceSession();
  elements.workspaceStatus.textContent = `已创建本地草稿：${title}`;
  pushSessionHistory({
    type: "quick_capture_created",
    level: "info",
    detail: `${title} · Inbox`,
  });
  render();
}

elements.searchInput.addEventListener("input", (event) => {
  state.searchQuery = event.target.value;
  render();
});

elements.searchInput.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.target.value = "";
    state.searchQuery = "";
    render();
  }
});

elements.quickCaptureButton.addEventListener("click", () => {
  createQuickCaptureNote();
});

elements.newNoteButton.addEventListener("click", () => {
  createQuickCaptureNote();
});

elements.toggleAdvancedControlsButton?.addEventListener("click", () => {
  persistLocalUiSettings({
    ...state.localUiSettings,
    expertMode: !state.localUiSettings.expertMode,
  });
  elements.workspaceStatus.textContent = state.localUiSettings.expertMode
    ? "已开启高级调试模式。"
    : "已切回普通工作台模式。";
  render();
});

for (const button of elements.navButtons) {
  button.addEventListener("click", () => {
    state.activeNavView = button.dataset.navView || "overview";
    render();
  });
}

elements.loadSampleButton.addEventListener("click", async () => {
  try {
    state.lastBridgeError = null;
    state.lastExecution = null;
    state.lastExecutionAtMs = null;
    await loadSample();
  } catch (error) {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
  }
});

elements.loadAppSessionButton.addEventListener("click", async () => {
  try {
    await loadSampleAppSession();
  } catch (error) {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
  }
});

elements.refreshAppSessionButton.addEventListener("click", async () => {
  try {
    await refreshFullAppSession();
  } catch (error) {
    state.lastBridgeError = normalizeBridgeError(error);
    elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
    renderBridgeError(state.lastBridgeError);
  }
});

elements.loadLiveButton.addEventListener("click", async () => {
  try {
    state.lastBridgeError = null;
    state.lastExecution = null;
    state.lastExecutionAtMs = null;
    await loadLiveSnapshot();
  } catch (error) {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
  }
});

elements.loadWorkspaceSampleButton.addEventListener("click", async () => {
  try {
    await loadWorkspaceShellFromPath(WORKSPACE_SAMPLE_PATH, "演示工作区");
  } catch (error) {
    elements.workspaceStatus.textContent = error instanceof Error ? error.message : String(error);
  }
});

elements.refreshLocalButton.addEventListener("click", async () => {
  try {
    await refreshFromDesktop();
  } catch (error) {
    state.lastBridgeError = normalizeBridgeError(error);
    elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
    renderBridgeStatus();
  }
});

elements.reloadBridgeStatusButton.addEventListener("click", async () => {
  try {
    await requestBridgeStatus();
    state.lastBridgeError = null;
    renderBridgeStatus();
  } catch (error) {
    state.lastBridgeError = normalizeBridgeError(error);
    renderBridgeStatus();
  }
});

elements.applyInputButton.addEventListener("click", () => {
  try {
    applyTextareaPayload();
  } catch (error) {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
  }
});

elements.applyWorkspaceInputButton.addEventListener("click", () => {
  try {
    applyWorkspaceTextareaPayload();
  } catch (error) {
    elements.workspaceStatus.textContent = error instanceof Error ? error.message : String(error);
  }
});

elements.clearInputButton.addEventListener("click", () => {
  elements.payloadInput.value = "";
  for (const timerId of draftAutosaveTimers.values()) {
    window.clearTimeout(timerId);
  }
  draftAutosaveTimers.clear();
  state.syncCenter = null;
  state.activityFeed = null;
  state.snapshotMetadata = null;
  state.appSession = null;
  state.editorDrafts = {};
  state.draftRecoveryMeta = {};
  state.selectedAction = null;
  state.selectedActionSource = null;
  state.lastBridgeError = null;
  state.lastExecution = null;
  state.lastExecutionAtMs = null;
  state.sourceLabel = "未加载";
  render();
});

elements.fileInput.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) {
    return;
  }

  try {
    await importLocalFile(file);
  } catch (error) {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
  } finally {
    event.target.value = "";
  }
});

elements.workspaceFileInput.addEventListener("change", async (event) => {
  const file = event.target.files?.[0];
  if (!file) {
    return;
  }

  try {
    await importWorkspaceFile(file);
  } catch (error) {
    elements.workspaceStatus.textContent = error instanceof Error ? error.message : String(error);
  } finally {
    event.target.value = "";
  }
});

elements.executeSelectedButton.addEventListener("click", async () => {
  try {
    await executeSelectedAction();
  } catch (error) {
    state.lastBridgeError = normalizeBridgeError(error);
    elements.actionExecutionStatus.textContent = state.lastBridgeError.message;
    renderBridgeError(state.lastBridgeError);
  }
});

window.addEventListener("beforeunload", (event) => {
  if (!collectDirtyEditorDrafts().length) {
    return;
  }
  event.preventDefault();
  event.returnValue = "";
});

loadLocalUiSettings();
refreshRecoveryDrafts();
render();
startBridgeStatusPolling({
  intervalMs: 15000,
  onStatus(status) {
    state.bridgeStatus = status;
    state.bridgeCheckedAtMs = Date.now();
    renderBridgeStatus();
  },
  onError(error) {
    state.bridgeStatus = buildOfflineBridgeStatus(
      error && typeof error === "object" && typeof error.message === "string"
        ? new Error(error.message)
        : null,
    );
    state.bridgeCheckedAtMs = Date.now();
    renderBridgeStatus();
  },
});
loadInitialSession().catch(async (error) => {
  try {
    state.workspaceShell = null;
    state.workspaceSourceLabel = "演示工作区";
    elements.workspaceInput.value = JSON.stringify(WORKSPACE_SAMPLE, null, 2);
    renderWorkspaceChrome();
    await loadInitialPayload();
  } catch {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
  }
});

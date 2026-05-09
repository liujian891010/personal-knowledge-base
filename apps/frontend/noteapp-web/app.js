import {
  executeBridgeAction,
  fetchSampleAppSession,
  fetchBridgeStatus,
  refreshAppSession,
  refreshBridgeSnapshot,
  startBridgeStatusPolling,
} from "./bridge-client.js";

const SAMPLE_PATH = "./fixtures/sync-shell-snapshot.sample.json";
const LIVE_SNAPSHOT_PATH = "./fixtures/live-sync-shell.json";
const WORKSPACE_SAMPLE_PATH = "./fixtures/workspace-shell.sample.json";
const DEFAULT_ACTION_COMMAND = "pkb-desktop-sync";
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
  bridgeStatus: null,
  bridgeCheckedAtMs: null,
  lastBridgeError: null,
  lastExecution: null,
  sourceLabel: "未加载",
};

const elements = {
  payloadKind: document.getElementById("payload-kind"),
  payloadDetail: document.getElementById("payload-detail"),
  panelCard: document.getElementById("panel-card"),
  summaryGrid: document.getElementById("summary-grid"),
  cardsGrid: document.getElementById("cards-grid"),
  activityCard: document.getElementById("activity-card"),
  actionContractOutput: document.getElementById("action-contract-output"),
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
  workspaceEditor: document.getElementById("workspace-editor"),
  workspaceAiPanel: document.getElementById("workspace-ai-panel"),
};

function formatDateTime(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "Unknown time";
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
    "Unsupported payload shape. Expected sync-shell-snapshot, sync-center, or sync-activity JSON.",
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

  throw new Error("Unsupported workspace shell shape. Expected { sections: [], notes: {} }.");
}

function validateAppSession(payload) {
  if (
    payload &&
    typeof payload === "object" &&
    payload.syncPayload &&
    payload.workspaceShell &&
    typeof payload.source === "string"
  ) {
    return payload;
  }

  throw new Error("Unsupported app session shape.");
}

function renderExecutionResult(execution) {
  if (!execution) {
    elements.actionResultOutput.textContent = "尚未执行任何动作。";
    return;
  }
  elements.actionResultOutput.textContent = JSON.stringify(execution, null, 2);
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
    const sourceLabel = status.config?.configSource || "env";
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

function setSelectedAction(action, source = "manual selection") {
  state.selectedAction = action;

  if (!action) {
    elements.actionContractHelp.textContent =
      "请在面板、卡片或活动流中先选择一个动作，这里会展示它对应的执行契约。";
    elements.actionContractOutput.textContent = "尚未选择任何动作。";
    elements.actionExecutionStatus.textContent =
      "只有本地桌面桥接配置完整后，才能真正执行动作。";
    elements.executeSelectedButton.disabled = true;
    return;
  }

  const commandLine = [DEFAULT_ACTION_COMMAND, action.command, ...(action.argv || [])].join(" ");
  const lines = [
    `来源: ${source}`,
    `动作 ID: ${action.action_id}`,
    `可执行: ${action.enabled !== false}`,
    `命令: ${action.command}`,
    `参数: ${JSON.stringify(action.argv || [])}`,
    `需要确认: ${Boolean(action.requires_confirmation)}`,
    `Shell 契约: ${commandLine}`,
  ];

  if (action.reason) {
    lines.push(`原因: ${action.reason}`);
  }

  elements.actionContractHelp.textContent =
    "前端不会直接执行动作；这里只展示会被本地 bridge 转发的精确执行契约。";
  elements.actionContractOutput.textContent = lines.join("\n");
  elements.actionExecutionStatus.textContent =
    "点击“执行当前动作”后，会通过本地桌面 bridge 转发该契约。";
  elements.executeSelectedButton.disabled = !state.bridgeStatus?.available;
}

function createActionChip(action, source) {
  const fragment = elements.actionChipTemplate.content.cloneNode(true);
  const button = fragment.querySelector(".action-chip");
  const label = fragment.querySelector(".action-label");
  const command = fragment.querySelector(".action-command");

  label.textContent = action.label;
  command.textContent = [action.command, ...(action.argv || [])].join(" ");

  if (action.enabled === false) {
    button.classList.add("is-disabled");
  }

  button.addEventListener("click", () => setSelectedAction(action, source));
  return fragment;
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
    stale: "过期",
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
  }[kind] || kind;
}

function createSignal(level, label) {
  return {
    level: resolveTone(level),
    label,
  };
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
    signals.push(createSignal(card.level, `${card.kind}: ${card.title}`));
  }

  for (const reason of matchedBlockingReasons) {
    signals.push(createSignal("danger", `提交门禁：${reason}`));
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

function getSelectedWorkspaceNote() {
  const workspaceShell = state.workspaceShell || WORKSPACE_SAMPLE;
  return workspaceShell.notes[state.selectedWorkspaceNoteId] || workspaceShell.notes["desktop-bridge"];
}

function renderWorkspaceTree() {
  const workspaceShell = state.workspaceShell || WORKSPACE_SAMPLE;
  elements.workspaceTree.innerHTML = workspaceShell.sections
    .map(
      (section) => `
        <section class="tree-section">
          <h3>${escapeHtml(section.label)}</h3>
          <div class="tree-list">
            ${section.items
              .map(
                (item) => `
                  <button
                    class="tree-node ${item.id === state.selectedWorkspaceNoteId ? "is-active" : ""}"
                    data-note-id="${escapeHtml(item.id)}"
                    type="button"
                  >
                    <span class="tree-node-title">${escapeHtml(item.title)}</span>
                    <span class="tree-node-path">${escapeHtml(item.path)}</span>
                    <span class="tree-node-path">${escapeHtml(item.status)}</span>
                    <span class="tree-node-signals" data-signal-host="${escapeHtml(item.id)}"></span>
                  </button>
                `,
              )
              .join("")}
          </div>
        </section>
      `,
    )
    .join("");

  for (const button of elements.workspaceTree.querySelectorAll("[data-note-id]")) {
    button.addEventListener("click", () => {
      state.selectedWorkspaceNoteId = button.dataset.noteId;
      renderWorkspaceChrome();
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

function renderWorkspaceEditor() {
  const note = getSelectedWorkspaceNote();
  const syncContext = deriveWorkspaceSyncContext(note);
  elements.workspaceEditor.innerHTML = `
    <div class="editor-toolbar">
      <div>
        <p class="card-meta">编辑器原型</p>
        <h2 class="editor-title">${escapeHtml(note.title)}</h2>
      </div>
      <span class="level-pill tone-${resolveTone(note.statusTone)}">${escapeHtml(note.statusLabel)}</span>
    </div>
    <div class="editor-meta-row">
      <span class="mini-pill tone-info">${escapeHtml(note.path)}</span>
      <span class="mini-pill tone-info">${escapeHtml(note.lastSaved)}</span>
    </div>
    <ul class="editor-tags">
      ${note.tags.map((tag) => `<li>${escapeHtml(tag)}</li>`).join("")}
    </ul>
    <section class="editor-sync-box">
      <h3>同步信号</h3>
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
    <pre class="editor-body">${escapeHtml(note.body)}</pre>
  `;
}

function renderWorkspaceAiPanel() {
  const note = getSelectedWorkspaceNote();
  const syncContext = deriveWorkspaceSyncContext(note);
  elements.workspaceAiPanel.innerHTML = `
    <div class="pane-heading">
      <div>
        <p class="card-meta">AI 面板</p>
        <h2 class="ai-panel-title">${escapeHtml(note.title)} 的上下文</h2>
      </div>
      <span class="mini-pill tone-warning">草稿</span>
    </div>
    <div class="ai-stat-grid">
      <article class="ai-stat">
        <span class="metric-label">队列</span>
        <strong>${escapeHtml(note.ai.queueDepth)}</strong>
      </article>
      <article class="ai-stat">
        <span class="metric-label">告警</span>
        <strong>${escapeHtml(note.ai.warnings)}</strong>
      </article>
    </div>
    <section class="ai-sync-box">
      <h3>操作建议</h3>
      <ul class="ai-list">
        ${syncContext.actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </section>
    <section class="ai-section">
      <h3>相关实体</h3>
      <div class="editor-tags">
        ${note.ai.relatedEntities.map((entity) => `<span class="ai-chip">${escapeHtml(entity)}</span>`).join("")}
      </div>
    </section>
    <section class="ai-section">
      <h3>建议动作</h3>
      <ul class="ai-list">
        ${note.ai.suggestions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </section>
    <section class="ai-section">
      <h3>Lint 提示</h3>
      ${
        note.ai.lint.length
          ? `<ul class="ai-list">${note.ai.lint.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
          : '<p class="ai-copy">当前没有 lint 提示。</p>'
      }
    </section>
    <section class="ai-section">
      <h3>相关同步活动</h3>
      ${
        syncContext.relatedActivity.length
          ? `<ul class="ai-list">${syncContext.relatedActivity
              .map(
                (record) =>
                  `<li>${escapeHtml(record.action_id)} / ${escapeHtml(record.status)} / ${escapeHtml(record.message || "无附加信息")}</li>`,
              )
              .join("")}</ul>`
          : '<p class="ai-copy">这篇文档当前没有匹配到相关同步活动。</p>'
      }
    </section>
  `;
}

function renderWorkspaceChrome() {
  elements.workspaceStatus.textContent = `工作区契约来源：${state.workspaceSourceLabel}`;
  renderWorkspaceTree();
  renderWorkspaceEditor();
  renderWorkspaceAiPanel();
}

function buildPayloadDetail() {
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
      kicker: summary.commit_gate.blocking_reasons?.join(", ") || "当前无阻塞原因",
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
      kicker: `Manifest 状态：${summary.state.last_manifest_summary_status}`,
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
  if (!syncCenter.cards.length) {
    elements.cardsGrid.innerHTML = `
      <div class="empty-state">
        <p>当前载荷没有返回同步卡片。</p>
      </div>
    `;
    return;
  }

  elements.cardsGrid.innerHTML = "";
  for (const card of syncCenter.cards) {
    const article = document.createElement("article");
    article.className = "sync-card";
    article.innerHTML = `
      <div class="card-title-row">
        <div>
          <p class="card-meta">${escapeHtml(formatCardKindLabel(card.kind))} / ${escapeHtml(card.card_id)}</p>
          <h3>${escapeHtml(card.title)}</h3>
        </div>
        <span class="level-pill tone-${resolveTone(card.level)}">${escapeHtml(formatLevelLabel(card.level))}</span>
      </div>
      <p class="card-body">${escapeHtml(card.body)}</p>
      <p class="summary-kicker">徽标数：${escapeHtml(card.badge_count)}</p>
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
  const records = Array.isArray(feed?.records) ? feed.records : [];

  if (!records.length) {
    elements.activityCard.innerHTML = `
      <div class="activity-header">
        <div>
          <h2>最近活动</h2>
          <p class="activity-empty">当前还没有加载任何同步活动记录。</p>
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
      </div>
      <span class="level-pill tone-${resolveTone(latestRecord.level)}">
        ${escapeHtml(formatStatusLabel(latestRecord.status))}
      </span>
    </div>
    <div class="timeline"></div>
  `;

  const timeline = elements.activityCard.querySelector(".timeline");
  for (const record of [...records].reverse()) {
    const item = document.createElement("article");
    item.className = "timeline-item";
    item.innerHTML = `
      <div class="timeline-row">
        <span class="timeline-title">${escapeHtml(record.action_id)}</span>
        <span class="timeline-time">${escapeHtml(formatDateTime(record.occurred_at_ms))}</span>
      </div>
      <div class="timeline-row">
        <span class="mini-pill tone-${resolveTone(record.level)}">${escapeHtml(formatLevelLabel(record.level))}</span>
        <span class="card-meta">${escapeHtml(record.command)} · 来源 ${escapeHtml(record.source)}</span>
      </div>
      ${record.message ? `<p class="timeline-message">${escapeHtml(record.message)}</p>` : ""}
    `;
    timeline.appendChild(item);
  }
}

function renderEmptyDashboard(message) {
  elements.payloadKind.textContent = "未加载";
  elements.payloadDetail.textContent = message;
  elements.panelCard.innerHTML = `<div class="empty-state"><p>${escapeHtml(message)}</p></div>`;
  elements.summaryGrid.innerHTML = "";
  elements.cardsGrid.innerHTML = "";
  elements.activityCard.innerHTML = `
    <div class="empty-state">
      <p>加载同步载荷后，这里才会显示同步看板。</p>
    </div>
  `;
  renderExecutionResult(state.lastExecution);
  setSelectedAction(null);
}

function render() {
  renderBridgeStatus();
  renderExecutionResult(state.lastExecution);
  renderWorkspaceChrome();

  if (!state.syncCenter && !state.activityFeed) {
    renderEmptyDashboard("请先加载样例会话，或手动粘贴同步 JSON。");
    return;
  }

  elements.payloadKind.textContent = state.snapshotMetadata
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
          当前只加载了活动流。若要显示顶部面板、摘要和同步卡片，请继续导入
          <code>sync-center</code> 或 <code>sync-shell-snapshot</code>。
        </p>
      </div>
    `;
    elements.summaryGrid.innerHTML = "";
    elements.cardsGrid.innerHTML = "";
    renderActivity(state.activityFeed);
  }
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
    throw new Error(`Unable to load payload ${path}: ${response.status}`);
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
  await loadPayloadFromPath(SAMPLE_PATH, "内置同步快照样例");
}

async function loadLiveSnapshot() {
  await loadPayloadFromPath(LIVE_SNAPSHOT_PATH, "本地导出的实时同步快照");
}

async function loadWorkspaceShellFromPath(path, sourceLabel) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Unable to load workspace shell ${path}: ${response.status}`);
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
  if (!workspaceShell.notes[state.selectedWorkspaceNoteId]) {
    state.selectedWorkspaceNoteId = Object.keys(workspaceShell.notes)[0] || "desktop-bridge";
  }
  elements.workspaceInput.value = JSON.stringify(workspaceShell, null, 2);
  renderWorkspaceChrome();
  elements.actionExecutionStatus.textContent = `工作区契约来源：${sourceLabel}`;
}

function applyAppSession(payload, sourceLabel) {
  const session = validateAppSession(payload);
  applyWorkspaceShell(session.workspaceShell, `${sourceLabel} / workspace`);
  applyPayload(session.syncPayload, `${sourceLabel} / sync`);
  state.lastBridgeError = null;
  elements.actionExecutionStatus.textContent = `应用会话来源：${session.source} · ${formatDateTime(session.loadedAtMs)}`;
}

async function refreshFromDesktop() {
  const json = await refreshBridgeSnapshot({});
  state.lastBridgeError = null;
  state.lastExecution = null;
  elements.payloadInput.value = JSON.stringify(json.snapshot, null, 2);
  applyPayload(json.snapshot, "通过本地桌面桥接刷新同步快照");
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
  elements.payloadInput.value = JSON.stringify(json.snapshot, null, 2);
  applyPayload(json.snapshot, `通过本地桌面桥接执行动作：${actionId}`);
  elements.actionExecutionStatus.textContent =
    `动作 ${json.execution.action.action_id} 已执行，状态为 ${json.execution.status}。`;
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

  if (explicitPayloadPath || explicitWorkspacePath) {
    if (explicitWorkspacePath) {
      await loadWorkspaceShellFromPath(explicitWorkspacePath, explicitWorkspacePath);
    } else {
      await loadWorkspaceShellFromPath(WORKSPACE_SAMPLE_PATH, "内置工作区样例");
    }

    if (explicitPayloadPath) {
      await loadPayloadFromPath(explicitPayloadPath, `从 ${explicitPayloadPath} 加载`);
    } else {
      await loadInitialPayload();
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
  applyPayload(JSON.parse(raw), "文本框同步 JSON");
}

async function importLocalFile(file) {
  const text = await file.text();
  elements.payloadInput.value = text;
  state.lastBridgeError = null;
  state.lastExecution = null;
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

elements.loadSampleButton.addEventListener("click", async () => {
  try {
    state.lastBridgeError = null;
    state.lastExecution = null;
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
    await loadLiveSnapshot();
  } catch (error) {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
  }
});

elements.loadWorkspaceSampleButton.addEventListener("click", async () => {
  try {
    await loadWorkspaceShellFromPath(WORKSPACE_SAMPLE_PATH, "内置工作区样例");
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
  state.syncCenter = null;
  state.activityFeed = null;
  state.snapshotMetadata = null;
  state.selectedAction = null;
  state.lastBridgeError = null;
  state.lastExecution = null;
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
    state.workspaceSourceLabel = "内置兜底样例";
    elements.workspaceInput.value = JSON.stringify(WORKSPACE_SAMPLE, null, 2);
    renderWorkspaceChrome();
    await loadInitialPayload();
  } catch {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
  }
});

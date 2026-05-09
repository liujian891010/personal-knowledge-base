import {
  fetchAiBoundaryStatus,
  fetchBridgeStatus,
  fetchSampleAppSession,
  refreshAppSession,
  startAiBoundaryStatusPolling,
  startBridgeStatusPolling,
} from "./bridge-client.js";
import {
  createRuntimeSessionId,
  DRAFT_RECOVERY_STORAGE_KEY,
  listRecoverableDrafts,
  parseDraftRecoveryStore,
} from "./draft-recovery.js";

const root = document.getElementById("app");

const state = {
  activeView: "rebuild",
  bridgeStatus: null,
  aiBoundaryStatus: null,
  appSession: null,
  recoverableDrafts: [],
  runtimeSessionId: createRuntimeSessionId(),
  lastError: "",
  lastUpdatedAtMs: null,
  loading: {
    bridge: false,
    ai: false,
    sample: false,
    refresh: false,
  },
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatDateTime(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "未记录";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

function normalizeErrorMessage(error) {
  if (!error) {
    return "发生了未预期错误。";
  }
  if (error instanceof Error) {
    return error.message;
  }
  if (typeof error === "object" && typeof error.message === "string") {
    return error.message;
  }
  return String(error);
}

function summarizeBridgeStatus() {
  const status = state.bridgeStatus;
  if (!status) {
    return {
      headline: "尚未检查",
      detail: "前台展示层已清空，目前只保留必要运行入口。",
      tone: "warning",
    };
  }

  if (status.available) {
    return {
      headline: "桌面桥接已接通",
      detail: `${status.config?.vaultRoot || "已加载工作区"}${status.mode ? ` · ${status.mode}` : ""}`,
      tone: "success",
    };
  }

  return {
    headline: "桌面桥接未接通",
    detail: Array.isArray(status.missing) && status.missing.length
      ? `缺失项：${status.missing.join(" / ")}`
      : "可以先加载演示会话，后续再接入真实桌面会话。",
    tone: "warning",
  };
}

function summarizeAiStatus() {
  const status = state.aiBoundaryStatus;
  if (!status) {
    return {
      headline: "尚未检查",
      detail: "AI 边界仍未初始化。",
      tone: "warning",
    };
  }

  return {
    headline: status.available ? "AI 接口在线" : "AI 已退回本地",
    detail: status.message || status.mode || "等待下一次能力检查。",
    tone: status.available ? "success" : "warning",
  };
}

function summarizeSession() {
  const session = state.appSession;
  if (!session) {
    return {
      headline: "当前没有工作会话",
      detail: "可以加载演示会话，或在调试中心继续检查桥接和 AI 边界。",
      noteCount: 0,
      sourceLabel: "未加载",
      loadedAt: "未记录",
    };
  }

  const noteCount =
    session.workspaceSummary?.noteCount ||
    Object.keys(session.workspaceShell?.notes || {}).length ||
    0;
  const sourceLabel =
    session.sourceLabel ||
    session.workspaceSourceLabel ||
    (session.source ? String(session.source) : "已加载会话");
  const loadedAt = formatDateTime(session.loadedAtMs || session.generated_at_ms || Date.now());

  return {
    headline: "工作会话已加载",
    detail: `${noteCount} 篇文档可用于后续前台重建联调。`,
    noteCount,
    sourceLabel,
    loadedAt,
  };
}

function readRecoverableDrafts() {
  try {
    const rawValue = window.localStorage.getItem(DRAFT_RECOVERY_STORAGE_KEY);
    const workspaceShell = state.appSession?.workspaceShell || null;
    state.recoverableDrafts = listRecoverableDrafts(parseDraftRecoveryStore(rawValue), {
      currentSessionId: state.runtimeSessionId,
      workspaceShell,
    });
  } catch {
    state.recoverableDrafts = [];
  }
}

function pillClass(tone) {
  return {
    success: "pill-success",
    warning: "pill-warning",
    danger: "pill-danger",
    info: "pill-info",
  }[tone] || "pill-info";
}

function buildSidebar() {
  const bridge = summarizeBridgeStatus();
  const ai = summarizeAiStatus();
  const session = summarizeSession();
  return `
    <aside class="sidebar">
      <section class="brand">
        <div class="brand-row">
          <div class="brand-mark">N</div>
          <div>
            <h1>NoteAI</h1>
            <p>前台展示层已清空，等待按 phb-ui 重建</p>
          </div>
        </div>
        <div class="brand-status">仅保留必要运行骨架</div>
      </section>

      <nav class="nav">
        <div class="nav-label">入口</div>
        <button class="nav-button ${state.activeView === "rebuild" ? "is-active" : ""}" data-view="rebuild" type="button">
          <span class="nav-icon">核</span>
          <span>重建骨架</span>
        </button>
        <button class="nav-button ${state.activeView === "debug" ? "is-active" : ""}" data-view="debug" type="button">
          <span class="nav-icon">调</span>
          <span>调试中心</span>
        </button>
      </nav>

      <section class="sidebar-meta">
        <article class="sidebar-summary">
          <div class="nav-label">桥接</div>
          <strong>${escapeHtml(bridge.headline)}</strong>
          <p>${escapeHtml(bridge.detail)}</p>
        </article>
        <article class="sidebar-summary">
          <div class="nav-label">AI 边界</div>
          <strong>${escapeHtml(ai.headline)}</strong>
          <p>${escapeHtml(ai.detail)}</p>
        </article>
        <article class="sidebar-summary">
          <div class="nav-label">工作会话</div>
          <strong>${escapeHtml(session.sourceLabel)}</strong>
          <p>${escapeHtml(session.detail)}</p>
        </article>
        <article class="sidebar-summary">
          <div class="nav-label">恢复草稿</div>
          <strong>${escapeHtml(`${state.recoverableDrafts.length} 份`)}</strong>
          <p>${escapeHtml(state.recoverableDrafts.length ? "仍有本地恢复草稿等待重新接回。" : "当前没有待恢复草稿。")}</p>
        </article>
      </section>

      <section class="sidebar-footer">
        <div class="nav-label">当前模式</div>
        <div class="sidebar-account">
          <div class="sidebar-account-mark">N</div>
          <div class="sidebar-account-copy">
            <strong>重建模式</strong>
            <span>前台 UI 已整体清空</span>
          </div>
        </div>
      </section>
    </aside>
  `;
}

function buildTopbar() {
  return `
    <header class="topbar">
      <div class="topbar-copy">
        <strong>${state.activeView === "debug" ? "调试中心" : "前台重建骨架"}</strong>
        <p>${escapeHtml(
          state.activeView === "debug"
            ? "这里保留最小原始状态观察面，用于后续 phb-ui 前台重建联调。"
            : "当前复杂前端界面已经全部移除，只保留桥接、AI、会话和恢复入口。",
        )}</p>
      </div>
      <div class="topbar-actions">
        <button class="button button-secondary" data-action="check-bridge" type="button" ${state.loading.bridge ? "disabled" : ""}>检查桥接</button>
        <button class="button button-secondary" data-action="check-ai" type="button" ${state.loading.ai ? "disabled" : ""}>检查 AI</button>
        <button class="button button-primary" data-action="load-sample" type="button" ${state.loading.sample ? "disabled" : ""}>加载演示会话</button>
      </div>
    </header>
  `;
}

function buildRebuildView() {
  const bridge = summarizeBridgeStatus();
  const ai = summarizeAiStatus();
  const session = summarizeSession();
  const lastUpdated = state.lastUpdatedAtMs ? formatDateTime(state.lastUpdatedAtMs) : "未检查";

  return `
    <section class="content">
      <section class="hero">
        <div class="hero-kicker">重建模式</div>
        <h2>当前前端 UI 已全部清除</h2>
        <p>这一步只保留了必要运行骨架。接下来会在这套极简前台之上，按 phb-ui 的视觉与交互重建真正的用户界面。</p>
        <div class="pill-row">
          <span class="pill ${pillClass(bridge.tone)}">${escapeHtml(bridge.headline)}</span>
          <span class="pill ${pillClass(ai.tone)}">${escapeHtml(ai.headline)}</span>
          <span class="pill pill-info">${escapeHtml(`恢复草稿 ${state.recoverableDrafts.length} 份`)}</span>
          <span class="pill pill-info">${escapeHtml(`最近检查 ${lastUpdated}`)}</span>
        </div>
        <div class="hero-actions">
          <button class="button button-primary" data-action="load-sample" type="button" ${state.loading.sample ? "disabled" : ""}>加载演示会话</button>
          <button class="button button-secondary" data-action="refresh-session" type="button" ${state.loading.refresh ? "disabled" : ""}>刷新真实会话</button>
          <button class="button button-subtle" data-view="debug" type="button">打开调试中心</button>
        </div>
      </section>

      <section class="grid">
        <article class="card">
          <div class="card-kicker">桥接</div>
          <h3>${escapeHtml(bridge.headline)}</h3>
          <p>${escapeHtml(bridge.detail)}</p>
          <div class="card-actions">
            <button class="button button-secondary" data-action="check-bridge" type="button" ${state.loading.bridge ? "disabled" : ""}>重新检查</button>
          </div>
        </article>

        <article class="card">
          <div class="card-kicker">AI 边界</div>
          <h3>${escapeHtml(ai.headline)}</h3>
          <p>${escapeHtml(ai.detail)}</p>
          <div class="card-actions">
            <button class="button button-secondary" data-action="check-ai" type="button" ${state.loading.ai ? "disabled" : ""}>重新检查</button>
          </div>
        </article>

        <article class="card">
          <div class="card-kicker">工作会话</div>
          <h3>${escapeHtml(session.headline)}</h3>
          <div class="metric-row">
            <span>来源</span>
            <span class="mono">${escapeHtml(session.sourceLabel)}</span>
          </div>
          <div class="metric-row">
            <span>文档数</span>
            <span class="mono">${escapeHtml(session.noteCount)}</span>
          </div>
          <div class="metric-row">
            <span>加载时间</span>
            <span class="mono">${escapeHtml(session.loadedAt)}</span>
          </div>
        </article>

        <article class="card">
          <div class="card-kicker">本地恢复</div>
          <h3>${escapeHtml(`${state.recoverableDrafts.length} 份待恢复草稿`)}</h3>
          ${
            state.recoverableDrafts.length
              ? `
                <p>${escapeHtml(
                  state.recoverableDrafts
                    .slice(0, 3)
                    .map((entry) => entry.title || entry.noteId)
                    .join(" · "),
                )}</p>
              `
              : '<p>当前没有需要恢复的本地草稿。</p>'
          }
        </article>
      </section>

      ${
        state.lastError
          ? `
            <article class="card">
              <div class="card-kicker">最近错误</div>
              <h3>保留错误出口</h3>
              <p>${escapeHtml(state.lastError)}</p>
              <div class="card-actions">
                <button class="button button-subtle" data-action="clear-error" type="button">清除提示</button>
              </div>
            </article>
          `
          : ""
      }
    </section>
  `;
}

function formatJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function buildDebugView() {
  return `
    <section class="content">
      <section class="hero">
        <div class="hero-kicker">调试中心</div>
        <h2>仅保留最小调试面</h2>
        <p>前台复杂界面已经被清空。这里保留桥接、AI 和会话的原始状态输出，作为下一轮 phb-ui 前台重建时的联调底座。</p>
      </section>

      <section class="debug-grid">
        <article class="debug-card">
          <div class="debug-block">
            <div class="debug-label">桥接状态</div>
            <pre class="debug-output">${escapeHtml(formatJson(state.bridgeStatus))}</pre>
          </div>
        </article>

        <article class="debug-card">
          <div class="debug-block">
            <div class="debug-label">AI 边界</div>
            <pre class="debug-output">${escapeHtml(formatJson(state.aiBoundaryStatus))}</pre>
          </div>
        </article>

        <article class="debug-card">
          <div class="debug-block">
            <div class="debug-label">工作会话</div>
            <pre class="debug-output">${escapeHtml(formatJson(state.appSession))}</pre>
          </div>
        </article>

        <article class="debug-card">
          <div class="debug-block">
            <div class="debug-label">恢复草稿</div>
            <pre class="debug-output">${escapeHtml(formatJson(state.recoverableDrafts))}</pre>
          </div>
        </article>
      </section>
    </section>
  `;
}

function render() {
  if (!root) {
    return;
  }

  root.innerHTML = `
    <div class="shell">
      ${buildSidebar()}
      <main class="main">
        ${buildTopbar()}
        <div class="main-scroll">
          ${state.activeView === "debug" ? buildDebugView() : buildRebuildView()}
        </div>
      </main>
    </div>
  `;

  for (const button of root.querySelectorAll("[data-view]")) {
    button.addEventListener("click", () => {
      state.activeView = button.dataset.view || "rebuild";
      render();
    });
  }

  for (const button of root.querySelectorAll("[data-action]")) {
    button.addEventListener("click", async () => {
      await handleAction(button.dataset.action || "");
    });
  }
}

async function withLoading(key, runner) {
  state.loading[key] = true;
  render();
  try {
    await runner();
    state.lastError = "";
    state.lastUpdatedAtMs = Date.now();
  } catch (error) {
    state.lastError = normalizeErrorMessage(error);
    state.lastUpdatedAtMs = Date.now();
  } finally {
    state.loading[key] = false;
    readRecoverableDrafts();
    render();
  }
}

async function handleAction(action) {
  if (action === "check-bridge") {
    await withLoading("bridge", async () => {
      state.bridgeStatus = await fetchBridgeStatus();
    });
    return;
  }

  if (action === "check-ai") {
    await withLoading("ai", async () => {
      state.aiBoundaryStatus = await fetchAiBoundaryStatus();
    });
    return;
  }

  if (action === "load-sample") {
    await withLoading("sample", async () => {
      state.appSession = await fetchSampleAppSession();
    });
    return;
  }

  if (action === "refresh-session") {
    await withLoading("refresh", async () => {
      state.appSession = await refreshAppSession();
    });
    return;
  }

  if (action === "clear-error") {
    state.lastError = "";
    render();
  }
}

function startPolling() {
  startBridgeStatusPolling({
    intervalMs: 15000,
    onStatus(status) {
      state.bridgeStatus = status;
      state.lastUpdatedAtMs = Date.now();
      render();
    },
    onError(error) {
      state.lastError = normalizeErrorMessage(error);
      state.lastUpdatedAtMs = Date.now();
      render();
    },
  });

  startAiBoundaryStatusPolling({
    intervalMs: 15000,
    onStatus(status) {
      state.aiBoundaryStatus = status;
      state.lastUpdatedAtMs = Date.now();
      render();
    },
    onError(error) {
      state.lastError = normalizeErrorMessage(error);
      state.lastUpdatedAtMs = Date.now();
      render();
    },
  });
}

function init() {
  readRecoverableDrafts();
  render();
  startPolling();
  window.addEventListener("storage", (event) => {
    if (event.key === DRAFT_RECOVERY_STORAGE_KEY) {
      readRecoverableDrafts();
      render();
    }
  });
}

init();

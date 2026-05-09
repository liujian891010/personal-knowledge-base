import {
  executeBridgeAction,
  fetchBridgeStatus,
  refreshBridgeSnapshot,
  startBridgeStatusPolling,
} from "./bridge-client.js";

const SAMPLE_PATH = "./fixtures/sync-shell-snapshot.sample.json";
const LIVE_SNAPSHOT_PATH = "./fixtures/live-sync-shell.json";
const DEFAULT_ACTION_COMMAND = "pkb-desktop-sync";

const state = {
  syncCenter: null,
  activityFeed: null,
  snapshotMetadata: null,
  selectedAction: null,
  bridgeStatus: null,
  bridgeCheckedAtMs: null,
  lastBridgeError: null,
  lastExecution: null,
  sourceLabel: "Not loaded",
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

function renderExecutionResult(execution) {
  if (!execution) {
    elements.actionResultOutput.textContent = "No action has been executed yet.";
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
        typeof error.message === "string" ? error.message : "Bridge request failed unexpectedly.",
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
        message: "Could not reach the local dev server bridge.",
        details: error ? { reason: error.message } : null,
      },
    ],
  };
}

function renderBridgeError(error) {
  if (!error) {
    elements.bridgeErrorOutput.textContent = "No bridge errors.";
    return;
  }
  elements.bridgeErrorOutput.textContent = JSON.stringify(error, null, 2);
}

function renderBridgeDiagnostics(status) {
  if (!status) {
    elements.bridgeDiagnosticsOutput.textContent = "Waiting for /api/bridge/status ...";
    return;
  }
  elements.bridgeDiagnosticsOutput.textContent = JSON.stringify(
    {
      checkedAt: state.bridgeCheckedAtMs ? formatDateTime(state.bridgeCheckedAtMs) : "not yet",
      mode: status.mode || "unknown",
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
    elements.bridgeStatus.textContent = "Checking local desktop bridge...";
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
      `Local desktop bridge ready for ${status.config.vaultId} at ${status.config.vaultRoot} ` +
      `(${sourceLabel})`;
    elements.refreshLocalButton.disabled = false;
    elements.executeSelectedButton.disabled = !state.selectedAction;
    elements.reloadBridgeStatusButton.disabled = false;
    renderBridgeDiagnostics(status);
    renderBridgeError(state.lastBridgeError);
    return;
  }

  elements.bridgeStatus.textContent = `Local desktop bridge unavailable: ${status.missing.join(", ")}`;
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
      "Select an action from the panel, cards, or activity area to preview the shell command contract.";
    elements.actionContractOutput.textContent = "No action selected.";
    elements.actionExecutionStatus.textContent =
      "Local execution is available only when the desktop bridge is configured.";
    elements.executeSelectedButton.disabled = true;
    return;
  }

  const commandLine = [DEFAULT_ACTION_COMMAND, action.command, ...(action.argv || [])].join(" ");
  const lines = [
    `source: ${source}`,
    `action_id: ${action.action_id}`,
    `enabled: ${action.enabled !== false}`,
    `command: ${action.command}`,
    `argv: ${JSON.stringify(action.argv || [])}`,
    `requires_confirmation: ${Boolean(action.requires_confirmation)}`,
    `shell: ${commandLine}`,
  ];

  if (action.reason) {
    lines.push(`reason: ${action.reason}`);
  }

  elements.actionContractHelp.textContent =
    "The UI does not execute actions directly; it previews the exact command contract the local bridge can forward.";
  elements.actionContractOutput.textContent = lines.join("\n");
  elements.actionExecutionStatus.textContent =
    "Use Run Selected Action to forward this contract through the local desktop bridge.";
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
      <span class="level-pill tone-${tone}">${escapeHtml(panel.level)}</span>
      <span class="mini-pill tone-info">Conflicts ${escapeHtml(panel.conflict_badge_count)}</span>
      <span class="mini-pill tone-info">Changes ${escapeHtml(panel.change_badge_count)}</span>
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
      label: "Commit Gate",
      value: summary.commit_gate.can_submit_commit ? "Open" : "Blocked",
      kicker: summary.commit_gate.blocking_reasons?.join(", ") || "No blocking reasons",
    },
    {
      label: "Local Changes",
      value: summary.changes.change_count ?? 0,
      kicker: `${summary.changes.tracked_record_count ?? 0} tracked records scanned`,
    },
    {
      label: "Conflicts",
      value:
        (summary.conflicts.conflict_copies?.length || 0) +
        (summary.conflicts.conflict_orphans?.length || 0),
      kicker: summary.conflicts.actual_has_unresolved_conflicts
        ? "Unresolved artifacts still exist"
        : "No unresolved local artifacts",
    },
    {
      label: "Revisions",
      value: `${summary.state.acked_revision}/${summary.state.remote_head_revision}`,
      kicker: `Manifest ${summary.state.last_manifest_summary_status}`,
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
        <p>No sync cards were provided by the current payload.</p>
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
          <p class="card-meta">${escapeHtml(card.kind)} / ${escapeHtml(card.card_id)}</p>
          <h3>${escapeHtml(card.title)}</h3>
        </div>
        <span class="level-pill tone-${resolveTone(card.level)}">${escapeHtml(card.level)}</span>
      </div>
      <p class="card-body">${escapeHtml(card.body)}</p>
      <p class="summary-kicker">Badge count: ${escapeHtml(card.badge_count)}</p>
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
          <h2>Recent Activity</h2>
          <p class="activity-empty">No sync activity records have been loaded yet.</p>
        </div>
      </div>
    `;
    return;
  }

  const latestRecord = records[records.length - 1];
  elements.activityCard.innerHTML = `
    <div class="activity-header">
      <div>
        <h2>Recent Activity</h2>
        <p class="summary-copy">
          ${escapeHtml(feed.total_count)} records loaded. Latest event at
          ${escapeHtml(formatDateTime(latestRecord.occurred_at_ms))}.
        </p>
      </div>
      <span class="level-pill tone-${resolveTone(latestRecord.level)}">
        ${escapeHtml(latestRecord.status)}
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
        <span class="mini-pill tone-${resolveTone(record.level)}">${escapeHtml(record.level)}</span>
        <span class="card-meta">${escapeHtml(record.command)} from ${escapeHtml(record.source)}</span>
      </div>
      ${record.message ? `<p class="timeline-message">${escapeHtml(record.message)}</p>` : ""}
    `;
    timeline.appendChild(item);
  }
}

function renderEmptyDashboard(message) {
  elements.payloadKind.textContent = "Not loaded";
  elements.payloadDetail.textContent = message;
  elements.panelCard.innerHTML = `<div class="empty-state"><p>${escapeHtml(message)}</p></div>`;
  elements.summaryGrid.innerHTML = "";
  elements.cardsGrid.innerHTML = "";
  elements.activityCard.innerHTML = `
    <div class="empty-state">
      <p>Load a payload to render the sync dashboard.</p>
    </div>
  `;
  renderExecutionResult(state.lastExecution);
  setSelectedAction(null);
}

function render() {
  renderBridgeStatus();
  renderExecutionResult(state.lastExecution);

  if (!state.syncCenter && !state.activityFeed) {
    renderEmptyDashboard("Load the sample contract or paste your own JSON.");
    return;
  }

  elements.payloadKind.textContent = state.snapshotMetadata
    ? "sync-shell-snapshot"
    : state.syncCenter
      ? "sync-center"
      : "sync-activity";
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
          Activity-only payload loaded. Import a <code>sync-center</code> payload to render
          panel, summary, and card sections.
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
  await loadPayloadFromPath(SAMPLE_PATH, "Bundled sync shell snapshot sample");
}

async function loadLiveSnapshot() {
  await loadPayloadFromPath(LIVE_SNAPSHOT_PATH, "Exported live sync shell snapshot");
}

async function refreshFromDesktop() {
  const json = await refreshBridgeSnapshot({});
  state.lastBridgeError = null;
  state.lastExecution = null;
  elements.payloadInput.value = JSON.stringify(json.snapshot, null, 2);
  applyPayload(json.snapshot, "Refreshed from local desktop bridge");
}

async function executeSelectedAction() {
  if (!state.selectedAction) {
    throw new Error("select an action before trying to run it");
  }

  const actionId = state.selectedAction.action_id;
  const json = await executeBridgeAction(actionId);

  state.lastBridgeError = null;
  state.lastExecution = json.execution;
  elements.payloadInput.value = JSON.stringify(json.snapshot, null, 2);
  applyPayload(json.snapshot, `Executed ${actionId} through local desktop bridge`);
  elements.actionExecutionStatus.textContent =
    `Executed ${json.execution.action.action_id} with status ${json.execution.status}.`;
}

async function loadInitialPayload() {
  const explicitPath = resolveInitialPayloadPath();
  if (explicitPath) {
    await loadPayloadFromPath(explicitPath, `Loaded from ${explicitPath}`);
    return;
  }

  try {
    await loadLiveSnapshot();
  } catch {
    await loadSample();
  }
}

function applyTextareaPayload() {
  const raw = elements.payloadInput.value.trim();
  if (!raw) {
    renderEmptyDashboard(
      "Paste a sync-shell-snapshot, sync-center, or sync-activity JSON payload first.",
    );
    return;
  }

  state.lastBridgeError = null;
  state.lastExecution = null;
  applyPayload(JSON.parse(raw), "Textarea JSON payload");
}

async function importLocalFile(file) {
  const text = await file.text();
  elements.payloadInput.value = text;
  state.lastBridgeError = null;
  state.lastExecution = null;
  applyPayload(JSON.parse(text), `Imported file: ${file.name}`);
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

elements.loadLiveButton.addEventListener("click", async () => {
  try {
    state.lastBridgeError = null;
    state.lastExecution = null;
    await loadLiveSnapshot();
  } catch (error) {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
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

elements.clearInputButton.addEventListener("click", () => {
  elements.payloadInput.value = "";
  state.syncCenter = null;
  state.activityFeed = null;
  state.snapshotMetadata = null;
  state.selectedAction = null;
  state.lastBridgeError = null;
  state.lastExecution = null;
  state.sourceLabel = "Not loaded";
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
loadInitialPayload().catch((error) => {
  renderEmptyDashboard(error instanceof Error ? error.message : String(error));
});

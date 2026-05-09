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
      label: "Notes",
      items: [
        {
          id: "desktop-bridge",
          title: "Desktop Bridge Rollout",
          path: "Notes/Engineering/Desktop Bridge Rollout.md",
          status: "Modified",
        },
        {
          id: "sync-recovery",
          title: "Sync Recovery Checklist",
          path: "Notes/Engineering/Sync Recovery Checklist.md",
          status: "Review",
        },
        {
          id: "release-cadence",
          title: "Release Cadence",
          path: "Notes/Product/Release Cadence.md",
          status: "Stable",
        },
      ],
    },
    {
      id: "ai-wiki",
      label: ".ai/wiki",
      items: [
        {
          id: "desktop-shell",
          title: "Desktop Shell Spec",
          path: ".ai/wiki/shell/Desktop Shell Spec.md",
          status: "AI draft",
        },
        {
          id: "bridge-diagnostics",
          title: "Bridge Diagnostics",
          path: ".ai/wiki/ops/Bridge Diagnostics.md",
          status: "Needs accept",
        },
      ],
    },
  ],
  notes: {
    "desktop-bridge": {
      title: "Desktop Bridge Rollout",
      path: "Notes/Engineering/Desktop Bridge Rollout.md",
      statusTone: "warning",
      statusLabel: "Modified locally",
      lastSaved: "Saved 6 minutes ago",
      tags: ["sync", "desktop", "bridge", "noteapp-web"],
      syncContext: {
        watchActionIds: ["pull", "show-vault-summary"],
        watchCardKinds: ["baseline", "activity"],
        watchBlockingReasons: ["requires_full_pull"],
      },
      body: `# Desktop Bridge Rollout

## Current slice
- Export sync shell snapshots through the desktop CLI.
- Forward executable actions through a local dev bridge instead of re-implementing them in the browser.
- Keep browser-side diagnostics visible so missing local config is obvious.

## Next decisions
1. Stabilize the bridge contract with lightweight tests.
2. Expand the web shell from sync-only to full workspace chrome.
3. Move from prototype panes to real file-tree and editor contracts.`,
      ai: {
        queueDepth: 2,
        warnings: 1,
        relatedEntities: ["Desktop CLI", "Sync Center", "Bridge Local Config"],
        suggestions: [
          "Promote the bridge status payload to a shared frontend contract.",
          "Expose selected note context to the AI panel for follow-up workflows.",
        ],
        lint: ["Bridge status should surface config precedence and failure codes."],
      },
    },
    "sync-recovery": {
      title: "Sync Recovery Checklist",
      path: "Notes/Engineering/Sync Recovery Checklist.md",
      statusTone: "danger",
      statusLabel: "Needs conflict audit",
      lastSaved: "Saved yesterday",
      tags: ["sync", "recovery", "conflicts"],
      syncContext: {
        watchActionIds: ["pull", "submit-detected-commit"],
        watchCardKinds: ["baseline", "local-changes", "activity"],
        watchBlockingReasons: ["requires_full_pull"],
      },
      body: `# Sync Recovery Checklist

## Before retry
- Confirm staging files were cleaned after the failed attempt.
- Verify unresolved conflict copies are still visible in the vault.
- Rebuild the next snapshot from a fresh working tree scan.

## Operator note
Do not resume a commit from stale plaintext snapshots. The next round must rebuild from the latest source versions.`,
      ai: {
        queueDepth: 1,
        warnings: 3,
        relatedEntities: ["commit_intent_journal", ".noteapp/staging", "conflict_copies"],
        suggestions: [
          "Add a visible recovery badge in the sync dock when staging cleanup is required.",
          "Link conflict artifacts directly from the future file-tree contract.",
        ],
        lint: [
          "Missing rollback note for blob staging cleanup.",
          "Needs example operator timeline for drift-abort handling.",
        ],
      },
    },
    "release-cadence": {
      title: "Release Cadence",
      path: "Notes/Product/Release Cadence.md",
      statusTone: "success",
      statusLabel: "Ready",
      lastSaved: "Saved this morning",
      tags: ["product", "delivery", "weekly"],
      syncContext: {
        watchActionIds: ["show-vault-summary"],
        watchCardKinds: ["local-changes"],
        watchBlockingReasons: [],
      },
      body: `# Release Cadence

## Shipping rule
- Push after each meaningful phase.
- Keep the static shell deployable at every step.
- Avoid mixing experimental UI work with bridge boundary fixes in the same commit unless they are tightly coupled.

## Weekly ritual
Monday: sync and diagnostics
Wednesday: desktop shell boundary work
Friday: workspace UX refinement`,
      ai: {
        queueDepth: 0,
        warnings: 0,
        relatedEntities: ["Weekly Review", "Milestone Board", "Release Notes"],
        suggestions: ["Summarize the last three pushed commits into a changelog draft."],
        lint: [],
      },
    },
    "desktop-shell": {
      title: "Desktop Shell Spec",
      path: ".ai/wiki/shell/Desktop Shell Spec.md",
      statusTone: "info",
      statusLabel: "AI draft",
      lastSaved: "Compiled 18 minutes ago",
      tags: ["ai", "shell", "spec"],
      syncContext: {
        watchActionIds: ["sync-activity"],
        watchCardKinds: ["activity"],
        watchBlockingReasons: [],
      },
      body: `# Desktop Shell Spec

## Intent
Capture the shell-level contracts the desktop client exposes to the static web layer.

## Coverage
- sync-shell-snapshot
- sync-center summary and cards
- activity feed
- executable action forwarding

## Gap
The current web shell still needs a first-class file tree and editor contract.`,
      ai: {
        queueDepth: 4,
        warnings: 2,
        relatedEntities: ["sync-shell-snapshot", "activity_feed", "execute-sync-action-and-snapshot"],
        suggestions: [
          "Accept the AI draft once the editor and tree panes are backed by real contracts.",
        ],
        lint: ["Spec references future UI panes without sample contract payloads."],
      },
    },
    "bridge-diagnostics": {
      title: "Bridge Diagnostics",
      path: ".ai/wiki/ops/Bridge Diagnostics.md",
      statusTone: "warning",
      statusLabel: "Needs accept",
      lastSaved: "Compiled 2 hours ago",
      tags: ["ai", "ops", "diagnostics"],
      syncContext: {
        watchActionIds: ["pull", "sync-activity"],
        watchCardKinds: ["activity", "baseline"],
        watchBlockingReasons: ["requires_full_pull"],
      },
      body: `# Bridge Diagnostics

## Captured signals
- config source precedence
- missing required bridge settings
- desktop CLI spawn failures
- invalid JSON returned by the desktop boundary

## Pending
Map diagnostics into a shared frontend model and keep the browser copy minimal.`,
      ai: {
        queueDepth: 1,
        warnings: 1,
        relatedEntities: ["configSource", "sourceByField", "desktop_cli_invalid_json"],
        suggestions: ["Add one screenshot-ready status panel for operator demos."],
        lint: ["Needs explicit note that bearer tokens remain local-only."],
      },
    },
  },
};

const state = {
  syncCenter: null,
  activityFeed: null,
  snapshotMetadata: null,
  workspaceShell: null,
  workspaceSourceLabel: "Not loaded",
  selectedWorkspaceNoteId: "desktop-bridge",
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
      headline: "Workspace note is not linked to a sync payload yet.",
      signals: [createSignal("info", "No sync payload")],
      actions: ["Load a sync-shell-snapshot or sync-center payload to enrich this workspace note."],
      relatedActivity: [],
    };
  }

  if (summary.changes?.change_count > 0) {
    signals.push(createSignal("warning", `${summary.changes.change_count} local changes`));
  }
  if (summary.commit_gate?.requires_full_pull) {
    signals.push(createSignal("danger", "Full pull required"));
  }
  if (summary.conflicts?.actual_has_unresolved_conflicts) {
    signals.push(createSignal("danger", "Unresolved conflicts"));
  }
  if (summary.worker_health?.status) {
    signals.push(createSignal(summary.worker_health.status === "healthy" ? "success" : "warning", summary.worker_health.status));
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
    signals.push(createSignal("danger", `Gate: ${reason}`));
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
    actions.push("Run Pull before attempting a new submit.");
  }
  if (matchedActivity.some((record) => record.status === "failed")) {
    actions.push("Inspect the failed sync activity details and retry after fixing the local condition.");
  }
  if (summary.changes?.change_count > 0) {
    actions.push("Keep this note aligned with the pending local changes before the next sync cycle.");
  }
  if (!actions.length) {
    actions.push("No immediate sync follow-up is required for this note.");
  }

  const latestRelated = matchedActivity.slice(-2).reverse();
  const headlineParts = [
    panel.headline,
    matchedCards[0]?.title || null,
    latestRelated[0]?.message || latestRelated[0]?.status || null,
  ].filter(Boolean);

  return {
    headline: headlineParts.join(" | "),
    signals: dedupedSignals.length ? dedupedSignals : [createSignal("info", "No matched sync signals")],
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
        <p class="card-meta">Editor Prototype</p>
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
      <h3>Sync Signals</h3>
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
        <p class="card-meta">AI Panel</p>
        <h2 class="ai-panel-title">Context For ${escapeHtml(note.title)}</h2>
      </div>
      <span class="mini-pill tone-warning">Draft</span>
    </div>
    <div class="ai-stat-grid">
      <article class="ai-stat">
        <span class="metric-label">Queue</span>
        <strong>${escapeHtml(note.ai.queueDepth)}</strong>
      </article>
      <article class="ai-stat">
        <span class="metric-label">Warnings</span>
        <strong>${escapeHtml(note.ai.warnings)}</strong>
      </article>
    </div>
    <section class="ai-sync-box">
      <h3>Operator Next Steps</h3>
      <ul class="ai-list">
        ${syncContext.actions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </section>
    <section class="ai-section">
      <h3>Related Entities</h3>
      <div class="editor-tags">
        ${note.ai.relatedEntities.map((entity) => `<span class="ai-chip">${escapeHtml(entity)}</span>`).join("")}
      </div>
    </section>
    <section class="ai-section">
      <h3>Suggested Actions</h3>
      <ul class="ai-list">
        ${note.ai.suggestions.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
      </ul>
    </section>
    <section class="ai-section">
      <h3>Lint</h3>
      ${
        note.ai.lint.length
          ? `<ul class="ai-list">${note.ai.lint.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`
          : '<p class="ai-copy">No lint warnings for this note.</p>'
      }
    </section>
    <section class="ai-section">
      <h3>Related Sync Activity</h3>
      ${
        syncContext.relatedActivity.length
          ? `<ul class="ai-list">${syncContext.relatedActivity
              .map(
                (record) =>
                  `<li>${escapeHtml(record.action_id)} / ${escapeHtml(record.status)} / ${escapeHtml(record.message || "no message")}</li>`,
              )
              .join("")}</ul>`
          : '<p class="ai-copy">No matching sync activity for this note.</p>'
      }
    </section>
  `;
}

function renderWorkspaceChrome() {
  elements.workspaceStatus.textContent = `Workspace shell source: ${state.workspaceSourceLabel}`;
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
  renderWorkspaceChrome();

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
  elements.actionExecutionStatus.textContent = `Workspace shell source: ${sourceLabel}.`;
}

function applyAppSession(payload, sourceLabel) {
  const session = validateAppSession(payload);
  applyWorkspaceShell(session.workspaceShell, `${sourceLabel} / workspace`);
  applyPayload(session.syncPayload, `${sourceLabel} / sync`);
  state.lastBridgeError = null;
  elements.actionExecutionStatus.textContent = `App session source: ${session.source} at ${formatDateTime(session.loadedAtMs)}.`;
}

async function refreshFromDesktop() {
  const json = await refreshBridgeSnapshot({});
  state.lastBridgeError = null;
  state.lastExecution = null;
  elements.payloadInput.value = JSON.stringify(json.snapshot, null, 2);
  applyPayload(json.snapshot, "Refreshed from local desktop bridge");
}

async function loadSampleAppSession() {
  const session = await fetchSampleAppSession();
  applyAppSession(session, "Bundled app session");
}

async function refreshFullAppSession() {
  const session = await refreshAppSession({});
  applyAppSession(session, "Desktop bridge app session");
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

async function loadInitialSession() {
  const explicitPayloadPath = resolveInitialPayloadPath();
  const explicitWorkspacePath = resolveInitialWorkspacePath();
  const sessionMode = resolveInitialSessionMode();

  if (explicitPayloadPath || explicitWorkspacePath) {
    if (explicitWorkspacePath) {
      await loadWorkspaceShellFromPath(explicitWorkspacePath, explicitWorkspacePath);
    } else {
      await loadWorkspaceShellFromPath(WORKSPACE_SAMPLE_PATH, "bundled workspace shell sample");
    }

    if (explicitPayloadPath) {
      await loadPayloadFromPath(explicitPayloadPath, `Loaded from ${explicitPayloadPath}`);
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

function applyWorkspaceTextareaPayload() {
  const raw = elements.workspaceInput.value.trim();
  if (!raw) {
    throw new Error("Paste a workspace shell JSON payload first.");
  }
  applyWorkspaceShell(JSON.parse(raw), "Workspace textarea JSON payload");
}

async function importWorkspaceFile(file) {
  const text = await file.text();
  applyWorkspaceShell(JSON.parse(text), `Imported workspace file: ${file.name}`);
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
    await loadWorkspaceShellFromPath(WORKSPACE_SAMPLE_PATH, "Bundled workspace shell sample");
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
    state.workspaceSourceLabel = "Fallback inline sample";
    elements.workspaceInput.value = JSON.stringify(WORKSPACE_SAMPLE, null, 2);
    renderWorkspaceChrome();
    await loadInitialPayload();
  } catch {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
  }
});

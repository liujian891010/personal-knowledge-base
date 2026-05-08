const SAMPLE_PATH = "./fixtures/sync-center.sample.json";
const DEFAULT_ACTION_COMMAND = "pkb-desktop-sync";

const state = {
  syncCenter: null,
  activityFeed: null,
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
  payloadInput: document.getElementById("payload-input"),
  loadSampleButton: document.getElementById("load-sample-button"),
  applyInputButton: document.getElementById("apply-input-button"),
  clearInputButton: document.getElementById("clear-input-button"),
  fileInput: document.getElementById("file-input"),
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

  throw new Error("Unsupported payload shape. Expected sync-center or sync-activity JSON.");
}

function setSelectedAction(action, source = "manual selection") {
  if (!action) {
    elements.actionContractHelp.textContent =
      "Select an action from the panel, cards, or activity area to preview the shell command contract.";
    elements.actionContractOutput.textContent = "No action selected.";
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
    "The UI does not execute actions yet; it previews the exact command contract the shell can forward.";
  elements.actionContractOutput.textContent = lines.join("\n");
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
  panelActions.appendChild(
    createActionChip(panel.primary_action, "panel.primary_action"),
  );
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
  setSelectedAction(null);
}

function render() {
  if (!state.syncCenter && !state.activityFeed) {
    renderEmptyDashboard("Load the sample contract or paste your own JSON.");
    return;
  }

  elements.payloadKind.textContent = state.syncCenter ? "sync-center" : "sync-activity";
  elements.payloadDetail.textContent = state.sourceLabel;

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

function applyPayload(payload, sourceLabel) {
  const kind = detectPayloadKind(payload);
  state.sourceLabel = sourceLabel;

  if (kind === "sync-center") {
    state.syncCenter = payload;
    state.activityFeed = payload.recent_activity || null;
  } else {
    state.syncCenter = null;
    state.activityFeed = payload;
  }

  render();
}

async function loadSample() {
  const response = await fetch(SAMPLE_PATH);
  if (!response.ok) {
    throw new Error(`Unable to load sample payload: ${response.status}`);
  }

  const payload = await response.json();
  elements.payloadInput.value = JSON.stringify(payload, null, 2);
  applyPayload(payload, "Bundled sync-center sample");
}

function applyTextareaPayload() {
  const raw = elements.payloadInput.value.trim();
  if (!raw) {
    renderEmptyDashboard("Paste a sync-center or sync-activity JSON payload first.");
    return;
  }

  const payload = JSON.parse(raw);
  applyPayload(payload, "Textarea JSON payload");
}

async function importLocalFile(file) {
  const text = await file.text();
  elements.payloadInput.value = text;
  const payload = JSON.parse(text);
  applyPayload(payload, `Imported file: ${file.name}`);
}

elements.loadSampleButton.addEventListener("click", async () => {
  try {
    await loadSample();
  } catch (error) {
    renderEmptyDashboard(error instanceof Error ? error.message : String(error));
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

render();
loadSample().catch((error) => {
  renderEmptyDashboard(error instanceof Error ? error.message : String(error));
});

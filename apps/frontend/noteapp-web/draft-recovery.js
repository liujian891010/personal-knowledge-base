export const DRAFT_RECOVERY_STORAGE_KEY = "noteapp-web.draft-recovery.v1";

function createEmptyStore() {
  return {
    version: 1,
    entries: {},
  };
}

export function createRuntimeSessionId(nowMs = Date.now()) {
  return `runtime-${nowMs}-${Math.random().toString(16).slice(2, 10)}`;
}

export function parseDraftRecoveryStore(rawValue) {
  if (!rawValue) {
    return createEmptyStore();
  }

  let parsed = rawValue;
  if (typeof rawValue === "string") {
    try {
      parsed = JSON.parse(rawValue);
    } catch {
      return createEmptyStore();
    }
  }

  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return createEmptyStore();
  }

  const rawEntries = parsed.entries;
  if (!rawEntries || typeof rawEntries !== "object" || Array.isArray(rawEntries)) {
    return createEmptyStore();
  }

  const entries = {};
  for (const [noteId, entry] of Object.entries(rawEntries)) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      continue;
    }
    entries[noteId] = {
      noteId,
      title: typeof entry.title === "string" ? entry.title : "",
      body: typeof entry.body === "string" ? entry.body : "",
      path: typeof entry.path === "string" ? entry.path : "",
      sectionId: typeof entry.sectionId === "string" ? entry.sectionId : "",
      sectionLabel: typeof entry.sectionLabel === "string" ? entry.sectionLabel : "",
      workspaceSourceLabel: typeof entry.workspaceSourceLabel === "string" ? entry.workspaceSourceLabel : "",
      sessionId: typeof entry.sessionId === "string" ? entry.sessionId : "",
      updatedAtMs: typeof entry.updatedAtMs === "number" ? entry.updatedAtMs : 0,
    };
  }

  return {
    version: 1,
    entries,
  };
}

export function serializeDraftRecoveryStore(store) {
  return JSON.stringify(parseDraftRecoveryStore(store));
}

export function buildDraftRecoveryEntry(
  { noteId, title, body, path, sectionId, sectionLabel, workspaceSourceLabel },
  { sessionId, updatedAtMs = Date.now() } = {},
) {
  return {
    noteId,
    title: String(title || ""),
    body: String(body || ""),
    path: String(path || ""),
    sectionId: String(sectionId || ""),
    sectionLabel: String(sectionLabel || ""),
    workspaceSourceLabel: String(workspaceSourceLabel || ""),
    sessionId: String(sessionId || ""),
    updatedAtMs,
  };
}

export function upsertDraftRecoveryEntry(store, entry) {
  const normalized = parseDraftRecoveryStore(store);
  return {
    version: 1,
    entries: {
      ...normalized.entries,
      [entry.noteId]: {
        ...entry,
      },
    },
  };
}

export function removeDraftRecoveryEntry(store, noteId) {
  const normalized = parseDraftRecoveryStore(store);
  if (!normalized.entries[noteId]) {
    return normalized;
  }

  const entries = { ...normalized.entries };
  delete entries[noteId];
  return {
    version: 1,
    entries,
  };
}

function findSectionLabelByNoteId(workspaceShell, noteId) {
  for (const section of workspaceShell?.sections || []) {
    for (const item of section.items || []) {
      if (item.id === noteId) {
        return section.label || "";
      }
    }
  }
  return "";
}

export function listRecoverableDrafts(store, { currentSessionId, workspaceShell } = {}) {
  const normalized = parseDraftRecoveryStore(store);
  return Object.values(normalized.entries)
    .filter((entry) => entry.sessionId && entry.sessionId !== currentSessionId)
    .map((entry) => {
      const note = workspaceShell?.notes?.[entry.noteId] || null;
      return {
        ...entry,
        noteMissing: !note,
        noteTitle: note?.title || "",
        notePath: note?.path || "",
        sectionLabel: entry.sectionLabel || findSectionLabelByNoteId(workspaceShell, entry.noteId),
      };
    })
    .sort((left, right) => right.updatedAtMs - left.updatedAtMs);
}

import assert from "node:assert/strict";

import {
  buildDraftRecoveryEntry,
  createRuntimeSessionId,
  listRecoverableDrafts,
  parseDraftRecoveryStore,
  removeDraftRecoveryEntry,
  serializeDraftRecoveryStore,
  upsertDraftRecoveryEntry,
} from "../draft-recovery.js";

const WORKSPACE_SHELL = {
  sections: [
    {
      id: "notes",
      label: "笔记",
      items: [
        {
          id: "doc-a",
          title: "文档 A",
          path: "Notes/A.md",
        },
      ],
    },
  ],
  notes: {
    "doc-a": {
      title: "文档 A",
      path: "Notes/A.md",
      body: "saved",
    },
  },
};

export async function runDraftRecoveryTests() {
  const results = [];

  {
    const store = parseDraftRecoveryStore("not-json");
    assert.deepEqual(store, { version: 1, entries: {} });
    results.push("parseDraftRecoveryStore falls back to an empty store for invalid JSON");
  }

  {
    const currentSessionId = createRuntimeSessionId(1000);
    let store = parseDraftRecoveryStore(null);
    store = upsertDraftRecoveryEntry(
      store,
      buildDraftRecoveryEntry(
        {
          noteId: "doc-a",
          title: "文档 A 草稿",
          body: "draft-a",
          path: "Notes/A.md",
          sectionId: "notes",
          sectionLabel: "笔记",
          workspaceSourceLabel: "浏览器本地草稿",
        },
        {
          sessionId: "previous-session",
          updatedAtMs: 3000,
        },
      ),
    );
    store = upsertDraftRecoveryEntry(
      store,
      buildDraftRecoveryEntry(
        {
          noteId: "doc-b",
          title: "文档 B 草稿",
          body: "draft-b",
          path: "Notes/B.md",
          sectionId: "notes",
          sectionLabel: "笔记",
          workspaceSourceLabel: "浏览器本地草稿",
        },
        {
          sessionId: currentSessionId,
          updatedAtMs: 4000,
        },
      ),
    );

    const recoverable = listRecoverableDrafts(store, {
      currentSessionId,
      workspaceShell: WORKSPACE_SHELL,
    });

    assert.equal(recoverable.length, 1);
    assert.equal(recoverable[0].noteId, "doc-a");
    assert.equal(recoverable[0].noteMissing, false);
    assert.equal(recoverable[0].sectionLabel, "笔记");
    results.push("listRecoverableDrafts filters out drafts from the current runtime session");
  }

  {
    let store = parseDraftRecoveryStore(null);
    store = upsertDraftRecoveryEntry(
      store,
      buildDraftRecoveryEntry(
        {
          noteId: "doc-a",
          title: "文档 A 草稿",
          body: "draft-a",
          path: "Notes/A.md",
        },
        {
          sessionId: "older-session",
          updatedAtMs: 2000,
        },
      ),
    );

    store = removeDraftRecoveryEntry(store, "doc-a");
    const roundTrip = parseDraftRecoveryStore(serializeDraftRecoveryStore(store));
    assert.deepEqual(roundTrip, { version: 1, entries: {} });
    results.push("removeDraftRecoveryEntry clears persisted drafts and survives serialization round-trips");
  }

  return results;
}

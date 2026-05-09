import assert from "node:assert/strict";

import {
  buildAiCopilotAnswerFromPayload,
  buildAiWikiCompileFromPayload,
} from "../ai-boundary.js";

export async function runAiBoundaryTests() {
  const completed = [];

  const answer = buildAiCopilotAnswerFromPayload({
    question: "下一步怎么做？",
    scopeLabel: "当前文档",
    scopeDetail: "聚焦单篇笔记",
    anchorNote: {
      id: "note-1",
      title: "同步桥接",
    },
    scopeNotes: [
      {
        id: "note-1",
        title: "同步桥接",
        summary: "记录当前桥接状态和下一步整理方向。",
      },
    ],
    aggregatedSuggestions: ["生成一篇跟进笔记"],
    syncContext: {
      actions: ["先保存草稿"],
      signals: [],
      hasBlockingSyncWork: false,
    },
    aiBriefing: {
      summary: "需要先把当前结论固化下来。",
      outline: ["现状", "下一步"],
    },
    nowMs: 123,
  });

  assert.equal(answer.anchorNoteId, "note-1");
  assert.equal(answer.scopeLabel, "当前文档");
  assert.equal(answer.generatedAtMs, 123);
  assert.ok(answer.headline.includes("下一步") || answer.summary.includes("推进"));
  completed.push("buildAiCopilotAnswerFromPayload returns a structured answer payload");

  const wiki = buildAiWikiCompileFromPayload({
    targetTitle: "同步桥接知识页",
    scopeLabel: "工作区",
    anchorNote: {
      id: "note-1",
      title: "同步桥接",
    },
    scopeNotes: [
      {
        id: "note-1",
        title: "同步桥接",
        path: "Notes/同步桥接.md",
        summary: "记录桥接现状。",
      },
      {
        id: "note-2",
        title: "知识编译",
        path: "Notes/知识编译.md",
        summary: "记录知识页结构。",
      },
    ],
    topEntities: ["桥接", "知识页"],
    topSuggestions: ["先人工校对"],
    syncContext: {
      actions: ["先保存草稿"],
      signals: ["当前没有额外风险"],
    },
    aiBriefing: {
      headline: "建议先核对结构",
      summary: "两篇文档都需要整理成统一知识页。",
    },
    nowMs: 456,
  });

  assert.equal(wiki.title, "同步桥接知识页");
  assert.equal(wiki.generatedAtMs, 456);
  assert.deepEqual(wiki.sourceNoteIds, ["note-1", "note-2"]);
  assert.ok(wiki.body.includes("## 编译范围"));
  assert.ok(wiki.body.includes("同步桥接"));
  completed.push("buildAiWikiCompileFromPayload returns a structured wiki draft payload");

  return completed;
}

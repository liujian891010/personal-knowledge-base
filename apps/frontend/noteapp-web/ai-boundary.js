function normalizeStringList(items, limit = 8) {
  if (!Array.isArray(items)) {
    return [];
  }
  return Array.from(
    new Set(
      items
        .map((item) => (typeof item === "string" ? item.trim() : ""))
        .filter(Boolean),
    ),
  ).slice(0, limit);
}

function formatGeneratedAt(nowMs = Date.now()) {
  return new Date(nowMs).toLocaleString("zh-CN", { hour12: false });
}

export function buildAiCopilotAnswerFromPayload(body = {}) {
  const question = typeof body.question === "string" && body.question.trim() ? body.question.trim() : "这篇内容下一步应该怎么推进？";
  const scopeLabel = typeof body.scopeLabel === "string" && body.scopeLabel.trim() ? body.scopeLabel.trim() : "当前文档";
  const scopeDetail = typeof body.scopeDetail === "string" ? body.scopeDetail.trim() : "";
  const anchorNote = body.anchorNote && typeof body.anchorNote === "object" ? body.anchorNote : {};
  const syncContext = body.syncContext && typeof body.syncContext === "object" ? body.syncContext : {};
  const aiBriefing = body.aiBriefing && typeof body.aiBriefing === "object" ? body.aiBriefing : {};
  const scopeNotes = Array.isArray(body.scopeNotes) ? body.scopeNotes.filter((item) => item && typeof item === "object") : [];
  const aggregatedEntities = normalizeStringList(body.aggregatedEntities, 5);
  const aggregatedLint = normalizeStringList(body.aggregatedLint, 3);
  const aggregatedSuggestions = normalizeStringList(body.aggregatedSuggestions, 4);
  const normalizedQuestion = question.replace(/\s+/g, "");
  const noteTitle = typeof anchorNote.title === "string" && anchorNote.title.trim() ? anchorNote.title.trim() : "当前文档";
  const noteSummaries = scopeNotes
    .slice(0, 3)
    .map((entry) => `《${entry.title || "未命名文档"}》：${entry.summary || "待补正文摘要"}`);

  let headline = `${scopeLabel}里最值得先推进的是补齐结论并明确下一步。`;
  let summary = `${scopeDetail || "这是当前作用范围的整理结果"}。当前焦点仍是《${noteTitle}》，${aiBriefing.summary || "建议先把当前结论写稳，再继续推进。"}。`;
  const bullets = [];

  if (/风险|问题|阻塞|冲突|卡住|告警/.test(normalizedQuestion)) {
    headline = `${scopeLabel}里最需要先处理的是同步阻塞和内容风险。`;
    summary = syncContext.hasBlockingSyncWork
      ? "当前同步侧仍有阻塞项，建议先把阻塞原因和待确认项写清楚，再继续提交或拉取。"
      : "当前没有明显同步阻塞，但仍建议先核对 AI 提示和草稿中的模糊表述。";
    bullets.push(
      body.draftDirty ? "先保存本地草稿，避免带着未定稿内容进入同步动作。" : "草稿已对齐，可以直接核对同步侧动作。",
      aggregatedLint[0] || "优先核对这篇内容里的风险提示，确认没有遗漏前置条件。",
      normalizeStringList(syncContext.signals, 3)[0] || "当前没有命中的同步告警，可继续关注内容质量。",
    );
  } else if (/下一步|行动|推进|待办|怎么做/.test(normalizedQuestion)) {
    headline = body.draftDirty ? "建议先定稿，再推进同步或跟进行动。" : "当前最适合先完成一条明确的下一步动作。";
    summary = body.draftDirty
      ? "这篇内容还带着未保存草稿，先把当前结论保存下来，再决定是否进入同步或拆分跟进任务。"
      : "这篇内容已经具备继续推进的条件，可以按优先级依次处理同步动作、结构补充和后续笔记拆分。";
    bullets.push(
      body.draftDirty ? "先保存当前草稿，并确认标题与正文是否已经能代表当前结论。" : "保持当前草稿稳定，优先执行最靠前的一条同步或整理动作。",
      normalizeStringList(syncContext.actions, 4)[0] || "补一段明确结论，减少后续回看成本。",
      aggregatedSuggestions[0] || "如需继续拆分任务，可直接生成一篇跟进笔记。",
    );
  } else if (/关系|关联|实体|联系/.test(normalizedQuestion)) {
    headline = `${scopeLabel}当前最集中的关联线索已经浮出来了。`;
    summary = aggregatedEntities.length
      ? `当前高频实体主要集中在 ${aggregatedEntities.join("、")}，它们构成了这一轮整理和追问的主线。`
      : "当前还没有明显的高频实体线索，更适合先补正文和结构。";
    bullets.push(
      aggregatedEntities[0] ? `优先围绕“${aggregatedEntities[0]}”回看相关文档，确认术语和上下文是否一致。` : "先补正文中的实体名词，后续图谱和 AI 面板会更稳定。",
      noteSummaries[0] || `当前焦点《${noteTitle}》是最直接的入口。`,
      noteSummaries[1] || "如果需要跨文档梳理，可切到知识图谱继续查看相关节点。",
    );
  } else if (/结构|大纲|整理|重组/.test(normalizedQuestion)) {
    headline = "先把结构骨架搭稳，再继续补细节会更高效。";
    summary = Array.isArray(aiBriefing.outline) && aiBriefing.outline.length
      ? `当前已经识别到 ${aiBriefing.outline.length} 个结构节点，可在此基础上继续补背景、结论和下一步。`
      : "当前正文还缺少明显的小节结构，建议先补齐背景、核心信息和下一步三个最小分段。";
    bullets.push(
      aiBriefing.outline?.[0] ? `保留“${aiBriefing.outline[0]}”作为主骨架，再补 1-2 个平级小节。` : "先补“背景 / 核心信息 / 下一步”三段最小结构。",
      noteSummaries[0] || "先提炼一段不超过 50 字的结论放在开头。",
      aggregatedSuggestions[0] || "结构补齐后，再决定是否生成跟进笔记。",
    );
  } else {
    bullets.push(
      noteSummaries[0] || `《${noteTitle}》目前仍是这一轮整理的主入口。`,
      normalizeStringList(syncContext.actions, 4)[0] || aiBriefing.headline || "建议先写清当前结论。",
      aggregatedSuggestions[0] || "如需沉淀更多结论，可把本轮回答直接写回草稿。",
    );
  }

  return {
    anchorNoteId: typeof anchorNote.id === "string" ? anchorNote.id : "",
    question,
    scopeLabel,
    headline,
    summary,
    bullets: bullets.filter(Boolean).slice(0, 4),
    sources: scopeNotes.slice(0, 3).map((entry) => ({ id: entry.id, title: entry.title })),
    entities: aggregatedEntities,
    generatedAtMs: typeof body.nowMs === "number" ? body.nowMs : Date.now(),
  };
}

export function buildAiWikiCompileFromPayload(body = {}) {
  const targetTitle = typeof body.targetTitle === "string" && body.targetTitle.trim() ? body.targetTitle.trim() : "AI 知识页";
  const anchorNote = body.anchorNote && typeof body.anchorNote === "object" ? body.anchorNote : {};
  const scopeLabel = typeof body.scopeLabel === "string" && body.scopeLabel.trim() ? body.scopeLabel.trim() : "当前文档";
  const scopeNotes = Array.isArray(body.scopeNotes) ? body.scopeNotes.filter((item) => item && typeof item === "object") : [];
  const aiBriefing = body.aiBriefing && typeof body.aiBriefing === "object" ? body.aiBriefing : {};
  const syncContext = body.syncContext && typeof body.syncContext === "object" ? body.syncContext : {};
  const topEntities = normalizeStringList(body.topEntities, 8);
  const topLint = normalizeStringList(body.topLint, 4);
  const topSuggestions = normalizeStringList(body.topSuggestions, 4);
  const nowMs = typeof body.nowMs === "number" ? body.nowMs : Date.now();
  const sourceRows = scopeNotes
    .slice(0, 8)
    .map(
      (entry, index) =>
        `${index + 1}. 《${entry.title || "未命名文档"}》\n   - 路径：${entry.path || "待补路径"}\n   - 摘要：${entry.summary || "待补正文摘要"}`,
    );

  return {
    title: targetTitle,
    body: `# ${targetTitle}

## 编译范围
- 作用范围：${scopeLabel}
- 覆盖文档：${scopeNotes.length} 篇
- 焦点文档：${anchorNote.title || "当前文档"}
- 生成时间：${formatGeneratedAt(nowMs)}

## 核心结论
- ${aiBriefing.headline || "建议先核对结构与结论"}
- ${aiBriefing.summary || "建议先把当前作用范围内的重点结论沉淀下来。"}
- ${scopeNotes.length > 1 ? `当前编译同时吸收了 ${scopeNotes.length} 篇相关文档的上下文。` : "当前编译主要围绕单篇笔记展开。"}

## 关键实体
${topEntities.length ? topEntities.map((entity) => `- ${entity}`).join("\n") : "- 当前没有稳定的实体提取结果，可先补正文再重新编译。"}

## 同步与风险
${[...normalizeStringList(syncContext.signals, 4), ...topLint].filter(Boolean).slice(0, 4).map((item) => `- ${item}`).join("\n") || "- 当前没有额外的同步或校对风险。"}

## 来源文档
${sourceRows.join("\n") || "1. 当前没有可展开的来源文档摘要。"}

## 建议下一步
${[...normalizeStringList(syncContext.actions, 5), ...topSuggestions].filter(Boolean).slice(0, 5).map((item, index) => `${index + 1}. ${item}`).join("\n") || "1. 继续补充这篇知识页的结构和结论。"}
`,
    sourceNoteIds: scopeNotes.map((entry) => entry.id).filter((id) => typeof id === "string").slice(0, 12),
    sourceScopeLabel: scopeLabel,
    topEntities,
    warnings: topEntities.length ? 0 : 1,
    suggestions: [
      "先人工核对这篇知识页，再决定是否继续补结构或进入正式同步。",
      "如果范围过大，可切回单篇笔记或分区后重新编译。",
    ],
    lint: topEntities.length ? [] : ["当前实体提取较弱，建议补充正文后重新编译。"],
    generatedAtMs: nowMs,
  };
}

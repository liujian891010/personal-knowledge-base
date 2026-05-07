# ai-boundary

V1 的 AI 产物边界只冻结同步集合和控制规则，不冻结模型供应商或具体编译实现。

## 参与常规跨端同步

以下内容进入常规 sync set：

1. `.ai/wiki/`
2. `.ai/index.md`
3. `.ai/AGENTS.md`

这些文件都属于用户可审计、可迁移、可回溯的知识资产。

## 不参与常规跨端同步

以下内容默认不进入 manifest，也不进入常规跨端同步：

1. `.ai/raw/`
2. `.ai/log.md`

其中：

1. `.ai/raw/` 是本地缓存和中间产物，可以在导出完整迁移包时按显式选项附带，但不能被本地设置提升为常规 sync 成员
2. `.ai/log.md` 是本地审计日志，不参与常规同步，也不应作为远端收敛依据

## wiki 页面最小契约

`.ai/wiki/` 中的逻辑页面结构由 `packages/protocol/schemas/wiki-page.schema.json` 定义，v1 冻结：

1. `page_type`
2. `summary`
3. `body_markdown`
4. `source_refs`
5. `related_pages`
6. `ai_generated`
7. `user_edited`
8. `locked`
9. `last_ai_compiled_at`
10. `last_compiled_from_sources_hash`
11. `last_synced_revision`

## 运行边界

1. AI 问答优先检索 `.ai/wiki/`
2. wiki 不足时才回查原始笔记、附件抽取文本或其他 raw 层
3. AI 输出写回默认需要用户确认
4. 禁止后台静默改写 `.ai/wiki/`
5. 禁止把“整库直接问答”作为默认主路径

## 与同步层的关系

1. `.ai/wiki/`、`.ai/index.md`、`.ai/AGENTS.md` 和普通文件一样走加密 blob 同步
2. manifest 中允许出现 `ai_wiki`、`ai_index`、`ai_agents` 三种 AI 文件类型
3. `.ai/raw/` 永远不应进入 manifest v1 的 `files[]`

参考 fixture：

1. `packages/protocol/fixtures/wiki-page/golden-topic.json`

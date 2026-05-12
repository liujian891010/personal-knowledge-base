# AI Context Task Panel Plan

记录日期：2026-05-11

## 目标

把“当前笔记、文件夹、搜索结果、用户多选文档”统一收口为一个能力：把选取内容加入 AI 文档上下文，然后在独立 AI 文档页持续对话。总结、写报告、提炼行动项、改写、对比、找风险都只是同一个上下文会话里的不同指令。

核心原则：

1. 用户不是只和 AI 聊天，而是在明确文档上下文里持续交流。
2. 所有任务都必须基于可追溯来源生成，输出保留 citations / sources。
3. 输出先预览，再由用户决定插入当前笔记或保存为新知识页。
4. 第一版优先支持真实高频场景：选中文件夹或多个文档后，跳转到 AI 文档页，将这些文档内容作为持续上下文，再让用户自由输入任务。

## 上下文类型

```ts
type AiContext =
  | { type: 'current_note'; file_id: string }
  | { type: 'folder'; folder_path: string; recursive?: boolean }
  | { type: 'search_results'; query: string; file_ids: string[] }
  | { type: 'selected_files'; file_ids: string[] };
```

场景说明：

1. `current_note`：总结当前打开的 Markdown。
2. `folder`：用户选中文件夹，系统把该文件夹下 Markdown 内容加入 AI 上下文。
3. `search_results`：用户搜索后，把命中的文档加入 AI 上下文。
4. `selected_files`：用户多选几个文档，把这些文档加入 AI 上下文。

## 统一请求结构

```ts
type AiTaskRequest = {
  task: 'summarize' | 'write_report' | 'ask' | 'extract' | 'rewrite';
  context: AiContext;
  instruction: string;
  template_id?: string;
  output: {
    mode: 'preview' | 'insert_current_note' | 'create_note';
    insert_position?: 'cursor' | 'append' | 'prepend';
    target_path?: string;
  };
};
```

第一版可以只实现 `preview`，再补 `insert_current_note` 和 `create_note`。

## UI 交互

### 入口

1. 文件树右键文件夹：`加入 AI 上下文` / `用 AI 处理此文件夹`。
2. 文件树多选 Markdown 后：`加入 AI 上下文` / `基于所选文档生成`。
3. 当前笔记工具栏：`把当前笔记加入 AI 上下文`。
4. 搜索页结果区：`把搜索结果加入 AI 上下文`。
5. AI Wiki 页保留 `Ask AI Wiki`，作为基于已编译知识库的问答入口。

### AI 文档对话页

参考 ChatGPT 交互模式，AI 上下文不是一次性浮层，而是一个可持续交流的 AI 文档页面，包含：

1. 上下文摘要：例如 `文件夹：Projects/Alpha`，或 `已选择 5 个文档`。
2. 来源列表：显示参与生成的文件，可移除单个文档。
3. 指令输入框：用户自由输入，例如 `给我总结一下`、`基于这些材料写一份汇报`、`找出风险和待办`、`对比这些方案的差异`。
4. 模板选择：无模板、通用总结、通用报告、项目汇报、调研报告、周报、决策建议、风险分析。
5. 生成按钮：调用 provider，展示 loading、错误和 no-citation / no-source 提示。
6. 结果预览：Markdown 渲染 + 来源引用列表。
7. 输出动作：`插入当前笔记`、`保存为新知识页`、`复制 Markdown`。
8. 持续对话：上下文保留在页面左侧，每一轮用户消息都基于当前上下文重新生成，并保留 sources。

## 文件夹上下文规则

第一版约束：

1. 只读取 active Markdown。
2. 排除 `.ai/`、`.noteapp/`、附件和非 Markdown。
3. 默认递归读取子目录，可以在 UI 提供“包含子文件夹”开关。
4. 最多读取 20 个 Markdown 文件。
5. 第一版设置临时保护阈值，例如每个文件最多取前 4000 字、总上下文最多 30000 字；该阈值必须可配置，不作为产品标准。
6. 后续应按模型上下文窗口和 token 预算动态计算，而不是固定按字符数截断。
7. 超出限制时在结果顶部提示“仅纳入前 N 个文件 / 部分内容已截断”。
8. 输出必须包含“来源”章节，除非用户明确要求只做草稿且不展示来源。

## 多选文档上下文规则

用户多选 Markdown 后，这些文件会成为本次 AI 任务的上下文。用户可以要求总结、写报告、提炼观点、对比差异、生成待办、改写成邮件等。

第一版约束：

1. 最多选择 20 个文件。
2. 只允许 Markdown 文档参与生成。
3. 按用户选择顺序或文件树顺序组装上下文。
4. 每个文件保留 title、path、excerpt/content slice。
5. 输出报告必须保留引用来源。
6. 如果有模板，严格按模板结构输出。
7. 不预设任务类型；task 可以根据用户指令映射为 `ask` / `write_report` / `summarize` / `extract` / `rewrite`，但 UI 上核心表达是“这些文档已加入上下文”。

## 模板

第一版使用内置模板，不先做模板编辑器。

```ts
type AiTemplate = {
  id: string;
  name: string;
  description: string;
  prompt: string;
};
```

建议内置：

1. `none`：无模板。
2. `general-summary`：通用总结。
3. `general-report`：通用报告。
4. `project-report`：项目汇报。
5. `research-report`：调研报告。
6. `weekly-report`：周报。
7. `decision-brief`：决策建议。
8. `risk-analysis`：风险分析。

后续可支持 `.noteapp/ai-templates/*.md` 用户自定义模板。

## 插入当前笔记

第一版建议先支持 `append`，不要先做光标插入。

原因：

1. 光标插入依赖编辑器内部状态，容易和草稿/保存状态冲突。
2. 追加到当前笔记末尾更安全，可复用现有编辑器草稿和保存流程。

插入格式：

```md
## AI 生成 - 2026-05-11

生成内容...

### 来源

- [[source one]]
- [[source two]]
```

插入后只更新编辑器草稿，不直接写盘；用户保存时走现有保存逻辑。

## 保存为新知识页

不要保存到 `.ai/wiki`，因为 `.ai/` 是系统目录且普通笔记库隐藏。

建议保存到用户可见目录：

```text
AI Notes/
  2026-05-11 folder-name 总结.md
```

frontmatter：

```md
---
type: ai_generated_note
source_type: folder
source_path: Projects/example
template_id: project-report
created_at: 2026-05-11T00:00:00+08:00
---
```

## 后端实现顺序

1. 新增 `ai-context-task` service 方法，先支持 `folder` 和 `selected_files` 的 `preview`。
2. 增加 CLI：`run-ai-context-task`，方便测试。
3. 增加 bridge：`POST /api/ai/context-task`。
4. 复用现有 provider config / health check / provider dispatch。
5. 组装 sources/citations，调用真实模型。
6. 返回 answer、sources、model_status、truncation_info。
7. 再补 `insert_current_note` 和 `create_note` 输出动作。

## 前端实现顺序

1. 文件树支持多选 Markdown。
2. 文件夹右键增加 `AI 总结此文件夹`。
3. 多选文档后增加 `AI 生成报告`。
4. 新增 `AiContextTaskPanel`。
5. 支持模板选择、指令输入、结果预览、来源列表。
6. 支持追加插入当前笔记。
7. 支持保存为 `AI Notes/*.md`。
8. 再补搜索结果总结和当前笔记总结入口。

## 明天优先级

当前进展：

1. `folder + preview` 已实现：文件夹行可将该文件夹下 Markdown 加入 AI 文档页，用户可持续输入任意指令后生成结果。
2. `selected_files + preview` 已实现：文件树可多选 Markdown，将这些文档加入 AI 文档页，用户可持续输入任意指令后生成结果。
3. 输出已带来源、模型状态和临时截断提示。
4. 前端已从一次性浮层调整为独立 `AI 文档` 页面，Explorer 只负责选择上下文并跳转。

后续继续：

1. 支持将结果追加插入当前笔记。
2. 支持保存为 `AI Notes/*.md` 新知识页。
3. 支持当前笔记入口和搜索结果入口。
4. 将临时字符阈值升级为按模型上下文窗口和 token 预算动态计算。

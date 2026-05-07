# 类Obsidian软件最终可落地方案（本地优先 + 多端同步 + AI知识库版）

> 版本：`v1.0.43`
>
> 本次更新（基于 v1.0.42 → v1.0.43 的 Codex 审核）：
>
> **P1 修复**
> 1. §8.2 / §10.4 升级兼容：补充旧 `local_tombstone_ledger` 缺失 `local_delete_seq` 时的启动期补号规则，避免升级后首个新 commit 无法回写升级前遗留 tombstone
>
> **P2 修复**
> 2. §17.2 test 47：收紧为仅覆盖“`intent_delete_seq_upper_bound` 存在”的当前版本 submitted 恢复路径，避免与 legacy fallback 语义混淆

## 一、方案结论

这是一份面向小团队落地的最终版方案，目标不是复制 Obsidian 的全部生态，而是做出一个：

1. **本地优先**
2. **多端可同步**
3. **Markdown 原文编辑优先**
4. **支持双向链接与知识组织**
5. **内置 AI 知识编译与问答能力**

的可交付产品。

最终判断：

1. **可以立项**
2. **必须收缩 V1 范围**
3. **桌面端必须优先**
4. **同步协议必须先于业务开发冻结**
5. **AI 不能只做轻量 RAG 助手，而要做知识编译层**

这份方案综合了以下几轮共识：

1. 本地文件必须是主数据
2. SQLite 必须存在，但只做本地索引、状态和 AI 辅助层，不做主存储
3. 同步系统不能只靠时间戳和版本自增，必须有 `base_revision`、`manifest`、`tombstone` 和 CAS
4. AI 必须成为知识库主入口之一
5. AI 路线不再采用“轻量 RAG 优先”，而采用更接近 **Andrej Karpathy 于 2026 年 4 月 4 日公开的 `LLM Wiki` 思路**的“编译型知识库”路线

---

## 二、产品定义

### 2.1 产品定位

本产品是一个面向个人和小团队的 AI-native 知识库工具。

它有三个核心层：

1. **原始内容层**
   - Markdown 笔记
   - 附件
   - 用户配置
2. **同步一致性层**
   - 保证多端不丢数据
   - 保证本地优先
3. **AI 知识层**
   - 将原始内容持续编译为结构化知识页
   - 在结构化知识层上完成问答、总结、提纲、整理和辅助写作

### 2.2 目标用户

优先用户：

1. 重度 Markdown 笔记用户
2. 研究、咨询、产品、投资、学习型用户
3. 希望把零散资料整理成长期知识资产的个人用户

不优先用户：

1. 需要多人实时协作的团队
2. 强依赖复杂插件生态的 Obsidian 老用户
3. 只需要轻量待办或简单便签的用户

### 2.3 与传统笔记工具的差异

核心差异不是“也能写 Markdown”，而是：

1. 所有内容本地可见、可迁移
2. 同步只做中转，不劫持主数据
3. AI 建立在知识库层之上，不只是聊天框
4. AI 会持续沉淀 `.ai/wiki/` 结构化知识页

---

## 三、核心原则

### 3.1 本地优先

必须满足：

1. 没网可用
2. 云端故障不影响本地阅读编辑
3. 用户能直接看到自己的笔记文件
4. 用户可以脱离产品迁移数据

平台说明：

1. 桌面端必须强满足以上四条
2. 移动端受系统沙箱限制，V1 可接受“私有目录存储 + 明确的完整 Vault 导出能力”作为降级方案
3. 若用户在移动端选择系统文件选择器授权目录，则应尽量满足“文件可见”
4. 即使使用私有目录，移动端也必须提供“导出完整 Vault（含 `.noteapp/filemap.json`）”能力，以满足可迁移要求

### 3.2 稳定优先于炫技

V1 不追求：

1. 复杂富文本体验
2. 巨大插件生态
3. 激进的实时同步
4. 大而全的 Agent 系统

V1 优先追求：

1. 不丢数据
2. 编辑稳定
3. 同步可解释
4. AI 有依据、可追溯

### 3.3 AI 是知识编译器，不只是问答器

AI 的职责不只是：

1. 回答问题

更重要的是：

1. 编译资料
2. 生成结构化知识页
3. 持续维护知识库索引
4. 让后续问答建立在知识层而不是原始碎片之上

### 3.4 文件系统是主存储，SQLite 是辅助存储

这是必须坚持的边界：

1. **文件系统 = Source of Truth**
2. **SQLite = Index / Cache / State / AI辅助层**
3. **云端 = Sync Relay / Backup Layer**

---

## 四、V1 范围

### 4.1 V1 上线必须闭环

#### A. 桌面端

1. 多 Vault 管理
2. Markdown 原文编辑
3. 分屏预览
4. 附件插入与预览
5. 双向链接与反向链接
6. 本地搜索
7. 基础主题和设置
8. 本地自动保存

#### B. 同步

1. 桌面端与桌面端同步
2. E2EE
3. 冲突副本
4. 同步日志
5. 断点续传
6. 脏工作树保护（拉取远端时不覆盖未提交本地修改）
7. 设备管理与 `acked_revision` / tombstone 回收闭环

#### C. AI 能力

1. AI 对话窗口
2. 当前笔记总结
3. 文件夹总结
4. 搜索结果总结
5. 引用式回答
6. 生成 `.ai/wiki/` 知识页
7. 结果插入当前笔记或新建知识页
8. 基于知识页的桌面端问答 MVP

### 4.2 V1.1 紧随迭代（移动端最小接入，不阻塞 V1 上线）

1. 笔记浏览
2. 简单编辑
3. 手动同步
4. 前台准实时同步
5. 当前笔记 AI 总结
6. 简易问答

### 4.3 V1 明确不做

1. 插件市场
2. 多人实时协作
3. Git / WebDAV 多协议同步
4. 自动批量改写整个知识库
5. 大型 Agent 工作流平台
6. 富文本所见即所得编辑器
7. 大规模图谱优化
8. 移动端后台强实时同步承诺
9. 复杂版本回滚 UI
10. 平台级 AI 计费系统
11. 移动端 AI 全量能力与移动端作为首发平台
12. 不经知识页收敛的“整库直接问答”主路线；V1 允许“整个 Vault”作为**显式触发的批量知识编译范围**，但不允许把它做成默认问答入口

---

## 五、用户核心场景

### 5.1 基础笔记流

1. 用户创建 Vault
2. 新建 Markdown 笔记
3. 建立 `[[wiki link]]`
4. 搜索、浏览、整理

### 5.2 多端同步流

1. 用户在桌面端编辑
2. 本地先落文件
3. 同步引擎检测变更
4. 上传加密 blob 与 manifest
5. 另一台设备拉取并回放变更

### 5.3 AI 知识编译流

1. 用户选中一篇笔记、一个文件夹、一个搜索结果集或整个 Vault（注：选择"整个 Vault"时，进入批量编译流程，不作为直接问答入口；见 §4.3）
2. 点击 `Ingest` / `总结` / `编译知识页`
3. 系统抽取原始内容并切分
4. AI 生成结构化 wiki 页面
5. 系统更新 `.ai/index.md` 和页面链接
6. 后续问答优先针对 `.ai/wiki/` 层进行

### 5.4 AI 问答流

1. 用户输入问题
2. 系统确定范围
3. 优先检索 `.ai/wiki/`
4. 不足时回查 raw sources
5. 生成引用式回答
6. 用户决定是否插入当前笔记或沉淀成新知识页

---

## 六、总架构

### 6.1 总体结构

```text
桌面端(Tauri) / 移动端(Flutter，V1.1)
        ↓
共享核心(Rust Core)
        ↓
本地文件系统 + SQLite
        ↓
AI知识编译与上下文编排层
        ↓
同步客户端(Sync Engine)
        ↓
轻量服务端(API)
        ↓
对象存储(OSS/COS/S3兼容)
        ↓
可选模型服务(BYOK/轻量Gateway)
```

### 6.2 分层职责

#### A. UI 层

负责：

1. 编辑器
2. 文件树
3. 搜索页
4. 设置页
5. 同步状态
6. AI 对话与引用面板

不负责：

1. 同步协议逻辑
2. 搜索索引实现
3. 加密实现
4. AI 编译流程核心逻辑

#### B. 共享核心层

建议由 Rust 承担：

1. Vault 元数据管理
2. 双向链接解析
3. 搜索索引维护
4. chunk 切分
5. manifest 读写
6. 冲突检测
7. 加解密
8. AI 知识页 schema 校验

#### C. 本地存储层

由文件系统和 SQLite 共同组成：

1. 文件系统存主数据
2. SQLite 存状态、索引、缓存、AI 辅助数据

#### D. AI 知识编译层

负责：

1. Ingest
2. Compile
3. Index
4. Ask
5. 引用回跳
6. 知识页更新

#### E. 同步层

负责：

1. 文件变更检测
2. 本地 revision 维护
3. blob 上传下载
4. manifest commit
5. 冲突处理

#### F. 云端层

只负责：

1. 用户认证
2. 设备注册与管理
3. vault head 管理
4. blob 上传授权
5. blob 下载授权
6. commit CAS 并发控制
7. 设备心跳与活跃状态维护（账号级操作，不区分 vault）
8. `acked_revision` 记录维护
9. tombstone 回收资格判定

不负责：

1. 存储内容明文
2. AI 调用与编排
3. 客户端业务逻辑

> 详细职责见 §14.1

---

## 七、本地目录结构

建议采用统一结构：

```text
VaultRoot/
  .vaultinfo
  .noteapp/
    settings.json
    sync.json
    backup.json
    filemap.json
    tombstone-ledger.jsonl
    drafts/
    staging/
    staging-orphans/
    conflict-orphans/
  .ai/
    AGENTS.md
    index.md
    log.md
    raw/
    wiki/
  Notes/
  Attachments/
```

### 7.1 目录说明

1. `.vaultinfo`
   - 保存 `vault_id`、格式版本、创建时间
2. `.noteapp/`
   - 应用配置与 Vault 级元数据
3. `.noteapp/filemap.json`
   - 持久化保存 `file_id -> path / type / status` 映射，是文件身份的**唯一权威来源**
   - 不作为用户主要编辑对象；同步时不按“普通文件”上传，而是由其内容导出 `manifest`
4. `.noteapp/tombstone-ledger.jsonl`
   - `local_tombstone_ledger` 的文件侧持久化镜像，采用 append-only / checkpoint 友好的恢复格式
   - 用于 `filemap.json` 重建、SQLite 损坏后的 tombstone 恢复，以及防止仅靠当前可见文件系统误判“已删文件不存在历史”
   - 仅本地存在，**不参与跨端同步**
5. `.noteapp/drafts/`
   - 自动保存与异常退出恢复用的临时草稿目录
   - 仅本地存在，**不参与同步**
6. `.noteapp/staging/`
   - 同步引擎临时暂存目录，承载两类临时文件：
     - **拉取两阶段落盘**：以 `<file_id>.staging` 命名，由 §10.4 拉取流程第 10 步生成
     - **commit 快照与临时密文**：以 `<file_id>.snapshot.plain`（明文快照）与 `<blob_id>.blob.staging`（确定性加密产出的临时密文 blob）命名，由 §10.4 提交流程的"内容快照阶段"生成
   - 仅同步内部使用，**不参与跨端同步**；App 启动时若非空，进入**同步恢复判定**：
     - 若存在 `sync_apply_journal`，由 journal 驱动拉取阶段的恢复（仅处理 `<file_id>.staging`）
     - 若存在 `commit_intent_journal`，由该 journal 驱动 commit 阶段的恢复（处理 `<file_id>.snapshot.plain` 与 `<blob_id>.blob.staging`）
     - 同时存在两种 journal 时按"拉取先于提交"顺序串行恢复；不得绕开 journal 单独处理任何 staging 文件
7. `.noteapp/staging-orphans/`（**`.noteapp/` 直属，非 `staging/` 的子目录**）
   - 当 `.noteapp/staging/` 中的 staging 文件无法关联到任何 journal 与 manifest 时，统一移入此目录而非直接删除，等待人工审计
   - 仅本地存在，**不参与跨端同步**；不包含在完整 Vault 迁移包中
8. `.noteapp/conflict-orphans/`
   - 存放原目录已被删除的冲突副本（孤儿冲突副本）
   - 仅本地存在，**不参与跨端同步**
9. `.ai/raw/`
   - AI ingest 的原始来源映射、抽取文本、中间产物
10. `.ai/wiki/`
   - AI 编译后的结构化知识页
11. `.ai/index.md`
    - 知识页总索引
12. `.ai/log.md`
    - ingest / compile / query / lint 日志
13. `Notes/`
    - 用户笔记
14. `Attachments/`
    - 图片、PDF、音频、视频等附件

### 7.2 文件身份与迁移边界

1. `file_id` 不写入 Markdown 正文或附件本体，统一保存在 `VaultRoot/.noteapp/filemap.json`
2. `filemap.json` 属于 Vault 元数据，和 `Notes/`、`Attachments/` 一样需要随 Vault 一起迁移
3. 用户若导出并拷走**完整 Vault 迁移包**，视为**原 Vault 迁移**；完整迁移包必须同时包含 §7.2.3 所列全部必要文件（含 `filemap.json`）；若导出时 `.noteapp/conflict-orphans/` 非空，则该目录也必须一并包含，否则不视为完整迁移包
4. 用户若只拷 `Notes/` / `Attachments/` 而丢失 `.noteapp/filemap.json`，默认视为**内容导入**，不是原 Vault 身份的无损迁移；若同时包含 §7.2.3 中除 `filemap.json` 以外的其他文件，仍视为内容导入，不升级为原 Vault 迁移
5. 若 `filemap.json` 丢失但用户仍希望修复原 Vault，系统只能进入”身份修复模式”做 best-effort 恢复，不承诺保留全部原始 `file_id`
6. 远端与协议层**不直接把 `filemap.json` 当普通业务文件保存或裁决**；跨端同步使用的正式格式始终是由 `filemap.json` 导出的 `manifest`

`filemap.json` 的最小语义约束：

1. `status` 取值固定为：`active | deleted | conflict_copy`
2. `active`：当前正常存在的主文件
3. `deleted`：该 `file_id` 对应文件已被删除，但条目必须保留到对应 tombstone 完成回收后才可物理移除
4. `conflict_copy`：本地未解决的冲突副本文件；必须拥有新的 `file_id`，不得复用原文件的 `file_id`，且**不得导出为远端 tombstone**
5. 只要某个 `active` / `deleted` 条目仍可能参与 tombstone 判定、身份修复或规范状态解释，就不得从 `filemap.json` 中直接删除该条目；已解决的 `conflict_copy` 条目是例外，可在记录同步日志后直接从本地移除

### 7.2.1 filemap.json 写入保护

`filemap.json` 由 Rust Core 统一管理，必须满足：

1. **串行写入**：所有写入操作（新建、重命名、删除）通过单一写入队列串行执行，不允许并发写入
2. **原子替换**：写入时先写临时文件 `.noteapp/filemap.json.tmp`，写入完成后 rename 替换原文件，避免写入中断导致文件损坏
3. **启动校验**：App 启动时若发现 `.noteapp/filemap.json.tmp` 残留，说明上次写入未完成，优先尝试从 tmp 文件恢复，恢复失败则进入”身份修复模式”
4. **禁止直接编辑**：UI 层不得直接读写 `filemap.json`，所有操作必须通过 Rust Core 提供的接口
5. **唯一真相**：本地运行态下，凡涉及文件身份、路径、删除状态的判断，均以 `filemap.json` 为唯一依据；SQLite 和 manifest 都不得独立覆盖它
6. **同步收敛例外**：仅当客户端已经**成功应用**某个远端 manifest 后，才允许以该 manifest 对应的最终文件系统状态重写本地 `filemap.json`

### 7.2.2 `.noteapp/` 同步边界

V1 约定如下：

1. `.vaultinfo`：**不参与跨端同步**。每端在 vault 创建或恢复时本地生成；其中的 `vault_id` 必须与服务端记录一致——首端创建时由客户端生成并随首次 commit 写入服务端；新端恢复时从恢复包读取并写入本机 `.vaultinfo`，确保多端 `vault_id` 一致；其他字段（格式版本、创建时间）允许各端独立维护
2. `filemap.json`：参与跨端同步，但不作为普通文件上传，而是导出为 manifest
3. `settings.json`：默认仅本地生效，不跨端同步，除非后续单独定义”偏好同步”
4. `sync.json`：设备本地状态，不跨端同步
5. `backup.json`：设备本地状态，不跨端同步
6. `drafts/`：临时草稿，不跨端同步
7. `staging/`：同步引擎内部临时目录，不跨端同步
8. `staging-orphans/`：无法关联 journal 与 manifest 的孤立 staging 文件隔离目录，不跨端同步
9. `conflict-orphans/`：本地冲突副本存放目录，不跨端同步

`.ai/` 目录同步边界补充（与 §25.2 同步策略表一致）：

1. `.ai/wiki/`：参与跨端同步
2. `.ai/index.md`：参与跨端同步
3. `.ai/AGENTS.md`：参与跨端同步（编译规范，需保持多端一致）
4. `.ai/log.md`：**不参与跨端同步**，本地日志，亦不包含在完整 Vault 迁移包中
5. `.ai/raw/`：**不参与常规跨端同步**；V1 中它始终被视为本地缓存/中间产物。用户侧仅允许配置其本地体积上限、清理策略与“导出迁移包时是否附带”，**不得**通过任何本地设置把 `.ai/raw/` 纳入 manifest 或常规 sync set

### 7.2.3 完整 Vault 迁移包边界

V1 所谓“完整 Vault 迁移包”，明确指：

1. `.vaultinfo`
2. `Notes/`
3. `Attachments/`
4. `.ai/wiki/`
5. `.ai/index.md`
6. `.ai/AGENTS.md`
7. `.noteapp/filemap.json`
8. `.noteapp/conflict-orphans/`（**若目录非空则必须包含；若为空可省略空目录本体**）

默认**不包含**：

1. `.noteapp/settings.json`
2. `.noteapp/sync.json`
3. `.noteapp/backup.json`
4. `.noteapp/drafts/`
5. `.noteapp/staging/`（同步内部临时目录）
6. `.noteapp/staging-orphans/`（孤立 staging 文件隔离目录，审计用途）
7. `.ai/log.md`
8. `.ai/raw/`（默认不包含，除非用户显式选择一起导出）

导入规则：

1. 导入“完整 Vault 迁移包”后，目标设备必须重新生成自己的 `sync.json`、`backup.json` 和设备本地配置
2. 若迁移包中包含 `.noteapp/conflict-orphans/`，导入后必须同步恢复这些未解决冲突副本，并据此把本地 `has_unresolved_conflicts` 置为 `true`
3. 因此”原 Vault 迁移”保留的是**内容身份、知识结构与未解决本地冲突副本**，不是把旧设备的本地运行状态原封不动搬到新设备；**导入完成后不得直接视为”可立即继续提交”的已对齐同步基线**
4. 导入完成后，目标设备必须把同步运行态重置为”待重建基线”：重新生成 `sync.json`，并在首次联网时先执行一次 `pull / reconcile`，用远端最新 head 重建本地 `last_applied_revision`、`remote_head_revision`、`acked_revision` 与 `last_manifest_summary`；在该重建完成前，禁止新的 commit。**用户体验约束**：导入完成后、首次 pull/reconcile 重建基线前，若用户尝试编辑并触发 commit，系统必须在 Q-fm 临界区 A 的 `last_manifest_summary` gate 处拦截（见 §10.4 第 961 行），并向用户展示明确提示：”导入完成，请先联网同步以重建同步基线”；不得静默排队或误导用户认为 commit 已成功
5. V1 不存在“开启 `.ai/raw/` 常规跨端同步”的合法状态；导出界面若提供“附带 `.ai/raw/`”选项，仅影响**本次迁移包内容**，不得反向改变同步协议边界或后续 manifest 生成规则

**关于 `settings.json` 不同步的说明：**

V1 用户在桌面端修改主题或设置后，移动端不会自动同步，这是已知的体验落差，属于 V1 接受的限制。若后续需要跨端同步偏好，应单独定义 `sync_settings.json`，不复用 `settings.json`，以避免设备本地状态与跨端偏好混用。

### 7.3 为什么需要 `.ai/`

这是本方案与传统 RAG 知识库的关键差异。

`.ai/` 的价值：

1. 让 AI 的产出可见、可审计
2. 让问答建立在持续维护的知识层上
3. 让 AI 的总结与整理成为真正的“库内资产”

---

## 八、数据分层设计

### 8.1 哪些数据放文件系统

必须放文件：

1. Markdown 笔记本体
2. 附件文件
3. 用户可编辑配置
4. `.noteapp/filemap.json`
5. `.ai/wiki/` 知识页
6. `.ai/index.md`、`.ai/log.md`

原因：

1. 用户可迁移
2. 可审计
3. 可备份
4. 保持本地优先
5. 保证 `file_id` 在跨设备、重命名、删除后的身份稳定

### 8.2 哪些数据放 SQLite

SQLite 必须存在，但不保存主内容。

建议保存：

1. `devices`
2. `vault_state`
3. `file_index`
4. `note_links`
5. `search_index` 或 FTS5 虚拟表
6. `sync_logs`
7. `note_chunks`
8. `raw_sources`
9. `wiki_pages`
10. `ai_sessions`
11. `wiki_tasks`：记录 AI 编译任务的状态（**主键 `task_id`**；唯一索引 `(target_wiki_path)` 仅作用于 `status IN (pending, running)` 的行——等价于"同一目标 wiki 页同时最多一个 pending/running 任务"），至少包含以下字段：
    - `task_id`：任务唯一标识
    - `target_wiki_path`：目标知识页路径
    - `task_base_page_hash`：任务启动时目标页内容哈希（用于写回冲突检测）
    - `task_base_revision`：任务启动时本机 `last_applied_revision`
    - `task_sources_hash`：本次编译使用的来源集合哈希
    - `status`：`pending | running | done | failed | skipped | superseded`
    - `created_at`、`updated_at`

`wiki_tasks` 状态转移说明：

- 终态：`done | failed | skipped | superseded`，进入终态后不再恢复
- `superseded` 含义：当写回判断链第 5 条（来源集合不一致）触发重新编译时，原任务记录状态置为 `superseded`（表示已被新任务接力，不再恢复）；随后创建新的 `wiki_tasks` 记录（新 `task_id`），不复用原记录的 `task_base_page_hash` / `task_base_revision` / `task_sources_hash` 值。**原任务置为 `superseded` 与替代任务创建必须在同一 SQLite 事务中原子完成**，不得先终态化旧任务、再异步补建新任务，以避免崩溃后本次重编译请求丢失
- **并发约束**：发起新 `wiki_tasks` 前必须先检查同 `target_wiki_path` 是否已有 `pending` / `running` 任务；若有，必须在**同一 SQLite 事务**内把该旧任务置为 `superseded` 并创建替代任务，禁止两个 running 任务并发写回同一页，也禁止出现“旧任务已终态、但新任务尚未落库”的空窗
- **取消语义**：`wiki_tasks` worker 在进入耗时编译前、拿到编译结果后、以及真正写回 `.ai/wiki/` 前，都必须重新读取自己的 `status`；若已不是 `running`（尤其变为 `superseded`），必须立即终止，不得继续写回旧结果
- **唯一索引 DDL**：`(target_wiki_path)` 的唯一约束必须使用 SQLite 部分索引，仅作用于活跃状态行：
  ```sql
  CREATE UNIQUE INDEX idx_wiki_tasks_active_path
    ON wiki_tasks (target_wiki_path)
    WHERE status IN ('pending', 'running');
  ```
  不得使用全量唯一索引，否则同一路径的历史终态记录（`done`/`failed`/`superseded`）会触发唯一约束冲突。

12. `sync_apply_journal`：记录某次远端 manifest 应用的阶段化状态，用于崩溃恢复，至少包含以下字段：
    - `journal_id`：日志唯一标识
    - `target_revision`：正在应用的远端 revision
    - `target_manifest_hash`：目标 manifest 摘要
    - `phase`：`preparing | staging | materializing | filemap_rewrite | finalizing`
    - `ops_hash`：本次落盘操作计划的 SHA256 哈希摘要（**仅用于崩溃恢复时的完整性校验，不含可回放计划内容**；恢复时须从 `target_revision` 对应 manifest 重新推导落盘计划，再与此哈希比对）
    - `created_at`、`updated_at`

13. `commit_intent_journal`：记录某次本地 commit 的幂等意图，用于”服务端已成功、客户端未来得及落本地状态即崩溃”的恢复，至少包含以下字段：
    - `commit_intent_id`：客户端在发起 commit 前生成的唯一标识
    - `intent_manifest_hash`：提交前对客户端 canonical manifest 计算出的稳定摘要（不含服务端回填字段）
    - `base_revision`
    - `created_by_device`
    - `status`：`prepared | submitted | acknowledged`
    - `intent_delete_seq_upper_bound`：本次 commit 在 Q-fm 临界区 A 记录的 `vault_state.local_delete_sequence` 快照值，用于临界区 C 回写 tombstone 时筛选”本次 commit 启动前已删除的文件”。**兼容说明**：该字段自 `v1.0.41` 起为新建 journal 的必填字段；升级前已落盘的历史 `submitted/acknowledged` journal 可能缺失此字段，恢复时必须按下文定义的 legacy time-based fallback 处理
    - `created_at`、`updated_at`（其中 `created_at` 即该次 commit 在 Q-fm 临界区 A 内持久化 intent 的时刻）

`commit_intent_journal` 状态说明：

1. `prepared`：本地已持久化提交意图，但网络请求尚未发出
2. `submitted`：网络请求已发出，**或**已收到服务端成功响应但尚未完成本地状态收敛（当前版本语义）
3. `acknowledged`：**历史兼容状态**，仅用于兼容 `v1.0.17` 之前版本可能残留的 journal；当前版本不再主动写入该状态。**注意**：恢复路径中允许将 `acknowledged` 规范化为 `submitted`（见 §10.4 pre-check C），此写入不属于”主动写入 `acknowledged`”的禁止范围

**历史状态机对比表**：

| 版本 | 状态转移路径 | `submitted` 语义 | `acknowledged` 语义 | 临界区 C 行为 |
|------|-------------|-----------------|-------------------|--------------|
| **v1.0.17 之前** | `prepared` → `submitted` → `acknowledged` → 清除 | 仅表示”请求已发出、尚未收到响应” | 表示”已收到成功响应、尚未清除 journal” | 收到成功响应后先写 `acknowledged`，再在独立步骤清除 journal |
| **v1.0.17 及之后（当前版本）** | `prepared` → `submitted` → 清除 | 表示”请求已发出、**或**已收到成功响应但尚未完成本地收敛” | **不再使用**；启动时若发现残留，按 `submitted` 等价流程处理 | 收到成功响应后在同一 SQLite 事务内直接清除 journal，不经过 `acknowledged` |

**关键差异说明**：

- 旧版本的 `submitted` 与 `acknowledged` 分别对应”请求飞行中”与”响应已到、待清理”两个独立窗口
- 当前版本合并了这两个窗口：`submitted` 覆盖从”请求发出”到”本地状态收敛完成”的整个区间；临界区 C 的原子事务消除了”响应已到、journal 未清除”的独立中间态
- 因此当前版本启动时若发现残留 `acknowledged`，不能假设”上次一定收到了成功响应”（可能是旧版本在 `submitted → acknowledged` 转换时崩溃），必须按 `submitted` 的完整远端确认流程处理

补充约束：

1. `prepared` 只允许出现在”本地已锁定提交计划、但尚未向服务端发出 commit 请求”的阶段；若应用重启时发现残留 `prepared`，必须按 §10.4 的”prepared 启动恢复”处理
2. `submitted` 表示”请求结果未知或尚未确认落本地状态”，**不得**在未做远端确认的情况下直接删除 journal；无论是进程崩溃后重启，还是同一进程内遭遇超时 / 连接中断，都必须按 §10.4 的”submitted 结果确认流程”判定该 intent 是否已在服务端落地
3. `acknowledged` 仅用于兼容 `v1.0.17` 之前版本可能残留的 journal。当前版本的 Q-fm 临界区 C 不再写入该状态，而是在同一 SQLite 事务内直接清除 journal 并重置 `commit_in_progress`
4. 由于 `vault_state.commit_in_progress` 保证同设备同一时刻最多只有一个未完成 commit，且启动恢复完成前禁止发起新 commit，因此若恢复路径检测到残留 `.noteapp/staging/*.snapshot.plain` 与 `.noteapp/staging/*.blob.staging` 文件，可将这些后缀文件整体视为**当前唯一未完成 intent** 的残留临时文件并统一清理；无需在 `commit_intent_journal` 中额外逐个持久化文件列表

`sync_apply_journal` 阶段状态机转移表：

| 阶段 | 进入条件 | 退出条件 | 崩溃恢复动作 |
|------|----------|----------|-------------|
| `preparing` | 开始任何文件落盘前，journal 写入持久化 | 落盘计划（ops_hash）生成完毕 | 重新拉取 `target_revision` 对应 manifest，重新生成落盘计划 |
| `staging` | 开始将互相占位的 clean 文件搬入 `.noteapp/staging/` | 所有 staging 文件写入完毕 | 按以下顺序恢复：(1) 优先从 journal 中持久化的 `target_revision` 对应 manifest `files[file_id].path` 定位 staging 文件应落到的最终路径；(2) 若 manifest 不可用，再用 `<file_id>.staging` 反查 `filemap.json`——**注意**：此处反查 `filemap.json` 仅用于确认该 `file_id` 是否为已知文件，不用于确定最终落盘路径（`filemap.json` 在本阶段尚未重写，反映的是应用前旧状态，rename/move 场景下路径可能不准确）；最终落盘路径仍以 manifest 为准；(3) 两者均不可用的残留 staging 文件，**移入 `.noteapp/staging-orphans/` 而非删除**，等待人工审计 |
| `materializing` | 开始将文件从 staging 或直接从 blob 落盘到规范路径 | 所有文件落盘完毕（含冲突副本生成） | 从 `target_revision` 对应 manifest 重新推导落盘计划（用 `ops_hash` 做完整性校验，`ops_hash` 为哈希摘要，**不含**可回放计划内容）；已落盘文件幂等跳过，未落盘文件重新写入；**文件写入全部完成后，必须继续执行 SQLite + 索引更新（含 `last_applied_revision` 写入），并将该 SQLite 更新与 journal phase 推进到 `filemap_rewrite` 在同一 SQLite 事务中原子完成**，再继续恢复，不得截断于文件写入 |
| `filemap_rewrite` | 开始重写本地 `filemap.json` | `filemap.json` 原子替换完成 | 若 `.noteapp/filemap.json.tmp` 存在，优先从 tmp 恢复；恢复失败则必须以**当前文件系统状态 + `target_revision` 对应 manifest 的 `files[]/tombstones[]` + `local_tombstone_ledger`** 联合重建 `filemap.json`；不得仅按当前文件系统重建。若目标 manifest 与本地 tombstone 账本都不可用，则保持恢复模式并阻止新的 commit，直到重新获取目标 manifest 或进入人工修复。**注意**：`last_applied_revision` 已在 `materializing` 阶段的 SQLite 更新中写入，本阶段恢复无需重复更新，仅需完成 `filemap.json` 原子替换 |
| `finalizing` | `filemap.json` 重写完成，且本地状态缓存开始收尾 | journal 清除 | 重新执行缺失的收尾步骤：确保 `remote_head_revision = target_revision`、`last_manifest_summary = target_manifest_hash`、`acked_revision = target_revision`，并把 `pending_ack_to_server` 补记为包含 `target_revision`（**幂等，用 set/upsert 语义**，与拉取流程第13步保持对称）；仅当这些状态也全部持久化完成后才允许清除 journal |

**规则**：journal 未清除时，App 启动必须先进入同步恢复模式，按上表从当前 phase 继续恢复，不得跳过。

其中 `vault_state` 至少需要持久化（**主键 `vault_id`，每个 vault 一行**）：

1. `last_applied_revision`
2. `remote_head_revision`（新 vault 创建时服务端 `head_revision = 0`，为合法初始值；客户端首次拉取前本地缓存值亦为 0）。该字段表示”本机最近一次**成功观测到**的远端 head revision”：成功调用 `GET /vaults/:vaultId/head` 后应立即刷新为返回值；本地 commit 成功后也必须同步推进为 `new_revision`
3. `acked_revision`：本机已成功应用并确认的最新 revision。本地必须持久化该字段；拉取成功路径与 commit 成功路径都要推进它。若实现选择把它与 `last_applied_revision` 做镜像存储，也必须在代码中保持两者单调一致，不得只存在内存中
3a. `local_delete_sequence`：本地删除操作的单调递增序列号（u64，初始值 0），用于 tombstone 回写判定。每次用户删除文件时原子递增，不受时钟回拨影响
4. `pending_ack_to_server`：**专用于"待向服务端上报"语义**。记录已本地应用但尚未成功调用 `POST /ack` 上报给服务端的 revision 集合（**推荐用集合/队列实现**；若选择单值字段，新 revision 覆盖旧值时，旧 revision 的 ack 上报责任由服务端取 max 语义代为覆盖，但 §17.2 test 58 的"集合一致"断言在单值实现下等价为"字段值 >= 恢复前的值"）。仅在**拉取成功路径**写入：先更新本地 `acked_revision`、再写入此字段、再异步调用 `/ack`；上报失败保留供下次重试。**提交成功路径不写入此字段**——因为服务端已在 commit 同一事务内更新该设备的 `acked_revision`，无需上报。
5. `commit_in_progress`：布尔值，提交流程的 Q-fm 临界区 A 置为 `true`、临界区 C 置为 `false`；用于同设备并发 commit 互斥（见 §10.4 提交流程）。**结构冻结语义**：当该字段为 `true` 时，任何会改写 `filemap.json` 的本地结构操作（新建文件、重命名 / 移动、删除、冲突副本确认清理、导入新文件、**tombstone 回收触发的 `filemap.json` 条目移除**等）都不得穿插进当前 commit 的 A→C 窗口，必须在同一 Q-fm 队列中排队到临界区 C 完成之后；普通文件内容保存仍可继续，但只影响后续 commit，不得回流污染当前 intent
6. `local_tombstone_ledger`：本地 tombstone 账本；至少持久化每个 `status=deleted` 条目的 `file_id`、`deleted_revision`、`deleted_by_device`、`last_known_path`、`deleted_at`、`local_delete_seq`，用于 `filemap.json` 重建、旧设备恢复和防止已删文件误复活；**不得仅从当前可见文件系统反推**
   - **双持久化约束**：除 SQLite 主表外，Rust Core 还必须把该账本同步镜像到 `VaultRoot/.noteapp/tombstone-ledger.jsonl`（或等价的 append-only / checkpoint 友好文件格式）。该镜像仅用于**本地崩溃恢复、SQLite 损坏恢复、`filemap.json` 重建**，不参与跨端同步，也不作为服务端裁决对象
   - **恢复优先级**：若 SQLite 可用，以 SQLite 为运行态主来源；若 SQLite 损坏或丢失，则必须先读取 `tombstone-ledger.jsonl` 重建 `local_tombstone_ledger`，再继续执行 `filemap.json` / 索引恢复；读取 `tombstone-ledger.jsonl` 时，若末尾存在不完整行（崩溃时写入中断），必须跳过该不完整行继续解析已完整写入的行，不得因单行损坏导致整个账本不可用。**空账本语义**：若文件为空、仅含空白、或仅包含一个尾部截断且前面没有任何完整记录，但也不存在其他非空畸形内容，则应视为”合法空 ledger”，表示当前 Vault 尚无 tombstone，而不是损坏。仅当 SQLite 与文件镜像**同时不可用**，或镜像文件含有非空畸形内容且无法解析出任何合法记录时，才允许降级为人工修复模式，并阻止新的 commit
   - **升级补号规则**：若应用从 `v1.0.41` 之前版本升级后，发现存在 `deleted_revision = null` 但 `local_delete_seq` 缺失的历史本地 tombstone，则在任何新的 commit、pull 应用或 submitted/acknowledged 恢复开始前，必须先执行一次**启动期补号**：按 `deleted_at` 升序排列这些缺失条目；若 `deleted_at` 相同则按 `file_id` 字典序打破平局；随后以 `vault_state.local_delete_sequence` 当前值为起点，为每个缺失条目顺序补写新的单调递增 `local_delete_seq`，并把 `vault_state.local_delete_sequence` 推进到补号后的最大值。**持久化要求**：SQLite 中的 `local_tombstone_ledger`、`vault_state.local_delete_sequence` 与 `tombstone-ledger.jsonl` 镜像必须在同一次恢复闭环内共同落盘完成后，才允许离开启动恢复模式。若任一待补号条目缺失合法 `deleted_at`，则不得猜测顺序，必须进入人工修复模式并阻止新的 commit
   - **删除序列号机制**：为避免时钟回拨导致 tombstone 回写判定失败，必须使用单调递增序列号。`vault_state` 增加 `local_delete_sequence`（u64，初始值 0）；每次用户删除文件时，先原子递增该序列号（`local_delete_sequence += 1`），再写入 ledger 记录：`deleted_revision = null`、`local_delete_seq = 当前递增后的序列号`、`deleted_at = now`（`deleted_at` 仅用于 UI 展示与日志，不参与回写判定）。本次 commit 在 manifest tombstones[] 中以 `deleted_revision = null` 提交；commit 成功响应到达后，由 §10.4 提交流程的 Q-fm 临界区 C 把 ledger 中所有 `deleted_revision = null` 且 `local_delete_seq <= commit_intent_journal.intent_delete_seq_upper_bound` 的条目统一回写为 `new_revision`；commit 失败保留 null，下次 commit 重试
   - **历史兼容 fallback**：若启动恢复时命中的历史 `submitted/acknowledged` journal **缺失** `intent_delete_seq_upper_bound`（即该 journal 早于 `v1.0.41` 的删除序列号机制），则 tombstone 回写必须退回旧规则：筛选所有 `deleted_revision = null` 且 `deleted_at <= commit_intent_journal.created_at` 的 ledger 条目回写到命中 revision；此路径**不得**要求旧 ledger 记录存在 `local_delete_seq`。一旦该历史 journal 恢复完成或清理完成，后续新 commit 生成的 journal 必须全部携带 `intent_delete_seq_upper_bound`
   - 客户端应用远端 manifest 时，若发现新的 tombstones[] 条目本地 ledger 中不存在，则按远端 `deleted_revision` 与 `deleted_at` 写入 ledger（`local_delete_seq` 置为 `0`，表示来自他设备；**注意**：本地删除序列号从 1 开始递增，0 专用于标识来自他设备的 tombstone，确保回写判定 `local_delete_seq <= intent_delete_seq_upper_bound` 在所有场景下都有明确语义）
7. `last_manifest_summary`（或等价的本地 manifest 摘要缓存，用于拉取 diff 与恢复）。该字段表示”本机最近一次**成功收敛到本地状态**的 head manifest 摘要”：成功应用远端 manifest 后刷新为 `target_manifest_hash`；本地 commit 成功后刷新为该次新 head 的 canonical manifest 摘要（即客户端本次提交的 manifest 在服务端补入 `revision = new_revision` 后对应的本地缓存摘要，或与之等价的稳定摘要）
   - **初始值规则**分两类：
     1. **本地新建空 vault**：必须初始化为**空 vault 的 final head manifest 摘要常量**，其计算语义与本字段完全一致，即对 `files=[]`、`tombstones=[]`、`revision=0` 构成的**最终 canonical manifest**计算稳定摘要；**不得**借用 §10.4 Q-fm 临界区 B 的 `intent_manifest_hash` 规则（后者会省略 `revision`，语义不同）。**序列化与实现说明**：此常量必须按与服务端一致的 manifest 序列化算法计算，并以内置常量形式固定在客户端版本中；同一客户端版本、同一协议版本下，所有实现都必须产出完全一致的字节序列与哈希值。实现者**不得**以任意稳定非 null 字节串、`SHA256("")`、固定魔数等方式替代该常量，也不应把它与服务端任意真实 head 摘要做相等性比较
     2. **通过 §7.2.3 完整迁移包导入**：不得把导入态误当作“本地新建空 vault 初始态”。由于迁移包不包含旧设备的同步运行态，导入后本字段必须直接进入 `null` / stale sentinel 的“待重建基线”状态，并要求先做一次 pull / reconcile；只有在该重建完成后，才允许写回新的有效摘要值
   - 正常运行期间该字段必须为有效摘要值。只有以下两类来源允许将其持久化为 `null`（或等价的”stale / 需刷新”哨兵值）：(1) §10.4 `submitted` 结果确认流程的 `manifest 404 fallback`，表示”本地摘要缓存已失效，但文件身份与 revision 状态仍可继续恢复”；(2) **完整迁移包导入后的“待重建基线”状态**，因为迁移包不包含旧设备的同步运行态，必须先经一次完整 pull / reconcile 重建摘要缓存。这两种来源都必须复用同一套 commit gate 与“完整 pull 后解除”的规则
   - 当该字段为 `null` / stale sentinel 时，它本身就是**持久化同步门闩**：客户端不得发起新的 commit；下一次同步必须跳过任何基于旧摘要的快捷 diff，直接下载最新 manifest 做一次完整 pull / 完整收敛；只有在该完整 pull 成功并重写出新的有效摘要后，才允许重新开启 commit
8. `has_unresolved_conflicts`（布尔值）：当本地 `filemap.json` 中存在 `status=conflict_copy` 且用户尚未处理的条目时为 `true`；提交流程以此字段判断是否存在未解决冲突，为 `true` 时禁止发起 commit。**清除时机**：(1) 用户在 UI 中确认解决某个冲突副本后，Rust Core 必须按用户选择删除或归档该本地副本文件，并从 `filemap.json` 中移除对应的 `status=conflict_copy` 条目；随后重新扫描 `filemap.json`，若不再存在任何 `status=conflict_copy` 条目，则将 `has_unresolved_conflicts` 置为 `false`；(2) 用户手动合并冲突副本内容到主文件后，冲突副本被删除，Rust Core 重新扫描 `filemap.json`，确认无 `status=conflict_copy` 条目后置为 `false`。**并发安全**：`has_unresolved_conflicts` 的读取与提交发起必须在 Rust Core 的串行写入队列内完成（与 `filemap.json` 写入共用同一队列），禁止在队列外并发读取该字段后直接发起 commit，以消除"扫描后、提交前"的竞态窗口

补充说明：

1. `file_index` 是**本地缓存索引**，可以重建
2. `file_id` 的权威来源不是 SQLite，而是 `VaultRoot/.noteapp/filemap.json`
3. SQLite 损坏或重建后，应从文件系统 + `filemap.json` 恢复索引，而不是重新分配全部 `file_id`
4. 方案 A 下，本地用于同步恢复和 diff 的状态可以放在 SQLite，但这些状态都不得取代 `filemap.json` 对文件身份的裁决权

### 8.3 为什么 SQLite 是必须的

因为如果没有 SQLite：

1. 搜索只能反复扫文件
2. 同步状态难以维护
3. AI chunk 与 raw/wiki 映射很快失控
4. 反向链接与知识索引性能会明显变差

结论：

1. **没有 SQLite 可以做一个简单 Markdown 工具**
2. **但做不了这个方案里的产品**

---

## 九、技术选型

### 9.1 客户端

1. 桌面端：`Tauri + TypeScript`
2. 移动端：`Flutter`
3. 共享核心：`Rust`

### 9.1.1 Rust Core 与 Flutter 的 FFI 接口定义

Rust Core 通过 **C ABI + UniFFI** 的方式暴露给 Flutter：

1. 接口形式：使用 `UniFFI` 生成 Dart binding，避免手写 C ABI 胶水代码
2. 暴露范围：仅暴露必要的同步、索引、加解密、AI schema 校验接口，不暴露内部实现细节
3. 数据传递：使用 `bytes` / `string` / 简单结构体，避免跨 FFI 传递复杂 Rust 类型
4. 错误处理：所有 FFI 接口返回统一错误枚举，Dart 侧统一处理
5. 线程模型：Rust Core 内部使用 `tokio` 异步运行时，FFI 接口暴露同步阻塞版本，Flutter 侧在 `Isolate` 中调用；FFI 阻塞调用必须在独立线程池中执行（使用 `tokio::task::spawn_blocking` 或专用线程），不得在 tokio async 上下文中直接调用，避免阻塞 tokio worker 线程导致运行时饥饿

**POC 阶段必须验证：**

1. UniFFI 在 Android（arm64）和 iOS（arm64）上能否正常编译和链接
2. Flutter 调用 Rust Core 的基础 FFI 调用延迟是否在可接受范围内
3. 若 UniFFI 无法打通，备选方案为 `flutter_rust_bridge`，但需重新评估接口设计成本

**高风险说明：若 POC 阶段 FFI 无法打通，移动端方案需整体重新评估，不建议进入全面开发。**

### 9.2 编辑器与解析

建议：

1. 编辑器：`CodeMirror 6`
2. Markdown 渲染：`markdown-it`（插件生态成熟，扩展成本低于 remark）
3. wiki-link 解析：自定义 tokenizer / AST 扩展

不建议：

1. 把 `TinyMCE` 当主编辑器
2. 把 `Marked` 当编辑器

#### 自动保存与崩溃恢复机制

V1 必须实现：

1. **保存间隔**：用户停止输入后 1 秒自动保存到本地文件系统（debounce）
2. **临时草稿**：每次编辑开始时，先写入 `.noteapp/drafts/<file_id>.draft.tmp`，写入完成后 rename 为 `.noteapp/drafts/<file_id>.draft`（原子替换）；保存成功后删除草稿文件；App 启动时若发现 `.draft.tmp` 残留，说明上次草稿写入未完成，优先尝试从 `.draft.tmp` 恢复，恢复失败则丢弃
3. **崩溃恢复**：App 启动时检测 `.noteapp/drafts/` 目录，若存在草稿文件，提示用户"上次异常退出，是否恢复未保存内容"
4. **草稿清理**：用户确认恢复或放弃后，删除对应草稿文件；草稿文件不参与同步

### 9.3 本地搜索

建议：

1. `SQLite FTS5`
2. 中文分词补层
3. 增量索引更新

### 9.4 服务端

建议：

1. `Node.js + Express` 或 `Fastify`
2. 数据库：初期优先使用托管 SQLite（如 `Turso` / `Cloudflare D1`），用户量超过 5000 后再迁移至 `PostgreSQL`
3. `OSS / COS / S3兼容对象存储`

**关于数据库选型说明：**

对于 3 人小团队、初期用户 < 1000 的场景，PostgreSQL 自建运维成本偏高。推荐分阶段：

| 阶段 | 用户量 | 数据库方案 |
|------|--------|-----------|
| V1 初期 | < 1000 | Turso（托管 SQLite，按用量计费，运维成本极低） |
| V1 稳定期 | 1000-5000 | 评估是否迁移，视查询复杂度决定（§19.2 成本估算以迁移后 PostgreSQL 为基准，实际迁移时机以本表为准） |
| V2+ | > 5000 | PostgreSQL（自建或托管，如 Supabase / Neon） |

服务端只存储用户认证、设备信息、vault head、blob 元数据，数据量小，SQLite 完全够用。

### 9.5 AI 模型接入

V1 推荐：

1. `BYOK`
2. 客户端直连模型 API
3. 预留轻量 AI Gateway

---

## 十、同步设计

### 10.1 目标

同步层的唯一目标是：

1. 保证多端一致
2. 保证不丢数据
3. 保证本地优先

### 10.2 核心对象

必须定义：

1. `device_id`（UUID 字符串）
2. `vault_id`（UUID 字符串）
3. `revision`（u64，单调递增，从 1 开始）
4. `base_revision`（u64，提交时对应本机 `last_applied_revision`；新 vault 首次提交时 `base_revision = 0`，为合法初始值）
5. `file_id`（UUID 字符串，创建时生成，全生命周期不变）
6. `content_hash`（明文内容的 `SHA256` hex 字符串；供本地同步层与 manifest 使用。**V1 中它会随 manifest 一起提交并由服务端保存**，但不作为 blob 检查 / 上传接口的 key，也不作为独立对象路径暴露）
7. `blob_id`（远端对象寻址哈希，hex 字符串）：定义为 `HMAC_SHA256(blob_id_key, content_hash)`，其中 `blob_id_key := HKDF(vault_key, "noteapp/blob-id/v1", 32 bytes)`；**`blob_id` 不等于 `content_hash`**，目的是防止远端通过 `content_hash` 做已知明文匹配；同一 vault 内相同明文必然产生相同 `blob_id`，跨 vault 不可见关联
8. `tombstone`（包含 `file_id`、`deleted_at`、`deleted_revision`、`deleted_by_device`、`last_known_path` 的结构体）
9. `acked_revision`（u64，单调递增，取 `max(current, reported)`；新设备注册时初始值为 0，表示尚未确认任何 revision；服务端更新语义见 §14.2 第6条，客户端本地持久化语义见 §8.2 第3条）

**所有时间字段格式约定**：协议层（manifest、SQLite 状态、journal 等）统一使用 UTC Unix 时间戳、毫秒精度（u64）；用户可见 UI 与日志展示层使用 ISO 8601；本文档后续若未单独说明，时间字段均按此约定。

补充约定：

1. `file_id` 在文件首次创建时生成，后续**重命名 / 移动 / 改后缀**时保持不变
2. `path` 表示文件当前位置，不代表文件身份
3. 重命名 / 移动在协议层表示为：**同一 `file_id` 的 `path` 变化**，不是“删除旧文件 + 新建新文件”
4. 同一个 revision 内允许同时发生“内容变更 + 路径变更”
5. 任何同步、冲突、删除判断优先基于 `file_id`，而不是仅基于 `path`
6. `file_id` 的持久化权威来源为 `VaultRoot/.noteapp/filemap.json`
7. SQLite 中的 `file_index` 只是缓存，不是 `file_id` 分配来源
8. 若 `filemap.json` 丢失，系统进入“身份修复模式”：优先按 `path`、`content_hash`、文件类型做 best-effort 对齐；无法确认身份的文件才重新分配 `file_id`，并记录修复日志
9. `manifest` 不是第二权威，而是 `filemap.json` 在同步层的**导出格式**
10. **V1 只严格承诺“应用内重命名 / 移动”保持 `file_id` 稳定**；对于 App 外部直接在文件系统完成的重命名 / 移动，客户端只做 best-effort 识别
11. App 外部变更的 best-effort 规则为：优先按”同目录或近邻目录 + 相同 `content_hash` + 接近 `local_mtime` + 文件类型一致”识别为重命名 / 移动；其中 `local_mtime` 是文件系统自身记录的本地 mtime，**不**与 `manifest.files[].mtime` 直接比较（manifest 的 mtime 是源设备语义，不可用于本地变更检测）；否则降级为”删除旧文件 + 导入新文件”
12. 因此产品文案不得把“外部任意文件操作都无损保留身份”当作 V1 承诺；V1 承诺的是“完整 Vault 迁移无损、应用内改名无损、外部改动尽力识别”

### 10.3 Manifest 结构

V1 明确采用：**`filemap.json` 唯一权威 + 全量快照 manifest 导出 + 增量 blob 上传**。

也就是：

1. 上传阶段只上传新增或变更的 blob
2. commit 阶段提交的是“由当前 `filemap.json` 导出的、该 revision 下整个 Vault 的最新元数据快照”
3. 落后多版本的设备，拉取**最新一个 manifest** 即可恢复到当前状态，不依赖把中间每个 revision 的增量顺序回放

每次同步提交一个 manifest，包含：

1. `vault_id`
2. `revision`
3. `base_revision`
4. `created_by_device`
5. `created_at`
6. `files[]`
7. `tombstones[]`

每个 `files[]` 项：

1. `file_id`
2. `path`：**经过 Unicode NFC 归一化后**写入 manifest 的 Vault 相对路径
3. `content_hash`：用于目标设备解密时重建 deterministic nonce；属于 manifest 元数据，服务端可见
4. `blob_id`
5. `size`：明文大小（字节数）
6. `mtime`（UTC Unix 时间戳，毫秒精度，**语义为「源设备本地最后写入该文件时的时间」**）：跨设备拉取应用时**不得**将其作为目标设备本地文件的 mtime 直接写入，必须独立缓存（见 §10.4 拉取流程的 mtime 处理规则）

约束：

1. 同一个 manifest 的 `files[]` 中，所有 `path` 在 NFC 归一化后必须唯一；**服务端收到 commit 时必须校验 path 已是 NFC 编码**，非 NFC 编码的 manifest 一律拒绝
2. 任意时刻只允许一个 `status=active` 的 `file_id` 占用某个规范路径（**最终收敛后的不变量**；同步收敛过程中的中间态豁免该校验，豁免范围为”拉取流程第5步开始至第12步 `filemap.json` 重写完成”；重写完成后立即恢复校验）
3. 若同步收敛过程中出现”不同 `file_id` 竞争同一路径”，必须进入 **path collision** 处理，禁止直接覆盖其中任意一方的未决工作副本
4. 同一个 manifest 的 `tombstones[]` 中，所有 `file_id` 也必须唯一

每个 `tombstones[]` 项：

1. `file_id`
2. `deleted_at`
3. `deleted_revision`
4. `deleted_by_device`
5. `last_known_path`

补充说明：

1. `files[]` 只表示**当前仍然存活的文件快照**
2. 删除文件不会继续留在 `files[]` 中，而是进入 `tombstones[]`
3. 同一个 manifest 的 `tombstones[]` 中，`file_id` 必须唯一；同一 `file_id` 不得重复出现多个 tombstone 项
4. `deleted_revision` 不是客户端本地拍脑袋填写的字段；**客户端提交新 tombstone 时，`deleted_revision` 必须填 `null`**，服务端以此区分"本次新产生的 tombstone"和"继承自上一版 head 的 tombstone"；**不得**使用 `0`、空字符串或其他“等价零值”替代 `null`，以避免客户端 `intent_manifest_hash` 与服务端校验规则对同一 tombstone 产生不同序列化结果。对本次 commit 新产生的 tombstone，服务端必须在 CAS 成功并生成新 head 后，统一回填 `deleted_revision = new_revision`
5. 若某个 `file_id` 已在上一版 head 的 `tombstones[]` 中存在，则其 `deleted_revision` 一旦生成后即视为不可变元数据；后续 commit 只能原样继承，不得改写
6. **孤立 tombstone 处理规则**：若 `tombstones[]` 中出现的 `file_id` 既不在本次 `files[]` 中，也从未出现于服务端任何历史 manifest 的 `files[]`（即该文件从未被 commit 过），服务端**接受该 tombstone 作为合法无害条目**，正常回填 `deleted_revision = new_revision` 并纳入新 head；客户端侧**不得**将此类「本地创建后未曾 commit 即删除」的文件的 tombstone 视为阻塞提交的错误；此类孤立 tombstone 对其他设备无实质影响（它们从未见过该文件），仅作为 manifest 中的无害历史记录存在
7. 服务端至少保留”最新 manifest + 必要 tombstones”，以支持旧设备拉齐状态并阻止已删文件误复活；此外，**所有历史 revision 的 manifest 必须在生成后至少保留 30 天**（与 tombstone 最短保留期对齐），以确保 §10.4 submitted 崩溃恢复 step 4 在该窗口内能成功拉取 `matched_revision` 对应的 manifest 计算 `last_manifest_summary`；超过 30 天的历史 manifest 方可按存储策略清理
8. 客户端提交前，必须先把本地 `filemap.json` 与待提交 manifest 对齐；manifest 中出现的 `file_id` 必须都能在本地 `filemap.json` 中找到对应记录
9. 若本地 `filemap.json` 与当前待导出的 manifest 推导结果不一致，以 `filemap.json` 为准重导 manifest，而不是反向修改 `filemap.json`
10. 若客户端成功拉取并应用远端 manifest，则该 manifest 成为**新的同步目标状态**；客户端必须据此重写本地 `filemap.json`
11. 因此裁决规则分两个方向：
    - **提交方向**（本地 → 远端）：导出 manifest 时以本地 `filemap.json` 为准，若两者不一致，重导 manifest，不得反向修改 `filemap.json`
    - **拉取方向**（远端 → 本地）：成功应用远端 manifest 后，以该 manifest 对应的最终文件系统落盘状态重写本地 `filemap.json`；此时 manifest 是收敛目标，`filemap.json` 随之更新
12. `filemap.json` 中 `status=deleted` 的条目在 tombstone 完成回收前，必须仍能导出对应的 `tombstones[]` 信息；不得因为本地文件已不存在就提前丢弃该条目
13. `status=conflict_copy` 的条目不得进入规范 manifest 的 `files[]` 或 `tombstones[]`；只要它们仍存在，本地提交就必须继续被拦截，直到用户完成处理

#### filemap.json 与 manifest 的关系

`filemap.json` 与 manifest 的关系如下：

1. `filemap.json` 是**本地唯一权威**
2. `manifest` 是**同步导出格式**，由 `filemap.json + 当前文件状态` 派生生成
3. 远端只保存 manifest 与 blob，不把 `filemap.json` 当作普通业务文件参与 `files[]`
4. 因此 `filemap.json` 不需要自己的业务 `file_id`，不存在“`filemap.json` 自己的身份从哪里来”的自引用问题
5. 拉取远端 manifest 并成功应用后，客户端按最终落盘结果**重建/重写本地 `filemap.json`**
6. `filemap.json` 若与已成功应用的 manifest 不一致，以“已成功应用的 manifest 对应的最终文件系统状态”为准，重写 `filemap.json`
7. `filemap.json` 的冲突不以“文件冲突副本”形式暴露；它的冲突已经体现在普通文件的重命名/删除/恢复冲突中，由 Rust Core 在完成冲突收敛后统一产出新的 `filemap.json`
8. 与 `filemap.json` 重建、身份修复、结构化收敛有关的事件写入**同步日志**体系，不写入 `.ai/log.md`
9. 生成冲突副本时，原文件条目保持原 `file_id`；冲突副本以 `status=conflict_copy` 写入 `filemap.json`，分配新的 `file_id`

### 10.4 同步流程

V1 的同步引擎必须显式区分三类本地状态：

1. **`last_applied` 基线**：最近一次成功应用到本机的远端 revision 视图
2. **`working tree` 工作副本**：用户当前正在编辑和看到的本地文件系统状态
3. **`pending_local_changes` 本地待提交变更**：相对 `last_applied` 的未提交变更集合；每条至少记录 `base_revision`、`base_content_hash`，文本文件还需保留可用于 three-way merge 的基线快照或等价 patch

核心规则：

1. **只有 clean 文件允许被远端直接覆盖**
2. **dirty 文件在拉取远端时，必须先 merge、rebase 或转冲突副本，禁止远端 blob 直接覆盖工作副本**
3. **冲突处理既可能发生在 CAS 失败后，也可能发生在“本地 dirty + 远端已有新 head”的拉取阶段**
4. **若不同 `file_id` 竞争同一路径，目标 manifest 的规范路径优先，但落败方的未提交内容必须转冲突副本或先搬离，禁止被静默覆盖**

#### 拉取流程

1. 客户端读取本地 `remote_head_revision`、`last_applied_revision`、`pending_local_changes` 与 `last_manifest_summary`
2. 查询远端 head；若请求成功，立即把返回的 `head_revision` 刷新到本地 `remote_head_revision`
3. 若远端更高，或本地 `last_manifest_summary` 为 `null` / stale sentinel，下载**最新 manifest 快照**（**注意**：`last_manifest_summary` 为 null/stale 时，无论 revision 是否与本地 `last_applied_revision` 一致，都必须重新下载 manifest 并完整收敛，不得以 revision 相等为由跳过）
4. 在开始任何文件落盘前，先写入 `sync_apply_journal`，持久化 `target_revision`、目标 manifest 摘要、计划操作摘要和当前阶段 `preparing`；**只要 journal 未清除，启动恢复路径优先于正常文件扫描**
5. 对比本地 `last_applied` 摘要、`working tree` 的 dirty 集合，以及远端 `files[]` / `tombstones[]`。若本地 `last_manifest_summary` 为有效值，可按正常摘要快捷路径判断差异；若其为 `null` / stale sentinel，则**不得**使用任何基于旧摘要的快捷路径，必须把本次拉取视为“摘要缓存失效后的完整收敛”，直接基于最新 manifest 做完整 diff 与完整落盘计划推导
6. 对每个 `file_id` 先分类为 `clean` / `dirty`
7. `clean` 文件：
    - 若远端有新增或更新，必须先通过 `POST /vaults/:vaultId/blobs/download-init` 获取该 `blob_id` 对应的受控下载凭证，再获取密文 blob 并在本地解密写入规范路径；**不得**依赖未定义的对象存储公开路径或把 `blob_id` 直接当作静态 URL
    - 若远端有删除，直接消费 tombstone
8. `dirty` 文件：
    - 若远端无变化，保留本地工作副本，不做覆盖
    - 若远端也变化，进入 rebase / merge / 冲突副本分流流程，**先保住本地修改，再决定规范路径如何落盘**
    - 若远端对该 `file_id` 发出 tombstone（已删除），且本地为 dirty，则本地修改版本转为冲突副本，规范路径按 tombstone 删除处理；不允许 dirty 本地版本阻止 tombstone 消费
9. 若某个远端 `file_id=A` 的目标路径为 `P`，而本地另一个 `file_id=B` 已占用 `P`，则进入 **path collision**：
    - 若 `B` 为 `clean` 且按目标状态本应删除或搬离，先清理 / 搬离 `B`，再落盘 `A`
    - 若 `B` 为 `dirty` 或仍需保留，则 `A` 保持目标 manifest 的规范路径 `P`，`B` 转为冲突副本或搬到冲突路径，禁止直接覆盖 `B`
10. **clean 路径交换 / 重命名环处理**：若目标 manifest 中存在 `A -> B`、`B -> A` 或更长的路径占位环，不得按最终路径直接顺序覆盖；必须采用“两阶段落盘”：
    - 第一阶段：把所有会互相占位的 clean 文件搬到临时 staging 路径
    - 第二阶段：再从 staging 路径统一落到最终规范路径
    - staging 路径仅为同步内部临时态，不写入最终 `filemap.json`；staging 文件统一存放在 `.noteapp/staging/` 目录下，以 `<file_id>.staging` 命名；App 启动时若检测到 `.noteapp/staging/` 目录非空，说明上次两阶段落盘可能未完成，**不得直接清空**：必须先检查是否存在 `sync_apply_journal`。若存在，则仅允许把这些 staging 文件作为该 journal 恢复流程中 `staging` 阶段的子步骤处理；若不存在，则**不得**再按 `<file_id>.staging` 独立回放到规范路径，而应将无法归属到活跃 journal 的残留文件移入 `.noteapp/staging-orphans/` 并触发一次完整拉取或人工修复，避免形成第二套独立恢复协议
11. 更新 SQLite、索引、本地 `last_applied_revision`（先于 `filemap.json` 重写；若此步失败，journal 仍未清除，崩溃恢复可重试）；同时把目标 manifest 中每个 `files[].mtime` 缓存到 SQLite `file_index.manifest_mtime` 字段，**不写入本地文件系统的实际 mtime**；本地文件实际 mtime 由 OS 在落盘时自然生成，与 manifest mtime 解耦
12. 所有文件收敛完成且 SQLite 更新成功后，根据”最终落盘的规范文件 + 冲突副本 + tombstones”重写本地 `filemap.json`（原子替换，先写 `.tmp` 再 rename），其中 `path` 字段使用与 manifest 一致的 NFC 编码；**`filemap.json` 重写必须在 SQLite 更新成功后执行**，以确保两者不一致时 journal 仍可指导恢复；同时清理本地 `local_tombstone_ledger` 与 `filemap.json` 中”远端已不再持有 tombstone（即服务端已回收）”的 `status=deleted` 条目：判定标准为”本地条目的 `file_id` 既不在远端 `files[]` 也不在远端 `tombstones[]`，且 `tombstone.deleted_revision != null && target_revision >= tombstone.deleted_revision`”（**注意**：`deleted_revision = null` 的条目表示尚未被服务端回填，必然不满足回收条件，跳过即可，不参与本步骤判定）
13. 在上述文件系统与 `filemap.json` 收敛后，刷新本地状态缓存：将 `remote_head_revision = target_revision`、`last_manifest_summary = sync_apply_journal.target_manifest_hash`、`acked_revision = target_revision`，并把 `pending_ack_to_server` 记为包含 `target_revision`（**写入必须幂等**：若因崩溃重启后重新执行本步骤，重复写入同一 revision 不应产生副作用；服务端 `/ack` 接口已定义为幂等，客户端侧实现可用 set 或 upsert 语义）
14. 仅当第 11-13 步全部成功后，才将 `sync_apply_journal.phase` 置为 `finalizing` 并清除该 journal；在此之前崩溃，均视为”远端应用未完成”
15. journal 清除后，再异步通过 `POST /vaults/:vaultId/devices/:deviceId/ack` 向服务端上报 `pending_ack_to_server` 中的 revision；上报失败不影响本地状态，保留 `pending_ack_to_server` 供下次同步时重试

#### 拉取应用的崩溃恢复

1. App 启动时若发现 `sync_apply_journal` 未清除，必须先进入**同步恢复模式**，暂停普通文件监听、索引增量更新和“扫描本地脏变更”流程
2. **只要 `sync_apply_journal` 未清除，就视为上次远端 manifest 应用尚未完整收敛**；不得仅通过比较 `target_revision` 与 `last_applied_revision` 判断是否“已完成”，因为崩溃可能发生在 `last_applied_revision` 已更新但 `filemap.json` 尚未重写的窗口内
3. 恢复流程优先顺序为：
   - 若 journal 中存在可验证的 staging/落盘计划，则按 journal 把文件系统收敛到 `target_revision` 的目标状态
   - 若 journal 已损坏或本地中间态不可验证，则保留可识别的冲突副本与 staging 残留，随后强制重新拉取 `target_revision` 对应 manifest 做一次完整收敛
4. 只有在文件系统、`filemap.json`、SQLite 索引、`last_applied_revision`、`remote_head_revision`、`last_manifest_summary`、`acked_revision` 与 `pending_ack_to_server` 已共同收敛后，才允许清除 journal 并恢复正常同步
5. **staging 恢复是 journal 恢复的子步骤，不独立触发**：App 启动时若同时存在 journal 残留和 staging 残留，必须先按 journal 恢复流程处理（journal 优先），staging 恢复作为 journal 恢复中 `staging` 阶段的具体执行步骤；不得在 journal 恢复流程之外单独触发 staging 恢复，以避免两套协议并发执行导致文件被错误覆盖
6. 因此 `sync_apply_journal` 与 `.noteapp/staging/` 一起构成远端应用的崩溃恢复协议；二者缺一不可

#### 提交流程

**串行队列与内容快照模型**：

V1 必须明确区分三类执行域，避免把网络长耗时操作放在 `filemap.json` 的串行写入队列内：

1. **串行写入队列（Q-fm）**：与 §7.2.1 描述的 `filemap.json` 串行写入队列**同一队列**，覆盖所有 `filemap.json` 的读 + 改 + 写，以及 `vault_state.has_unresolved_conflicts` 的读取与 commit 的”开始 / 锁定 / 完成”标记。此队列内**禁止**进行网络 I/O 或大文件加密。**关键约束**：一旦某次 commit 在临界区 A 将 `commit_in_progress` 置为 `true`，后续任何会改写 `filemap.json` 的本地结构操作都只能继续在 Q-fm 中排队，直到该 commit 的临界区 C 清锁后才允许落地；不得在 A 与 C 之间插入第二次 `filemap.json` 改写，否则会破坏 manifest 结构视图与内容快照的一致性。
2. **commit 工作线程（W-commit）**：进入 Q-fm 短临界区拿到”提交快照计划”后，离开队列在独立线程做”基于冻结快照源生成 `<file_id>.snapshot.plain` → 计算 hash → 派生 nonce → 加密 → 落临时密文 blob”、“询问远端 blob → 上传缺失 blob”、“网络 commit 请求”等操作。**禁止**在离开 Q-fm 后重新打开 `Notes/`、`Attachments/`、`.ai/wiki/` 等 live 源文件去补读本次 commit 的内容。
3. **编辑器保存线程**：写 `Notes/*.md` 笔记内容**不进入** Q-fm（笔记文件内容不修改 `filemap.json`），仅在新建 / 重命名 / 删除等结构操作时进入 Q-fm 修改 `filemap.json`。当 `commit_in_progress = true` 时，这些结构操作必须等待当前 commit 完成；实现可选择”排队后自动继续”或”立即返回 UI 可重试提示”，但**不得**让它们在当前 commit **完成前**（即 `commit_in_progress` 被临界区 C 清除之前）插入落盘——这包括临界区 B 之前和 B→C 的窗口期。普通内容保存可以继续发生，但若某个文件已被纳入当前 commit 的 `content_snapshot_plan`，后续保存只能更新 working tree / 下次 commit 的输入，**不得回写污染当前已冻结的快照源**。

**pull 与 commit 互斥调度约束**：pull 应用流程（`sync_apply_journal` 存续期间）与 commit 流程（`commit_in_progress = true` 期间）在 sync engine 调度层面**互斥执行，不允许并发触发**。具体规则：已有 commit 在飞（`commit_in_progress = true`）时，sync engine 不得触发新的 pull 应用；已有 pull 在应用（`sync_apply_journal` 未清除）时，sync engine 不得触发新的 commit。此约束补全了”结构冻结”的边界：pull 步骤 12（`filemap.json` 原子重写）虽然也经过 Q-fm，但若不在调度层禁止并发，pull 步骤 11 对 `last_applied_revision` 的推进仍可能与 commit 的 Q-fm 临界区 C 产生状态覆盖竞争。§10.4 启动预检 pre-check E 已覆盖进程重启时的恢复场景；本条补全运行时调度层的同等保障。

**内容快照不变量**：一次 commit 启动后，本次 commit 的所有 `content_hash` / `blob_id` / `manifest.files[]` 字段，必须来自同一份”内容快照”——具体为**同一份 `content_snapshot_plan` 所冻结的源版本集合**及其产出的 `<file_id>.snapshot.plain` / `<blob_id>.blob.staging` 文件；后续步骤一律基于该快照，**不得**重新读取 `Notes/`、`Attachments/` 或 `.ai/wiki/` 等 live 源文件；用户在快照之后对源文件的修改归到下一次 commit。若冻结集合中的任一文件在其明文快照真正落成前已偏离 A 阶段记录的源版本标识，则本次 commit **必须整体中止并重扫**，不得静默混入新旧两个时刻的内容。

**完整步骤如下：**

**启动预检 / 恢复（先于任何新 commit / pull）**：应用启动后，必须先执行一次本地提交状态预检；只有该预检完成，才允许进入新的提交临界区或拉取应用流程。

- **0.** 若发现 `local_tombstone_ledger` 中存在 `deleted_revision = null` 且 `local_delete_seq` 缺失的历史本地 tombstone，必须先按 §8.2 的“升级补号规则”完成补号并持久化；在补号完成前，**禁止**进入下面的 journal 恢复、commit 与 pull 应用流程
- **A.** 若发现残留 `status = prepared` 的 `commit_intent_journal`，执行下文的 **prepared 启动恢复**
- **B.** 若发现残留 `status = submitted` 的 `commit_intent_journal`，执行下文的 **submitted 结果确认流程**
- **C.** 若发现残留 `status = acknowledged` 的 `commit_intent_journal`，将其视为**历史兼容的结果未知状态**：不得使用 `intent_manifest_hash` 与 `last_manifest_summary` 做本地短路比对，必须直接复用与 `submitted` 等价的远端确认流程（可先在本地规范化为 `submitted`，也可在实现上直接复用同一确认分支）
- **D.** 若 `commit_in_progress = true` 且不存在任何 `prepared` / `submitted` / `acknowledged` journal，则仅将其视为**兼容旧版本或本地状态损坏**的兜底异常态，而非当前版本原子事务模型中的正常崩溃窗口；此时允许清理残留 staging 文件并把 `commit_in_progress` 重置为 `false`
- **E.** 在上述预检 / 恢复未完成前，**禁止**发起新的 commit 与新的拉取应用，避免本地状态与服务端确认流程交错

1. **【Q-fm 临界区 A，开始 commit】**：
   - 前置条件：启动预检 / 恢复已完成，且不存在任何待处理的 `commit_intent_journal`
   - 检查 `vault_state.commit_in_progress` 是否为 false；为 true 则拒绝（同设备并发 commit 互斥）
   - 检查 `last_manifest_summary` 是否为有效摘要；若为 `null` / stale sentinel，则**拒绝发起新 commit**，并要求先完成一次成功的完整 pull 以刷新摘要缓存（这也是 submitted 404 fallback 与”迁移包导入后待重建基线”共用的持久化 commit gate）。**唯一豁免条件**：仅当 vault 为**本地新建空 vault**、`last_applied_revision = 0`、不存在任何活跃的 `commit_intent_journal`、且 `last_manifest_summary` 已被初始化为空 vault 的 final head 摘要常量时，才允许首次 commit 继续执行；完整迁移包导入态不属于该豁免范围
   - **路径唯一性校验**：检查 `filemap.json` 中所有 `status = active` 条目的 `path` 经过 NFC 归一化后是否唯一；若存在两个或多个 `active` 条目归一化后指向同一路径，禁止提交；同时检查 `status = conflict_copy` 条目的路径不得与任何 `active` 条目路径重合（按 NFC 比较）
   - 检查 `vault_state.has_unresolved_conflicts == true`（即 `filemap.json` 中存在未处理的 `status = conflict_copy` 条目）则禁止直接提交，要求用户处理冲突副本或接受 merge 结果
   - 扫描本地相对 `last_applied` 基线的变更集合，生成 `pending_local_changes`（含每条变更的 `file_id` 与源路径）
   - 基于 `pending_local_changes` 生成**不可变**的 `content_snapshot_plan`：对每个待提交文件记录本次 commit 唯一使用的源版本标识（如编辑器 buffer revision、文件版本 token、`mtime+size` 的稳定组合或等价机制）。该计划一旦生成，本次 commit 后续只允许消费该计划对应的冻结源版本；若实现无法为某类文件提供可验证的冻结源句柄，则必须在离开临界区 A 前先把该文件的明文复制到 `.noteapp/staging/<file_id>.snapshot.plain`
   - 在 SQLite 中持久化一条新的 `commit_intent_journal`：写入 `commit_intent_id`、`base_revision = last_applied_revision`、`created_by_device`、`intent_delete_seq_upper_bound = vault_state.local_delete_sequence`（当前删除序列号快照），`intent_manifest_hash` 暂置为 null，`status = prepared`
   - 把 `vault_state.commit_in_progress` 置为 `true`，连同 `pending_local_changes` 一并锁定为”提交快照计划”
   - 自此进入**结构冻结窗口**：直到临界区 C 清除 `commit_in_progress` 前，所有会改写 `filemap.json` 的本地结构操作都必须在 Q-fm 队列中等待，不得穿插改写当前 commit 所依据的结构视图；普通内容编辑可继续发生，但只会进入后续 commit
2. **【W-commit 快照阶段，离开 Q-fm】**：对 `pending_local_changes` 中每个 `file_id`，依次执行：
   - 先校验该文件当前 live 源版本是否仍与 `content_snapshot_plan` 中记录的源版本标识一致；若任一文件不一致，说明用户已在快照落成前修改了该文件，本次 commit **必须整体中止**：清理已生成的 `*.snapshot.plain` / `*.blob.staging`、清理 `commit_intent_journal`、释放 `commit_in_progress`，随后重新扫描生成下一轮 commit，**不得**继续生成混合时刻的 manifest
   - 对仍一致的文件，从其**冻结源版本**读取一次明文，写入”快照文件”：`.noteapp/staging/<file_id>.snapshot.plain`（仅本进程内可见，亦可保留在内存中；单文件超过阈值时落盘以避免内存爆炸；**阈值定义**：桌面端编译时常量 `SNAPSHOT_MEMORY_THRESHOLD_BYTES = 10MB`，移动端 `4MB`；**调整仅限降低阈值，不得高于 10MB**；实现必须在编译时或运行时配置中明确区分平台，禁止硬编码单一值）
   - 基于快照内容计算 `content_hash`
   - 按 §11.2 派生 `nonce`，用 `content_key` 加密产出 `.noteapp/staging/<blob_id>.blob.staging` 临时密文 blob 文件，并派生 `blob_id`
   - 记录 `(file_id, content_hash, blob_id, plaintext_size)` 到内存中的”快照表”
3. **询问远端 blob 是否已存在**（按快照表中的 `blob_id` 集合，调用 `POST /vaults/:vaultId/blobs/check`）
4. **上传缺失 blob**：以临时密文 blob 文件作为输入；上传完成后保留临时文件直到 commit 完整确认并完成本地收敛，避免重传
5. **【Q-fm 临界区 B，导出 manifest 并标记 submitted】**：
   - 基于“**临界区 A 锁定的 `filemap.json` 结构视图** + 快照表中的 `(file_id, content_hash, blob_id)`”导出新的全量 canonical manifest（path 字段在写入前 NFC 归一化）；这里的“结构视图”指 A→C 窗口内未被其他本地结构操作改写的同一份 `filemap.json` 逻辑状态。新 tombstone 的 `deleted_revision` 填 null
   - 计算 `intent_manifest_hash`：对 canonical manifest 做稳定序列化（按字段名字典序、UTF-8 NFC、无空白字符）后取 SHA256；**排除规则**：(1) 整个 `manifest.revision` 字段从序列化中移除（omit，不写入序列化结果）；(2) `tombstones[]` 中每个条目的 `deleted_revision` 字段若值为 null，则在序列化时将该字段 **omit**（视为缺失，不写入 null 字面量）；tombstone 条目本身保留，仅该字段被省略；服务端在 CAS 通过、回填 `deleted_revision` 之前，必须用相同规则（omit revision 字段、omit null deleted_revision 字段）重算 `intent_manifest_hash` 并与客户端上报值比对
   - 把 `intent_manifest_hash` 持久化回 `commit_intent_journal`，并将 `status` 置为 `submitted`
6. **【W-commit 网络阶段，离开 Q-fm】**：携带 `base_revision`、`commit_intent_id`、`intent_manifest_hash`、canonical manifest（其中 `revision` 字段为 null）调用 `POST /vaults/:vaultId/commit`
7. 服务端按 §10.5 / §14.2 做 CAS 与 manifest 校验；通过则写入新 revision，把 `revision` 字段填入持久化的 manifest，**在同一服务端数据库事务内**将该设备的 `acked_revision` 更新为新 revision，并把 `commit_intent_id`、`intent_manifest_hash` 持久化到该 revision 的元数据中
8. **【Q-fm 临界区 C，落地本地状态】**：客户端收到成功响应后，**进入事务前必须先将 `commit_intent_journal.intent_delete_seq_upper_bound` 读入局部变量**（事务内第2项使用该缓存值，而非再次查询 journal 表，以防顺序颠倒时字段不可用）；**以下 SQLite 状态更新必须在同一数据库事务中原子完成**，避免当前版本在正常崩溃路径下暴露”本地状态已部分收敛、但 journal / 锁状态未同步”的中间态：
   - 更新本地 `last_applied_revision = new_revision`、本地 `remote_head_revision = new_revision`、本地 `acked_revision = new_revision`；**不修改 `pending_ack_to_server`**（commit 成功路径不写入也不清空该字段；`/ack` 仅用于”拉取并应用远端 revision”路径，服务端已在 commit 同一事务内更新该设备的 `acked_revision`，无需再上报）
   - 把 `local_tombstone_ledger` 中所有 `deleted_revision = null` 且 `local_delete_seq <= intent_delete_seq_upper_bound` 的条目统一回写为 `new_revision`（即本次 commit 启动前已纳入提交计划的新增 tombstone）
   - 刷新本地 `last_manifest_summary` 为该次新 head 的 canonical manifest 摘要（即客户端本次提交的 manifest 在服务端补入 `revision = new_revision` 后对应的稳定摘要，或与之等价的本地缓存摘要）
   - 清除该 `commit_intent_journal` 记录（**不经过 `acknowledged` 中间状态，直接删除**；这也是为什么启动时残留 `acknowledged` 只应视为历史兼容状态）
   - 把 `vault_state.commit_in_progress` 置为 `false`
   - **以上五项 SQLite 操作在同一事务中提交**，执行顺序为：第1项（revision推进）→ **第2项（tombstone ledger回写，必须在第4项清除journal之前执行）** → 第3项（摘要刷新）→ **第4项（清除journal）** → 第5项（释放锁）；**（事务外，第6步）** 事务提交后再执行：删除 `.noteapp/staging/<file_id>.snapshot.plain` 与 `.noteapp/staging/<blob_id>.blob.staging` 临时文件（文件系统操作不纳入 SQLite 事务；若此步骤前崩溃，残留 staging 文件在下次启动时由”启动预检 / 恢复”统一清理，不影响正确性）

**prepared 启动恢复**：若应用启动时发现残留 `status = prepared` 的 `commit_intent_journal`，说明上次提交在发出 commit 请求前就已中断。客户端必须先执行以下清理，再允许新的 commit / pull：

1. 清理 `.noteapp/staging/` 下所有残留 `*.snapshot.plain` 与 `*.blob.staging` 临时文件（这些文件在恢复窗口内都归属当前唯一未完成 intent；见 §8.2 的补充约束）
2. 清除 `commit_intent_journal`
3. 把 `vault_state.commit_in_progress` 置为 `false`
4. 重新解锁 `pending_local_changes`，允许后续重新扫描并生成新的提交计划

**历史兼容 `acknowledged` 恢复**：若应用启动时发现残留 `status = acknowledged` 的 `commit_intent_journal`，说明本地状态要么来自旧版本遗留，要么已遭遇外部修改 / 损坏。当前版本**不得**以 `intent_manifest_hash` 与 `last_manifest_summary` 做本地相等性短路判定；必须按以下规则处理：

1. 将该 journal 视为与 `submitted` 等价的“结果未知”状态，保留原有 `commit_intent_id`、`base_revision`、`created_at`、`intent_manifest_hash`
2. 立即复用下文的 **submitted 结果确认流程** 做远端确认；若实现上需要，可先把本地 `status` 规范化回 `submitted`
3. 命中后的本地收敛、未命中后的清理、`commit_in_progress` 释放、staging 文件删除规则，均与 `submitted` 完全一致，不允许另写一套本地短路分支

**submitted 结果确认流程**：若本地存在 `status = submitted` 的 `commit_intent_journal`，则表示“commit 请求已发出，但客户端尚未确认该 intent 是否已在服务端落地”。这既可能发生在**收到成功响应后崩溃**，也可能发生在**同一进程内遭遇超时 / 连接中断 / 响应丢失**。客户端必须按以下顺序处理（由启动恢复路径驱动，且在运行时碰到结果未知错误时也复用同一流程；先于任何新 commit 与新拉取）：

1. 调用 `GET /vaults/:vaultId/head` 获取最新 head revision 及其元数据（包含 `head_revision`、`commit_intent_id`、`created_by_device`），并将该值记为 `observed_head_revision`；若 head 元数据中的 `commit_intent_id` 与本地残留 journal 一致，则记 `matched_revision = observed_head_revision`，进入第 4 步
2. 若服务端 head 的 `created_by_device` 为本机但 `commit_intent_id` 不匹配（说明本机后续又成功提交过其他 intent），客户端必须通过 `GET /vaults/:vaultId/revisions?from=base_revision+1&to=observed_head_revision` 分页拉取区间内所有 revision 元数据，查找与本地 `commit_intent_id` 匹配的项（响应中每项包含 `revision`、`commit_intent_id`、`created_by_device`，**不需要下载完整 manifest**）；命中则记 `matched_revision = 命中的 revision`，进入第 4 步
3. 若服务端 head 的 `created_by_device` **不是本机**（说明本机崩溃后另一台设备又提交了新 revision），同样通过 `GET /vaults/:vaultId/revisions?from=base_revision+1&to=observed_head_revision` 扫描全区间，查找与本地 `commit_intent_id` 匹配的项；命中则记 `matched_revision = 命中的 revision`，进入第 4 步；未命中则进入第 5 步
4. 命中后：必须先确定本次 tombstone 回写筛选器。若 `commit_intent_journal.intent_delete_seq_upper_bound` 存在，则使用当前版本规则：筛选 `deleted_revision = null` 且 `local_delete_seq <= intent_delete_seq_upper_bound` 的条目；若该字段缺失，则说明命中了 `v1.0.41` 之前遗留的历史 journal，必须退回 legacy time-based fallback：筛选 `deleted_revision = null` 且 `deleted_at <= commit_intent_journal.created_at` 的条目。随后必须额外调用一次 `GET /vaults/:vaultId/manifest/:matched_revision` 获取该 revision 的完整 manifest，并基于其 canonical 内容计算最终稳定摘要 `matched_manifest_summary`（**不得**直接复用 `intent_manifest_hash`，因为后者不含服务端回填字段）；随后**以下状态更新必须在同一 SQLite 事务中原子完成**：把本地 `last_applied_revision` 推进到 `matched_revision`、本地 `acked_revision` 同步推进到 `matched_revision`、本地 `remote_head_revision` 刷新为 `observed_head_revision`、**不修改 `pending_ack_to_server`**（submitted 命中路径等同于”确认上次提交已成功”，与临界区 C 语义一致，服务端已在 commit 同一事务内更新该设备的 `acked_revision`，无需上报）、按上述已确定的筛选器回写 `local_tombstone_ledger`、刷新本地 `last_manifest_summary = matched_manifest_summary`、清除 `commit_intent_journal`、把 `vault_state.commit_in_progress` 置为 `false`；**事务提交后**再删除残留临时文件（文件系统操作不纳入 SQLite 事务；若此步骤前崩溃，残留文件在下次启动时由”启动预检 / 恢复”统一清理）；若 `observed_head_revision > matched_revision`，说明远端仍有后续新 head，恢复完成后必须立即进入 pull 流程继续收敛。
   **manifest 不可用 fallback**：若 `GET /vaults/:vaultId/manifest/:matched_revision` 返回 404（服务端已清理该历史 manifest），客户端应按以下步骤降级处理：(1) 仍将 `last_applied_revision`、`acked_revision`、`remote_head_revision`、`commit_intent_journal`、staging 文件、`commit_in_progress` 按上述规则收敛；**tombstone ledger 回写规则与正常命中路径相同**：若 `intent_delete_seq_upper_bound` 存在，则回写 `deleted_revision=null` 且 `local_delete_seq <= intent_delete_seq_upper_bound` 的条目；若该字段缺失，则回写 `deleted_revision=null` 且 `deleted_at <= commit_intent_journal.created_at` 的条目；两种情况下都不受 manifest 不可用影响；**且不修改 `pending_ack_to_server`**（submitted 命中恢复路径仍等同于”确认上次提交已成功”，服务端已在 commit 同一事务内更新该设备的 `acked_revision`，无需补做 `/ack` 上报）；(2) 将本地 `last_manifest_summary` 持久化标记为已过期（置为 `null` 或等价的”需刷新”哨兵值），并明确把该状态作为上文 Q-fm 临界区 A 的 commit gate；(3) 恢复完成后**必须立即进入对 `observed_head_revision` 的完整 pull**，以重新计算并写入最新的 `last_manifest_summary`；在该 pull 完成前，禁止发起新的 commit，且 pull 不得使用任何基于旧摘要的快捷 diff（避免以过期摘要作为 base 的错误 diff）
5. 若整个区间内均未匹配，则视为”上次提交确实未成功”，清除 `commit_intent_journal`、删除残留临时文件、把 `vault_state.commit_in_progress` 置为 `false` 后，正常进入新一轮 commit / pull 流程

**显式失败清理规则**：下列场景都属于”本次提交未成功生成新 revision”，必须在当前进程内立即执行清理，不能等到下次重启：

1. `blobs/check` 失败、blob 上传失败、快照阶段本地 I/O 失败，且此时 `commit_intent_journal.status = prepared`
2. 基于 `content_snapshot_plan` 的源版本校验发现快照漂移：任一待提交文件在明文快照落成前已偏离临界区 A 记录的冻结源版本
3. 服务端明确返回 `409 Conflict`
4. 服务端明确返回 manifest 校验失败、权限错误等确定性拒绝（4xx）

统一清理动作：

1. 清理 `.noteapp/staging/` 下所有残留 `*.snapshot.plain` 与 `*.blob.staging` 临时文件
2. 清除 `commit_intent_journal`
3. 把 `vault_state.commit_in_progress` 置为 `false`
4. 保留用户工作区与 `pending_local_changes` 的事实变更，后续按正常 rebase / 重试流程重新扫描；若本次失败原因为快照漂移，则下一轮**必须重新构建新的** `content_snapshot_plan`，不得复用旧计划、旧 `*.snapshot.plain` 或旧 `*.blob.staging`

**结果未知错误规则**：若 `status = submitted` 后遭遇超时、连接断开、客户端取消等待、5xx 且无法确认服务端是否已落盘，则**不得**直接做上述显式失败清理；必须立即进入上文的 `submitted` 结果确认流程，确认未落盘后才能清理并释放 `commit_in_progress`。
补充约束：在上述恢复 / 确认流程未完成前，**禁止**本机发起新的 commit 与拉取应用，避免与服务端不一致状态相互覆盖

> 设计意图：临界区 A / B / C 都很短（毫秒级），不会阻塞编辑器保存；而长耗时的快照加密、blob 上传与网络 commit 全部在 W-commit 工作线程内执行，对 UI 不可见。`vault_state.commit_in_progress` 标记保证同设备最多一个 commit 在飞，避免内存中”快照表”互相污染。

### 10.5 CAS 并发控制

服务端逻辑：

1. 读取 `current_head_revision`
2. 比对客户端 `base_revision`
3. 一致则提交
4. 不一致则 `409 Conflict`

客户端补充规则：

1. `base_revision` 必须对应本机当前 `last_applied_revision`
2. 只要存在未收敛的 pull 冲突或未处理冲突副本，就不得发起新的 commit
3. CAS 失败后，客户端不得重试原 manifest；必须先拉取新 head 并重做本地 rebase
4. 服务端在接受 `commit` 前，必须校验 manifest 基本不变量；校验失败直接拒绝，不允许坏 manifest 成为新 head

**关于 TOCTOU 窗口**：客户端先调用 `GET /vaults/:vaultId/head` 获取最新 revision，再调用 `GET /vaults/:vaultId/manifest/:revision` 下载 manifest，最后提交 commit。这两步之间存在时间窗口，另一设备可能已提交新 revision，导致 CAS 返回 409。这是**预期行为**，由 CAS 机制兜底处理；客户端收到 409 后按正常 rebase 流程处理即可，无需额外保护。

### 10.6 冲突处理

冲突处理有两个入口：

1. **pull 时发现“本地 dirty + 远端已更新”**
2. **commit 时 CAS 失败，随后拉取最新 head**

统一原则：

1. 当前远端 head 是新的 rebase 基线
2. **远端变化不得直接覆盖 dirty 工作副本**
3. 所有冲突结果都必须以“用户可解释、可恢复、不丢本地修改”为第一原则
4. **路径唯一性是强约束**：收敛完成后的 `working tree` 中，不允许两个 `status=active` 的 `file_id` 指向同一路径
5. **clean 文件回放顺序也必须安全**：凡涉及路径交换、重命名环或批量占位，均按 staging 两阶段落盘，不允许直接按最终路径顺序覆盖

文本文件：

1. 若本地为 `clean` 且远端有变化，直接应用远端版本
2. 若本地为 `dirty` 且远端无变化，保留本地工作副本，等待后续提交
3. 若同一 `file_id` 在共同基线上双方都修改，执行 three-way merge
4. merge 成功：
   - 将合并结果写回**规范路径**
   - 将该文件标记为“已 rebase 的本地待提交修改”
   - 用户下一次提交时以新 head 为 `base_revision`
5. merge 失败：
   - 规范路径写入远端 head 版本
   - 本地未提交版本落为冲突副本
   - 冲突副本分配新的 `file_id`
   - 原文件回到“基于远端 head 的 clean 状态”

二进制文件：

1. 不自动 merge
2. 若本地 `dirty` 且远端也变化，规范路径保留远端 head 版本
3. 本地未提交版本落为冲突副本，等待用户手工处理

路径冲突（不同 `file_id` 同一路径）：

1. 若远端 head 中 `file_id=A` 占用路径 `P`，而本地 `file_id=B` 也占用 `P`，优先保留 `A` 作为规范路径
2. 若 `B` 为 `clean`：
   - 若 `B` 在目标 manifest 中有对应条目且路径不同于 `P`，则将 `B` 搬移到其目标路径
   - 若 `B` 在目标 manifest 中不存在（已被远端删除），则消费 tombstone 删除 `B`
3. 若 `B` 为 `dirty`，则 `B` 必须转为冲突副本，分配新的冲突路径和新的 `file_id`
4. 该场景不做文本 auto-merge，因为冲突根因是身份冲突而不是“同一 `file_id` 的内容分叉”
5. `path collision` 事件必须写入同步日志，并在 UI 中单独标为“路径冲突”

删除操作：

1. 删除提交会写入 tombstone，而不是仅靠缺文件推断
2. 若”远端已删、本地仍改”，则保留删除结果为规范状态，本地修改版本转为冲突副本；冲突副本存放路径：优先存放在原文件所在目录；若原目录也已被删除，则存放在 Vault 根目录下的 `.noteapp/conflict-orphans/` 专用目录，并在同步日志中记录原路径
3. 若“远端已删、本地仅未同步且未改”，则本地直接消费 tombstone，不允许自动复活

#### tombstone 生命周期与回收

1. 每个设备在成功应用某个 revision 后，向服务端上报自己的 `acked_revision`
2. 服务端按 `vault_id + device_id` 维护每台**已加入该 vault 的设备**的最新 `acked_revision`
3. `POST /devices/register` 只是账号级设备注册，不会自动把该设备加入任意 vault，也不会让它立刻进入任意 vault 的 tombstone 回收判定集合
4. 设备首次加入某个 vault 的时机限定为以下两种之一：
   - 该设备成功拉取并应用该 vault 的 manifest 后，首次成功调用 `POST /vaults/:vaultId/devices/:deviceId/ack`
   - 该设备对该 vault 的首个 `commit` 成功，服务端在同一事务中写入新 head 并 upsert 该 vault 的设备成员记录
5. 未加入某个 vault 的设备，不参与该 vault 的 tombstone 回收判定，即使它已在账号层完成注册
6. tombstone 只有在**所有活跃且已加入该 vault 的设备**都满足 `acked_revision >= deleted_revision` 时，才可进入回收候选（此判断完全由服务端执行，客户端不参与回收决策）
7. 即使满足上条，也建议保留最短保留期（建议 30 天）后再物理清理
8. 长期离线设备不能无限阻塞回收：超过阈值（建议 30-60 天）未心跳的已加入设备，需在 UI 中标记为“失活候选”，由用户确认移除或服务端按策略失活
9. 已移除设备不再参与 tombstone 回收判定；设备移除后，服务端必须立即重新评估该设备所参与的所有 vault 的 tombstone 回收资格，将其从活跃设备集合中剔除后重新判定是否满足回收条件
10. 旧设备重新上线时，必须**先拉取最新 manifest 快照，再允许上传**；若其本地存在已被 tombstone 删除的旧文件，按 dirty/clean 状态处理：
    - 若该文件为 **clean**（用户未修改）：直接消费 tombstone，本地删除，不允许自动复活
    - 若该文件为 **dirty**（用户已修改）：转为冲突副本，提示用户处理，不能直接重新上传为主版本

### 10.7 V1 为什么不做 delta sync

原因：

1. 对小团队收益不高
2. 调试成本高
3. 整文件 + 哈希去重已足够先跑通

V1 只做：

1. 整文件同步
2. 哈希去重
3. 分片上传
4. 断点续传

---

## 十一、安全设计

### 11.1 原则

1. 云端不保存内容明文
2. 用户密钥不上传
3. 同步和 AI 分层，互不阻断
4. V1 的服务端会保存同步所需 manifest 元数据；其中包含 `content_hash`，因此 **V1 不承诺“云端看不见明文哈希”**，只承诺服务端无法仅凭这些元数据解密内容

### 11.2 加密方案

建议：

1. KDF：`Argon2id`
2. 内容加密：`XChaCha20-Poly1305`
3. 每个 Vault 创建时生成随机 256-bit `vault_key`
4. `vault_key` 只保存在本地安全存储中，服务端不保存明文密钥，也不代管可解密的密钥副本；**不得以明文形式存储在普通文件系统中**；各平台推荐实现：macOS Keychain、Windows DPAPI、Android Keystore、iOS Secure Enclave（或等价的系统级安全存储）
5. V1 新设备接入采用**本地配对 / 本地导入恢复包**方案，不依赖服务端中转密钥

#### 子密钥派生与确定性 nonce

为同时满足"跨设备内容寻址去重"与"服务端不持有明文内容或解密密钥"两个目标，V1 必须采用以下派生体系：

1. 子密钥派生（HKDF-SHA256，`info` 串区分用途，**禁止复用 `vault_key` 直接做加密或 HMAC**）：
   - `content_key := HKDF(vault_key, "noteapp/blob-content/v1", 32 bytes)`，作为 `XChaCha20-Poly1305` 的对称密钥
   - `blob_id_key := HKDF(vault_key, "noteapp/blob-id/v1", 32 bytes)`，用于 `HMAC_SHA256(blob_id_key, content_hash)` 派生 `blob_id`
   - 后续若引入新用途（如附件签名、wiki 索引校验），必须新增独立 `info` 串，禁止与上述两者重叠
2. blob 加密 nonce 派生：`nonce := HKDF(vault_key, "noteapp/blob-nonce/v1" || content_hash, 24 bytes)`
   - **确定性 nonce**：相同 `vault_key + content_hash` 永远派生出相同 nonce，配合 `content_key` 加密产生相同密文，是跨设备内容寻址去重得以实现的前提
   - nonce **不嵌入** 密文 blob 头部；解密侧由 `vault_key + content_hash` 重新派生，节省每个 blob 24 字节开销
3. 密文 blob 落盘格式：blob 文件 = 纯密文 + 16 字节 Poly1305 tag，无任何额外头部，所有元数据由 manifest 提供；因此 `encrypted_size = plaintext_size + 16`
5. **风险声明**：确定性 nonce 在同一 `vault_key` 下，相同明文会暴露"内容是否相同"的信号（可被用于重复内容推断），这是为支持去重的有意权衡，可接受；不同 vault 间因 `vault_key` 不同，相同明文产生不同 blob_id 与不同密文，相互不可关联
6. 客户端在 `POST /vaults/:vaultId/blobs/check` 与 `upload-init` 接口中，**只能用 `blob_id` 作为 key**，禁止把 `content_hash` 直接发给服务端
7. **元数据边界声明**：由于另一台设备解密时需要从 manifest 读取 `content_hash` 以重建 nonce，V1 的服务端会持久化该字段；因此本方案的安全边界是”服务端看不到内容明文、拿不到 `vault_key`、blob API 不接受 `content_hash` 直接寻址”，而**不是**”服务端完全看不到明文哈希”

#### 关于 AAD（Additional Authenticated Data）的设计说明

V1 的 `XChaCha20-Poly1305` 加密**有意不使用 AAD**，理由如下：

1. **当前安全边界**：若攻击者能操控对象存储中的 blob 映射，将文件 A 的密文伪装成文件 B 的内容，加密层无法在解密时检测此替换。但该攻击在上层由 manifest 完整性保护兜底——manifest 中记录了每个 `file_id` 对应的 `blob_id` 与 `content_hash`，伪造 blob 映射需同时篡改经服务端 CAS 保护的 manifest。
2. **潜在加固方向**（V1.1 可选）：将 `blob_id`（或 `vault_id || file_id`）作为 AAD 传入 `XChaCha20-Poly1305`，可在加密层独立检测跨文件密文替换攻击，不依赖 manifest 完整性。若引入 AAD，需确保解密侧能重建相同 AAD（`blob_id` 可由 manifest 提供），且对既有 blob 做迁移或版本区分。
3. **V1 结论**：不使用 AAD 是有意权衡，不是遗漏；安全边界在 §11.1 / §11.2 第 7 条已声明，不需要额外掩盖。

V1 密钥接入状态机明确如下：

1. **创建 Vault**：首台设备生成 `vault_key`，并生成一次性恢复包
2. **新增设备**：必须依赖“已登录且已解锁的现有设备”本地配对，或用户同时提供**恢复包文件 + 恢复短语**；恢复短语本身不携带恢复包内容，不能单独完成恢复
3. **设备注销**：只移除设备访问资格，不触发历史 blob 重新加密
4. **修改解锁口令**：本地重新 wrap `vault_key`，不重加密全部历史内容；**旧口令加密的 `vault_key` 副本必须在新口令封装成功后立即从本地安全存储中删除**，防止旧口令泄露后仍可解密 `vault_key`
5. **轮换 `vault_key`**：**不属于 V1 范围**。V1 只支持“改解锁口令，不换数据密钥”；真正的数据密钥轮换需要引入 `key_epoch` / 多 key 元数据 / 迁移协议，放到 V1.1 再设计

### 11.3 恢复机制

不宣传“密码找回”，只做：

1. 恢复包导出：导出 `vault_key` 的加密包、`vault_id`、创建时间和校验信息
2. 恢复短语保存：用户离线保存恢复短语，用于解锁恢复包；**恢复短语不是恢复包的替代品**
3. 本地导入恢复：新设备通过导入恢复文件、扫码或局域网面对面配对方式完成接入
4. **无现有设备时的恢复路径**：(1) 用户先完成账号登录认证；(2) 在新设备上同时提供恢复包文件与恢复短语；(3) 系统用恢复短语解密恢复包；(4) 提取 `vault_key` 和 `vault_id`，完成本地密钥初始化；(5) 从云端拉取最新 manifest 恢复数据。**认证必须先于密钥恢复完成**，否则无法访问云端 manifest。
5. **仅输入恢复短语而没有恢复包时**：V1 必须明确拒绝，并提示用户“请提供恢复包文件，或使用一台已解锁设备进行本地配对”
6. 本地改口令：使用已解锁的旧口令解开 `vault_key`，再用新口令重新封装
7. 恢复失败边界：**若无任何已解锁设备，且用户同时丢失恢复包 / 恢复短语，则现有加密同步数据不可恢复**

### 11.4 AI 隐私边界

必须明确：

1. 默认优先发送 `.ai/wiki/` 页面
2. wiki 不足时按以下顺序回查并发送必要片段：先 `Notes/` 下的原始笔记片段，再 `.ai/raw/` 中的中间产物（若本机可用）；其中”raw 片段”特指 `.ai/raw/` 目录内容（按 §25.1 定义为 ingest 流程的中间产物，默认不参与跨端同步），与 `Notes/` 原始笔记区分清楚
3. 默认不上传整个知识库
4. API Key 不进入同步
5. **”云端不保存明文”不等于”AI 明文永不离开本地”**，只要调用第三方模型 API，所选上下文就会按用户配置发送给该模型提供方

---

## 十二、AI 知识库方案

### 12.1 路线选择

本方案采用 **编译型知识库** 路线，而不是纯 RAG 路线。

核心参考：

1. Andrej Karpathy 于 **2026 年 4 月 4 日**公开的 `LLM Wiki` 思路

### 12.2 三层结构

#### Raw Layer

包括：

1. 用户笔记
2. 附件抽取文本
3. 外部导入资料
4. 网页、PDF、音频转写等原始材料

#### Wiki Layer

包括：

1. 主题页
2. 实体页
3. 时间线页
4. QA 页
5. 总结页

#### Schema / Control Layer

包括：

1. `AGENTS.md`
2. 索引规则
3. 引用规则
4. 命名规则
5. lint 规则

### 12.3 四步工作流

#### 1. Ingest

把原始内容送入 AI 流水线。

输入可以是：

1. 当前笔记
2. 文件夹
3. 搜索结果
4. 外部资料
5. 整个 Vault（仅用于显式触发的批量 Ingest / 编译知识页，不作为整库直接问答输入）

#### 2. Compile

AI 根据 schema 把原始内容整理成结构化 wiki 页面。

#### 3. Index

更新：

1. `.ai/index.md`
2. 页面间链接
3. 关键词映射
4. 实体映射

#### 4. Ask

用户提问时：

1. 优先查询 wiki 层
2. 不足时回查 raw 层
3. 生成引用式回答

### 12.4 为什么不把轻量 RAG 当主线

因为轻量 RAG 更像：

1. 从散乱文档里临时捞上下文

而这个产品更需要：

1. 长期沉淀
2. 逐步编译
3. 持续维护知识页
4. 让问答和写作共享知识中枢

### 12.5 wiki 页面类型

V1 最少支持：

1. `topic`
2. `entity`
3. `timeline`
4. `qa`
5. `summary`

每个知识页应包含：

1. 标题
2. 类型
3. 摘要
4. 正文结构
5. 来源引用
6. 相关页面
7. 更新时间

### 12.6 AI 查询逻辑

标准链路：

1. 用户输入问题
2. 选择范围
3. 先检索 `.ai/wiki/`
4. wiki 不足再回查 raw / note chunks
5. 组装上下文
6. 模型生成回答
7. 返回来源引用

### 12.7 检索实现

这里不是“不用检索”，而是“不让检索成为唯一核心”。

V1 推荐：

1. `SQLite FTS5` 负责标题、标签、文件名、精确词项召回
2. 后续可增加本地 embedding
3. 长期目标是混合检索

结论：

1. **编译型知识库 = 主路线**
2. **RAG / FTS / embedding = 基础设施**

### 12.8 AI 输出与写回策略

AI 可以输出：

1. 问答
2. 总结
3. 大纲
4. 新 wiki 页
5. 对现有 wiki 页的更新建议

默认必须用户确认后写回：

1. 插入当前笔记
2. 生成新知识页
3. 更新已有知识页
4. 复制到剪贴板

禁止：

1. 后台静默改写
2. 全库自动覆盖

### 12.9 AI 知识页版本管理

#### 问题背景

用户手工修订知识页后，AI 再次编译同一主题时，必须能判断"是否会覆盖用户修订内容"，不能静默覆盖。

#### 版本标记机制

每个 wiki 页面 frontmatter 增加以下字段：

```yaml
ai_generated: true          # 是否由 AI 生成
user_edited: false          # 当前页面是否带有尚未被“接受 AI 版本”显式清除的人工作业痕迹；该语义需随页面跨设备同步
last_ai_compiled_at: <ISO>  # 最近一次 AI 编译时间（注：与 AGENTS.md 中的 updated_at 字段不同；AGENTS.md.updated_at 记录编译规范文件本身的修改时间，此字段记录本 wiki 页面最近一次被 AI 编译的时间）
locked: false               # 用户是否锁定此页（锁定后 AI 不再更新）
last_compiled_from_sources_hash: <SHA256>      # 最近一次编译使用的来源集合摘要
last_synced_revision: <revision|null>          # 该页最近一次被同步引擎拉取并落盘远端版本时的 revision；仅由 Rust Core 在 `source = sync` 写入该页时更新，用于 stale compile 检测
```

#### 写回判断逻辑

除页面 frontmatter 外，每次 AI 编译任务在启动时还必须记录：

1. `task_base_page_hash`：任务启动时该 wiki 页当前内容哈希
2. `task_base_revision`：任务启动时本机 `last_applied_revision`
3. `task_sources_hash`：本次编译使用的来源集合哈希

AI 编译完成，准备写回 `.ai/wiki/` 时，按以下规则判断：

在进入下表判断链前，worker 必须先重新读取自身 `wiki_tasks.status`；只有状态仍为 `running` 才允许继续。若任务已被标记为 `superseded`、`failed`、`skipped` 或其他非 `running` 状态，必须立即终止，不得进入写回判定。

以下为**有序判断链**，按顺序逐条检查，命中第一条即执行对应处理，不再继续向下判断：

| 优先级 | 判断条件 | 处理方式 |
|--------|----------|----------|
| 1 | `locked: true` | 跳过此页，在 `.ai/log.md` 记录”已跳过（用户锁定）” |
| 2 | 当前页 `page_hash != task_base_page_hash`（页面在任务启动后已被修改，无论原因） | 一律禁止静默覆盖，必须弹出 diff 对比。**注意**：此处"无论原因"是有意的保守设计（与第3条保守设计对称）——即使修改原因是同步写入了与 AI 编译结果实质相同的内容，也触发确认流程，以确保用户感知到页面已被外部修改 |
| 3 | `last_synced_revision != null` 且 `last_synced_revision > task_base_revision`（该页在任务启动后被同步引擎更新过，stale compile） | 即使 `user_edited: false`，也进入 diff/确认流程。**注意**：即使同步落盘的远端版本与本地内容哈希相同（第2条未触发），只要 `last_synced_revision` 推进，仍触发本条；这是保守设计，确保 stale compile 不会静默覆盖同步状态 |
| 4 | `user_edited: true` 且 `locked: false` | 弹出 diff 对比，由用户选择”接受 AI 版本 / 保留手工版本 / 合并” |
| 5 | `task_sources_hash` 与当前来源集合不一致 | 允许继续展示 AI 结果，但默认不直接覆盖，提示用户”依据集合已变化”；若用户确认继续，则**必须先基于当前来源集合重新执行编译**：先把当前任务记录状态置为 `superseded`（终态，不再恢复），再创建新的 `wiki_tasks` 记录走完整判断链；不得复用旧任务的编译结果或旧的 `task_base_page_hash` / `task_base_revision` 值。**旧任务置为 `superseded` 与新任务创建必须在同一 SQLite 事务中原子完成**，不得拆成两个可中断步骤。**新任务走完整判断链时，若同时命中第 2 条（页面在旧任务运行期间已被修改），UI 应展示”来源已更新且页面已变化”的合并提示，不得把两次确认拆成独立弹窗分别弹出** |
| 6 | `page_hash == task_base_page_hash` 且 `user_edited: false`（以上均不命中） | 允许直接覆盖，并更新 `last_ai_compiled_at` / `last_compiled_from_sources_hash` |

**`last_synced_revision` 全局写入规则**：`last_synced_revision` 仅在同步引擎以 `source = sync` 拉取并落盘远端版本时更新；本地 AI 写回（`source = ai`）与本地 commit 成功均**不得**修改该字段。此规则适用于判断链所有条目，不限于第 6 条。

**`last_synced_revision = null` 的语义**：表示该 wiki 页”从未参与过同步”（如本地新建尚未 commit），不视为 stale；判断链第 3 条仅在 `last_synced_revision` 为非 null 数值且大于 `task_base_revision` 时触发。

**`task_base_revision = 0` 的语义**：新 vault 尚未应用任何远端 revision；此时若 `page_hash == task_base_page_hash`（第 2 条未触发），则应豁免第 3 条的 stale 判定，允许直接覆盖——因为页面内容实际未变化，仅 `last_synced_revision` 从 null 变为 1 不应触发强制确认。若 `page_hash != task_base_page_hash`，则第 2 条已拦截，无需第 3 条重复判定。**注意**：`last_synced_revision` 的合法非 null 值从 1 开始，0 不是合法的已同步 revision 值，不应出现在该字段中；若出现则视为数据异常，按 null 处理（即不触发第3条）。

**写回前最后一道栅栏**：即便判断链已命中“允许直接覆盖”或用户在 diff 中点击确认，Rust Core 在真正落盘前仍必须再次读取一次 `wiki_tasks.status` 与目标页当前 `page_hash`；只有在 `status = running` 且页面仍满足刚才的判定前提时才允许写入。否则本次写回必须中止，并把任务置为 `superseded` 或 `failed`（按触发原因记录）。

#### user_edited 标记时机

Rust Core 对 `.ai/wiki/` 文件的写入接口必须显式区分四种来源（`source`），并且只有 `source = editor` 才触发 `user_edited` 自动置位：

| 写入来源 `source` | 触发场景 | 是否触发 `user_edited = true` |
|------------------|----------|------------------------------|
| `editor` | 用户在编辑器中保存 | 是（且哈希实际变化时） |
| `sync` | 同步引擎拉取并落盘远端版本 | 否（按 frontmatter 同步语义，见第 3 条） |
| `ai` | AI 写回（用户确认后） | 否；同时按操作类型设置 `user_edited`（接受 AI 版本时置为 `false`） |
| `internal` | Rust Core 维护性写入（如 lint 修正、frontmatter 字段补齐） | 否（内部写入豁免） |

具体规则：

1. `source = editor` 写入：保存后 Rust Core 计算页面内容哈希，若与保存前不同，则将该页面 `user_edited` 置为 `true`；若保存前后内容哈希相同（自动保存但未实际修改），不置位
2. 用户在 AI 面板中点击”接受 AI 版本”后，写入以 `source = ai` 进行，且把 `user_edited` 显式置为 `false`
3. `user_edited` 是**随知识页内容一起同步的 frontmatter 语义位**；同步引擎以 `source = sync` 写入时，必须以远端页面 frontmatter 中的 `user_edited` 字段为准，本地不得因为”内容变了”这一事实自行推断或重置该字段
4. 若另一台设备上的用户手工编辑过该知识页并同步过来，则远端页面中的 `user_edited: true` 必须在本地继续保持为 `true`，从而在所有设备上都继续要求 AI 走 diff / 确认流
5. 只有两种情况允许把 `user_edited` 变回 `false`：
   - 当前用户在本机明确执行”接受 AI 版本”（写入 `source = ai`）
   - 远端同步下来的页面本身已经是”用户在另一台设备上明确接受 AI 版本后的结果”，其 frontmatter 已写成 `user_edited: false`，本地以 `source = sync` 写入并保留该字段
6. UI 层不得直接写 `.ai/wiki/` 文件；所有写入必须通过 Rust Core 接口指定 `source`，否则按 `source = editor` 处理（最严格语义）

#### 禁止行为

1. 不得在用户未确认的情况下覆盖 `user_edited: true` 的页面
2. 不得删除已有知识页，只能将其 frontmatter 中 `deprecated` 置为 `true`

---

## 十三、AI 交互窗口设计

### 13.1 桌面端形态

建议：

1. 右侧侧边栏 AI 面板
2. 独立全屏知识对话页
3. 页面中显示当前提问范围

### 13.2 核心操作

1. 问当前笔记
2. 总结当前笔记
3. 总结文件夹
4. 对搜索结果提问
5. 对整个 Vault 发起批量知识编译
6. 编译知识页
7. 插入回答

### 13.3 输出要求

1. 必须附来源
2. 可点击跳转原文
3. 若知识依据不足，必须明确提示
4. 不允许假装知道

---

## 十四、服务端设计

### 14.1 最小职责

1. 用户登录
2. 设备注册
3. vault head 查询
4. blob 检查
5. blob 上传授权
6. blob 下载授权
7. commit 提交
8. 设备心跳与活跃状态维护
9. `acked_revision` 记录维护
10. tombstone 回收资格判定
11. 设备列表与状态查询

### 14.2 推荐 API

1. `POST /auth/login`
2. `POST /devices/register`
3. `DELETE /devices/:deviceId`
4. `GET /vaults/:vaultId/devices`
5. `GET /vaults/:vaultId/head`（返回当前最新 revision 的元数据；响应体必须包含：`head_revision`（u64）、`commit_intent_id`（string）、`created_by_device`（string）、`created_at`（u64 UTC ms）；完整响应规格见补充约定第 14 条。客户端拉取时先调用此接口获取最新 revision，再调用 `GET /vaults/:vaultId/manifest/:revision` 下载对应 manifest 快照）
6. `GET /vaults/:vaultId/manifest/:revision`
7. `GET /vaults/:vaultId/revisions?from=<u64>&to=<u64>`（**轻量 revision 元数据列表**；专用于 submitted 崩溃恢复的 `commit_intent_id` 扫描，见补充约定第 15 条）
8. `POST /vaults/:vaultId/blobs/check`
9. `POST /vaults/:vaultId/blobs/upload-init`
10. `POST /vaults/:vaultId/blobs/download-init`
11. `POST /vaults/:vaultId/commit`
12. `POST /devices/:deviceId/heartbeat`（账号级，不区分 vault）
13. `POST /vaults/:vaultId/devices/:deviceId/ack`

补充约定：

1. `POST /vaults/:vaultId/commit` 在写入新 head 前，必须校验 manifest 基本不变量，至少包括：
   - `files[]` 中 `file_id` 唯一
   - `files[]` 中 `path` 唯一（NFC 归一化后比较）
   - `tombstones[]` 中 `file_id` 唯一
   - 任一 `file_id` 不得同时出现在 `files[]` 与 `tombstones[]`
   - `blob_id` 必须已存在或已在本次上传流程中完成注册
   - `path` 必须是合法 Vault 相对路径，不能逃逸出 VaultRoot
   - **`path` 必须已是 Unicode NFC 编码**，服务端按字节比较 `nfc(path) == path`，不一致即拒绝
   - 提交的 `manifest.revision` 字段必须为 null（或省略）；服务端 CAS 通过后填入 `new_revision`
   - 对”本次新产生的 tombstone”，客户端提交时 `deleted_revision` **必须为 `null`**；若上报为 `0`、空字符串或任何其他替代值，服务端必须拒绝提交。只有在该值为 `null` 时，服务端才可在生成新 `revision` 后统一回填 `deleted_revision = new_revision`
   - 对”前一版 head 中已存在的 tombstone”，客户端不得改写其 `deleted_revision`；若上报值与历史值不一致，提交必须被拒绝
2. 上述校验任一失败时，`POST /vaults/:vaultId/commit` 必须拒绝提交，不得生成新 revision
3. `POST /vaults/:vaultId/commit` 请求体必须包含 `commit_intent_id` 与 canonical manifest 的稳定摘要 `intent_manifest_hash`
4. `POST /vaults/:vaultId/commit` 成功时，应在同一事务内把 `created_by_device` 的 `acked_revision` 更新为新 `revision`，并为该 `vault_id + device_id` upsert 一条“已加入该 vault”的设备成员记录；同时把 `commit_intent_id` 与 `intent_manifest_hash` 持久化到该 revision 的元数据中，供客户端崩溃恢复判定
5. `POST /vaults/:vaultId/blobs/download-init` 用于**拉取远端 blob 前**获取受控下载凭证；请求体至少包含 `blob_id`，响应至少包含短时有效的 `download_url`（或等价的流式下载句柄）、`expires_at`、`encrypted_size`（即 §11.2 定义的 `plaintext_size + 16`）与 `blob_id` 回显。该接口的职责是把”manifest 中的逻辑 `blob_id`”映射为一次性受控下载能力；**不得**要求客户端自行拼接对象存储路径，也不得把 `blob_id` 当作长期公开对象 key 暴露。**权限约束**：V1 不允许下发”可脱离服务端鉴权独立生效”的对象存储直签 URL；`download_url` 若存在，必须是**设备 / session 绑定、可即时撤销**的受控下载入口（或等价的服务端流式句柄）。设备被移除、session 被吊销、token 失效或服务端显式撤销后，**已签发但尚未使用的下载凭证也必须立即失效**，后续访问返回 `401/403`（或等价的 access denied），不得仅依赖 `expires_at` 自然过期。**凭证过期重试**：若下载过程中凭证已过期（`expires_at` 到期），客户端应重新调用 `download-init` 获取新凭证后重试，不得将凭证过期视为不可恢复错误。**下载进行中的处理**：若设备在 blob 下载传输过程中被移除，服务端应立即中断该连接（返回 `401/403` 或直接关闭 TCP 连接）；客户端侧应捕获该中断，停止写入本地文件，并清理不完整的 `.staging` 残留；不得允许”设备已移除、但当前传输仍可完成”的宽松语义
6. `POST /vaults/:vaultId/devices/:deviceId/ack` 用于**拉取并成功应用 revision 后**的显式确认；必须幂等，服务端取 `max(current_acked, reported_acked)`，重复上报不报错；若该设备此前尚未加入该 vault，则本次成功 ack 同时视为”加入该 vault”（即 §10.6 第4条定义的”加入该 vault”时机之一）
7. `POST /vaults/:vaultId/devices/:deviceId/ack` 上报失败不影响本地状态，客户端下次同步时重试；本地 `acked_revision` 先于网络上报更新
8. `POST /devices/:deviceId/heartbeat` 至少更新 `last_seen_at`，供”活跃设备 / 失活候选设备”判定使用
9. 服务端的 tombstone 回收器只能基于”活跃设备集合 + acked_revision + 最短保留期”做清理，不能仅按时间直接删
10. 服务端保存的是 manifest 与 blob，不保存也不裁决客户端的 `filemap.json` 文件本体；`filemap.json` 只存在于客户端本地。**注意：manifest 属于服务端持久化元数据，其中包含 `content_hash`、`blob_id`、路径和 tombstone 信息**
11. `GET /vaults/:vaultId/devices` 至少返回：`device_id`、`device_name`、`last_seen_at`、`acked_revision`、`status(active|inactive_candidate|removed)`，供设备管理 UI 展示和用户确认移除；该接口只返回**已加入当前 vault** 的设备
12. `DELETE /devices/:deviceId` 表示**账号级设备注销**：该设备会从用户账号下的所有 vault 活跃设备集合中移除，而不是仅从当前 vault 隐藏；**设备移除后，服务端必须立即重新评估该设备所参与的所有 vault 的 tombstone 回收资格**，将其从活跃设备集合中剔除后重新判定是否满足回收条件；同时必须**立即吊销**该设备现有的 access token / refresh token / 设备会话，并要求所有后续以该 `device_id` 发起的 vault 级 API（至少包括 `GET /vaults/:vaultId/head`、`GET /vaults/:vaultId/manifest/:revision`、`POST /vaults/:vaultId/blobs/check`、`POST /vaults/:vaultId/blobs/upload-init`、`POST /vaults/:vaultId/blobs/download-init`、`POST /vaults/:vaultId/commit`、`POST /vaults/:vaultId/devices/:deviceId/ack`）返回 `401` 或 `403`；**补充约束**：此前已由 `download-init` 签发、但尚未使用或尚未完成下载的 blob 下载凭证，也必须在设备移除时同步失效，不得出现“新 API 已拒绝，但旧下载 URL / 句柄仍可继续读取 blob 直到自然过期”的状态；不得出现“设备已从 tombstone 判定集合移除，但仍可继续读写该 vault”的状态
13. 若后续需要”仅从单个 vault 移除设备”，必须新增独立接口，不能复用 `DELETE /devices/:deviceId`
14. 由于 V1 新设备接入采用本地恢复包 / 本地配对方案，服务端**不提供** `vault_key` 代管、转发或找回 API
15. `GET /vaults/:vaultId/head` 响应体除 `head_revision`（u64）外，还必须包含该 revision 的元数据：`commit_intent_id`（string）、`created_by_device`（string）、`created_at`（u64 UTC ms）；供 §10.4 submitted 结果确认流程的第 1 步直接比对。**注意**：该接口只提供元数据，不提供最终 `last_manifest_summary`
16. `GET /vaults/:vaultId/revisions?from=X&to=Y` 为**轻量版 revision 元数据列表接口**，仅返回指定范围内每个 revision 的元数据，**不含 blob、`files[]` 或 `tombstones[]` 正文内容**；响应格式为数组，每项包含：`revision`（u64）、`commit_intent_id`（string）、`intent_manifest_hash`（string）、`created_by_device`（string）、`created_at`（u64 UTC ms）。该接口专用于 §10.4「submitted 结果确认流程」steps 2–3 中跨多个 revision 扫描 `commit_intent_id`，避免逐个下载全量 manifest；`from` 和 `to` 均为闭区间；单次请求上限建议 1000 个 revision，超出时服务端返回截断标记，客户端分页继续。**命中后的处理规则见 §10.4 submitted 结果确认流程 step 4**（不得把元数据中的 `intent_manifest_hash` 误当作已回填 revision 后的 manifest 摘要）

### 14.3 AI 与服务端的关系

V1 不建议把 AI 强绑定到同步服务端。

推荐顺序：

1. 第一阶段：客户端 BYOK 直连模型 API
2. 第二阶段：轻量 AI Gateway
3. 第三阶段：平台托管、额度和计费

这样做的好处：

1. AI 故障不影响编辑与同步
2. 上线门槛更低
3. 商业化之前不需要背模型成本

---

## 十五、平台边界

### 15.1 桌面端

优先级：

1. Windows
2. macOS
3. Linux

### 15.2 Android

要考虑 Scoped Storage。

V1 推荐：

1. 若优先落在 App 私有目录，必须同时提供“导出完整 Vault”能力
2. 若用户主动选择系统文件选择器授权目录，则优先使用授权目录作为可见存储位置
3. 产品文案需明确：私有目录方案满足离线与同步，但“直接可见”能力弱于桌面端

### 15.3 iOS

V1 建议：

1. 优先私有目录
2. 外部目录能力保守支持
3. 必须提供“导出完整 Vault”能力，确保用户可脱离产品迁移数据
4. 后台同步只做 best-effort

---

## 十六、团队与实施计划

### 16.1 团队配置

推荐 3 人：

1. 工程师 A：桌面端 UI
2. 工程师 B：Rust Core + SQLite + Sync
3. 工程师 C：服务端 + Flutter + 测试协助

若只有 2 人：

1. 一人桌面端
2. 一人 Core + 服务端
3. 移动端后置

### 16.2 分阶段推进

#### 阶段 0：POC（1-2 周）

目标：

1. 跑通 Tauri + Rust Core
2. 跑通 Flutter FFI 最小调用（**高风险验证点：若 FFI 无法打通，移动端方案需整体重新评估，不建议进入全面开发**）
3. 跑通 SQLite FTS5
4. 跑通对象存储上传下载
5. 跑通 AI ingest / compile / ask 最小链路
6. 跑通 `filemap.json` 的生成、保存与恢复

#### 阶段 1：桌面本地可用（4-6 周）

目标：

1. Vault 管理
2. Markdown 编辑
3. 双向链接
4. 本地搜索
5. 自动保存
6. 崩溃恢复流程验证（草稿文件生成、启动检测、恢复提示）
7. AI 窗口壳子
8. 当前笔记总结 MVP
9. 基于阶段 0 POC 结论，确认 AI 链路集成方式（模型 API 调用路径、上下文拼装接口、写回触发点）

#### 阶段 2：同步闭环（4-6 周）

目标：

1. manifest 模型
2. blob 上传下载
3. CAS commit
4. 冲突副本
5. 同步日志
6. `filemap.json` 与 manifest 对齐
7. `acked_revision` / heartbeat / tombstone 回收闭环
8. 两台桌面设备同步
9. AI 基于知识页的问答 MVP
10. 拉取阶段的 dirty working tree 保护、rebase 和冲突副本闭环
11. path collision 检测与收敛（不同 `file_id` 竞争同一路径时的拦截与处理）
12. clean 文件路径交换 / 重命名环的两阶段落盘实现（staging 目录机制）

到此为止，构成 **V1 可上线交付线**。

#### 阶段 3：V1 稳定与发布（2-3 周）

目标：

1. 修复边界问题
2. 安装包签名
3. 上线材料
4. FAQ / 隐私说明 / 用户手册

#### 阶段 4：V1.1 移动端最小接入（3-5 周）

目标：

1. 浏览
2. 简单编辑
3. 前台同步
4. 当前笔记 AI 总结
5. 简易问答

### 16.3 总周期

更现实的总周期：

1. **V1（桌面+桌面同步+桌面 AI MVP）：3-4.5 个月**
2. **V1.1（补移动端最小接入）：4.5-6 个月**

如果要求 3-4 个月，则必须进一步砍：

1. iOS 延后
2. Linux 延后
3. AI 只保留当前笔记总结和知识页问答 MVP

---

## 十七、测试与验收

### 17.1 基础功能测试

必须覆盖：

1. 新建/修改/删除/重命名/移动笔记
2. 附件插入与相对路径恢复
3. 双向链接与反向链接
4. 搜索索引增量更新
5. 自动保存恢复
6. `.noteapp/drafts/` 草稿恢复与清理
7. 恢复包文件 + 恢复短语接入新设备
8. 仅输入恢复短语、缺少恢复包时被正确拒绝，并展示明确提示

### 17.2 同步测试

必须覆盖：

1. 单端修改同步
2. 双端不同文件并发修改
3. 双端同文件并发修改
4. 删除与重命名交叉操作
5. 断网恢复
6. 错误密码
7. 上传中断续传
8. manifest 损坏
9. 对象存储短时不可用
10. 落后多 revision 的旧设备直接拉取最新 manifest 后恢复正确状态
11. **应用内**重命名 / 移动：同一 `file_id` 不会退化为”删除 + 新建”（强制，阻塞发布）
12. 不同 `file_id` 竞争同一路径时，目标 manifest 的规范路径能稳定收敛，且落败方不会被静默覆盖
13. 本地 `filemap.json` 存在两个 `status=active` 条目指向同一路径时，提交被正确拦截（路径唯一性校验）
14. clean 文件发生路径交换 / 重命名环时，两阶段落盘能正确收敛，不会因回放顺序导致覆盖或失败
15. 服务端能拒绝 `files[]` 中出现重复 `path` 或重复 `file_id` 的非法 manifest
16. 服务端能拒绝 `tombstones[]` 中重复 `file_id` 或篡改既有 `deleted_revision` 的非法 manifest
17. 服务端为“本次新删除”的文件正确回填 `deleted_revision = new_revision`，且后续 revision 不会改写该值
18. 长期离线旧设备回连后，不会误复活已被 tombstone 删除的文件
19. SQLite 重建后，能够基于 `filemap.json` 恢复原有 `file_id`
20. `commit` 成功后创建设备的 `acked_revision` 自动推进
21. 拉取成功后的显式 ack 能正确影响 tombstone 回收资格
22. 设备列表接口能正确展示 `last_seen_at`、`acked_revision` 与失活候选状态
23. 拉取远端新 head 时，dirty 本地文件不被远端版本直接覆盖（dirty working tree 保护）
24. 远端 tombstone 删除 + 本地 dirty 场景：本地修改版本转为冲突副本，规范路径按 tombstone 删除
25. 远端 manifest 应用过程中若在“文件已部分落盘、`filemap.json` 未重写”阶段崩溃，App 重启后会先进入同步恢复模式，而不是把半应用状态误判为新的本地 dirty 变更
26. 新注册但尚未加入某 vault 的设备，不会阻塞该 vault 的 tombstone 回收
27. 设备在首次成功 `ack` 或首次成功 `commit` 后，才进入该 vault 的活跃设备集合并参与 tombstone 判定
28. `filemap.json` 重建时，`status=deleted` 条目能基于目标 manifest / `local_tombstone_ledger` 正确恢复，不会因仅扫描当前文件系统而丢失 tombstone 元数据
29. 若 `.noteapp/conflict-orphans/` 非空，完整 Vault 迁移包必须包含该目录；导入后未解决孤儿冲突副本仍存在，且本地 `has_unresolved_conflicts` 被正确置为 `true`
30. SQLite 损坏或重建后，再次生成同名冲突副本时不会覆盖既有未解决副本；最终命名以文件系统占位校验为准
31. 提交成功后客户端在落本地 `last_applied_revision` 前崩溃，重启后能基于 `commit_intent_journal` + 远端 `commit_intent_id` / `intent_manifest_hash` 正确识别”上次提交已成功”，而不是误判为普通 CAS 冲突（注：此处 `intent_manifest_hash` 仅用于远端 commit 匹配识别，不用于 `last_manifest_summary` 的计算，见 test 43）
32. 提交成功后客户端崩溃，另一台设备随后又提交新 revision（导致 head 的 `created_by_device` 不是本机），本机重启后仍能通过扫描 `base_revision+1` 至 head 之间的所有 revision 正确识别自己的历史提交，而不是误判为”提交未成功”导致重复提交
33. Q-fm 临界区 A 完成后、commit 请求发出前崩溃；重启后残留 `status = prepared` 的 `commit_intent_journal` 会被正确清理，`commit_in_progress` 被释放，且用户本地变更不会丢失
34. `status = submitted` 后发生超时 / 连接中断，客户端不会直接清空 journal，而是先执行远端确认；只有确认该 intent 未落盘后才清理临时文件并释放 `commit_in_progress`
35. 启动预检 / 恢复必须先于任何新 commit 与新拉取执行；存在残留 `prepared` / `submitted` / 历史兼容 `acknowledged` journal 时，系统不会直接进入新的 Q-fm 临界区 A
36. 启动时若发现残留 `status = acknowledged` 的 `commit_intent_journal`，客户端不会用 `intent_manifest_hash` 与 `last_manifest_summary` 做本地短路判断，而是按 `submitted` 等价流程执行远端确认
37. 启动时若发现 `commit_in_progress = true` 但不存在任何活跃 journal，系统只将其视为旧版本兼容或本地状态损坏的兜底异常态；清理并释放锁后不会误删用户工作区变更
38. `blobs/check` 失败、上传失败、409、manifest 校验失败等显式失败场景下，客户端会在当前进程内清理 `commit_intent_journal`、临时文件和 `commit_in_progress`，不会卡住后续 commit
39. commit 成功后，仅有 `deleted_revision = null` 且 `local_delete_seq <= commit_intent_journal.intent_delete_seq_upper_bound` 的本地 tombstone ledger 条目会被回写为 `new_revision`；commit 启动后才产生的删除事件（序列号更大）不得被误记入本次 revision（submitted 命中路径的等价断言见 test 47）
40. 应用重启后，本地持久化的 `acked_revision` 与 `last_applied_revision` 仍保持单调一致；拉取成功路径与 commit 成功路径推进后的值不会因重启回退
41. 成功查询远端 `head` 后，本地 `remote_head_revision` 会刷新为返回值；本地 commit 成功后，该字段也会推进到 `new_revision`，不会停留在旧缓存
42. 成功应用远端 manifest 或本地 commit 成功后，`last_manifest_summary` 会刷新为当前已收敛 head 的摘要；重启恢复后不会与 `last_applied_revision` / `remote_head_revision` 脱节
43. `submitted` 命中“上次提交已成功”后的恢复分支，会额外拉取 `matched_revision` 对应 manifest 计算最终 `last_manifest_summary`；不得把 `intent_manifest_hash` 直接当作本地收敛后的 manifest 摘要
44. 若 `submitted` 扫描命中的是历史 revision、但当前 `observed_head_revision` 更高，本地恢复后 `last_applied_revision / acked_revision` 仅推进到命中 revision，`remote_head_revision` 仍保留为最新 head，且系统会立即进入后续 pull 收敛
45. pull 应用在 `sync_apply_journal` 未清除前崩溃时，恢复完成后 `remote_head_revision`、`last_manifest_summary`、`acked_revision` 与 `pending_ack_to_server` 也会一起收敛；不得出现“文件已收敛但本地状态缓存仍停留旧值”的半完成状态
46. App 启动时若仅发现 `.noteapp/staging/` 残留而不存在 `sync_apply_journal`，系统不会再按 `<file_id>.staging` 独立回放到规范路径，而是转入 `staging-orphans` / 完整拉取 / 人工修复分支，避免形成与 journal 并行的第二套恢复协议
47. 若 `submitted` 恢复命中后拉取 `GET /vaults/:vaultId/manifest/:matched_revision` 返回 404，且该历史 journal **存在** `intent_delete_seq_upper_bound`（当前版本路径），客户端会把 `last_manifest_summary` 持久化标记为 stale/null、立即进入对 `observed_head_revision` 的完整 pull，并在该 pull 完成前拒绝新的 commit；同时 `local_tombstone_ledger` 中 `deleted_revision=null` 且 `local_delete_seq <= commit_intent_journal.intent_delete_seq_upper_bound` 的条目已被回写为 `matched_revision`，不因 manifest 不可用而停留 null；完整 pull 成功后，`last_manifest_summary` 恢复为有效摘要，后续 commit 不再被 gate 拒绝（与 test 48 联合验证；历史 journal 缺字段时见 test 60）
48. 当本地 `last_manifest_summary` 为 stale/null sentinel 时，拉取流程不会走任何基于旧摘要的快捷 diff，而是直接按最新 manifest 执行完整收敛；成功 pull 后会重写有效摘要并自动解除 commit gate
49. 新建 Vault 完成首次本地编辑后，首次 commit（`last_applied_revision = 0`，无任何历史 journal）能正常发起，不被 `last_manifest_summary` gate 阻断；commit 成功后 `last_manifest_summary` 由**空 vault 的 final head 摘要常量**刷新为新 head 的 canonical manifest 摘要；同版本客户端对该空 vault 常量的输出必须完全一致
50. 通过 §7.2.3 完整迁移包导入后，目标设备会把同步基线置为“待重建”状态：`last_manifest_summary` 进入 stale/null sentinel，新的 commit 被 gate 拒绝；只有在完成一次 pull / reconcile、重建 `last_applied_revision` / `remote_head_revision` / `acked_revision` / `last_manifest_summary` 后，后续 commit 才允许正常发起
51. 某次 commit 在 Q-fm 临界区 A 之后、临界区 C 之前进行期间，用户若新建文件、重命名 / 移动文件、删除文件或处理冲突副本，这些会改写 `filemap.json` 的结构操作不会穿插污染当前 commit：要么在 Q-fm 队列中排队到当前 commit 结束后再落地，要么被 UI 明确提示稍后重试；本次已在飞 manifest 必须仍只反映临界区 A 锁定时的结构视图
52. 某次 commit 在 W-commit 长耗时阶段进行时，用户对既有文件内容继续编辑不会回流修改当前已在飞 manifest / `intent_manifest_hash`；这些内容变更只会保留在 working tree 中，等待后续 commit 单独提交
53. 某次 commit 在临界区 A 已生成 `content_snapshot_plan` 后、相关 `.snapshot.plain` 尚未全部落成前，若用户继续修改其中任一文件，客户端会把本次 commit 整体判定为“快照漂移失败”并立即清理临时文件 / journal / `commit_in_progress`，随后重新扫描生成下一轮 commit；不得产出混合两个时刻内容的 manifest
54. SQLite 损坏但 `VaultRoot/.noteapp/tombstone-ledger.jsonl` 仍可用时，系统能先据此重建 `local_tombstone_ledger`，再恢复 `filemap.json` / 索引；若 `tombstone-ledger.jsonl` 末尾存在不完整行，系统跳过该行继续解析，不因单行损坏导致整个账本不可用；若镜像文件为空、仅含空白或仅含一个尾部截断且前面没有完整记录，则把它视为**合法空 ledger** 而非损坏；仅当 SQLite 与 `tombstone-ledger.jsonl` 同时不可用，或镜像文件含非空畸形内容且无法解析出任何合法记录时，才必须进入人工修复模式并阻止新的 commit
55. 设备执行 `DELETE /devices/:deviceId` 后，原设备持有的 access token / refresh token / session 会被立即吊销；其后对 `GET /vaults/:vaultId/head`、`GET /vaults/:vaultId/manifest/:revision`、`POST /vaults/:vaultId/blobs/check`、`POST /vaults/:vaultId/blobs/upload-init`、`POST /vaults/:vaultId/blobs/download-init`、`POST /vaults/:vaultId/commit`、`POST /vaults/:vaultId/devices/:deviceId/ack` 的访问都返回 `401/403`，且该设备不再参与 tombstone 回收判定
56. 拉取远端新增或更新文件时，客户端会先调用 `POST /vaults/:vaultId/blobs/download-init` 获取一次性下载能力，再获取密文 blob 并解密落盘；实现不得要求客户端自行拼接对象存储路径，也不得把 `blob_id` 当作长期公开 URL
57. 本次新产生的 tombstone 在客户端导出 manifest 时始终使用 `deleted_revision = null`；若客户端错误上报 `0`、空字符串或其他替代值，服务端会拒绝提交，从而保证 `intent_manifest_hash` 与服务端校验对新 tombstone 的序列化语义一致
58. 若本地在 `submitted` 命中恢复前已经存在较早 revision 的 `pending_ack_to_server` 待上报队列，执行”上次提交已成功”恢复分支后，该字段仍保持原值、不会被清空或覆盖；可观测断言：恢复完成后 `pending_ack_to_server` 集合与恢复前完全一致；模拟网络恢复后，`/ack` 重试能成功上报这些既有 revision，服务端对应设备的 `acked_revision` 推进到正确值
59. 设备在被 `DELETE /devices/:deviceId` 移除前已拿到 `POST /vaults/:vaultId/blobs/download-init` 返回的下载凭证时，设备移除后该旧凭证也会立即失效：再次访问返回 `401/403`（或等价 access denied），不得继续读取 blob 直到 `expires_at` 自然到期
60. 若启动时命中的历史 `submitted/acknowledged` journal 缺失 `intent_delete_seq_upper_bound`（升级前遗留），客户端仍能完成恢复：tombstone 回写退回 `deleted_revision = null && deleted_at <= commit_intent_journal.created_at` 的 legacy 规则，不要求旧 ledger 记录存在 `local_delete_seq`；恢复完成后该历史 journal 被清除，后续新 commit 生成的 journal 全部携带 `intent_delete_seq_upper_bound`
61. 若应用从 `v1.0.41` 之前版本升级后，本地仍存在 `deleted_revision = null` 但 `local_delete_seq` 缺失的历史 tombstone，启动预检会先按 `deleted_at` 升序、`file_id` 字典序补写 `local_delete_seq`，并同步推进 `vault_state.local_delete_sequence`；补号完成前，系统不会进入新的 commit、pull 或 journal 恢复。补号完成后，升级后的首个**新 commit** 仍能用 `intent_delete_seq_upper_bound + local_delete_seq` 正确回写这些升级前遗留 tombstone；若任一待补号条目缺失合法 `deleted_at`，系统进入人工修复模式并阻止新的 commit

参考覆盖（非阻塞）：

1. **外部文件系统**重命名 / 移动：客户端能按 best-effort 规则（同目录或近邻目录 + 相同 `content_hash` + 接近 `mtime` + 文件类型一致）识别为重命名 / 移动，而非直接降级为”删除 + 导入”

### 17.3 AI 测试

必须覆盖：

1. 当前笔记总结
2. 文件夹总结
3. 基于知识页的桌面端问答
4. wiki 编译成功率
5. 来源引用回跳
6. 依据不足时的拒答
7. AI 故障不影响本地编辑和同步
8. `.ai/raw/` 缺失时的按需重建或能力降级提示
9. 跨设备同步后的知识页若带有 `user_edited: true`，另一台设备上的 AI 回写仍必须进入 diff / 确认流
10. “整个 Vault”级别批量知识编译的二次确认与隐私提示
11. 用户选择“整个 Vault”时，系统进入的是批量编译流程，而不是整库直接问答流程
12. 同一路径旧 `wiki_tasks` 被标记为 `superseded` 后，即使其 worker 已拿到旧编译结果，也必须在写回前中止，禁止 stale 结果覆盖新任务或用户修改
13. `last_synced_revision` 仅在 `source = sync` 的远端落盘路径更新；本地 AI 写回与本地 commit 成功后，该字段都不会被错误推进
14. 调整 `.ai/raw/` 的本地体积上限、自动清理策略或“导出迁移包时是否附带 `.ai/raw/`”选项，只会影响本机缓存管理或单次导出内容；不会改变 manifest / sync set，也不会因为不同设备的 raw 保留策略不同而制造跨端同步分歧
15. 触发“来源集合已变化，需要 supersede 旧任务并创建替代任务”时，旧任务终态化与新任务落库必须原子完成；即使在两者之间崩溃，恢复后也不会出现“旧任务已停、新任务不存在、重编译请求丢失”的状态

### 17.4 性能硬门槛

以下为 **V1 上线前必须达到** 的性能基准，未达标不得发布：

| 场景 | 基准要求 |
|------|----------|
| 本地全文搜索（10000 篇笔记） | 首字响应 < 200ms |
| 本地全文搜索（1000 篇笔记） | 首字响应 < 50ms |
| 笔记打开渲染（< 100KB） | < 300ms |
| 自动保存写入（单文件） | < 100ms |
| 同步引擎处理 1000 个文件变更 | < 30 秒（Wi-Fi 环境，不含网络传输） |
| 桌面端冷启动到可编辑 | < 2 秒 |

**基准测试假设条件：**
- 同步引擎基准：平均文件大小 10KB；不同文件大小下结果可能显著不同

**说明：**
1. 搜索基准基于 SQLite FTS5，中文分词补层不得使首字响应超过 500ms
2. 同步处理时间仅指本地 manifest 生成、diff 计算、加密和冲突收敛耗时
3. 移动端性能不属于 V1 硬门槛

### 17.5 上线门槛

建议至少满足：

1. 单文件连续同步 1000 次无损坏
2. 冲突结果可解释、可恢复
3. 删除不会被离线旧设备误复活
4. AI 回答默认附来源
5. AI 服务不可用时客户端优雅降级
6. 拉取远端新 head 时，不会覆盖 dirty 本地工作副本
7. 无任何已解锁设备且无恢复包/恢复短语时，产品明确提示“数据不可恢复”，不制造伪恢复承诺

### 17.6 参考指标（非阻塞）

以下指标用于评估体验，不作为 V1 阻塞发布条件：

1. AI ingest 100 篇笔记（平均 2KB/篇） < 60 秒（含 API 调用）
2. AI 问答首 token 响应 < 3 秒（网络正常时）
3. 移动端冷启动到可编辑 < 3 秒

---

## 十八、主要风险

### 风险 1：Rust Core 成本偏高

应对：

1. 先做 POC
2. 共享核心先只承载同步、索引、AI schema，不追求一步到位

### 风险 2：移动端文件权限复杂

应对：

1. V1 限制在受控目录
2. 不承诺桌面级自由目录能力

### 风险 3：同步 bug 难排查

应对：

1. 日志必须完善
2. 先桌面-桌面，再接移动端
3. 先整文件同步

### 风险 4：AI 范围失控

应对：

1. V1 只做问答、总结、知识编译
2. 不做全自动 Agent
3. 不做平台级 AI 编排系统

### 风险 5：编译型知识库质量不稳定

应对：

1. 用 `AGENTS.md` 约束页面 schema
2. 引入 lint 与引用检查
3. 重要知识页允许用户手工修订

### 风险 6：Flutter FFI 无法打通

这是移动端的最高优先级验证点，必须在 POC 阶段（第 0 阶段）完成验证，不得推迟。

应对：

1. POC 阶段第一周即验证 UniFFI 在 Android arm64 和 iOS arm64 上的编译与链接
2. 若 UniFFI 不可行，立即评估 `flutter_rust_bridge` 作为备选
3. 若两种方案均无法打通，移动端整体方案需重新评估（考虑放弃 Flutter，改用原生 Swift/Kotlin 各自调用 Rust，或放弃共享 Rust Core 改为移动端独立实现）
4. 此风险一旦触发，将影响整体工期 4-8 周，需提前告知团队

---

## 十九、成本预算

### 19.1 初期成本

主要包括：

1. 云服务器
2. 数据库（初期托管 SQLite，见 9.4 节）
3. 对象存储
4. CDN（附件分发）
5. 域名与 HTTPS 证书
6. Apple Developer Program（688 元/年）
7. 测试设备

### 19.2 月度估算

#### 基础场景：用户量 < 1000

| 项目 | 估算 | 说明 |
|------|------|------|
| 云服务器 | 50-150 元/月 | 2核4G，轻量应用服务器 |
| 数据库（Turso） | 0-50 元/月 | 免费额度内基本够用 |
| 对象存储（存储费） | 20-80 元/月 | 按实际存储量计费 |
| 对象存储（流量费） | 30-150 元/月 | 同步下载流量，附件场景不可忽略 |
| CDN | 0-50 元/月 | 初期可不开，用户投诉延迟后再开 |
| 监控与告警 | 0-30 元/月 | 使用云厂商基础监控 |
| 备份存储 | 10-30 元/月 | 低频存储，成本低 |

**结论：100-540 元/月** 是更现实的初期运行区间（含流量和备份）。

#### 具体场景估算：1000 用户 × 平均 500MB 存储（假设已迁移至 PostgreSQL；实际迁移时机见 §9.4，1000 用户时仅为评估节点，非强制迁移）

| 项目 | 计算 | 估算 |
|------|------|------|
| 对象存储容量 | 1000 × 500MB = 500GB | 约 75 元/月（0.15元/GB） |
| 同步流量（保守） | 每用户每天 10MB 下载 × 1000 × 30天 = 300GB | 约 90 元/月（0.3元/GB） |
| 服务器 | 4核8G，处理 1000 并发设备 | 约 200-400 元/月 |
| 数据库 | 迁移至 PostgreSQL 托管（Supabase Pro）（**注：此为假设已迁移场景**；实际迁移时机见 §9.4，1000 用户时视查询复杂度评估；若仍使用 Turso，此项成本约 0-50 元/月） | 约 150 元/月 |
| **合计** | | **约 515-715 元/月** |

**结论：1000 用户规模下，月运营成本约 500-800 元，单用户成本约 0.5-0.8 元/月，商业化定价需覆盖此成本。**

### 19.3 AI 成本策略

V1 建议：

1. BYOK 为主
2. 平台托管为后续商业化能力

原因：

1. 降低团队前期现金压力
2. 避免模型费用和风控压力过早压垮产品

---

## 二十、V1.1 迭代方向

V1 稳定后再考虑：

1. 本地 embedding
2. 混合检索
3. AI Gateway
4. 数据密钥轮换（`key_epoch` / 多 key 元数据 / 迁移协议）
5. WebDAV / Git 同步
6. 版本历史与回滚
7. 模板系统
8. 导出 PDF / HTML
9. 更完整的图谱
10. 知识库级批量整理工作流
11. 插件能力

---

## 二十一、最终建议

如果目标是做一个真正有竞争力的类 Obsidian 产品，这个项目的核心不在“编辑器页面做得像不像”，而在三件事：

1. **本地文件是不是主数据**
2. **多端同步是不是稳定且不丢数据**
3. **AI 是否能把原始资料持续编译成可复用的知识层**

所以最终执行建议是：

1. **立项通过**
2. **以桌面端为核心**
3. **以 manifest + CAS 为同步底座**
4. **以 `.ai/wiki/` 为 AI 知识中枢**
5. **以 SQLite 作为索引和状态层，而不是主存储**
6. **把“V1 上线”收敛为桌面端闭环，把移动端放到 V1.1**

一句话总结：

**这不是一个”加了聊天框的笔记软件”，而应该是一个”本地优先、同步可靠、AI 持续编译知识”的知识操作系统雏形。**

---

## 二十二、补充设计：AI 调用链路中的加解密数据流

### 22.1 问题背景

第十一节确立了”云端不保存内容明文、服务端不持有 `vault_key`”的原则，所有 blob 在上传前均已加密；但 manifest 元数据（含 `content_hash`）会由服务端持久化（见 §11.1 第4条、§11.2 第7条）。AI 功能需要读取 `.ai/wiki/` 页面和原始笔记内容，因此必须明确：**解密在哪一步发生，解密后的数据如何流转，是否会离开本地设备。**

### 22.2 核心原则

1. **解密只发生在本地客户端**，服务端和对象存储始终只接触加密 blob。
2. **AI 调用使用的是本地明文内容**，不是加密 blob。
3. **明文内容不经过自建服务端**，直接由客户端发往第三方模型 API（BYOK 模式）。
4. **`.ai/wiki/` 页面本身也参与加密同步**，但在本地文件系统中以明文形式存储，供 AI 层直接读取。

### 22.3 数据流向图

```text
本地文件系统（明文）
  ├── Notes/          ← 用户笔记，明文
  └── .ai/wiki/       ← AI 知识页，明文

         ↓ 读取（本地，无网络）
AI 知识编译与检索层（Rust Core）
  - 切分 chunk
  - 检索相关片段
  - 拼装上下文

         ↓ 发送（HTTPS，直连第三方模型 API）
模型 API（OpenAI / Claude / 其他）
  - 接收明文上下文
  - 返回生成结果

         ↓ 写回（本地，用户确认后）
本地文件系统（明文）

         ↓ 同步时加密（Rust Core）
加密 blob → 对象存储（云端，密文）
```

### 22.4 同步时的加密时机

`.ai/wiki/` 页面与普通笔记一样，在同步提交时由 Rust Core 加密后上传：

1. 本地写入 `.ai/wiki/` 时：**明文写入文件系统**
2. 同步引擎在生成 commit 内容快照时（见 §10.4 提交流程的"内容快照"步骤）：**读取明文 → 计算 `content_hash` → 派生 `nonce` → 用 `content_key` 加密 → 落临时密文 blob 文件 → 派生 `blob_id`**
3. 上传阶段：以 `blob_id` 为 key 询问远端是否已存在；不存在则上传该临时密文 blob 文件
4. 另一台设备拉取时：**下载密文 blob → 由 manifest 中的 `content_hash` 重新派生 `nonce` → 用 `content_key` 解密 → 写入本地文件系统（明文）**

派生规则统一遵循 §11.2 的"子密钥派生与确定性 nonce"小节，不在此重复。

### 22.5 AI 调用时的解密时机

AI 调用**不需要额外解密步骤**，因为本地文件系统中的内容始终是明文：

1. 用户触发 AI 操作（总结、问答、编译知识页）
2. Rust Core 直接从本地文件系统读取明文内容
3. 切分、检索、拼装上下文
4. 客户端直接调用模型 API（BYOK），发送明文上下文
5. 模型返回结果，客户端展示
6. 用户确认后，结果以明文写入本地文件系统

### 22.6 隐私边界说明

必须在产品中向用户明确告知：

1. AI 功能默认优先发送 `.ai/wiki/` 页面内容，而非整个知识库
2. 仅在 wiki 依据不足时，才发送必要的原始笔记片段
3. 发送内容直接到达用户配置的模型 API，不经过本产品服务端
4. API Key 存储在本地安全存储中，不参与同步，不上传服务端
5. 用户可随时关闭 AI 功能，不影响编辑与同步
6. 首次开启 AI 时，必须弹出显式同意说明，明确写明“选中的明文内容将发送到第三方模型提供方”
7. 每次调用前，UI 需展示当前作用范围，例如“当前笔记 / 文件夹 / 搜索结果 / 整个 Vault（仅批量编译）”
8. 对于“整个 Vault”级别调用，默认增加二次确认，并明确提示“本次将进入批量知识编译流程，而不是整库直接问答”
9. 产品文案中不得把“同步链路 E2EE”表述成“模型调用不出本地”或“零数据外发”
10. 若后续提供平台托管模型或企业模式，必须单独声明供应商保留策略、训练策略和数据驻留边界，不能沿用 BYOK 文案混写

---

## 二十三、补充设计：AGENTS.md 最小 Schema 定义

### 23.1 文件位置与用途

`.ai/AGENTS.md` 是 AI 知识编译层的控制文件，定义：

1. wiki 页面的字段规范（schema）
2. 页面命名规则
3. 来源引用格式
4. lint 检查规则
5. AI 编译时必须遵守的约束

该文件由开发者预置初始版本，用户可手工修订，AI 在编译知识页时必须读取并遵守。

### 23.2 最小 AGENTS.md 示例

`````markdown
# AGENTS.md — AI 知识库编译规范

## 1. 页面类型定义

支持以下页面类型（page_type）：

- `topic`：主题综述页，适合某个领域、方法论、技术方向
- `entity`：实体页，适合人物、公司、产品、项目、概念
- `timeline`：时间线页，适合事件序列、版本历史、项目进展
- `qa`：问答页，适合高频问题与标准答案
- `summary`：总结页，适合文件夹、项目阶段、资料集合的阶段性总结

## 2. 页面必填字段

每个 wiki 页面必须包含以下 frontmatter：

```yaml
---
title: <页面标题>
type: <topic|entity|timeline|qa|summary>
summary: <一句话摘要，不超过 100 字>
sources:
  - <来源笔记路径或外部资料标识>
related:
  - <相关 wiki 页面路径>
updated_at: <ISO 8601 时间，如 2026-04-29T12:00:00Z；此字段面向用户可读，使用 ISO 8601 格式，与协议层 UTC Unix 毫秒时间戳不同；另见 §12.9 `last_ai_compiled_at` 字段说明，两者语义不同>
---
```

## 3. 命名规则

- 文件名使用小写中划线格式，例如：`rust-ownership-model.md`
- 实体页以实体名称命名，例如：`andrej-karpathy.md`
- 时间线页以主题加 `-timeline` 结尾，例如：`project-alpha-timeline.md`
- 问答页以 `qa-` 开头，例如：`qa-sync-conflict-handling.md`

## 4. 来源引用规则

- 每个知识页至少引用一个来源
- 来源路径使用相对于 VaultRoot 的路径
- 外部资料使用 URL 或资料标识
- 禁止生成无来源的知识页

## 5. Lint 规则

AI 编译完成后，系统自动检查：

1. frontmatter 必填字段是否完整
2. `sources` 列表是否非空
3. `type` 是否为合法值
4. `updated_at` 是否为有效时间格式
5. 文件名是否符合命名规则

lint 失败的页面不写入 `.ai/wiki/`，记录到 `.ai/log.md`。

## 6. 禁止行为

AI 在编译知识页时禁止：

1. 生成无来源引用的结论
2. 静默覆盖用户手工修订过的知识页（需提示用户确认）
3. 修改 `Notes/` 目录下的原始笔记
4. 删除已有知识页（只能标记为 `deprecated`）

## 7. 索引规则（`.ai/index.md` 维护规范）

`.ai/index.md` 是知识页总索引，由 Rust Core 在每次 wiki 写入完成后自动维护，规则如下：

1. 索引格式：每个知识页一行，形如 `- [<title>](<相对路径>) — <type> — <summary 前 80 字>`
2. 排序：默认按 `type` 分组、组内按 `updated_at` 倒序
3. 失效项处理：若知识页 frontmatter 中 `deprecated: true`，索引中保留但加 `（已废弃）` 后缀，置于该 type 末尾
4. 实体页 / 时间线页可选附"近期更新"段，列出最近 30 天内 `updated_at` 变化的页面
5. 索引文件本身不写入审计字段（`updated_at` 等），其变化由同步引擎记录到 sync 日志，不写 `.ai/log.md`
6. 用户手工编辑 `.ai/index.md` 不被支持；下次 AI 编译完成会按当前 `.ai/wiki/` 状态全量重生成。**注意**：`user_edited` 标记机制（§12.9）仅适用于 `.ai/wiki/` 下的知识页，不适用于 `.ai/index.md`；UI 应在用户尝试编辑 `index.md` 时给出提示"此文件由系统自动维护，手工修改将在下次编译时被覆盖"
`````

### 23.3 lint 检查实现位置

lint 检查由 Rust Core 实现，在以下时机触发：

1. AI 编译完成后，写入 `.ai/wiki/` 前
2. 用户手工编辑 `.ai/wiki/` 页面并保存后
3. 用户主动触发"知识库健康检查"时

lint 结果写入 `.ai/log.md`，UI 在 AI 面板中展示警告数量。

---

## 二十四、补充设计：冲突副本命名规范与处理流程

### 24.1 命名格式

冲突副本统一采用以下格式：

```
<原文件名> (conflict <日期> <设备名>).<扩展名>
```

示例：

```
项目计划 (conflict 2026-04-29 MacBook-Pro).md
meeting-notes (conflict 2026-04-29 iPhone-15).md
logo (conflict 2026-04-29 Desktop-Win).png
```

规则说明：

1. 日期格式为 `YYYY-MM-DD`
2. 设备名取 `device_name` 字段（用户注册设备时填写）；`device_name` 限制为 ASCII 字母、数字、连字符和空格，最长 32 字符；处理顺序为：**① 先截断至 32 字符**，**② 再将非法特殊字符替换为连字符**，**③ 最后做平台文件名合法性校验**（若平台不允许文件名含空格，统一替换为连字符）；此顺序确保截断后末尾的特殊字符也能被正确替换
3. 冲突副本与原文件存放在**同一目录**
4. 若同一文件同一天产生多个冲突副本，追加序号：`(conflict 2026-04-29 设备名 2).md`；序号从 2 开始（第一个副本无序号），按"文件名 + 日期 + 设备名"组合在 SQLite 中持久化计数，App 重启后优先从 SQLite 读取当前最大序号继续递增；**SQLite 损坏或重建后，必须通过扫描文件系统中已存在的同名冲突副本文件反推当前最大序号，再从该值继续递增，不得从 0 重新开始**（最终命名仍以第 5 条文件系统占位校验为准）
5. **最终命名裁决必须以文件系统占位校验为准**：在真正落盘冲突副本前，Rust Core 必须检查目标路径是否已存在；若已存在，则继续递增序号直至找到空闲路径。SQLite 计数仅用于加速，不得作为唯一裁决来源；即使 SQLite 已损坏或被重建，也不得覆盖现有未解决冲突副本

### 24.2 冲突副本的产生时机

| 场景 | 处理方式 |
|------|----------|
| 文本文件，同一基线双方修改，自动 merge 成功 | 不产生副本，合并结果写回规范路径 |
| 文本文件，同一基线双方修改，merge 失败 | 保留当前远端 head 版本为原文件，落后客户端的待提交版本生成冲突副本 |
| 二进制文件（图片、PDF 等），双方均修改 | 保留当前远端 head 版本为原文件，落后客户端的待提交版本生成冲突副本 |
| 一方删除，另一方修改 | 删除状态保持为规范状态；修改版本生成冲突副本，UI 提示用户是否恢复 |
| 同一 `file_id` 双方重命名到不同路径（无内容修改） | 当前远端 head 路径为规范路径；落后客户端的路径变更写入同步日志，不产生冲突副本；**必须在 UI 中单独提示用户**"您的重命名操作与远端冲突，文件已恢复为远端路径 `<path>`"，不得静默处理 |
| 同一 `file_id` 双方重命名到不同路径（同时有内容修改） | 当前远端 head 路径为规范路径；若 merge 成功则合并内容写回规范路径；若 merge 失败则落后客户端版本生成冲突副本（使用规范路径名加冲突后缀） |
| 不同 `file_id` 竞争同一路径 | 当前目标 manifest 的路径为规范路径；落败方若为 dirty 则生成冲突副本，若为 clean 则搬离或删除；事件单独标记为“路径冲突” |

### 24.3 UI 提示策略

冲突副本产生后，客户端必须：

1. 在同步状态栏显示冲突数量角标
2. 在文件树中对冲突副本文件标注醒目图标
3. 在同步日志面板列出所有冲突事件，包含：
   - 冲突文件路径
   - 冲突来源设备名
   - 冲突发生时间
   - 冲突类型（merge 失败 / 二进制冲突 / 删除冲突 / 路径冲突）
4. 提供操作入口：
   - 保留本地版本（删除副本）
   - 保留远端版本（用副本覆盖原文件）
   - 打开对比视图（仅文本文件）

### 24.4 冲突副本的清理

冲突副本不自动跨端同步，也不进入远端 tombstone 生命周期；它只作为**本地待解决冲突产物**存在，由用户主动处理。

用户处理完成后，可在 UI 中标记为"已解决"，系统按以下规则收尾：

1. 若用户选择丢弃冲突副本：将副本移入回收站或直接删除（根据用户设置），并从 `filemap.json` 中移除对应 `status=conflict_copy` 条目
2. 若用户选择吸收冲突副本内容到主文件：在主文件保存成功后，同样删除该副本文件，并从 `filemap.json` 中移除对应条目
3. 冲突副本的删除只记录到同步日志，不生成远端 tombstone

---

## 二十五、补充设计：.ai/raw/ 同步策略与清理策略

### 25.1 目录内容定义

`.ai/raw/` 存放 AI ingest 流程的中间产物，包括：

1. 原始资料的来源映射文件（记录哪个笔记/附件被 ingest 过）
2. 附件抽取的纯文本（PDF、音频转写等）
3. 外部导入资料的本地快照

### 25.2 同步策略

`.ai/raw/` 目录**不参与常规跨端同步**，原因：

1. 内容体积不可控，可能远大于用户笔记本身
2. 属于可重新生成的中间产物，不是主数据
3. 同步中间产物会显著增加存储和流量成本

具体规则：

| 子目录/文件 | 是否同步 | 说明 |
|------------|----------|------|
| `.ai/wiki/` | 是 | AI 知识页是产出物，需要同步 |
| `.ai/index.md` | 是 | 知识页索引，需要同步 |
| `.ai/log.md` | 否 | 本地日志，不同步；亦不包含在完整 Vault 迁移包中（见 §7.2.3） |
| `.ai/raw/` | 否 | 中间产物，不同步 |
| `.ai/AGENTS.md` | 是 | 编译规范，需要同步保持一致 |

V1 **不允许**通过任何本地设置把 `.ai/raw/` 纳入常规跨端同步。用户可配置的只有：

1. `.ai/raw/` 本地体积上限
2. `.ai/raw/` 本地自动清理策略
3. 导出迁移包时是否附带 `.ai/raw/`

这些选项都只影响**本机缓存管理或单次导出行为**，不得改变 manifest、`filemap.json` 导出规则或 sync engine 的常规同步集合。

设计结论：

1. **跨端共享的一致 AI 层是 `.ai/wiki/`，不是 `.ai/raw/`**
2. `.ai/raw/` 被定义为**本地缓存 / 中间产物**，允许在不同设备上缺失或不一致；这种“不一致”是受设计允许的本地缓存差异，**不是**通过常规 sync 协议传播的状态差异
3. 当某设备缺少所需 raw 时，应优先尝试从本地 `Notes/`、附件和已同步的 `.ai/wiki/` **按需重建**
4. 若本机无法重建（例如缺少附件抽取能力或原始外部导入材料），AI 仍可基于 `.ai/wiki/` 与可见原文回答，但必须明确提示”部分 raw 依据当前设备不可用”
5. 产品层面不承诺”任何设备上的 AI 回答能力完全一致”，V1 只承诺：**共享知识页一致、原始笔记一致、raw 缓存尽力重建**

### 25.3 各 raw 类型的重建能力说明

不同类型的 raw 中间产物，重建能力差异显著，必须明确：

| raw 类型 | 来源 | 可重建？ | 重建方式 | 备注 |
|----------|------|----------|----------|------|
| Markdown 笔记抽取文本 | `Notes/` 目录 | **是** | 直接读取原始 `.md` 文件 | 无需额外能力 |
| 来源映射文件 | ingest 记录 | **是** | 重新扫描 `Notes/` 和 `Attachments/` 重建映射 | 需重新 ingest |
| PDF 抽取文本 | `Attachments/` 中的 PDF | **条件可** | 需本地 PDF 解析能力（桌面端支持，移动端 V1 不保证） | 移动端缺失时降级 |
| 音频转写文本 | `Attachments/` 中的音频 | **否** | 需调用转写 API，成本不可控，V1 不自动重建 | 提示用户手动重新 ingest |
| 外部导入资料快照 | 用户手动导入的外部文件 | **否** | 原始外部资料不在 Vault 内，无法自动恢复 | 提示用户重新导入 |

**降级策略：**

1. 桌面端：PDF 抽取能力内置，Markdown 和 PDF 类 raw 均可重建
2. 移动端：仅保证 Markdown 类 raw 可重建；PDF 和音频类 raw 缺失时，AI 功能基于 `.ai/wiki/` 降级运行，UI 显示”部分附件依据在本设备不可用”
3. 任何设备：音频转写和外部导入类 raw 缺失时，不自动重建，仅提示用户

### 25.4 体积控制

为防止 `.ai/raw/` 无限增长，系统必须：

1. 对单个抽取文本文件设置上限（建议 **2MB**）
2. 对整个 `.ai/raw/` 目录设置总量上限（建议 **500MB**，可配置）
3. 超出上限时，在 AI 面板显示警告，提示用户清理；**默认行为为仅警告，不拒绝写入**，避免阻断 AI 编译流程；若用户长期忽略警告导致超出**系统默认上限（500MB）的 2 倍（即 1GB）**，则暂停新的 ingest 任务并强制提示清理；若用户将上限配置为自定义值，强制暂停阈值取 `max(用户配置值 × 2, 100MB)`，确保用户配置的上限实际生效（而非被系统默认值覆盖）；绝对最小阈值不低于 100MB。**注意**：当用户配置值 < 50MB 时，强制暂停阈值由 100MB 绝对下限主导，实际暂停点可能超过用户配置值的 2 倍；建议 UI 在用户配置低于 50MB 时给出提示"强制暂停阈值将以 100MB 为准"。**典型配置决策表**：

| 用户配置上限 | 强制暂停阈值（`max(配置×2, 100MB)`） |
|-------------|--------------------------------------|
| 40MB | 100MB（由绝对下限主导） |
| 50MB | 100MB（临界值，两者相等） |
| 100MB | 200MB |
| 200MB | 400MB |
| 500MB（默认） | 1000MB（即 1GB） |

### 25.5 清理策略

提供两种清理方式：

**自动清理（可选开启）：**

1. 超过 N 天未被引用的 raw 文件自动删除（默认 30 天）
2. 对应 wiki 页面已删除的 raw 文件自动清理

**手动清理：**

1. 用户在设置页触发"清理 AI 中间产物"
2. 系统列出可清理文件及体积
3. 用户确认后删除

---

## 二十六、补充设计：移动端同步触发机制规格

### 26.1 前台同步触发条件

移动端前台运行时，同步触发规则如下：

| 触发条件 | 说明 |
|----------|------|
| 笔记保存后 | 用户完成编辑并保存，立即触发上传 |
| 进入 App 前台 | App 从后台切回前台时，触发一次拉取 |
| 手动点击同步按钮 | 用户主动触发完整同步 |
| 定时轮询（前台） | 前台运行时每 **60 秒**检查一次远端 head；遵循 §10.4 的 pull/commit 互斥调度约束：若当前有 commit 在飞（`commit_in_progress = true`）或 pull 应用进行中（`sync_apply_journal` 未清除），本次轮询跳过，等待下一个周期 |

### 26.2 后台同步策略

移动端后台同步采用 **best-effort** 策略，不承诺实时性：

**Android：**
1. 使用 `WorkManager` 注册周期性后台任务
2. 触发时机由系统调度决定，建议间隔设为 15 分钟
3. 仅在 Wi-Fi 且充电时执行（可配置）
4. 不保证后台任务一定执行

**iOS：**
1. 使用 `BGAppRefreshTask` 注册后台刷新
2. 触发时机完全由系统决定，无法保证频率
3. 每次后台任务时间限制约 30 秒，只做轻量拉取
4. 不承诺后台上传能力

### 26.3 同步延迟预期

| 场景 | 预期延迟 |
|------|----------|
| 前台编辑后保存 | < 5 秒（网络正常时） |
| App 切回前台 | < 10 秒 |
| 后台同步 | 不保证，最长可能数小时 |
| 手动触发 | 取决于变更量和网络 |

### 26.4 降级策略

当同步失败或网络不可用时：

1. 本地编辑**不受阻断**，继续正常使用
2. 同步状态栏显示"待同步"标记
3. 恢复网络后，App 进入前台时自动重试
4. 用户可随时手动触发同步

### 26.5 产品文案要求

移动端产品界面中，同步能力的描述必须使用以下口径：

- 正确：「前台实时同步，后台尽力同步」
- 正确：「离线编辑，联网后自动上传」
- 禁止：「实时同步」「后台自动同步」「永远保持最新」

---

## 审核链与修订记录

### v1.0.19 → v1.0.20（Codex 审核）

#### P0（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P0-1 | `submitted` 恢复命中成功后缺少最终 `last_manifest_summary` 的可实现数据来源，`intent_manifest_hash` 与最终 head manifest 摘要语义不同 | 在 §10.4 / §14.2 中明确：扫描命中某个 `matched_revision` 后，客户端必须额外调用 `GET /vaults/:vaultId/manifest/:matched_revision` 计算最终 `matched_manifest_summary`，禁止复用 `intent_manifest_hash` |

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | 命中历史 revision 时，恢复分支会把 `remote_head_revision` 错误回退到旧 revision | 在 §10.4 中区分 `matched_revision` 与 `observed_head_revision`：`last_applied_revision / acked_revision` 仅推进到命中 revision，`remote_head_revision` 保留最新 head；若 head 更高则恢复后立即继续 pull |
| P1-2 | `.noteapp/staging/` 仍存在“独立回放”和“journal 子步骤”两套冲突恢复协议 | 在 §10.4 拉取应用流程与崩溃恢复中统一为 journal-first：仅在存在 `sync_apply_journal` 时允许回放 staging；无 journal 时转入 `staging-orphans` / 完整拉取 / 人工修复 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | 文档缺少审核链与版本一致性记录 | 新增本节，补全 `v1.0.19 → v1.0.20` 审核闭环 |

### v1.0.20 → v1.0.21（Claude 审核）

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | submitted 确认流程 steps 2–3 扫描上界使用 `head_revision` 而非 step 1 定义的 `observed_head_revision`，命名不一致 | steps 2–3 统一改为 `to=observed_head_revision` |
| P2-2 | `GET /head` 响应规格分散：item 5 只提 revision，item 14 才补充 `commit_intent_id` 等字段 | item 5 补全响应字段列表并标注「完整规格见第 14 条」 |
| P2-3 | submitted 恢复 step 4 依赖 `GET /manifest/:matched_revision`，但服务端保留期未定义，404 时无 fallback | §14.2 item 7 补入 30 天最短保留期；§10.4 step 4 补充 404 fallback 分支（标记 last_manifest_summary 过期 → 强制 pull） |

#### P3（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P3-1 | test 31 提及 `intent_manifest_hash` 用于识别，与 test 43 的「不得用于摘要」并列易混淆 | test 31 添加括注，明确「此处用于匹配识别，非用于 last_manifest_summary 计算，见 test 43」 |

### v1.0.21 → v1.0.22（Codex 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | submitted 404 fallback 虽然要求“pull 完成前禁止新 commit”，但缺少可持久化的 commit gate，重启后可能失效 | 将 `last_manifest_summary = null / stale sentinel` 正式定义为持久化同步门闩，并在 §10.4 Q-fm 临界区 A 显式检查，未刷新前拒绝新 commit |
| P1-2 | 文档允许 `last_manifest_summary` 进入 null/stale 状态，但未定义 pull / diff 在该状态下的执行语义 | 在 §8.2 字段定义与 §10.4 拉取流程中明确：stale/null 摘要下禁止走摘要快捷路径，必须下载最新 manifest 做完整收敛；成功 pull 后才重写有效摘要 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | 新增的 submitted 404 fallback 分支缺少对应验收用例 | 在 §17.2 新增 test 47-48，覆盖 stale/null 摘要 gate、完整 pull 解锁 commit、禁用摘要快捷路径 |

### v1.0.22 → v1.0.23（Claude 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | `last_manifest_summary` 未定义初始值；新 vault 或迁移包导入后该字段自然为 null，Q-fm A 的 null 检查永久阻断首次 commit | §8.2 字段定义补入初始值规则：新建 / 导入时必须初始化为空 manifest 的稳定哈希值，禁止初始化为 null；null 仅允许由 submitted 404 fallback 显式写入 |
| P1-2 | Q-fm A 的 null/stale 检查对「从未同步的初始态」缺乏豁免，与 P1-1 构成双重兜底 | Q-fm A 补充豁免：`last_applied_revision = 0` 且无活跃 journal 时（合法初始态）豁免 null/stale 检查，允许首次 commit |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | 缺少验证新 vault 首次 commit 与迁移包导入后首次 commit 行为的测试用例 | §17.2 新增 test 49–50，覆盖上述两条路径 |

### v1.0.23 → v1.0.24（Codex 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | `last_manifest_summary` 初始值规则错误引用了 Q-fm 临界区 B 的 `intent_manifest_hash` 语义，导致 intent 摘要与 final head 摘要重新混用 | §8.2 改为显式使用“空 vault 的 final head manifest 摘要常量”，并明确不得借用 `intent_manifest_hash` 的 omit-`revision` 规则 |
| P1-2 | 完整迁移包导入后缺少可信同步基线，却仍把“首次 commit 不受阻断”定义为合法路径 | §7.2.3 / §8.2 / §10.4 改为：迁移包导入后进入“待重建基线”状态，必须先做一次 pull/reconcile，完成同步状态重建后才允许 commit |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | 验收用例把“迁移包导入后首次 commit 允许”写成了通过条件，与迁移包边界定义不一致 | §17.2 test 50 改为验证“先 pull/reconcile，后 commit”的正确路径 |

### v1.0.24 → v1.0.25（Claude 审核）

#### P3（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P3-1 | §8.2 空 vault 常量的序列化算法未定义，跨实现可能产生不同常量值 | 补充说明：常量应按与服务端一致的 manifest 序列化算法计算并内置为编译期常量；若无法保证对齐，可用任意实现定义的稳定非 null 字节串，唯一要求是不为 null/stale 且不等于任何真实的 head manifest 摘要 |

### v1.0.25 → v1.0.26（Codex 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | §8.2 仍允许“任意实现定义稳定字节串 / 固定魔数”替代空 vault 常量，跨客户端实现可能再次分叉 | 收紧为单一、版本化、可复现的 final head manifest 摘要常量；同版本客户端输出必须一致，禁止任意魔数兜底 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | test 49 只验证首次 commit 允许，未验证空 vault 常量在同版本实现间的一致性 | test 49 增补“同版本客户端输出完全一致”的验收要求 |

### v1.0.26 → v1.0.27（Codex 审核）
#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | §10.4 中 Q-fm 临界区 A 只锁定了 `pending_local_changes`，但临界区 B 仍按“当前 `filemap.json`”导出 manifest；若用户在 A→B 之间执行新建 / 重命名 / 删除，当前 commit 会把结构快照与内容快照混成两个时刻的数据 | 为 `commit_in_progress` 与 Q-fm 增加“结构冻结窗口”语义：A→C 期间任何改写 `filemap.json` 的本地结构操作都必须排队到当前 commit 完成之后；临界区 B 明确改为基于“临界区 A 锁定的 `filemap.json` 结构视图”导出 manifest |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | 文档只校验了 `last_manifest_summary` 相关恢复与首次 commit 路径，未验证“commit 进行中插入结构操作 / 内容编辑”时的边界行为 | 在 §17.2 新增 test 51–52，分别覆盖结构操作排队不污染当前 manifest、内容编辑只进入后续 commit |

### v1.0.27 → v1.0.28（Claude 审核）

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | Q-fm 第 3 条「编辑器保存线程」约束上界写成”临界区 B 之前”，字面上暗示 B→C 窗口可插入结构写入 | 改为”commit 完成前（`commit_in_progress` 被临界区 C 清除之前）”，并显式注明包含 B→C 窗口期 |
| P2-2 | 结构冻结的适用边界未覆盖 pull/commit 并发场景，pull 步骤 11/12 与 commit Q-fm C 的状态覆盖竞争未显式排除 | 在”串行队列与内容快照模型”段新增「pull 与 commit 互斥调度约束」，明确 sync engine 层面不允许并发触发，并说明原因和与 pre-check E 的关系 |

### v1.0.28 → v1.0.29（Codex 审核）

#### P0（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P0-1 | §10.4 只冻结了结构视图，没有把一次 commit 的内容读取显式约束为同一份可验证的冻结快照；W-commit 若回读 live 文件，可能把同一 revision 的 `manifest` / `content_hash` / blob 内容混成两个时刻 | 在 §10.4 为 commit 增加 `content_snapshot_plan`、冻结源版本校验和快照漂移中止规则；W-commit 只允许消费冻结快照或 `.snapshot.plain`，一旦发现源版本漂移必须整轮 abort 并重扫 |
| P0-2 | §7.2.2 / §7.2.3 / §25.2 之前仍给本地设置留下“把 `.ai/raw/` 纳入常规同步”的解释空间，导致设备间 manifest / sync set 可能分叉 | 在 §7.2.2 / §7.2.3 / §25.2 统一收紧边界：`.ai/raw/` 在 V1 中始终不参与常规跨端同步；本地设置只允许管理体积、清理和导出附带，不得改变 manifest、`filemap.json` 导出规则或 sync set |

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | `local_tombstone_ledger` 仅依赖 SQLite，若 SQLite 损坏且 `filemap.json` 又需要 tombstone 元数据重建，则缺少第二恢复来源 | 在 §7 / §8.2 引入 `VaultRoot/.noteapp/tombstone-ledger.jsonl` 作为本地 append-only 镜像，并明确恢复优先级：SQLite 优先，SQLite 损坏时先用镜像重建，二者同时不可用才进入人工修复模式并阻止新 commit |
| P1-2 | 设备移除仅描述了 tombstone 判定集合变化，未把“认证失效 / API 拒绝”写成协议硬约束；被移除设备理论上仍可能继续调用 vault 级接口 | 在 §14.2 明确 `DELETE /devices/:deviceId` 为账号级设备注销：服务端必须立即吊销该设备 token / session，并对其后续 vault 级 API 访问返回 `401/403`，同时将其移出 tombstone 回收活跃设备集合 |

### v1.0.29 → v1.0.30（Claude 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | §10.4 临界区 C 描述”清空 `pending_ack_to_server`”，与 §8.2 字段定义”commit 路径不写入该字段”直接矛盾；若 commit 前已有待上报的拉取 revision，清空会导致其永久丢失，影响 tombstone 回收判定 | 临界区 C 改为”不修改 `pending_ack_to_server`”，并补充说明原因 |
| P1-2 | §10.4 submitted 结果确认流程 step 4 中，tombstone ledger 回写与 journal 清除未要求原子性；若清除先于回写完成时崩溃，重启后 `commit_intent_journal.created_at` 丢失，无法再筛选待回写条目 | step 4 明确上述状态更新必须在同一 SQLite 事务中原子完成，事务提交后再删除临时文件 |
| P1-3 | §12.9 写回判断链第 5 条触发重新编译后，新任务若同时命中第 2 条（页面已变化），文档未说明 UI 应如何呈现，可能导致实现分歧（两次独立弹窗 vs 合并提示） | 第 5 条补充：新任务命中第 2 条时，UI 应展示”来源已更新且页面已变化”的合并提示，不得拆成独立弹窗 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | §10.4 拉取流程第 13 步写入 `pending_ack_to_server` 未说明幂等性；崩溃重启后重新执行本步骤可能重复写入同一 revision | 第 13 步补充”写入必须幂等”说明，可用 set 或 upsert 语义实现 |
| P2-2 | §24.1 冲突副本命名第 4 条：SQLite 损坏重建后计数从 0 重新开始，可能与文件系统已有副本序号冲突，虽最终由文件系统占位校验兜底，但未说明重建初始化规则 | 第 4 条补充：SQLite 重建后须扫描文件系统反推当前最大序号，再从该值继续递增 |
| P2-3 | §8.2 `local_tombstone_ledger` 恢复优先级 / test 54：未说明 `tombstone-ledger.jsonl` 末尾存在不完整行（崩溃时写入中断）时的处理，可能因单行损坏导致整个账本不可用 | §8.2 恢复优先级与 test 54 均补充：末尾不完整行必须跳过继续解析，不得因单行损坏导致整个账本不可用 |

### v1.0.30 → v1.0.31（Codex 审核）

#### P0（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P0-1 | 拉取流程要求“下载 blob、解密并落盘”，但 API 只定义了 `blobs/check` / `upload-init`，没有 blob 下载协议；同时 `blob_id` 又被定义为不作为公开对象路径暴露，导致 pull 路径不可实现 | 在 §10.4 / §14.2 补齐 `POST /vaults/:vaultId/blobs/download-init`（或等价受控下载协议）并补充约束：客户端必须先获取短时下载能力，再获取密文 blob，不得把 `blob_id` 当作静态对象路径 |

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | `wiki_tasks` 的“旧任务置为 `superseded` → 新任务创建”是两个可中断动作；若中间崩溃，会出现旧任务终态化但替代任务未落库，导致重编译请求丢失 | 在 §8.2 / §12.9 收紧为同一 SQLite 事务内原子完成，并补充 AI 验收用例验证崩溃恢复后不会丢任务 |
| P1-2 | `tombstone-ledger.jsonl` “完全无法解析出有效行即损坏”的规则误伤合法空账本；没有 tombstone 的 Vault 会被错误打入人工修复 | 在 §8.2 / §17.2 test 54 区分“合法空 ledger”与“非空畸形镜像”：空文件、纯空白或仅尾部截断但无其他脏内容时视为合法空账本 |
| P1-3 | 新 tombstone 的 `deleted_revision` 同时允许 `null` 与“等价零值”，但 `intent_manifest_hash` 的 omit 规则与服务端校验只对 `null` 闭合，存在跨实现摘要分叉风险 | 在 §10.3 / §10.4 / §14.2 统一协议为 `null` 唯一合法占位值，并在同步测试中补充对 `0` / 空串等错误值的拒绝校验 |

### v1.0.31 → v1.0.32（Claude 审核）

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | §10.4 submitted step 4 中 `pending_ack_to_server` 被”清空”，与 v1.0.30 已修复的临界区 C 语义（”不修改”）再次不一致；submitted 命中路径等同于”确认上次提交已成功”，服务端已在 commit 同一事务内更新 `acked_revision`，无需上报 | step 4 改为”不修改 `pending_ack_to_server`”，并补充原因说明 |
| P2-2 | §14.2 第 5 条 `download-init` 响应字段名为 `ciphertext_size`，与 §11.2 定义的 `encrypted_size` 不一致，实现时可能产生歧义 | 统一为 `encrypted_size`，并标注与 §11.2 的对应关系 |

#### P3（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P3-1 | §6.2 / §14.1 最小职责中”blob 上传下载授权”合并为一条，与 API 列表中上传（`upload-init`）和下载（`download-init`）分列不对齐 | 拆分为”blob 上传授权”和”blob 下载授权”两条 |
| P3-2 | §10.4 拉取流程第 7 步描述”或等价的受控下载协议”，边界不清晰 | 删除该歧义描述，直接引用 `POST /vaults/:vaultId/blobs/download-init` |

### v1.0.32 → v1.0.33（Codex 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | `download-init` 只定义了签发下载凭证，没有约束“已签发但尚未使用的 blob 下载凭证”在设备移除 / session 吊销后如何失效；会留下“新 API 已拒绝，但旧 URL 仍可拉取 blob”的权限缝隙 | 在 §14.2 把 blob 下载凭证收紧为“设备 / session 绑定、可即时撤销”的受控下载入口，并要求设备移除时同步使已签发凭证失效；§17.2 新增对应回归用例 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | §14.1 最小职责列表编号重复，`7.` 出现两次 | 顺延后半段编号，恢复为连续编号 |
| P2-2 | 本轮修正了 submitted 命中恢复路径“不修改 `pending_ack_to_server`”的语义，但缺少回归用例防止后续再次写回“清空” | 在 §17.2 新增 test 58，验证已有待上报 ack 在 submitted 命中恢复后保持不变 |
| P2-3 | 新增 `download-init` 后，没有验证“已签发旧凭证在设备移除时立即失效”的边界行为 | 在 §17.2 新增 test 59，覆盖旧下载凭证撤销语义 |

### v1.0.33 → v1.0.34（Claude 审核）

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | §6.2 云端层职责列表编号重复（`6.` 出现两次） | 第二个 `6.` 改为 `7.`，后续顺延 |
| P2-2 | §8.2 `deleted_at` 筛选语义需跨字段查找 `commit_intent_journal.created_at` 的精确定义，实现者容易遗漏 | 内联说明”即 Q-fm 临界区 A 持久化 intent 的时刻，见 §8.2 `commit_intent_journal` 字段定义” |
| P2-3 | §10.4 拉取流程第 12 步 tombstone 回收判定条件”已推进过”语义模糊 | 改为 `target_revision >= tombstone.deleted_revision` |
| P2-4 | §12.9 写回判断链”本地 AI 写回不修改 `last_synced_revision`”嵌在第 6 条末尾，易被误认为仅适用于第 6 条 | 提升为独立全局规则段落 |
| P2-5 | §17.2 test 58 验收条件”保持不变”缺乏可执行断言 | 补充：恢复前后 `pending_ack_to_server` 集合一致；`/ack` 重试能成功上报这些 revision |

#### P3（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P3-1 | §10.4 步骤 7”同事务”同时指服务端和客户端事务，可读性差 | 服务端处改为”在同一服务端数据库事务内” |
| P3-2 | §11.2 第 3/4 条”无额外头部”与 `encrypted_size` 公式分两条，存在维护分叉风险 | 合并为一条 |
| P3-3 | §14.2 第 16 条末尾重复了 §10.4 step 4 的约束 | 改为引用 §10.4 step 4，删除重复正文 |
| P3-4 | §24.1 冲突副本命名第 2 条 `device_name` 截断与特殊字符替换的执行顺序未明确 | 明确顺序：先截断→再替换特殊字符→最后平台校验 |

### v1.0.34 → v1.0.35（Claude 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | §8.2 `sync_apply_journal` 状态机 `finalizing` 阶段崩溃恢复动作未明确 `pending_ack_to_server` 的幂等补记语义，与拉取流程第13步不对称 | 补充”幂等，用 set/upsert 语义” |
| P1-2 | §10.4 临界区 C 的5项原子操作未明确执行顺序，tombstone ledger 回写（第2项）与 journal 清除（第4项）的先后关系不明，实现者若拆成两个事务会导致 tombstone 永久停留 null | 明确标注第2项必须在第4项之前执行，且均在同一事务内 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | §10.4 submitted step 4 manifest 404 fallback 中”按上述规则收敛”未明确 tombstone ledger 回写在 manifest 不可用时是否可执行 | 补充说明：tombstone ledger 回写不受 manifest 不可用影响，规则与正常命中路径相同 |
| P2-2 | §8.2 `sync_apply_journal` 状态机 `filemap_rewrite` 阶段崩溃恢复未说明 `last_applied_revision` 已在 `materializing` 阶段写入，实现者可能重复更新 | 补充说明：本阶段恢复无需重复更新 `last_applied_revision` |
| P2-3 | §12.9 写回判断链第3条未说明”内容哈希未变但 `last_synced_revision` 推进”仍触发的设计意图 | 补充保守设计说明 |
| P2-4 | §17.2 test 47 未验证 404 fallback 中 tombstone ledger 回写是否正确执行 | 增补断言 |
| P2-5 | §6.2 云端层职责列表与 §14.1 最小职责条目数量不对称，未说明是否有意省略 | 末尾加注”详细职责见 §14.1” |

#### P3（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P3-1 | §10.4 W-commit 快照阶段”超过阈值”未定义具体值 | 补充推荐阈值（单文件 > 10MB 时落盘） |
| P3-2 | §11.2 第5条”频度分析”措辞歧义 | 改为”重复内容推断” |
| P3-3 | §23.2 AGENTS.md 第7节未明确 `user_edited` 机制不适用于 `index.md` | 补充说明及 UI 提示要求 |
| P3-4 | §25.4 体积控制：用户配置 < 50MB 时强制暂停阈值由 100MB 主导，可能让用户困惑 | 补充说明并建议 UI 提示 |
| P3-5 | §14.2 第5条未说明 download-init 凭证过期时的重试策略 | 补充：凭证过期时重新调用 download-init 获取新凭证后重试 |

### v1.0.35 → v1.0.36（Codex 审核）

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | 页首“本次更新（v1.0.34 → v1.0.35）”摘要误混入上一版已完成的 3 条旧修订说明，导致与本轮审校链和版本一致性记录不一致 | 删除页首重复旧条目，仅保留 `v1.0.35` 这一轮实际改动摘要 |

### v1.0.36 → v1.0.37（Claude 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | §10.4 临界区 C”以上五项”计数与实际操作数量不符，事务外文件清理未明确标注，实现者可能误以为文件清理在事务内 | 改为”以上五项 SQLite 操作”，并在事务外文件清理前标注”（事务外，第6步）” |
| P1-2 | §8.2 `commit_intent_journal` 第3条未说明恢复路径中将 `acknowledged` 规范化为 `submitted` 是否合法，实现者可能误以为恢复路径也不应写入任何状态 | 补充说明：恢复路径中允许将 `acknowledged` 规范化为 `submitted`，不属于禁止范围 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | §10.4 第12步 tombstone 回收判定未说明 `deleted_revision = null` 的条目如何处理，null 比较语义因语言而异 | 补充说明：`deleted_revision = null` 的条目不参与本步骤判定，跳过即可 |
| P2-2 | §8.2 `pending_ack_to_server` “可用单值字段或队列实现”与 test 58 要求”集合一致”存在语义冲突 | 明确推荐集合/队列实现，并说明单值实现下 test 58 的等价断言 |
| P2-3 | §12.9 `task_base_revision = 0` 语义说明未说明 `last_synced_revision = 0` 是否合法 | 补充说明：0 不是合法的已同步 revision 值，若出现则视为数据异常按 null 处理 |
| P2-4 | §17.2 test 47 未验证 404 fallback 后完整 pull 成功时 commit gate 被解除 | 增补断言，并与 test 48 互相引用 |
| P2-5 | §8.2 `sync_apply_journal` 状态机 `staging` 阶段崩溃恢复第(2)条：用旧 `filemap.json` 反查 staging 文件目标路径可能不准确 | 补充说明：反查仅用于确认 `file_id` 是否已知，不用于确定最终落盘路径 |

#### P3（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P3-1 | §10.2 第9条 `acked_revision` 定义孤立，需交叉查阅才能确认服务端/客户端语义 | 加注交叉引用 |
| P3-2 | §23.2 AGENTS.md `updated_at` 注释未引用 §12.9 `last_ai_compiled_at` 的区别说明 | 加注引用 |
| P3-3 | §26.1 定时轮询未说明是否遵循 §10.4 pull/commit 互斥约束 | 补充说明：有 commit 在飞或 pull 进行中时跳过本次轮询 |

### v1.0.37 → v1.0.38（Claude 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | §10.4 临界区 C 未说明 `commit_intent_journal.created_at` 必须在事务开始前读入局部变量，实现者若在事务内先清除 journal 再回写 tombstone，`created_at` 已不可读 | 在临界区 C 开头补充”进入事务前必须先将 `created_at` 读入局部变量” |
| P1-2 | §10.4 submitted step 4 manifest 404 fallback 未将 `matched_revision` 写入 `pending_ack_to_server`，导致该 revision 的 ack 永久丢失，影响 tombstone 回收判定 | **历史记录说明**：该判断已在 `v1.0.38 → v1.0.39` 审核中被回滚；当前协议以“不修改 `pending_ack_to_server`”为准 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | §8.2 `commit_in_progress` 结构冻结语义操作列表未包含”tombstone 回收触发的 `filemap.json` 条目移除” | 补充该操作到列表中 |
| P2-2 | §10.4 拉取流程第3步未说明 `last_manifest_summary` 为 null/stale 时即使 revision 未变也必须完整下载 | 补充说明 |
| P2-3 | §8.2 `materializing` 阶段未明确 SQLite 更新与 journal phase 推进必须在同一 SQLite 事务原子完成 | 明确原子性要求 |
| P2-4 | §12.9 写回判断链第2条”无论原因”未说明是有意的保守设计 | 补充保守设计说明 |
| P2-5 | §17.2 test 39 未引用 submitted 命中路径的等价断言（test 47） | test 39 末尾加注引用 |

#### P3（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P3-1 | §10.4 W-commit 快照阶段落盘阈值未说明上界，移动端可能设置过高 | 补充”调整仅限降低阈值，不得高于 10MB；移动端建议不超过 4MB” |
| P3-2 | §11.2 第4条”本地安全存储”未定义各平台具体实现要求 | 补充各平台推荐机制，明确不得明文存储 |
| P3-3 | §14.2 第6条 ack 接口”加入该 vault”副作用未交叉引用 §10.6 第4条 | 加注引用 |
| P3-4 | §25.4 体积控制强制暂停阈值逻辑复杂，未提供决策表 | 补充典型配置决策表 |

### v1.0.38 → v1.0.39（Codex 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | §10.4 submitted step 4 manifest 404 fallback 把 `matched_revision` 写入 `pending_ack_to_server`，与 §8.2 字段定义、§10.4 临界区 C 以及 §17.2 test 58 的“保持不变”语义冲突 | 删除 fallback 中该写入，明确 submitted 命中恢复路径不修改 `pending_ack_to_server` |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | 页首“本次更新（v1.0.37 → v1.0.38）”混入上一轮条目，导致与本轮审核链和版本一致性记录不一致 | 清理页首摘要，仅保留本轮实际修订项 |

### 版本一致性

- 标题版本：`v1.0.38` → `v1.0.39`
- 本次更新说明：改为“基于 v1.0.38 → v1.0.39 的 Codex 审核”
- 协议正文：§10.4 submitted step 4 manifest 404 fallback 删除 `pending_ack_to_server` 写入，恢复与 §8.2 字段定义、§10.4 临界区 C 以及 §17.2 test 58 的一致语义；页首“本次更新”摘要清理为仅包含本轮两项修订
- 审核链：已补入 `v1.0.38 → v1.0.39`

### v1.0.39 → v1.0.40（Codex 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | §8.2 `last_manifest_summary` 的字段定义一边写“仅 submitted 404 fallback 可置 `null`”，另一边又把“完整迁移包导入后的待重建基线”列为第二个合法来源，形成实现歧义 | 合并为单一定义，明确合法来源仅有两类：submitted 404 fallback 与完整迁移包导入后的待重建基线 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | 审核链对 `pending_ack_to_server` 的历史结论前后相反，但旧条目未标注已被回滚，阅读时容易误判当前协议结论 | 保留原历史记录，并在 `v1.0.37 → v1.0.38` 的对应条目中显式标注该判断已在 `v1.0.39` 被回滚 |

### 版本一致性

- 标题版本：`v1.0.39` → `v1.0.40`
- 本次更新说明：改为“基于 v1.0.39 → v1.0.40 的 Codex 审核”
- 协议正文：§8.2 `last_manifest_summary` 字段定义统一 `null / stale sentinel` 的合法来源，只保留“submitted 404 fallback”与“完整迁移包导入后的待重建基线”两类来源
- 审核链：已补入 `v1.0.39 → v1.0.40`，并为 `v1.0.37 → v1.0.38` 中已被回滚的 `pending_ack_to_server` 结论补加历史说明

### v1.0.40 → v1.0.41（Claude 审核）

#### P0（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P0-1 | §8.2 `commit_intent_journal` 对 `submitted` / `acknowledged` 的历史语义说明不完整，旧状态机与当前状态机边界混杂，易导致恢复实现误判 | 补充历史状态机对比表，明确 `v1.0.17` 前后的状态转移差异，并把 `acknowledged` 收紧为仅历史兼容状态 |
| P0-2 | tombstone 回写若仅依赖 `deleted_at` 时间戳，遇到时钟回拨会出现本应回写的本地删除永远卡在 `deleted_revision = null` 的风险 | 在 §8.2 引入 `vault_state.local_delete_sequence` 与 `commit_intent_journal.intent_delete_seq_upper_bound`，把当前版本回写判定统一改为单调序列号 |

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | §10.4 拉取流程第 12 步 tombstone 回收判定未显式排除 `deleted_revision = null`，实现者可能把未落盘的新 tombstone 错当成可回收对象 | 在拉取流程第 12 步补充显式判定 `tombstone.deleted_revision != null` |

### 版本一致性

- 标题版本：`v1.0.40` → `v1.0.41`
- 本次更新说明：改为“基于 v1.0.40 → v1.0.41 的 Claude 审核”
- 协议正文：补入 `commit_intent_journal` 历史状态机对比表；新增 `local_delete_sequence` / `intent_delete_seq_upper_bound` 删除序列号机制；拉取流程第 12 步增加 `tombstone.deleted_revision != null` 显式检查
- 审核链：已补入 `v1.0.40 → v1.0.41`

### v1.0.41 → v1.0.42（Codex 审核）

#### P0（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P0-1 | `v1.0.41` 新增 `intent_delete_seq_upper_bound` 后，`submitted/acknowledged` 历史兼容恢复路径仍直接硬依赖该字段；若命中升级前遗留 journal，tombstone 回写将不可执行 | 在 §8.2 / §10.4 明确：历史 journal 缺字段时，恢复路径退回 `deleted_at <= commit_intent_journal.created_at` 的 legacy time-based fallback；当前版本 journal 继续使用序列号规则 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | 页首已更新到 `v1.0.41`，但底部缺少 `v1.0.40 → v1.0.41` 的审核链与版本一致性记录，文档闭环不完整 | 补入 `v1.0.40 → v1.0.41` 的 Claude 审核与对应版本一致性记录 |
| P2-2 | 本轮修订若只改正文不补审校记录，`v1.0.42` 会再次出现版本号已更新但审核链缺失的问题 | 追加 `v1.0.41 → v1.0.42` 的 Codex 审核记录与版本一致性条目，并补充 test 60 固化兼容恢复行为 |

### 版本一致性

- 标题版本：`v1.0.41` → `v1.0.42`
- 本次更新说明：改为“基于 v1.0.41 → v1.0.42 的 Codex 审核”
- 协议正文：为历史 `submitted/acknowledged` journal 缺失 `intent_delete_seq_upper_bound` 的场景补入 legacy time-based tombstone 回写 fallback，并新增 test 60
- 审核链：已补入 `v1.0.40 → v1.0.41` 与 `v1.0.41 → v1.0.42`

### v1.0.42 → v1.0.43（Codex 审核）

#### P1（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P1-1 | 仅修复“旧 journal 缺 `intent_delete_seq_upper_bound`”仍不够；若升级前遗留的 `local_tombstone_ledger` 条目缺失 `local_delete_seq`，升级后的首个新 commit 仍无法按当前版本规则回写这些 tombstone | 在 §8.2 / §10.4 增加启动期“升级补号规则”：先为历史缺失条目补写 `local_delete_seq` 并推进 `vault_state.local_delete_sequence`，完成后才允许进入新的 commit / pull / journal 恢复 |

#### P2（全部修复）

| # | 问题 | 修复 |
|---|------|------|
| P2-1 | test 47 仍把 submitted 404 fallback 写成统一使用 `local_delete_seq <= intent_delete_seq_upper_bound`，与 legacy fallback 语义并列后容易误读为“所有恢复路径都必须用序列号判定” | 将 test 47 明确限定为“`intent_delete_seq_upper_bound` 存在”的当前版本路径，并把历史缺字段场景显式指向 test 60 |
| P2-2 | 新增的升级补号规则缺少验收闭环，后续容易回归为“只修旧 journal、不修旧 ledger” | 在 §17.2 新增 test 61，验证旧 ledger 补号完成后，升级后的首个新 commit 仍能正确回写升级前遗留 tombstone |

### 版本一致性

- 标题版本：`v1.0.42` → `v1.0.43`
- 本次更新说明：改为“基于 v1.0.42 → v1.0.43 的 Codex 审核”
- 协议正文：补入旧 `local_tombstone_ledger` 缺失 `local_delete_seq` 的启动期升级补号规则；提交前恢复流程显式要求先完成补号；test 47 收紧为当前版本路径，并新增 test 61
- 审核链：已补入 `v1.0.42 → v1.0.43`

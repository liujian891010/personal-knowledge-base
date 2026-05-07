# 多 Agent 任务分配表 v1

## 1. 使用方式

本表用于把任务直接分配给多个 Agent 执行。每个 Agent 必须只在自己的目录归属范围内工作，禁止越权修改共享契约。

建议分配顺序：

1. 先分配 AG00、AG01
2. 再并行分配 AG02、AG03、AG04
3. 然后分配 AG05、AG06、AG07
4. 最后分配 AG08、AG09

---

## 2. 任务分配总表

| Agent | 任务名 | 优先级 | 前置依赖 | 目录归属 | 核心交付物 | 单独验收标准 |
|------|------|------|------|------|------|------|
| AG00 | 总控 / 架构集成 | P0 | 无 | 根目录、`docs/` | 仓库结构、规则文档、集成节奏 | 所有模块 owner 清晰，目录结构与规范一致 |
| AG01 | 协议与共享模型 | P0 | AG00 | `packages/protocol/` | schema、DTO、OpenAPI、fixtures | manifest/filemap/vault_state/AI schema 冻结且可复用 |
| AG02 | 本地存储 Core | P0 | AG01 | `packages/vault-core/` | Vault 初始化、filemap、ledger、恢复逻辑 | 新建/改名/删除/恢复正确，`filemap.json` 唯一真相成立 |
| AG03 | SQLite 索引与搜索 | P0 | AG01 | `packages/vault-core/` | SQLite schema、migration、FTS、wiki_tasks | 索引可重建，搜索结果与文件系统一致 |
| AG04 | 同步服务端 | P0 | AG01 | `apps/backend/noteapp-server/` | 设备管理、head、blob 授权、CAS commit | 并发提交受控，接口契约一致，可按 Python 规范部署 |
| AG05 | 同步客户端 | P1 | AG01、AG02、AG03、AG04 | `packages/vault-core/` 或 `packages/sync-core/` | push/pull/reconcile、journal、冲突处理 | 双端同步、离线恢复、崩溃恢复、幂等重试成立 |
| AG06 | AI 编译与问答后端 | P1 | AG01、AG03、AG04 | `packages/ai-core/`、后端 AI 模块 | ingest、compile、ask、引用回答 | 问答优先 wiki，编译可幂等，降级提示明确 |
| AG07 | Web 前端 | P1 | AG01、AG05、AG06 | `apps/frontend/noteapp-web/` | 文件树、编辑器、同步栏、AI 面板 | 主链路可跑通，部署方式符合 static 规范 |
| AG08 | Desktop / Mobile 客户端 | P2 | AG01、AG02、AG05、AG06 | `clients/desktop/`、`clients/mobile/` | 桌面端壳、移动端适配、导入导出 | 桌面端本地优先成立，移动端具备前台同步和 AI 降级 |
| AG09 | E2E / 混沌测试 | P1 | 全局 | `packages/test-fixtures/`、`tests/e2e/` | fixtures、崩溃测试、兼容测试 | 提交/拉取崩溃、SQLite 损坏、旧 journal 恢复均覆盖 |

---

## 3. 每个 Agent 的明确任务书

### AG00 总控 / 架构集成

- 目标：
  搭建统一骨架，锁定规则，控制集成节奏。
- 直接任务：
  1. 初始化 Monorepo 目录
  2. 建立 `docs/architecture/`、`docs/contracts/`、`docs/testing/`
  3. 建立根级 README 和开发约束文档
  4. 约定分支命名、包命名、模块所有权
- 禁止事项：
  1. 不越权重写具体业务模块
  2. 不直接替代 AG01 定义协议
- 输出检查：
  1. 是否形成统一目录骨架
  2. 是否写清楚 owner 和集成顺序

### AG01 协议与共享模型

- 目标：
  提供整个项目的唯一契约源。
- 直接任务：
  1. 定义 `manifest` schema
  2. 定义 `tombstone` schema
  3. 定义 `filemap.json` schema
  4. 定义 `vault_state` schema
  5. 定义 AI wiki page schema
  6. 定义同步 API DTO
  7. 提供 canonical 序列化 fixtures
- 禁止事项：
  1. 不在其他目录复制协议定义
  2. 不在前后端中维护第二份真相
- 输出检查：
  1. 共享结构是否版本化
  2. 字段语义是否闭环
  3. 是否有 golden fixtures

### AG02 本地存储 Core

- 目标：
  完成本地文件系统主存储能力。
- 直接任务：
  1. 实现 Vault 初始化器
  2. 实现 `filemap.json` 单写入口
  3. 实现 `.tmp` 原子替换与恢复
  4. 实现 `tombstone-ledger.jsonl`
  5. 实现 `drafts/`、`staging/`、`conflict-orphans/`
- 禁止事项：
  1. 不把主内容放进 SQLite
  2. 不修改 manifest 规则
- 输出检查：
  1. 文件身份是否稳定
  2. 删除历史是否可恢复
  3. 冲突副本是否隔离

### AG03 SQLite 索引与搜索

- 目标：
  实现 SQLite 辅助层和搜索能力。
- 直接任务：
  1. 设计 schema 与迁移
  2. 实现 `file_index`
  3. 实现 `search_index` / FTS5
  4. 实现 `wiki_pages`、`raw_sources`、`wiki_tasks`
  5. 实现 `vault_state`
- 禁止事项：
  1. 不把正文当主存储
  2. 不引入和协议冲突的状态字段
- 输出检查：
  1. 删库重建是否可行
  2. 搜索和文件系统是否一致

### AG04 同步服务端

- 目标：
  提供远端同步中继能力。
- 直接任务：
  1. 初始化 Python 后端项目
  2. 完成 `.cicd.env`
  3. 完成 `requirements.txt`
  4. 提供 `config/nacosConfig.py`
  5. 实现设备管理、blob 授权、CAS commit、ack
- 禁止事项：
  1. 不假设云端是主真相
  2. 不吞并客户端恢复语义
- 输出检查：
  1. 是否满足现有 Python 部署规范
  2. API 是否与 AG01 对齐

### AG05 同步客户端

- 目标：
  在本地执行同步协议闭环。
- 直接任务：
  1. 变更检测
  2. `commit_intent_journal`
  3. `sync_apply_journal`
  4. pull / reconcile
  5. 冲突副本落地
  6. journal 驱动恢复
- 禁止事项：
  1. 不跳过 journal 直接恢复 staging
  2. 不并发执行 pull 和 commit
- 输出检查：
  1. 离线编辑是否能收敛
  2. 崩溃后是否幂等恢复

### AG06 AI 编译与问答后端

- 目标：
  完成 AI 知识编译层和问答层。
- 直接任务：
  1. ingest
  2. compile
  3. `.ai/wiki/` 生成
  4. `.ai/index.md` 更新
  5. ask 编排
  6. 引用式回答
  7. `wiki_tasks` worker
- 禁止事项：
  1. 不把 `.ai/raw/` 加入常规同步
  2. 不走“整库直接问答”作为默认主路径
- 输出检查：
  1. wiki 是否稳定产出
  2. 问答是否优先 wiki

### AG07 Web 前端

- 目标：
  实现 Web 演示主链路。
- 直接任务：
  1. 文件树
  2. 编辑器
  3. 同步状态栏
  4. AI 面板
  5. 冲突处理面板
- 禁止事项：
  1. 不在前端缓存里定义第二份文件身份真相
  2. 不默认依赖高版本 Node 服务器构建
- 输出检查：
  1. 主链路是否可演示
  2. `.cicd.env` 是否符合 static 项目规范

### AG08 Desktop / Mobile

- 目标：
  完成客户端侧适配。
- 直接任务：
  1. 桌面端壳与文件能力
  2. 移动端私有目录和前台同步
  3. 完整 Vault 导入导出
  4. AI 降级提示
- 禁止事项：
  1. 不绕过本地存储 core
  2. 不承诺移动端全量 raw 能力一致
- 输出检查：
  1. 桌面端是否本地优先
  2. 移动端是否按方案降级

### AG09 E2E / 混沌测试

- 目标：
  构建长期可信的回归体系。
- 直接任务：
  1. fixtures
  2. crash injection
  3. import/export 测试
  4. 旧 journal 恢复测试
  5. SQLite 损坏恢复测试
- 禁止事项：
  1. 不替代业务模块修协议
- 输出检查：
  1. 是否覆盖关键恢复路径
  2. 是否覆盖协议兼容路径

---

## 4. 建议分支命名

| Agent | 建议分支名 |
|------|------|
| AG00 | `feat/ag00-repo-governance` |
| AG01 | `feat/ag01-protocol-contracts` |
| AG02 | `feat/ag02-vault-core-storage` |
| AG03 | `feat/ag03-sqlite-index-search` |
| AG04 | `feat/ag04-sync-server-python` |
| AG05 | `feat/ag05-sync-client-engine` |
| AG06 | `feat/ag06-ai-compile-ask` |
| AG07 | `feat/ag07-web-app` |
| AG08 | `feat/ag08-clients-desktop-mobile` |
| AG09 | `feat/ag09-e2e-chaos-tests` |

---

## 5. 建议提交节奏

### 波次 1

1. AG00
2. AG01

### 波次 2

1. AG02
2. AG03
3. AG04

### 波次 3

1. AG05
2. AG06
3. AG07

### 波次 4

1. AG08
2. AG09

---

## 6. 集成检查清单

每个 Agent 提交合并前，必须回答以下问题：

1. 是否只修改了自己负责的目录
2. 是否新增了共享字段
3. 是否提供了单独验收步骤
4. 是否说明了未解决风险
5. 是否与 `packages/protocol/` 保持一致

---

## 7. 一句话执行建议

先让少数 Agent 把契约和骨架做硬，再让多数 Agent 并行实现业务能力；不要一开始就同时让很多 Agent 进入协议层。

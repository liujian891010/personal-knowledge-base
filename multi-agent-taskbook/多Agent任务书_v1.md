# 多 Agent 任务书 v1

## 1. 文档目标

本任务书用于将“类 Obsidian 软件最终可落地方案（本地优先 + 多端同步 + AI 知识库版）”拆解为多个可并行、可单独验收、最终可集成落地的开发任务。

约束来源：

1. 当前目录下方案文档：`类Obsidian软件最终可落地方案（本地优先+多端同步+AI知识库版）_v1.0.43.md`
2. 用户补充的 MonoRepo 规范

本任务书面向：

1. 总控 Agent
2. 各专项执行 Agent
3. 后续集成与验收负责人

---

## 2. 总体实施原则

### 2.1 先冻结契约，再并行开发

必须先冻结以下 6 类共享契约，再启动大规模并行：

1. Vault 目录结构
2. `filemap.json` 语义
3. `manifest / tombstone` canonical 规则
4. `vault_state` 字段定义
5. 同步 API 契约
6. AI 产物边界：同步 `.ai/wiki/`，不常规同步 `.ai/raw/`

### 2.2 按所有权拆，不按页面拆

禁止按“页面”或“前后端各做一半”来拆任务。必须按模块所有权拆分，确保每个 Agent 有清晰写权限边界。

### 2.3 部署单元尽量少，模块尽量清晰

V1 推荐只保留两个部署单元：

1. `apps/frontend/noteapp-web`
2. `apps/backend/noteapp-server`

桌面端和移动端先作为客户端工程管理，不纳入当前 Web 服务部署单元。

### 2.4 严格遵守 MonoRepo 规范

V1 目录结构采用“多个前后端项目”规范：

```text
apps/
  frontend/
    noteapp-web/
  backend/
    noteapp-server/
clients/
  desktop/
  mobile/
packages/
  protocol/
  vault-core/
  ai-core/
  test-fixtures/
docs/
```

所有部署子项目必须提供 `.cicd.env`。

建议：

1. 前端：`cicd_monorepo_type='static'`
2. 后端：`cicd_monorepo_type='python'`
3. 后端优先 Python，不优先 Node
4. 若前端构建依赖高版本 Node，则走预构建产物并设置 `cicd_build_static=false`

---

## 3. 推荐仓库结构

```text
repo/
  apps/
    frontend/
      noteapp-web/
        .cicd.env
    backend/
      noteapp-server/
        .cicd.env
        requirements.txt
        config/
          nacosConfig.py
  clients/
    desktop/
    mobile/
  packages/
    protocol/
    vault-core/
    ai-core/
    test-fixtures/
  tests/
    e2e/
  docs/
```

---

## 4. Agent 角色与边界

### AG00 总控 / 架构集成 Agent

- 目标：
  负责统一任务编排、契约冻结、分支合并、冲突仲裁、集成验收。
- 主要输入：
  - 方案文档
  - 本任务书
  - MonoRepo 规范
- 主要输出：
  - 仓库基础结构
  - 统一开发规则
  - 集成基线
- 目录归属：
  - 仓库根目录结构
  - `docs/`
  - CI 相关公共文件
- 不可修改边界：
  - 不越权重写各专项模块实现
- 验收标准：
  1. 所有模块均有 owner
  2. 所有共享契约有单一来源
  3. 各 Agent 产出可被统一集成

### AG01 协议与共享模型 Agent

- 目标：
  冻结共享协议和数据模型，作为所有后续实现的唯一契约来源。
- 主要输出：
  1. `manifest` schema
  2. `tombstone` schema
  3. `filemap.json` schema
  4. `vault_state` schema
  5. AI wiki page schema
  6. API DTO / OpenAPI
  7. golden fixtures
- 目录归属：
  - `packages/protocol/`
- 不可修改边界：
  - 不实现业务逻辑
  - 不直接修改客户端/服务端 UI
- 依赖：
  - 仅依赖方案文档和总控结论
- 验收标准：
  1. 所有共享结构有明确字段语义
  2. 具备版本号和兼容说明
  3. 同一输入在不同实现中产生一致摘要和序列化结果

### AG02 本地存储 Core Agent

- 目标：
  实现文件系统主存储、本地元数据、身份映射与恢复逻辑。
- 主要输出：
  1. Vault 目录初始化
  2. `filemap.json` 读写与串行化管理
  3. 原子写入与 `.tmp` 恢复
  4. `tombstone-ledger.jsonl`
  5. `drafts/`、`staging/`、`conflict-orphans/` 管理
- 目录归属：
  - `packages/vault-core/`
- 不可修改边界：
  - 不定义协议字段
  - 不修改服务端 API 契约
- 依赖：
  - AG01
- 验收标准：
  1. 新建、重命名、移动、删除可正确更新 `filemap.json`
  2. `filemap.json` 永远是本地身份唯一真相
  3. 崩溃后可恢复 `.tmp`、staging、ledger
  4. 冲突副本不进入远端 tombstone 生命周期

### AG03 SQLite 索引与搜索 Agent

- 目标：
  实现 SQLite 辅助层，用于索引、状态、搜索、AI 辅助数据管理。
- 主要输出：
  1. SQLite schema
  2. migration 脚本
  3. `file_index`
  4. `search_index` / FTS5
  5. `wiki_pages`
  6. `raw_sources`
  7. `wiki_tasks`
  8. `vault_state`
- 目录归属：
  - `packages/vault-core/`
  - 如需拆分，可新增 `packages/search-core/`
- 不可修改边界：
  - 不保存主内容正文
  - 不修改 manifest 协议
- 依赖：
  - AG01
  - 与 AG02 并行联调
- 验收标准：
  1. SQLite 删除后可按文件系统重建索引
  2. 搜索结果与当前文件系统一致
  3. `vault_state` 字段实现与协议一致
  4. `wiki_tasks` 并发约束可被测试覆盖

### AG04 同步服务端 Agent

- 目标：
  实现远端同步中继、认证、CAS 提交、blob 上传下载授权。
- 主要输出：
  1. `apps/backend/noteapp-server/` 服务骨架
  2. 设备管理接口
  3. vault head 管理
  4. blob `upload-init` / `download-init`
  5. commit CAS 控制
  6. `acked_revision` 维护
  7. tombstone 回收资格判断
- 目录归属：
  - `apps/backend/noteapp-server/`
- 不可修改边界：
  - 不重定义本地 `filemap.json` 语义
  - 不直接侵入客户端本地存储实现
- 依赖：
  - AG01
- 验收标准：
  1. 并发 commit 不乱序
  2. 被移除设备立即失效
  3. 下载凭证可撤销
  4. API 契约与 `packages/protocol/` 保持一致
  5. 符合 Python 项目部署规范，包含 `.cicd.env`、`requirements.txt`、`config/nacosConfig.py`

### AG05 同步客户端 Agent

- 目标：
  实现本地变更检测、commit/pull/reconcile、崩溃恢复、冲突处理。
- 主要输出：
  1. 变更检测器
  2. `commit_intent_journal`
  3. `sync_apply_journal`
  4. push / pull / reconcile 流程
  5. blob 上传下载客户端
  6. 冲突副本生成与日志记录
- 目录归属：
  - `packages/vault-core/`
  - 必要时可新增 `packages/sync-core/`
- 不可修改边界：
  - 不重定义服务端 API
  - 不修改 AI schema
- 依赖：
  - AG01
  - AG02
  - AG03
  - AG04
- 验收标准：
  1. 两端编辑、离线编辑、rename/delete 可收敛
  2. 提交中崩溃、拉取中崩溃可恢复
  3. 幂等重试不造成状态污染
  4. pull/commit 互斥调度成立

### AG06 AI 编译与问答后端 Agent

- 目标：
  实现 AI ingest、compile、ask、知识页落地与引用回答能力。
- 主要输出：
  1. ingest 流程
  2. 原始内容抽取与切分
  3. `.ai/wiki/` 知识页生成
  4. `.ai/index.md` 更新
  5. 问答编排：优先 wiki，不足回查 raw/source
  6. 引用式回答
  7. `wiki_tasks` worker 机制
- 目录归属：
  - `packages/ai-core/`
  - `apps/backend/noteapp-server/` 内 AI 模块
- 不可修改边界：
  - 不改变 `.ai/raw/` 常规不同步边界
  - 不定义新的共享协议字段
- 依赖：
  - AG01
  - AG03
  - AG04
- 验收标准：
  1. 同一来源重复编译可幂等
  2. `wiki_tasks` 的 superseded 语义正确
  3. 问答优先检索 `.ai/wiki/`
  4. raw 缺失时有明确降级提示

### AG07 Web 前端 Agent

- 目标：
  实现 Web 端主链路界面与交互。
- 主要输出：
  1. 文件树
  2. 编辑器集成
  3. 同步状态栏
  4. AI 面板
  5. 冲突处理面板
  6. Vault 管理基础界面
- 目录归属：
  - `apps/frontend/noteapp-web/`
- 不可修改边界：
  - 不在前端私自发明协议字段
  - 不把本地真相从 `filemap.json` 转移到前端状态
- 依赖：
  - AG01
  - AG05
  - AG06
- 验收标准：
  1. 能走通“编辑 -> 同步 -> AI 编译 -> AI 问答”
  2. 冲突副本有明确可见性
  3. 若采用静态部署，构建产物和 `.cicd.env` 符合规范
  4. 若 Node 版本高于 `v16.20.2` 不可直接作为服务器构建前提

### AG08 Desktop / Mobile 客户端 Agent

- 目标：
  实现桌面端壳层、本地能力接入，以及移动端前台同步和 AI 降级体验。
- 主要输出：
  1. `clients/desktop/`
  2. `clients/mobile/`
  3. 本地文件能力接入
  4. 移动端私有目录适配
  5. Vault 导入导出
  6. AI 降级提示
- 目录归属：
  - `clients/desktop/`
  - `clients/mobile/`
- 不可修改边界：
  - 不修改服务端同步协议
  - 不改变 `.ai/raw/` 同步边界
- 依赖：
  - AG01
  - AG02
  - AG05
  - AG06
- 验收标准：
  1. 桌面端具备完整本地优先能力
  2. 移动端支持前台同步、后台 best-effort
  3. raw 缺失时 AI 可基于 `.ai/wiki/` 降级运行

### AG09 E2E / 混沌测试 Agent

- 目标：
  建立可重复的集成测试、恢复测试与兼容性验证体系。
- 主要输出：
  1. fixture 数据集
  2. golden manifest / golden filemap
  3. 崩溃注入测试
  4. 导入导出测试
  5. 旧版本兼容恢复测试
- 目录归属：
  - `packages/test-fixtures/`
  - `tests/e2e/`
- 不可修改边界：
  - 不修改业务协议定义
- 依赖：
  - 全局联动
- 验收标准：
  1. 提交中崩溃恢复覆盖
  2. 拉取中崩溃恢复覆盖
  3. SQLite 损坏恢复覆盖
  4. `filemap.json.tmp` 残留恢复覆盖
  5. 旧 journal 兼容路径覆盖

---

## 5. 任务依赖关系

### 第一阶段：冻结基线

1. AG00 总控
2. AG01 协议与共享模型

### 第二阶段：底层并行实现

1. AG02 本地存储 Core
2. AG03 SQLite 索引与搜索
3. AG04 同步服务端

### 第三阶段：主链路打通

1. AG05 同步客户端
2. AG06 AI 编译与问答后端
3. AG07 Web 前端

### 第四阶段：客户端扩展

1. AG08 Desktop / Mobile

### 第五阶段：全链路回归

1. AG09 E2E / 混沌测试

---

## 6. 每个 Agent 必须遵守的规则

### 6.1 单一 owner 规则

以下内容必须有单一 owner，禁止多 Agent 同时修改：

1. `packages/protocol/`
2. `manifest` canonical 算法
3. `filemap.json` 核心语义
4. `vault_state` 字段定义

### 6.2 输出格式规则

每个 Agent 完成任务时，必须提交：

1. 变更说明
2. 修改目录列表
3. 新增/变更契约列表
4. 验收脚本或验收步骤
5. 未解决风险

### 6.3 集成前禁止事项

未经过 AG00 审核，任何 Agent 不得：

1. 擅自新增同步状态字段
2. 擅自修改 `.ai/wiki` 与 `.ai/raw` 的边界
3. 擅自改变 Monorepo 目录结构
4. 擅自引入不兼容部署体系的运行时前提

---

## 7. 部署与工程约束

### 7.1 前端约束

1. Web 前端目录必须是 `apps/frontend/noteapp-web/`
2. 必须存在 `.cicd.env`
3. 推荐：

```bash
cicd_monorepo_type='static'
cicd_monorepo_name='noteapp-web'
```

4. 若服务器不构建静态资源，则补充：

```bash
cicd_build_static='false'
```

### 7.2 后端约束

1. 后端目录必须是 `apps/backend/noteapp-server/`
2. 必须存在 `.cicd.env`
3. 必须存在 `requirements.txt`
4. 如接入 Nacos，必须存在 `config/nacosConfig.py`
5. 推荐：

```bash
cicd_monorepo_type='python'
cicd_monorepo_name='noteapp-server'
```

### 7.3 技术选型约束

1. 后端优先 Python
2. 前端不得默认依赖高于 `Node v16.20.2` 的服务器构建前提
3. 客户端工程不纳入当前 Web 服务部署单元

---

## 8. V1 验收里程碑

### M1 契约冻结

验收内容：

1. `packages/protocol/` 完成
2. 所有 schema 与 fixtures 固化
3. 共享边界评审通过

### M2 本地优先可用

验收内容：

1. 本地 Vault 可初始化
2. 文件读写、重命名、删除、恢复可用
3. SQLite 索引可工作

### M3 双端同步闭环

验收内容：

1. 两台设备可 push / pull / reconcile
2. 冲突副本机制成立
3. 崩溃恢复成立

### M4 AI 知识编译闭环

验收内容：

1. 可对笔记/目录执行 ingest
2. `.ai/wiki/` 可生成并更新索引
3. 问答优先基于 wiki 层

### M5 Web 可演示

验收内容：

1. 编辑
2. 同步
3. AI 编译
4. AI 问答
5. 冲突处理

### M6 客户端与回归

验收内容：

1. 桌面端本地优先能力成立
2. 移动端具备前台同步和 AI 降级
3. E2E 与混沌测试通过

---

## 9. 当前建议的启动顺序

建议按以下顺序立项：

1. AG00：落仓库骨架、文档、统一规则
2. AG01：冻结协议、schema、fixtures
3. AG02 / AG03 / AG04：并行开发底层能力
4. AG05：打通客户端同步
5. AG06：打通 AI 编译与问答
6. AG07：完成 Web 主链路
7. AG08：接入桌面端与移动端
8. AG09：全链路回归与混沌测试

---

## 10. 一句话结论

V1 不要拆成很多部署服务；要拆成很多“可单独验收的模块任务”。服务尽量少，契约尽量硬，目录所有权尽量清晰，这样多个 Agent 才能并行且最终可合并。

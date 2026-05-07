# vault-core

本目录用于实现本地存储、同步客户端和恢复相关核心能力。

当前 phase 1 交付的是一个 Python 参考实现，先把 AG02 的底层语义跑通：

1. Vault 目录初始化
2. `filemap.json` canonical 序列化
3. `.tmp` 原子写入与恢复
4. `conflict_copy` 记录约束

当前 phase 2 继续补齐：

1. `tombstone-ledger.jsonl` append-only 持久化
2. legacy ledger 缺失 `local_delete_seq` 的启动补号
3. 删除操作到 `filemap` + tombstone 的联动语义

当前 phase 3 继续补齐：

1. 新建 / 改名 / 移动的 `filemap` 变更 API
2. 冲突副本命名与去重
3. `staging-orphans/` 与 `conflict-orphans/` 隔离 helper

当前 AG03 phase 1 开始补 SQLite 基线：

1. `vault_state` 持久化
2. `file_index`
3. `wiki_tasks` 活跃任务唯一约束
4. `search_index` / FTS 结构

当前 phase 继续补 journal 持久化：

1. `sync_apply_journal`
2. `commit_intent_journal`
3. legacy `acknowledged -> submitted` 规范化

后续阶段再在此基础上补 pull / commit 恢复和更完整的索引联调。

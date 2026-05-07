# vault-core tests

当前测试覆盖：

1. Vault 初始化目录结构
2. `filemap.json` 原子写入与 round-trip
3. `.tmp` 恢复路径
4. `conflict_copy` 约束校验
5. `tombstone-ledger.jsonl` 追加与 legacy 修复
6. 删除操作对 `filemap` 和 tombstone 的联动
7. 新建 / 改名 / 冲突副本登记
8. 冲突副本命名和 orphan 隔离路径
9. SQLite schema bootstrap
10. `vault_state` round-trip
11. `wiki_tasks` 活跃路径唯一约束
12. `sync_apply_journal` round-trip
13. `commit_intent_journal` round-trip 与 legacy 规范化

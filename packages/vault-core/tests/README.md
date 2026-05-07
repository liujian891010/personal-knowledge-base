# vault-core tests

当前测试覆盖：

1. Vault 初始化目录结构
2. `filemap.json` 原子写入与 round-trip
3. `.tmp` 恢复路径
4. `conflict_copy` 约束校验
5. `tombstone-ledger.jsonl` 追加与 legacy 修复
6. 删除操作对 `filemap` 和 tombstone 的联动

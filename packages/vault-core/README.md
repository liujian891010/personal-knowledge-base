# vault-core

本目录用于实现本地存储、同步客户端和恢复相关核心能力。

当前 phase 1 交付的是一个 Python 参考实现，先把 AG02 的底层语义跑通：

1. Vault 目录初始化
2. `filemap.json` canonical 序列化
3. `.tmp` 原子写入与恢复
4. `conflict_copy` 记录约束

后续阶段再在此基础上补 journal、tombstone ledger、pull / commit 恢复和 SQLite 联调。

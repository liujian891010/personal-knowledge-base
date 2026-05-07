# filemap

`VaultRoot/.noteapp/filemap.json` 是本地文件身份的唯一真相。

## 结构冻结

顶层字段：

1. `schema_version`
2. `vault_id`
3. `updated_at`
4. `files[]`

每个 `files[]` 记录：

1. `file_id`
2. `path`
3. `type`
4. `status`
5. `content_hash` 可选
6. `last_known_revision` 可选
7. `updated_at`
8. `conflict_source_file_id` 条件必填

## 核心语义

1. `file_id` 在应用内创建时生成，全生命周期稳定
2. `path` 表示当前位置，不代表身份
3. `type` 描述内容类别，v1 固定为 `note | attachment | ai_wiki | ai_index | ai_agents`
4. `status` 固定为 `active | deleted | conflict_copy`

状态约束：

1. `active` 表示该记录参与正常工作树与 manifest 导出
2. `deleted` 表示该 `file_id` 已从规范路径删除，但身份历史仍需保留给同步与恢复
3. `conflict_copy` 表示本地保留下来的冲突副本；它不进入远端 tombstone 生命周期，且必须带 `conflict_source_file_id`

## 写入保护

`filemap.json` 只能由本地核心层串行写入，必须满足：

1. 先写 `.noteapp/filemap.json.tmp`
2. 完整刷盘后再原子替换
3. 启动发现 `.tmp` 残留时，优先进入恢复逻辑
4. UI、服务端、SQLite 都不得直接改写 `filemap.json`

## 与其他层的边界

1. manifest 是 filemap 的导出格式，不是反向权威
2. SQLite `file_index` 只是缓存，不得分配或改写 `file_id`
3. 只有在远端 manifest 已成功应用到本地后，核心层才允许用该最终状态重写 `filemap.json`

## 崩溃恢复约束

1. 若 `filemap.json` 损坏但 `.tmp` 可读，优先从 `.tmp` 恢复
2. 若 `filemap.json` 与 SQLite 不一致，以 `filemap.json` 为准
3. 若 `filemap.json` 丢失，系统进入身份修复模式；只有无法 best-effort 对齐的文件才允许分配新 `file_id`

## 禁止扩展

以下字段或语义不得在其他模块中重新定义：

1. `status = conflict_copy` 的含义
2. `conflict_source_file_id` 的触发条件
3. `last_known_revision` 的含义
4. `type` 枚举

参考 fixture：

1. `packages/protocol/fixtures/filemap/golden-mixed-state.json`

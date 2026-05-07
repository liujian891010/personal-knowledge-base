# protocol-schema-overview

当前首版 schema 的设计原则：

1. 先冻结核心字段与状态边界
2. 允许通过 `meta` 预留低风险扩展位
3. 不在 schema 层过早写死实现细节

当前已冻结的重点：

1. `manifest.schema.json`
   - 冻结 `vault_id`、`revision`、`files[]`、`tombstones[]`
2. `filemap.schema.json`
   - 冻结 `file_id`、`path`、`type`、`status`
3. `tombstone.schema.json`
   - 冻结 `deleted_revision = null` 作为唯一合法未提交占位语义
4. `vault-state.schema.json`
   - 冻结 `last_applied_revision`、`remote_head_revision`、`acked_revision`、`pending_ack_to_server`、`commit_in_progress`、`last_manifest_summary`、`local_delete_sequence`
5. `wiki-page.schema.json`
   - 冻结 AI wiki 产物的逻辑结构，强调 `source_refs`

当前仍保留给后续实现细化的内容：

1. manifest canonical 序列化细节
2. blob 命名规则
3. API request/response 的完整 OpenAPI 细节
4. wiki markdown 与逻辑 schema 的映射细节

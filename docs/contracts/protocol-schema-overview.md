# protocol-schema-overview

AG01 phase 1 冻结的共享协议范围如下。

1. 先冻结核心字段与状态边界
2. 允许通过 `meta` 预留低风险扩展位
3. 先冻结单一真相，再放开并行开发

## 已冻结的 schema

1. `manifest.schema.json`
   - 冻结 `revision / base_revision / created_by_device / created_at / files[] / tombstones[] / summary_hash`
2. `filemap.schema.json`
   - 冻结 `file_id / path / type / status / conflict_source_file_id / updated_at`
3. `tombstone.schema.json`
   - 冻结 `deleted_revision = null` 的未提交占位语义，以及 `local_delete_seq`
4. `vault-state.schema.json`
   - 冻结 `last_applied_revision / remote_head_revision / acked_revision / pending_ack_to_server / commit_in_progress / last_manifest_summary / last_manifest_summary_status / local_delete_sequence`
5. `wiki-page.schema.json`
   - 冻结 AI wiki 产物的逻辑结构，强调 `page_type / source_refs / user_edited / locked`

## 已冻结的文档

1. `docs/contracts/manifest.md`
2. `docs/contracts/filemap.md`
3. `docs/contracts/sync-api.md`
4. `docs/contracts/ai-boundary.md`

## 已冻结的 golden fixtures

1. `packages/protocol/fixtures/manifest/golden-initial-sync.json`
2. `packages/protocol/fixtures/filemap/golden-mixed-state.json`
3. `packages/protocol/fixtures/vault-state/golden-post-pull.json`
4. `packages/protocol/fixtures/wiki-page/golden-topic.json`

## 仍允许后续实现细化

1. blob 的具体对象存储命名与生命周期策略
2. 客户端内部 journal 文件的磁盘编码
3. SQLite 表结构和索引细节
4. wiki markdown 文本模板的美化策略

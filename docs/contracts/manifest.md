# manifest

`manifest` 是 `VaultRoot/.noteapp/filemap.json` 在同步层的导出格式，不是第二份权威真相。

## 结构冻结

canonical `manifest` 必须包含：

1. `schema_version`
2. `vault_id`
3. `revision`
4. `base_revision`
5. `created_by_device`
6. `created_at`
7. `files[]`
8. `tombstones[]`
9. `summary_hash`

其中：

1. `revision` 是本次提交对应的目标 revision；新 vault 的首次提交允许 `base_revision = 0`
2. `files[]` 是当前 vault 的全量快照，不是增量 patch
3. `tombstones[]` 是当前仍需保留的删除标记集合
4. `summary_hash` 是 canonical JSON 的稳定摘要，用于快捷判断和恢复校验

## files[] 规则

每个 `files[]` 项必须包含：

1. `file_id`
2. `path`
3. `type`
4. `content_hash`
5. `blob_id`
6. `size`
7. `mtime`

约束：

1. `path` 必须是 Vault 相对路径，并在写入前完成 Unicode NFC 归一化
2. 同一个 manifest 内，`path` 必须唯一
3. `file_id` 是身份，`path` 不是身份
4. `mtime` 语义是源设备本地最后写入时间，单位是 UTC Unix 毫秒；目标设备拉取时不得直接拿它覆盖本地文件系统 mtime

## tombstones[] 规则

每个 `tombstone` 至少包含：

1. `file_id`
2. `deleted_revision`
3. `deleted_at`
4. `local_delete_seq`

补充规则：

1. `deleted_revision = null` 只允许出现在本地未提交删除意图中；进入已提交 manifest 后必须是整数
2. `local_delete_seq` 是本地删除顺序号，canonical v1 输出必须带上它
3. `last_known_path` 仅用于恢复、日志和冲突解释，不用于文件身份判断

## canonical 序列化

v1 统一要求：

1. 对象键按字典序输出
2. 不输出未定义字段
3. `files[]` 按 `path`、`file_id` 排序
4. `tombstones[]` 按 `local_delete_seq`、`file_id` 排序
5. JSON 使用 UTF-8，无 BOM
6. 摘要计算输入是 canonical JSON 字节串；不同实现不得各自发明排序或摘要口径

## 写入方与读取方

写入方：

1. AG02/AG05 所在的本地核心层导出 manifest
2. AG04 服务端只校验和持久化，不重写语义

读取方：

1. AG04 服务端用于 CAS 校验和 head 管理
2. AG05 客户端用于 pull / reconcile
3. AG03 可缓存摘要结果，但不得成为权威

## 崩溃恢复约束

1. commit 进行中若本地崩溃，恢复路径必须以 `commit_intent_journal` 为准重新判断该 manifest 是否已落远端
2. `summary_hash` 缺失、损坏或被标记为 stale 时，客户端必须回退到重新下载 manifest 并做完整收敛
3. 不允许通过本地 SQLite 自行“补写”一个未经 filemap 导出的 manifest

## 禁止扩展

以下内容不得在其他模块中各自扩展出第二套定义：

1. `files[].type`
2. `summary_hash` 计算口径
3. `tombstones[]` 排序规则
4. `deleted_revision = null` 的语义

参考 fixture：

1. `packages/protocol/fixtures/manifest/golden-initial-sync.json`

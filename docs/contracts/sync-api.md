# sync-api

当前首版同步 API 已冻结主链路接口分组和关键语义，OpenAPI 源文件位于：

`packages/protocol/openapi/sync-api.yaml`

## 接口分组

1. 设备管理
2. head 查询
3. manifest 拉取
4. CAS commit
5. commit intent 恢复确认
6. ack 上报
7. blob 上传授权
8. blob 下载授权

## 接口路径

1. `POST /devices/register`
2. `DELETE /devices/{deviceId}`
3. `GET /vaults/{vaultId}/head`
4. `GET /vaults/{vaultId}/manifests/{revision}`
5. `POST /vaults/{vaultId}/commits`
6. `POST /vaults/{vaultId}/commits/resolve-intent`
7. `POST /vaults/{vaultId}/ack`
8. `POST /vaults/{vaultId}/blobs/check`
9. `POST /vaults/{vaultId}/blobs/upload-init`
10. `POST /vaults/{vaultId}/blobs/download-init`

## 冻结语义

1. commit 必须按 `base_revision` 执行 CAS
2. `CreateCommitRequest.manifest` 必须是由本地 `filemap.json` 导出的全量快照，不允许客户端只提交局部 patch
3. `commit_intent_id + intent_manifest_hash` 用于崩溃恢复时判断一次提交是否已经落远端
4. pull 成功后由客户端通过 `/ack` 上报已应用 revision
5. commit 成功路径不应把 revision 写入本地 `pending_ack_to_server`
6. blob 下载必须走 `download-init`，不得把 `blob_id` 当静态公开地址
7. 服务端只做同步中继、授权和 CAS 校验，不拥有主数据真相

## writer / reader 边界

1. AG01 负责 DTO 和 OpenAPI 定义
2. AG04 负责服务端实现，但不得私自扩展共享字段含义
3. AG05 负责客户端调用和恢复逻辑，但不得重定义返回语义

## 错误处理约束

1. `409` 只表示 CAS 冲突或 manifest 冲突，不得混入鉴权或对象存储错误
2. 鉴权失败必须使用 `401` 或 `403`
3. `download-init` 和 `upload-init` 返回的能力必须短时有效、可撤销

## 与本地状态的关系

1. `GET /vaults/{vaultId}/head` 成功后，客户端应刷新本地 `remote_head_revision`
2. 本地 `acked_revision` 和服务端按设备维护的 ack 状态必须保持单调递增
3. `resolve-intent` 是恢复路径接口，不是正常提交流程的替代接口

禁止行为：

1. 客户端绕过 `download-init` 直接拼对象存储 URL
2. 服务端重排 `manifest.files[]` / `tombstones[]` 后再计算自己的摘要
3. 客户端把 `ack` 当作“提交成功确认”

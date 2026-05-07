# sync-api

当前首版同步 API 已冻结主链路接口分组：

1. 设备管理
2. head 查询
3. manifest 拉取
4. CAS commit
5. commit intent 恢复确认
6. ack 上报
7. blob 上传授权
8. blob 下载授权

当前 OpenAPI 文件：

`packages/protocol/openapi/sync-api.yaml`

当前接口路径：

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

当前约束重点：

1. commit 必须按 `base_revision` 执行 CAS
2. pull 成功后由客户端通过 `/ack` 上报已应用 revision
3. commit 成功路径不应把 revision 写入本地 `pending_ack_to_server`
4. blob 下载必须走 `download-init`，不得把 `blob_id` 当静态公开地址

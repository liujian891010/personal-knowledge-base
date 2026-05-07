# protocol

本目录是全仓库共享协议与数据契约的唯一来源。

禁止在其他模块维护第二份真相：

1. manifest
2. filemap
3. tombstone
4. vault_state
5. AI wiki page schema

当前包含三类产物：

1. `schemas/`：JSON Schema 单一真相
2. `openapi/`：同步 API 契约
3. `fixtures/`：golden 输入输出样例，用于跨实现一致性校验

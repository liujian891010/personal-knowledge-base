# NoteApp Monorepo

这是一个基于 Monorepo 管理的本地优先知识库项目骨架。

当前目录约定：

1. `apps/frontend/noteapp-web/`：Web 前端部署项目
2. `apps/backend/noteapp-server/`：Python 后端部署项目
3. `clients/`：桌面端与移动端客户端工程
4. `packages/`：共享协议、核心能力和测试夹具
5. `docs/`：架构、契约和测试文档

用户向文档见 [`docs/user/README.md`](docs/user/README.md)，覆盖 V1 手册、FAQ、隐私说明与 AI 边界。

当前阶段已经进入 MVP 收口。可用范围、延后范围和发布前验收命令见
[`docs/develop/mvp-scope.md`](docs/develop/mvp-scope.md)。

MVP 启动、验收和交付口径见
[`docs/develop/mvp-handoff.md`](docs/develop/mvp-handoff.md)。

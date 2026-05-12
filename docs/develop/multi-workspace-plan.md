# 多工作区支持方案

## 1. 目标

当前项目只支持单一工作区运行：bridge 进程中只有一个活动工作区根路径，前端所有文件树、搜索、回收站、AI 文档、设置页都默认绑定这个唯一工作区。

多工作区的目标不是“同时挂载多个工作区到一个视图”，而是：

- 支持用户注册多个工作区
- 支持在多个工作区之间快速切换
- 运行时始终只存在一个活动工作区
- 当前已有功能继续围绕“活动工作区”工作

这是一种“工作区注册表 + 活动工作区切换”的方案。

## 2. 为什么不做并行多工作区

不建议第一版做“多个工作区同时打开”的原因：

- 文件树、搜索、草稿、links、trash、AI 上下文都会变成跨工作区聚合问题
- `file_id` 的唯一性边界会变复杂
- 现有 API 基本都默认作用于当前工作区，强改成多工作区参数化成本高
- UI 复杂度会明显上升，不利于当前 MVP 收口

因此推荐保持：

- 单活动工作区
- 多工作区注册与切换

## 3. 当前实现约束

当前单工作区约束主要在以下位置：

- `apps/frontend/noteapp-web/scripts/sync-shell-bridge.mjs`
  - 只有一个全局 `selectedVaultRoot`
  - `workspace-root.json` 只保存一个 `vault_root`
- `apps/frontend/noteapp-web/src/useLocalSettingsSnapshot.ts`
  - 设置页只读写当前唯一工作区
- 当前 AI 会话已落到当前工作区 `.noteapp/ai-chats`
  - 这对多工作区是有利的，因为天然隔离

## 4. 方案总览

### 4.1 核心概念

引入三个概念：

- `workspace registry`
  - 用户已注册的工作区列表
- `active workspace`
  - 当前正在使用的工作区
- `workspace local state`
  - 每个工作区自身的本地状态，如 `.noteapp` 下的文件、草稿、AI 会话、回收站、索引

### 4.2 运行模型

运行时仍保持：

- bridge 只服务一个活动工作区
- `/api/workspace/*` 永远指向当前活动工作区
- 用户切换工作区时，更新 `active_workspace_id`

## 5. 数据模型

### 5.1 持久化文件

不再只保存单一 `workspace-root.json`，升级为 `workspace-registry.json`。

建议位置：

- 与当前 bridge 配置持久化文件同级
- 不放在某个工作区内部

建议结构：

```json
{
  "schema_version": "v1",
  "active_workspace_id": "ws_20260512_ab12cd",
  "workspaces": [
    {
      "id": "ws_20260512_ab12cd",
      "name": "my_md",
      "vault_root": "C:\\WorkSpace\\my_md",
      "created_at_ms": 1770000000000,
      "last_opened_at_ms": 1770000000000
    }
  ]
}
```

### 5.2 字段说明

- `id`
  - 工作区稳定主键，用于前端切换和删除
- `name`
  - 用户可编辑的显示名
- `vault_root`
  - 真实工作区路径
- `created_at_ms`
  - 注册时间
- `last_opened_at_ms`
  - 最近切换到该工作区的时间
- `active_workspace_id`
  - 当前活动工作区 ID

## 6. API 设计

### 6.1 新增 API

建议在 bridge 增加：

- `GET /api/workspaces`
  - 读取工作区注册表和当前活动工作区
- `POST /api/workspaces`
  - 注册新工作区
- `PATCH /api/workspaces/:id`
  - 修改工作区显示名
- `DELETE /api/workspaces/:id`
  - 删除工作区注册项
- `POST /api/workspaces/:id/activate`
  - 切换活动工作区
- `POST /api/workspaces/select-folder`
  - 调用系统目录选择器，注册或切换工作区

### 6.2 保留现有 API 语义

以下 API 不需要改成多工作区参数化：

- `/api/workspace/files`
- `/api/workspace/search`
- `/api/workspace/trash`
- `/api/workspace/files/:id/content`
- `/api/workspace/files/:id/draft`
- `/api/ai/chat-sessions`
- `/api/settings/snapshot`

统一语义：

- 它们都只作用于当前活动工作区

这样可以大幅降低改造成本。

## 7. bridge 实现改造

### 7.1 全局状态改造

当前：

- `selectedVaultRoot: string`

改为：

- `workspaceRegistry`
- `activeWorkspaceId`
- `selectedVaultRoot` 作为从 registry 派生出来的运行时值

### 7.2 启动逻辑

bridge 启动时：

1. 读取 `workspace-registry.json`
2. 若存在 `active_workspace_id`，解析出 `selectedVaultRoot`
3. 若 registry 不存在但旧版 `workspace-root.json` 存在
   - 自动迁移成单工作区 registry
4. 若都不存在
   - 进入“未配置工作区”状态

### 7.3 注册逻辑

注册新工作区时：

1. 校验路径存在且为目录
2. 校验是否已注册相同 `vault_root`
3. 若未初始化 `.noteapp`
   - 允许后续沿用现有初始化流程
4. 将该工作区写入 registry
5. 可选：注册成功后直接设为活动工作区

### 7.4 删除逻辑

删除工作区时默认只做：

- 从 registry 移除

不做：

- 删除磁盘目录
- 删除工作区真实文档

如果删除的是当前活动工作区：

- 自动切换到剩余列表中的第一个
- 若无剩余工作区，则进入未配置状态

## 8. 前端 UI 方案

### 8.1 入口位置

不建议把多工作区入口只放在设置页里。

建议放在全局壳层顶部：

- 顶栏用户信息区域附近增加“工作区切换器”
- 显示当前工作区名称
- 点击后弹出工作区管理弹窗

### 8.2 弹窗能力

弹窗提供：

- 当前活动工作区高亮
- 切换工作区
- 新增工作区
- 通过系统目录选择器添加
- 修改工作区显示名
- 删除注册项

删除时使用项目内 UI 弹窗，不使用浏览器 `confirm`

### 8.3 设置页调整

设置页中的“工作区文件夹”区域调整为：

- 当前工作区路径
- 切换工作区
- 选择并添加工作区
- 从注册表移除当前工作区

不再把“选择文件夹”当成唯一工作区入口。

## 9. 切换工作区后的联动行为

切换工作区后必须统一刷新以下状态：

- 文件树 snapshot
- 当前选中文档
- 文档内容缓存
- draft 缓存
- links / backlinks
- 搜索结果
- 回收站数据
- AI 文档会话列表与当前会话
- 设置页 snapshot
- 同步状态 snapshot

否则会出现：

- UI 仍显示旧工作区内容
- 后台请求已经切到新工作区
- 页面局部数据错位

建议做一个统一的“工作区切换后全局失效刷新”机制，而不是页面各自猜测。

## 10. AI 文档与多工作区关系

当前 AI 会话持久化位置是：

- `.noteapp/ai-chats`

这是正确的，不需要挪动到全局配置目录。

多工作区下的行为应该是：

- 每个工作区拥有自己的 AI 文档会话
- 切换工作区后，AI 文档页重新加载该工作区的会话
- 不做跨工作区会话混合

这样可以保证语义清晰：

- 会话跟随知识库
- 上下文与文件来源一致

## 11. 兼容与迁移

### 11.1 从单工作区迁移

需要兼容已有用户：

1. 启动时如果发现旧版 `workspace-root.json`
2. 自动生成 `workspace-registry.json`
3. 将旧的 `vault_root` 迁移为第一个工作区
4. 将其设置为 `active_workspace_id`
5. 保留旧文件一段时间或标记为 deprecated

### 11.2 对现有页面的兼容性

由于运行时仍然只有一个活动工作区：

- Explorer
- Search
- Trash
- AI Chat
- Settings

理论上只需要在工作区切换时触发刷新，不需要改它们的核心协议。

## 12. 风险与边界

### 12.1 不要做的事情

第一版不要做：

- 多工作区同时挂载到一个文件树
- 搜索结果跨多个工作区聚合
- 一个 AI 会话引用多个工作区文件
- 删除注册项时默认删除真实目录

### 12.2 需要重点处理的边界

- 注册重复路径
- 当前活动工作区被删除
- 工作区路径已不存在
- 工作区被外部移动
- 切换过程中旧页面缓存未清空
- 首次启动时无工作区

## 13. 实施顺序

建议按以下顺序实现：

1. bridge 引入 `workspace-registry.json`
2. 保持 `/api/workspace/*` 仍指向当前活动工作区
3. 新增 `GET/POST/PATCH/DELETE /api/workspaces`
4. 新增 `POST /api/workspaces/:id/activate`
5. 做单工作区到 registry 的自动迁移
6. 前端壳层增加工作区切换器
7. 接入全局切换后的刷新机制
8. 最后再清理设置页中的旧单工作区入口文案

## 14. 第一版验收标准

第一版可接受的完成标准：

- 用户可以注册多个工作区
- 用户可以在顶部切换工作区
- 切换后文件树、搜索、回收站、AI 文档、设置页均刷新为对应工作区数据
- AI 会话按工作区隔离
- 删除工作区只删除注册项，不删除真实文件
- 旧单工作区配置可自动迁移

## 15. 结论

推荐采用：

- 多工作区注册表
- 单活动工作区运行时
- 全局切换 + 页面统一刷新

这是当前项目成本最低、风险最可控、与现有架构最兼容的落地方式。

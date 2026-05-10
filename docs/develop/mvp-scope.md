# MVP 收口边界

本文档用于锁定当前阶段的发布范围，避免继续把演示页、未来功能或重构工作混入主线。

## 当前必须可用

1. 桌面端本地工作区可以扫描文件状态，识别已跟踪、缺失、本地修改和冲突副本。
2. 前端笔记库浏览页可以读取真实工作区文件，查看文件元数据，编辑并保存本地文件内容。
3. 前端保存后能把“本地已修改 / 待同步”状态引导到同步页。
4. 同步页可以执行检查、提交、拉取、冲突清理等真实桥接动作，并展示执行后的状态反馈。
5. 发生提交冲突后，可以先拉取远端基线，再继续提交本地变更。
6. 拉取远端内容后，如果本地存在冲突副本，前端可以展示冲突副本并清理。
7. 主导航只开放已经接入真实工作流的页面：笔记库浏览、同步状态、冲突解决、设置。

## 当前明确延后

1. 仪表盘真实数据化。
2. AI Wiki 生成、问答和副驾驶。
3. 图谱真实关系计算与交互。
4. 回收站真实删除、恢复和清空。
5. 搜索、新建笔记、快捷记录等未接真实后端的交互。
6. 完整冲突合并编辑器。
7. 移动端客户端。
8. 真实加密 Provider 替换当前占位实现。

## 发布前验收命令

```powershell
npm.cmd run lint
```

在 `apps/frontend/noteapp-web` 目录执行。

```powershell
& 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' tests\e2e\frontend_dist_smoke.py
& 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' tests\e2e\frontend_sync_snapshot_smoke.py
& 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' tests\e2e\sync_server_smoke.py
$env:PYTHONPATH='packages/vault-core/src;.'; & 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' tests\e2e\desktop_sync_smoke.py
```

在仓库根目录执行。

## 收口规则

1. 只有影响“当前必须可用”列表的缺口才进入本阶段实现。
2. “当前明确延后”列表中的内容只能记录，不在本阶段继续开发。
3. 如果主导航出现未接真实数据的页面入口，视为发布阻塞。
4. 每完成一个可验证闭环，必须提交一次代码。

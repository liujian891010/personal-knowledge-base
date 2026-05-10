# MVP 启动与交付说明

本文档只覆盖当前 MVP 收口版。功能边界见 [`mvp-scope.md`](mvp-scope.md)。

## 本次验收结果

验收日期：2026-05-10

已通过：

1. 前端 TypeScript 检查。
2. 前端 dist 构建和 SPA 冒烟。
3. 前端同步快照桥接冒烟。
4. 后端同步服务器冒烟。
5. 桌面端双设备同步冒烟。
6. 三服务人工启动核验：后端、前端桥接服务、前端 dev server 同时运行，完成“读取工作区文件 -> 保存文件 -> 检测本地变更 -> 提交同步 -> 状态恢复 success”。

## 一次性准备

在仓库根目录准备前端依赖：

```powershell
cd apps\frontend\noteapp-web
npm install
```

准备后端依赖：

```powershell
cd apps\backend\noteapp-server
& 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' -m pip install -r requirements.txt
```

## 启动后端同步服务

新开一个 PowerShell，在仓库根目录执行：

```powershell
cd apps\backend\noteapp-server
& 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' -m uvicorn main:app --host 127.0.0.1 --port 8000
```

健康检查：

```text
http://127.0.0.1:8000/health
```

## 启动前端桥接服务

新开一个 PowerShell，在仓库根目录执行，并把 `NOTEAPP_VAULT_ROOT` 改成真实本地笔记库目录：

```powershell
cd apps\frontend\noteapp-web
$env:PYTHON='C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe'
$env:NOTEAPP_VAULT_ROOT='C:\vaults\pkb'
$env:NOTEAPP_SYNC_BASE_URL='http://127.0.0.1:8000'
$env:NOTEAPP_VAULT_ID='vault-local'
$env:NOTEAPP_DEVICE_ID='desktop-local'
npm.cmd run sync:bridge
```

桥接服务默认地址：

```text
http://127.0.0.1:3187/health
```

## 启动前端界面

新开一个 PowerShell：

```powershell
cd apps\frontend\noteapp-web
npm.cmd run dev
```

浏览器访问：

```text
http://127.0.0.1:3000/
```

如果 `3000` 已被占用，可以临时换端口：

```powershell
npm.cmd run dev -- --port 3001 --host 127.0.0.1
```

同时需要让桥接服务允许新的前端来源：

```powershell
$env:NOTEAPP_SYNC_BRIDGE_ORIGIN='http://127.0.0.1:3001'
```

当前主导航只开放：笔记库浏览、同步状态、冲突解决、设置。

## 发布验收命令

前端目录执行：

```powershell
cd apps\frontend\noteapp-web
npm.cmd run lint
```

仓库根目录执行：

```powershell
& 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' tests\e2e\frontend_dist_smoke.py
& 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' tests\e2e\frontend_sync_snapshot_smoke.py
& 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' tests\e2e\sync_server_smoke.py
$env:PYTHONPATH='packages/vault-core/src;.'; & 'C:\Users\ROBERT LIU\AppData\Local\Programs\Python\Python312\python.exe' tests\e2e\desktop_sync_smoke.py
```

## 交付口径

当前交付的是本地优先知识库 MVP 闭环，不是完整产品版。

可以演示：

1. 浏览真实本地工作区文件。
2. 查看文件元数据。
3. 编辑并保存本地文件。
4. 检测本地变更。
5. 提交、拉取、恢复远端基线。
6. 展示并清理冲突副本。
7. 在设置页输入本机文件夹路径，切换当前工作区。

## 切换工作区

进入“设置 -> 通用 -> 工作区文件夹”，输入电脑上的已有文件夹路径，然后点击“应用文件夹”。

应用后，本地桥接服务会：

1. 校验该文件夹是否存在。
2. 初始化缺失的 `.noteapp` 工作区数据。
3. 如果工作区是空 filemap，会自动登记已有的 `.md`、`.markdown`、`.txt` 文件。
4. 刷新设置快照、工作区文件列表和同步状态。

浏览器不能直接读取操作系统的完整文件夹路径，所以当前 MVP 使用“输入或粘贴路径”的方式完成选择。

不要承诺：

1. AI Wiki 已完成。
2. 图谱已接真实数据。
3. 回收站已接真实删除恢复。
4. 移动端已可用。
5. 加密 Provider 已生产化。

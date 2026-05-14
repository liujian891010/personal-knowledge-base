# NoteApp Desktop

当前目录是桌面客户端壳层第一版骨架，目标是先完成：

1. 启动桌面窗口
2. 自动拉起本地 bridge
3. 等待本地服务健康检查通过
4. 退出时回收 bridge 子进程

## 当前运行方式

### 开发模式

直接启动桌面端即可，桌面壳会自动拉起：

1. 本地 bridge
2. 前端 dev server（当 dist 不存在时）

```powershell
cd apps\desktop\noteapp-desktop
npm.cmd run dev
```

也可以从前端目录直接启动：

```powershell
cd apps\frontend\noteapp-web
npm.cmd run desktop:dev
```

### 生产模式

先构建前端：

```powershell
cd apps\frontend\noteapp-web
npm.cmd run build
```

再启动桌面端：

```powershell
cd apps\desktop\noteapp-desktop
npm.cmd run start
```

## 打包

### Windows

```powershell
cd apps\desktop\noteapp-desktop
npm.cmd run dist:win
```

产物输出到 `apps/desktop/noteapp-desktop/release/`。

### macOS

macOS 安装包需要在 macOS 主机上构建；Electron Builder 不支持在 Windows 上直接产出 macOS `dmg` / `zip`。

```bash
cd apps/desktop/noteapp-desktop
npm install
npm run dist:mac
```

未配置 Apple 开发者证书时，脚本默认关闭证书自动发现，可产出未签名本地测试包。正式发布仍需补齐签名与 notarization。

## 当前依赖的环境变量

如果未传，桌面端会使用默认值：

1. `NOTEAPP_SYNC_BASE_URL=http://127.0.0.1:8000`
2. `NOTEAPP_VAULT_ID=vault-local`
3. `NOTEAPP_DEVICE_ID=desktop-local`
4. `NOTEAPP_SYNC_BRIDGE_HOST=127.0.0.1`
5. `NOTEAPP_SYNC_BRIDGE_PORT=3187`

如需指定 Python，可在启动前设置：

```powershell
$env:PYTHON='C:\Path\To\python.exe'
```

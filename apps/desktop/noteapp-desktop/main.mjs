import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import { spawn } from 'node:child_process';
import { appendFileSync, existsSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const preloadPath = path.resolve(__dirname, 'preload.mjs');
const bridgeHost = process.env.NOTEAPP_SYNC_BRIDGE_HOST || '127.0.0.1';
const bridgePort = Number(process.env.NOTEAPP_SYNC_BRIDGE_PORT || 3187);
const bridgeBaseUrl = `http://${bridgeHost}:${bridgePort}`;
const devRendererUrl = process.env.NOTEAPP_DESKTOP_WEB_URL || '';
const healthTimeoutMs = 20_000;
const healthPollIntervalMs = 500;
const rendererHost = '127.0.0.1';
const rendererPort = Number(process.env.NOTEAPP_DESKTOP_RENDERER_PORT || 3000);
const fallbackRendererDevUrl = `http://${rendererHost}:${rendererPort}`;

let mainWindow = null;
let bridgeProcess = null;
let rendererProcess = null;
let isQuitting = false;

function bridgeStatePath(fileName) {
  return path.join(app.getPath('userData'), 'bridge-state', fileName);
}

function desktopLogPath() {
  return path.join(app.getPath('userData'), 'desktop-runtime.log');
}

function writeDesktopLog(message) {
  try {
    const targetPath = desktopLogPath();
    mkdirSync(path.dirname(targetPath), { recursive: true });
    appendFileSync(targetPath, `[${new Date().toISOString()}] ${message}\n`, 'utf8');
  } catch {
    // Ignore logging failures.
  }
}

function runtimeRoot() {
  return app.isPackaged
    ? path.join(process.resourcesPath, 'bundle')
    : path.resolve(__dirname, '..', '..', '..');
}

function webAppRoot() {
  return path.join(runtimeRoot(), 'apps', 'frontend', 'noteapp-web');
}

function bridgeScriptPath() {
  return path.join(webAppRoot(), 'scripts', 'sync-shell-bridge.mjs');
}

function frontendDistPath() {
  return path.join(webAppRoot(), 'dist', 'index.html');
}

function desktopEnv() {
  return {
    ...process.env,
    ELECTRON_RUN_AS_NODE: '1',
    NOTEAPP_REPO_ROOT: runtimeRoot(),
    NOTEAPP_SYNC_BRIDGE_HOST: bridgeHost,
    NOTEAPP_SYNC_BRIDGE_PORT: String(bridgePort),
    NOTEAPP_SYNC_BRIDGE_ALLOW_REMOTE: 'false',
    NOTEAPP_SYNC_BRIDGE_ORIGIN: devRendererUrl || '*',
    NOTEAPP_SYNC_BASE_URL: process.env.NOTEAPP_SYNC_BASE_URL || 'http://127.0.0.1:8000',
    NOTEAPP_VAULT_ID: process.env.NOTEAPP_VAULT_ID || 'vault-local',
    NOTEAPP_DEVICE_ID: process.env.NOTEAPP_DEVICE_ID || 'desktop-local',
    NOTEAPP_SYNC_SNAPSHOT_OUTPUT: process.env.NOTEAPP_SYNC_SNAPSHOT_OUTPUT || bridgeStatePath('live-sync-shell.json'),
    NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT: process.env.NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT || bridgeStatePath('local-settings-snapshot.json'),
    NOTEAPP_AUTH_SESSION_OUTPUT: process.env.NOTEAPP_AUTH_SESSION_OUTPUT || bridgeStatePath('auth-session.json'),
    NOTEAPP_WORKSPACE_FILES_OUTPUT: process.env.NOTEAPP_WORKSPACE_FILES_OUTPUT || bridgeStatePath('workspace-files.json'),
    NOTEAPP_WORKSPACE_ROOT_OUTPUT: process.env.NOTEAPP_WORKSPACE_ROOT_OUTPUT || bridgeStatePath('workspace-root.json'),
    NOTEAPP_WORKSPACE_REGISTRY_OUTPUT: process.env.NOTEAPP_WORKSPACE_REGISTRY_OUTPUT || bridgeStatePath('workspace-registry.json'),
  };
}

function createLoadingHtml(message) {
  return `<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <title>NoteApp Desktop</title>
    <style>
      body {
        margin: 0;
        min-height: 100vh;
        display: grid;
        place-items: center;
        background: #101826;
        color: #e5e7eb;
        font-family: "Segoe UI", "PingFang SC", sans-serif;
      }
      .panel {
        width: min(520px, calc(100vw - 48px));
        padding: 28px 24px;
        border: 1px solid rgba(169, 200, 252, 0.18);
        border-radius: 18px;
        background: linear-gradient(180deg, rgba(22, 33, 62, 0.95), rgba(11, 16, 32, 0.96));
        box-shadow: 0 24px 60px rgba(0, 0, 0, 0.35);
      }
      h1 {
        margin: 0 0 12px;
        font-size: 18px;
      }
      p {
        margin: 0;
        line-height: 1.7;
        color: #cbd5e1;
        white-space: pre-wrap;
      }
    </style>
  </head>
  <body>
    <div class="panel">
      <h1>NoteApp Desktop</h1>
      <p>${message}</p>
    </div>
  </body>
</html>`;
}

function createMainWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1100,
    minHeight: 760,
    show: false,
    backgroundColor: '#101826',
    autoHideMenuBar: true,
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  window.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  window.once('ready-to-show', () => {
    window.show();
  });

  return window;
}

function ensureBridgeProcess() {
  if (bridgeProcess && !bridgeProcess.killed) {
    return bridgeProcess;
  }
  writeDesktopLog(`starting bridge from ${bridgeScriptPath()}`);
  bridgeProcess = spawn(process.execPath, [bridgeScriptPath()], {
    cwd: webAppRoot(),
    env: desktopEnv(),
    stdio: 'inherit',
    windowsHide: true,
  });

  bridgeProcess.once('exit', (code, signal) => {
    const exitedProcess = bridgeProcess;
    bridgeProcess = null;
    const reason = signal ? `signal ${signal}` : `exit code ${code ?? 'unknown'}`;
    writeDesktopLog(`bridge exited: ${reason}`);
    if (isQuitting) {
      return;
    }
    if (mainWindow && !mainWindow.isDestroyed()) {
      const detail = [
        '本地服务已退出，桌面端无法继续工作。',
        `bridge 退出原因：${reason}`,
        '',
        '请检查：',
        '1. Python 是否可用',
        '2. 同步服务环境变量是否正确',
        '3. 端口 3187 是否被占用',
      ].join('\n');
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(createLoadingHtml(detail))}`).catch(() => {});
    }
    if (exitedProcess && code && code !== 0) {
      dialog.showErrorBox('NoteApp Desktop', `本地服务异常退出：${reason}`);
    }
  });

  return bridgeProcess;
}

function ensureRendererProcess() {
  if (rendererProcess && !rendererProcess.killed) {
    return rendererProcess;
  }
  writeDesktopLog(`starting renderer dev server in ${webAppRoot()}`);
  rendererProcess = spawn('npm.cmd', ['run', 'dev', '--', '--host', rendererHost, '--port', String(rendererPort)], {
    cwd: webAppRoot(),
    env: process.env,
    stdio: 'inherit',
    windowsHide: true,
  });
  rendererProcess.once('exit', () => {
    writeDesktopLog('renderer dev server exited');
    rendererProcess = null;
  });
  return rendererProcess;
}

async function waitForBridgeHealth() {
  const startedAt = Date.now();
  let lastError = 'bridge is not ready';
  while (Date.now() - startedAt < healthTimeoutMs) {
    try {
      const response = await fetch(`${bridgeBaseUrl}/health`, { cache: 'no-store' });
      if (response.ok) {
        return;
      }
      lastError = `health returned ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, healthPollIntervalMs));
  }
  throw new Error(lastError);
}

async function loadBridgeDependencies() {
  const response = await fetch(`${bridgeBaseUrl}/health/dependencies`, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`dependency health returned ${response.status}`);
  }
  return response.json();
}

function dependencyErrorMessage(payload) {
  const problems = [];
  if (!payload || typeof payload !== 'object') {
    return '本地服务依赖状态返回无效。';
  }
  if (payload.python && payload.python.ok === false) {
    problems.push(`Python 不可用：${payload.python.message || payload.python.command || 'unknown error'}`);
  }
  if (payload.sync && payload.sync.ok === false) {
    const missing = Array.isArray(payload.sync.missing_env) ? payload.sync.missing_env.join(', ') : 'unknown';
    problems.push(`同步环境变量不完整：${missing}`);
  }
  if (payload.workspace && payload.workspace.configured && payload.workspace.exists === false) {
    problems.push(`工作区路径不存在：${payload.workspace.vault_root || 'unknown path'}`);
  }
  if (problems.length === 0) {
    return null;
  }
  return [
    '桌面端启动完成，但本地依赖未准备好：',
    ...problems.map((problem, index) => `${index + 1}. ${problem}`),
  ].join('\n');
}

async function resolveRendererEntry() {
  if (devRendererUrl) {
    return devRendererUrl;
  }
  const distPath = frontendDistPath();
  if (!existsSync(distPath)) {
    if (app.isPackaged) {
      throw new Error([
        '桌面安装包缺少前端构建产物。',
        `缺少文件：${distPath}`,
      ].join('\n'));
    }
    ensureRendererProcess();
    return fallbackRendererDevUrl;
  }
  return pathToFileURL(distPath).toString();
}

async function waitForRenderer(entryUrl) {
  if (!entryUrl.startsWith('http://')) {
    return;
  }
  const startedAt = Date.now();
  let lastError = 'renderer is not ready';
  while (Date.now() - startedAt < healthTimeoutMs) {
    try {
      const response = await fetch(entryUrl, { cache: 'no-store' });
      if (response.ok) {
        return;
      }
      lastError = `renderer returned ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }
    await new Promise((resolveDelay) => setTimeout(resolveDelay, healthPollIntervalMs));
  }
  throw new Error(`前端入口启动失败：${lastError}`);
}

async function bootstrap() {
  writeDesktopLog(`bootstrap start: packaged=${app.isPackaged}, runtimeRoot=${runtimeRoot()}, webAppRoot=${webAppRoot()}`);
  mainWindow = createMainWindow();
  await mainWindow.loadURL(
    `data:text/html;charset=utf-8,${encodeURIComponent(createLoadingHtml('正在启动本地服务，请稍候...'))}`,
  );

  ensureBridgeProcess();
  await waitForBridgeHealth();
  const dependencies = await loadBridgeDependencies();
  const dependencyError = dependencyErrorMessage(dependencies);
  if (dependencyError) {
    throw new Error(dependencyError);
  }
  const rendererEntry = await resolveRendererEntry();
  writeDesktopLog(`renderer entry resolved: ${rendererEntry}`);
  await waitForRenderer(rendererEntry);
  await mainWindow.loadURL(rendererEntry);
  writeDesktopLog('main window loaded renderer entry');
}

function shutdownBridge() {
  if (!bridgeProcess || bridgeProcess.killed) {
    return;
  }
  bridgeProcess.kill();
}

function shutdownRenderer() {
  if (!rendererProcess || rendererProcess.killed) {
    return;
  }
  rendererProcess.kill();
}

ipcMain.handle('noteapp:select-workspace-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow ?? undefined, {
    title: '选择工作区文件夹',
    buttonLabel: '确认',
    properties: ['openDirectory', 'createDirectory'],
  });
  if (result.canceled) {
    return null;
  }
  return result.filePaths[0] ?? null;
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('before-quit', () => {
  isQuitting = true;
  shutdownBridge();
  shutdownRenderer();
});

app.on('activate', async () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    try {
      await bootstrap();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      dialog.showErrorBox('NoteApp Desktop', message);
    }
  }
});

app.whenReady().then(async () => {
  try {
    await bootstrap();
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    writeDesktopLog(`bootstrap failed: ${message}`);
    if (mainWindow && !mainWindow.isDestroyed()) {
      await mainWindow.loadURL(
        `data:text/html;charset=utf-8,${encodeURIComponent(createLoadingHtml(message))}`,
      ).catch(() => {});
      mainWindow.show();
    }
    dialog.showErrorBox('NoteApp Desktop', message);
  }
});

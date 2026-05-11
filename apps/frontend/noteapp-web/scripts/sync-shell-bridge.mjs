import { createServer } from 'node:http';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const appRoot = resolve(scriptPath, '..', '..');
const defaultSnapshotPath = resolve(appRoot, 'public', 'fixtures', 'live-sync-shell.json');
const defaultSettingsSnapshotPath = resolve(appRoot, 'public', 'fixtures', 'local-settings-snapshot.json');
const defaultWorkspaceFilesPath = resolve(appRoot, 'public', 'fixtures', 'workspace-files.json');
const defaultWorkspaceRootPath = resolve(appRoot, 'public', 'fixtures', 'workspace-root.json');
const host = process.env.NOTEAPP_SYNC_BRIDGE_HOST || '127.0.0.1';
const port = Number(process.env.NOTEAPP_SYNC_BRIDGE_PORT || 3187);
const allowRemoteHost = process.env.NOTEAPP_SYNC_BRIDGE_ALLOW_REMOTE === 'true';
const allowedOrigin = process.env.NOTEAPP_SYNC_BRIDGE_ORIGIN || 'http://127.0.0.1:3000';
const snapshotPath = process.env.NOTEAPP_SYNC_SNAPSHOT_OUTPUT
  ? resolve(process.env.NOTEAPP_SYNC_SNAPSHOT_OUTPUT)
  : defaultSnapshotPath;
const settingsSnapshotPath = process.env.NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT
  ? resolve(process.env.NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT)
  : defaultSettingsSnapshotPath;
const workspaceFilesPath = process.env.NOTEAPP_WORKSPACE_FILES_OUTPUT
  ? resolve(process.env.NOTEAPP_WORKSPACE_FILES_OUTPUT)
  : defaultWorkspaceFilesPath;
const workspaceRootPath = process.env.NOTEAPP_WORKSPACE_ROOT_OUTPUT
  ? resolve(process.env.NOTEAPP_WORKSPACE_ROOT_OUTPUT)
  : defaultWorkspaceRootPath;
const initialVaultRoot = process.env.NOTEAPP_VAULT_ROOT || '';
let selectedVaultRoot = readPersistedWorkspaceRoot() || initialVaultRoot;
const maxRequestBodyBytes = 1_200_000;

const args = new Set(process.argv.slice(2));

function printHelp() {
  console.log(`sync-shell-bridge

Local-only HTTP bridge for the web UI sync shell.

Environment:
  NOTEAPP_SYNC_BRIDGE_HOST       Host, default 127.0.0.1
  NOTEAPP_SYNC_BRIDGE_PORT       Port, default 3187
  NOTEAPP_SYNC_BRIDGE_ALLOW_REMOTE
                                  Set true to allow non-loopback hosts
  NOTEAPP_SYNC_BRIDGE_ORIGIN     CORS origin, default http://127.0.0.1:3000
  NOTEAPP_SYNC_SNAPSHOT_OUTPUT   Output JSON path, default public/fixtures/live-sync-shell.json
  NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT
                                  Output JSON path, default public/fixtures/local-settings-snapshot.json
  NOTEAPP_WORKSPACE_FILES_OUTPUT  Output JSON path, default public/fixtures/workspace-files.json
  NOTEAPP_WORKSPACE_ROOT_OUTPUT   Selected workspace root JSON path, default public/fixtures/workspace-root.json

It forwards to:
  GET  /api/sync/snapshot        npm run sync:snapshot equivalent
  POST /api/sync/actions/:id     npm run sync:action equivalent
  GET  /api/sync/live            Read current live snapshot without running CLI
  GET  /api/settings/snapshot    npm run settings:snapshot equivalent
  POST /api/settings/snapshot    npm run settings:write equivalent
  GET  /api/settings/live        Read current settings snapshot without running CLI
  GET  /api/workspace/files      npm run workspace:files equivalent
  POST /api/workspace/files      Create a local Markdown note
  POST /api/workspace/attachments
                                  Create a local attachment under Attachments/
  PATCH /api/workspace/files/:id Rename a local Markdown note
  DELETE /api/workspace/files/:id
                                  Delete a local Markdown note
  GET  /api/workspace/search?q=term
                                  Rebuild and query local workspace search index
  GET  /api/workspace/files/:id/links
                                  Read outgoing links and backlinks for one workspace file
  GET  /api/workspace/files/:id/draft
                                  Read unsaved draft for one workspace file
  PUT  /api/workspace/files/:id/draft
                                  Atomically write unsaved draft text
  DELETE /api/workspace/files/:id/draft
                                  Clear unsaved draft
  GET  /api/workspace/files/:id/blob
                                  Read a workspace file as base64 for preview
  GET  /api/workspace/live       Read current workspace files without running CLI
  GET  /api/workspace/root       Read selected workspace root
  POST /api/workspace/root       Validate and switch selected workspace root
  POST /api/workspace/select-folder
                                  Open a native folder picker and switch selected workspace root
  POST /api/ai/wiki/compile      Compile deterministic local AI Wiki pages
  POST /api/ai/ask               Answer from local AI Wiki citations
  GET  /health                   Health check
`);
}

function isAllowedOrigin(origin) {
  return !origin || allowedOrigin === '*' || origin === allowedOrigin;
}

function isLoopbackHost(value) {
  return value === '127.0.0.1' || value === 'localhost' || value === '::1';
}

function corsOrigin(origin) {
  if (allowedOrigin === '*') {
    return '*';
  }
  return origin || allowedOrigin;
}

function jsonResponse(request, response, statusCode, payload) {
  response.writeHead(statusCode, {
    'access-control-allow-origin': corsOrigin(request.headers.origin),
    'access-control-allow-methods': 'GET,POST,PUT,PATCH,DELETE,OPTIONS',
    'access-control-allow-headers': 'content-type',
    'cache-control': 'no-store',
    'content-type': 'application/json; charset=utf-8',
  });
  response.end(JSON.stringify(payload, null, 2));
}

function readRequestBody(request) {
  return new Promise((resolveBody, rejectBody) => {
    const chunks = [];
    let size = 0;
    request.on('data', (chunk) => {
      size += chunk.length;
      if (size > maxRequestBodyBytes) {
        rejectBody(new Error('request body is too large'));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => resolveBody(Buffer.concat(chunks).toString('utf8')));
    request.on('error', rejectBody);
  });
}

function readPersistedWorkspaceRoot() {
  if (!existsSync(workspaceRootPath)) {
    return '';
  }
  try {
    const payload = JSON.parse(readFileSync(workspaceRootPath, 'utf8'));
    return typeof payload.vault_root === 'string' ? payload.vault_root : '';
  } catch {
    return '';
  }
}

function persistWorkspaceRoot(vaultRoot) {
  mkdirSync(resolve(workspaceRootPath, '..'), { recursive: true });
  writeFileSync(
    workspaceRootPath,
    `${JSON.stringify(
      {
        schema_version: 'v1',
        vault_root: vaultRoot,
        updated_at_ms: Date.now(),
      },
      null,
      2,
    )}\n`,
    'utf8',
  );
}

function bridgeEnv(extraEnv = {}) {
  return {
    ...process.env,
    NOTEAPP_VAULT_ROOT: selectedVaultRoot,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    ...extraEnv,
  };
}

function runScript(scriptName, extraEnv = {}) {
  if (!selectedVaultRoot || !selectedVaultRoot.trim()) {
    throw new Error('workspace root is not configured');
  }
  const result = spawnSync(process.execPath, [`scripts/${scriptName}`], {
    cwd: appRoot,
    env: bridgeEnv(extraEnv),
    encoding: 'utf8',
    stdout: 'pipe',
    stderr: 'pipe',
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(
      [
        `${scriptName} failed with exit code ${result.status}`,
        result.stdout.trim(),
        result.stderr.trim(),
      ]
        .filter(Boolean)
        .join('\n'),
    );
  }
}

function runDesktopCli(commandArgs) {
  if (!selectedVaultRoot || !selectedVaultRoot.trim()) {
    throw new Error('workspace root is not configured');
  }
  const python = process.env.PYTHON || 'python';
  const result = spawnSync(
    python,
    [
      '-m',
      'clients.desktop.cli',
      '--vault-root',
      selectedVaultRoot,
      '--base-url',
      requireBridgeEnv('NOTEAPP_SYNC_BASE_URL'),
      '--vault-id',
      requireBridgeEnv('NOTEAPP_VAULT_ID'),
      '--device-id',
      requireBridgeEnv('NOTEAPP_DEVICE_ID'),
      ...(process.env.NOTEAPP_BEARER_TOKEN ? [`--bearer-token=${process.env.NOTEAPP_BEARER_TOKEN}`] : []),
      ...commandArgs,
    ],
    {
      cwd: resolve(appRoot, '..', '..', '..'),
      env: bridgeEnv({
        PYTHONPATH: buildPythonPath(),
      }),
      encoding: 'utf8',
      stdout: 'pipe',
      stderr: 'pipe',
    },
  );

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(
      [
        `desktop cli failed with exit code ${result.status}`,
        result.stdout.trim(),
        result.stderr.trim(),
      ]
        .filter(Boolean)
        .join('\n'),
    );
  }
  return result.stdout;
}

function requireBridgeEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim()) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function buildPythonPath() {
  const repoRoot = resolve(appRoot, '..', '..', '..');
  const entries = [
    resolve(repoRoot, 'packages', 'vault-core', 'src'),
    resolve(repoRoot, 'packages', 'ai-core', 'src'),
    repoRoot,
  ];
  if (process.env.PYTHONPATH) {
    entries.push(process.env.PYTHONPATH);
  }
  return entries.join(process.platform === 'win32' ? ';' : ':');
}

function ensureWorkspaceInitialized() {
  runDesktopCli([
    'init',
    '--now-ms',
    process.env.NOTEAPP_INIT_NOW_MS || String(Date.now()),
  ]);
  runDesktopCli(['import-existing-workspace-files']);
}

function workspaceRootPayload() {
  const exists = Boolean(selectedVaultRoot) && existsSync(selectedVaultRoot);
  const initialized = exists && existsSync(resolve(selectedVaultRoot, '.noteapp', 'filemap.json'));
  return {
    schema_version: 'v1',
    vault_root: selectedVaultRoot,
    source: selectedVaultRoot === initialVaultRoot ? 'environment' : 'runtime',
    exists,
    initialized,
    config_path: workspaceRootPath,
  };
}

function normalizeWorkspaceRootPayload(payload) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('workspace root request must contain an object');
  }
  return normalizeWorkspaceRootPath(payload.vault_root);
}

function normalizeWorkspaceRootPath(value) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error('workspace root must be a non-empty string');
  }
  const resolved = resolve(value.trim());
  if (!existsSync(resolved)) {
    throw new Error(`workspace root does not exist: ${resolved}`);
  }
  if (!statSync(resolved).isDirectory()) {
    throw new Error(`workspace root is not a directory: ${resolved}`);
  }
  return resolved;
}

function selectWorkspaceRootWithDialog() {
  if (process.env.NOTEAPP_WORKSPACE_SELECT_ROOT) {
    return normalizeWorkspaceRootPath(process.env.NOTEAPP_WORKSPACE_SELECT_ROOT);
  }
  if (process.platform !== 'win32') {
    throw new Error('workspace folder selection is only implemented for the Windows bridge');
  }

  const script = [
    'Add-Type -AssemblyName System.Windows.Forms',
    '[Console]::OutputEncoding = [System.Text.Encoding]::UTF8',
    '$dialog = New-Object System.Windows.Forms.FolderBrowserDialog',
    '$dialog.Description = "Select workspace folder"',
    '$dialog.ShowNewFolderButton = $true',
    'if ($env:NOTEAPP_WORKSPACE_DIALOG_INITIAL -and (Test-Path -LiteralPath $env:NOTEAPP_WORKSPACE_DIALOG_INITIAL)) { $dialog.SelectedPath = $env:NOTEAPP_WORKSPACE_DIALOG_INITIAL }',
    '$result = $dialog.ShowDialog()',
    'if ($result -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $dialog.SelectedPath; exit 0 }',
    'exit 2',
  ].join('; ');

  const result = spawnSync('powershell.exe', ['-NoProfile', '-STA', '-Command', script], {
    env: {
      ...process.env,
      NOTEAPP_WORKSPACE_DIALOG_INITIAL: selectedVaultRoot,
    },
    encoding: 'utf8',
    stdout: 'pipe',
    stderr: 'pipe',
  });
  if (result.error) {
    throw result.error;
  }
  if (result.status === 2) {
    throw new Error('workspace folder selection was cancelled');
  }
  if (result.status !== 0) {
    throw new Error(
      [
        `workspace folder selection failed with exit code ${result.status}`,
        result.stdout.trim(),
        result.stderr.trim(),
      ]
        .filter(Boolean)
        .join('\n'),
    );
  }
  return normalizeWorkspaceRootPath(result.stdout.trim());
}

function applyWorkspaceRoot(vaultRoot) {
  selectedVaultRoot = vaultRoot;
  persistWorkspaceRoot(selectedVaultRoot);
  ensureWorkspaceInitialized();
  runScript('write-local-settings-snapshot.mjs', {
    NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT: settingsSnapshotPath,
  });
  runScript('write-workspace-files.mjs', {
    NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
  });
  runScript('write-live-sync-shell.mjs', {
    NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
  });
  return readSettingsSnapshot();
}

function readSnapshot() {
  if (!existsSync(snapshotPath)) {
    throw new Error(`sync snapshot was not found: ${snapshotPath}`);
  }
  return JSON.parse(readFileSync(snapshotPath, 'utf8'));
}

function readSettingsSnapshot() {
  if (!existsSync(settingsSnapshotPath)) {
    throw new Error(`settings snapshot was not found: ${settingsSnapshotPath}`);
  }
  return JSON.parse(readFileSync(settingsSnapshotPath, 'utf8'));
}

function readWorkspaceFiles() {
  if (!existsSync(workspaceFilesPath)) {
    throw new Error(`workspace files snapshot was not found: ${workspaceFilesPath}`);
  }
  return JSON.parse(readFileSync(workspaceFilesPath, 'utf8'));
}

function actionIdFromPath(pathname) {
  const prefix = '/api/sync/actions/';
  if (!pathname.startsWith(prefix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length);
  if (!encoded) {
    return null;
  }
  return decodeURIComponent(encoded);
}

function workspaceContentFileIdFromPath(pathname) {
  const prefix = '/api/workspace/files/';
  const suffix = '/content';
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length, -suffix.length);
  if (!encoded) {
    return null;
  }
  return decodeURIComponent(encoded);
}

function workspaceDraftFileIdFromPath(pathname) {
  const prefix = '/api/workspace/files/';
  const suffix = '/draft';
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length, -suffix.length);
  if (!encoded) {
    return null;
  }
  return decodeURIComponent(encoded);
}

function workspaceBlobFileIdFromPath(pathname) {
  const prefix = '/api/workspace/files/';
  const suffix = '/blob';
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length, -suffix.length);
  if (!encoded) {
    return null;
  }
  return decodeURIComponent(encoded);
}

function workspaceLinksFileIdFromPath(pathname) {
  const prefix = '/api/workspace/files/';
  const suffix = '/links';
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length, -suffix.length);
  if (!encoded) {
    return null;
  }
  return decodeURIComponent(encoded);
}

function workspaceFileIdFromPath(pathname) {
  const prefix = '/api/workspace/files/';
  if (!pathname.startsWith(prefix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length);
  if (!encoded || encoded.includes('/')) {
    return null;
  }
  return decodeURIComponent(encoded);
}

function workspaceNoteFileIdFromPath(pathname) {
  const prefix = '/api/workspace/notes/';
  if (!pathname.startsWith(prefix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length);
  if (!encoded || encoded.includes('/')) {
    return null;
  }
  return decodeURIComponent(encoded);
}

function workspaceNoteLinksFileIdFromPath(pathname) {
  const prefix = '/api/workspace/notes/';
  const suffix = '/links';
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length, -suffix.length);
  if (!encoded) {
    return null;
  }
  return decodeURIComponent(encoded);
}

function errorPayload(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (
    message.includes('request body is too large')
    || message.includes('Unexpected end of JSON input')
    || message.includes('workspace root')
  ) {
    return {
      statusCode: 400,
      payload: {
        code: 'invalid_request',
        message,
      },
    };
  }
  if (message.includes('local settings') || message.includes('JSON file must contain an object')) {
    return {
      statusCode: 400,
      payload: {
        code: 'invalid_settings',
        message,
      },
    };
  }
  if (message.includes('sync action not found')) {
    return {
      statusCode: 404,
      payload: {
        code: 'sync_action_not_found',
        message,
      },
    };
  }
  return {
    statusCode: 500,
    payload: {
      code: 'sync_bridge_error',
      message,
    },
  };
}

if (args.has('--help') || args.has('-h')) {
  printHelp();
  process.exit(0);
}

if (!allowRemoteHost && !isLoopbackHost(host)) {
  console.error(
    `Refusing to bind sync bridge to non-loopback host ${host}. ` +
      'Set NOTEAPP_SYNC_BRIDGE_ALLOW_REMOTE=true to override.',
  );
  process.exit(1);
}

const server = createServer(async (request, response) => {
  if (!isAllowedOrigin(request.headers.origin)) {
    response.writeHead(403, {
      'cache-control': 'no-store',
      'content-type': 'application/json; charset=utf-8',
    });
    response.end(
      JSON.stringify(
        {
          code: 'origin_not_allowed',
          message: 'Origin is not allowed to use the sync bridge.',
        },
        null,
        2,
      ),
    );
    return;
  }

  if (request.method === 'OPTIONS') {
    jsonResponse(request, response, 204, {});
    return;
  }

  const url = new URL(request.url || '/', `http://${host}:${port}`);
  const routePathname = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, '') : url.pathname;

  try {
    if (request.method === 'GET' && url.pathname === '/health') {
      jsonResponse(request, response, 200, {
        ok: true,
        host,
        port,
        allowRemoteHost,
        snapshotPath,
        settingsSnapshotPath,
        workspaceFilesPath,
        workspaceRootPath,
        vaultRoot: selectedVaultRoot,
        allowedOrigin,
      });
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/sync/live') {
      jsonResponse(request, response, 200, readSnapshot());
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/sync/snapshot') {
      runScript('write-live-sync-shell.mjs', {
        NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
      });
      jsonResponse(request, response, 200, readSnapshot());
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/settings/live') {
      jsonResponse(request, response, 200, readSettingsSnapshot());
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/settings/snapshot') {
      runScript('write-local-settings-snapshot.mjs', {
        NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT: settingsSnapshotPath,
      });
      jsonResponse(request, response, 200, readSettingsSnapshot());
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/settings/snapshot') {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-settings-'));
      try {
        const inputPath = resolve(tempRoot, 'settings.json');
        writeFileSync(inputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
        runScript('write-local-settings.mjs', {
          NOTEAPP_SETTINGS_INPUT_JSON: inputPath,
          NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT: settingsSnapshotPath,
        });
      } finally {
        rmSync(tempRoot, { recursive: true, force: true });
      }
      jsonResponse(request, response, 200, readSettingsSnapshot());
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/workspace/live') {
      jsonResponse(request, response, 200, readWorkspaceFiles());
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/workspace/root') {
      jsonResponse(request, response, 200, workspaceRootPayload());
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/workspace/root') {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      jsonResponse(request, response, 200, applyWorkspaceRoot(normalizeWorkspaceRootPayload(payload)));
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/workspace/select-folder') {
      jsonResponse(request, response, 200, applyWorkspaceRoot(selectWorkspaceRootWithDialog()));
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/workspace/files') {
      runDesktopCli(['import-existing-workspace-files']);
      runScript('write-workspace-files.mjs', {
        NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
      });
      jsonResponse(request, response, 200, readWorkspaceFiles());
      return;
    }

    if (request.method === 'GET' && routePathname === '/api/workspace/trash') {
      const stdout = runDesktopCli(['workspace-trash']);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (request.method === 'POST' && routePathname === '/api/workspace/trash/empty') {
      const stdout = runDesktopCli(['empty-workspace-trash']);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (
      request.method === 'POST'
      && (url.pathname === '/api/workspace/files' || url.pathname === '/api/workspace/notes')
    ) {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      if (!payload || typeof payload.path !== 'string') {
        throw new Error('workspace note create request must include path');
      }
      const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-workspace-create-'));
      try {
        const inputPath = resolve(tempRoot, 'content.txt');
        writeFileSync(inputPath, typeof payload.text === 'string' ? payload.text : '', 'utf8');
        const stdout = runDesktopCli([
          'create-workspace-note',
          '--path',
          payload.path,
          '--input-text-file',
          inputPath,
        ]);
        runScript('write-workspace-files.mjs', {
          NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
        });
        runScript('write-live-sync-shell.mjs', {
          NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
        });
        jsonResponse(request, response, 200, JSON.parse(stdout));
      } finally {
        rmSync(tempRoot, { recursive: true, force: true });
      }
      return;
    }

    if (request.method === 'POST' && routePathname === '/api/workspace/attachments') {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      if (!payload || typeof payload.file_name !== 'string' || typeof payload.content_base64 !== 'string') {
        throw new Error('workspace attachment create request must include file_name and content_base64');
      }
      const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-workspace-attachment-'));
      try {
        const inputPath = resolve(tempRoot, 'attachment.b64');
        writeFileSync(inputPath, payload.content_base64, 'utf8');
        const stdout = runDesktopCli([
          'create-workspace-attachment',
          '--file-name',
          payload.file_name,
          '--input-base64-file',
          inputPath,
        ]);
        runScript('write-workspace-files.mjs', {
          NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
        });
        runScript('write-live-sync-shell.mjs', {
          NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
        });
        jsonResponse(request, response, 200, JSON.parse(stdout));
      } finally {
        rmSync(tempRoot, { recursive: true, force: true });
      }
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/workspace/search') {
      const query = url.searchParams.get('q') || '';
      const limit = url.searchParams.get('limit') || '20';
      const stdout = runDesktopCli(['search-workspace', '--query', query, '--limit', limit]);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (request.method === 'POST' && routePathname === '/api/ai/wiki/compile') {
      const stdout = runDesktopCli(['compile-ai-wiki']);
      runScript('write-workspace-files.mjs', {
        NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
      });
      runScript('write-live-sync-shell.mjs', {
        NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
      });
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (request.method === 'POST' && routePathname === '/api/ai/ask') {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      if (!payload || typeof payload.question !== 'string') {
        throw new Error('AI ask request must include question');
      }
      const limit = Number.isFinite(Number(payload.limit)) ? String(Number(payload.limit)) : '5';
      const stdout = runDesktopCli(['ask-ai-wiki', '--question', payload.question, '--limit', limit]);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (request.method === 'POST' && routePathname === '/api/ai/provider/health') {
      const stdout = runDesktopCli(['ai-provider-health']);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const workspaceLinksFileId = (
      workspaceLinksFileIdFromPath(url.pathname)
      || workspaceNoteLinksFileIdFromPath(url.pathname)
    );
    if (request.method === 'GET' && workspaceLinksFileId) {
      const stdout = runDesktopCli(['workspace-links', '--file-id', workspaceLinksFileId]);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const workspaceContentFileId = workspaceContentFileIdFromPath(url.pathname);
    if (request.method === 'GET' && workspaceContentFileId) {
      const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-workspace-content-'));
      try {
        const outputPath = resolve(tempRoot, 'workspace-file-content.json');
        runScript('write-workspace-file-content.mjs', {
          NOTEAPP_WORKSPACE_FILE_ID: workspaceContentFileId,
          NOTEAPP_WORKSPACE_FILE_CONTENT_OUTPUT: outputPath,
        });
        jsonResponse(request, response, 200, JSON.parse(readFileSync(outputPath, 'utf8')));
      } finally {
        rmSync(tempRoot, { recursive: true, force: true });
      }
      return;
    }

    const workspaceBlobFileId = workspaceBlobFileIdFromPath(url.pathname);
    if (request.method === 'GET' && workspaceBlobFileId) {
      const stdout = runDesktopCli(['workspace-file-blob', '--file-id', workspaceBlobFileId]);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (request.method === 'PUT' && workspaceContentFileId) {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      if (!payload || typeof payload.text !== 'string') {
        throw new Error('workspace file content request must include text');
      }
      const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-workspace-write-'));
      try {
        const inputPath = resolve(tempRoot, 'content.txt');
        const outputPath = resolve(tempRoot, 'workspace-file-content.json');
        writeFileSync(inputPath, payload.text, 'utf8');
        runScript('write-workspace-file-content-update.mjs', {
          NOTEAPP_WORKSPACE_FILE_ID: workspaceContentFileId,
          NOTEAPP_WORKSPACE_FILE_TEXT_INPUT: inputPath,
          NOTEAPP_WORKSPACE_FILE_CONTENT_OUTPUT: outputPath,
        });
        runScript('write-workspace-files.mjs', {
          NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
        });
        runScript('write-live-sync-shell.mjs', {
          NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
        });
        jsonResponse(request, response, 200, JSON.parse(readFileSync(outputPath, 'utf8')));
      } finally {
        rmSync(tempRoot, { recursive: true, force: true });
      }
      return;
    }

    const workspaceFileId = workspaceFileIdFromPath(url.pathname) || workspaceNoteFileIdFromPath(url.pathname);
    if (request.method === 'PATCH' && workspaceFileId) {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      if (!payload || typeof payload.path !== 'string') {
        throw new Error('workspace note rename request must include path');
      }
      const stdout = runDesktopCli([
        'rename-workspace-note',
        '--file-id',
        workspaceFileId,
        '--path',
        payload.path,
      ]);
      runScript('write-workspace-files.mjs', {
        NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
      });
      runScript('write-live-sync-shell.mjs', {
        NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
      });
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (request.method === 'DELETE' && workspaceFileId) {
      const stdout = runDesktopCli(['delete-workspace-note', '--file-id', workspaceFileId]);
      runScript('write-workspace-files.mjs', {
        NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
      });
      runScript('write-live-sync-shell.mjs', {
        NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
      });
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const trashItemPrefix = '/api/workspace/trash/';
    if (routePathname.startsWith(trashItemPrefix)) {
      const tail = routePathname.slice(trashItemPrefix.length);
      const parts = tail.split('/').filter(Boolean);
      if (parts.length === 2 && parts[1] === 'restore' && request.method === 'POST') {
        const stdout = runDesktopCli(['restore-workspace-trash', '--file-id', decodeURIComponent(parts[0])]);
        runScript('write-workspace-files.mjs', {
          NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
        });
        jsonResponse(request, response, 200, JSON.parse(stdout));
        return;
      }
      if (parts.length === 1 && request.method === 'DELETE') {
        const stdout = runDesktopCli(['purge-workspace-trash', '--file-id', decodeURIComponent(parts[0])]);
        jsonResponse(request, response, 200, JSON.parse(stdout));
        return;
      }
    }

    const workspaceDraftFileId = workspaceDraftFileIdFromPath(url.pathname);
    if (request.method === 'GET' && workspaceDraftFileId) {
      const stdout = runDesktopCli(['workspace-file-draft', '--file-id', workspaceDraftFileId]);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (request.method === 'PUT' && workspaceDraftFileId) {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      if (!payload || typeof payload.text !== 'string') {
        throw new Error('workspace file draft request must include text');
      }
      const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-workspace-draft-'));
      try {
        const inputPath = resolve(tempRoot, 'draft.txt');
        writeFileSync(inputPath, payload.text, 'utf8');
        const stdout = runDesktopCli([
          'write-workspace-file-draft',
          '--file-id',
          workspaceDraftFileId,
          '--input-text-file',
          inputPath,
        ]);
        jsonResponse(request, response, 200, JSON.parse(stdout));
      } finally {
        rmSync(tempRoot, { recursive: true, force: true });
      }
      return;
    }

    if (request.method === 'DELETE' && workspaceDraftFileId) {
      const stdout = runDesktopCli(['clear-workspace-file-draft', '--file-id', workspaceDraftFileId]);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const actionId = actionIdFromPath(url.pathname);
    if (request.method === 'POST' && actionId) {
      runScript('execute-sync-action.mjs', {
        NOTEAPP_SYNC_ACTION_ID: actionId,
        NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
      });
      jsonResponse(request, response, 200, readSnapshot());
      return;
    }

    jsonResponse(request, response, 404, {
      code: 'not_found',
      message: 'Route was not found.',
    });
  } catch (error) {
    const resolvedError = errorPayload(error);
    jsonResponse(request, response, resolvedError.statusCode, resolvedError.payload);
  }
});

server.listen(port, host, () => {
  console.log(`noteapp-web sync bridge running at http://${host}:${port}/`);
});

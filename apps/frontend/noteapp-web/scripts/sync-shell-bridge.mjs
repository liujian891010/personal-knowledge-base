import { createServer } from 'node:http';
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createHash, randomUUID } from 'node:crypto';

const scriptPath = fileURLToPath(import.meta.url);
const appRoot = resolve(scriptPath, '..', '..');
const repoRoot = process.env.NOTEAPP_REPO_ROOT
  ? resolve(process.env.NOTEAPP_REPO_ROOT)
  : resolve(appRoot, '..', '..', '..');
const defaultSnapshotPath = resolve(appRoot, 'public', 'fixtures', 'live-sync-shell.json');
const defaultSettingsSnapshotPath = resolve(appRoot, 'public', 'fixtures', 'local-settings-snapshot.json');
const defaultAuthSessionPath = resolve(appRoot, 'public', 'fixtures', 'auth-session.json');
const defaultWorkspaceFilesPath = resolve(appRoot, 'public', 'fixtures', 'workspace-files.json');
const defaultWorkspaceRootPath = resolve(appRoot, 'public', 'fixtures', 'workspace-root.json');
const defaultWorkspaceRegistryPath = resolve(appRoot, 'public', 'fixtures', 'workspace-registry.json');
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
const authSessionPath = process.env.NOTEAPP_AUTH_SESSION_OUTPUT
  ? resolve(process.env.NOTEAPP_AUTH_SESSION_OUTPUT)
  : defaultAuthSessionPath;
const workspaceFilesPath = process.env.NOTEAPP_WORKSPACE_FILES_OUTPUT
  ? resolve(process.env.NOTEAPP_WORKSPACE_FILES_OUTPUT)
  : defaultWorkspaceFilesPath;
const workspaceRootPath = process.env.NOTEAPP_WORKSPACE_ROOT_OUTPUT
  ? resolve(process.env.NOTEAPP_WORKSPACE_ROOT_OUTPUT)
  : defaultWorkspaceRootPath;
const workspaceRegistryPath = process.env.NOTEAPP_WORKSPACE_REGISTRY_OUTPUT
  ? resolve(process.env.NOTEAPP_WORKSPACE_REGISTRY_OUTPUT)
  : defaultWorkspaceRegistryPath;
const initialVaultRoot = process.env.NOTEAPP_VAULT_ROOT || '';
const initialWorkspaceState = readInitialWorkspaceState();
let workspaceRegistry = initialWorkspaceState.registry;
let activeWorkspaceId = initialWorkspaceState.activeWorkspaceId;
let selectedVaultRoot = initialWorkspaceState.selectedVaultRoot;
const maxRequestBodyBytes = 5_000_000;
let cachedSyncSession = null;

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
  NOTEAPP_AUTH_SESSION_OUTPUT     Auth session JSON path, default public/fixtures/auth-session.json
  NOTEAPP_WORKSPACE_FILES_OUTPUT  Output JSON path, default public/fixtures/workspace-files.json
  NOTEAPP_WORKSPACE_ROOT_OUTPUT   Selected workspace root JSON path, default public/fixtures/workspace-root.json
  NOTEAPP_WORKSPACE_REGISTRY_OUTPUT
                                  Workspace registry JSON path, default public/fixtures/workspace-registry.json

It forwards to:
  GET  /api/sync/snapshot        npm run sync:snapshot equivalent
  POST /api/sync/actions/:id     npm run sync:action equivalent
  GET  /api/sync/live            Read current live snapshot without running CLI
  GET  /api/settings/snapshot    npm run settings:snapshot equivalent
  POST /api/settings/snapshot    npm run settings:write equivalent
  GET  /api/settings/live        Read current settings snapshot without running CLI
  GET  /api/crypto/status        Read local E2EE unlock state
  POST /api/crypto/unlock        Store a local E2EE vault key
  POST /api/crypto/lock          Remove the local E2EE vault key
  POST /api/crypto/recovery/export
                                  Generate an offline E2EE recovery package
  POST /api/crypto/recovery/import
                                  Import an offline E2EE recovery package
  GET  /api/auth/session         Read persisted local login session
  POST /api/auth/session         Persist local login session
  DELETE /api/auth/session       Clear local login session
  GET  /api/devices              List current vault devices and ack state
  POST /api/devices/heartbeat    Refresh current device heartbeat
  DELETE /api/devices/:id        Revoke one device
  GET  /api/workspace/files      npm run workspace:files equivalent
  POST /api/workspace/files      Create a local Markdown note
  POST /api/workspace/attachments
                                  Create a local attachment under Attachments/
  PATCH /api/workspace/files/:id Rename a local Markdown note
  POST /api/workspace/files/:id/move
                                  Move a local Markdown note across folders.
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
  GET  /api/workspaces           Read registered workspaces and active workspace
  POST /api/workspaces           Register a workspace by path
  POST /api/workspaces/select-folder
                                  Open a native folder picker and register/activate workspace
  PATCH /api/workspaces/:id      Rename a registered workspace
  DELETE /api/workspaces/:id     Remove a registered workspace
  POST /api/workspaces/:id/activate
                                  Activate a registered workspace
  POST /api/ai/wiki/compile      Compile deterministic local AI Wiki pages
  POST /api/ai/ask               Answer from local AI Wiki citations
  POST /api/ai/context-task      Run an AI task with selected workspace context
  POST /api/ai/writeback/preview Preview an AI writeback without changing files
  POST /api/ai/writeback/apply   Apply a confirmed AI writeback
  GET  /api/ai/chat-sessions     List local AI document sessions
  POST /api/ai/chat-sessions     Create a local AI document session
  GET  /api/ai/chat-sessions/:id Read a local AI document session
  PUT  /api/ai/chat-sessions/:id Update a local AI document session
  DELETE /api/ai/chat-sessions/:id
                                  Delete a local AI document session
  POST /api/ai/provider/health   Test configured AI provider
  GET  /health                   Health check
  GET  /health/dependencies      Dependency health summary
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

function emptyWorkspaceRegistry() {
  return {
    schema_version: 'v1',
    active_workspace_id: null,
    workspaces: [],
  };
}

function normalizeRegisteredWorkspace(workspace) {
  if (!workspace || typeof workspace !== 'object') {
    return null;
  }
  const id = typeof workspace.id === 'string' && workspace.id.trim()
    ? workspace.id.trim()
    : `ws_${Date.now()}_${randomUUID().slice(0, 8)}`;
  const vaultRoot = typeof workspace.vault_root === 'string' ? workspace.vault_root.trim() : '';
  if (!vaultRoot) {
    return null;
  }
  const createdAt = Number.isFinite(Number(workspace.created_at_ms))
    ? Number(workspace.created_at_ms)
    : Date.now();
  const lastOpenedAt = Number.isFinite(Number(workspace.last_opened_at_ms))
    ? Number(workspace.last_opened_at_ms)
    : createdAt;
  return {
    id,
    name: normalizeWorkspaceName(workspace.name, vaultRoot),
    vault_root: vaultRoot,
    created_at_ms: createdAt,
    last_opened_at_ms: lastOpenedAt,
  };
}

function normalizeWorkspaceRegistry(payload) {
  const registry = emptyWorkspaceRegistry();
  if (!payload || typeof payload !== 'object') {
    return registry;
  }
  const workspaces = Array.isArray(payload.workspaces)
    ? payload.workspaces.map(normalizeRegisteredWorkspace).filter(Boolean)
    : [];
  const activeWorkspaceIdCandidate = typeof payload.active_workspace_id === 'string'
    ? payload.active_workspace_id
    : null;
  registry.workspaces = workspaces;
  registry.active_workspace_id = workspaces.some((workspace) => workspace.id === activeWorkspaceIdCandidate)
    ? activeWorkspaceIdCandidate
    : (workspaces[0]?.id ?? null);
  return registry;
}

function normalizeWorkspaceName(name, vaultRoot) {
  if (typeof name === 'string' && name.trim()) {
    return name.trim().slice(0, 80);
  }
  return basename(vaultRoot.replace(/[\\/]+$/, '')) || vaultRoot;
}

function readInitialWorkspaceState() {
  if (existsSync(workspaceRegistryPath)) {
    try {
      const registry = normalizeWorkspaceRegistry(JSON.parse(readFileSync(workspaceRegistryPath, 'utf8')));
      const activeWorkspace = registry.workspaces.find((workspace) => workspace.id === registry.active_workspace_id) ?? null;
      return {
        registry,
        activeWorkspaceId: activeWorkspace?.id ?? null,
        selectedVaultRoot: activeWorkspace?.vault_root ?? '',
      };
    } catch {
      // Fall through to compatibility migration.
    }
  }

  const legacyWorkspaceRoot = readPersistedWorkspaceRoot() || initialVaultRoot;
  if (legacyWorkspaceRoot) {
    const registry = emptyWorkspaceRegistry();
    const workspace = normalizeRegisteredWorkspace({
      id: `ws_${Date.now()}_${randomUUID().slice(0, 8)}`,
      name: normalizeWorkspaceName('', legacyWorkspaceRoot),
      vault_root: legacyWorkspaceRoot,
      created_at_ms: Date.now(),
      last_opened_at_ms: Date.now(),
    });
    if (workspace) {
      registry.workspaces = [workspace];
      registry.active_workspace_id = workspace.id;
    }
    persistWorkspaceRegistry(registry);
    return {
      registry,
      activeWorkspaceId: registry.active_workspace_id,
      selectedVaultRoot: workspace?.vault_root ?? '',
    };
  }

  return {
    registry: emptyWorkspaceRegistry(),
    activeWorkspaceId: null,
    selectedVaultRoot: '',
  };
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

function persistWorkspaceRegistry(registry = workspaceRegistry) {
  mkdirSync(resolve(workspaceRegistryPath, '..'), { recursive: true });
  writeFileSync(workspaceRegistryPath, `${JSON.stringify(registry, null, 2)}\n`, 'utf8');
}

function requireWorkspaceRoot() {
  if (!selectedVaultRoot || !selectedVaultRoot.trim()) {
    throw new Error('workspace root is not configured');
  }
  return selectedVaultRoot;
}

function findWorkspaceById(workspaceId) {
  return workspaceRegistry.workspaces.find((workspace) => workspace.id === workspaceId) ?? null;
}

function findWorkspaceByRoot(vaultRoot) {
  return workspaceRegistry.workspaces.find((workspace) => workspace.vault_root === vaultRoot) ?? null;
}

function workspaceRegistrationSummary(workspace) {
  const exists = existsSync(workspace.vault_root);
  const initialized = exists && existsSync(resolve(workspace.vault_root, '.noteapp', 'filemap.json'));
  return {
    ...workspace,
    exists,
    initialized,
    is_active: workspace.id === activeWorkspaceId,
  };
}

function workspaceRegistryPayload() {
  return {
    schema_version: 'v1',
    active_workspace_id: activeWorkspaceId,
    config_path: workspaceRegistryPath,
    workspaces: workspaceRegistry.workspaces.map(workspaceRegistrationSummary),
  };
}

function refreshWorkspaceArtifacts() {
  if (!selectedVaultRoot || !selectedVaultRoot.trim()) {
    return;
  }
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
}

function refreshWorkspaceArtifactsBestEffort() {
  try {
    refreshWorkspaceArtifacts();
    return null;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    console.error(`[workspace-artifacts] ${message}`);
    return message;
  }
}

function activateWorkspaceById(workspaceId, options = {}) {
  const workspace = findWorkspaceById(workspaceId);
  if (!workspace) {
    throw Object.assign(new Error('workspace was not found'), { statusCode: 404 });
  }
  activeWorkspaceId = workspace.id;
  selectedVaultRoot = workspace.vault_root;
  workspaceRegistry = {
    ...workspaceRegistry,
    active_workspace_id: workspace.id,
    workspaces: workspaceRegistry.workspaces.map((item) => (
      item.id === workspace.id
        ? { ...item, last_opened_at_ms: Date.now() }
        : item
    )),
  };
  persistWorkspaceRegistry();
  persistWorkspaceRoot(selectedVaultRoot);
  if (options.refreshArtifacts !== false) {
    const refreshError = options.bestEffortArtifacts
      ? refreshWorkspaceArtifactsBestEffort()
      : (refreshWorkspaceArtifacts(), null);
    if (refreshError) {
      return {
        ...workspaceRegistryPayload(),
        warning: refreshError,
      };
    }
  }
  return workspaceRegistryPayload();
}

function registerWorkspace(vaultRoot, options = {}) {
  const normalizedVaultRoot = normalizeWorkspaceRootPath(vaultRoot);
  const existingWorkspace = findWorkspaceByRoot(normalizedVaultRoot);
  if (existingWorkspace) {
    if (options.activate !== false) {
      activateWorkspaceById(existingWorkspace.id, options);
    }
    return findWorkspaceById(existingWorkspace.id);
  }
  const workspace = normalizeRegisteredWorkspace({
    id: `ws_${Date.now()}_${randomUUID().slice(0, 8)}`,
    name: options.name,
    vault_root: normalizedVaultRoot,
    created_at_ms: Date.now(),
    last_opened_at_ms: Date.now(),
  });
  workspaceRegistry = {
    ...workspaceRegistry,
    workspaces: [...workspaceRegistry.workspaces, workspace],
    active_workspace_id: options.activate === false
      ? workspaceRegistry.active_workspace_id
      : workspace.id,
  };
  persistWorkspaceRegistry();
  if (options.activate === false) {
    return workspace;
  }
  activateWorkspaceById(workspace.id, options);
  return findWorkspaceById(workspace.id);
}

function renameWorkspaceRegistration(workspaceId, name) {
  const workspace = findWorkspaceById(workspaceId);
  if (!workspace) {
    throw Object.assign(new Error('workspace was not found'), { statusCode: 404 });
  }
  workspaceRegistry = {
    ...workspaceRegistry,
    workspaces: workspaceRegistry.workspaces.map((item) => (
      item.id === workspaceId
        ? { ...item, name: normalizeWorkspaceName(name, item.vault_root) }
        : item
    )),
  };
  persistWorkspaceRegistry();
  return workspaceRegistryPayload();
}

function deleteWorkspaceRegistration(workspaceId) {
  const workspace = findWorkspaceById(workspaceId);
  if (!workspace) {
    throw Object.assign(new Error('workspace was not found'), { statusCode: 404 });
  }
  const workspaceIndex = workspaceRegistry.workspaces.findIndex((item) => item.id === workspaceId);
  const remainingWorkspaces = workspaceRegistry.workspaces.filter((item) => item.id !== workspaceId);
  let nextActiveWorkspaceId = activeWorkspaceId;
  if (activeWorkspaceId === workspaceId) {
    const nextWorkspace = workspaceIndex >= 0
      ? (workspaceRegistry.workspaces[workspaceIndex + 1] ?? workspaceRegistry.workspaces[workspaceIndex - 1] ?? null)
      : null;
    nextActiveWorkspaceId = nextWorkspace && nextWorkspace.id !== workspaceId
      ? nextWorkspace.id
      : (remainingWorkspaces[0]?.id ?? null);
  }
  workspaceRegistry = {
    ...workspaceRegistry,
    active_workspace_id: nextActiveWorkspaceId,
    workspaces: remainingWorkspaces,
  };
  activeWorkspaceId = nextActiveWorkspaceId;
  selectedVaultRoot = nextActiveWorkspaceId ? (findWorkspaceById(nextActiveWorkspaceId)?.vault_root ?? '') : '';
  persistWorkspaceRegistry();
  persistWorkspaceRoot(selectedVaultRoot);
  if (nextActiveWorkspaceId) {
    refreshWorkspaceArtifacts();
  }
  return workspaceRegistryPayload();
}

function aiChatRootPath() {
  return resolve(requireWorkspaceRoot(), '.noteapp', 'ai-chats');
}

function aiChatSessionsPath() {
  return resolve(aiChatRootPath(), 'sessions');
}

function aiChatIndexPath() {
  return resolve(aiChatRootPath(), 'index.json');
}

function ensureAiChatSessionsDir() {
  const sessionsPath = aiChatSessionsPath();
  mkdirSync(sessionsPath, { recursive: true });
  return sessionsPath;
}

function normalizeAiChatSessionId(value) {
  if (typeof value !== 'string' || !/^[a-zA-Z0-9_-]+$/.test(value)) {
    throw new Error('AI chat session id is invalid');
  }
  return value;
}

function aiChatSessionPath(sessionId) {
  return resolve(ensureAiChatSessionsDir(), `${normalizeAiChatSessionId(sessionId)}.json`);
}

function summarizeAiChatSession(session) {
  return {
    id: session.id,
    title: session.title,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    messageCount: Array.isArray(session.messages) ? session.messages.length : 0,
    contextFileCount: session.context && Array.isArray(session.context.fileIds)
      ? session.context.fileIds.length
      : 0,
  };
}

function normalizeAiChatSession(payload, existingSession = null) {
  const now = Date.now();
  const source = payload && typeof payload === 'object' ? payload : {};
  const id = existingSession?.id || normalizeAiChatSessionId(
    typeof source.id === 'string' ? source.id : `chat_${now}_${randomUUID().slice(0, 8)}`,
  );
  const title = typeof source.title === 'string' && source.title.trim()
    ? source.title.trim().slice(0, 120)
    : existingSession?.title || '新的 AI 文档会话';
  const createdAt = Number.isFinite(Number(existingSession?.createdAt))
    ? Number(existingSession.createdAt)
    : now;
  const updatedAt = Number.isFinite(Number(source.updatedAt)) ? Number(source.updatedAt) : now;
  const messages = Array.isArray(source.messages) ? source.messages : existingSession?.messages || [];
  const context = Object.prototype.hasOwnProperty.call(source, 'context')
    ? source.context
    : existingSession?.context ?? null;

  return {
    schemaVersion: 'v1',
    id,
    title,
    createdAt,
    updatedAt,
    context,
    messages,
  };
}

function readAiChatSession(sessionId) {
  const path = aiChatSessionPath(sessionId);
  if (!existsSync(path)) {
    throw Object.assign(new Error('AI chat session was not found'), { statusCode: 404 });
  }
  return JSON.parse(readFileSync(path, 'utf8'));
}

function writeAiChatSession(session) {
  const path = aiChatSessionPath(session.id);
  const tempPath = `${path}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tempPath, `${JSON.stringify(session, null, 2)}\n`, 'utf8');
  renameSync(tempPath, path);
  return session;
}

function listAiChatSessions() {
  const sessionsPath = ensureAiChatSessionsDir();
  const sessions = readdirSync(sessionsPath)
    .filter((name) => name.endsWith('.json'))
    .map((name) => {
      try {
        const payload = JSON.parse(readFileSync(resolve(sessionsPath, name), 'utf8'));
        return summarizeAiChatSession(payload);
      } catch {
        return null;
      }
    })
    .filter(Boolean)
    .sort((left, right) => right.updatedAt - left.updatedAt);

  const payload = {
    schemaVersion: 'v1',
    sessions,
  };
  writeFileSync(aiChatIndexPath(), `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  return payload;
}

function deleteAiChatSession(sessionId) {
  const path = aiChatSessionPath(sessionId);
  if (existsSync(path)) {
    unlinkSync(path);
  }
  return listAiChatSessions();
}

function bridgeEnv(extraEnv = {}) {
  const derivedEnv = deriveBridgeRuntimeEnv();
  const env = {
    ...process.env,
    ...derivedEnv,
    NOTEAPP_VAULT_ROOT: selectedVaultRoot,
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
    ...extraEnv,
  };
  return env;
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
    windowsHide: true,
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
  const env = bridgeEnv({
    PYTHONPATH: buildPythonPath(),
  });
  if (!env.NOTEAPP_BEARER_TOKEN && shouldAutoAttachBearer(commandArgs)) {
    const bearerToken = resolveBridgeBearerToken(env);
    if (bearerToken) {
      env.NOTEAPP_BEARER_TOKEN = bearerToken;
    }
  }
  const result = spawnSync(
    python,
    [
      '-m',
      'clients.desktop.cli',
      '--vault-root',
      selectedVaultRoot,
      '--base-url',
      requireEnvValue(env, 'NOTEAPP_SYNC_BASE_URL'),
      '--vault-id',
      requireEnvValue(env, 'NOTEAPP_VAULT_ID'),
      '--device-id',
      requireEnvValue(env, 'NOTEAPP_DEVICE_ID'),
      ...(env.NOTEAPP_BEARER_TOKEN ? [`--bearer-token=${env.NOTEAPP_BEARER_TOKEN}`] : []),
      ...commandArgs,
    ],
    {
      cwd: repoRoot,
      env,
      encoding: 'utf8',
      stdout: 'pipe',
      stderr: 'pipe',
      windowsHide: true,
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

function shouldAutoAttachBearer(commandArgs) {
  const command = commandArgs[0];
  return [
    'file-versions',
    'file-version-content',
    'diff-file-version',
    'restore-file-version',
    'update-file-version',
    'vault-devices',
    'heartbeat-vault-device',
    'revoke-device',
  ].includes(command);
}

function readCryptoStatus() {
  return JSON.parse(runDesktopCli(['crypto-status']));
}

function unlockCryptoVault(payload) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('crypto unlock request must contain an object');
  }
  const vaultKeyBase64 = typeof payload.vault_key_base64 === 'string'
    ? payload.vault_key_base64.trim()
    : '';
  const vaultKeyHex = typeof payload.vault_key_hex === 'string'
    ? payload.vault_key_hex.trim()
    : '';
  if (Boolean(vaultKeyBase64) === Boolean(vaultKeyHex)) {
    throw new Error('crypto unlock requires exactly one of vault_key_base64 or vault_key_hex');
  }
  return JSON.parse(runDesktopCli([
    'crypto-unlock',
    ...(vaultKeyBase64 ? ['--vault-key-base64', vaultKeyBase64] : ['--vault-key-hex', vaultKeyHex]),
  ]));
}

function lockCryptoVault() {
  return JSON.parse(runDesktopCli(['crypto-lock']));
}

function requireRecoveryPhrase(payload) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('crypto recovery request must contain an object');
  }
  const recoveryPhrase = typeof payload.recovery_phrase === 'string'
    ? payload.recovery_phrase.trim()
    : '';
  if (!recoveryPhrase) {
    throw new Error('crypto recovery requires recovery_phrase');
  }
  return recoveryPhrase;
}

function exportCryptoRecoveryPackage(payload) {
  const recoveryPhrase = requireRecoveryPhrase(payload);
  return JSON.parse(runDesktopCli([
    'crypto-recovery-export',
    '--recovery-phrase',
    recoveryPhrase,
  ]));
}

function importCryptoRecoveryPackage(payload) {
  const recoveryPhrase = requireRecoveryPhrase(payload);
  const recoveryPackageJson = typeof payload.recovery_package_json === 'string'
    ? payload.recovery_package_json.trim()
    : '';
  if (!recoveryPackageJson) {
    throw new Error('请提供恢复包文件，或使用一台已解锁设备进行本地配对');
  }

  const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-crypto-recovery-'));
  try {
    const inputPath = resolve(tempRoot, 'recovery-package.json');
    writeFileSync(inputPath, `${recoveryPackageJson}\n`, 'utf8');
    const result = JSON.parse(runDesktopCli([
      'crypto-recovery-import',
      '--recovery-phrase',
      recoveryPhrase,
      '--input-json',
      inputPath,
    ]));
    runScript('write-local-settings-snapshot.mjs', {
      NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT: settingsSnapshotPath,
    });
    return result;
  } finally {
    rmSync(tempRoot, { recursive: true, force: true });
  }
}

function requireEnvValue(env, name) {
  const value = env[name];
  if (!value || !value.trim()) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function resolveBridgeBearerToken(env) {
  if (env.NOTEAPP_BEARER_TOKEN && env.NOTEAPP_BEARER_TOKEN.trim()) {
    return env.NOTEAPP_BEARER_TOKEN.trim();
  }
  if (env.NOTEAPP_SYNC_BRIDGE_AUTO_REGISTER === 'false') {
    return '';
  }
  const baseUrl = env.NOTEAPP_SYNC_BASE_URL || '';
  if (!isLoopbackSyncBaseUrl(baseUrl)) {
    return '';
  }
  const now = Date.now();
  if (
    cachedSyncSession
    && cachedSyncSession.baseUrl === baseUrl
    && cachedSyncSession.accessToken
    && cachedSyncSession.accessExpiresAtMs > now + 60_000
  ) {
    return cachedSyncSession.accessToken;
  }
  const session = registerLocalSyncSession(env);
  cachedSyncSession = session;
  return session.accessToken;
}

function isLoopbackSyncBaseUrl(baseUrl) {
  try {
    const url = new URL(baseUrl);
    return url.protocol === 'http:' && isLoopbackHost(url.hostname);
  } catch {
    return false;
  }
}

function registerLocalSyncSession(env) {
  const authSession = readAuthSession();
  const userInfo = authSession.user_info && typeof authSession.user_info === 'object'
    ? authSession.user_info
    : {};
  const accountKey = typeof userInfo.userId === 'string' && userInfo.userId.trim()
    ? `app-user:${userInfo.userId.trim()}`
    : `local-bridge:${env.NOTEAPP_VAULT_ID || 'default'}`;
  const displayName = typeof userInfo.userName === 'string' && userInfo.userName.trim()
    ? userInfo.userName.trim()
    : 'NoteApp Local Bridge';
  const payload = {
    device_name: `NoteApp Bridge (${env.NOTEAPP_DEVICE_ID || 'desktop-local'})`,
    platform: 'desktop',
    protocol_version: 'v1',
    account_key: accountKey,
    display_name: displayName,
    access_token_ttl_ms: 30 * 24 * 60 * 60 * 1000,
    refresh_token_ttl_ms: 90 * 24 * 60 * 60 * 1000,
  };
  const response = postSyncBackendJson(env.NOTEAPP_SYNC_BASE_URL, '/devices/register', payload);
  if (!response || typeof response.access_token !== 'string' || !response.access_token.trim()) {
    throw new Error('sync backend auto-registration did not return an access token');
  }
  return {
    baseUrl: env.NOTEAPP_SYNC_BASE_URL,
    accessToken: response.access_token.trim(),
    accessExpiresAtMs: Number.isFinite(Number(response.access_token_expires_at_ms))
      ? Number(response.access_token_expires_at_ms)
      : Date.now() + 30 * 24 * 60 * 60 * 1000,
  };
}

function postSyncBackendJson(baseUrl, path, payload) {
  const script = `
const [baseUrl, path, payloadJson] = process.argv.slice(1);
const controller = new AbortController();
const timer = setTimeout(() => controller.abort(), 10000);
(async () => {
  try {
    const response = await fetch(new URL(path, baseUrl), {
      method: 'POST',
      headers: {
        'accept': 'application/json',
        'content-type': 'application/json',
      },
      body: payloadJson,
      signal: controller.signal,
    });
    const text = await response.text();
    if (!response.ok) {
      console.error(text || response.statusText);
      process.exit(20);
      return;
    }
    process.stdout.write(text);
  } catch (error) {
    console.error(error instanceof Error ? error.message : String(error));
    process.exit(1);
  } finally {
    clearTimeout(timer);
  }
})();
`;
  const result = spawnSync(
    process.execPath,
    ['-e', script, baseUrl, path, JSON.stringify(payload)],
    {
      encoding: 'utf8',
      stdout: 'pipe',
      stderr: 'pipe',
      windowsHide: true,
    },
  );
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(
      [
        `sync backend auto-registration failed with exit code ${result.status}`,
        result.stdout.trim(),
        result.stderr.trim(),
      ]
        .filter(Boolean)
        .join('\n'),
    );
  }
  return JSON.parse(result.stdout);
}

function readWorkspaceSettingsFile() {
  if (!selectedVaultRoot || !selectedVaultRoot.trim()) {
    return null;
  }
  const path = resolve(selectedVaultRoot, '.noteapp', 'settings.json');
  if (!existsSync(path)) {
    return null;
  }
  try {
    const payload = JSON.parse(readFileSync(path, 'utf8'));
    return payload && typeof payload === 'object' ? payload : null;
  } catch {
    return null;
  }
}

function readPersistedSettingsSnapshot() {
  if (!existsSync(settingsSnapshotPath)) {
    return null;
  }
  try {
    const payload = JSON.parse(readFileSync(settingsSnapshotPath, 'utf8'));
    return payload && typeof payload === 'object' ? payload : null;
  } catch {
    return null;
  }
}

function readWorkspaceFilemapVaultId() {
  if (!selectedVaultRoot || !selectedVaultRoot.trim()) {
    return '';
  }
  const path = resolve(selectedVaultRoot, '.noteapp', 'filemap.json');
  if (!existsSync(path)) {
    return '';
  }
  try {
    const payload = JSON.parse(readFileSync(path, 'utf8'));
    return payload && typeof payload.vault_id === 'string' ? payload.vault_id.trim() : '';
  } catch {
    return '';
  }
}

function fallbackWorkspaceVaultId() {
  if (!selectedVaultRoot || !selectedVaultRoot.trim()) {
    return '';
  }
  const normalizedRoot = normalizeWorkspaceRootPath(selectedVaultRoot);
  const stableHash = createHash('sha1')
    .update(process.platform === 'win32' ? normalizedRoot.toLowerCase() : normalizedRoot)
    .digest('hex')
    .slice(0, 16);
  return `vault-local-${stableHash}`;
}

function deriveBridgeRuntimeEnv() {
  const settings = readWorkspaceSettingsFile();
  const snapshot = readPersistedSettingsSnapshot();
  const snapshotMatchesWorkspace = snapshot
    && typeof snapshot === 'object'
    && typeof snapshot.vault_root === 'string'
    && snapshot.vault_root === selectedVaultRoot;
  const sync = settings && typeof settings === 'object' && settings.sync && typeof settings.sync === 'object'
    ? settings.sync
    : (snapshot && snapshot.sync && typeof snapshot.sync === 'object' ? snapshot.sync : null);
  const derivedEnv = {};
  const baseUrl = sync && typeof sync.base_url === 'string' && sync.base_url.trim()
    ? sync.base_url.trim()
    : 'http://127.0.0.1:8000';
  if (!process.env.NOTEAPP_SYNC_BASE_URL && baseUrl) {
    derivedEnv.NOTEAPP_SYNC_BASE_URL = baseUrl;
  }
  const filemapVaultId = readWorkspaceFilemapVaultId();
  const settingsVaultId = settings && typeof settings === 'object' && typeof settings.vault_id === 'string'
    ? settings.vault_id
    : '';
  const snapshotVaultId = snapshotMatchesWorkspace && snapshot && typeof snapshot.vault_id === 'string'
    ? snapshot.vault_id
    : '';
  const vaultId = filemapVaultId || settingsVaultId || snapshotVaultId || fallbackWorkspaceVaultId();
  if (!process.env.NOTEAPP_VAULT_ID && vaultId && vaultId.trim()) {
    derivedEnv.NOTEAPP_VAULT_ID = vaultId.trim();
  }
  const deviceId = settings && typeof settings === 'object' && typeof settings.device_id === 'string'
    ? settings.device_id
    : (
      snapshotMatchesWorkspace && snapshot && typeof snapshot.device_id === 'string'
        ? snapshot.device_id
        : 'desktop-local'
    );
  if (!process.env.NOTEAPP_DEVICE_ID && deviceId && deviceId.trim()) {
    derivedEnv.NOTEAPP_DEVICE_ID = deviceId.trim();
  }
  return derivedEnv;
}

function buildPythonPath() {
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
    workspace_id: activeWorkspaceId,
    vault_root: selectedVaultRoot,
    source: selectedVaultRoot === initialVaultRoot ? 'environment' : 'runtime',
    exists,
    initialized,
    config_path: workspaceRootPath,
    registry_path: workspaceRegistryPath,
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
    windowsHide: true,
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
  registerWorkspace(vaultRoot, { activate: true, bestEffortArtifacts: true });
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

function authSessionPayload(userInfo = null, updatedAtMs = null) {
  return {
    schema_version: 'v1',
    authenticated: userInfo !== null,
    user_info: userInfo,
    updated_at_ms: updatedAtMs,
  };
}

function readAuthSession() {
  if (!existsSync(authSessionPath)) {
    return authSessionPayload();
  }
  try {
    const payload = JSON.parse(readFileSync(authSessionPath, 'utf8'));
    const userInfo = payload && typeof payload === 'object' && 'user_info' in payload
      ? payload.user_info ?? null
      : null;
    const updatedAtMs = payload && typeof payload === 'object' && Number.isFinite(Number(payload.updated_at_ms))
      ? Number(payload.updated_at_ms)
      : null;
    return authSessionPayload(userInfo, updatedAtMs);
  } catch {
    return authSessionPayload();
  }
}

function detectPythonHealth() {
  const python = process.env.PYTHON || 'python';
  const result = spawnSync(python, ['--version'], {
    encoding: 'utf8',
    stdout: 'pipe',
    stderr: 'pipe',
    windowsHide: true,
  });
  if (result.error) {
    return {
      ok: false,
      command: python,
      message: result.error.message,
    };
  }
  if (result.status !== 0) {
    return {
      ok: false,
      command: python,
      message: [result.stdout.trim(), result.stderr.trim()].filter(Boolean).join('\n') || `exit code ${result.status}`,
    };
  }
  return {
    ok: true,
    command: python,
    version: result.stdout.trim() || result.stderr.trim() || null,
  };
}

function detectWorkspaceHealth() {
  if (!selectedVaultRoot || !selectedVaultRoot.trim()) {
    return {
      configured: false,
      exists: false,
      initialized: false,
      active_workspace_id: activeWorkspaceId,
      vault_root: '',
    };
  }
  const exists = existsSync(selectedVaultRoot);
  const initialized = exists && existsSync(resolve(selectedVaultRoot, '.noteapp', 'filemap.json'));
  return {
    configured: true,
    exists,
    initialized,
    active_workspace_id: activeWorkspaceId,
    vault_root: selectedVaultRoot,
  };
}

function detectSyncRuntimeHealth() {
  const derivedEnv = deriveBridgeRuntimeEnv();
  const baseUrl = derivedEnv.NOTEAPP_SYNC_BASE_URL || process.env.NOTEAPP_SYNC_BASE_URL || '';
  const vaultId = derivedEnv.NOTEAPP_VAULT_ID || process.env.NOTEAPP_VAULT_ID || '';
  const deviceId = derivedEnv.NOTEAPP_DEVICE_ID || process.env.NOTEAPP_DEVICE_ID || '';
  const missing = [
    !baseUrl ? 'NOTEAPP_SYNC_BASE_URL' : null,
    !vaultId ? 'NOTEAPP_VAULT_ID' : null,
    !deviceId ? 'NOTEAPP_DEVICE_ID' : null,
  ].filter(Boolean);
  return {
    ok: missing.length === 0,
    base_url: baseUrl || null,
    vault_id: vaultId || null,
    device_id: deviceId || null,
    missing_env: missing,
  };
}

function dependencyHealthPayload() {
  const python = detectPythonHealth();
  const workspace = detectWorkspaceHealth();
  const sync = detectSyncRuntimeHealth();
  const authSession = readAuthSession();
  const ok = python.ok && (!workspace.configured || workspace.exists);
  return {
    ok,
    python,
    sync,
    workspace,
    auth: {
      authenticated: authSession.authenticated,
      updated_at_ms: authSession.updated_at_ms,
    },
    bridge: {
      host,
      port,
      allow_remote_host: allowRemoteHost,
      allowed_origin: allowedOrigin,
    },
    paths: {
      snapshot_path: snapshotPath,
      settings_snapshot_path: settingsSnapshotPath,
      auth_session_path: authSessionPath,
      workspace_files_path: workspaceFilesPath,
      workspace_root_path: workspaceRootPath,
      workspace_registry_path: workspaceRegistryPath,
    },
  };
}

function shouldRefreshSettingsSnapshot() {
  if (!selectedVaultRoot || !selectedVaultRoot.trim()) {
    return !existsSync(settingsSnapshotPath);
  }
  if (!existsSync(settingsSnapshotPath)) {
    return true;
  }
  try {
    const payload = JSON.parse(readFileSync(settingsSnapshotPath, 'utf8'));
    if (!payload || typeof payload !== 'object') {
      return true;
    }
    const derivedEnv = deriveBridgeRuntimeEnv();
    const nextVaultId = derivedEnv.NOTEAPP_VAULT_ID || process.env.NOTEAPP_VAULT_ID || '';
    const nextDeviceId = derivedEnv.NOTEAPP_DEVICE_ID || process.env.NOTEAPP_DEVICE_ID || '';
    return payload.vault_root !== selectedVaultRoot
      || payload.vault_id !== nextVaultId
      || payload.device_id !== nextDeviceId;
  } catch {
    return true;
  }
}

function persistAuthSession(userInfo) {
  mkdirSync(resolve(authSessionPath, '..'), { recursive: true });
  const payload = authSessionPayload(userInfo, Date.now());
  const tempPath = `${authSessionPath}.${process.pid}.${Date.now()}.tmp`;
  writeFileSync(tempPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  renameSync(tempPath, authSessionPath);
  return payload;
}

function clearAuthSession() {
  if (existsSync(authSessionPath)) {
    unlinkSync(authSessionPath);
  }
  return authSessionPayload();
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

function workspaceVersionsFileIdFromPath(pathname) {
  const prefix = '/api/workspace/files/';
  const suffix = '/versions';
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length, -suffix.length);
  if (!encoded || encoded.includes('/')) {
    return null;
  }
  return decodeURIComponent(encoded);
}

function workspaceVersionRouteFromPath(pathname, suffix) {
  const prefix = '/api/workspace/files/';
  if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length, -suffix.length);
  const parts = encoded.split('/');
  if (parts.length !== 3 || parts[1] !== 'versions' || !parts[0] || !parts[2]) {
    return null;
  }
  return {
    fileId: decodeURIComponent(parts[0]),
    versionId: decodeURIComponent(parts[2]),
  };
}

function workspaceVersionContentFromPath(pathname) {
  return workspaceVersionRouteFromPath(pathname, '/content');
}

function workspaceVersionDiffFromPath(pathname) {
  return workspaceVersionRouteFromPath(pathname, '/diff');
}

function workspaceVersionRestoreFromPath(pathname) {
  return workspaceVersionRouteFromPath(pathname, '/restore');
}

function workspaceFileVersionIdFromPath(pathname) {
  const prefix = '/api/workspace/file-versions/';
  if (!pathname.startsWith(prefix)) {
    return null;
  }
  const encoded = pathname.slice(prefix.length);
  if (!encoded || encoded.includes('/')) {
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

function workspaceMoveFileIdFromPath(pathname) {
  const suffix = '/move';
  for (const prefix of ['/api/workspace/files/', '/api/workspace/notes/']) {
    if (!pathname.startsWith(prefix) || !pathname.endsWith(suffix)) {
      continue;
    }
    const encoded = pathname.slice(prefix.length, -suffix.length);
    if (!encoded || encoded.includes('/')) {
      return null;
    }
    return decodeURIComponent(encoded);
  }
  return null;
}

function errorPayload(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (isSyncBackendUnavailableMessage(message)) {
    return {
      statusCode: 503,
      payload: {
        code: 'sync_backend_unavailable',
        message: 'Sync backend is unavailable. Start the sync server or update NOTEAPP_SYNC_BASE_URL.',
      },
    };
  }
  const backendHttpError = syncBackendHttpErrorPayload(message);
  if (backendHttpError) {
    return backendHttpError;
  }
  if (error && typeof error === 'object' && Number.isFinite(Number(error.statusCode))) {
    return {
      statusCode: Number(error.statusCode),
      payload: {
        code: Number(error.statusCode) === 404 ? 'not_found' : 'sync_bridge_error',
        message,
      },
    };
  }
  if (
    message.includes('request body is too large')
    || message.includes('Unexpected end of JSON input')
    || message.includes('workspace root')
    || message.includes('workspace folder selection was cancelled')
  ) {
    return {
      statusCode: 400,
      payload: {
        code: message.includes('workspace folder selection was cancelled') ? 'request_cancelled' : 'invalid_request',
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

function isSyncBackendUnavailableMessage(message) {
  return [
    'ConnectionRefusedError',
    'ECONNREFUSED',
    'WinError 10061',
    'actively refused',
    'No connection could be made',
    'Failed to establish a new connection',
    'urlopen error',
  ].some((marker) => message.includes(marker));
}

function syncBackendHttpErrorPayload(message) {
  const match = /HTTP Error (\d{3}): ([^\r\n]+)/.exec(message);
  if (!match) {
    return null;
  }
  const statusCode = Number(match[1]);
  const reason = match[2].trim();
  if (statusCode === 401) {
    return {
      statusCode,
      payload: {
        code: 'sync_backend_unauthorized',
        message: 'Sync backend rejected the request. Configure NOTEAPP_BEARER_TOKEN or refresh sync credentials.',
      },
    };
  }
  if (statusCode === 403) {
    return {
      statusCode,
      payload: {
        code: 'sync_backend_forbidden',
        message: 'Sync backend denied access for the current credentials.',
      },
    };
  }
  return {
    statusCode,
    payload: {
      code: 'sync_backend_http_error',
      message: `Sync backend returned ${statusCode}${reason ? ` ${reason}` : ''}.`,
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
        workspaceRegistryPath,
        activeWorkspaceId,
        vaultRoot: selectedVaultRoot,
        allowedOrigin,
      });
      return;
    }

    if (request.method === 'GET' && url.pathname === '/health/dependencies') {
      jsonResponse(request, response, 200, dependencyHealthPayload());
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

    if (request.method === 'GET' && url.pathname === '/api/auth/session') {
      jsonResponse(request, response, 200, readAuthSession());
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/settings/snapshot') {
      if (shouldRefreshSettingsSnapshot()) {
        runScript('write-local-settings-snapshot.mjs', {
          NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT: settingsSnapshotPath,
        });
      }
      jsonResponse(request, response, 200, readSettingsSnapshot());
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/crypto/status') {
      jsonResponse(request, response, 200, readCryptoStatus());
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/crypto/unlock') {
      const rawBody = await readRequestBody(request);
      const payload = rawBody ? JSON.parse(rawBody) : {};
      jsonResponse(request, response, 200, unlockCryptoVault(payload));
      runScript('write-local-settings-snapshot.mjs', {
        NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT: settingsSnapshotPath,
      });
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/crypto/lock') {
      jsonResponse(request, response, 200, lockCryptoVault());
      runScript('write-local-settings-snapshot.mjs', {
        NOTEAPP_SETTINGS_SNAPSHOT_OUTPUT: settingsSnapshotPath,
      });
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/crypto/recovery/export') {
      const rawBody = await readRequestBody(request);
      const payload = rawBody ? JSON.parse(rawBody) : {};
      jsonResponse(request, response, 200, exportCryptoRecoveryPackage(payload));
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/crypto/recovery/import') {
      const rawBody = await readRequestBody(request);
      const payload = rawBody ? JSON.parse(rawBody) : {};
      jsonResponse(request, response, 200, importCryptoRecoveryPackage(payload));
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/auth/session') {
      const rawBody = await readRequestBody(request);
      const payload = rawBody ? JSON.parse(rawBody) : {};
      if (!payload || typeof payload !== 'object' || !('userInfo' in payload)) {
        throw new Error('auth session request must include userInfo');
      }
      jsonResponse(request, response, 200, persistAuthSession(payload.userInfo ?? null));
      return;
    }

    if (request.method === 'DELETE' && url.pathname === '/api/auth/session') {
      jsonResponse(request, response, 200, clearAuthSession());
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

    if (request.method === 'GET' && url.pathname === '/api/workspaces') {
      jsonResponse(request, response, 200, workspaceRegistryPayload());
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/workspaces') {
      const rawBody = await readRequestBody(request);
      const payload = rawBody ? JSON.parse(rawBody) : {};
      const workspace = registerWorkspace(payload?.vault_root, {
        name: payload?.name,
        activate: payload?.activate !== false,
        bestEffortArtifacts: true,
      });
      jsonResponse(request, response, 200, {
        workspace: workspace ? workspaceRegistrationSummary(workspace) : null,
        registry: workspaceRegistryPayload(),
      });
      return;
    }

    if (request.method === 'POST' && url.pathname === '/api/workspaces/select-folder') {
      const workspace = registerWorkspace(selectWorkspaceRootWithDialog(), {
        activate: true,
        bestEffortArtifacts: true,
      });
      jsonResponse(request, response, 200, {
        workspace: workspace ? workspaceRegistrationSummary(workspace) : null,
        registry: workspaceRegistryPayload(),
      });
      return;
    }

    const workspaceRegistryPrefix = '/api/workspaces/';
    if (routePathname.startsWith(workspaceRegistryPrefix)) {
      const tail = routePathname.slice(workspaceRegistryPrefix.length);
      const parts = tail.split('/').filter(Boolean);
      if (parts.length === 1 && request.method === 'PATCH') {
        const rawBody = await readRequestBody(request);
        const payload = rawBody ? JSON.parse(rawBody) : {};
        jsonResponse(request, response, 200, renameWorkspaceRegistration(decodeURIComponent(parts[0]), payload?.name));
        return;
      }
      if (parts.length === 1 && request.method === 'DELETE') {
        jsonResponse(request, response, 200, deleteWorkspaceRegistration(decodeURIComponent(parts[0])));
        return;
      }
      if (parts.length === 2 && parts[1] === 'activate' && request.method === 'POST') {
        jsonResponse(request, response, 200, activateWorkspaceById(decodeURIComponent(parts[0]), {
          bestEffortArtifacts: true,
        }));
        return;
      }
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

    if (request.method === 'POST' && routePathname === '/api/ai/context-task') {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-ai-context-'));
      const inputPath = resolve(tempRoot, 'request.json');
      try {
        writeFileSync(inputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
        const stdout = runDesktopCli(['ai-context-task', '--input-json', inputPath]);
        jsonResponse(request, response, 200, JSON.parse(stdout));
      } finally {
        rmSync(tempRoot, { recursive: true, force: true });
      }
      return;
    }

    if (request.method === 'POST' && routePathname === '/api/ai/writeback/preview') {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-ai-writeback-preview-'));
      const inputPath = resolve(tempRoot, 'request.json');
      try {
        writeFileSync(inputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
        const stdout = runDesktopCli(['ai-writeback-preview', '--input-json', inputPath]);
        jsonResponse(request, response, 200, JSON.parse(stdout));
      } finally {
        rmSync(tempRoot, { recursive: true, force: true });
      }
      return;
    }

    if (request.method === 'POST' && routePathname === '/api/ai/writeback/apply') {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      const tempRoot = mkdtempSync(resolve(tmpdir(), 'noteapp-ai-writeback-apply-'));
      const inputPath = resolve(tempRoot, 'request.json');
      try {
        writeFileSync(inputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
        const stdout = runDesktopCli(['ai-writeback-apply', '--input-json', inputPath]);
        jsonResponse(request, response, 200, JSON.parse(stdout));
      } finally {
        rmSync(tempRoot, { recursive: true, force: true });
      }
      return;
    }

    if (request.method === 'GET' && routePathname === '/api/ai/chat-sessions') {
      jsonResponse(request, response, 200, listAiChatSessions());
      return;
    }

    if (request.method === 'POST' && routePathname === '/api/ai/chat-sessions') {
      const rawBody = await readRequestBody(request);
      const payload = rawBody ? JSON.parse(rawBody) : {};
      const session = writeAiChatSession(normalizeAiChatSession(payload));
      jsonResponse(request, response, 200, session);
      return;
    }

    const aiChatSessionPrefix = '/api/ai/chat-sessions/';
    if (routePathname.startsWith(aiChatSessionPrefix)) {
      const sessionId = decodeURIComponent(routePathname.slice(aiChatSessionPrefix.length));
      if (request.method === 'GET') {
        jsonResponse(request, response, 200, readAiChatSession(sessionId));
        return;
      }
      if (request.method === 'PUT') {
        const rawBody = await readRequestBody(request);
        const payload = rawBody ? JSON.parse(rawBody) : {};
        const existingSession = readAiChatSession(sessionId);
        const session = writeAiChatSession(normalizeAiChatSession({ ...payload, id: sessionId }, existingSession));
        jsonResponse(request, response, 200, session);
        return;
      }
      if (request.method === 'DELETE') {
        jsonResponse(request, response, 200, deleteAiChatSession(sessionId));
        return;
      }
    }

    if (request.method === 'POST' && routePathname === '/api/ai/provider/health') {
      const stdout = runDesktopCli(['ai-provider-health']);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (request.method === 'GET' && routePathname === '/api/devices') {
      const stdout = runDesktopCli(['vault-devices']);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    if (request.method === 'POST' && routePathname === '/api/devices/heartbeat') {
      const stdout = runDesktopCli(['heartbeat-vault-device']);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const deviceRoutePrefix = '/api/devices/';
    if (request.method === 'DELETE' && routePathname.startsWith(deviceRoutePrefix)) {
      const deviceId = decodeURIComponent(routePathname.slice(deviceRoutePrefix.length));
      if (!deviceId || deviceId.includes('/')) {
        throw new Error('device revoke request must include one device id');
      }
      const stdout = runDesktopCli(['revoke-device', '--target-device-id', deviceId]);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const workspaceVersionsFileId = workspaceVersionsFileIdFromPath(routePathname);
    if (request.method === 'GET' && workspaceVersionsFileId) {
      const commandArgs = ['file-versions', '--file-id', workspaceVersionsFileId];
      const limit = url.searchParams.get('limit');
      const cursor = url.searchParams.get('cursor');
      if (limit) {
        commandArgs.push('--limit', limit);
      }
      if (cursor) {
        commandArgs.push('--cursor', cursor);
      }
      const stdout = runDesktopCli(commandArgs);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const workspaceVersionContentRoute = workspaceVersionContentFromPath(routePathname);
    if (request.method === 'GET' && workspaceVersionContentRoute) {
      const stdout = runDesktopCli([
        'file-version-content',
        '--file-id',
        workspaceVersionContentRoute.fileId,
        '--version-id',
        workspaceVersionContentRoute.versionId,
      ]);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const workspaceVersionDiffRoute = workspaceVersionDiffFromPath(routePathname);
    if (request.method === 'GET' && workspaceVersionDiffRoute) {
      const contextLines = url.searchParams.get('context_lines') || url.searchParams.get('contextLines');
      const commandArgs = [
        'diff-file-version',
        '--file-id',
        workspaceVersionDiffRoute.fileId,
        '--version-id',
        workspaceVersionDiffRoute.versionId,
      ];
      if (contextLines) {
        commandArgs.push('--context-lines', contextLines);
      }
      const stdout = runDesktopCli(commandArgs);
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const workspaceVersionRestoreRoute = workspaceVersionRestoreFromPath(routePathname);
    if (request.method === 'POST' && workspaceVersionRestoreRoute) {
      const rawBody = await readRequestBody(request);
      const payload = rawBody ? JSON.parse(rawBody) : {};
      const commandArgs = [
        'restore-file-version',
        '--file-id',
        workspaceVersionRestoreRoute.fileId,
        '--version-id',
        workspaceVersionRestoreRoute.versionId,
        '--created-at',
        String(Date.now()),
      ];
      if (typeof payload.version_label === 'string' && payload.version_label.trim()) {
        commandArgs.push('--version-label', payload.version_label.trim());
      }
      if (typeof payload.change_note === 'string' && payload.change_note.trim()) {
        commandArgs.push('--change-note', payload.change_note.trim());
      }
      if (payload.is_pinned === true) {
        commandArgs.push('--pin-version');
      }
      const stdout = runDesktopCli(commandArgs);
      runScript('write-workspace-files.mjs', {
        NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
      });
      runScript('write-live-sync-shell.mjs', {
        NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
      });
      jsonResponse(request, response, 200, JSON.parse(stdout));
      return;
    }

    const workspaceFileVersionId = workspaceFileVersionIdFromPath(routePathname);
    if (request.method === 'PATCH' && workspaceFileVersionId) {
      const rawBody = await readRequestBody(request);
      const payload = rawBody ? JSON.parse(rawBody) : {};
      const commandArgs = ['update-file-version', '--version-id', workspaceFileVersionId];
      if (typeof payload.version_label === 'string' && payload.version_label.trim()) {
        commandArgs.push('--version-label', payload.version_label.trim());
      }
      if (typeof payload.change_note === 'string' && payload.change_note.trim()) {
        commandArgs.push('--change-note', payload.change_note.trim());
      }
      if (payload.is_pinned === true) {
        commandArgs.push('--pin');
      } else if (payload.is_pinned === false) {
        commandArgs.push('--unpin');
      }
      if (commandArgs.length === 3) {
        throw new Error('workspace file version update request must include metadata or pin change');
      }
      const stdout = runDesktopCli(commandArgs);
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

    const workspaceMoveFileId = workspaceMoveFileIdFromPath(url.pathname);
    if (request.method === 'POST' && workspaceMoveFileId) {
      const rawBody = await readRequestBody(request);
      const payload = JSON.parse(rawBody);
      if (!payload || typeof payload.path !== 'string') {
        throw new Error('workspace note move request must include path');
      }
      const stdout = runDesktopCli([
        'move-workspace-note',
        '--file-id',
        workspaceMoveFileId,
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

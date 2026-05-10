import { createServer } from 'node:http';
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const appRoot = resolve(scriptPath, '..', '..');
const defaultSnapshotPath = resolve(appRoot, 'public', 'fixtures', 'live-sync-shell.json');
const defaultSettingsSnapshotPath = resolve(appRoot, 'public', 'fixtures', 'local-settings-snapshot.json');
const defaultWorkspaceFilesPath = resolve(appRoot, 'public', 'fixtures', 'workspace-files.json');
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

It forwards to:
  GET  /api/sync/snapshot        npm run sync:snapshot equivalent
  POST /api/sync/actions/:id     npm run sync:action equivalent
  GET  /api/sync/live            Read current live snapshot without running CLI
  GET  /api/settings/snapshot    npm run settings:snapshot equivalent
  POST /api/settings/snapshot    npm run settings:write equivalent
  GET  /api/settings/live        Read current settings snapshot without running CLI
  GET  /api/workspace/files      npm run workspace:files equivalent
  GET  /api/workspace/live       Read current workspace files without running CLI
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
    'access-control-allow-methods': 'GET,POST,OPTIONS',
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
      if (size > 65536) {
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

function runScript(scriptName, extraEnv = {}) {
  const result = spawnSync(process.execPath, [`scripts/${scriptName}`], {
    cwd: appRoot,
    env: {
      ...process.env,
      ...extraEnv,
    },
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

function errorPayload(error) {
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes('request body is too large') || message.includes('Unexpected end of JSON input')) {
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

    if (request.method === 'GET' && url.pathname === '/api/workspace/files') {
      runScript('write-workspace-files.mjs', {
        NOTEAPP_WORKSPACE_FILES_OUTPUT: workspaceFilesPath,
      });
      jsonResponse(request, response, 200, readWorkspaceFiles());
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

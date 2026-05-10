import { createServer } from 'node:http';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const appRoot = resolve(scriptPath, '..', '..');
const defaultSnapshotPath = resolve(appRoot, 'public', 'fixtures', 'live-sync-shell.json');
const host = process.env.NOTEAPP_SYNC_BRIDGE_HOST || '127.0.0.1';
const port = Number(process.env.NOTEAPP_SYNC_BRIDGE_PORT || 3187);
const allowedOrigin = process.env.NOTEAPP_SYNC_BRIDGE_ORIGIN || '*';
const snapshotPath = process.env.NOTEAPP_SYNC_SNAPSHOT_OUTPUT
  ? resolve(process.env.NOTEAPP_SYNC_SNAPSHOT_OUTPUT)
  : defaultSnapshotPath;

const args = new Set(process.argv.slice(2));

function printHelp() {
  console.log(`sync-shell-bridge

Local-only HTTP bridge for the web UI sync shell.

Environment:
  NOTEAPP_SYNC_BRIDGE_HOST       Host, default 127.0.0.1
  NOTEAPP_SYNC_BRIDGE_PORT       Port, default 3187
  NOTEAPP_SYNC_BRIDGE_ORIGIN     CORS origin, default *
  NOTEAPP_SYNC_SNAPSHOT_OUTPUT   Output JSON path, default public/fixtures/live-sync-shell.json

It forwards to:
  GET  /api/sync/snapshot        npm run sync:snapshot equivalent
  POST /api/sync/actions/:id     npm run sync:action equivalent
  GET  /api/sync/live            Read current live snapshot without running CLI
  GET  /health                   Health check
`);
}

function jsonResponse(response, statusCode, payload) {
  response.writeHead(statusCode, {
    'access-control-allow-origin': allowedOrigin,
    'access-control-allow-methods': 'GET,POST,OPTIONS',
    'access-control-allow-headers': 'content-type',
    'cache-control': 'no-store',
    'content-type': 'application/json; charset=utf-8',
  });
  response.end(JSON.stringify(payload, null, 2));
}

function runScript(scriptName, extraEnv = {}) {
  const result = spawnSync(process.execPath, [`scripts/${scriptName}`], {
    cwd: appRoot,
    env: {
      ...process.env,
      ...extraEnv,
      NOTEAPP_SYNC_SNAPSHOT_OUTPUT: snapshotPath,
    },
    text: true,
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

if (args.has('--help') || args.has('-h')) {
  printHelp();
  process.exit(0);
}

const server = createServer((request, response) => {
  if (request.method === 'OPTIONS') {
    jsonResponse(response, 204, {});
    return;
  }

  const url = new URL(request.url || '/', `http://${host}:${port}`);

  try {
    if (request.method === 'GET' && url.pathname === '/health') {
      jsonResponse(response, 200, {
        ok: true,
        snapshotPath,
      });
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/sync/live') {
      jsonResponse(response, 200, readSnapshot());
      return;
    }

    if (request.method === 'GET' && url.pathname === '/api/sync/snapshot') {
      runScript('write-live-sync-shell.mjs');
      jsonResponse(response, 200, readSnapshot());
      return;
    }

    const actionId = actionIdFromPath(url.pathname);
    if (request.method === 'POST' && actionId) {
      runScript('execute-sync-action.mjs', {
        NOTEAPP_SYNC_ACTION_ID: actionId,
      });
      jsonResponse(response, 200, readSnapshot());
      return;
    }

    jsonResponse(response, 404, {
      code: 'not_found',
      message: 'Route was not found.',
    });
  } catch (error) {
    jsonResponse(response, 500, {
      code: 'sync_bridge_error',
      message: error instanceof Error ? error.message : String(error),
    });
  }
});

server.listen(port, host, () => {
  console.log(`noteapp-web sync bridge running at http://${host}:${port}/`);
});

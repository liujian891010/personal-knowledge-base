import { mkdirSync } from 'node:fs';
import { delimiter, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const appRoot = resolve(scriptPath, '..', '..');
const repoRoot = resolve(appRoot, '..', '..', '..');
const outputPath = process.env.NOTEAPP_SYNC_SNAPSHOT_OUTPUT
  ? resolve(process.env.NOTEAPP_SYNC_SNAPSHOT_OUTPUT)
  : resolve(appRoot, 'public', 'fixtures', 'live-sync-shell.json');

const args = new Set(process.argv.slice(2));
const isHelp = args.has('--help') || args.has('-h');
const isDryRun = args.has('--dry-run');

function printHelp() {
  console.log(`execute-sync-action

Execute one desktop sync action and refresh the live sync-shell snapshot.

Required environment:
  NOTEAPP_VAULT_ROOT       Local vault root path
  NOTEAPP_SYNC_BASE_URL    Sync server base URL
  NOTEAPP_VAULT_ID         Vault id
  NOTEAPP_DEVICE_ID        Desktop device id
  NOTEAPP_SYNC_ACTION_ID   Action id from sync-shell snapshot

Optional environment:
  NOTEAPP_BEARER_TOKEN     Device bearer token
  NOTEAPP_SYNC_NOW_MS      Stable generated_at timestamp
  NOTEAPP_ACTIVITY_LIMIT   Activity feed limit, default 20
  NOTEAPP_SYNC_SNAPSHOT_OUTPUT
                          Output JSON path, default public/fixtures/live-sync-shell.json
  PYTHON                   Python executable, default python

Options:
  --dry-run                Print the resolved command without running it
  --help                   Show this help
`);
}

function requireEnv(name) {
  const value = process.env[name];
  if (!value || !value.trim()) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

function buildPythonPath() {
  const entries = [
    resolve(repoRoot, 'packages', 'vault-core', 'src'),
    repoRoot,
  ];
  if (process.env.PYTHONPATH) {
    entries.push(process.env.PYTHONPATH);
  }
  return entries.join(delimiter);
}

function buildCommand() {
  const python = process.env.PYTHON || 'python';
  const commandArgs = [
    '-m',
    'clients.desktop.cli',
    '--vault-root',
    requireEnv('NOTEAPP_VAULT_ROOT'),
    '--base-url',
    requireEnv('NOTEAPP_SYNC_BASE_URL'),
    '--vault-id',
    requireEnv('NOTEAPP_VAULT_ID'),
    '--device-id',
    requireEnv('NOTEAPP_DEVICE_ID'),
  ];

  if (process.env.NOTEAPP_BEARER_TOKEN) {
    commandArgs.push('--bearer-token', process.env.NOTEAPP_BEARER_TOKEN);
  }

  commandArgs.push(
    'execute-sync-action-and-snapshot',
    '--action-id',
    requireEnv('NOTEAPP_SYNC_ACTION_ID'),
  );

  if (process.env.NOTEAPP_SYNC_NOW_MS) {
    commandArgs.push('--now-ms', process.env.NOTEAPP_SYNC_NOW_MS);
  }

  commandArgs.push(
    '--activity-limit',
    process.env.NOTEAPP_ACTIVITY_LIMIT || '20',
    '--output-json',
    outputPath,
  );

  return { python, commandArgs };
}

if (isHelp) {
  printHelp();
  process.exit(0);
}

try {
  mkdirSync(resolve(outputPath, '..'), { recursive: true });
  const { python, commandArgs } = buildCommand();
  const env = {
    ...process.env,
    PYTHONPATH: buildPythonPath(),
  };

  if (isDryRun) {
    console.log(
      JSON.stringify(
        {
          ok: true,
          cwd: repoRoot,
          python,
          args: commandArgs,
          outputPath,
          pythonpath: env.PYTHONPATH,
        },
        null,
        2,
      ),
    );
    process.exit(0);
  }

  const result = spawnSync(python, commandArgs, {
    cwd: repoRoot,
    env,
    stdio: 'inherit',
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }

  console.log(`wrote ${outputPath}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

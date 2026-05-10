import { mkdirSync, writeFileSync } from 'node:fs';
import { delimiter, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptPath = fileURLToPath(import.meta.url);
const appRoot = resolve(scriptPath, '..', '..');
const repoRoot = resolve(appRoot, '..', '..', '..');
const outputPath = process.env.NOTEAPP_WORKSPACE_FILE_CONTENT_OUTPUT
  ? resolve(process.env.NOTEAPP_WORKSPACE_FILE_CONTENT_OUTPUT)
  : resolve(appRoot, 'public', 'fixtures', 'workspace-file-content.json');

const args = new Set(process.argv.slice(2));
const isHelp = args.has('--help') || args.has('-h');
const isDryRun = args.has('--dry-run');

function printHelp() {
  console.log(`write-workspace-file-content

Generate public/fixtures/workspace-file-content.json from the desktop sync CLI.

Required environment:
  NOTEAPP_VAULT_ROOT                    Local vault root path
  NOTEAPP_SYNC_BASE_URL                 Sync server base URL
  NOTEAPP_VAULT_ID                      Vault id
  NOTEAPP_DEVICE_ID                     Desktop device id
  NOTEAPP_WORKSPACE_FILE_ID             File id to read

Optional environment:
  NOTEAPP_BEARER_TOKEN                  Device bearer token
  NOTEAPP_WORKSPACE_FILE_CONTENT_OUTPUT Output JSON path, default public/fixtures/workspace-file-content.json
  PYTHON                                Python executable, default python

Options:
  --dry-run                             Print the resolved command without running it
  --help                                Show this help
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
    'workspace-file-content',
    '--file-id',
    requireEnv('NOTEAPP_WORKSPACE_FILE_ID'),
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
    PYTHONIOENCODING: 'utf-8',
    PYTHONUTF8: '1',
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
    encoding: 'utf8',
    stdout: 'pipe',
    stderr: 'inherit',
  });

  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }

  const payload = JSON.parse(result.stdout);
  writeFileSync(outputPath, `${JSON.stringify(payload, null, 2)}\n`, 'utf8');
  console.log(`wrote ${outputPath}`);
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error));
  process.exit(1);
}

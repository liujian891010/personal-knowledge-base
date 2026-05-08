import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { delimiter } from "node:path";
import { spawnSync } from "node:child_process";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "..");
const repoRoot = resolve(appRoot, "..", "..", "..");
const defaultOutputPath = join(appRoot, "fixtures", "live-sync-shell.json");

function printUsage() {
  console.log(`Usage:
  npm run sync:snapshot -- --vault-root <path> --base-url <url> --vault-id <id> --device-id <id>

Environment fallbacks:
  PKB_VAULT_ROOT
  PKB_BASE_URL
  PKB_VAULT_ID
  PKB_DEVICE_ID
  PKB_BEARER_TOKEN
  PKB_SYNC_ACTIVITY_LIMIT
  PKB_SYNC_NOW_MS
  PKB_SYNC_SNAPSHOT_OUTPUT
  PYTHON_BIN
`);
}

function readArgMap(argv) {
  const map = new Map();
  for (let index = 0; index < argv.length; index += 1) {
    const key = argv[index];
    if (!key.startsWith("--")) {
      continue;
    }
    const next = argv[index + 1];
    if (!next || next.startsWith("--")) {
      map.set(key, "true");
      continue;
    }
    map.set(key, next);
    index += 1;
  }
  return map;
}

const args = readArgMap(process.argv.slice(2));
if (args.has("--help")) {
  printUsage();
  process.exit(0);
}

const vaultRoot = args.get("--vault-root") || process.env.PKB_VAULT_ROOT;
const baseUrl = args.get("--base-url") || process.env.PKB_BASE_URL;
const vaultId = args.get("--vault-id") || process.env.PKB_VAULT_ID;
const deviceId = args.get("--device-id") || process.env.PKB_DEVICE_ID;
const bearerToken = args.get("--bearer-token") || process.env.PKB_BEARER_TOKEN;
const activityLimit = args.get("--activity-limit") || process.env.PKB_SYNC_ACTIVITY_LIMIT || "20";
const nowMs = args.get("--now-ms") || process.env.PKB_SYNC_NOW_MS;
const outputPath = resolve(
  args.get("--output-json") || process.env.PKB_SYNC_SNAPSHOT_OUTPUT || defaultOutputPath,
);

const missing = [
  ["--vault-root", vaultRoot],
  ["--base-url", baseUrl],
  ["--vault-id", vaultId],
  ["--device-id", deviceId],
].filter(([, value]) => !value);

if (missing.length) {
  printUsage();
  console.error(`Missing required settings: ${missing.map(([name]) => name).join(", ")}`);
  process.exit(1);
}

const pythonPath = [join(repoRoot, "packages", "vault-core", "src"), repoRoot].join(delimiter);
const cliArgs = [
  "-m",
  "clients.desktop.cli",
  "--vault-root",
  vaultRoot,
  "--base-url",
  baseUrl,
  "--vault-id",
  vaultId,
  "--device-id",
  deviceId,
];

if (bearerToken) {
  cliArgs.push("--bearer-token", bearerToken);
}

cliArgs.push(
  "sync-shell-snapshot",
  "--activity-limit",
  activityLimit,
  "--output-json",
  outputPath,
);

if (nowMs) {
  cliArgs.push("--now-ms", nowMs);
}

const result = spawnSync(process.env.PYTHON_BIN || "python", cliArgs, {
  cwd: repoRoot,
  stdio: "inherit",
  env: {
    ...process.env,
    PYTHONPATH: process.env.PYTHONPATH
      ? `${pythonPath}${delimiter}${process.env.PYTHONPATH}`
      : pythonPath,
  },
});

if (result.error) {
  throw result.error;
}

if (result.status !== 0) {
  process.exit(result.status ?? 1);
}

console.log(`Exported live sync shell snapshot to ${outputPath}`);

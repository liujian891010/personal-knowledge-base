import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { delimiter } from "node:path";
import { spawnSync } from "node:child_process";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "..");
const repoRoot = resolve(appRoot, "..", "..", "..");
const defaultSnapshotPath = join(appRoot, "fixtures", "live-sync-shell.json");
const localBridgeConfigPath = join(appRoot, "bridge.local.json");

export function printBridgeUsage() {
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

Optional local config file:
  apps/frontend/noteapp-web/bridge.local.json
`);
}

export function readArgMap(argv) {
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

function loadLocalBridgeConfig() {
  if (!existsSync(localBridgeConfigPath)) {
    return {};
  }
  const payload = JSON.parse(readFileSync(localBridgeConfigPath, "utf-8"));
  return payload && typeof payload === "object" ? payload : {};
}

export function resolveBridgeConfig({ args = new Map(), env = process.env } = {}) {
  const localConfig = loadLocalBridgeConfig();
  return {
    vaultRoot: args.get("--vault-root") || env.PKB_VAULT_ROOT || localConfig.vaultRoot || null,
    baseUrl: args.get("--base-url") || env.PKB_BASE_URL || localConfig.baseUrl || null,
    vaultId: args.get("--vault-id") || env.PKB_VAULT_ID || localConfig.vaultId || null,
    deviceId: args.get("--device-id") || env.PKB_DEVICE_ID || localConfig.deviceId || null,
    bearerToken: args.get("--bearer-token") || env.PKB_BEARER_TOKEN || localConfig.bearerToken || null,
    activityLimit:
      args.get("--activity-limit") || env.PKB_SYNC_ACTIVITY_LIMIT || localConfig.activityLimit || "20",
    nowMs: args.get("--now-ms") || env.PKB_SYNC_NOW_MS || localConfig.nowMs || null,
    outputJson: resolve(
      args.get("--output-json") ||
        env.PKB_SYNC_SNAPSHOT_OUTPUT ||
        localConfig.outputJson ||
        defaultSnapshotPath,
    ),
    pythonBin: env.PYTHON_BIN || localConfig.pythonBin || "python",
    configSource: existsSync(localBridgeConfigPath) ? localBridgeConfigPath : "env",
  };
}

export function listMissingBridgeSettings(config) {
  return [
    ["--vault-root", config.vaultRoot],
    ["--base-url", config.baseUrl],
    ["--vault-id", config.vaultId],
    ["--device-id", config.deviceId],
  ]
    .filter(([, value]) => !value)
    .map(([name]) => name);
}

function buildPythonPath(env = process.env) {
  const rootPath = [join(repoRoot, "packages", "vault-core", "src"), repoRoot].join(delimiter);
  return env.PYTHONPATH ? `${rootPath}${delimiter}${env.PYTHONPATH}` : rootPath;
}

export function buildDesktopCliPrefix(config) {
  const cliArgs = [
    "-m",
    "clients.desktop.cli",
    "--vault-root",
    config.vaultRoot,
    "--base-url",
    config.baseUrl,
    "--vault-id",
    config.vaultId,
    "--device-id",
    config.deviceId,
  ];
  if (config.bearerToken) {
    cliArgs.push("--bearer-token", config.bearerToken);
  }
  return cliArgs;
}

export function runDesktopCliJson(commandArgs, { config, env = process.env, stdio = "pipe" }) {
  const result = spawnSync(config.pythonBin, [...buildDesktopCliPrefix(config), ...commandArgs], {
    cwd: repoRoot,
    stdio,
    encoding: "utf-8",
    env: {
      ...env,
      PYTHONPATH: buildPythonPath(env),
    },
  });

  if (result.error) {
    throw result.error;
  }

  if (result.status !== 0) {
    const stderr = typeof result.stderr === "string" ? result.stderr.trim() : "";
    throw new Error(stderr || `desktop CLI exited with status ${result.status ?? 1}`);
  }

  if (stdio === "inherit") {
    return null;
  }

  return JSON.parse(result.stdout);
}

export function buildSnapshotCommandArgs(config, overrides = {}) {
  const commandArgs = [
    "sync-shell-snapshot",
    "--activity-limit",
    String(overrides.activityLimit || config.activityLimit),
    "--output-json",
    overrides.outputJson || config.outputJson,
  ];
  const resolvedNowMs = overrides.nowMs || config.nowMs;
  if (resolvedNowMs) {
    commandArgs.push("--now-ms", String(resolvedNowMs));
  }
  return commandArgs;
}

export function buildExecuteActionArgs(actionId, nowMs) {
  const commandArgs = ["execute-sync-action", "--action-id", actionId];
  if (nowMs) {
    commandArgs.push("--now-ms", String(nowMs));
  }
  return commandArgs;
}

export function buildExecuteActionAndSnapshotArgs(actionId, nowMs, activityLimit) {
  const commandArgs = [
    "execute-sync-action-and-snapshot",
    "--action-id",
    actionId,
    "--activity-limit",
    String(activityLimit),
  ];
  if (nowMs) {
    commandArgs.push("--now-ms", String(nowMs));
  }
  return commandArgs;
}

export { appRoot, defaultSnapshotPath, localBridgeConfigPath, repoRoot };

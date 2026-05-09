import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "..");
const repoRoot = resolve(appRoot, "..", "..", "..");
const defaultSnapshotPath = join(appRoot, "fixtures", "live-sync-shell.json");
const localBridgeConfigPath = join(appRoot, "bridge.local.json");
const BRIDGE_MODE = "desktop-cli-local";

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

function createDiagnostic(level, code, message, details = null) {
  return {
    level,
    code,
    message,
    details,
  };
}

function loadLocalBridgeConfig() {
  if (!existsSync(localBridgeConfigPath)) {
    return {
      config: {},
      diagnostics: [],
    };
  }
  try {
    const payload = JSON.parse(readFileSync(localBridgeConfigPath, "utf-8"));
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      return {
        config: {},
        diagnostics: [
          createDiagnostic(
            "warning",
            "bridge_local_config_invalid_shape",
            "bridge.local.json must contain a top-level JSON object.",
            {
              path: localBridgeConfigPath,
            },
          ),
        ],
      };
    }
    return {
      config: payload,
      diagnostics: [
        createDiagnostic("info", "bridge_local_config_loaded", "Loaded bridge.local.json.", {
          path: localBridgeConfigPath,
        }),
      ],
    };
  } catch (error) {
    return {
      config: {},
      diagnostics: [
        createDiagnostic(
          "danger",
          "bridge_local_config_invalid_json",
          "bridge.local.json could not be parsed.",
          {
            path: localBridgeConfigPath,
            reason: error instanceof Error ? error.message : String(error),
          },
        ),
      ],
    };
  }
}

function resolveConfigValue(argValue, envValue, localValue, fallback = null) {
  return argValue || envValue || localValue || fallback;
}

function resolveConfigSource(argValue, envValue, localValue, fallbackLabel = null) {
  if (argValue) {
    return "cli_arg";
  }
  if (envValue) {
    return "env";
  }
  if (localValue) {
    return localBridgeConfigPath;
  }
  return fallbackLabel;
}

function resolveConfigSourceSummary(sourceByField) {
  const distinctSources = [...new Set(Object.values(sourceByField).filter(Boolean))];
  if (distinctSources.length === 0) {
    return "unconfigured";
  }
  if (distinctSources.length === 1) {
    return distinctSources[0];
  }
  return distinctSources.join(" + ");
}

export function resolveBridgeConfig({ args = new Map(), env = process.env } = {}) {
  const localConfig = loadLocalBridgeConfig();
  const sourceByField = {
    vaultRoot: resolveConfigSource(
      args.get("--vault-root"),
      env.PKB_VAULT_ROOT,
      localConfig.config.vaultRoot,
    ),
    baseUrl: resolveConfigSource(
      args.get("--base-url"),
      env.PKB_BASE_URL,
      localConfig.config.baseUrl,
    ),
    vaultId: resolveConfigSource(args.get("--vault-id"), env.PKB_VAULT_ID, localConfig.config.vaultId),
    deviceId: resolveConfigSource(
      args.get("--device-id"),
      env.PKB_DEVICE_ID,
      localConfig.config.deviceId,
    ),
    bearerToken: resolveConfigSource(
      args.get("--bearer-token"),
      env.PKB_BEARER_TOKEN,
      localConfig.config.bearerToken,
    ),
    activityLimit: resolveConfigSource(
      args.get("--activity-limit"),
      env.PKB_SYNC_ACTIVITY_LIMIT,
      localConfig.config.activityLimit,
      "default:20",
    ),
    nowMs: resolveConfigSource(args.get("--now-ms"), env.PKB_SYNC_NOW_MS, localConfig.config.nowMs),
    outputJson: resolveConfigSource(
      args.get("--output-json"),
      env.PKB_SYNC_SNAPSHOT_OUTPUT,
      localConfig.config.outputJson,
      defaultSnapshotPath,
    ),
    pythonBin: resolveConfigSource(undefined, env.PYTHON_BIN, localConfig.config.pythonBin, "default:python"),
  };
  return {
    vaultRoot: resolveConfigValue(args.get("--vault-root"), env.PKB_VAULT_ROOT, localConfig.config.vaultRoot),
    baseUrl: resolveConfigValue(args.get("--base-url"), env.PKB_BASE_URL, localConfig.config.baseUrl),
    vaultId: resolveConfigValue(args.get("--vault-id"), env.PKB_VAULT_ID, localConfig.config.vaultId),
    deviceId: resolveConfigValue(args.get("--device-id"), env.PKB_DEVICE_ID, localConfig.config.deviceId),
    bearerToken: resolveConfigValue(
      args.get("--bearer-token"),
      env.PKB_BEARER_TOKEN,
      localConfig.config.bearerToken,
    ),
    activityLimit: resolveConfigValue(
      args.get("--activity-limit"),
      env.PKB_SYNC_ACTIVITY_LIMIT,
      localConfig.config.activityLimit,
      "20",
    ),
    nowMs: resolveConfigValue(args.get("--now-ms"), env.PKB_SYNC_NOW_MS, localConfig.config.nowMs),
    outputJson: resolve(
      resolveConfigValue(
        args.get("--output-json"),
        env.PKB_SYNC_SNAPSHOT_OUTPUT,
        localConfig.config.outputJson,
        defaultSnapshotPath,
      ),
    ),
    pythonBin: resolveConfigValue(undefined, env.PYTHON_BIN, localConfig.config.pythonBin, "python"),
    bridgeMode: BRIDGE_MODE,
    configSource: resolveConfigSourceSummary(sourceByField),
    sourceByField,
    diagnostics: localConfig.diagnostics,
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

function createBridgeCommandError(code, message, details = {}) {
  const error = new Error(message);
  error.code = code;
  error.details = details;
  return error;
}

function buildOutputPreview(output) {
  if (typeof output !== "string") {
    return "";
  }
  return output.trim().slice(0, 400);
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
    throw createBridgeCommandError(
      "desktop_cli_spawn_failed",
      `Unable to start desktop CLI with ${config.pythonBin}.`,
      {
        pythonBin: config.pythonBin,
        reason: result.error.message,
      },
    );
  }

  if (result.status !== 0) {
    const stderr = typeof result.stderr === "string" ? result.stderr.trim() : "";
    throw createBridgeCommandError(
      "desktop_cli_failed",
      stderr || `desktop CLI exited with status ${result.status ?? 1}.`,
      {
        status: result.status ?? 1,
        stderr,
        stdoutPreview: buildOutputPreview(result.stdout),
      },
    );
  }

  if (stdio === "inherit") {
    return null;
  }

  try {
    return JSON.parse(result.stdout);
  } catch (error) {
    throw createBridgeCommandError("desktop_cli_invalid_json", "Desktop CLI returned invalid JSON.", {
      reason: error instanceof Error ? error.message : String(error),
      stdoutPreview: buildOutputPreview(result.stdout),
    });
  }
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

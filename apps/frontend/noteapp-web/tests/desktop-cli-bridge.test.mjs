import assert from "node:assert/strict";
import { existsSync, rmSync, writeFileSync } from "node:fs";

import {
  buildExecuteActionAndSnapshotArgs,
  buildSnapshotCommandArgs,
  localBridgeConfigPath,
  resolveBridgeConfig,
} from "../scripts/desktop-cli-bridge.mjs";

function cleanupLocalBridgeConfig() {
  if (existsSync(localBridgeConfigPath)) {
    rmSync(localBridgeConfigPath, { force: true });
  }
}

export async function runDesktopCliBridgeTests() {
  const completed = [];

  cleanupLocalBridgeConfig();
  try {
    writeFileSync(
      localBridgeConfigPath,
      JSON.stringify({
        vaultRoot: "C:/local-vault",
        baseUrl: "https://local.example.com",
        vaultId: "vault-local",
        deviceId: "device-local",
        activityLimit: 18,
        pythonBin: "python-local",
      }),
      "utf-8",
    );

    const config = resolveBridgeConfig({
      args: new Map([
        ["--vault-root", "C:/cli-vault"],
        ["--activity-limit", "25"],
      ]),
      env: {
        PKB_BASE_URL: "https://env.example.com",
        PKB_VAULT_ID: "vault-env",
        PKB_DEVICE_ID: "device-env",
      },
    });

    assert.equal(config.vaultRoot, "C:/cli-vault");
    assert.equal(config.baseUrl, "https://env.example.com");
    assert.equal(config.vaultId, "vault-env");
    assert.equal(config.deviceId, "device-env");
    assert.equal(config.activityLimit, "25");
    assert.equal(config.pythonBin, "python-local");
    assert.equal(config.sourceByField.vaultRoot, "cli_arg");
    assert.equal(config.sourceByField.baseUrl, "env");
    assert.equal(config.sourceByField.pythonBin, localBridgeConfigPath);
    assert.match(config.configSource, /cli_arg/);
    assert.match(config.configSource, /env/);
    assert.match(config.configSource, /bridge\.local\.json/);
    assert.equal(config.diagnostics[0]?.code, "bridge_local_config_loaded");
    completed.push("resolveBridgeConfig merges cli, env, and local sources by priority");
  } finally {
    cleanupLocalBridgeConfig();
  }

  writeFileSync(localBridgeConfigPath, "{ invalid json", "utf-8");
  try {
    const config = resolveBridgeConfig({
      env: {},
    });

    assert.equal(config.vaultRoot, null);
    assert.equal(config.diagnostics[0]?.code, "bridge_local_config_invalid_json");
    completed.push("resolveBridgeConfig reports invalid local config without throwing");
  } finally {
    cleanupLocalBridgeConfig();
  }

  const commandArgs = buildSnapshotCommandArgs(
    {
      activityLimit: "20",
      outputJson: "C:/snapshots/current.json",
      nowMs: "123",
    },
    {
      activityLimit: 40,
      outputJson: "C:/snapshots/override.json",
      nowMs: 999,
    },
  );

  assert.deepEqual(commandArgs, [
    "sync-shell-snapshot",
    "--activity-limit",
    "40",
    "--output-json",
    "C:/snapshots/override.json",
    "--now-ms",
    "999",
  ]);
  completed.push("buildSnapshotCommandArgs applies overrides and output path");

  assert.deepEqual(buildExecuteActionAndSnapshotArgs("sync-now", 456, 12), [
    "execute-sync-action-and-snapshot",
    "--action-id",
    "sync-now",
    "--activity-limit",
    "12",
    "--now-ms",
    "456",
  ]);
  completed.push("buildExecuteActionAndSnapshotArgs keeps action id and limit together");

  return completed;
}

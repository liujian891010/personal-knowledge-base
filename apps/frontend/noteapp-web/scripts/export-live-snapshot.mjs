import {
  buildSnapshotCommandArgs,
  listMissingBridgeSettings,
  printBridgeUsage,
  readArgMap,
  resolveBridgeConfig,
  runDesktopCliJson,
} from "./desktop-cli-bridge.mjs";

const args = readArgMap(process.argv.slice(2));
if (args.has("--help")) {
  printBridgeUsage();
  process.exit(0);
}

const config = resolveBridgeConfig({ args });
const missing = listMissingBridgeSettings(config);
if (missing.length) {
  printBridgeUsage();
  console.error(`Missing required settings: ${missing.join(", ")}`);
  process.exit(1);
}

runDesktopCliJson(buildSnapshotCommandArgs(config), {
  config,
  stdio: "inherit",
});

console.log(`Exported live sync shell snapshot to ${config.outputJson}`);

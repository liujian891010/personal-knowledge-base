import { runBridgeClientTests } from "../tests/bridge-client.test.mjs";
import { runDesktopCliBridgeTests } from "../tests/desktop-cli-bridge.test.mjs";

const suites = [
  ["desktop-cli-bridge", runDesktopCliBridgeTests],
  ["bridge-client", runBridgeClientTests],
];

let failureCount = 0;

for (const [suiteName, runSuite] of suites) {
  try {
    const results = await runSuite();
    for (const testName of results) {
      console.log(`ok ${suiteName} :: ${testName}`);
    }
  } catch (error) {
    failureCount += 1;
    console.error(`not ok ${suiteName}`);
    console.error(error instanceof Error ? error.stack || error.message : String(error));
  }
}

if (failureCount > 0) {
  process.exitCode = 1;
} else {
  console.log(`All noteapp-web bridge tests passed (${suites.length} suites).`);
}

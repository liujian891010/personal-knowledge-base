import fixture from '../fixtures/local-settings-snapshot.example.json';
import { parseLocalSettingsSnapshot, summarizeLocalSettingsSnapshot } from '../src/settingsSnapshot';

const snapshot = parseLocalSettingsSnapshot(fixture);
const summary = summarizeLocalSettingsSnapshot(snapshot);

if (summary.schemaVersion !== 'v1') {
  throw new Error(`unexpected settings schema version: ${summary.schemaVersion}`);
}
if (!summary.vaultId || !summary.deviceId) {
  throw new Error('settings summary is missing vault or device identity');
}

console.log(`settings snapshot fixture ok: ${summary.vaultId}/${summary.deviceId}`);

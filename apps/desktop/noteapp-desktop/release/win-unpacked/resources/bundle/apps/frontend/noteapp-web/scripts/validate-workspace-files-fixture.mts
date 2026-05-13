import fixture from '../fixtures/workspace-files.example.json';
import { parseWorkspaceFilesSnapshot, summarizeWorkspaceFilesSnapshot } from '../src/workspaceFiles';

const snapshot = parseWorkspaceFilesSnapshot(fixture);
const summary = summarizeWorkspaceFilesSnapshot(snapshot);

if (summary.schemaVersion !== 'v1') {
  throw new Error(`unexpected workspace files schema version: ${summary.schemaVersion}`);
}
if (summary.totalCount !== snapshot.files.length) {
  throw new Error('workspace files total count does not match files length');
}

console.log(`workspace files fixture ok: ${summary.vaultId}/${summary.totalCount}`);

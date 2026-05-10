import fixture from '../fixtures/workspace-file-content.example.json';
import { parseWorkspaceFileContent } from '../src/workspaceFileContent';

const content = parseWorkspaceFileContent(fixture);

if (content.schema_version !== 'v1') {
  throw new Error(`unexpected workspace file content schema version: ${content.schema_version}`);
}
if (!content.text.includes('# Product Roadmap')) {
  throw new Error('workspace file content fixture text was not parsed');
}

console.log(`workspace file content fixture ok: ${content.file_id}`);

import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('..', import.meta.url)));

function readProjectFile(relativePath) {
  return readFileSync(resolve(root, relativePath), 'utf8');
}

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

function extractNavItemsBlock(appSource) {
  const match = appSource.match(/const navItems: NavItem\[\] = \[([\s\S]*?)\];/);
  assert(match, 'navItems block was not found in src/App.tsx');
  return match[1];
}

const appSource = readProjectFile('src/App.tsx');
const desktopSource = readProjectFile('src/desktop.ts');
const explorerSource = readProjectFile('src/views/ExplorerView.tsx');
const graphSource = readProjectFile('src/views/GraphView.tsx');
const trashSource = readProjectFile('src/views/TrashView.tsx');
const settingsSource = readProjectFile('src/views/SettingsView.tsx');
const localSettingsSource = readProjectFile('src/useLocalSettingsSnapshot.ts');
const navItemsBlock = extractNavItemsBlock(appSource);

const openMainViews = ['explorer', 'ai-chat', 'graph', 'trash', 'settings'];
const hiddenViews = ['sync', 'conflicts', 'ai-wiki'];

for (const viewId of openMainViews) {
  assert(
    navItemsBlock.includes(`id: '${viewId}'`),
    `main nav is missing expected V1 entry: ${viewId}`,
  );
}

for (const viewId of hiddenViews) {
  assert(
    !navItemsBlock.includes(`id: '${viewId}'`),
    `main nav exposes hidden V1 entry before its acceptance task: ${viewId}`,
  );
}

assert(
  appSource.includes('activeWorkspace: RegisteredWorkspace | null'),
  'TopBar does not accept activeWorkspace from the registry controller',
);
assert(
  appSource.includes('workspaces: RegisteredWorkspace[]'),
  'TopBar does not accept real workspace registry entries',
);
assert(
  !appSource.includes('const activeWorkspace = null;'),
  'TopBar still contains the activeWorkspace stub',
);
assert(
  !appSource.includes('const onSelectWorkspaceFolder = () => {};'),
  'TopBar still contains the workspace selection stub',
);
assert(
  appSource.includes('onActivateWorkspace={handleActivateWorkspace}'),
  'TopBar is not wired to the real workspace activation handler',
);
assert(
  appSource.includes('onDeleteWorkspace={(workspaceId) => void handleDeleteWorkspace(workspaceId)}'),
  'TopBar is not wired to the real workspace delete handler',
);
assert(
  desktopSource.includes('exportDesktopDiagnostics') && appSource.includes('handleExportDiagnostics'),
  'Desktop diagnostics export is not exposed from the main shell',
);
assert(
  explorerSource.includes('useSyncShellController(source === \'bridge\')'),
  'Explorer does not load sync state from the live workspace bridge',
);
assert(
  explorerSource.includes('syncSummary.changeBadgeCount > 0'),
  'Explorer does not surface pending local changes after edits',
);
assert(
  explorerSource.includes('executePrimarySyncAction'),
  'Explorer sync prompt is not wired to the executable primary sync action',
);
assert(
  appSource.includes("onOpenConflicts={() => setCurrentView('conflicts')}"),
  'Explorer conflict prompt is not wired to the conflict review view',
);
assert(
  explorerSource.includes("syncSummary.primaryActionId === 'list-conflicts'"),
  'Explorer does not route conflict sync state to the conflict review entry',
);
assert(
  explorerSource.includes('/api/workspace/attachments') || readProjectFile('src/useWorkspaceFiles.ts').includes('/api/workspace/attachments'),
  'Workspace file controller does not expose attachment import through the bridge',
);
assert(
  explorerSource.includes('markdownAttachmentLink'),
  'Explorer does not insert a workspace-relative attachment link into Markdown',
);
assert(
  explorerSource.includes("file.type === 'attachment'"),
  'Explorer file tree does not render attachment entries',
);
assert(
  explorerSource.includes('/api/workspace/files/${encodeURIComponent(fileId)}/blob'),
  'Explorer does not load attachment blob previews from the bridge',
);
assert(
  explorerSource.includes("attachmentPreviewKind(attachmentPreview.mime_type) === 'pdf'"),
  'Explorer does not expose a PDF preview path for attachments',
);
assert(
  explorerSource.includes("['note', 'attachment'].includes(selectedFile.type)"),
  'Explorer delete action is not enabled for attachment trash flow',
);
assert(
  explorerSource.includes('explainWorkspaceSearchResult') && explorerSource.includes('renderHighlightedSearchText'),
  'Explorer search results do not expose explainable matching and highlighting',
);
assert(
  explorerSource.includes('await loadLinks(created.file_id)'),
  'Explorer unresolved wiki-link creation does not refresh the created note backlinks',
);
assert(
  graphSource.includes('useWorkspaceLinksController'),
  'GraphView is not wired to workspace links',
);
assert(
  trashSource.includes('pendingTrashAction') && trashSource.includes('confirmPendingTrashAction'),
  'TrashView high-risk delete actions do not require a consequence confirmation',
);
assert(
  graphSource.includes('links?.outgoing') && graphSource.includes('links?.backlinks'),
  'GraphView does not build nodes and edges from outgoing/backlink links data',
);
assert(
  !graphSource.includes('left-[30%]') && !graphSource.includes('x1="50%"'),
  'GraphView still contains static sample graph coordinates',
);

for (const [name, source] of [
  ['src/useLocalSettingsSnapshot.ts', localSettingsSource],
  ['src/views/SettingsView.tsx', settingsSource],
]) {
  assert(
    !source.includes('/api/workspace/select-folder'),
    `${name} still references the old single-workspace folder picker API`,
  );
  assert(
    !source.includes('/api/workspace/root'),
    `${name} still references the old single-workspace root API`,
  );
  assert(
    !source.includes('selectWorkspaceRoot'),
    `${name} still references selectWorkspaceRoot`,
  );
  assert(
    !source.includes('saveWorkspaceRoot'),
    `${name} still references saveWorkspaceRoot`,
  );
}

console.log('entry policy smoke passed');

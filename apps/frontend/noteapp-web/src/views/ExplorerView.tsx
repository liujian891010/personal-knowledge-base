import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  Columns2,
  Cloud,
  Code2,
  Edit3,
  Eye,
  Folder,
  Info,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Save,
  Search,
  Trash2,
} from 'lucide-react';

import { useWorkspaceFilesController } from '../useWorkspaceFiles';
import { useWorkspaceFileContentController } from '../useWorkspaceFileContent';
import { useWorkspaceLinksController } from '../useWorkspaceLinks';
import { useWorkspaceSearchController } from '../useWorkspaceSearch';
import type { AiContextDraft } from '../aiContext';
import type { WorkspaceFileEntry } from '../workspaceFiles';
import type { WorkspaceNoteLink } from '../workspaceLinks';

const explorerCollapsedFoldersStoragePrefix = 'noteapp.explorer.collapsedFolders.v1';
const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

function fileName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

function workspaceRootName(path: string): string {
  return fileName(path) || '工作区';
}

function folderPathForFile(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.slice(0, -1).join('/');
}

function fileBelongsToFolder(path: string, folderPath: string): boolean {
  const fileFolder = folderPathForFile(path);
  if (!folderPath) {
    return true;
  }
  return fileFolder === folderPath || fileFolder.startsWith(`${folderPath}/`);
}

function isSystemAiPath(path: string): boolean {
  return path === '.ai' || path.startsWith('.ai/');
}

function isEditableWorkspaceFile(file: WorkspaceFileEntry | null): boolean {
  if (!file) {
    return false;
  }
  return ['note', 'ai_index', 'ai_wiki', 'ai_agents'].includes(file.type);
}

function isAiContextEligibleFile(file: WorkspaceFileEntry): boolean {
  return file.type === 'note' && file.status === 'active' && file.exists_on_disk && !isSystemAiPath(file.path);
}

function isVisibleMarkdownFile(file: WorkspaceFileEntry): boolean {
  return file.type === 'note' && file.status !== 'deleted' && !isSystemAiPath(file.path);
}

function notePathFromWikiLink(linkText: string): string {
  const target = linkText.split('|', 1)[0].split('#', 1)[0].trim();
  const safeName = target.replace(/[<>:"/\\|?*\x00-\x1f]+/g, '-').replace(/\s+/g, ' ').trim();
  return `Notes/${safeName || 'Untitled'}.md`;
}

function folderAncestors(folderPath: string): string[] {
  const parts = folderPath.split('/').filter(Boolean);
  const ancestors = [''];
  for (let index = 0; index < parts.length; index += 1) {
    ancestors.push(parts.slice(0, index + 1).join('/'));
  }
  return ancestors;
}

function isDescendantOfCollapsedFolder(path: string, collapsedFolders: Set<string>): boolean {
  if (path === '') {
    return collapsedFolders.has('');
  }
  const parts = path.split('/').filter(Boolean);
  for (let index = 0; index < parts.length; index += 1) {
    const ancestorPath = parts.slice(0, index + 1).join('/');
    if (collapsedFolders.has(ancestorPath)) {
      return true;
    }
  }
  return collapsedFolders.has('');
}

type ExplorerRow =
  | {
    kind: 'folder';
    id: string;
    name: string;
    path: string;
    depth: number;
    count: number;
  }
  | {
    kind: 'file';
    id: string;
    file: WorkspaceFileEntry;
    depth: number;
  };

type MarkdownEditorMode = 'edit' | 'preview' | 'split';
type FileDialogState =
  | { kind: 'create'; path: string }
  | { kind: 'rename'; path: string }
  | { kind: 'delete'; path: string };
function isRowVisible(row: ExplorerRow, collapsedFolders: Set<string>): boolean {
  if (row.kind === 'folder') {
    if (row.path === '') {
      return true;
    }
    const parentPath = row.path.split('/').slice(0, -1).join('/');
    return !isDescendantOfCollapsedFolder(parentPath, collapsedFolders);
  }

  return !isDescendantOfCollapsedFolder(folderPathForFile(row.file.path), collapsedFolders);
}

function collapsedFoldersStorageKey(vaultRoot: string): string {
  return `${explorerCollapsedFoldersStoragePrefix}:${vaultRoot || 'default'}`;
}

function readCollapsedFolders(storageKey: string): Set<string> {
  try {
    const rawValue = window.localStorage.getItem(storageKey);
    if (!rawValue) {
      return new Set();
    }
    const parsed: unknown = JSON.parse(rawValue);
    if (!Array.isArray(parsed)) {
      return new Set();
    }
    return new Set(parsed.filter((value): value is string => typeof value === 'string'));
  } catch {
    return new Set();
  }
}

function writeCollapsedFolders(storageKey: string, collapsedFolders: Set<string>) {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(Array.from(collapsedFolders)));
  } catch {
    // 浏览器可能禁用了 localStorage；目录折叠仍可在当前会话内使用。
  }
}

function MarkdownFileIcon({ size = 16, tone = 'normal' }: { size?: number; tone?: 'normal' | 'danger' }) {
  const iconClassName = tone === 'danger' ? 'text-[#e94560]' : 'text-[#a9c8fc]';
  return (
    <svg
      aria-hidden="true"
      viewBox="0 0 512 512"
      width={size}
      height={size}
      className={`flex-shrink-0 ${iconClassName}`}
      fill="currentColor"
    >
      <path d="M475.64 95.36H36.36A36.4 36.4 0 0 0 0 131.72v248.56a36.4 36.4 0 0 0 36.36 36.36h439.28A36.4 36.4 0 0 0 512 380.28V131.72a36.4 36.4 0 0 0-36.36-36.36ZM283.91 320h-54.82v-94.55l-40.91 51.14-40.91-51.14V320H92.45V192h54.82l40.91 54.55L229.09 192h54.82Zm68.36 0-68.36-64h45.45v-64h45.46v64h45.45Z" />
    </svg>
  );
}

function isMarkdownBoundary(line: string): boolean {
  return (
    /^```/.test(line)
    || /^#{1,6}\s+/.test(line)
    || /^\s*[-*+]\s+/.test(line)
    || /^\s*\d+\.\s+/.test(line)
    || /^>\s?/.test(line)
    || /^\s*([-*_])\s*(\1\s*){2,}$/.test(line)
  );
}

function renderInlineMarkdown(
  text: string,
  wikiLinkByText: Map<string, WorkspaceNoteLink> = new Map(),
  onOpenWikiLink?: (fileId: string) => void,
): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*\n]+?\*\*|\*[^*\n]+?\*|\[\[[^\]\n]+\]\]|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    const key = `${match.index}-${token}`;
    const linkMatch = /^\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(token);
    const wikiLinkMatch = /^\[\[([^\]\n]+)\]\]$/.exec(token);

    if (token.startsWith('`') && token.endsWith('`')) {
      nodes.push(
        <code key={key} className="rounded bg-[#0b1020] px-1.5 py-0.5 font-mono text-[0.92em] text-[#ffb782]">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith('**') && token.endsWith('**')) {
      nodes.push(<strong key={key} className="font-bold text-[#f3f4f6]">{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('*') && token.endsWith('*')) {
      nodes.push(<em key={key} className="italic text-slate-200">{token.slice(1, -1)}</em>);
    } else if (linkMatch) {
      nodes.push(
        <a
          key={key}
          href={linkMatch[2]}
          target="_blank"
          rel="noreferrer"
          className="text-[#a9c8fc] underline decoration-[#a9c8fc]/40 underline-offset-4 hover:text-white"
        >
          {linkMatch[1]}
        </a>,
      );
    } else if (wikiLinkMatch) {
      const linkText = wikiLinkMatch[1].trim();
      const link = wikiLinkByText.get(linkText);
      const targetFileId = link?.target_file_id ?? null;
      nodes.push(
        <button
          key={key}
          type="button"
          disabled={!targetFileId}
          onClick={() => {
            if (targetFileId) {
              onOpenWikiLink?.(targetFileId);
            }
          }}
          title={link?.target_path ?? `Unresolved link: ${linkText}`}
          className={`rounded px-1.5 py-0.5 font-semibold ${
            targetFileId
              ? 'bg-[#0f3460]/50 text-[#a9c8fc] hover:bg-[#0f3460] hover:text-white'
              : 'bg-[#3b2330] text-[#ffb782] cursor-help'
          }`}
        >
          [[{linkText}]]
        </button>,
      );
    } else {
      nodes.push(token);
    }
    lastIndex = pattern.lastIndex;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }

  return nodes;
}

function renderMarkdownBlocks(
  markdown: string,
  wikiLinkByText: Map<string, WorkspaceNoteLink> = new Map(),
  onOpenWikiLink?: (fileId: string) => void,
): React.ReactNode[] {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const nodes: React.ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    const fenceMatch = /^```([\w-]+)?\s*$/.exec(line);
    if (fenceMatch) {
      const language = fenceMatch[1];
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) {
        index += 1;
      }
      nodes.push(
        <pre key={`code-${index}`} className="overflow-auto rounded-lg border border-[#0f3460] bg-[#0b1020] p-4 text-[12px] leading-relaxed text-slate-200">
          {language && <div className="mb-3 font-mono text-[10px] uppercase tracking-wider text-slate-500">{language}</div>}
          <code>{codeLines.join('\n')}</code>
        </pre>,
      );
      continue;
    }

    const headingMatch = /^(#{1,6})\s+(.+)$/.exec(line);
    if (headingMatch) {
      const level = Math.min(headingMatch[1].length, 6);
      const headingClass = [
        'text-3xl',
        'text-2xl',
        'text-xl',
        'text-lg',
        'text-base',
        'text-sm',
      ][level - 1];
      nodes.push(
        React.createElement(
          `h${level}`,
          {
            key: `heading-${index}`,
            className: `${headingClass} font-bold leading-tight text-[#f3f4f6]`,
          },
          renderInlineMarkdown(headingMatch[2], wikiLinkByText, onOpenWikiLink),
        ),
      );
      index += 1;
      continue;
    }

    if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
      nodes.push(<hr key={`hr-${index}`} className="border-[#0f3460]" />);
      index += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ''));
        index += 1;
      }
      nodes.push(
        <blockquote key={`quote-${index}`} className="border-l-4 border-[#a9c8fc] bg-[#0f3460]/20 py-2 pl-4 text-slate-300">
          {quoteLines.map((quoteLine, quoteIndex) => (
            <p key={quoteIndex} className="my-1 leading-relaxed">
              {renderInlineMarkdown(quoteLine, wikiLinkByText, onOpenWikiLink)}
            </p>
          ))}
        </blockquote>,
      );
      continue;
    }

    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*+]\s+/, ''));
        index += 1;
      }
      nodes.push(
        <ul key={`ul-${index}`} className="list-disc space-y-1 pl-6 text-slate-300">
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInlineMarkdown(item, wikiLinkByText, onOpenWikiLink)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*\d+\.\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*\d+\.\s+/, ''));
        index += 1;
      }
      nodes.push(
        <ol key={`ol-${index}`} className="list-decimal space-y-1 pl-6 text-slate-300">
          {items.map((item, itemIndex) => (
            <li key={itemIndex}>{renderInlineMarkdown(item, wikiLinkByText, onOpenWikiLink)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    const paragraphLines: string[] = [];
    while (index < lines.length && lines[index].trim() && !isMarkdownBoundary(lines[index])) {
      paragraphLines.push(lines[index].trim());
      index += 1;
    }
    nodes.push(
      <p key={`p-${index}`} className="leading-7 text-slate-300">
        {renderInlineMarkdown(paragraphLines.join(' '), wikiLinkByText, onOpenWikiLink)}
      </p>,
    );
  }

  return nodes;
}

function MarkdownPreview({
  markdown,
  wikiLinkByText,
  onOpenWikiLink,
}: {
  markdown: string;
  wikiLinkByText?: Map<string, WorkspaceNoteLink>;
  onOpenWikiLink?: (fileId: string) => void;
}) {
  const blocks = useMemo(
    () => renderMarkdownBlocks(markdown, wikiLinkByText, onOpenWikiLink),
    [markdown, onOpenWikiLink, wikiLinkByText],
  );

  if (!markdown.trim()) {
    return (
      <div className="flex h-full items-center justify-center text-[13px] text-slate-500">
        暂无 Markdown 内容。
      </div>
    );
  }

  return (
    <div className="min-h-full space-y-4 p-5 text-[14px]">
      {blocks}
    </div>
  );
}

function buildExplorerRows(files: WorkspaceFileEntry[], rootName: string): ExplorerRow[] {
  const folderPaths = new Set<string>(['']);
  const recursiveDocumentCounts = new Map<string, number>([['', 0]]);
  for (const file of files) {
    const parts = file.path.split(/[\\/]/).filter(Boolean);
    const folderParts = parts.slice(0, -1);
    if (isVisibleMarkdownFile(file)) {
      recursiveDocumentCounts.set('', (recursiveDocumentCounts.get('') ?? 0) + 1);
    }
    for (let index = 0; index < folderParts.length; index += 1) {
      const folderPath = folderParts.slice(0, index + 1).join('/');
      folderPaths.add(folderPath);
      if (isVisibleMarkdownFile(file)) {
        recursiveDocumentCounts.set(folderPath, (recursiveDocumentCounts.get(folderPath) ?? 0) + 1);
      }
    }
  }

  const rows: ExplorerRow[] = [];
  const sortedFolders = Array.from(folderPaths).sort((left, right) => {
    if (left === '') {
      return -1;
    }
    if (right === '') {
      return 1;
    }
    return left.localeCompare(right);
  });

  for (const folderPath of sortedFolders) {
    const depth = folderPath === '' ? 0 : folderPath.split('/').length - 1;
    const folderFiles = files
      .filter((file) => folderPathForFile(file.path) === folderPath)
      .sort((left, right) => left.path.localeCompare(right.path));
    rows.push({
      kind: 'folder',
      id: `folder:${folderPath || '__root__'}`,
      name: folderPath ? fileName(folderPath) : rootName,
      path: folderPath,
      depth,
      count: recursiveDocumentCounts.get(folderPath) ?? 0,
    });
    for (const file of folderFiles) {
      rows.push({
        kind: 'file',
        id: `file:${file.file_id}`,
        file,
        depth: folderPath === '' ? 1 : folderPath.split('/').length,
      });
    }
  }
  return rows;
}

function formatBytes(value: number | null): string {
  if (value === null) {
    return '缺失';
  }
  if (value < 1024) {
    return `${value} 字节`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatFileTime(ms: number): string {
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ms));
}

function sourceLabel(source: string): string {
  if (source === 'bridge') {
    return '本机桥接';
  }
  if (source === 'live-fixture') {
    return '实时快照';
  }
  if (source === 'example') {
    return '示例数据';
  }
  return source;
}

function statusLabel(file: WorkspaceFileEntry): string {
  if (!file.exists_on_disk) {
    return '磁盘缺失';
  }
  if (file.status === 'active') {
    return '正常';
  }
  if (file.status === 'deleted') {
    return '已删除';
  }
  if (file.status === 'conflict_copy') {
    return '冲突副本';
  }
  return file.status;
}

function fileTypeLabel(type: string): string {
  if (type === 'note') {
    return '笔记';
  }
  return type;
}

function statusClasses(file: WorkspaceFileEntry): string {
  if (file.status === 'conflict_copy') {
    return 'text-[#ffb782] bg-[#ffb782]/10 border-[#ffb782]/30';
  }
  if (file.status === 'deleted' || !file.exists_on_disk) {
    return 'text-[#e94560] bg-[#e94560]/10 border-[#e94560]/30';
  }
  return 'text-emerald-300 bg-emerald-400/10 border-emerald-400/30';
}

export default function ExplorerView({
  setView,
  initialSelectedPath,
  initialContextFileIds,
  onInitialSelectedPathConsumed,
  onInitialContextFileIdsConsumed,
  onOpenAiContext,
}: {
  setView: (v: string) => void;
  initialSelectedPath?: string | null;
  initialContextFileIds?: string[] | null;
  onInitialSelectedPathConsumed?: () => void;
  onInitialContextFileIdsConsumed?: () => void;
  onOpenAiContext: (context: AiContextDraft) => void;
}) {
  const {
    summary,
    files,
    source,
    lastError,
    isRefreshing,
    refresh,
    createNote,
    renameNote,
    deleteNote,
  } = useWorkspaceFilesController();
  const {
    content: selectedContent,
    lastError: contentError,
    isLoading: isContentLoading,
    isSaving: isContentSaving,
    isDraftSaving,
    savedAtMs,
    loadContent,
    saveContent,
    loadDraft,
    saveDraft,
    clearDraft,
    clearContent,
  } = useWorkspaceFileContentController();
  const {
    query: searchQuery,
    setQuery: setSearchQuery,
    results: searchResults,
    totalCount: searchResultCount,
    lastError: searchError,
    isSearching,
  } = useWorkspaceSearchController();
  const {
    links: selectedLinks,
    lastError: linksError,
    isLoading: isLinksLoading,
    loadLinks,
    clearLinks,
  } = useWorkspaceLinksController();
  const visibleFiles = useMemo(
    () => (source === 'bridge' ? files.filter(isVisibleMarkdownFile) : []),
    [files, source],
  );
  const rootName = useMemo(() => workspaceRootName(summary.vaultRoot), [summary.vaultRoot]);
  const explorerRows = useMemo(() => buildExplorerRows(visibleFiles, rootName), [rootName, visibleFiles]);
  const folderStorageKey = useMemo(() => collapsedFoldersStorageKey(summary.vaultRoot), [summary.vaultRoot]);
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(() => new Set());
  const [loadedFolderStorageKey, setLoadedFolderStorageKey] = useState<string | null>(null);
  const visibleExplorerRows = useMemo(
    () => explorerRows.filter((row) => isRowVisible(row, collapsedFolders)),
    [collapsedFolders, explorerRows],
  );
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);

  useEffect(() => {
    if (visibleFiles.length === 0) {
      setSelectedFileId(null);
      clearContent();
      return;
    }
    if (!selectedFileId || !visibleFiles.some((file) => file.file_id === selectedFileId)) {
      setSelectedFileId(visibleFiles[0].file_id);
    }
  }, [clearContent, selectedFileId, visibleFiles]);

  useEffect(() => {
    if (!initialSelectedPath) {
      return;
    }
    const targetFile = visibleFiles.find((file) => file.path === initialSelectedPath);
    if (targetFile && targetFile.file_id !== selectedFileId) {
      setSelectedFileId(targetFile.file_id);
    }
    if (targetFile) {
      onInitialSelectedPathConsumed?.();
    }
  }, [initialSelectedPath, onInitialSelectedPathConsumed, selectedFileId, visibleFiles]);

  const selectedFile = visibleFiles.find((file) => file.file_id === selectedFileId) ?? null;
  const selectedFileIsEditable = isEditableWorkspaceFile(selectedFile);
  const selectedFileExistsOnDisk = selectedFile?.exists_on_disk ?? false;
  const selectedFileStatus = selectedFile?.status ?? '';
  const [isInfoPanelOpen, setIsInfoPanelOpen] = useState(false);
  const [draftText, setDraftText] = useState('');
  const [editorMode, setEditorMode] = useState<MarkdownEditorMode>('preview');
  const [pendingDraftRecovery, setPendingDraftRecovery] = useState<{
    fileId: string;
    text: string;
    updatedAt: number | null;
  } | null>(null);
  const [autoSaveStatus, setAutoSaveStatus] = useState<'idle' | 'draft' | 'saving' | 'saved' | 'error'>('idle');
  const [fileMutationError, setFileMutationError] = useState<string | null>(null);
  const [fileDialog, setFileDialog] = useState<FileDialogState | null>(null);
  const [selectedContextFileIds, setSelectedContextFileIds] = useState<Set<string>>(() => new Set());
  const wikiLinkByText = useMemo(() => {
    const result = new Map<string, WorkspaceNoteLink>();
    for (const link of selectedLinks?.outgoing ?? []) {
      result.set(link.link_text, link);
    }
    return result;
  }, [selectedLinks]);

  const openWorkspaceFile = useCallback((fileId: string) => {
    setSelectedFileId(fileId);
  }, []);

  useEffect(() => {
    if (!initialContextFileIds) {
      return;
    }
    setSelectedContextFileIds(new Set(initialContextFileIds));
    onInitialContextFileIdsConsumed?.();
  }, [initialContextFileIds, onInitialContextFileIdsConsumed]);

  const selectedContextFiles = useMemo(
    () => visibleFiles.filter((file) => selectedContextFileIds.has(file.file_id) && isAiContextEligibleFile(file)),
    [selectedContextFileIds, visibleFiles],
  );

  function toggleContextFile(fileId: string) {
    setSelectedContextFileIds((current) => {
      const next = new Set(current);
      if (next.has(fileId)) {
        next.delete(fileId);
      } else {
        next.add(fileId);
      }
      return next;
    });
  }

  function openAiContextForFolder(folderPath: string) {
    const folderFiles = visibleFiles.filter((file) => (
      isAiContextEligibleFile(file)
      && fileBelongsToFolder(file.path, folderPath)
    ));
    onOpenAiContext({
      type: 'folder',
      title: folderPath ? `文件夹：${folderPath}` : `工作区：${rootName}`,
      folderPath,
      fileIds: folderFiles.map((file) => file.file_id),
    });
  }

  function openAiContextForSelectedFiles() {
    if (selectedContextFiles.length === 0) {
      return;
    }
    onOpenAiContext({
      type: 'selected_files',
      title: `已选择 ${selectedContextFiles.length} 个文档`,
      fileIds: selectedContextFiles.map((file) => file.file_id),
    });
    setSelectedContextFileIds(new Set());
  }

  useEffect(() => {
    setCollapsedFolders(readCollapsedFolders(folderStorageKey));
    setLoadedFolderStorageKey(folderStorageKey);
  }, [folderStorageKey]);

  useEffect(() => {
    if (loadedFolderStorageKey !== folderStorageKey) {
      return;
    }
    writeCollapsedFolders(folderStorageKey, collapsedFolders);
  }, [collapsedFolders, folderStorageKey, loadedFolderStorageKey]);

  useEffect(() => {
    if (!selectedFile || loadedFolderStorageKey !== folderStorageKey) {
      return;
    }
    const ancestors = folderAncestors(folderPathForFile(selectedFile.path));
    setCollapsedFolders((current) => {
      if (!ancestors.some((ancestor) => current.has(ancestor))) {
        return current;
      }
      const next = new Set(current);
      for (const ancestor of ancestors) {
        next.delete(ancestor);
      }
      return next;
    });
  }, [folderStorageKey, loadedFolderStorageKey, selectedFile]);

  useEffect(() => {
    if (
      source !== 'bridge'
      || !selectedFile
      || !selectedFileExistsOnDisk
      || selectedFileStatus !== 'active'
      || !selectedFileIsEditable
    ) {
      clearContent();
      clearLinks();
      setPendingDraftRecovery(null);
      return;
    }
    setPendingDraftRecovery(null);
    void loadContent(selectedFile.file_id);
    void loadLinks(selectedFile.file_id);
    void loadDraft(selectedFile.file_id)
      .then((loadedDraft) => {
        if (loadedDraft.has_draft && loadedDraft.text !== null) {
          setPendingDraftRecovery({
            fileId: selectedFile.file_id,
            text: loadedDraft.text,
            updatedAt: loadedDraft.updated_at,
          });
        }
      })
      .catch(() => {
        // Error state is exposed by the content controller.
      });
  }, [
    clearContent,
    clearLinks,
    loadContent,
    loadDraft,
    loadLinks,
    selectedFileId,
    selectedFileExistsOnDisk,
    selectedFileIsEditable,
    selectedFileStatus,
    source,
  ]);

  const isContentDirty = Boolean(selectedContent && draftText !== selectedContent.text);
  const canEditContent = Boolean(
    selectedFile
    && selectedFileIsEditable
    && selectedFile.exists_on_disk
    && selectedFile.status === 'active'
    && selectedContent,
  );

  useEffect(() => {
    setEditorMode('preview');
  }, [selectedFileId]);

  useEffect(() => {
    setDraftText(selectedContent?.text ?? '');
    setAutoSaveStatus('idle');
  }, [selectedContent]);

  useEffect(() => {
    if (
      !selectedFile
      || !selectedContent
      || !canEditContent
      || !isContentDirty
      || isContentLoading
      || isContentSaving
    ) {
      return;
    }

    const fileId = selectedFile.file_id;
    const nextText = draftText;
    const draftTimer = window.setTimeout(() => {
      setAutoSaveStatus('draft');
      void saveDraft(fileId, nextText).catch(() => {
        setAutoSaveStatus('error');
      });
    }, 250);
    const saveTimer = window.setTimeout(() => {
      setAutoSaveStatus('saving');
      void saveContent(fileId, nextText)
        .then(refresh)
        .then(() => loadLinks(fileId))
        .then(() => {
          setAutoSaveStatus('saved');
        })
        .catch(() => {
          setAutoSaveStatus('error');
        });
    }, 1000);

    return () => {
      window.clearTimeout(draftTimer);
      window.clearTimeout(saveTimer);
    };
  }, [
    canEditContent,
    draftText,
    isContentDirty,
    isContentLoading,
    isContentSaving,
    loadLinks,
    refresh,
    saveContent,
    saveDraft,
    selectedContent,
    selectedFile,
  ]);

  async function handleSaveContent() {
    if (!selectedFile || !selectedContent || !isContentDirty || isContentSaving) {
      return;
    }
    try {
      await saveContent(selectedFile.file_id, draftText);
      await refresh();
      await loadLinks(selectedFile.file_id);
      setAutoSaveStatus('saved');
    } catch {
      // 错误信息由 hook 写入页面状态。
      setAutoSaveStatus('error');
    }
  }

  async function handleCreateLinkedNote(linkText: string) {
    if (!selectedFile) {
      return;
    }
    try {
      const path = notePathFromWikiLink(linkText);
      const created = await createNote(path, `# ${fileName(path).replace(/\.(md|markdown)$/i, '')}\n`);
      await loadLinks(selectedFile.file_id);
      openWorkspaceFile(created.file_id);
      setEditorMode('edit');
      setFileMutationError(null);
    } catch (error) {
      setFileMutationError(error instanceof Error ? error.message : String(error));
    }
  }

  function handleCreateNote() {
    setFileDialog({ kind: 'create', path: 'Notes/Untitled.md' });
  }

  function handleRenameNote() {
    if (selectedFile) {
      setFileDialog({ kind: 'rename', path: fileName(selectedFile.path) });
    }
  }

  function handleDeleteNote() {
    if (selectedFile) {
      setFileDialog({ kind: 'delete', path: selectedFile.path });
    }
  }

  async function handleConfirmFileDialog() {
    if (!fileDialog) {
      return;
    }
    const path = fileDialog.path.trim();
    if (fileDialog.kind !== 'delete' && !path) {
      setFileMutationError('文档路径不能为空。');
      return;
    }
    try {
      if (fileDialog.kind === 'create') {
        const created = await createNote(path, `# ${fileName(path).replace(/\.(md|markdown)$/i, '')}\n`);
        openWorkspaceFile(created.file_id);
        setEditorMode('edit');
      } else if (fileDialog.kind === 'rename') {
        if (!selectedFile || path === selectedFile.path) {
          setFileDialog(null);
          return;
        }
        const renamed = await renameNote(selectedFile.file_id, path);
        openWorkspaceFile(renamed.file_id);
      } else if (selectedFile) {
        await deleteNote(selectedFile.file_id);
        clearContent();
        clearLinks();
        setSelectedFileId(null);
      }
      setFileMutationError(null);
      setFileDialog(null);
    } catch (error) {
      setFileMutationError(error instanceof Error ? error.message : String(error));
    }
  }

  function handleRecoverDraft() {
    if (!pendingDraftRecovery || pendingDraftRecovery.fileId !== selectedFileId) {
      return;
    }
    setDraftText(pendingDraftRecovery.text);
    setEditorMode('edit');
    setPendingDraftRecovery(null);
  }

  function handleDiscardDraft() {
    if (!pendingDraftRecovery) {
      return;
    }
    const fileId = pendingDraftRecovery.fileId;
    setPendingDraftRecovery(null);
    void clearDraft(fileId).catch(() => {
      setAutoSaveStatus('error');
    });
  }

  function toggleFolder(folderPath: string) {
    setCollapsedFolders((current) => {
      const next = new Set(current);
      if (next.has(folderPath)) {
        next.delete(folderPath);
      } else {
        next.add(folderPath);
      }
      return next;
    });
  }

  return (
    <div className="flex h-full bg-[#1a1a2e] overflow-hidden">
      <aside className="hidden md:flex w-72 border-r border-[#0f3460] bg-[#16213e]/80 flex-shrink-0 flex-col max-h-full overflow-hidden">
        <div className="p-4 border-b border-[#0f3460] flex items-center justify-between bg-[#16213e]">
          <div className="min-w-0">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">工作区</span>
            <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-slate-500">
              <span>{sourceLabel(source)}</span>
              <span>{summary.activeCount} 个正常</span>
              {summary.missingCount > 0 && <span className="text-[#e94560]">{summary.missingCount} 个缺失</span>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={refresh}
              disabled={isRefreshing}
              title="刷新工作区"
              className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#121316] border border-[#0f3460] text-slate-400 hover:text-white disabled:opacity-50 transition-colors"
            >
              <RefreshCw size={15} className={isRefreshing ? 'animate-spin' : ''} />
            </button>
            <button
              onClick={handleCreateNote}
              title="新建文档"
              className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#121316] border border-[#0f3460] text-slate-400 hover:text-white transition-colors"
            >
              <Plus size={15} />
            </button>
          </div>
        </div>
        {selectedContextFiles.length > 0 && (
          <div className="border-b border-[#0f3460] bg-[#0f3460]/20 px-3 py-2">
            <button
              onClick={openAiContextForSelectedFiles}
              className="flex w-full items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 py-2 text-[12px] font-semibold text-[#a9c8fc] transition-colors hover:text-white"
            >
              <Bot size={14} />
              已选 {selectedContextFiles.length} 个文档，打开 AI 文档
            </button>
          </div>
        )}
        <div className="border-b border-[#0f3460] bg-[#121316]/50 p-3">
          <label className="flex items-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 py-2 text-slate-400 focus-within:border-[#a9c8fc]">
            <Search size={14} />
            <input
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              className="min-w-0 flex-1 bg-transparent text-[12px] text-[#e3e2e6] outline-none placeholder:text-slate-600"
              placeholder="Search local notes"
            />
          </label>
          {searchQuery.trim() && (
            <div className="mt-2 rounded border border-[#0f3460] bg-[#16213e]">
              <div className="border-b border-[#0f3460] px-3 py-2 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                {isSearching ? 'Searching' : `${searchResultCount} results`}
              </div>
              {searchError && (
                <div className="px-3 py-2 text-[11px] text-[#ffb782]">{searchError}</div>
              )}
              {!searchError && searchResults.length === 0 && !isSearching && (
                <div className="px-3 py-2 text-[11px] text-slate-500">No local matches.</div>
              )}
              {searchResults.map((result) => (
                <button
                  key={result.file_id}
                  onClick={() => openWorkspaceFile(result.file_id)}
                  className="block w-full border-b border-[#0f3460]/60 px-3 py-2 text-left last:border-b-0 hover:bg-[#1f2b4a]"
                  title={result.path}
                >
                  <div className="truncate text-[12px] font-semibold text-[#e3e2e6]">{result.title}</div>
                  <div className="mt-1 truncate font-mono text-[10px] text-slate-500">{result.path}</div>
                  <div className="mt-1 line-clamp-2 text-[11px] leading-4 text-slate-400">{result.snippet}</div>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {explorerRows.length === 0 ? (
            <div className="p-4 text-[13px] text-slate-400">暂无已跟踪文件。</div>
          ) : (
            visibleExplorerRows.map((row) => (
              row.kind === 'folder' ? (
                <div
                  key={row.id}
                  className="flex w-full items-center gap-2 py-1.5 pr-2 text-left text-slate-300 transition-colors hover:bg-[#1f2b4a] hover:text-slate-100"
                  style={{ paddingLeft: `${16 + row.depth * 14}px` }}
                >
                  <button
                    type="button"
                    onClick={() => toggleFolder(row.path)}
                    className="flex min-w-0 flex-1 items-center gap-2 py-0.5 text-left"
                    title={`${collapsedFolders.has(row.path) ? '展开目录' : '折叠目录'}：${row.path || summary.vaultRoot || rootName}`}
                  >
                    {collapsedFolders.has(row.path) ? (
                      <ChevronRight size={14} className="text-slate-500" />
                    ) : (
                      <ChevronDown size={14} className="text-slate-500" />
                    )}
                    <Folder size={16} className="text-[#a9c8fc]" />
                    <span className="text-[13px] font-semibold flex-1 font-sans truncate">{row.name}</span>
                    <span className="font-mono text-[10px] text-slate-500" title={`${row.count} 篇文档`}>{row.count}</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => openAiContextForFolder(row.path)}
                    className="rounded border border-[#0f3460] bg-[#121316] px-2 py-1 text-[10px] font-semibold text-[#a9c8fc] hover:text-white"
                    title="将此文件夹下的 Markdown 加入 AI 上下文"
                  >
                    AI
                  </button>
                </div>
              ) : (
                <div
                  key={row.id}
                  style={{ paddingLeft: `${24 + row.depth * 14}px` }}
                  className={`flex w-full items-center gap-2 py-2 pr-4 text-[13px] font-sans truncate text-left transition-colors ${
                    selectedFileId === row.file.file_id
                      ? 'bg-[#1f2b4a] text-[#e3e2e6] border-r-2 border-[#e94560]'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-[#1f2b4a]'
                  }`}
                >
                  {isAiContextEligibleFile(row.file) && (
                    <button
                      type="button"
                      onClick={() => toggleContextFile(row.file.file_id)}
                      className={`h-4 w-4 flex-shrink-0 rounded border text-[10px] leading-3 ${
                        selectedContextFileIds.has(row.file.file_id)
                          ? 'border-[#a9c8fc] bg-[#0f3460] text-white'
                          : 'border-[#0f3460] bg-[#121316] text-transparent'
                      }`}
                      title="选择此文档加入 AI 上下文"
                    >
                      ✓
                    </button>
                  )}
                  <button
                    onClick={() => openWorkspaceFile(row.file.file_id)}
                    title={row.file.path}
                    className="flex min-w-0 flex-1 items-center gap-2 text-left"
                  >
                    <MarkdownFileIcon size={14} tone={row.file.exists_on_disk ? 'normal' : 'danger'} />
                    <span className="truncate">{fileName(row.file.path)}</span>
                  </button>
                </div>
              )
            ))
          )}
        </div>
      </aside>

      <div className="flex-1 flex flex-col h-full relative overflow-hidden bg-[#121316]">
        <header className="bg-[#16213e] border-b border-[#0f3460] h-14 flex items-center justify-between px-4 flex-shrink-0 z-10 shadow-sm">
          <div className="flex items-center gap-4 min-w-0">
            <div className="flex items-center gap-2 text-slate-300 min-w-0">
              <MarkdownFileIcon size={18} />
              <span className="text-[13px] font-semibold truncate">
                {selectedFile ? fileName(selectedFile.path) : '工作区'}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center bg-[#0f3460]/30 rounded-md p-0.5 border border-[#0f3460]">
              <span className="px-3 py-1.5 rounded text-slate-500 flex items-center gap-1.5 font-mono text-[12px] font-bold uppercase tracking-wider">
                AI Wiki 暂不开放
              </span>
            </div>
          </div>
        </header>

        <div className="flex-1 overflow-hidden bg-[#121316] p-4 md:p-6">
          {lastError && (
            <div className="mb-4 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782] line-clamp-3">
              {lastError}
            </div>
          )}
          {contentError && (
            <div className="mb-4 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782] line-clamp-3">
              {contentError}
            </div>
          )}
          {fileMutationError && (
            <div className="mb-4 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782] line-clamp-3">
              {fileMutationError}
            </div>
          )}
          {linksError && (
            <div className="mb-4 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782] line-clamp-3">
              {linksError}
            </div>
          )}

          {selectedFile ? (
            <div className="relative flex h-full min-h-0 w-full gap-4">
              <section className="hidden border border-[#0f3460] rounded-xl bg-[#16213e] p-5 shadow-lg shadow-black/20">
                <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-3">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded border font-mono text-[11px] uppercase tracking-wider font-bold ${statusClasses(selectedFile)}`}>
                        {statusLabel(selectedFile)}
                      </span>
                      <span className="font-mono text-[11px] text-slate-500">{fileTypeLabel(selectedFile.type)}</span>
                    </div>
                    <h2 className="text-2xl font-bold text-[#e3e2e6] truncate">{fileName(selectedFile.path)}</h2>
                    <p className="font-mono text-[12px] text-slate-500 mt-2 break-words">{selectedFile.path}</p>
                    {isContentDirty && autoSaveStatus !== 'idle' && (
                      <span className="hidden sm:inline font-mono text-[11px] text-slate-400">
                        {autoSaveStatus === 'draft' && (isDraftSaving ? 'æ­£åœ¨å†™å…¥è‰ç¨¿' : 'è‰ç¨¿å·²å†™å…¥')}
                        {autoSaveStatus === 'saving' && 'æ­£åœ¨è‡ªåŠ¨ä¿å­˜'}
                        {autoSaveStatus === 'saved' && 'å·²è‡ªåŠ¨ä¿å­˜'}
                        {autoSaveStatus === 'error' && 'è‡ªåŠ¨ä¿å­˜å¤±è´¥'}
                      </span>
                    )}
                  </div>
                  {!selectedFile.exists_on_disk && (
                    <div className="flex items-center gap-2 text-[#e94560] text-[12px]">
                      <AlertTriangle size={16} />
                      <span>磁盘文件缺失</span>
                    </div>
                  )}
                </div>
              </section>

              <section className="hidden grid-cols-1 md:grid-cols-2 gap-4">
                <div className="border border-[#0f3460] rounded-xl bg-[#16213e] p-5">
                  <h3 className="text-[15px] font-bold text-[#e3e2e6] mb-4">文件元数据</h3>
                  <dl className="grid grid-cols-1 gap-3 text-[13px]">
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">文件 ID</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{selectedFile.file_id}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">更新时间</dt>
                      <dd className="font-mono text-[#e3e2e6] mt-1">{formatFileTime(selectedFile.updated_at)}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">大小</dt>
                      <dd className="font-mono text-[#e3e2e6] mt-1">{formatBytes(selectedFile.size_bytes)}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">版本</dt>
                      <dd className="font-mono text-[#e3e2e6] mt-1">{selectedFile.last_known_revision ?? '本地'}</dd>
                    </div>
                  </dl>
                </div>

                <div className="border border-[#0f3460] rounded-xl bg-[#16213e] p-5">
                  <h3 className="text-[15px] font-bold text-[#e3e2e6] mb-4">工作区</h3>
                  <dl className="grid grid-cols-1 gap-3 text-[13px]">
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">知识库</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{summary.vaultId}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">设备</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{summary.deviceId}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">根目录</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{summary.vaultRoot}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">内容哈希</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{selectedFile.content_hash ?? '无'}</dd>
                    </div>
                  </dl>
                </div>
              </section>

              <section className="flex min-h-0 flex-1 flex-col border border-[#0f3460] rounded-xl bg-[#16213e] shadow-lg shadow-black/20 overflow-hidden">
                <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[#0f3460] px-5 py-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <Code2 size={16} className="flex-shrink-0 text-[#a9c8fc]" />
                    <h3 className="text-[15px] font-bold text-[#e3e2e6]">Markdown 编辑器</h3>
                    {isContentDirty && autoSaveStatus !== 'idle' && (
                      <span className="hidden sm:inline font-mono text-[11px] text-slate-400">
                        {autoSaveStatus === 'draft' && (isDraftSaving ? 'Writing draft' : 'Draft written')}
                        {autoSaveStatus === 'saving' && 'Autosaving'}
                        {autoSaveStatus === 'saved' && 'Autosaved'}
                        {autoSaveStatus === 'error' && 'Autosave failed'}
                      </span>
                    )}
                    {savedAtMs && !isContentDirty && (
                      <span className="hidden sm:inline font-mono text-[11px] text-emerald-300">
                        已保存到本地 {formatFileTime(savedAtMs)}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {selectedFileIsEditable && (
                    <div className="flex items-center rounded border border-[#0f3460] bg-[#121316] p-0.5">
                      <button
                        onClick={() => setEditorMode('edit')}
                        title="编辑 Markdown"
                        className={`inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-[12px] font-semibold transition-colors ${
                          editorMode === 'edit' ? 'bg-[#0f3460] text-white' : 'text-slate-400 hover:text-white'
                        }`}
                      >
                        <Edit3 size={13} />
                        <span>编辑</span>
                      </button>
                      <button
                        onClick={() => setEditorMode('preview')}
                        title="预览 Markdown"
                        className={`inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-[12px] font-semibold transition-colors ${
                          editorMode === 'preview' ? 'bg-[#0f3460] text-white' : 'text-slate-400 hover:text-white'
                        }`}
                      >
                        <Eye size={13} />
                        <span>预览</span>
                      </button>
                      <button
                        onClick={() => setEditorMode('split')}
                        title="分栏编辑和预览"
                        className={`inline-flex h-7 items-center gap-1.5 rounded px-2.5 text-[12px] font-semibold transition-colors ${
                          editorMode === 'split' ? 'bg-[#0f3460] text-white' : 'text-slate-400 hover:text-white'
                        }`}
                      >
                        <Columns2 size={13} />
                        <span>分栏</span>
                      </button>
                    </div>
                    )}
                    <button
                      onClick={() => setIsInfoPanelOpen((value) => !value)}
                      title={isInfoPanelOpen ? '收起详情' : '显示详情'}
                      className="inline-flex h-8 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 text-[12px] font-semibold text-slate-300 hover:text-white transition-colors"
                    >
                      {isInfoPanelOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
                      <span>详情</span>
                    </button>
                    <button
                      onClick={handleRenameNote}
                      disabled={!selectedFile || selectedFile.type !== 'note' || selectedFile.status !== 'active'}
                      title="重命名文档"
                      className="inline-flex h-8 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 text-[12px] font-semibold text-slate-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
                    >
                      <Edit3 size={14} />
                      <span>重命名</span>
                    </button>
                    <button
                      onClick={handleDeleteNote}
                      disabled={!selectedFile || selectedFile.type !== 'note' || selectedFile.status !== 'active'}
                      title="删除文档"
                      className="inline-flex h-8 items-center justify-center gap-2 rounded border border-[#e94560]/40 bg-[#e94560]/10 px-3 text-[12px] font-semibold text-[#ffb3c0] hover:text-white disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
                    >
                      <Trash2 size={14} />
                      <span>删除</span>
                    </button>
                    {savedAtMs && !isContentDirty && (
                      <button
                        onClick={() => setView('sync')}
                        title="打开同步状态"
                        className="inline-flex h-8 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-3 text-[12px] font-semibold text-[#a9c8fc] hover:text-white transition-colors"
                      >
                        <Cloud size={14} />
                        <span>打开同步</span>
                      </button>
                    )}
                    <button
                      onClick={handleSaveContent}
                      disabled={!canEditContent || !isContentDirty || isContentLoading || isContentSaving}
                      title="保存文件内容"
                      className="inline-flex h-8 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 text-[12px] font-semibold text-slate-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-50 transition-colors"
                    >
                      <Save size={14} />
                      <span>{isContentSaving ? '保存中' : '保存'}</span>
                    </button>
                  </div>
                </div>
                {pendingDraftRecovery && pendingDraftRecovery.fileId === selectedFile.file_id && selectedFileIsEditable && (
                  <div className="border-b border-[#ffb782]/30 bg-[#ffb782]/10 px-5 py-3 text-[12px] text-[#ffd8a8]">
                    <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                      <span>
                        Unsaved draft found
                        {pendingDraftRecovery.updatedAt ? ` (${formatFileTime(pendingDraftRecovery.updatedAt)})` : ''}
                        . Restore it into the editor?
                      </span>
                      <div className="flex gap-2">
                        <button
                          onClick={handleRecoverDraft}
                          className="rounded border border-[#ffb782]/40 bg-[#ffb782]/20 px-3 py-1 font-semibold text-[#fff3df] hover:bg-[#ffb782]/30"
                        >
                          Restore
                        </button>
                        <button
                          onClick={handleDiscardDraft}
                          className="rounded border border-[#0f3460] bg-[#121316] px-3 py-1 font-semibold text-slate-300 hover:text-white"
                        >
                          Discard
                        </button>
                      </div>
                    </div>
                  </div>
                )}
                <div className="min-h-0 flex-1 overflow-hidden bg-[#121316]">
                  {selectedFileIsEditable && editorMode === 'edit' && (
                    <textarea
                      value={isContentLoading ? '正在加载文件内容...' : draftText}
                      onChange={(event) => setDraftText(event.target.value)}
                      disabled={!canEditContent || isContentLoading || isContentSaving}
                      spellCheck={false}
                      className="block h-full min-h-0 w-full resize-none overflow-auto bg-[#121316] p-5 font-mono text-[13px] leading-relaxed text-slate-300 outline-none placeholder:text-slate-600 disabled:cursor-not-allowed disabled:text-slate-500"
                      placeholder="此文件内容不可用。"
                    />
                  )}

                  {selectedFileIsEditable && editorMode === 'preview' && (
                    <div className="h-full overflow-auto">
                      <MarkdownPreview
                        markdown={isContentLoading ? '正在加载文件内容...' : draftText}
                        wikiLinkByText={wikiLinkByText}
                        onOpenWikiLink={openWorkspaceFile}
                      />
                    </div>
                  )}

                  {selectedFileIsEditable && editorMode === 'split' && (
                    <div className="grid h-full min-h-0 grid-cols-1 md:grid-cols-2">
                      <textarea
                        value={isContentLoading ? '正在加载文件内容...' : draftText}
                        onChange={(event) => setDraftText(event.target.value)}
                        disabled={!canEditContent || isContentLoading || isContentSaving}
                        spellCheck={false}
                        className="block h-full min-h-0 w-full resize-none overflow-auto border-b border-[#0f3460] bg-[#121316] p-5 font-mono text-[13px] leading-relaxed text-slate-300 outline-none placeholder:text-slate-600 disabled:cursor-not-allowed disabled:text-slate-500 md:border-b-0 md:border-r"
                        placeholder="此文件内容不可用。"
                      />
                      <div className="h-full min-h-0 overflow-auto bg-[#101827]">
                        <MarkdownPreview
                          markdown={isContentLoading ? '正在加载文件内容...' : draftText}
                          wikiLinkByText={wikiLinkByText}
                          onOpenWikiLink={openWorkspaceFile}
                        />
                      </div>
                    </div>
                  )}
                </div>
              </section>

              {isInfoPanelOpen && (
                <aside className="absolute bottom-0 right-0 top-0 z-20 h-full min-h-0 w-80 flex-shrink-0 overflow-y-auto rounded-xl border border-[#0f3460] bg-[#16213e] p-4 shadow-lg shadow-black/30 xl:static xl:shadow-black/20">
                  <div className="mb-4 flex items-center justify-between gap-3">
                    <div className="flex min-w-0 items-center gap-2">
                      <Info size={16} className="text-[#a9c8fc]" />
                      <h3 className="truncate text-[15px] font-bold text-[#e3e2e6]">文件详情</h3>
                    </div>
                    <button
                      onClick={() => setIsInfoPanelOpen(false)}
                      title="收起详情"
                      className="inline-flex h-7 w-7 items-center justify-center rounded border border-[#0f3460] bg-[#121316] text-slate-400 hover:text-white"
                    >
                      <PanelRightClose size={14} />
                    </button>
                  </div>

                  <div className="mb-4 min-w-0 rounded border border-[#0f3460] bg-[#121316] p-3">
                    <div className="mb-2 flex flex-wrap items-center gap-2">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded border font-mono text-[10px] uppercase tracking-wider font-bold ${statusClasses(selectedFile)}`}>
                        {statusLabel(selectedFile)}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500">{fileTypeLabel(selectedFile.type)}</span>
                    </div>
                    <p className="truncate text-[14px] font-bold text-[#e3e2e6]" title={fileName(selectedFile.path)}>
                      {fileName(selectedFile.path)}
                    </p>
                    <p className="mt-2 break-words font-mono text-[11px] text-slate-500">{selectedFile.path}</p>
                    {!selectedFile.exists_on_disk && (
                      <div className="mt-3 flex items-center gap-2 text-[12px] text-[#e94560]">
                        <AlertTriangle size={15} />
                        <span>磁盘文件缺失</span>
                      </div>
                    )}
                  </div>

                  <div className="mb-4">
                    <h4 className="mb-2 text-[12px] font-bold text-slate-300">文件元数据</h4>
                    <dl className="grid grid-cols-1 gap-3 text-[12px]">
                      <div>
                        <dt className="text-[10px] uppercase tracking-wider text-slate-500">文件 ID</dt>
                        <dd className="mt-1 break-words font-mono text-[#e3e2e6]">{selectedFile.file_id}</dd>
                      </div>
                      <div>
                        <dt className="text-[10px] uppercase tracking-wider text-slate-500">更新时间</dt>
                        <dd className="mt-1 font-mono text-[#e3e2e6]">{formatFileTime(selectedFile.updated_at)}</dd>
                      </div>
                      <div>
                        <dt className="text-[10px] uppercase tracking-wider text-slate-500">大小</dt>
                        <dd className="mt-1 font-mono text-[#e3e2e6]">{formatBytes(selectedFile.size_bytes)}</dd>
                      </div>
                      <div>
                        <dt className="text-[10px] uppercase tracking-wider text-slate-500">版本</dt>
                        <dd className="mt-1 font-mono text-[#e3e2e6]">{selectedFile.last_known_revision ?? '本地'}</dd>
                      </div>
                      <div>
                        <dt className="text-[10px] uppercase tracking-wider text-slate-500">内容哈希</dt>
                        <dd className="mt-1 break-words font-mono text-[#e3e2e6]">{selectedFile.content_hash ?? '无'}</dd>
                      </div>
                    </dl>
                  </div>

                  <div className="mb-4">
                    <div className="mb-2 flex items-center justify-between gap-2">
                      <h4 className="text-[12px] font-bold text-slate-300">双链与反链</h4>
                      {isLinksLoading && <span className="font-mono text-[10px] text-slate-500">Loading</span>}
                    </div>
                    <div className="space-y-3 text-[12px]">
                      <div>
                        <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                          Outgoing {selectedLinks?.outgoing_count ?? 0}
                        </div>
                        {(selectedLinks?.outgoing ?? []).length === 0 ? (
                          <p className="text-slate-500">暂无出链。</p>
                        ) : (
                          <div className="space-y-1">
                            {selectedLinks?.outgoing.map((link) => (
                              <div
                                key={`${link.ordinal}-${link.link_text}`}
                                className={`rounded border px-2 py-1.5 ${
                                  link.target_file_id
                                    ? 'border-[#0f3460] bg-[#121316] text-[#a9c8fc] hover:text-white'
                                    : 'border-[#ffb782]/30 bg-[#ffb782]/10 text-[#ffb782]'
                                }`}
                                title={link.target_path ?? 'Unresolved link'}
                              >
                                <button
                                  type="button"
                                  disabled={!link.target_file_id}
                                  onClick={() => {
                                    if (link.target_file_id) {
                                      openWorkspaceFile(link.target_file_id);
                                    }
                                  }}
                                  className="block w-full text-left disabled:cursor-default"
                                >
                                  <span className="block truncate font-semibold">[[{link.link_text}]]</span>
                                  <span className="block truncate font-mono text-[10px] text-slate-500">
                                    {link.target_path ?? 'unresolved'}
                                  </span>
                                </button>
                                {!link.target_file_id && (
                                  <button
                                    type="button"
                                    onClick={() => void handleCreateLinkedNote(link.link_text)}
                                    className="mt-2 rounded border border-[#ffb782]/40 bg-[#ffb782]/10 px-2 py-1 text-[11px] font-semibold text-[#ffd8a8] hover:bg-[#ffb782]/20 hover:text-white"
                                  >
                                    Create linked note
                                  </button>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      <div>
                        <div className="mb-1 font-mono text-[10px] uppercase tracking-wider text-slate-500">
                          Backlinks {selectedLinks?.backlink_count ?? 0}
                        </div>
                        {(selectedLinks?.backlinks ?? []).length === 0 ? (
                          <p className="text-slate-500">暂无反链。</p>
                        ) : (
                          <div className="space-y-1">
                            {selectedLinks?.backlinks.map((link) => (
                              <button
                                key={`${link.source_file_id}-${link.ordinal}`}
                                type="button"
                                onClick={() => openWorkspaceFile(link.source_file_id)}
                                className="block w-full rounded border border-[#0f3460] bg-[#121316] px-2 py-1.5 text-left text-slate-300 hover:text-white"
                                title={link.source_path}
                              >
                                <span className="block truncate font-semibold">{fileName(link.source_path)}</span>
                                <span className="block truncate font-mono text-[10px] text-slate-500">
                                  {link.source_path}
                                </span>
                              </button>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>
                  </div>

                  <div>
                    <h4 className="mb-2 text-[12px] font-bold text-slate-300">工作区</h4>
                    <dl className="grid grid-cols-1 gap-3 text-[12px]">
                      <div>
                        <dt className="text-[10px] uppercase tracking-wider text-slate-500">知识库</dt>
                        <dd className="mt-1 break-words font-mono text-[#e3e2e6]">{summary.vaultId}</dd>
                      </div>
                      <div>
                        <dt className="text-[10px] uppercase tracking-wider text-slate-500">设备</dt>
                        <dd className="mt-1 break-words font-mono text-[#e3e2e6]">{summary.deviceId}</dd>
                      </div>
                      <div>
                        <dt className="text-[10px] uppercase tracking-wider text-slate-500">根目录</dt>
                        <dd className="mt-1 break-words font-mono text-[#e3e2e6]">{summary.vaultRoot}</dd>
                      </div>
                    </dl>
                  </div>
                </aside>
              )}

              <div className="hidden w-fit items-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-3 py-2 text-[13px] text-slate-500">
                知识图谱暂不进入当前收口版本
              </div>
            </div>
          ) : (
            <div className="max-w-2xl rounded-xl border border-[#0f3460] bg-[#16213e] p-6 text-[13px] text-slate-400">
              当前未选择已跟踪文件。
            </div>
          )}
        </div>
      </div>
      {fileDialog && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void handleConfirmFileDialog();
            }}
            className="w-full max-w-md rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 shadow-2xl shadow-black/40"
          >
            <div className="mb-4">
              <h3 className="text-[16px] font-bold text-[#e3e2e6]">
                {fileDialog.kind === 'create' && '新建文档'}
                {fileDialog.kind === 'rename' && '重命名文档'}
                {fileDialog.kind === 'delete' && '移入回收站'}
              </h3>
              <p className="mt-2 text-[12px] leading-relaxed text-slate-400">
                {fileDialog.kind === 'create' && '输入相对工作区路径；未带扩展名时会自动使用 .md。'}
                {fileDialog.kind === 'rename' && '只修改当前目录下的文件名，不会移动到其他文件夹。'}
                {fileDialog.kind === 'delete' && `将移出工作区并保留到 .noteapp/trash：${fileName(fileDialog.path)}`}
              </p>
            </div>
            {fileDialog.kind !== 'delete' && (
              <label className="mb-4 block">
                <span className="mb-2 block text-[12px] font-semibold text-slate-300">
                  {fileDialog.kind === 'rename' ? '文件名' : '文档路径'}
                </span>
                <input
                  autoFocus
                  value={fileDialog.path}
                  onChange={(event) => setFileDialog({ ...fileDialog, path: event.target.value })}
                  className="w-full rounded border border-[#0f3460] bg-[#121316] px-3 py-2 font-mono text-[13px] text-[#e3e2e6] outline-none focus:border-[#a9c8fc]"
                  placeholder={fileDialog.kind === 'rename' ? 'Untitled.md' : 'Notes/Untitled.md'}
                />
              </label>
            )}
            {fileMutationError && (
              <div className="mb-4 rounded border border-[#ffb782]/30 bg-[#ffb782]/10 px-3 py-2 text-[12px] text-[#ffb782]">
                {fileMutationError}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setFileDialog(null)}
                className="rounded border border-[#0f3460] bg-[#121316] px-4 py-2 text-[12px] font-semibold text-slate-300 hover:text-white"
              >
                取消
              </button>
              <button
                type="submit"
                className={`rounded px-4 py-2 text-[12px] font-semibold text-white ${
                  fileDialog.kind === 'delete'
                    ? 'bg-[#e94560] hover:bg-[#ff5d76]'
                    : 'bg-[#0f3460] hover:bg-[#15508f]'
                }`}
              >
                {fileDialog.kind === 'delete' ? '移入回收站' : '确认'}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

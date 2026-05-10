import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Columns2,
  Cloud,
  Code2,
  Edit3,
  Eye,
  FileText,
  Folder,
  Info,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  RefreshCw,
  Save,
} from 'lucide-react';

import { useWorkspaceFilesController } from '../useWorkspaceFiles';
import { useWorkspaceFileContentController } from '../useWorkspaceFileContent';
import type { WorkspaceFileEntry } from '../workspaceFiles';

function fileName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

function folderName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts.slice(0, -1).join('/') : '知识库根目录';
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

function renderInlineMarkdown(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const pattern = /(`[^`]+`|\*\*[^*\n]+?\*\*|\*[^*\n]+?\*|\[[^\]\n]+\]\(https?:\/\/[^)\s]+\))/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }

    const token = match[0];
    const key = `${match.index}-${token}`;
    const linkMatch = /^\[([^\]\n]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(token);

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

function renderMarkdownBlocks(markdown: string): React.ReactNode[] {
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
          renderInlineMarkdown(headingMatch[2]),
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
              {renderInlineMarkdown(quoteLine)}
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
            <li key={itemIndex}>{renderInlineMarkdown(item)}</li>
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
            <li key={itemIndex}>{renderInlineMarkdown(item)}</li>
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
        {renderInlineMarkdown(paragraphLines.join(' '))}
      </p>,
    );
  }

  return nodes;
}

function MarkdownPreview({ markdown }: { markdown: string }) {
  const blocks = useMemo(() => renderMarkdownBlocks(markdown), [markdown]);

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

function buildExplorerRows(files: WorkspaceFileEntry[]): ExplorerRow[] {
  const folderCounts = new Map<string, number>();
  for (const file of files) {
    const parts = file.path.split(/[\\/]/).filter(Boolean);
    const folderParts = parts.slice(0, -1);
    if (folderParts.length === 0) {
      folderCounts.set('', (folderCounts.get('') ?? 0) + 1);
      continue;
    }
    for (let index = 0; index < folderParts.length; index += 1) {
      const folderPath = folderParts.slice(0, index + 1).join('/');
      folderCounts.set(folderPath, (folderCounts.get(folderPath) ?? 0) + 1);
    }
  }

  const rows: ExplorerRow[] = [];
  const sortedFolders = Array.from(folderCounts.keys()).sort((left, right) => {
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
      .filter((file) => folderName(file.path) === (folderPath || '知识库根目录'))
      .sort((left, right) => left.path.localeCompare(right.path));
    rows.push({
      kind: 'folder',
      id: `folder:${folderPath || '__root__'}`,
      name: folderPath ? fileName(folderPath) : '知识库根目录',
      path: folderPath,
      depth,
      count: folderCounts.get(folderPath) ?? 0,
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

export default function ExplorerView({ setView }: { setView: (v: string) => void }) {
  const {
    summary,
    files,
    source,
    lastError,
    isRefreshing,
    refresh,
  } = useWorkspaceFilesController();
  const {
    content: selectedContent,
    lastError: contentError,
    isLoading: isContentLoading,
    isSaving: isContentSaving,
    savedAtMs,
    loadContent,
    saveContent,
    clearContent,
  } = useWorkspaceFileContentController();
  const visibleFiles = useMemo(
    () => files.filter((file) => file.status !== 'deleted'),
    [files],
  );
  const explorerRows = useMemo(() => buildExplorerRows(visibleFiles), [visibleFiles]);
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

  const selectedFile = visibleFiles.find((file) => file.file_id === selectedFileId) ?? null;
  const [isInfoPanelOpen, setIsInfoPanelOpen] = useState(false);

  useEffect(() => {
    if (!selectedFile || !selectedFile.exists_on_disk || selectedFile.status !== 'active') {
      clearContent();
      return;
    }
    void loadContent(selectedFile.file_id);
  }, [clearContent, loadContent, selectedFile]);

  const [draftText, setDraftText] = useState('');
  const [editorMode, setEditorMode] = useState<MarkdownEditorMode>('edit');
  const isContentDirty = Boolean(selectedContent && draftText !== selectedContent.text);
  const canEditContent = Boolean(
    selectedFile && selectedFile.exists_on_disk && selectedFile.status === 'active' && selectedContent,
  );

  useEffect(() => {
    setDraftText(selectedContent?.text ?? '');
  }, [selectedContent]);

  async function handleSaveContent() {
    if (!selectedFile || !selectedContent || !isContentDirty || isContentSaving) {
      return;
    }
    try {
      await saveContent(selectedFile.file_id, draftText);
      await refresh();
    } catch {
      // 错误信息由 hook 写入页面状态。
    }
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
            <button className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#121316] border border-[#0f3460] text-slate-400 hover:text-white transition-colors">
              <Plus size={15} />
            </button>
          </div>
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {explorerRows.length === 0 ? (
            <div className="p-4 text-[13px] text-slate-400">暂无已跟踪文件。</div>
          ) : (
            explorerRows.map((row) => (
              row.kind === 'folder' ? (
                <div
                  key={row.id}
                  className="flex items-center gap-2 py-2 pr-4 text-slate-300"
                  style={{ paddingLeft: `${16 + row.depth * 14}px` }}
                  title={row.path || '知识库根目录'}
                >
                  <Folder size={16} className="text-[#a9c8fc]" />
                  <span className="text-[13px] font-semibold flex-1 font-sans truncate">{row.name}</span>
                  <span className="font-mono text-[10px] text-slate-500">{row.count}</span>
                </div>
              ) : (
                <button
                  key={row.id}
                  onClick={() => setSelectedFileId(row.file.file_id)}
                  title={row.file.path}
                  style={{ paddingLeft: `${24 + row.depth * 14}px` }}
                  className={`flex w-full items-center gap-2 py-2 pr-4 text-[13px] font-sans truncate text-left transition-colors ${
                    selectedFileId === row.file.file_id
                      ? 'bg-[#1f2b4a] text-[#e3e2e6] border-r-2 border-[#e94560]'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-[#1f2b4a]'
                  }`}
                >
                  <FileText size={14} className={row.file.exists_on_disk ? 'text-slate-500' : 'text-[#e94560]'} />
                  <span className="truncate">{fileName(row.file.path)}</span>
                </button>
              )
            ))
          )}
        </div>
      </aside>

      <div className="flex-1 flex flex-col h-full relative overflow-hidden bg-[#121316]">
        <header className="bg-[#16213e] border-b border-[#0f3460] h-14 flex items-center justify-between px-4 flex-shrink-0 z-10 shadow-sm">
          <div className="flex items-center gap-4 min-w-0">
            <div className="flex items-center gap-2 text-slate-300 min-w-0">
              <FileText size={18} className="text-[#e94560] flex-shrink-0" />
              <span className="text-[13px] font-semibold truncate">
                {selectedFile ? fileName(selectedFile.path) : '工作区'}
              </span>
            </div>
            <div className="h-4 w-px bg-[#0f3460] hidden sm:block" />
            <div className="hidden sm:flex items-center gap-2 text-slate-500 font-mono text-[11px] min-w-0">
              <span>{summary.totalCount} 个已跟踪</span>
              {selectedFile && <span className="truncate">{formatBytes(selectedFile.size_bytes)}</span>}
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
                    <span className="font-mono text-[11px] text-slate-500">
                      {isContentLoading ? '加载中' : selectedContent ? selectedContent.encoding : '不可用'}
                    </span>
                    {savedAtMs && !isContentDirty && (
                      <span className="hidden sm:inline font-mono text-[11px] text-emerald-300">
                        已保存到本地 {formatFileTime(savedAtMs)}
                      </span>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
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
                    <button
                      onClick={() => setIsInfoPanelOpen((value) => !value)}
                      title={isInfoPanelOpen ? '收起详情' : '显示详情'}
                      className="inline-flex h-8 items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 text-[12px] font-semibold text-slate-300 hover:text-white transition-colors"
                    >
                      {isInfoPanelOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
                      <span>详情</span>
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
                <div className="min-h-0 flex-1 overflow-hidden bg-[#121316]">
                  {editorMode === 'edit' && (
                    <textarea
                      value={isContentLoading ? '正在加载文件内容...' : draftText}
                      onChange={(event) => setDraftText(event.target.value)}
                      disabled={!canEditContent || isContentLoading || isContentSaving}
                      spellCheck={false}
                      className="block h-full min-h-0 w-full resize-none overflow-auto bg-[#121316] p-5 font-mono text-[13px] leading-relaxed text-slate-300 outline-none placeholder:text-slate-600 disabled:cursor-not-allowed disabled:text-slate-500"
                      placeholder="此文件内容不可用。"
                    />
                  )}

                  {editorMode === 'preview' && (
                    <div className="h-full overflow-auto">
                      <MarkdownPreview markdown={isContentLoading ? '正在加载文件内容...' : draftText} />
                    </div>
                  )}

                  {editorMode === 'split' && (
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
                        <MarkdownPreview markdown={isContentLoading ? '正在加载文件内容...' : draftText} />
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
    </div>
  );
}

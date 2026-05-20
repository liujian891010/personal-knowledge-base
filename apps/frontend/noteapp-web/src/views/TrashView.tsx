import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertCircle,
  FileArchive,
  FileAudio,
  FileCode,
  FileImage,
  FileJson,
  FileQuestionMark,
  FileSpreadsheet,
  FileText,
  FileType,
  FileVideoCamera,
  Presentation,
  RefreshCw,
  Search,
  Trash2,
} from 'lucide-react';

import { syncBridgeUrl } from '../syncBridgeConfig';
import { invalidateWorkspaceFilesCache } from '../useWorkspaceFiles';

interface TrashItem {
  file_id: string;
  path: string;
  type: string;
  deleted_at: number;
  trash_path: string;
  exists_in_trash: boolean;
  size_bytes: number | null;
  mime_type?: string | null;
}

interface TrashSnapshot {
  items: TrashItem[];
  total_count: number;
}

type PendingTrashAction =
  | { kind: 'empty' }
  | { kind: 'purge'; item: TrashItem };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function requireString(payload: Record<string, unknown>, key: string): string {
  const value = payload[key];
  if (typeof value !== 'string') {
    throw new Error(`trash item missing string field: ${key}`);
  }
  return value;
}

function requireNumber(payload: Record<string, unknown>, key: string): number {
  const value = payload[key];
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    throw new Error(`trash item missing numeric field: ${key}`);
  }
  return value;
}

function optionalNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function optionalString(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key];
  return typeof value === 'string' ? value : null;
}

function parseTrashSnapshot(payload: unknown): TrashSnapshot {
  if (!isObject(payload) || !Array.isArray(payload.items)) {
    throw new Error('trash snapshot must include items');
  }
  return {
    total_count: requireNumber(payload, 'total_count'),
    items: payload.items.map((item) => {
      if (!isObject(item)) {
        throw new Error('trash item must be an object');
      }
      return {
        file_id: requireString(item, 'file_id'),
        path: requireString(item, 'path'),
        type: requireString(item, 'type'),
        deleted_at: requireNumber(item, 'deleted_at'),
        trash_path: requireString(item, 'trash_path'),
        exists_in_trash: Boolean(item.exists_in_trash),
        size_bytes: optionalNumber(item, 'size_bytes'),
        mime_type: optionalString(item, 'mime_type'),
      };
    }),
  };
}

function fileName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

function formatBytes(value: number | null): string {
  if (value === null) {
    return '不可用';
  }
  if (value < 1024) {
    return `${value} B`;
  }
  if (value < 1024 * 1024) {
    return `${(value / 1024).toFixed(1)} KB`;
  }
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}

function formatTime(ms: number): string {
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ms));
}

type FileVisualKind =
  | 'markdown'
  | 'image'
  | 'pdf'
  | 'text'
  | 'json'
  | 'code'
  | 'spreadsheet'
  | 'presentation'
  | 'document'
  | 'archive'
  | 'audio'
  | 'video'
  | 'unknown';

const codePreviewExtensions = new Set([
  '.bat',
  '.c',
  '.cmd',
  '.cpp',
  '.cs',
  '.css',
  '.go',
  '.h',
  '.html',
  '.java',
  '.js',
  '.jsx',
  '.kt',
  '.lua',
  '.php',
  '.ps1',
  '.py',
  '.rb',
  '.rs',
  '.sh',
  '.sql',
  '.tsx',
  '.ts',
  '.vue',
  '.xml',
  '.yaml',
  '.yml',
]);
const textPreviewExtensions = new Set([
  '.csv',
  '.ini',
  '.log',
  '.markdown',
  '.md',
  '.toml',
  '.txt',
]);
const archiveExtensions = new Set(['.7z', '.gz', '.rar', '.tar', '.tgz', '.zip']);
const spreadsheetExtensions = new Set(['.csv', '.ods', '.xls', '.xlsx']);
const presentationExtensions = new Set(['.odp', '.ppt', '.pptx']);
const documentExtensions = new Set(['.doc', '.docx', '.odt', '.rtf']);

function fileExtension(path: string): string {
  const name = fileName(path).toLowerCase();
  const dotIndex = name.lastIndexOf('.');
  return dotIndex >= 0 ? name.slice(dotIndex) : '';
}

function inferMimeTypeFromPath(path: string): string | null {
  const extension = fileExtension(path);
  if (extension === '.md' || extension === '.markdown') {
    return 'text/markdown';
  }
  if (extension === '.txt' || extension === '.log') {
    return 'text/plain';
  }
  if (extension === '.csv') {
    return 'text/csv';
  }
  if (extension === '.json') {
    return 'application/json';
  }
  if (extension === '.pdf') {
    return 'application/pdf';
  }
  if (extension === '.docx') {
    return 'application/vnd.openxmlformats-officedocument.wordprocessingml.document';
  }
  if (['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'].includes(extension)) {
    return extension === '.svg' ? 'image/svg+xml' : `image/${extension === '.jpg' ? 'jpeg' : extension.slice(1)}`;
  }
  if (['.mp3', '.wav', '.ogg', '.m4a', '.flac'].includes(extension)) {
    if (extension === '.m4a') {
      return 'audio/mp4';
    }
    return extension === '.mp3' ? 'audio/mpeg' : `audio/${extension.slice(1)}`;
  }
  if (['.mp4', '.webm', '.mov', '.avi', '.mkv'].includes(extension)) {
    if (extension === '.avi') {
      return 'video/x-msvideo';
    }
    if (extension === '.mkv') {
      return 'video/x-matroska';
    }
    return extension === '.mov' ? 'video/quicktime' : `video/${extension.slice(1)}`;
  }
  return null;
}

function fileVisualKind(path: string, mimeType?: string | null, fileType?: string): FileVisualKind {
  if (fileType === 'note' || fileType === 'ai_index' || fileType === 'ai_wiki' || fileType === 'ai_agents') {
    return 'markdown';
  }
  const normalizedMimeType = (mimeType ?? inferMimeTypeFromPath(path) ?? '').toLowerCase();
  const extension = fileExtension(path);
  if (normalizedMimeType === 'text/markdown' || extension === '.md' || extension === '.markdown') {
    return 'markdown';
  }
  if (normalizedMimeType.startsWith('image/')) {
    return 'image';
  }
  if (normalizedMimeType === 'application/pdf') {
    return 'pdf';
  }
  if (normalizedMimeType.startsWith('audio/')) {
    return 'audio';
  }
  if (normalizedMimeType.startsWith('video/')) {
    return 'video';
  }
  if (normalizedMimeType.includes('spreadsheet') || normalizedMimeType.includes('excel') || spreadsheetExtensions.has(extension)) {
    return 'spreadsheet';
  }
  if (normalizedMimeType.includes('presentation') || normalizedMimeType.includes('powerpoint') || presentationExtensions.has(extension)) {
    return 'presentation';
  }
  if (normalizedMimeType.includes('wordprocessing') || normalizedMimeType.includes('msword') || documentExtensions.has(extension)) {
    return 'document';
  }
  if (
    normalizedMimeType.includes('zip')
    || normalizedMimeType.includes('rar')
    || normalizedMimeType.includes('tar')
    || normalizedMimeType.includes('gzip')
    || archiveExtensions.has(extension)
  ) {
    return 'archive';
  }
  if (normalizedMimeType.includes('json') || extension === '.json') {
    return 'json';
  }
  if (codePreviewExtensions.has(extension)) {
    return 'code';
  }
  if (normalizedMimeType.startsWith('text/') || textPreviewExtensions.has(extension)) {
    return 'text';
  }
  return 'unknown';
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

function fileIconClassName(kind: FileVisualKind, tone: 'normal' | 'danger'): string {
  if (tone === 'danger') {
    return 'flex-shrink-0 text-[#e94560]';
  }
  switch (kind) {
    case 'image':
      return 'flex-shrink-0 text-emerald-300';
    case 'pdf':
      return 'flex-shrink-0 text-[#e94560]';
    case 'audio':
    case 'video':
      return 'flex-shrink-0 text-[#c4b5fd]';
    case 'spreadsheet':
      return 'flex-shrink-0 text-emerald-300';
    case 'presentation':
      return 'flex-shrink-0 text-[#ffb782]';
    case 'archive':
      return 'flex-shrink-0 text-[#ffd8a8]';
    case 'code':
    case 'json':
      return 'flex-shrink-0 text-[#a9c8fc]';
    case 'document':
    case 'text':
      return 'flex-shrink-0 text-slate-300';
    default:
      return 'flex-shrink-0 text-slate-400';
  }
}

function FileKindIcon({
  kind,
  size = 16,
  tone = 'normal',
}: {
  kind: FileVisualKind;
  size?: number;
  tone?: 'normal' | 'danger';
}) {
  if (kind === 'markdown') {
    return <MarkdownFileIcon size={size} tone={tone} />;
  }
  const className = fileIconClassName(kind, tone);
  switch (kind) {
    case 'image':
      return <FileImage size={size} className={className} />;
    case 'pdf':
      return <FileText size={size} className={className} />;
    case 'audio':
      return <FileAudio size={size} className={className} />;
    case 'video':
      return <FileVideoCamera size={size} className={className} />;
    case 'spreadsheet':
      return <FileSpreadsheet size={size} className={className} />;
    case 'presentation':
      return <Presentation size={size} className={className} />;
    case 'archive':
      return <FileArchive size={size} className={className} />;
    case 'code':
      return <FileCode size={size} className={className} />;
    case 'json':
      return <FileJson size={size} className={className} />;
    case 'document':
      return <FileType size={size} className={className} />;
    case 'text':
      return <FileText size={size} className={className} />;
    default:
      return <FileQuestionMark size={size} className={className} />;
  }
}

function TrashFileIcon({ item, size = 20 }: { item: TrashItem; size?: number }) {
  return (
    <FileKindIcon
      kind={fileVisualKind(item.path, item.mime_type, item.type)}
      size={size}
      tone={item.exists_in_trash ? 'normal' : 'danger'}
    />
  );
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (isObject(payload) && typeof payload.message === 'string') {
      if (response.status === 404 && payload.message === 'Route was not found.') {
        return '回收站 API 未加载，请重启同步桥和前端开发服务后重试。';
      }
      return payload.message;
    }
  } catch {
    // Fall through.
  }
  return `request failed: ${response.status}`;
}

export default function TrashView() {
  const [snapshot, setSnapshot] = useState<TrashSnapshot>({ items: [], total_count: 0 });
  const [query, setQuery] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [lastError, setLastError] = useState<string | null>(null);
  const [pendingTrashAction, setPendingTrashAction] = useState<PendingTrashAction | null>(null);

  const loadTrash = async () => {
    setIsLoading(true);
    try {
      const response = await fetch(`${syncBridgeUrl}/api/workspace/trash`, { cache: 'no-store' });
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      setSnapshot(parseTrashSnapshot(await response.json()));
      setLastError(null);
    } catch (error) {
      setLastError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadTrash();
  }, []);

  const filteredItems = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) {
      return snapshot.items;
    }
    return snapshot.items.filter((item) => item.path.toLowerCase().includes(normalized));
  }, [query, snapshot.items]);

  async function mutateTrash(endpoint: string, init: RequestInit) {
    setIsLoading(true);
    try {
      const response = await fetch(`${syncBridgeUrl}${endpoint}`, init);
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      invalidateWorkspaceFilesCache();
      await loadTrash();
    } catch (error) {
      setLastError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsLoading(false);
    }
  }

  async function confirmPendingTrashAction() {
    const action = pendingTrashAction;
    if (!action) {
      return;
    }
    setPendingTrashAction(null);
    if (action.kind === 'empty') {
      await mutateTrash('/api/workspace/trash/empty', { method: 'POST' });
      return;
    }
    await mutateTrash(`/api/workspace/trash/${encodeURIComponent(action.item.file_id)}`, { method: 'DELETE' });
  }

  const emptyStateText = query.trim()
    ? '没有匹配当前搜索的回收站项目。清除搜索条件或刷新后再试。'
    : '回收站为空。被删除的文档和附件会先出现在这里，确认后再永久清除。';

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[#1a1a2e]">
      <div className="flex-shrink-0 border-b border-[#0f3460] bg-[#16213e] px-6 py-6 shadow-lg shadow-black/20 md:px-8">
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
          <div>
            <h1 className="flex items-center gap-3 text-3xl font-bold tracking-tight text-[#e3e2e6]">
              <Trash2 className="text-[#e94560]" size={32} />
              回收站
            </h1>
            <p className="mt-2 max-w-xl text-sm text-slate-400">
              这里显示从工作区移入 `.noteapp/trash` 的文档和附件。恢复会回到原路径，原路径被占用时会失败以避免覆盖。
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <button
              onClick={loadTrash}
              disabled={isLoading}
              className="flex w-fit items-center gap-2 rounded-lg border border-[#0f3460] bg-[#121316] px-4 py-2 text-[13px] font-medium text-slate-300 transition-colors hover:text-white disabled:opacity-50"
            >
              <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
              刷新
            </button>
            <button
              onClick={() => setPendingTrashAction({ kind: 'empty' })}
              disabled={isLoading || snapshot.items.length === 0}
              className="flex w-fit items-center gap-2 rounded-lg border border-[#e94560]/40 bg-[#e94560]/10 px-4 py-2 text-[13px] font-medium text-[#ffb3c0] transition-colors hover:text-white disabled:opacity-50"
            >
              <AlertCircle size={16} />
              清空回收站
            </button>
          </div>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 md:p-8">
        <div className="mx-auto max-w-5xl">
          <div className="mb-6 flex items-center gap-4">
            <label className="relative max-w-md flex-1">
              <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                type="text"
                placeholder="搜索回收站..."
                className="w-full rounded-lg border border-[#0f3460] bg-[#121316] py-2 pl-10 pr-4 font-sans text-[13px] text-white placeholder:text-slate-500 focus:border-[#e94560] focus:outline-none focus:ring-1 focus:ring-[#e94560]/50"
              />
            </label>
            <span className="font-mono text-[11px] text-slate-500">{snapshot.total_count} items</span>
          </div>

          {lastError && (
            <div className="mb-4 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782]">
              {lastError}
            </div>
          )}

          <div className="overflow-hidden rounded-xl border border-[#0f3460] bg-[#16213e] shadow-lg shadow-black/20">
            <div className="hidden grid-cols-12 gap-4 border-b border-[#0f3460] bg-[#1f2b4a]/50 p-4 text-xs font-semibold uppercase tracking-wider text-slate-400 md:grid">
              <div className="col-span-5">文件</div>
              <div className="col-span-3">原路径</div>
              <div className="col-span-2">移入时间</div>
              <div className="col-span-2 text-right">操作</div>
            </div>
            <div className="divide-y divide-[#0f3460]">
              {filteredItems.length === 0 ? (
                <div className="p-8 text-center text-[13px] leading-6 text-slate-500">{emptyStateText}</div>
              ) : (
                filteredItems.map((item) => (
                  <div key={item.file_id} className="group grid grid-cols-1 items-center gap-4 p-4 transition-colors hover:bg-[#1f2b4a] md:grid-cols-12">
                    <div className="col-span-1 flex items-center gap-3 md:col-span-5">
                      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg border border-[#0f3460] bg-[#121316]">
                        <TrashFileIcon item={item} />
                      </div>
                      <div className="min-w-0">
                        <p className="truncate text-[14px] font-medium text-[#e3e2e6]">{fileName(item.path)}</p>
                        <p className="mt-1 font-mono text-[11px] text-slate-500">{item.type} / {formatBytes(item.size_bytes)}</p>
                        {!item.exists_in_trash && <p className="mt-1 text-[11px] text-[#ffb782]">trash file missing</p>}
                      </div>
                    </div>
                    <div className="hidden min-w-0 items-center text-[13px] text-slate-400 md:col-span-3 md:flex">
                      <span className="truncate" title={item.path}>{item.path}</span>
                    </div>
                    <div className="hidden items-center text-[13px] text-slate-400 md:col-span-2 md:flex">
                      {formatTime(item.deleted_at)}
                    </div>
                    <div className="col-span-1 flex items-center justify-end gap-2 md:col-span-2">
                      <button
                        onClick={() => void mutateTrash(`/api/workspace/trash/${encodeURIComponent(item.file_id)}/restore`, { method: 'POST' })}
                        disabled={!item.exists_in_trash || isLoading}
                        className="flex items-center gap-1.5 rounded border border-[#0f3460] bg-[#121316] px-3 py-1.5 text-xs font-medium text-[#a9c8fc] transition-colors hover:bg-[#0f3460] hover:text-white disabled:opacity-50"
                      >
                        <RefreshCw size={14} />
                        恢复
                      </button>
                      <button
                        onClick={() => setPendingTrashAction({ kind: 'purge', item })}
                        disabled={isLoading}
                        className="rounded border border-[#e94560]/30 bg-[#e94560]/10 px-3 py-1.5 text-xs font-medium text-[#ffb3c0] transition-colors hover:text-white disabled:opacity-50"
                      >
                        清除
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </div>
      {pendingTrashAction && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-2xl border border-[#e94560]/40 bg-[#16213e] p-5 shadow-2xl shadow-black/50">
            <div className="flex items-start gap-3">
              <div className="mt-0.5 flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg border border-[#e94560]/30 bg-[#e94560]/10 text-[#ffb3c0]">
                <AlertCircle size={20} />
              </div>
              <div className="min-w-0">
                <h2 className="text-[16px] font-bold text-white">
                  {pendingTrashAction.kind === 'empty' ? '清空回收站' : '永久清除文件'}
                </h2>
                <p className="mt-2 text-[13px] leading-6 text-slate-300">
                  {pendingTrashAction.kind === 'empty'
                    ? `将永久删除回收站中的 ${snapshot.total_count} 个项目。执行后无法在本应用内恢复，请确认这些文件不再需要。`
                    : `将永久删除回收站副本：${fileName(pendingTrashAction.item.path)}。执行后无法从回收站恢复到原路径。`}
                </p>
                {pendingTrashAction.kind === 'purge' && (
                  <p className="mt-2 break-all font-mono text-[11px] text-slate-500">
                    {pendingTrashAction.item.path}
                  </p>
                )}
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setPendingTrashAction(null)}
                className="rounded border border-[#0f3460] bg-[#121316] px-4 py-2 text-[12px] font-semibold text-slate-300 hover:text-white"
              >
                取消
              </button>
              <button
                type="button"
                disabled={isLoading}
                onClick={() => void confirmPendingTrashAction()}
                className="rounded border border-[#e94560]/40 bg-[#e94560] px-4 py-2 text-[12px] font-semibold text-white hover:bg-[#ff5d76] disabled:cursor-not-allowed disabled:opacity-50"
              >
                确认永久删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

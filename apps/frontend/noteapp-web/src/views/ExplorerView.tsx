import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Cloud,
  FileText,
  Folder,
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
  return parts.length > 1 ? parts[0] : '知识库根目录';
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
  const filesByFolder = useMemo(() => {
    const grouped = new Map<string, WorkspaceFileEntry[]>();
    for (const file of visibleFiles) {
      const folder = folderName(file.path);
      grouped.set(folder, [...(grouped.get(folder) ?? []), file]);
    }
    return Array.from(grouped.entries()).sort(([left], [right]) => left.localeCompare(right));
  }, [visibleFiles]);
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

  useEffect(() => {
    if (!selectedFile || !selectedFile.exists_on_disk || selectedFile.status !== 'active') {
      clearContent();
      return;
    }
    void loadContent(selectedFile.file_id);
  }, [clearContent, loadContent, selectedFile]);

  const [draftText, setDraftText] = useState('');
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
          {filesByFolder.length === 0 ? (
            <div className="p-4 text-[13px] text-slate-400">暂无已跟踪文件。</div>
          ) : (
            filesByFolder.map(([folder, folderFiles]) => (
              <div key={folder} className="flex flex-col">
                <div className="flex items-center gap-2 px-4 py-2 text-slate-300">
                  <Folder size={16} className="text-[#a9c8fc]" />
                  <span className="text-[13px] font-semibold flex-1 font-sans truncate">{folder}</span>
                  <span className="font-mono text-[10px] text-slate-500">{folderFiles.length}</span>
                </div>
                <div className="flex flex-col">
                  {folderFiles.map((file) => (
                    <button
                      key={file.file_id}
                      onClick={() => setSelectedFileId(file.file_id)}
                      title={file.path}
                      className={`flex items-center gap-2 px-8 py-2 text-[13px] font-sans truncate text-left transition-colors ${
                        selectedFileId === file.file_id
                          ? 'bg-[#1f2b4a] text-[#e3e2e6] border-r-2 border-[#e94560]'
                          : 'text-slate-400 hover:text-slate-200 hover:bg-[#1f2b4a]'
                      }`}
                    >
                      <FileText size={14} className={file.exists_on_disk ? 'text-slate-500' : 'text-[#e94560]'} />
                      <span className="truncate">{fileName(file.path)}</span>
                    </button>
                  ))}
                </div>
              </div>
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

        <div className="flex-1 overflow-y-auto bg-[#121316] p-6 md:p-8">
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
            <div className="max-w-4xl flex flex-col gap-6">
              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-5 shadow-lg shadow-black/20">
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

              <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
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

              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] shadow-lg shadow-black/20 overflow-hidden">
                <div className="flex items-center justify-between gap-3 border-b border-[#0f3460] px-5 py-3">
                  <div className="flex items-center gap-3 min-w-0">
                    <h3 className="text-[15px] font-bold text-[#e3e2e6]">内容编辑器</h3>
                    <span className="font-mono text-[11px] text-slate-500">
                      {isContentLoading ? '加载中' : selectedContent ? selectedContent.encoding : '不可用'}
                    </span>
                    {savedAtMs && !isContentDirty && (
                      <span className="hidden sm:inline font-mono text-[11px] text-emerald-300">
                        已保存到本地 {formatFileTime(savedAtMs)}
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
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
                <textarea
                  value={isContentLoading ? '正在加载文件内容...' : draftText}
                  onChange={(event) => setDraftText(event.target.value)}
                  disabled={!canEditContent || isContentLoading || isContentSaving}
                  spellCheck={false}
                  className="block h-[420px] w-full resize-y overflow-auto bg-[#121316] p-5 font-mono text-[13px] leading-relaxed text-slate-300 outline-none placeholder:text-slate-600 disabled:cursor-not-allowed disabled:text-slate-500"
                  placeholder="此文件内容不可用。"
                />
              </section>

              <div className="inline-flex w-fit items-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-3 py-2 text-[13px] text-slate-500">
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

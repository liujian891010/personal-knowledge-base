import { useEffect, useMemo, useState } from 'react';
import { AlertTriangle, CheckCircle2, FileText, RefreshCw, Trash2 } from 'lucide-react';

import { useSyncShellController } from '../useSyncShellSnapshot';
import { useWorkspaceFileContentController } from '../useWorkspaceFileContent';
import { useWorkspaceFilesController } from '../useWorkspaceFiles';

function formatBytes(value: number | null): string {
  if (value === null) {
    return '未知';
  }
  if (value < 1024) {
    return `${value} B`;
  }
  return `${(value / 1024).toFixed(1)} KB`;
}

export default function ConflictsView() {
  const {
    summary: syncSummary,
    cards,
    lastActionNotice,
    isExecuting,
    executingActionId,
    refresh: refreshSync,
    executeSyncAction,
  } = useSyncShellController();
  const {
    files,
    lastError: workspaceError,
    isRefreshing: isWorkspaceRefreshing,
    refresh: refreshWorkspace,
  } = useWorkspaceFilesController();
  const {
    content,
    lastError: contentError,
    isLoading: isContentLoading,
    loadContent,
    clearContent,
  } = useWorkspaceFileContentController();
  const conflictFiles = useMemo(
    () => files.filter((file) => file.status === 'conflict_copy'),
    [files],
  );
  const conflictCard = cards.find((card) => card.card_id === 'conflicts') ?? null;
  const resolveAllAction = conflictCard?.actions.find(
    (action) => action.action_id === 'resolve-conflicts-all',
  ) ?? null;
  const [selectedFileId, setSelectedFileId] = useState<string | null>(null);
  const selectedFile = conflictFiles.find((file) => file.file_id === selectedFileId) ?? conflictFiles[0] ?? null;

  useEffect(() => {
    if (selectedFile) {
      setSelectedFileId(selectedFile.file_id);
      void loadContent(selectedFile.file_id);
      return;
    }
    setSelectedFileId(null);
    clearContent();
  }, [clearContent, loadContent, selectedFile]);

  const resolveAll = async () => {
    if (!resolveAllAction) {
      return;
    }
    await executeSyncAction(resolveAllAction);
    await Promise.all([refreshWorkspace(), refreshSync()]);
  };

  const isBusy = isExecuting || isWorkspaceRefreshing || isContentLoading;

  return (
    <div className="flex h-full flex-col bg-[#121316]">
      <header className="flex-shrink-0 border-b border-[#43474f]/30 bg-[#1a1c1f] p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2">
              <AlertTriangle className={syncSummary.conflictBadgeCount > 0 ? 'text-[#ffb782]' : 'text-emerald-300'} size={20} />
              <span className="text-[13px] font-semibold uppercase tracking-wider text-slate-300">同步冲突</span>
            </div>
            <h2 className="break-words text-2xl font-bold text-[#e3e2e6]">
              {syncSummary.conflictBadgeCount > 0
                ? `${syncSummary.conflictBadgeCount} 个本地冲突等待处理`
                : '当前没有未处理冲突'}
            </h2>
            <p className="mt-2 text-sm text-slate-400">
              冲突副本只保存在本地，不会同步到远端。确认已保留需要的内容后，可以清理冲突副本并重新开放同步。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              disabled={isBusy}
              onClick={() => void Promise.all([refreshWorkspace(), refreshSync()])}
              className="inline-flex h-9 items-center gap-2 rounded border border-[#43474f] bg-[#121316] px-3 text-[13px] font-medium text-slate-300 transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              <RefreshCw size={15} className={isWorkspaceRefreshing ? 'animate-spin' : ''} />
              刷新
            </button>
            <button
              disabled={!resolveAllAction || isBusy}
              onClick={() => void resolveAll()}
              className="inline-flex h-9 items-center gap-2 rounded border border-[#ffb782]/30 bg-[#ffb782]/10 px-3 text-[13px] font-medium text-[#ffb782] transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              <Trash2 size={15} />
              {executingActionId === 'resolve-conflicts-all' ? '处理中...' : '清理全部冲突副本'}
            </button>
          </div>
        </div>
        {(workspaceError || contentError || lastActionNotice) && (
          <div className="mt-4 rounded border border-[#0f3460] bg-[#121316] px-3 py-2 text-[12px] text-slate-300">
            {lastActionNotice ? `${lastActionNotice.title}：${lastActionNotice.detail}` : workspaceError ?? contentError}
          </div>
        )}
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-hidden p-4 lg:grid-cols-[340px_1fr] lg:p-6">
        <section className="min-h-0 overflow-hidden rounded-xl border border-[#43474f]/30 bg-[#1a1c1f]">
          <div className="border-b border-[#43474f]/30 px-4 py-3">
            <div className="text-[13px] font-semibold text-[#e3e2e6]">冲突副本</div>
            <div className="mt-1 font-mono text-[11px] text-slate-500">{conflictFiles.length} 个文件</div>
          </div>
          <div className="max-h-full overflow-y-auto p-2">
            {conflictFiles.length === 0 ? (
              <div className="flex flex-col items-center justify-center gap-3 px-4 py-14 text-center">
                <CheckCircle2 className="text-emerald-300" size={34} />
                <div>
                  <div className="text-[14px] font-semibold text-[#e3e2e6]">没有冲突副本</div>
                  <p className="mt-1 text-[13px] text-slate-400">同步流程没有发现需要人工处理的本地副本。</p>
                </div>
              </div>
            ) : (
              conflictFiles.map((file) => (
                <button
                  key={file.file_id}
                  onClick={() => setSelectedFileId(file.file_id)}
                  className={`mb-2 flex w-full items-start gap-3 rounded-lg border p-3 text-left transition-colors ${
                    selectedFile?.file_id === file.file_id
                      ? 'border-[#e94560] bg-[#1f2b4a] text-white'
                      : 'border-[#43474f]/30 bg-[#121316] text-slate-300 hover:border-[#0f3460]'
                  }`}
                >
                  <FileText className="mt-0.5 flex-shrink-0 text-[#ffb782]" size={16} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13px] font-semibold">{file.path}</span>
                    <span className="mt-1 block truncate font-mono text-[11px] text-slate-500">
                      {file.file_id} / {formatBytes(file.size_bytes)}
                    </span>
                  </span>
                </button>
              ))
            )}
          </div>
        </section>

        <section className="min-h-0 overflow-hidden rounded-xl border border-[#43474f]/30 bg-[#1a1c1f]">
          {selectedFile ? (
            <div className="flex h-full min-h-0 flex-col">
              <div className="border-b border-[#43474f]/30 px-4 py-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="rounded border border-[#ffb782]/30 bg-[#ffb782]/10 px-2 py-0.5 font-mono text-[11px] uppercase tracking-wider text-[#ffb782]">
                    冲突副本
                  </span>
                  <span className="font-mono text-[11px] text-slate-500">
                    来源：{selectedFile.conflict_source_file_id ?? '未知'}
                  </span>
                </div>
                <h3 className="mt-2 truncate text-[15px] font-bold text-[#e3e2e6]">{selectedFile.path}</h3>
              </div>
              <pre className="min-h-0 flex-1 overflow-auto bg-[#0d0e11] p-5 font-mono text-[13px] leading-relaxed text-slate-300">
                {isContentLoading ? '正在加载冲突副本内容...' : content?.text ?? '此冲突副本内容不可用。'}
              </pre>
            </div>
          ) : (
            <div className="flex h-full flex-col items-center justify-center px-6 text-center">
              <CheckCircle2 className="text-emerald-300" size={42} />
              <h3 className="mt-4 text-lg font-bold text-[#e3e2e6]">冲突处理完成</h3>
              <p className="mt-2 max-w-md text-[13px] text-slate-400">
                当前工作区没有冲突副本。后续可以继续同步、拉取或提交本地变更。
              </p>
            </div>
          )}
        </section>
      </main>
    </div>
  );
}

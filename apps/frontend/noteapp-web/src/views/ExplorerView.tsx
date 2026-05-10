import React, { useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Brain,
  ChevronRight,
  FileText,
  Folder,
  Network,
  Plus,
  RefreshCw,
} from 'lucide-react';

import { useWorkspaceFilesController } from '../useWorkspaceFiles';
import type { WorkspaceFileEntry } from '../workspaceFiles';

function fileName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

function folderName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 1 ? parts[0] : 'Vault root';
}

function formatBytes(value: number | null): string {
  if (value === null) {
    return 'Missing';
  }
  if (value < 1024) {
    return `${value} B`;
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
      return;
    }
    if (!selectedFileId || !visibleFiles.some((file) => file.file_id === selectedFileId)) {
      setSelectedFileId(visibleFiles[0].file_id);
    }
  }, [selectedFileId, visibleFiles]);

  const selectedFile = visibleFiles.find((file) => file.file_id === selectedFileId) ?? null;

  return (
    <div className="flex h-full bg-[#1a1a2e] overflow-hidden">
      <aside className="hidden md:flex w-72 border-r border-[#0f3460] bg-[#16213e]/80 flex-shrink-0 flex-col max-h-full overflow-hidden">
        <div className="p-4 border-b border-[#0f3460] flex items-center justify-between bg-[#16213e]">
          <div className="min-w-0">
            <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">Workspace</span>
            <div className="mt-1 flex items-center gap-2 font-mono text-[10px] text-slate-500">
              <span>{source}</span>
              <span>{summary.activeCount} active</span>
              {summary.missingCount > 0 && <span className="text-[#e94560]">{summary.missingCount} missing</span>}
            </div>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={refresh}
              disabled={isRefreshing}
              title="Refresh workspace"
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
            <div className="p-4 text-[13px] text-slate-400">No tracked files.</div>
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
                {selectedFile ? fileName(selectedFile.path) : 'Workspace'}
              </span>
            </div>
            <div className="h-4 w-px bg-[#0f3460] hidden sm:block" />
            <div className="hidden sm:flex items-center gap-2 text-slate-500 font-mono text-[11px] min-w-0">
              <span>{summary.totalCount} tracked</span>
              {selectedFile && <span className="truncate">{formatBytes(selectedFile.size_bytes)}</span>}
            </div>
          </div>
          <div className="flex items-center gap-4">
            <div className="flex items-center bg-[#0f3460]/30 rounded-md p-0.5 border border-[#0f3460]">
              <button onClick={() => setView('wiki')} className="px-3 py-1.5 rounded text-[#e94560] hover:bg-[#1f2b4a] hover:text-white transition-colors flex items-center gap-1.5 font-mono text-[12px] font-bold uppercase tracking-wider">
                <Brain size={14} /> [[wiki]]
              </button>
            </div>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto bg-[#121316] p-6 md:p-8">
          {lastError && (
            <div className="mb-4 rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782] line-clamp-3">
              {lastError}
            </div>
          )}

          {selectedFile ? (
            <div className="max-w-4xl flex flex-col gap-6">
              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-5 shadow-lg shadow-black/20">
                <div className="flex flex-col md:flex-row md:items-start justify-between gap-4">
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-3">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded border font-mono text-[11px] uppercase tracking-wider font-bold ${statusClasses(selectedFile)}`}>
                        {selectedFile.exists_on_disk ? selectedFile.status : 'missing'}
                      </span>
                      <span className="font-mono text-[11px] text-slate-500">{selectedFile.type}</span>
                    </div>
                    <h2 className="text-2xl font-bold text-[#e3e2e6] truncate">{fileName(selectedFile.path)}</h2>
                    <p className="font-mono text-[12px] text-slate-500 mt-2 break-words">{selectedFile.path}</p>
                  </div>
                  {!selectedFile.exists_on_disk && (
                    <div className="flex items-center gap-2 text-[#e94560] text-[12px]">
                      <AlertTriangle size={16} />
                      <span>Missing on disk</span>
                    </div>
                  )}
                </div>
              </section>

              <section className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="border border-[#0f3460] rounded-xl bg-[#16213e] p-5">
                  <h3 className="text-[15px] font-bold text-[#e3e2e6] mb-4">File metadata</h3>
                  <dl className="grid grid-cols-1 gap-3 text-[13px]">
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">File ID</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{selectedFile.file_id}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">Updated</dt>
                      <dd className="font-mono text-[#e3e2e6] mt-1">{formatFileTime(selectedFile.updated_at)}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">Size</dt>
                      <dd className="font-mono text-[#e3e2e6] mt-1">{formatBytes(selectedFile.size_bytes)}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">Revision</dt>
                      <dd className="font-mono text-[#e3e2e6] mt-1">{selectedFile.last_known_revision ?? 'local'}</dd>
                    </div>
                  </dl>
                </div>

                <div className="border border-[#0f3460] rounded-xl bg-[#16213e] p-5">
                  <h3 className="text-[15px] font-bold text-[#e3e2e6] mb-4">Workspace</h3>
                  <dl className="grid grid-cols-1 gap-3 text-[13px]">
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">Vault</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{summary.vaultId}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">Device</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{summary.deviceId}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">Root</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{summary.vaultRoot}</dd>
                    </div>
                    <div>
                      <dt className="text-[11px] uppercase tracking-wider text-slate-500">Content hash</dt>
                      <dd className="font-mono text-[#e3e2e6] break-words mt-1">{selectedFile.content_hash ?? 'none'}</dd>
                    </div>
                  </dl>
                </div>
              </section>

              <button
                onClick={() => setView('wiki')}
                className="inline-flex w-fit items-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-3 py-2 text-[13px] text-[#a9c8fc] hover:text-white transition-colors"
              >
                <Network size={14} />
                <span>Open wiki graph</span>
                <ChevronRight size={14} />
              </button>
            </div>
          ) : (
            <div className="max-w-2xl rounded-xl border border-[#0f3460] bg-[#16213e] p-6 text-[13px] text-slate-400">
              No tracked file is selected.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

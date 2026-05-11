import React, { useMemo, useState } from 'react';
import { Bot, ExternalLink, FileText, RefreshCw, Sparkles } from 'lucide-react';

import { invalidateWorkspaceFilesCache, useWorkspaceFilesController } from '../useWorkspaceFiles';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

interface AiWikiArtifact {
  title: string;
  path: string;
  source_path: string;
}

interface AiWikiCompileResult {
  generated_at: string;
  source_count: number;
  artifact_count: number;
  index_path: string;
  artifacts: AiWikiArtifact[];
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function parseCompileResult(payload: unknown): AiWikiCompileResult {
  if (!isObject(payload) || !Array.isArray(payload.artifacts)) {
    throw new Error('AI wiki compile response must include artifacts');
  }
  return {
    generated_at: String(payload.generated_at ?? ''),
    source_count: Number(payload.source_count ?? 0),
    artifact_count: Number(payload.artifact_count ?? 0),
    index_path: String(payload.index_path ?? '.ai/index.md'),
    artifacts: payload.artifacts.map((item) => {
      if (!isObject(item)) {
        throw new Error('AI wiki artifact must be an object');
      }
      return {
        title: String(item.title ?? ''),
        path: String(item.path ?? ''),
        source_path: String(item.source_path ?? ''),
      };
    }),
  };
}

async function responseError(response: Response): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (isObject(payload) && typeof payload.message === 'string') {
      return payload.message;
    }
  } catch {
    // Fall through.
  }
  return `request failed: ${response.status}`;
}

export default function AiWikiView({
  onOpenWorkspacePath,
}: {
  onOpenWorkspacePath: (path: string) => void;
}) {
  const [result, setResult] = useState<AiWikiCompileResult | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [isCompiling, setIsCompiling] = useState(false);
  const {
    files,
    refresh: refreshWorkspaceFiles,
    isRefreshing: isWorkspaceRefreshing,
    lastError: workspaceFilesError,
  } = useWorkspaceFilesController();
  const aiFiles = useMemo(
    () => files
      .filter((file) => file.status === 'active' && (file.type === 'ai_index' || file.type === 'ai_wiki'))
      .sort((left, right) => left.path.localeCompare(right.path)),
    [files],
  );

  async function compileWiki() {
    setIsCompiling(true);
    try {
      const response = await fetch(`${syncBridgeUrl}/api/ai/wiki/compile`, { method: 'POST' });
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      const nextResult = parseCompileResult(await response.json());
      invalidateWorkspaceFilesCache();
      await refreshWorkspaceFiles();
      setResult(nextResult);
      setLastError(null);
    } catch (error) {
      setLastError(error instanceof Error ? error.message : String(error));
    } finally {
      setIsCompiling(false);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden bg-[#1a1a2e]">
      <div className="flex-shrink-0 border-b border-[#0f3460] bg-[#16213e] px-6 py-6 shadow-lg shadow-black/20 md:px-8">
        <div className="flex flex-col justify-between gap-4 md:flex-row md:items-center">
          <div>
            <h1 className="flex items-center gap-3 text-3xl font-bold tracking-tight text-[#e3e2e6]">
              <Bot className="text-[#a9c8fc]" size={32} />
              AI 知识库
            </h1>
            <p className="mt-2 max-w-2xl text-sm leading-relaxed text-slate-400">
              当前 MVP 使用本地确定性编译：读取 Markdown 笔记，生成 `.ai/wiki/*.md` 和 `.ai/index.md`。
              附件/图片/PDF 解析已暂缓，后续再接入。
            </p>
          </div>
          <button
            onClick={() => void compileWiki()}
            disabled={isCompiling}
            className="inline-flex w-fit items-center justify-center gap-2 rounded-lg border border-[#0f3460] bg-[#0f3460]/30 px-4 py-2 text-[13px] font-semibold text-[#a9c8fc] transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
          >
            <RefreshCw size={16} className={isCompiling ? 'animate-spin' : ''} />
            编译 AI Wiki
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 md:p-8">
        <div className="mx-auto grid max-w-5xl gap-5">
          <section className="rounded-xl border border-[#0f3460] bg-[#16213e] p-6 shadow-lg shadow-black/20">
            <div className="flex items-start gap-3">
              <div className="rounded-lg border border-[#0f3460] bg-[#121316] p-3 text-[#a9c8fc]">
                <Sparkles size={22} />
              </div>
              <div>
                <h2 className="text-xl font-bold text-[#e3e2e6]">本地编译边界</h2>
                <p className="mt-2 text-[13px] leading-relaxed text-slate-400">
                  首版不调用外部模型，先建立稳定产物层。编译后的 Wiki 页面会作为 `ai_wiki` 文件进入 filemap，
                  可以被搜索、双链和后续同步流程识别。
                </p>
              </div>
            </div>
          </section>

          {lastError && (
            <div className="rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782]">
              {lastError}
            </div>
          )}

          {workspaceFilesError && (
            <div className="rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782]">
              {workspaceFilesError}
            </div>
          )}

          {result && (
            <section className="overflow-hidden rounded-xl border border-[#0f3460] bg-[#16213e] shadow-lg shadow-black/20">
              <div className="border-b border-[#0f3460] p-4">
                <h2 className="text-lg font-bold text-[#e3e2e6]">最近一次编译</h2>
                <p className="mt-1 font-mono text-[11px] text-slate-500">
                  {result.generated_at} / sources {result.source_count} / pages {result.artifact_count}
                </p>
                <button
                  onClick={() => onOpenWorkspacePath(result.index_path)}
                  className="mt-2 inline-flex items-center gap-2 font-mono text-[12px] text-[#a9c8fc] transition-colors hover:text-white"
                >
                  {result.index_path}
                  <ExternalLink size={13} />
                </button>
              </div>
              <div className="divide-y divide-[#0f3460]">
                {result.artifacts.map((artifact) => (
                  <button
                    key={artifact.path}
                    onClick={() => onOpenWorkspacePath(artifact.path)}
                    className="flex w-full items-center gap-3 p-4 text-left transition-colors hover:bg-[#1f2b4a]"
                  >
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#0f3460] bg-[#121316]">
                      <FileText size={18} className="text-slate-400" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[14px] font-medium text-[#e3e2e6]">{artifact.title}</p>
                      <p className="mt-1 truncate font-mono text-[11px] text-slate-500">
                        {artifact.path} from {artifact.source_path}
                      </p>
                    </div>
                    <ExternalLink size={15} className="text-slate-500" />
                  </button>
                ))}
              </div>
            </section>
          )}

          <section className="overflow-hidden rounded-xl border border-[#0f3460] bg-[#16213e] shadow-lg shadow-black/20">
            <div className="flex items-center justify-between gap-3 border-b border-[#0f3460] p-4">
              <div>
                <h2 className="text-lg font-bold text-[#e3e2e6]">已生成页面</h2>
                <p className="mt-1 text-[12px] text-slate-500">
                  当前 filemap 中的 AI Index / AI Wiki 文件，共 {aiFiles.length} 个。
                </p>
              </div>
              <button
                onClick={() => void refreshWorkspaceFiles()}
                disabled={isWorkspaceRefreshing}
                className="inline-flex items-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:text-white disabled:opacity-50"
              >
                <RefreshCw size={14} className={isWorkspaceRefreshing ? 'animate-spin' : ''} />
                刷新
              </button>
            </div>
            <div className="divide-y divide-[#0f3460]">
              {aiFiles.length === 0 ? (
                <div className="p-8 text-center text-[13px] text-slate-500">
                  还没有生成 AI Wiki 页面。先点击上方“编译 AI Wiki”。
                </div>
              ) : (
                aiFiles.map((file) => (
                  <button
                    key={file.file_id}
                    onClick={() => onOpenWorkspacePath(file.path)}
                    className="flex w-full items-center gap-3 p-4 text-left transition-colors hover:bg-[#1f2b4a]"
                  >
                    <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#0f3460] bg-[#121316]">
                      <FileText size={18} className={file.type === 'ai_index' ? 'text-[#a9c8fc]' : 'text-slate-400'} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[14px] font-medium text-[#e3e2e6]">
                        {file.type === 'ai_index' ? 'AI Knowledge Index' : file.path.split('/').pop()}
                      </p>
                      <p className="mt-1 truncate font-mono text-[11px] text-slate-500">{file.path}</p>
                    </div>
                    <ExternalLink size={15} className="text-slate-500" />
                  </button>
                ))
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}

import React, { useEffect, useMemo, useState } from 'react';
import { Bot, Clock, ExternalLink, FileText, Fingerprint, MessageSquare, RefreshCw, Send, Sparkles } from 'lucide-react';

import { invalidateWorkspaceFilesCache, useWorkspaceFilesController } from '../useWorkspaceFiles';
import { parseWorkspaceFileContent } from '../workspaceFileContent';
import type { WorkspaceFileEntry } from '../workspaceFiles';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

interface AiWikiArtifact {
  title: string;
  path: string;
  source_path: string;
}

interface AiWikiSkippedPage {
  path: string;
  reason: string;
  source_path: string | null;
}

interface AiWikiCompileResult {
  generated_at: string;
  source_count: number;
  artifact_count: number;
  written_count: number;
  skipped_count: number;
  index_path: string;
  artifacts: AiWikiArtifact[];
  skipped: AiWikiSkippedPage[];
}

interface AiWikiPageSummary {
  fileId: string;
  path: string;
  type: string;
  title: string;
  pageType: string;
  sourcePath: string | null;
  sourceFileId: string | null;
  sourceContentHash: string | null;
  lastCompiledAt: string | null;
  sourcesHash: string | null;
  bodyPreview: string;
}

interface AiAskCitation {
  file_id: string;
  path: string;
  title: string;
  excerpt: string;
  score: number;
}

interface AiAskResult {
  question: string;
  answer: string;
  citation_count: number;
  citations: AiAskCitation[];
  model_status: string;
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
    written_count: Number(payload.written_count ?? 0),
    skipped_count: Number(payload.skipped_count ?? 0),
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
    skipped: Array.isArray(payload.skipped)
      ? payload.skipped.map((item) => {
        if (!isObject(item)) {
          throw new Error('AI wiki skipped item must be an object');
        }
        return {
          path: String(item.path ?? ''),
          reason: String(item.reason ?? ''),
          source_path: typeof item.source_path === 'string' ? item.source_path : null,
        };
      })
      : [],
  };
}

function parseAskResult(payload: unknown): AiAskResult {
  if (!isObject(payload) || !Array.isArray(payload.citations)) {
    throw new Error('AI ask response must include citations');
  }
  return {
    question: String(payload.question ?? ''),
    answer: String(payload.answer ?? ''),
    citation_count: Number(payload.citation_count ?? 0),
    model_status: String(payload.model_status ?? 'unknown'),
    citations: payload.citations.map((item) => {
      if (!isObject(item)) {
        throw new Error('AI ask citation must be an object');
      }
      return {
        file_id: String(item.file_id ?? ''),
        path: String(item.path ?? ''),
        title: String(item.title ?? ''),
        excerpt: String(item.excerpt ?? ''),
        score: Number(item.score ?? 0),
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

function formatAskError(message: string): string {
  const lower = message.toLowerCase();
  if (lower.includes('route was not found') || lower.includes('not found') || lower.includes('404')) {
    return 'AI Ask 接口不可用：请重启本机 sync:bridge，确认已加载最新 /api/ai/ask 路由。';
  }
  if (lower.includes('failed to fetch') || lower.includes('connection refused')) {
    return '无法连接本机 bridge：请先启动或重启 sync:bridge，再重试 Ask AI Wiki。';
  }
  if (lower.includes('not_configured') || lower.includes('api key')) {
    return `模型配置不可用：请到设置页填写 AI Key 并点击 Test。原始错误：${message}`;
  }
  return message;
}

function askStatusNotice(result: AiAskResult): { level: 'info' | 'warning'; title: string; body: string } | null {
  const status = result.model_status.toLowerCase();
  if (status.includes('error_fallback')) {
    return {
      level: 'warning',
      title: '模型调用失败，已使用本地兜底回答',
      body: '当前答案来自本地确定性检索摘要。请在设置页点击 Test 检查 AI Key、模型协议和模型地址。',
    };
  }
  if (status.endsWith(':no_citations') || result.citation_count === 0 || result.citations.length === 0) {
    return {
      level: 'info',
      title: '没有找到可引用的 AI Wiki 页面',
      body: 'Ask 只会基于已编译的 .ai/wiki 引用回答。请先编译 AI Wiki，或换一个更接近文档标题/关键词的问题后重试。',
    };
  }
  return null;
}

function parseFrontmatter(text: string): { fields: Record<string, string>; body: string } {
  if (!text.startsWith('---\n')) {
    return { fields: {}, body: text };
  }
  const endIndex = text.indexOf('\n---', 4);
  if (endIndex === -1) {
    return { fields: {}, body: text };
  }
  const fields: Record<string, string> = {};
  const frontmatter = text.slice(4, endIndex).split(/\r?\n/);
  for (const line of frontmatter) {
    const separatorIndex = line.indexOf(':');
    if (separatorIndex <= 0) {
      continue;
    }
    const key = line.slice(0, separatorIndex).trim();
    const value = line.slice(separatorIndex + 1).trim();
    if (key) {
      fields[key] = value;
    }
  }
  return { fields, body: text.slice(endIndex + 4).trim() };
}

function firstMeaningfulLine(text: string): string {
  for (const line of text.split(/\r?\n/)) {
    const normalized = line.trim();
    if (!normalized || normalized.startsWith('#') || normalized.startsWith('-') || normalized.startsWith('```')) {
      continue;
    }
    return normalized.slice(0, 180);
  }
  return 'No preview available.';
}

function fallbackTitle(path: string): string {
  const name = path.split('/').pop() || path;
  return name.replace(/\.(md|markdown)$/i, '');
}

async function fetchWorkspaceContent(fileId: string) {
  const response = await fetch(
    `${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/content`,
    { cache: 'no-store' },
  );
  if (!response.ok) {
    throw new Error(await responseError(response));
  }
  return parseWorkspaceFileContent(await response.json());
}

async function loadAiWikiPageSummary(file: WorkspaceFileEntry): Promise<AiWikiPageSummary> {
  const content = await fetchWorkspaceContent(file.file_id);
  const { fields, body } = parseFrontmatter(content.text);
  return {
    fileId: file.file_id,
    path: file.path,
    type: file.type,
    title: fields.title || fallbackTitle(file.path),
    pageType: fields.page_type || file.type,
    sourcePath: fields.source_path ?? null,
    sourceFileId: fields.source_file_id ?? null,
    sourceContentHash: fields.source_content_hash ?? null,
    lastCompiledAt: fields.last_ai_compiled_at ?? null,
    sourcesHash: fields.last_compiled_from_sources_hash ?? null,
    bodyPreview: firstMeaningfulLine(body),
  };
}

export default function AiWikiView({
  onOpenWorkspacePath,
}: {
  onOpenWorkspacePath: (path: string) => void;
}) {
  const [result, setResult] = useState<AiWikiCompileResult | null>(null);
  const [lastError, setLastError] = useState<string | null>(null);
  const [isCompiling, setIsCompiling] = useState(false);
  const [pageSummaries, setPageSummaries] = useState<AiWikiPageSummary[]>([]);
  const [selectedPage, setSelectedPage] = useState<AiWikiPageSummary | null>(null);
  const [isLoadingPages, setIsLoadingPages] = useState(false);
  const [pageError, setPageError] = useState<string | null>(null);
  const [question, setQuestion] = useState('');
  const [askResult, setAskResult] = useState<AiAskResult | null>(null);
  const [askError, setAskError] = useState<string | null>(null);
  const [isAsking, setIsAsking] = useState(false);
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
  const currentAskStatusNotice = askResult ? askStatusNotice(askResult) : null;

  useEffect(() => {
    let cancelled = false;
    if (aiFiles.length === 0) {
      setPageSummaries([]);
      setPageError(null);
      return;
    }
    setIsLoadingPages(true);
    Promise.all(aiFiles.map(loadAiWikiPageSummary))
      .then((summaries) => {
        if (cancelled) {
          return;
        }
        setPageSummaries(summaries);
        setSelectedPage((current) => {
          if (current && summaries.some((summary) => summary.fileId === current.fileId)) {
            return current;
          }
          return summaries[0] ?? null;
        });
        setPageError(null);
      })
      .catch((error) => {
        if (!cancelled) {
          setPageError(error instanceof Error ? error.message : String(error));
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsLoadingPages(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [aiFiles]);

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

  async function askAiWiki() {
    const normalizedQuestion = question.trim();
    if (!normalizedQuestion) {
      setAskError('请输入问题后再 Ask。');
      return;
    }
    setIsAsking(true);
    try {
      const response = await fetch(`${syncBridgeUrl}/api/ai/ask`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ question: normalizedQuestion, limit: 5 }),
      });
      if (!response.ok) {
        throw new Error(await responseError(response));
      }
      setAskResult(parseAskResult(await response.json()));
      setAskError(null);
    } catch (error) {
      setAskResult(null);
      setAskError(formatAskError(error instanceof Error ? error.message : String(error)));
    } finally {
      setIsAsking(false);
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

          <section className="overflow-hidden rounded-xl border border-[#0f3460] bg-[#16213e] shadow-lg shadow-black/20">
            <div className="border-b border-[#0f3460] p-4">
              <div className="flex items-start gap-3">
                <div className="rounded-lg border border-[#0f3460] bg-[#121316] p-3 text-[#a9c8fc]">
                  <MessageSquare size={20} />
                </div>
                <div>
                  <h2 className="text-lg font-bold text-[#e3e2e6]">Ask AI Wiki</h2>
                  <p className="mt-1 text-[12px] leading-relaxed text-slate-500">
                    Ask over `.ai/wiki` with citations. If `NOTEAPP_AI_API_KEY` and `NOTEAPP_AI_MODEL` are configured,
                    the desktop bridge uses an OpenAI-compatible model; otherwise it falls back to local deterministic
                    answers.
                  </p>
                </div>
              </div>
            </div>
            <div className="grid gap-4 p-4">
              <div className="flex flex-col gap-3 md:flex-row">
                <input
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter' && !event.shiftKey) {
                      event.preventDefault();
                      void askAiWiki();
                    }
                  }}
                  placeholder="Ask about your compiled AI Wiki..."
                  className="min-w-0 flex-1 rounded-lg border border-[#0f3460] bg-[#121316] px-4 py-2 text-[13px] text-[#e3e2e6] outline-none placeholder:text-slate-600 focus:border-[#a9c8fc]/50"
                />
                <button
                  onClick={() => void askAiWiki()}
                  disabled={isAsking}
                  className="inline-flex items-center justify-center gap-2 rounded-lg border border-[#0f3460] bg-[#0f3460]/30 px-4 py-2 text-[13px] font-semibold text-[#a9c8fc] transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <Send size={15} />
                  {isAsking ? 'Asking' : 'Ask'}
                </button>
              </div>

              {askError && (
                <div className="rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782]">
                  {askError}
                </div>
              )}

              {askResult && (
                <div className="grid gap-4">
                  <div className="rounded-lg border border-[#0f3460] bg-[#121316] p-4">
                    <div className="mb-3 flex flex-wrap items-center gap-2 text-[11px]">
                      <span className="rounded border border-[#0f3460] px-2 py-1 font-mono text-[#a9c8fc]">
                        {askResult.model_status}
                      </span>
                      <span className="font-mono text-slate-500">citations {askResult.citation_count}</span>
                    </div>
                    <pre className="whitespace-pre-wrap text-[13px] leading-6 text-slate-300">{askResult.answer}</pre>
                  </div>
                  {currentAskStatusNotice && (
                    <div className={`rounded-lg border p-4 text-[12px] ${
                      currentAskStatusNotice.level === 'warning'
                        ? 'border-[#ffb782]/30 bg-[#ffb782]/10 text-[#ffb782]'
                        : 'border-[#0f3460] bg-[#0f3460]/30 text-[#a9c8fc]'
                    }`}
                    >
                      <p className="font-semibold text-[#e3e2e6]">{currentAskStatusNotice.title}</p>
                      <p className="mt-1 leading-relaxed">{currentAskStatusNotice.body}</p>
                      {askResult.citation_count === 0 && (
                        <div className="mt-3 flex flex-wrap gap-2">
                          <button
                            onClick={() => void compileWiki()}
                            disabled={isCompiling}
                            className="inline-flex items-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 py-1.5 text-[12px] font-medium text-[#a9c8fc] transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <RefreshCw size={13} className={isCompiling ? 'animate-spin' : ''} />
                            编译 AI Wiki
                          </button>
                          <button
                            onClick={() => void refreshWorkspaceFiles()}
                            disabled={isWorkspaceRefreshing}
                            className="inline-flex items-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 py-1.5 text-[12px] font-medium text-slate-300 transition-colors hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
                          >
                            <RefreshCw size={13} className={isWorkspaceRefreshing ? 'animate-spin' : ''} />
                            刷新检索数据
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                  {askResult.citations.length > 0 && (
                    <div className="grid gap-2">
                      {askResult.citations.map((citation) => (
                        <button
                          key={citation.file_id}
                          onClick={() => {
                            const target = pageSummaries.find((page) => page.fileId === citation.file_id);
                            if (target) {
                              setSelectedPage(target);
                            }
                          }}
                          className="rounded-lg border border-[#0f3460] bg-[#121316] p-3 text-left transition-colors hover:bg-[#1f2b4a]"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <p className="font-semibold text-[#e3e2e6]">{citation.title}</p>
                            <span className="font-mono text-[10px] text-slate-500">score {citation.score}</span>
                          </div>
                          <p className="mt-1 truncate font-mono text-[11px] text-slate-500">{citation.path}</p>
                          <p className="mt-2 line-clamp-2 text-[12px] leading-5 text-slate-400">{citation.excerpt}</p>
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
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

          {pageError && (
            <div className="rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782]">
              {pageError}
            </div>
          )}

          {result && (
            <section className="overflow-hidden rounded-xl border border-[#0f3460] bg-[#16213e] shadow-lg shadow-black/20">
              <div className="border-b border-[#0f3460] p-4">
                <h2 className="text-lg font-bold text-[#e3e2e6]">最近一次编译</h2>
                <p className="mt-1 font-mono text-[11px] text-slate-500">
                  {result.generated_at} / sources {result.source_count} / pages {result.artifact_count}
                  {' '} / written {result.written_count} / skipped {result.skipped_count}
                </p>
                <span className="mt-2 inline-flex items-center gap-2 font-mono text-[12px] text-[#a9c8fc]">
                  {result.index_path}
                </span>
              </div>
              <div className="divide-y divide-[#0f3460]">
                {result.artifacts.map((artifact) => (
                  <button
                    key={artifact.path}
                    onClick={() => {
                      const target = pageSummaries.find((page) => page.path === artifact.path);
                      if (target) {
                        setSelectedPage(target);
                      }
                    }}
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
              {result.skipped.length > 0 && (
                <div className="border-t border-[#0f3460] bg-[#121316]/40 p-4">
                  <p className="text-[12px] font-semibold text-[#ffb782]">跳过页面</p>
                  <div className="mt-2 grid gap-2">
                    {result.skipped.map((item) => (
                      <div key={`${item.path}:${item.reason}`} className="rounded border border-[#0f3460] bg-[#121316] px-3 py-2">
                        <p className="truncate font-mono text-[11px] text-slate-300">{item.path}</p>
                        <p className="mt-1 text-[11px] text-slate-500">
                          {item.reason}
                          {item.source_path ? ` / ${item.source_path}` : ''}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </section>
          )}

          <section className="overflow-hidden rounded-xl border border-[#0f3460] bg-[#16213e] shadow-lg shadow-black/20">
            <div className="flex items-center justify-between gap-3 border-b border-[#0f3460] p-4">
              <div>
                <h2 className="text-lg font-bold text-[#e3e2e6]">已生成页面</h2>
                <p className="mt-1 text-[12px] text-slate-500">
                  当前 filemap 中的 AI Index / AI Wiki 文件，共 {aiFiles.length} 个。
                  {isLoadingPages ? ' 正在解析页面元数据...' : ''}
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
              {pageSummaries.length === 0 ? (
                <div className="p-8 text-center text-[13px] text-slate-500">
                  还没有生成 AI Wiki 页面。先点击上方“编译 AI Wiki”。
                </div>
              ) : (
                pageSummaries.map((page) => (
                  <button
                    key={page.fileId}
                    onClick={() => setSelectedPage(page)}
                    className="grid w-full grid-cols-1 gap-3 p-4 text-left transition-colors hover:bg-[#1f2b4a] md:grid-cols-12"
                  >
                    <div className="flex items-start gap-3 md:col-span-5">
                      <div className="flex h-10 w-10 flex-shrink-0 items-center justify-center rounded-lg border border-[#0f3460] bg-[#121316]">
                        <FileText size={18} className={page.type === 'ai_index' ? 'text-[#a9c8fc]' : 'text-slate-400'} />
                      </div>
                      <div className="min-w-0">
                        <p className="truncate text-[14px] font-medium text-[#e3e2e6]">{page.title}</p>
                        <p className="mt-1 truncate font-mono text-[11px] text-slate-500">{page.path}</p>
                        <p className="mt-2 line-clamp-2 text-[12px] leading-relaxed text-slate-400">{page.bodyPreview}</p>
                      </div>
                    </div>
                    <div className="flex min-w-0 flex-col gap-2 text-[12px] text-slate-400 md:col-span-5">
                      <span className="inline-flex w-fit items-center rounded border border-[#0f3460] bg-[#121316] px-2 py-1 font-mono text-[11px] text-[#a9c8fc]">
                        {page.pageType}
                      </span>
                      {page.sourcePath && <span className="truncate">来源：{page.sourcePath}</span>}
                      {page.lastCompiledAt && (
                        <span className="inline-flex items-center gap-1 truncate">
                          <Clock size={13} />
                          {page.lastCompiledAt}
                        </span>
                      )}
                      {(page.sourceContentHash || page.sourcesHash) && (
                        <span className="inline-flex items-center gap-1 truncate font-mono text-[11px]">
                          <Fingerprint size={13} />
                          {page.sourceContentHash || page.sourcesHash}
                        </span>
                      )}
                    </div>
                    <div className="flex items-center justify-end md:col-span-2">
                      <ExternalLink size={15} className="text-slate-500" />
                    </div>
                  </button>
                ))
              )}
            </div>
          </section>

          {selectedPage && (
            <section className="rounded-xl border border-[#0f3460] bg-[#16213e] p-6 shadow-lg shadow-black/20">
              <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
                <div className="min-w-0">
                  <p className="font-mono text-[11px] uppercase tracking-wider text-[#a9c8fc]">{selectedPage.pageType}</p>
                  <h2 className="mt-1 truncate text-2xl font-bold text-[#e3e2e6]">{selectedPage.title}</h2>
                  <p className="mt-2 truncate font-mono text-[12px] text-slate-500">{selectedPage.path}</p>
                </div>
                <button
                  onClick={() => onOpenWorkspacePath(selectedPage.path)}
                  className="inline-flex w-fit items-center gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:text-white"
                >
                  打开源码
                  <ExternalLink size={14} />
                </button>
              </div>
              <div className="mt-5 grid gap-3 text-[13px] text-slate-400 md:grid-cols-2">
                {selectedPage.sourcePath && <div>来源：{selectedPage.sourcePath}</div>}
                {selectedPage.lastCompiledAt && <div>编译时间：{selectedPage.lastCompiledAt}</div>}
                {selectedPage.sourceContentHash && (
                  <div className="truncate font-mono md:col-span-2">source hash：{selectedPage.sourceContentHash}</div>
                )}
                {selectedPage.sourcesHash && (
                  <div className="truncate font-mono md:col-span-2">sources hash：{selectedPage.sourcesHash}</div>
                )}
              </div>
              <div className="mt-5 rounded-lg border border-[#0f3460] bg-[#121316] p-4 text-[13px] leading-relaxed text-slate-300">
                {selectedPage.bodyPreview}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

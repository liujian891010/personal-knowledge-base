import React, { useMemo, useState } from 'react';
import { Bot, FileText, Loader2, Plus, Send, Sparkles, User, X } from 'lucide-react';

import type { AiContextDraft } from '../aiContext';
import { useWorkspaceFilesController } from '../useWorkspaceFiles';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

interface AiContextTaskSource {
  file_id: string;
  path: string;
  title: string;
  excerpt: string;
  included_chars: number;
  original_chars: number;
  truncated: boolean;
}

interface AiContextTaskResult {
  context_type: string;
  instruction: string;
  answer: string;
  source_count: number;
  sources: AiContextTaskSource[];
  model_status: string;
  truncation: {
    included_file_count: number;
    skipped_file_count: number;
    included_chars: number;
    truncated: boolean;
    note: string;
  };
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
      nodes.push(<code key={key} className="rounded bg-[#0b1020] px-1.5 py-0.5 font-mono text-[#ffb782]">{token.slice(1, -1)}</code>);
    } else if (token.startsWith('**') && token.endsWith('**')) {
      nodes.push(<strong key={key} className="font-bold text-[#f3f4f6]">{token.slice(2, -2)}</strong>);
    } else if (token.startsWith('*') && token.endsWith('*')) {
      nodes.push(<em key={key} className="italic text-slate-200">{token.slice(1, -1)}</em>);
    } else if (linkMatch) {
      nodes.push(
        <a key={key} href={linkMatch[2]} target="_blank" rel="noreferrer" className="text-[#a9c8fc] underline underline-offset-4">
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

function MarkdownPreview({ markdown }: { markdown: string }) {
  const lines = markdown.replace(/\r\n/g, '\n').split('\n');
  const nodes: React.ReactNode[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    const heading = /^(#{1,6})\s+(.+)$/.exec(line);
    if (heading) {
      const level = Math.min(heading[1].length, 6);
      const className = ['text-3xl', 'text-2xl', 'text-xl', 'text-lg', 'text-base', 'text-sm'][level - 1];
      nodes.push(React.createElement(`h${level}`, { key: index, className: `${className} font-bold text-[#f3f4f6]` }, renderInlineMarkdown(heading[2])));
      index += 1;
      continue;
    }
    if (/^```/.test(line)) {
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !/^```/.test(lines[index])) {
        codeLines.push(lines[index]);
        index += 1;
      }
      index += 1;
      nodes.push(<pre key={index} className="overflow-x-auto rounded-lg border border-[#0f3460] bg-[#0b1020] p-4 font-mono text-[12px] text-slate-200">{codeLines.join('\n')}</pre>);
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^\s*[-*+]\s+/.test(lines[index])) {
        items.push(lines[index].replace(/^\s*[-*+]\s+/, ''));
        index += 1;
      }
      nodes.push(<ul key={index} className="list-disc space-y-1 pl-6">{items.map((item, itemIndex) => <li key={itemIndex}>{renderInlineMarkdown(item)}</li>)}</ul>);
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !/^(#{1,6})\s+/.test(lines[index]) && !/^\s*[-*+]\s+/.test(lines[index]) && !/^```/.test(lines[index])) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    nodes.push(<p key={index} className="leading-7">{renderInlineMarkdown(paragraph.join(' '))}</p>);
  }
  return <div className="space-y-4 break-words text-[14px] leading-7 text-slate-300">{nodes}</div>;
}

interface PreviewState {
  fileId: string;
  title: string;
  path: string;
  text: string;
}

type ChatMessage =
  | { role: 'user'; content: string }
  | { role: 'assistant'; content: string; result: AiContextTaskResult };

function mergeContext(current: AiContextDraft | null, incoming: AiContextDraft): AiContextDraft {
  if (!current) {
    return incoming;
  }
  const fileIds = Array.from(new Set([...current.fileIds, ...incoming.fileIds]));
  return {
    type: 'selected_files',
    title: `已选择 ${fileIds.length} 个文档`,
    fileIds,
  };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function responseErrorMessage(response: Response): Promise<string> {
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

function parseAiContextTaskResult(payload: unknown): AiContextTaskResult {
  if (!isObject(payload) || !Array.isArray(payload.sources)) {
    throw new Error('AI context task response must include sources');
  }
  const truncation = isObject(payload.truncation) ? payload.truncation : {};
  return {
    context_type: String(payload.context_type ?? ''),
    instruction: String(payload.instruction ?? ''),
    answer: String(payload.answer ?? ''),
    source_count: Number(payload.source_count ?? payload.sources.length),
    model_status: String(payload.model_status ?? 'unknown'),
    sources: payload.sources.map((source) => {
      if (!isObject(source)) {
        throw new Error('AI context source must be an object');
      }
      return {
        file_id: String(source.file_id ?? ''),
        path: String(source.path ?? ''),
        title: String(source.title ?? ''),
        excerpt: String(source.excerpt ?? ''),
        included_chars: Number(source.included_chars ?? 0),
        original_chars: Number(source.original_chars ?? 0),
        truncated: Boolean(source.truncated),
      };
    }),
    truncation: {
      included_file_count: Number(truncation.included_file_count ?? 0),
      skipped_file_count: Number(truncation.skipped_file_count ?? 0),
      included_chars: Number(truncation.included_chars ?? 0),
      truncated: Boolean(truncation.truncated),
      note: String(truncation.note ?? ''),
    },
  };
}

function fileName(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path;
}

async function fetchWorkspaceText(fileId: string): Promise<string> {
  const response = await fetch(`${syncBridgeUrl}/api/workspace/files/${encodeURIComponent(fileId)}/content`, {
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response));
  }
  const payload: unknown = await response.json();
  if (!isObject(payload) || typeof payload.text !== 'string') {
    throw new Error('workspace file content response must include text');
  }
  return payload.text;
}

export default function AiChatView({
  initialContext,
  onOpenExplorer,
  onClearInitialContext,
}: {
  initialContext: AiContextDraft | null;
  onOpenExplorer: (fileIds: string[]) => void;
  onClearInitialContext: () => void;
}) {
  const { files } = useWorkspaceFilesController();
  const [context, setContext] = useState<AiContextDraft | null>(() => initialContext);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);

  React.useEffect(() => {
    if (!initialContext) {
      return;
    }
    setContext((current) => mergeContext(current, initialContext));
    onClearInitialContext();
  }, [initialContext, onClearInitialContext]);

  const contextFiles = useMemo(
    () => (context ? files.filter((file) => context.fileIds.includes(file.file_id)) : []),
    [context, files],
  );

  function removeContextFile(fileId: string) {
    setContext((current) => {
      if (!current) {
        return current;
      }
      const fileIds = current.fileIds.filter((item) => item !== fileId);
      if (fileIds.length === 0) {
        return null;
      }
      if (current.type === 'folder') {
        return {
          type: 'selected_files',
          title: `已选择 ${fileIds.length} 个文档`,
          fileIds,
        };
      }
      return {
        ...current,
        title: `已选择 ${fileIds.length} 个文档`,
        fileIds,
      };
    });
  }

  async function openPreview(fileId: string, title: string, path: string) {
    setPreview({
      fileId,
      title,
      path,
      text: '',
    });
    setIsPreviewLoading(true);
    try {
      const text = await fetchWorkspaceText(fileId);
      setPreview({
        fileId,
        title,
        path,
        text,
      });
    } catch (nextError) {
      setPreview({
        fileId,
        title,
        path,
        text: nextError instanceof Error ? nextError.message : String(nextError),
      });
    } finally {
      setIsPreviewLoading(false);
    }
  }

  async function sendMessage() {
    if (!context) {
      setError('请先从笔记库选择文件夹或文档加入 AI 上下文。');
      return;
    }
    const instruction = input.trim();
    if (!instruction) {
      setError('请输入要让 AI 执行的指令。');
      return;
    }
    setMessages((current) => [...current, { role: 'user', content: instruction }]);
    setInput('');
    setIsRunning(true);
    try {
      const response = await fetch(`${syncBridgeUrl}/api/ai/context-task`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          context: context.type === 'folder'
            ? {
              type: 'folder',
              folder_path: context.folderPath,
              recursive: true,
            }
            : {
              type: 'selected_files',
              file_ids: context.fileIds,
            },
          instruction,
          output: {
            mode: 'preview',
          },
        }),
      });
      if (!response.ok) {
        throw new Error(await responseErrorMessage(response));
      }
      const result = parseAiContextTaskResult(await response.json());
      setMessages((current) => [...current, { role: 'assistant', content: result.answer, result }]);
      setError(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setIsRunning(false);
    }
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 overflow-hidden bg-[#1a1a2e] lg:grid-cols-[320px_1fr]">
      <aside className="min-h-0 border-r border-[#0f3460] bg-[#16213e] p-4">
        <div className="flex items-center gap-2 text-[#a9c8fc]">
          <Sparkles size={18} />
          <h2 className="text-lg font-bold text-[#e3e2e6]">AI 文档上下文</h2>
        </div>
        <p className="mt-2 text-[12px] leading-5 text-slate-500">
          从笔记库把文件夹或多个 Markdown 加入上下文，然后在这里持续追问、改写、提炼或生成文档。
        </p>
        <div className="mt-4 rounded-xl border border-[#0f3460] bg-[#121316] p-3">
          {context ? (
            <>
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="truncate text-[13px] font-semibold text-[#e3e2e6]" title={context.title}>
                    {context.title}
                  </p>
                  <p className="mt-1 font-mono text-[10px] text-slate-500">{context.fileIds.length} 个候选文档</p>
                </div>
                <button
                  onClick={() => setContext(null)}
                  className="rounded border border-[#0f3460] p-1 text-slate-400 hover:text-white"
                  title="清空上下文"
                >
                  <X size={13} />
                </button>
              </div>
              <div className="mt-3 grid max-h-[46vh] gap-2 overflow-y-auto overflow-x-hidden pr-1">
                {contextFiles.map((file) => (
                  <div key={file.file_id} className="rounded border border-[#0f3460] bg-[#16213e] px-3 py-2">
                    <div className="grid grid-cols-[minmax(0,1fr)_24px] items-start gap-2">
                      <button
                        onClick={() => void openPreview(file.file_id, fileName(file.path), file.path)}
                        className="flex min-w-0 items-center gap-2 text-left text-[12px] text-[#e3e2e6] hover:text-white"
                        title={file.path}
                      >
                        <FileText size={13} className="flex-shrink-0 text-[#a9c8fc]" />
                        <span className="min-w-0 truncate">{fileName(file.path)}</span>
                      </button>
                      <button
                        onClick={() => removeContextFile(file.file_id)}
                        className="flex h-6 w-6 flex-shrink-0 items-center justify-center rounded border border-[#0f3460] text-slate-500 hover:text-white"
                        title="从 AI 上下文移除此文档"
                      >
                        <X size={11} />
                      </button>
                    </div>
                    <p className="mt-1 break-all font-mono text-[10px] leading-4 text-slate-500">{file.path}</p>
                  </div>
                ))}
              </div>
            <button
              onClick={() => onOpenExplorer(context.fileIds)}
              className="mt-3 flex w-full items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-3 py-2 text-[12px] font-semibold text-[#a9c8fc] hover:text-white"
            >
                <Plus size={14} />
                继续添加文档
              </button>
            </>
          ) : (
            <button
              onClick={() => onOpenExplorer([])}
              className="flex w-full items-center justify-center gap-2 rounded border border-[#0f3460] bg-[#0f3460]/30 px-3 py-2 text-[12px] font-semibold text-[#a9c8fc] hover:text-white"
            >
              <Plus size={14} />
              去笔记库选择上下文
            </button>
          )}
        </div>
      </aside>

      <main className="flex min-h-0 flex-col bg-[#121316]">
        <div className="flex-1 overflow-y-auto p-5 md:p-8">
          {messages.length === 0 ? (
            <div className="mx-auto flex h-full max-w-3xl flex-col items-center justify-center text-center">
              <div className="rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 text-[#a9c8fc]">
                <Bot size={36} />
              </div>
              <h1 className="mt-5 text-2xl font-black text-[#e3e2e6]">基于文档持续交流</h1>
            </div>
          ) : (
            <div className="mx-auto grid max-w-4xl gap-5">
              {messages.map((message, index) => (
                <div
                  key={index}
                  className={`flex gap-3 ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  {message.role === 'assistant' && (
                    <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-[#0f3460] bg-[#16213e] text-[#a9c8fc]">
                      <Bot size={17} />
                    </div>
                  )}
                  <div className={`max-w-[82%] rounded-2xl border p-4 ${
                    message.role === 'user'
                      ? 'border-[#0f3460] bg-[#0f3460]/40 text-[#e3e2e6]'
                      : 'border-[#0f3460] bg-[#16213e] text-slate-300'
                  }`}
                  >
                    <pre className="whitespace-pre-wrap font-sans text-[13px] leading-6">{message.content}</pre>
                    {message.role === 'assistant' && (
                      <div className="mt-4 border-t border-[#0f3460] pt-3">
                        <div className="mb-2 flex flex-wrap items-center gap-2 text-[11px]">
                          <span className="rounded border border-[#0f3460] bg-[#121316] px-2 py-1 font-mono text-[#a9c8fc]">
                            {message.result.model_status}
                          </span>
                          <span className="font-mono text-slate-500">sources {message.result.source_count}</span>
                          {message.result.truncation.truncated && (
                            <span className="rounded border border-[#ffb782]/30 bg-[#ffb782]/10 px-2 py-1 text-[#ffb782]">
                              部分内容已截断
                            </span>
                          )}
                        </div>
                        <div className="grid gap-2">
                          {message.result.sources.map((source) => (
                            <button
                              key={source.file_id}
                              onClick={() => void openPreview(source.file_id, source.title, source.path)}
                              className="rounded border border-[#0f3460] bg-[#121316] px-3 py-2 text-left hover:bg-[#1f2b4a]"
                            >
                              <p className="truncate text-[12px] font-semibold text-[#e3e2e6]">{source.title}</p>
                              <p className="mt-1 truncate font-mono text-[10px] text-slate-500">{source.path}</p>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                  {message.role === 'user' && (
                    <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-[#0f3460] bg-[#16213e] text-slate-300">
                      <User size={17} />
                    </div>
                  )}
                </div>
              ))}
              {isRunning && (
                <div className="flex justify-start gap-3">
                  <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-[#0f3460] bg-[#16213e] text-[#a9c8fc]">
                    <Bot size={17} />
                  </div>
                  <div className="max-w-[82%] rounded-2xl border border-[#0f3460] bg-[#16213e] p-4 text-slate-300">
                    <div className="flex items-center gap-2 text-[13px] text-slate-400">
                      <Loader2 size={15} className="animate-spin text-[#a9c8fc]" />
                      AI 正在基于当前文档上下文生成回答...
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
        <div className="border-t border-[#0f3460] bg-[#16213e] p-4">
          {error && (
            <div className="mb-3 w-full rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782]">
              {error}
            </div>
          )}
          <div className="flex w-full min-w-0 flex-col gap-3 sm:flex-row">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void sendMessage();
                }
              }}
              className="h-20 min-w-0 flex-1 resize-none rounded-xl border border-[#0f3460] bg-[#121316] p-3 text-[13px] leading-5 text-[#e3e2e6] outline-none placeholder:text-slate-600 focus:border-[#a9c8fc]/60"
              placeholder="请输入问题或指令，例如：写报告、提炼风险、生成行动项、改写为汇报口径..."
            />
            <button
              onClick={() => void sendMessage()}
              disabled={isRunning}
              className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-[#0f3460] bg-[#0f3460]/40 text-[13px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50 sm:h-auto sm:w-28 sm:flex-shrink-0"
            >
              <Send size={15} />
              {isRunning ? '生成中' : '发送'}
            </button>
          </div>
        </div>
      </main>
      {preview && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="flex h-[min(760px,86vh)] w-[min(920px,94vw)] flex-col overflow-hidden rounded-2xl border border-[#0f3460] bg-[#16213e] shadow-2xl shadow-black/50">
            <div className="flex items-start justify-between gap-3 border-b border-[#0f3460] p-4">
              <div className="min-w-0">
                <h3 className="truncate text-lg font-bold text-[#e3e2e6]">{preview.title}</h3>
                <p className="mt-1 break-all font-mono text-[11px] text-slate-500">{preview.path}</p>
              </div>
              <button
                onClick={() => setPreview(null)}
                className="rounded border border-[#0f3460] bg-[#121316] p-2 text-slate-400 hover:text-white"
                title="关闭预览"
              >
                <X size={16} />
              </button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden bg-[#121316] p-5">
              {isPreviewLoading ? (
                <div className="flex h-full items-center justify-center gap-2 text-[13px] text-slate-400">
                  <Loader2 size={16} className="animate-spin text-[#a9c8fc]" />
                  正在加载文档...
                </div>
              ) : (
                <MarkdownPreview markdown={preview.text} />
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

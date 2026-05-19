import React, { useMemo, useState } from 'react';
import { Bot, FileText, Loader2, Plus, Send, Sparkles, Trash2, User, X } from 'lucide-react';

import type { AiContextDraft } from '../aiContext';
import {
  createAiChatSession,
  deleteAiChatSession,
  listAiChatSessions,
  readAiChatSession,
  saveAiChatSession,
  type AiChatMessage,
  type AiChatProviderDiagnostic,
  type AiChatSession,
  type AiChatSessionSummary,
  type AiChatTaskResult,
} from '../aiChatSessions';
import { syncBridgeUrl } from '../syncBridgeConfig';
import { useWorkspaceFilesController } from '../useWorkspaceFiles';

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

function isMarkdownTableDelimiter(line: string): boolean {
  const trimmed = line.trim();
  if (!trimmed.includes('-')) {
    return false;
  }
  const normalized = trimmed.replace(/^\|/, '').replace(/\|$/, '');
  const cells = normalized.split('|').map((cell) => cell.trim());
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function splitMarkdownTableRow(line: string): string[] {
  const normalized = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return normalized.split('|').map((cell) => cell.trim());
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
    if (
      index + 1 < lines.length
      && lines[index].includes('|')
      && isMarkdownTableDelimiter(lines[index + 1])
    ) {
      const headers = splitMarkdownTableRow(lines[index]);
      const alignments = splitMarkdownTableRow(lines[index + 1]).map((cell) => {
        if (cell.startsWith(':') && cell.endsWith(':')) {
          return 'center';
        }
        if (cell.endsWith(':')) {
          return 'right';
        }
        return 'left';
      });
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && lines[index].trim() && lines[index].includes('|')) {
        rows.push(splitMarkdownTableRow(lines[index]));
        index += 1;
      }
      nodes.push(
        <div key={`table-${index}`} className="overflow-x-auto rounded-lg border border-[#0f3460]">
          <table className="min-w-full border-collapse text-left text-[13px] leading-6">
            <thead className="bg-[#0f3460]/35 text-[#f3f4f6]">
              <tr>
                {headers.map((header, headerIndex) => (
                  <th
                    key={headerIndex}
                    className="border-b border-[#0f3460] px-3 py-2 font-semibold"
                    style={{ textAlign: alignments[headerIndex] ?? 'left' }}
                  >
                    {renderInlineMarkdown(header)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, rowIndex) => (
                <tr key={rowIndex} className="border-t border-[#0f3460]/70">
                  {headers.map((_, cellIndex) => (
                    <td
                      key={cellIndex}
                      className="px-3 py-2 align-top text-slate-300"
                      style={{ textAlign: alignments[cellIndex] ?? 'left' }}
                    >
                      {renderInlineMarkdown(row[cellIndex] ?? '')}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    const paragraph: string[] = [];
    while (
      index < lines.length
      && lines[index].trim()
      && !/^(#{1,6})\s+/.test(lines[index])
      && !/^\s*[-*+]\s+/.test(lines[index])
      && !/^```/.test(lines[index])
      && !(index + 1 < lines.length && lines[index].includes('|') && isMarkdownTableDelimiter(lines[index + 1]))
    ) {
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

async function responseErrorDiagnostic(response: Response, requestId: string): Promise<AiChatProviderDiagnostic> {
  try {
    const payload: unknown = await response.json();
    if (isObject(payload) && typeof payload.message === 'string') {
      const code = typeof payload.code === 'string' ? `${payload.code}: ` : '';
      return {
        requestId,
        statusCode: response.status,
        summary: `${code}${payload.message}`,
      };
    }
  } catch {
    // Fall through to status text.
  }
  return {
    requestId,
    statusCode: response.status,
    summary: response.statusText || `HTTP ${response.status}`,
  };
}

function parseAiContextTaskResult(payload: unknown): AiChatTaskResult {
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

function createMessageId(): string {
  return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function createRequestId(): string {
  return `ai_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

function defaultSessionTitle(messages: AiChatMessage[]): string {
  const firstUserMessage = messages.find((message) => message.role === 'user');
  if (!firstUserMessage) {
    return '新的 AI 文档会话';
  }
  return firstUserMessage.content.replace(/\s+/g, ' ').trim().slice(0, 28) || '新的 AI 文档会话';
}

function formatSessionTime(ms: number): string {
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ms));
}

function sessionSummaryFromSession(session: AiChatSession): AiChatSessionSummary {
  return {
    id: session.id,
    title: session.title,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    messageCount: session.messages.length,
    contextFileCount: session.context?.fileIds.length ?? 0,
  };
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
  onOpenWorkspacePath,
  onClearInitialContext,
}: {
  initialContext: AiContextDraft | null;
  onOpenExplorer: (fileIds: string[]) => void;
  onOpenWorkspacePath: (path: string) => void;
  onClearInitialContext: () => void;
}) {
  const { files } = useWorkspaceFilesController();
  const [sessions, setSessions] = useState<AiChatSessionSummary[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [sessionTitle, setSessionTitle] = useState('新的 AI 文档会话');
  const [context, setContext] = useState<AiContextDraft | null>(null);
  const [messages, setMessages] = useState<AiChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isRunning, setIsRunning] = useState(false);
  const [isSessionLoading, setIsSessionLoading] = useState(true);
  const [isSessionSaving, setIsSessionSaving] = useState(false);
  const [sessionToDelete, setSessionToDelete] = useState<AiChatSessionSummary | null>(null);
  const [preview, setPreview] = useState<PreviewState | null>(null);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const skipNextAutoSaveRef = React.useRef(false);
  const messageListRef = React.useRef<HTMLDivElement | null>(null);
  const messageListBottomRef = React.useRef<HTMLDivElement | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    async function loadInitialSession() {
      setIsSessionLoading(true);
      try {
        const nextSessions = await listAiChatSessions();
        if (cancelled) {
          return;
        }
        setSessions(nextSessions);
        if (nextSessions[0]) {
          const session = await readAiChatSession(nextSessions[0].id);
          if (cancelled) {
            return;
          }
          skipNextAutoSaveRef.current = true;
          setActiveSessionId(session.id);
          setSessionTitle(session.title);
          setContext(session.context ?? null);
          setMessages(session.messages ?? []);
        } else {
          skipNextAutoSaveRef.current = true;
          setActiveSessionId(null);
          setSessionTitle('新的 AI 文档会话');
          setContext(null);
          setMessages([]);
        }
      } catch (nextError) {
        if (!cancelled) {
          setError(nextError instanceof Error ? nextError.message : String(nextError));
        }
      } finally {
        if (!cancelled) {
          setIsSessionLoading(false);
        }
      }
    }
    void loadInitialSession();
    return () => {
      cancelled = true;
    };
  }, []);

  React.useEffect(() => {
    if (!initialContext || isSessionLoading) {
      return;
    }
    skipNextAutoSaveRef.current = true;
    setActiveSessionId(null);
    setSessionTitle('新的 AI 文档会话');
    setMessages([]);
    setContext(initialContext);
    const initialInstruction = initialContext.initialInstruction?.trim() ?? '';
    setInput(initialInstruction);
    setError(null);
    onClearInitialContext();
    if (initialContext.autoRun && initialInstruction) {
      void sendMessageWithContext(initialContext, initialInstruction, { forceNewSession: true });
    }
  }, [initialContext, isSessionLoading, onClearInitialContext]);

  React.useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      if (messageListBottomRef.current) {
        messageListBottomRef.current.scrollIntoView({ block: 'end' });
        return;
      }
      if (messageListRef.current) {
        messageListRef.current.scrollTop = messageListRef.current.scrollHeight;
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [messages, isRunning, activeSessionId]);

  React.useEffect(() => {
    if (!activeSessionId || isSessionLoading) {
      return;
    }
    if (skipNextAutoSaveRef.current) {
      skipNextAutoSaveRef.current = false;
      return;
    }
    const nextTitle = sessionTitle === '新的 AI 文档会话' ? defaultSessionTitle(messages) : sessionTitle;
    const session: AiChatSession = {
      schemaVersion: 'v1',
      id: activeSessionId,
      title: nextTitle,
      createdAt: Date.now(),
      updatedAt: Date.now(),
      context,
      messages,
    };
    const timer = window.setTimeout(() => {
      setIsSessionSaving(true);
      saveAiChatSession(session)
        .then((savedSession) => {
          setSessionTitle(savedSession.title);
          setSessions((current) => {
            const summary = sessionSummaryFromSession(savedSession);
            return [summary, ...current.filter((item) => item.id !== savedSession.id)]
              .sort((left, right) => right.updatedAt - left.updatedAt);
          });
          setError(null);
        })
        .catch((nextError) => {
          setError(nextError instanceof Error ? nextError.message : String(nextError));
        })
        .finally(() => setIsSessionSaving(false));
    }, 500);
    return () => window.clearTimeout(timer);
  }, [activeSessionId, context, isSessionLoading, messages, sessionTitle]);

  async function refreshSessions() {
    setSessions(await listAiChatSessions());
  }

  async function openSession(sessionId: string) {
    if (sessionId === activeSessionId) {
      return;
    }
    setIsSessionLoading(true);
    try {
      const session = await readAiChatSession(sessionId);
      skipNextAutoSaveRef.current = true;
      setActiveSessionId(session.id);
      setSessionTitle(session.title);
      setContext(session.context ?? null);
      setMessages(session.messages ?? []);
      setError(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setIsSessionLoading(false);
    }
  }

  async function confirmDeleteSession() {
    if (!sessionToDelete) {
      return;
    }
    setIsSessionLoading(true);
    try {
      const nextSessions = await deleteAiChatSession(sessionToDelete.id);
      setSessions(nextSessions);
      if (sessionToDelete.id === activeSessionId) {
        if (nextSessions[0]) {
          const nextSession = await readAiChatSession(nextSessions[0].id);
          skipNextAutoSaveRef.current = true;
          setActiveSessionId(nextSession.id);
          setSessionTitle(nextSession.title);
          setContext(nextSession.context ?? null);
          setMessages(nextSession.messages ?? []);
        } else {
          skipNextAutoSaveRef.current = true;
          setActiveSessionId(null);
          setSessionTitle('新的 AI 文档会话');
          setContext(null);
          setMessages([]);
        }
      }
      setSessionToDelete(null);
      setError(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setIsSessionLoading(false);
    }
  }

  const contextFiles = useMemo(
    () => (context ? files.filter((file) => context.fileIds.includes(file.file_id)) : []),
    [context, files],
  );
  const hasPendingAssistant = messages.some((message) => message.role === 'assistant' && message.pending);

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

  function updateAssistantMessage(messageId: string, patch: Partial<Extract<AiChatMessage, { role: 'assistant' }>>) {
    setMessages((current) => current.map((message) => (
      message.id === messageId && message.role === 'assistant'
        ? { ...message, ...patch }
        : message
    )));
  }

  async function streamAssistantMessage(
    messageId: string,
    result: AiChatTaskResult,
    requestId: string,
    requestedAt: number,
  ) {
    const answer = result.answer || 'AI provider returned an empty answer.';
    const firstTokenMs = Date.now() - requestedAt;
    const step = Math.max(8, Math.ceil(answer.length / 72));
    updateAssistantMessage(messageId, {
      content: answer.slice(0, step),
      diagnostic: {
        requestId,
        statusCode: 200,
        firstTokenMs,
        summary: result.model_status,
      },
    });
    for (let index = step * 2; index < answer.length; index += step) {
      await new Promise((resolve) => window.setTimeout(resolve, 18));
      updateAssistantMessage(messageId, { content: answer.slice(0, index) });
    }
    await new Promise((resolve) => window.setTimeout(resolve, 18));
    updateAssistantMessage(messageId, {
      content: answer,
      result,
      pending: false,
      diagnostic: {
        requestId,
        statusCode: 200,
        firstTokenMs,
        summary: result.model_status,
      },
    });
  }

  async function sendMessageWithContext(
    activeContext: AiContextDraft,
    rawInstruction: string,
    options: { forceNewSession?: boolean } = {},
  ) {
    const instruction = rawInstruction.trim();
    if (!instruction) {
      setError('请输入要让 AI 执行的指令。');
      return;
    }
    const userMessage: AiChatMessage = {
      id: createMessageId(),
      role: 'user',
      content: instruction,
      createdAt: Date.now(),
    };
    const requestId = createRequestId();
    const assistantMessageId = createMessageId();
    const pendingAssistantMessage: AiChatMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      createdAt: Date.now(),
      pending: true,
      diagnostic: {
        requestId,
        summary: 'waiting_for_first_token',
      },
    };
    setInput('');
    setIsRunning(true);
    const requestedAt = Date.now();
    let errorAlreadyRendered = false;
    try {
      let resolvedSessionId = options.forceNewSession ? null : activeSessionId;
      if (!resolvedSessionId) {
        const session = await createAiChatSession({
          title: defaultSessionTitle([userMessage]),
          context: activeContext,
          messages: [userMessage],
        });
        skipNextAutoSaveRef.current = true;
        resolvedSessionId = session.id;
        setActiveSessionId(session.id);
        setSessionTitle(session.title);
        setContext(session.context ?? activeContext);
        setMessages([...(session.messages ?? [userMessage]), pendingAssistantMessage]);
        setSessions((current) => [sessionSummaryFromSession(session), ...current.filter((item) => item.id !== session.id)]);
      } else {
        setMessages((current) => [...current, userMessage, pendingAssistantMessage]);
      }
      const response = await fetch(`${syncBridgeUrl}/api/ai/context-task`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-Noteapp-Request-Id': requestId,
        },
        body: JSON.stringify({
          context: activeContext.type === 'folder'
            ? {
              type: 'folder',
              folder_path: activeContext.folderPath,
              recursive: true,
            }
            : {
              type: 'selected_files',
              file_ids: activeContext.fileIds,
            },
          instruction,
          output: {
            mode: 'preview',
          },
        }),
      });
      if (!response.ok) {
        const diagnostic = await responseErrorDiagnostic(response, requestId);
        errorAlreadyRendered = true;
        updateAssistantMessage(assistantMessageId, {
          content: `AI 请求失败：${diagnostic.summary}`,
          pending: false,
          diagnostic,
        });
        throw new Error(`AI 请求失败（HTTP ${diagnostic.statusCode ?? 'unknown'}，Request ${requestId}）：${diagnostic.summary}`);
      }
      const result = parseAiContextTaskResult(await response.json());
      await streamAssistantMessage(assistantMessageId, result, requestId, requestedAt);
      setError(null);
    } catch (nextError) {
      const summary = nextError instanceof Error ? nextError.message : String(nextError);
      if (!errorAlreadyRendered) {
        updateAssistantMessage(assistantMessageId, {
          content: `AI 请求失败：${summary}`,
          pending: false,
          diagnostic: {
            requestId,
            summary,
          },
        });
      }
      setError(summary);
    } finally {
      setIsRunning(false);
    }
  }

  async function sendMessage() {
    if (!context) {
      setError('请先从笔记库选择文件夹或文档加入 AI 上下文。');
      return;
    }
    await sendMessageWithContext(context, input);
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
          <div className="mb-3 flex items-center justify-between gap-2">
            <p className="text-[12px] font-bold text-[#e3e2e6]">会话</p>
          </div>
          <div className="grid max-h-[22vh] gap-2 overflow-y-auto overflow-x-hidden pr-1">
            {isSessionLoading && sessions.length === 0 ? (
              <div className="flex items-center gap-2 rounded border border-[#0f3460] bg-[#16213e] px-3 py-2 text-[12px] text-slate-400">
                <Loader2 size={13} className="animate-spin text-[#a9c8fc]" />
                正在加载会话...
              </div>
            ) : sessions.length === 0 ? (
              <p className="rounded border border-[#0f3460] bg-[#16213e] px-3 py-2 text-[12px] text-slate-500">
                暂无会话
              </p>
            ) : sessions.map((session) => (
              <div
                key={session.id}
                className={`grid grid-cols-[minmax(0,1fr)_26px] items-center gap-2 rounded border px-2 py-2 ${
                  session.id === activeSessionId
                    ? 'border-[#e94560]/50 bg-[#0f3460]/40'
                    : 'border-[#0f3460] bg-[#16213e]'
                }`}
              >
                <button
                  onClick={() => void openSession(session.id)}
                  className="min-w-0 text-left"
                  title={session.title}
                >
                  <p className="truncate text-[12px] font-semibold text-[#e3e2e6]">{session.title}</p>
                  <p className="mt-1 truncate font-mono text-[10px] text-slate-500">
                    {formatSessionTime(session.updatedAt)} · {session.messageCount} 条
                  </p>
                </button>
                <button
                  onClick={() => setSessionToDelete(session)}
                  className="flex h-6 w-6 items-center justify-center rounded border border-[#0f3460] text-slate-500 hover:text-[#ffb782]"
                  title="删除会话"
                >
                  <Trash2 size={11} />
                </button>
              </div>
            ))}
          </div>
          <p className="mt-2 text-[10px] text-slate-600">
            {isSessionSaving ? '正在保存...' : '会话保存在当前工作区 .noteapp/ai-chats'}
          </p>
        </div>
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
        <div ref={messageListRef} className="flex-1 overflow-y-auto p-5 md:p-8">
          {!activeSessionId && !context ? (
            <div className="mx-auto flex h-full max-w-3xl flex-col items-center justify-center text-center">
              <div className="rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 text-[#a9c8fc]">
                <Bot size={36} />
              </div>
              <h1 className="mt-5 text-2xl font-black text-[#e3e2e6]">暂无会话</h1>
              <p className="mt-3 max-w-xl text-[14px] leading-7 text-slate-400">
                这里不会预先创建空会话。输入第一条问题并发送后，会自动创建新的 AI 会话。
              </p>
            </div>
          ) : messages.length === 0 ? (
            <div className="mx-auto flex h-full max-w-3xl flex-col items-center justify-center text-center">
              <div className="rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 text-[#a9c8fc]">
                <Bot size={36} />
              </div>
              <h1 className="mt-5 text-2xl font-black text-[#e3e2e6]">
                {context ? '基于新上下文开始提问' : '基于文档持续交流'}
              </h1>
            </div>
          ) : (
            <div className="mx-auto grid w-full max-w-7xl gap-5">
              {messages.map((message) => (
                <div
                  key={message.id}
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
                    {message.role === 'assistant' ? (
                      <>
                        {message.content ? (
                          <MarkdownPreview markdown={message.content} />
                        ) : (
                          <div className="flex items-center gap-2 text-[13px] text-slate-400">
                            <Loader2 size={15} className="animate-spin text-[#a9c8fc]" />
                            等待首 token...
                          </div>
                        )}
                        {message.pending && message.content && (
                          <div className="mt-3 flex items-center gap-2 text-[12px] text-slate-500">
                            <Loader2 size={13} className="animate-spin text-[#a9c8fc]" />
                            正在流式输出...
                          </div>
                        )}
                        {message.diagnostic && (
                          <div className="mt-3 flex flex-wrap gap-2 border-t border-[#0f3460] pt-3 font-mono text-[10px] text-slate-500">
                            <span>request {message.diagnostic.requestId}</span>
                            {typeof message.diagnostic.statusCode === 'number' && <span>HTTP {message.diagnostic.statusCode}</span>}
                            {typeof message.diagnostic.firstTokenMs === 'number' && <span>first-token {message.diagnostic.firstTokenMs}ms</span>}
                            <span>{message.diagnostic.summary}</span>
                          </div>
                        )}
                        {message.result && message.result.sources.length > 0 && (
                          <div className="mt-4 border-t border-[#0f3460] pt-3">
                            <div className="mb-2 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
                              来源
                            </div>
                            <div className="grid gap-2">
                              {message.result.sources.map((source) => (
                                <button
                                  key={`${message.id}-${source.file_id}-${source.path}`}
                                  type="button"
                                  onClick={() => onOpenWorkspacePath(source.path)}
                                  className="flex min-w-0 items-start gap-2 rounded border border-[#0f3460] bg-[#121316] px-3 py-2 text-left hover:border-[#a9c8fc]/60"
                                  title={source.path}
                                >
                                  <FileText size={13} className="mt-0.5 flex-shrink-0 text-[#a9c8fc]" />
                                  <span className="min-w-0">
                                    <span className="block truncate text-[12px] font-semibold text-[#e3e2e6]">
                                      {source.title}
                                    </span>
                                    <span className="mt-1 block truncate font-mono text-[10px] text-slate-500">
                                      {source.path}
                                    </span>
                                  </span>
                                </button>
                              ))}
                            </div>
                          </div>
                        )}
                      </>
                    ) : (
                      <pre className="whitespace-pre-wrap font-sans text-[13px] leading-6">{message.content}</pre>
                    )}
                  </div>
                  {message.role === 'user' && (
                    <div className="flex h-9 w-9 flex-shrink-0 items-center justify-center rounded-lg border border-[#0f3460] bg-[#16213e] text-slate-300">
                      <User size={17} />
                    </div>
                  )}
                </div>
              ))}
              {isRunning && !hasPendingAssistant && (
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
              <div ref={messageListBottomRef} aria-hidden="true" />
            </div>
          )}
        </div>
        <div className="border-t border-[#0f3460] bg-[#16213e] p-4">
          <div className="mx-auto w-full max-w-7xl">
            {error && (
              <div className="mb-3 w-full rounded-lg border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] text-[#ffb782]">
              {error}
            </div>
            )}
            <div className="flex w-full min-w-0 flex-col gap-3 sm:flex-row">
            <textarea
              value={input}
              disabled={isSessionLoading}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  void sendMessage();
                }
              }}
              className="h-20 min-w-0 flex-1 resize-none rounded-xl border border-[#0f3460] bg-[#121316] p-3 text-[13px] leading-5 text-[#e3e2e6] outline-none placeholder:text-slate-600 focus:border-[#a9c8fc]/60 disabled:opacity-60"
              placeholder={context
                ? '请输入问题或指令，例如：写报告、提炼风险、生成行动项、改写为汇报口径...'
                : '请输入问题；如需基于文档回答，请先从笔记库选择文件夹或文档加入 AI 上下文'}
            />
            <button
              onClick={() => void sendMessage()}
              disabled={isRunning || isSessionLoading || !context}
              className="inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-[#0f3460] bg-[#0f3460]/40 text-[13px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50 sm:h-auto sm:w-28 sm:flex-shrink-0"
            >
              <Send size={15} />
              {isRunning ? '生成中' : '发送'}
            </button>
            </div>
          </div>
        </div>
      </main>
      {sessionToDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 shadow-2xl shadow-black/50">
            <h3 className="text-lg font-bold text-[#e3e2e6]">删除会话</h3>
            <p className="mt-3 text-[13px] leading-6 text-slate-400">
              将删除本地 AI 文档会话：{sessionToDelete.title}
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <button
                onClick={() => setSessionToDelete(null)}
                className="rounded border border-[#0f3460] bg-[#121316] px-4 py-2 text-[13px] text-slate-300 hover:text-white"
              >
                取消
              </button>
              <button
                onClick={() => void confirmDeleteSession()}
                disabled={isSessionLoading}
                className="rounded border border-[#e94560]/40 bg-[#e94560]/20 px-4 py-2 text-[13px] font-semibold text-[#ffb3c0] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
              >
                删除
              </button>
            </div>
          </div>
        </div>
      )}
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

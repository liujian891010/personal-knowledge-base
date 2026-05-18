import type { AiContextDraft } from './aiContext';

const defaultSyncBridgeUrl = 'http://127.0.0.1:3187';
const syncBridgeUrl = (
  import.meta.env.VITE_NOTEAPP_SYNC_BRIDGE_URL || defaultSyncBridgeUrl
).replace(/\/+$/, '');

export interface AiChatTaskSource {
  file_id: string;
  path: string;
  title: string;
  excerpt: string;
  included_chars: number;
  original_chars: number;
  truncated: boolean;
}

export interface AiChatTaskResult {
  context_type: string;
  instruction: string;
  answer: string;
  source_count: number;
  sources: AiChatTaskSource[];
  model_status: string;
  truncation: {
    included_file_count: number;
    skipped_file_count: number;
    included_chars: number;
    truncated: boolean;
    note: string;
  };
}

export interface AiChatProviderDiagnostic {
  requestId: string;
  statusCode?: number;
  firstTokenMs?: number;
  summary: string;
}

export type AiChatMessage =
  | { id: string; role: 'user'; content: string; createdAt: number }
  | {
    id: string;
    role: 'assistant';
    content: string;
    createdAt: number;
    result?: AiChatTaskResult;
    pending?: boolean;
    diagnostic?: AiChatProviderDiagnostic;
  };

export interface AiChatSession {
  schemaVersion: 'v1';
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  context: AiContextDraft | null;
  messages: AiChatMessage[];
}

export interface AiChatSessionSummary {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messageCount: number;
  contextFileCount: number;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

async function responseErrorMessage(response: Response, source: string): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (isObject(payload) && typeof payload.message === 'string') {
      return `${source} returned ${response.status}: ${payload.message}`;
    }
  } catch {
    // Fall back to status text.
  }
  return `${source} returned ${response.status}`;
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${syncBridgeUrl}${path}`, {
    cache: 'no-store',
    ...init,
    headers: {
      ...(init?.body ? { 'content-type': 'application/json' } : {}),
      ...init?.headers,
    },
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, path));
  }
  return response.json() as Promise<T>;
}

export async function listAiChatSessions(): Promise<AiChatSessionSummary[]> {
  const payload = await requestJson<{ sessions: AiChatSessionSummary[] }>('/api/ai/chat-sessions');
  return Array.isArray(payload.sessions) ? payload.sessions : [];
}

export async function createAiChatSession(payload: Partial<AiChatSession> = {}): Promise<AiChatSession> {
  return requestJson<AiChatSession>('/api/ai/chat-sessions', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function readAiChatSession(sessionId: string): Promise<AiChatSession> {
  return requestJson<AiChatSession>(`/api/ai/chat-sessions/${encodeURIComponent(sessionId)}`);
}

export async function saveAiChatSession(session: AiChatSession): Promise<AiChatSession> {
  return requestJson<AiChatSession>(`/api/ai/chat-sessions/${encodeURIComponent(session.id)}`, {
    method: 'PUT',
    body: JSON.stringify(session),
  });
}

export async function deleteAiChatSession(sessionId: string): Promise<AiChatSessionSummary[]> {
  const payload = await requestJson<{ sessions: AiChatSessionSummary[] }>(
    `/api/ai/chat-sessions/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE' },
  );
  return Array.isArray(payload.sessions) ? payload.sessions : [];
}

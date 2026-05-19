import React, { useEffect, useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  Bot,
  FileDown,
  FolderOpen,
  KeyRound,
  Loader2,
  LogOut,
  Network,
  Settings,
  Trash2,
  UserRound,
  X,
} from 'lucide-react';

import ExplorerView from './views/ExplorerView';
import ConflictsView from './views/ConflictsView';
import SettingsView from './views/SettingsView';
import TrashView from './views/TrashView';
import AiWikiView from './views/AiWikiView';
import AiChatView from './views/AiChatView';
import GraphView from './views/GraphView';
import type { AiContextDraft } from './aiContext';
import { syncBridgeUrl } from './syncBridgeConfig';
import { exportDesktopDiagnostics, getDesktopApi } from './desktop';
import { invalidateLocalSettingsCache } from './useLocalSettingsSnapshot';
import { invalidateWorkspaceFilesCache } from './useWorkspaceFiles';
import { useWorkspaceRegistryController, type RegisteredWorkspace } from './useWorkspaceRegistry';

type AppView = 'explorer' | 'conflicts' | 'trash' | 'ai-chat' | 'ai-wiki' | 'graph' | 'settings' | 'sync';

const loginSessionStorageKey = 'userInfo';
const loginCheckUrl = 'https://sg-al-cwork-web.mediportal.com.cn/user/login/appkey';

interface NavItem {
  id: AppView;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
}

const navItems: NavItem[] = [
  { id: 'explorer', icon: FolderOpen, label: '笔记库浏览' },
  { id: 'ai-chat', icon: Bot, label: 'AI 文档' },
  { id: 'graph', icon: Network, label: '图谱' },
  { id: 'trash', icon: Trash2, label: '回收站' },
  { id: 'settings', icon: Settings, label: '设置' },
];

function readLoginSession(): unknown | null {
  return null;
}

async function responseErrorMessage(response: Response, source: string): Promise<string> {
  try {
    const payload: unknown = await response.json();
    if (typeof payload === 'object' && payload !== null && 'message' in payload && typeof payload.message === 'string') {
      return `${source} returned ${response.status}: ${payload.message}`;
    }
  } catch {
    // Ignore and fall back to the HTTP status.
  }
  return `${source} returned ${response.status}`;
}

async function loadStoredLoginSession(): Promise<unknown | null> {
  const response = await fetch(`${syncBridgeUrl}/api/auth/session`, {
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'auth session load'));
  }
  const payload: unknown = await response.json();
  if (typeof payload !== 'object' || payload === null) {
    return null;
  }
  const record = payload as Record<string, unknown>;
  return record.authenticated ? (record.user_info ?? null) : null;
}

async function persistLoginSession(userInfo: unknown): Promise<void> {
  const response = await fetch(`${syncBridgeUrl}/api/auth/session`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
    },
    body: JSON.stringify({ userInfo }),
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'auth session save'));
  }
}

async function clearLoginSession(): Promise<void> {
  const response = await fetch(`${syncBridgeUrl}/api/auth/session`, {
    method: 'DELETE',
  });
  if (!response.ok) {
    throw new Error(await responseErrorMessage(response, 'auth session clear'));
  }
}

function isLoginResponse(payload: unknown): payload is { resultCode: number; message?: string; data?: unknown } {
  return typeof payload === 'object' && payload !== null && 'resultCode' in payload;
}

function userNameFromUserInfo(userInfo: unknown): string {
  if (typeof userInfo !== 'object' || userInfo === null) {
    return '已登录用户';
  }
  const record = userInfo as Record<string, unknown>;
  const userName = record.userName ?? record.username ?? record.name ?? record.nickName;
  return typeof userName === 'string' && userName.trim() ? userName.trim() : '已登录用户';
}

async function verifyAppKey(appKey: string): Promise<unknown> {
  const params = new URLSearchParams({
    appKey,
    appCode: 'noteApp',
  });
  const requestUrl = `${loginCheckUrl}?${params.toString()}`;
  const response = await fetch(requestUrl, {
    cache: 'no-store',
  });
  if (!response.ok) {
    throw new Error(`login check returned ${response.status}`);
  }
  const payload: unknown = await response.json();
  if (!isLoginResponse(payload) || payload.resultCode !== 1) {
    const message = isLoginResponse(payload) && typeof payload.message === 'string'
      ? payload.message
      : '登录接口未返回成功状态';
    throw new Error(message);
  }
  return payload.data ?? null;
}

function LoginGate({ onLogin }: { onLogin: (userInfo: unknown) => Promise<void> | void }) {
  const [appKey, setAppKey] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const normalizedAppKey = appKey.trim();
    if (!normalizedAppKey) {
      setError('请输入 appKey。');
      return;
    }
      setIsSubmitting(true);
    setError(null);
    try {
      const userInfo = await verifyAppKey(normalizedAppKey);
      await onLogin(userInfo);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center overflow-hidden bg-[#080a12] p-4 text-[#e3e2e6]">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_20%_10%,rgba(233,69,96,0.28),transparent_28%),radial-gradient(circle_at_80%_20%,rgba(169,200,252,0.18),transparent_30%),linear-gradient(135deg,#080a12_0%,#101827_45%,#1a1a2e_100%)]" />
      <div className="absolute inset-x-0 bottom-0 h-1/2 bg-[linear-gradient(180deg,transparent,rgba(15,52,96,0.2))]" />
      <form
        onSubmit={handleSubmit}
        className="relative w-full max-w-md overflow-hidden rounded-3xl border border-[#0f3460] bg-[#121316]/95 p-6 shadow-2xl shadow-black/50 backdrop-blur-md"
      >
        <div className="mb-6 flex items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl border border-[#e94560]/40 bg-gradient-to-br from-[#e94560] to-[#0f3460] text-white shadow-lg">
            <KeyRound size={20} />
          </div>
          <div>
            <h1 className="text-xl font-black tracking-tight text-white">登录 NoteAI</h1>
            <p className="mt-1 text-[12px] text-slate-500">首次使用需要验证 appKey。</p>
          </div>
        </div>

        <label className="block">
          <span className="mb-2 block text-[12px] font-semibold uppercase tracking-wider text-slate-500">appKey</span>
          <input
            autoFocus
            value={appKey}
            onChange={(event) => setAppKey(event.target.value)}
            disabled={isSubmitting}
            className="h-11 w-full rounded-xl border border-[#0f3460] bg-[#0b1020] px-3 font-mono text-[13px] text-[#e3e2e6] outline-none placeholder:text-slate-600 focus:border-[#a9c8fc]/70 disabled:opacity-60"
            placeholder="请输入 appKey"
          />
        </label>

        {error && (
          <div className="mt-4 rounded-xl border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] leading-5 text-[#ffb782]">
            登录失败：{error}
          </div>
        )}

        <button
          type="submit"
          disabled={isSubmitting}
          className="mt-6 inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-[#0f3460] bg-[#0f3460]/60 text-[13px] font-bold text-[#a9c8fc] transition-colors hover:bg-[#15508f] hover:text-white disabled:cursor-not-allowed disabled:opacity-60"
        >
          {isSubmitting && <Loader2 size={16} className="animate-spin" />}
          {isSubmitting ? '验证中' : '登录'}
        </button>
      </form>
    </div>
  );
}

function Sidebar({ currentView, setView }: { currentView: AppView, setView: (view: AppView) => void }) {
  return (
    <aside className="hidden md:flex h-full w-64 flex-shrink-0 flex-col border-r border-[#0f3460] bg-[#16213e] z-40">
      <div className="border-b border-[#0f3460] p-6">
        <button className="flex items-center space-x-3 text-left" onClick={() => setView('explorer')}>
          <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-[#e94560]/30 bg-gradient-to-br from-[#e94560] to-[#0f3460] font-bold text-white shadow-lg">
            N
          </div>
          <div>
            <h1 className="text-xl font-black tracking-tighter text-[#e94560]">NoteAI</h1>
          </div>
        </button>
      </div>

      <nav className="flex-1 py-2 text-sm font-medium tracking-wide">
        <ul className="space-y-1">
          {navItems.map((item) => (
            <li key={item.id}>
              <button
                onClick={() => setView(item.id)}
                className={`flex w-full items-center space-x-3 border-l-4 px-4 py-3 transition-colors duration-200 ${
                  currentView === item.id
                    ? 'border-[#e94560] bg-[#1f2b4a] text-[#e94560]'
                    : 'border-transparent text-slate-400 hover:bg-[#1f2b4a] hover:text-slate-200'
                }`}
              >
                <item.icon size={20} />
                <span>{item.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </nav>

    </aside>
  );
}

function MobileNav({ currentView, setView }: { currentView: AppView, setView: (view: AppView) => void }) {
  return (
    <nav className="grid h-16 flex-shrink-0 grid-cols-5 border-t border-[#0f3460] bg-[#16213e] md:hidden">
      {navItems.map((item) => (
        <button
          key={item.id}
          onClick={() => setView(item.id)}
          className={`flex flex-col items-center justify-center gap-1 text-[11px] ${
            currentView === item.id ? 'text-[#e94560]' : 'text-slate-400'
          }`}
        >
          <item.icon size={18} />
          <span>{item.label}</span>
        </button>
      ))}
    </nav>
  );
}

function WorkspaceGate({
  isLoading,
  lastError,
  onOpenSettings,
}: {
  isLoading: boolean;
  lastError: string | null;
  onOpenSettings: () => void;
}) {
  return (
    <div className="flex h-full items-center justify-center p-6">
      <div className="w-full max-w-md rounded-3xl border border-[#0f3460] bg-[#16213e] p-8 shadow-2xl shadow-black/30">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-[#0f3460] bg-[#121316] text-[#a9c8fc]">
          <FolderOpen size={22} />
        </div>
        <h2 className="mt-5 text-2xl font-black text-[#e3e2e6]">未配置工作区</h2>
        <p className="mt-3 text-[13px] leading-6 text-slate-400">
          工作区添加、切换和移除已经统一放到设置页的通用项。请到设置里完成工作区配置。
        </p>
        {lastError && (
          <p className="mt-4 rounded-xl border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] leading-5 text-[#ffb782]">
            {lastError}
          </p>
        )}
        <button
          type="button"
          disabled={isLoading}
          onClick={onOpenSettings}
          className="mt-6 inline-flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-[#0f3460] bg-[#0f3460]/40 text-[13px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isLoading && <Loader2 size={16} className="animate-spin" />}
          <span className="text-[13px]">前往设置</span>
        </button>
      </div>
    </div>
  );
}

function TopBar({
  userInfo,
  onLogout,
  activeWorkspace,
  workspaces,
  isWorkspaceLoading,
  isWorkspaceMutating,
  isWorkspaceSwitching,
  workspaceError,
  onSelectWorkspaceFolder,
  onActivateWorkspace,
  onDeleteWorkspace,
}: {
  userInfo: unknown;
  onLogout: () => void;
  activeWorkspace: RegisteredWorkspace | null;
  workspaces: RegisteredWorkspace[];
  isWorkspaceLoading: boolean;
  isWorkspaceMutating: boolean;
  isWorkspaceSwitching: boolean;
  workspaceError: string | null;
  onSelectWorkspaceFolder: () => void | Promise<void>;
  onActivateWorkspace: (workspaceId: string) => void | Promise<void>;
  onDeleteWorkspace: (workspaceId: string) => void | Promise<void>;
}) {
  const userName = userNameFromUserInfo(userInfo);
  const canExportDiagnostics = Boolean(getDesktopApi()?.exportDiagnostics);
  const [diagnosticExportStatus, setDiagnosticExportStatus] = useState<string | null>(null);
  const [isWorkspaceDialogOpen, setIsWorkspaceDialogOpen] = useState(false);
  const [workspaceToDelete, setWorkspaceToDelete] = useState<RegisteredWorkspace | null>(null);
  const [workspaceToActivate, setWorkspaceToActivate] = useState<RegisteredWorkspace | null>(null);

  async function handleExportDiagnostics() {
    setDiagnosticExportStatus(null);
    try {
      const result = await exportDesktopDiagnostics();
      if (!result || result.canceled) {
        return;
      }
      setDiagnosticExportStatus(`诊断已导出：${result.path}`);
    } catch (error) {
      setDiagnosticExportStatus(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <>
    <header className="sticky top-0 z-30 flex h-16 w-full flex-shrink-0 items-center justify-between border-b border-[#0f3460] bg-[#16213e]/80 px-4 backdrop-blur-md md:px-6">
      <div className="min-w-0 flex-1" />
      <div className="flex min-w-0 items-center gap-2">
        {diagnosticExportStatus && (
          <span className="hidden max-w-56 truncate rounded-lg border border-[#0f3460] bg-[#121316] px-3 py-2 text-[11px] text-slate-400 lg:inline" title={diagnosticExportStatus}>
            {diagnosticExportStatus}
          </span>
        )}
        {canExportDiagnostics && (
          <button
            type="button"
            onClick={() => void handleExportDiagnostics()}
            className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-[#0f3460] bg-[#121316] px-3 text-[12px] font-semibold text-slate-300 transition-colors hover:text-white"
            title="导出桌面诊断包"
          >
            <FileDown size={14} />
            <span className="hidden lg:inline">诊断</span>
          </button>
        )}
        <div className="hidden min-w-0 items-center gap-2 rounded-lg border border-[#0f3460] bg-[#121316] px-3 py-2 text-[12px] text-slate-300 sm:flex">
          <UserRound size={14} className="flex-shrink-0 text-[#a9c8fc]" />
          <span className="max-w-40 truncate" title={userName}>{userName}</span>
        </div>
        <button
          type="button"
          onClick={onLogout}
          className="inline-flex h-9 items-center justify-center gap-2 rounded-lg border border-[#0f3460] bg-[#121316] px-3 text-[12px] font-semibold text-slate-300 transition-colors hover:text-white"
          title="退出登录"
        >
          <LogOut size={14} />
          <span className="hidden sm:inline">退出</span>
        </button>
      </div>
    </header>
    {isWorkspaceDialogOpen && (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
        <div className="flex w-full max-w-2xl flex-col overflow-hidden rounded-3xl border border-[#0f3460] bg-[#16213e] shadow-2xl shadow-black/50">
          <div className="flex items-center justify-between gap-3 border-b border-[#0f3460] px-5 py-4">
            <div>
              <h3 className="text-lg font-bold text-[#e3e2e6]">工作区</h3>
              <p className="mt-1 text-[12px] text-slate-500">切换当前活动工作区，或添加新的工作区文件夹。</p>
            </div>
            <button
              type="button"
              onClick={() => setIsWorkspaceDialogOpen(false)}
              className="rounded border border-[#0f3460] bg-[#121316] p-2 text-slate-400 hover:text-white"
            >
              <X size={16} />
            </button>
          </div>
          <div className="max-h-[60vh] overflow-y-auto p-5">
            {workspaceError && (
              <p className="mb-4 rounded-xl border border-[#ffb782]/30 bg-[#ffb782]/10 p-3 text-[12px] leading-5 text-[#ffb782]">
                {workspaceError}
              </p>
            )}
            <div className="grid gap-3">
              {workspaces.map((workspace) => (
                <div
                  key={workspace.id}
                  className={`grid grid-cols-[minmax(0,1fr)_auto] gap-3 rounded-2xl border p-4 ${
                    workspace.is_active
                      ? 'border-[#e94560]/40 bg-[#0f3460]/30'
                      : 'border-[#0f3460] bg-[#121316]'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => {
                      if (workspace.is_active) {
                        setIsWorkspaceDialogOpen(false);
                        return;
                      }
                      setWorkspaceToActivate(workspace);
                    }}
                    className="min-w-0 text-left"
                  >
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[13px] font-semibold text-[#e3e2e6]">{workspace.name}</span>
                      {workspace.is_active && (
                        <span className="rounded border border-[#e94560]/40 bg-[#e94560]/10 px-2 py-0.5 text-[10px] font-bold text-[#ffb3c0]">
                          当前
                        </span>
                      )}
                    </div>
                    <p className="mt-1 break-all font-mono text-[11px] text-slate-500">{workspace.vault_root}</p>
                    <p className="mt-2 text-[11px] text-slate-500">
                      {workspace.initialized ? '已初始化' : '待初始化'} · {workspace.exists ? '路径存在' : '路径缺失'}
                    </p>
                  </button>
                  <button
                    type="button"
                    disabled={isWorkspaceMutating}
                    onClick={() => setWorkspaceToDelete(workspace)}
                    className="inline-flex h-9 items-center justify-center rounded-lg border border-[#0f3460] bg-[#121316] px-3 text-[12px] text-slate-400 hover:text-[#ffb782] disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    移除
                  </button>
                </div>
              ))}
            </div>
          </div>
          <div className="flex items-center justify-between gap-3 border-t border-[#0f3460] px-5 py-4">
            <p className="text-[11px] text-slate-500">移除只删除注册项，不删除真实文件夹。</p>
            <button
              type="button"
              disabled={isWorkspaceMutating}
              onClick={() => {
                void onSelectWorkspaceFolder();
                setIsWorkspaceDialogOpen(false);
              }}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-xl border border-[#0f3460] bg-[#0f3460]/40 px-4 text-[13px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isWorkspaceMutating && <Loader2 size={15} className="animate-spin" />}
              添加工作区
            </button>
          </div>
        </div>
      </div>
    )}
    {workspaceToDelete && (
      <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
        <div className="w-full max-w-md rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 shadow-2xl shadow-black/50">
          <h3 className="text-lg font-bold text-[#e3e2e6]">移除工作区</h3>
          <p className="mt-3 text-[13px] leading-6 text-slate-400">
            将从工作区列表移除：{workspaceToDelete.name}
          </p>
          <p className="mt-2 break-all font-mono text-[11px] text-slate-500">{workspaceToDelete.vault_root}</p>
          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setWorkspaceToDelete(null)}
              className="rounded border border-[#0f3460] bg-[#121316] px-4 py-2 text-[13px] text-slate-300 hover:text-white"
            >
              取消
            </button>
            <button
              type="button"
              disabled={isWorkspaceMutating}
              onClick={() => {
                void onDeleteWorkspace(workspaceToDelete.id);
                setWorkspaceToDelete(null);
                setIsWorkspaceDialogOpen(false);
              }}
              className="rounded border border-[#e94560]/40 bg-[#e94560]/20 px-4 py-2 text-[13px] font-semibold text-[#ffb3c0] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              移除
            </button>
          </div>
        </div>
      </div>
    )}
    {workspaceToActivate && (
      <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
        <div className="w-full max-w-md rounded-2xl border border-[#0f3460] bg-[#16213e] p-5 shadow-2xl shadow-black/50">
          <h3 className="text-lg font-bold text-[#e3e2e6]">切换工作区</h3>
          <p className="mt-3 text-[13px] leading-6 text-slate-400">
            确认切换到：{workspaceToActivate.name}
          </p>
          <p className="mt-2 break-all font-mono text-[11px] text-slate-500">{workspaceToActivate.vault_root}</p>
          <div className="mt-5 flex justify-end gap-2">
            <button
              type="button"
              disabled={isWorkspaceSwitching}
              onClick={() => setWorkspaceToActivate(null)}
              className="rounded border border-[#0f3460] bg-[#121316] px-4 py-2 text-[13px] text-slate-300 hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              取消
            </button>
            <button
              type="button"
              disabled={isWorkspaceSwitching}
              onClick={() => {
                void onActivateWorkspace(workspaceToActivate.id);
                setWorkspaceToActivate(null);
                setIsWorkspaceDialogOpen(false);
              }}
              className="inline-flex items-center justify-center gap-2 rounded border border-[#2a5ea3] bg-[#0f3460]/40 px-4 py-2 text-[13px] font-semibold text-[#a9c8fc] hover:text-white disabled:cursor-not-allowed disabled:opacity-50"
            >
              {isWorkspaceSwitching && <Loader2 size={14} className="animate-spin" />}
              确认切换
            </button>
          </div>
        </div>
      </div>
    )}
    </>
  );
}

export default function App() {
  const [currentView, setCurrentView] = useState<AppView>('explorer');
  const [initialExplorerPath, setInitialExplorerPath] = useState<string | null>(null);
  const [initialExplorerContextFileIds, setInitialExplorerContextFileIds] = useState<string[] | null>(null);
  const [initialAiContext, setInitialAiContext] = useState<AiContextDraft | null>(null);
  const [userInfo, setUserInfo] = useState<unknown | null>(() => readLoginSession());
  const [isAuthLoading, setIsAuthLoading] = useState(true);
  const [workspaceReloadVersion, setWorkspaceReloadVersion] = useState(0);
  const [isWorkspaceSwitching, setIsWorkspaceSwitching] = useState(false);
  const {
    workspaces,
    activeWorkspace,
    activeWorkspaceId,
    isLoading: isWorkspaceLoading,
    isMutating: isWorkspaceMutating,
    lastError: workspaceError,
    activateWorkspace,
    selectWorkspaceFolder,
    removeWorkspace,
  } = useWorkspaceRegistryController(Boolean(userInfo) && !isAuthLoading);

  useEffect(() => {
    let cancelled = false;
    loadStoredLoginSession()
      .then((nextUserInfo) => {
        if (!cancelled) {
          setUserInfo(nextUserInfo);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setUserInfo(null);
        }
      })
      .finally(() => {
        if (!cancelled) {
          setIsAuthLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  function openWorkspacePath(path: string) {
    setInitialExplorerPath(path);
    setCurrentView('explorer');
  }

  function openAiContext(context: AiContextDraft) {
    setInitialAiContext(context);
    setCurrentView('ai-chat');
  }

  function openExplorerWithContext(fileIds: string[]) {
    setInitialExplorerContextFileIds(fileIds);
    setCurrentView('explorer');
  }

  async function handleLogin(nextUserInfo: unknown) {
    await persistLoginSession(nextUserInfo);
    setUserInfo(nextUserInfo);
  }

  async function logout() {
    await clearLoginSession();
    setUserInfo(null);
  }

  if (isAuthLoading) {
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-[#080a12]/96 text-[#e3e2e6]">
        <div className="flex items-center gap-3 rounded-2xl border border-[#0f3460] bg-[#121316] px-5 py-4 shadow-2xl shadow-black/40">
          <Loader2 size={18} className="animate-spin text-[#a9c8fc]" />
          <span className="text-[14px] font-semibold">Loading login session...</span>
        </div>
      </div>
    );
  }

  if (!userInfo) {
    return <LoginGate onLogin={handleLogin} />;
  }

  function refreshWorkspaceShell() {
    invalidateWorkspaceFilesCache();
    invalidateLocalSettingsCache();
    setInitialExplorerPath(null);
    setInitialExplorerContextFileIds(null);
    setInitialAiContext(null);
    setWorkspaceReloadVersion((current) => current + 1);
  }

  async function handleActivateWorkspace(workspaceId: string) {
    setIsWorkspaceSwitching(true);
    try {
      const nextActiveWorkspaceId = await activateWorkspace(workspaceId);
      if (nextActiveWorkspaceId) {
        refreshWorkspaceShell();
      }
    } finally {
      setIsWorkspaceSwitching(false);
    }
  }

  async function handleSelectWorkspaceFolder() {
    const nextActiveWorkspaceId = await selectWorkspaceFolder();
    if (nextActiveWorkspaceId) {
      refreshWorkspaceShell();
    }
  }

  async function handleDeleteWorkspace(workspaceId: string) {
    await removeWorkspace(workspaceId);
    refreshWorkspaceShell();
  }

  const shouldShowWorkspaceGate = !activeWorkspace && currentView !== 'settings';

  return (
    <div className="flex h-screen overflow-hidden bg-[#1a1a2e] font-sans text-[#e3e2e6]">
      {isAuthLoading && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-[#080a12]/96 text-[#e3e2e6]">
          <div className="flex items-center gap-3 rounded-2xl border border-[#0f3460] bg-[#121316] px-5 py-4 shadow-2xl shadow-black/40">
            <Loader2 size={18} className="animate-spin text-[#a9c8fc]" />
            <span className="text-[14px] font-semibold">正在恢复登录状态...</span>
          </div>
        </div>
      )}
      {!isAuthLoading && !userInfo && <LoginGate onLogin={handleLogin} />}
      <Sidebar currentView={currentView} setView={setCurrentView} />

      <div className="relative flex h-screen min-w-0 flex-1 flex-col overflow-hidden bg-[#1a1a2e]">
        <TopBar
          userInfo={userInfo}
          onLogout={() => void logout()}
          activeWorkspace={activeWorkspace}
          workspaces={workspaces}
          isWorkspaceLoading={isWorkspaceLoading}
          isWorkspaceMutating={isWorkspaceMutating}
          isWorkspaceSwitching={isWorkspaceSwitching}
          workspaceError={workspaceError}
          onSelectWorkspaceFolder={() => void handleSelectWorkspaceFolder()}
          onActivateWorkspace={handleActivateWorkspace}
          onDeleteWorkspace={(workspaceId) => void handleDeleteWorkspace(workspaceId)}
        />

        <div className="relative flex-1 overflow-hidden">
          {isWorkspaceSwitching && (
            <div className="absolute inset-0 z-40 flex items-center justify-center bg-[#0b1020]/78 backdrop-blur-sm">
              <div className="rounded-2xl border border-[#2a5ea3] bg-[#121316] px-5 py-4 text-center shadow-2xl shadow-black/40">
                <div className="flex items-center justify-center gap-3 text-[#a9c8fc]">
                  <Loader2 size={18} className="animate-spin" />
                  <span className="text-[14px] font-semibold">正在切换工作区...</span>
                </div>
                <p className="mt-2 text-[12px] text-slate-500">切换完成前，旧工作区内容将暂时隐藏。</p>
              </div>
            </div>
          )}
          {shouldShowWorkspaceGate ? (
            <WorkspaceGate
              isLoading={isWorkspaceLoading}
              lastError={workspaceError}
              onOpenSettings={() => setCurrentView('settings')}
            />
          ) : (
          <AnimatePresence mode="wait">
            <motion.div
              key={`${currentView}:${activeWorkspaceId ?? 'none'}:${workspaceReloadVersion}`}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2 }}
              className="flex h-full flex-col"
            >
              {currentView === 'explorer' && (
                <ExplorerView
                  initialSelectedPath={initialExplorerPath}
                  initialContextFileIds={initialExplorerContextFileIds}
                  onInitialSelectedPathConsumed={() => setInitialExplorerPath(null)}
                  onInitialContextFileIdsConsumed={() => setInitialExplorerContextFileIds(null)}
                  onOpenAiContext={openAiContext}
                  onOpenConflicts={() => setCurrentView('conflicts')}
                />
              )}
              {currentView === 'conflicts' && <ConflictsView />}
              {currentView === 'trash' && <TrashView />}
              {currentView === 'graph' && <GraphView />}
              {currentView === 'ai-chat' && (
                <AiChatView
                  initialContext={initialAiContext}
                  onClearInitialContext={() => setInitialAiContext(null)}
                  onOpenExplorer={openExplorerWithContext}
                  onOpenWorkspacePath={openWorkspacePath}
                />
              )}
              {currentView === 'ai-wiki' && <AiWikiView onOpenWorkspacePath={openWorkspacePath} />}
              {currentView === 'settings' && (
                <SettingsView
                  initialTab="general"
                  workspaces={workspaces}
                  activeWorkspace={activeWorkspace}
                  isWorkspaceLoading={isWorkspaceLoading}
                  isWorkspaceMutating={isWorkspaceMutating}
                  isWorkspaceSwitching={isWorkspaceSwitching}
                  workspaceError={workspaceError}
                  onSelectWorkspaceFolder={() => void handleSelectWorkspaceFolder()}
                  onActivateWorkspace={handleActivateWorkspace}
                  onDeleteWorkspace={(workspaceId) => void handleDeleteWorkspace(workspaceId)}
                />
              )}
              {currentView === 'sync' && (
                <SettingsView
                  initialTab="sync"
                  workspaces={workspaces}
                  activeWorkspace={activeWorkspace}
                  isWorkspaceLoading={isWorkspaceLoading}
                  isWorkspaceMutating={isWorkspaceMutating}
                  isWorkspaceSwitching={isWorkspaceSwitching}
                  workspaceError={workspaceError}
                  onSelectWorkspaceFolder={() => void handleSelectWorkspaceFolder()}
                  onActivateWorkspace={handleActivateWorkspace}
                  onDeleteWorkspace={(workspaceId) => void handleDeleteWorkspace(workspaceId)}
                />
              )}
            </motion.div>
          </AnimatePresence>
          )}
        </div>

        <MobileNav currentView={currentView} setView={setCurrentView} />
      </div>
    </div>
  );
}

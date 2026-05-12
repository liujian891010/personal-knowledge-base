import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  AlertTriangle,
  Bot,
  FolderOpen,
  KeyRound,
  Loader2,
  LogOut,
  Settings,
  Trash2,
  UserRound,
} from 'lucide-react';

import ExplorerView from './views/ExplorerView';
import ConflictsView from './views/ConflictsView';
import SettingsView from './views/SettingsView';
import TrashView from './views/TrashView';
import AiWikiView from './views/AiWikiView';
import AiChatView from './views/AiChatView';
import type { AiContextDraft } from './aiContext';

type AppView = 'explorer' | 'conflicts' | 'trash' | 'ai-chat' | 'ai-wiki' | 'settings' | 'sync';

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
  { id: 'trash', icon: Trash2, label: '回收站' },
  { id: 'settings', icon: Settings, label: '设置' },
];

function readLoginSession(): unknown | null {
  try {
    const rawValue = window.sessionStorage.getItem(loginSessionStorageKey);
    if (!rawValue) {
      return null;
    }
    return JSON.parse(rawValue);
  } catch {
    return null;
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

function LoginGate({ onLogin }: { onLogin: (userInfo: unknown) => void }) {
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
      window.sessionStorage.setItem(loginSessionStorageKey, JSON.stringify(userInfo));
      onLogin(userInfo);
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
    <nav className="grid h-16 flex-shrink-0 grid-cols-7 border-t border-[#0f3460] bg-[#16213e] md:hidden">
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

function TopBar({
  userInfo,
  onLogout,
}: {
  userInfo: unknown;
  onLogout: () => void;
}) {
  const userName = userNameFromUserInfo(userInfo);

  return (
    <header className="sticky top-0 z-30 flex h-16 w-full flex-shrink-0 items-center justify-between border-b border-[#0f3460] bg-[#16213e]/80 px-4 backdrop-blur-md md:px-6">
      <div />
      <div className="flex min-w-0 items-center gap-2">
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
  );
}

export default function App() {
  const [currentView, setCurrentView] = useState<AppView>('explorer');
  const [initialExplorerPath, setInitialExplorerPath] = useState<string | null>(null);
  const [initialExplorerContextFileIds, setInitialExplorerContextFileIds] = useState<string[] | null>(null);
  const [initialAiContext, setInitialAiContext] = useState<AiContextDraft | null>(null);
  const [userInfo, setUserInfo] = useState<unknown | null>(() => readLoginSession());

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

  function logout() {
    window.sessionStorage.removeItem(loginSessionStorageKey);
    setUserInfo(null);
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#1a1a2e] font-sans text-[#e3e2e6]">
      {!userInfo && <LoginGate onLogin={setUserInfo} />}
      <Sidebar currentView={currentView} setView={setCurrentView} />

      <div className="relative flex h-screen min-w-0 flex-1 flex-col overflow-hidden bg-[#1a1a2e]">
        <TopBar userInfo={userInfo} onLogout={logout} />

        <div className="relative flex-1 overflow-hidden">
          <AnimatePresence mode="wait">
            <motion.div
              key={currentView}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2 }}
              className="flex h-full flex-col"
            >
              {currentView === 'explorer' && (
                <ExplorerView
                  setView={setCurrentView}
                  initialSelectedPath={initialExplorerPath}
                  initialContextFileIds={initialExplorerContextFileIds}
                  onInitialSelectedPathConsumed={() => setInitialExplorerPath(null)}
                  onInitialContextFileIdsConsumed={() => setInitialExplorerContextFileIds(null)}
                  onOpenAiContext={openAiContext}
                />
              )}
              {currentView === 'conflicts' && <ConflictsView />}
              {currentView === 'trash' && <TrashView />}
              {currentView === 'ai-chat' && (
                <AiChatView
                  initialContext={initialAiContext}
                  onClearInitialContext={() => setInitialAiContext(null)}
                  onOpenExplorer={openExplorerWithContext}
                />
              )}
              {currentView === 'ai-wiki' && <AiWikiView onOpenWorkspacePath={openWorkspacePath} />}
              {currentView === 'settings' && <SettingsView initialTab="general" />}
              {currentView === 'sync' && <SettingsView initialTab="sync" />}
            </motion.div>
          </AnimatePresence>
        </div>

        <MobileNav currentView={currentView} setView={setCurrentView} />
      </div>
    </div>
  );
}

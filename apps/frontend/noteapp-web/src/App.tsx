import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  AlertTriangle,
  Bot,
  Cloud,
  FolderOpen,
  Settings,
  Trash2,
} from 'lucide-react';

import ExplorerView from './views/ExplorerView';
import ConflictsView from './views/ConflictsView';
import SettingsView from './views/SettingsView';
import TrashView from './views/TrashView';
import AiWikiView from './views/AiWikiView';

type AppView = 'explorer' | 'conflicts' | 'trash' | 'ai-wiki' | 'settings' | 'sync';

interface NavItem {
  id: AppView;
  icon: React.ComponentType<{ size?: number; className?: string }>;
  label: string;
}

const navItems: NavItem[] = [
  { id: 'explorer', icon: FolderOpen, label: '笔记库浏览' },
  { id: 'sync', icon: Cloud, label: '同步状态' },
  { id: 'conflicts', icon: AlertTriangle, label: '冲突解决' },
  { id: 'trash', icon: Trash2, label: '回收站' },
  { id: 'ai-wiki', icon: Bot, label: 'AI 知识库' },
  { id: 'settings', icon: Settings, label: '设置' },
];

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
            <p className="font-sans text-[10px] font-medium uppercase tracking-widest text-slate-400">
              本地优先的笔记库
            </p>
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

      <div className="border-t border-[#0f3460] p-4">
        <div className="rounded-lg border border-[#0f3460] bg-[#121316] px-3 py-2">
          <div className="text-[12px] font-semibold text-[#e3e2e6]">当前收口范围</div>
          <p className="mt-1 text-[11px] leading-relaxed text-slate-400">
            仅开放已接入真实工作区与同步桥接的页面。AI Wiki、图谱、回收站和仪表盘暂不进入主流程。
          </p>
        </div>
      </div>
    </aside>
  );
}

function MobileNav({ currentView, setView }: { currentView: AppView, setView: (view: AppView) => void }) {
  return (
    <nav className="grid h-16 flex-shrink-0 grid-cols-6 border-t border-[#0f3460] bg-[#16213e] md:hidden">
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

function TopBar({ currentView }: { currentView: AppView }) {
  const currentItem = navItems.find((item) => item.id === currentView);

  return (
    <header className="sticky top-0 z-30 flex h-16 w-full flex-shrink-0 items-center justify-between border-b border-[#0f3460] bg-[#16213e]/80 px-4 backdrop-blur-md md:px-6">
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">当前页面</div>
        <h2 className="text-base font-bold text-[#e3e2e6]">{currentItem?.label ?? '笔记库浏览'}</h2>
      </div>
      <div className="rounded border border-[#0f3460] bg-[#121316] px-2 py-1 text-[11px] text-slate-400">
        MVP 收口版
      </div>
    </header>
  );
}

export default function App() {
  const [currentView, setCurrentView] = useState<AppView>('explorer');
  const [initialExplorerPath, setInitialExplorerPath] = useState<string | null>(null);

  function openWorkspacePath(path: string) {
    setInitialExplorerPath(path);
    setCurrentView('explorer');
  }

  return (
    <div className="flex h-screen overflow-hidden bg-[#1a1a2e] font-sans text-[#e3e2e6]">
      <Sidebar currentView={currentView} setView={setCurrentView} />

      <div className="relative flex h-screen min-w-0 flex-1 flex-col overflow-hidden bg-[#1a1a2e]">
        <TopBar currentView={currentView} />

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
                <ExplorerView setView={setCurrentView} initialSelectedPath={initialExplorerPath} />
              )}
              {currentView === 'conflicts' && <ConflictsView />}
              {currentView === 'trash' && <TrashView />}
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

import React, { useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import {
  LayoutDashboard, Network, FolderOpen, AlertTriangle, Settings, Cloud, Plus, FileText, Brain, Search, Bell, MoreVertical, Trash2
} from 'lucide-react';

import DashboardView from './views/DashboardView';
import ExplorerView from './views/ExplorerView';
import WikiView from './views/WikiView';
import GraphView from './views/GraphView';
import ConflictsView from './views/ConflictsView';
import SettingsView from './views/SettingsView';
import TrashView from './views/TrashView';

function Sidebar({ currentView, setView }: { currentView: string, setView: (v: string) => void }) {
  const navItems = [
    { id: 'dashboard', icon: LayoutDashboard, label: '仪表盘' },
    { id: 'graph', icon: Network, label: '图谱视图' },
    { id: 'explorer', icon: FolderOpen, label: '笔记库浏览' },
    { id: 'conflicts', icon: AlertTriangle, label: '冲突解决' },
  ];

  return (
    <aside className="hidden md:flex flex-col h-full w-64 border-r border-[#0f3460] bg-[#16213e] flex-shrink-0 z-40">
      <div className="p-6 border-b border-[#0f3460]">
        <div className="flex items-center space-x-3 mb-6 cursor-pointer" onClick={() => setView('dashboard')}>
          <div className="w-10 h-10 rounded-lg bg-gradient-to-br from-[#e94560] to-purple-600 flex items-center justify-center text-white font-bold border border-[#e94560]/30 shadow-lg">N</div>
          <div>
            <h1 className="text-xl font-black text-[#e94560] tracking-tighter">NoteAI</h1>
            <p className="text-[10px] text-slate-400 uppercase tracking-widest font-sans font-medium">本地优先的笔记库</p>
          </div>
        </div>
        <button className="w-full py-2.5 px-4 bg-[#e94560] text-white rounded-lg text-[13px] font-medium hover:brightness-105 hover:shadow-[0_0_8px_rgba(233,69,96,0.5)] transition-all flex items-center justify-center space-x-2 bg-gradient-to-b from-white/10 to-transparent">
          <Plus size={18} />
          <span>新建笔记</span>
        </button>
      </div>
       
      <nav className="py-2 text-sm font-sans font-medium tracking-wide">
        <ul className="space-y-1">
          {navItems.map(item => (
            <li key={item.id}>
              <button 
                onClick={() => setView(item.id)}
                className={`w-full flex items-center space-x-3 px-4 py-3 transition-colors duration-200 ${currentView === item.id ? 'bg-[#1f2b4a] text-[#e94560] border-l-4 border-[#e94560]' : 'text-slate-400 hover:text-slate-200 hover:bg-[#1f2b4a] border-l-4 border-transparent'}`}
              >
                <item.icon size={20} />
                <span>{item.label}</span>
              </button>
            </li>
          ))}
        </ul>
      </nav>

      {/* Embedded File Tree (Visible mostly when expanding Vault Explorer, but we'll show it generally) */}
      <div className="flex-1 overflow-y-auto px-2 py-2">
        <div className="flex flex-col mb-4">
          <div className="flex items-center gap-2 px-2 py-1 text-slate-300">
            <FolderOpen size={16} />
            <span className="text-[13px] font-semibold flex-1 font-sans">笔记</span>
          </div>
          <div className="flex flex-col ml-6 border-l border-[#0f3460] pl-2 mt-1 gap-1">
            <button onClick={() => setView('explorer')} className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] font-sans truncate ${currentView === 'explorer' ? 'bg-[#1f2b4a] text-[#e94560]' : 'text-slate-400 hover:text-slate-200'}`}>
              <FileText size={14} /> 产品路线图.md
            </button>
          </div>
        </div>
        
        <div className="flex flex-col">
          <div className="flex items-center gap-2 px-2 py-1 text-slate-300">
            <Brain size={16} className="text-[#e94560]" />
            <span className="text-[13px] font-semibold flex-1 font-sans">.ai/wiki</span>
          </div>
          <div className="flex flex-col ml-6 border-l border-[#0f3460] pl-2 mt-1 gap-1">
            <button onClick={() => setView('wiki')} className={`flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] font-sans truncate ${currentView === 'wiki' ? 'bg-gradient-to-r from-[#16213e] to-[#1f2b4a]/20 text-[#e94560] border-l-2 border-[#e94560]' : 'text-slate-400 hover:text-slate-200'}`}>
               <Network size={14} /> 架构_V2.wiki
            </button>
          </div>
        </div>
      </div>

      <div className="p-4 border-t border-[#0f3460] text-sm font-sans font-medium tracking-wide">
        <button onClick={() => setView('trash')} className={`w-full flex items-center space-x-3 px-4 py-3 rounded transition-colors duration-200 ${currentView === 'trash' ? 'bg-[#0f3460]/20 text-[#e94560] border-l-2 border-[#e94560]' : 'text-slate-400 hover:text-slate-200 hover:bg-[#1f2b4a]'}`}>
          <Trash2 size={20} />
          <span>回收站</span>
        </button>
        <button onClick={() => setView('settings')} className={`w-full flex items-center space-x-3 px-4 py-3 rounded transition-colors duration-200 ${currentView === 'settings' ? 'bg-[#0f3460]/20 text-[#e94560] border-l-2 border-[#e94560]' : 'text-slate-400 hover:text-slate-200 hover:bg-[#1f2b4a]'}`}>
          <Settings size={20} />
          <span>设置</span>
        </button>
        <button className="w-full flex items-center space-x-3 text-slate-400 px-4 py-3 hover:text-slate-200 hover:bg-[#1f2b4a] rounded transition-colors duration-200">
          <Cloud size={20} />
          <span>同步状态</span>
        </button>
        <div className="mt-4 flex items-center gap-3 px-4 py-2">
            <img src="https://ui-avatars.com/api/?name=Alex+Chen&background=e94560&color=fff" alt="User" className="w-8 h-8 rounded-full border border-[#0f3460]" />
            <div className="flex-1 min-w-0 text-left">
                <p className="text-[13px] font-medium text-[#e3e2e6] truncate">Alex Chen</p>
                <p className="text-xs text-slate-500 truncate">专业版</p>
            </div>
        </div>
      </div>
    </aside>
  );
}

function TopBar() {
  return (
    <header className="flex justify-between items-center px-6 w-full sticky top-0 z-30 h-16 border-b border-[#0f3460] bg-[#16213e]/80 backdrop-blur-md flex-shrink-0">
      <div className="flex items-center space-x-6">
        <div className="relative hidden md:block">
          <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" />
          <input type="text" placeholder="搜索笔记库..." className="w-64 bg-[#121316] border border-[#0f3460] rounded-lg pl-10 pr-4 py-1.5 text-[13px] font-sans focus:outline-none focus:border-[#e94560] focus:ring-1 focus:ring-[#e94560]/50 transition-all placeholder:text-slate-500 text-white" />
        </div>
      </div>
      <div className="flex items-center space-x-4">
        <button className="hidden md:flex bg-[#0f3460] text-white px-3 py-1.5 rounded text-[13px] font-sans font-medium hover:brightness-105 hover:shadow-[0_0_8px_rgba(15,52,96,0.5)] transition-all">快速记录</button>
        <div className="flex items-center space-x-2 text-slate-400">
          <button className="p-2 hover:text-[#e94560] hover:bg-white/5 rounded-full"><Bell size={20} /></button>
          <button className="p-2 hover:text-[#e94560] hover:bg-white/5 rounded-full"><MoreVertical size={20} /></button>
          <img src="https://ui-avatars.com/api/?name=Alex+Chen&background=e94560&color=fff" alt="Avatar" className="w-8 h-8 rounded-full border border-[#0f3460] md:hidden" />
        </div>
      </div>
    </header>
  );
}

export default function App() {
  const [currentView, setCurrentView] = useState('dashboard');

  return (
    <div className="flex h-screen bg-[#1a1a2e] text-[#e3e2e6] font-sans overflow-hidden">
      <Sidebar currentView={currentView} setView={setCurrentView} />
      
      <div className="flex-1 flex flex-col min-w-0 h-screen overflow-hidden relative bg-[#1a1a2e]">
        <TopBar />
        
        <div className="flex-1 overflow-hidden relative">
          <AnimatePresence mode="wait">
            <motion.div
              key={currentView}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2 }}
              className="h-full flex flex-col"
            >
              {currentView === 'dashboard' && <DashboardView />}
              {currentView === 'explorer' && <ExplorerView setView={setCurrentView} />}
              {currentView === 'wiki' && <WikiView />}
              {currentView === 'graph' && <GraphView />}
              {currentView === 'conflicts' && <ConflictsView />}
              {currentView === 'settings' && <SettingsView />}
              {currentView === 'trash' && <TrashView />}
            </motion.div>
          </AnimatePresence>
        </div>
      </div>
    </div>
  );
}

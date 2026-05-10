import React from 'react';
import { FileText, Plus, Search, HelpCircle, Trash2, Settings, Cloud, Brain, Network, ChevronDown, ChevronRight, Folder } from 'lucide-react';

export default function ExplorerView({ setView }: { setView: (v: string) => void }) {
  return (
    <div className="flex h-full bg-[#1a1a2e] overflow-hidden">
      
      {/* File Tree Secondary Sidebar */}
      <aside className="hidden md:flex w-64 border-r border-[#0f3460] bg-[#16213e]/80 flex-shrink-0 flex-col max-h-full overflow-hidden">
        <div className="p-4 border-b border-[#0f3460] flex items-center justify-between bg-[#16213e]">
           <span className="text-[11px] font-bold text-slate-400 uppercase tracking-wider">浏览</span>
           <button className="text-slate-400 hover:text-white transition-colors">
             <Plus size={16} />
           </button>
        </div>
        <div className="flex-1 overflow-y-auto py-2">
          {/* Folders */}
          <div className="flex flex-col">
            <div className="flex items-center gap-2 px-4 py-2 text-slate-300 hover:bg-[#1f2b4a] cursor-pointer transition-colors">
              <ChevronDown size={14} className="text-slate-500" />
              <Folder size={16} className="text-[#a9c8fc]" />
              <span className="text-[13px] font-semibold flex-1 font-sans">架构文档</span>
            </div>
            <div className="flex items-center gap-2 px-4 py-2 text-slate-300 hover:bg-[#1f2b4a] cursor-pointer transition-colors">
              <ChevronRight size={14} className="text-slate-500" />
              <Folder size={16} className="text-[#a9c8fc]" />
              <span className="text-[13px] font-semibold flex-1 font-sans">会议记录</span>
            </div>
            <div className="flex flex-col">
              <div className="flex items-center gap-2 px-4 py-2 text-slate-300 hover:bg-[#1f2b4a] cursor-pointer transition-colors">
                <ChevronDown size={14} className="text-slate-500" />
                <Folder size={16} className="text-[#a9c8fc]" />
                <span className="text-[13px] font-semibold flex-1 font-sans">研究阶段</span>
              </div>
              
              <div className="flex flex-col">
                 <button className={`flex items-center gap-2 px-8 py-2 text-[13px] font-sans truncate bg-[#1f2b4a] text-[#e3e2e6] border-r-2 border-[#e94560]`}>
                   <FileText size={14} className="text-slate-500" /> 产品路线图.md
                 </button>
                 <button className={`flex items-center gap-2 px-8 py-2 text-[13px] font-sans truncate text-slate-400 hover:text-slate-200 hover:bg-[#1f2b4a]`}>
                   <FileText size={14} className="text-slate-500" /> Q3 规划.md
                 </button>
                 <button className={`flex items-center gap-2 px-8 py-2 text-[13px] font-sans truncate text-slate-400 hover:text-slate-200 hover:bg-[#1f2b4a]`}>
                   <FileText size={14} className="text-slate-500" /> 设计系统规范.md
                 </button>
              </div>
            </div>
          </div>
        </div>
      </aside>

      {/* Main Editor Canvas */}
      <div className="flex-1 flex flex-col h-full relative overflow-hidden bg-[#121316]">
        {/* Editor Toolbar */}
        <header className="bg-[#16213e] border-b border-[#0f3460] h-14 flex items-center justify-between px-4 flex-shrink-0 z-10 shadow-sm">
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2 text-slate-300">
              <FileText size={18} className="text-[#e94560]" />
              <span className="text-[13px] font-semibold">产品路线图.md</span>
            </div>
            <div className="h-4 w-px bg-[#0f3460] hidden sm:block"></div>
            <div className="hidden sm:flex items-center gap-2 text-slate-500 font-mono text-[11px]">
              <span>1,240 字</span>
              <span>·</span>
              <span>2秒前保存</span>
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

        {/* Editor Main Area w/ simulated view */}
        <div className="flex-1 overflow-y-auto flex relative bg-[#121316]">
          <div className="hidden sm:flex w-[50px] flex-shrink-0 bg-[#16213e]/20 border-r border-[#0f3460]/50 py-8 flex items-end pr-3 font-mono text-[14px] leading-[1.7] text-slate-600 select-none flex-col">
            {Array.from({ length: 25 }).map((_, i) => <span key={i}>{i + 1}</span>)}
          </div>
          <div className="flex-1 p-6 sm:p-8 md:px-12 font-mono text-[14px] sm:text-[15px] leading-[1.8] text-slate-300 whitespace-pre-wrap outline-none max-w-4xl pt-8 pb-24">
            <span className="text-[#e3e2e6] font-bold text-2xl font-sans tracking-tight block mb-6"># Q3-Q4 产品路线图</span>
            <span className="text-[#a9c8fc] font-bold block mt-8 mb-2">## 核心目标</span>
            1. 为 `.ai/wiki` 目录实现本地优先的向量搜索。{'\n'}
            2. 优化大型Markdown笔记库（&gt;10k 文件）的同步引擎。{'\n'}
            3. 使用多文档上下文窗口增强AI助手。{'\n\n'}
            
            <span className="text-[#a9c8fc] font-bold block mt-8 mb-2">## 当前冲刺</span>
            - 构建拆分视图组件。{'\n'}
            - 与后端团队进行 <span className="text-[#e94560] cursor-pointer hover:underline hover:bg-[#e94560]/10 px-1 rounded transition-colors" onClick={() => setView('wiki')}>[[架构_V2]]</span> 评审。{'\n'}
            - 重构 <span className="text-white bg-[#0f3460]/50 px-1 rounded border border-[#0f3460]">右侧面板助手</span> UI。{'\n\n'}
            
            <span className="text-[#a9c8fc] font-bold block mt-8 mb-2">## 上次同步的笔记</span>
            <span className="text-slate-400">
              需要确保 <span className="text-[#ffb782]">`style_active_navigation`</span> 逻辑严格应用于JSON中定义的SideNavBar组件。目前的同步协议在离线超过48小时时会丢弃事件，这违反了本地优先的约束。
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}

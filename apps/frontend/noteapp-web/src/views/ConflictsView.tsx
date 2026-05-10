import React from 'react';
import { AlertTriangle, X, Merge, Laptop, Smartphone, CheckCircle } from 'lucide-react';

export default function ConflictsView() {
  return (
    <div className="flex flex-col h-full bg-[#121316] relative">
      {/* Resolution Header */}
      <div className="flex-shrink-0 border-b border-[#43474f]/30 bg-[#1a1c1f] p-6 flex flex-col sm:flex-row sm:items-center justify-between gap-4 z-10 shadow-lg shadow-black/20">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <AlertTriangle className="text-red-400" size={20} />
            <span className="text-[13px] font-medium text-red-400 uppercase tracking-wider">同步冲突</span>
          </div>
          <h2 className="text-2xl font-bold text-[#e3e2e6] break-all">在 'Product Roadmap.md' 中检测到冲突</h2>
          <p className="text-slate-400 mt-2 text-sm">请选择要保留的版本，或手动合并更改。</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button className="px-4 py-2 rounded-lg border border-[#43474f] text-[#e3e2e6] hover:bg-[#292a2d] transition-colors text-[13px] font-medium flex items-center gap-2">
            <X size={18} /> 取消
          </button>
          <button className="px-4 py-2 rounded-lg bg-[#0f3460] text-[#a9c8fc] hover:brightness-110 transition-colors text-[13px] font-medium flex items-center gap-2 border border-[#0f3460]/50">
            <Merge size={18} /> 手动合并
          </button>
        </div>
      </div>

      {/* Comparison Area */}
      <div className="flex-1 overflow-hidden flex flex-col lg:flex-row p-4 lg:p-6 gap-4 lg:gap-6 bg-[#121316]">
        {/* Version A */}
        <div className="flex-1 flex flex-col rounded-xl border border-[#43474f]/30 bg-[#1a1c1f] overflow-hidden shadow-lg shadow-black/30">
          <div className="flex-shrink-0 p-4 border-b border-[#43474f]/30 bg-[#1e2023] flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-[#333538] flex items-center justify-center border border-[#43474f]/50">
                 <Laptop size={18} className="text-slate-400" />
              </div>
              <div>
                <div className="text-[13px] font-medium text-[#e3e2e6]">版本 A (MacBook Pro)</div>
                <div className="text-[11px] text-slate-500">修改时间: 今天, 10:42 AM</div>
              </div>
            </div>
            <button className="px-4 py-2 rounded-lg bg-[#0f3460] text-white hover:brightness-105 transition-all text-[13px] font-medium flex items-center gap-2 hover:shadow-[0_0_8px_rgba(15,52,96,0.5)] border border-[#0f3460]/50">
              <CheckCircle size={18} /> 保留此版本
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-4 bg-[#0d0e11] font-mono text-[15px] leading-[1.7] text-slate-400 relative">
             <div className="absolute inset-y-0 left-0 w-8 bg-[#0d0e11] border-r border-[#43474f]/20 flex flex-col items-center py-4 text-slate-600 text-xs select-none">
               {Array.from({length: 9}).map((_, i) => <span key={i}>{i+1}</span>)}
             </div>
             <div className="pl-10 space-y-2 whitespace-pre-wrap">
               <span className="text-[#a9c8fc]"># Q3 Product Roadmap</span>{'\n\n'}
               ## Core Objectives{'\n'}
               - Launch Local-First Sync Engine (Beta){'\n'}
               - Implement E2E Encryption for Vaults{'\n'}
               <span className="bg-red-500/20 text-red-300 px-1 -mx-1 rounded line-through decoration-red-500/50">- Revamp onboarding flow for mobile apps</span>{'\n'}
               <span className="bg-[#a9c8fc]/20 text-[#a9c8fc] px-1 -mx-1 rounded border-l-2 border-[#a9c8fc]">- Integrate LLM context awareness for search</span>{'\n\n'}
               ## Timeline{'\n'}
               **August:** Sync Engine release to early access group. Gather telemetry and resolve edge case conflicts.
             </div>
          </div>
        </div>

        {/* VS Divider */}
        <div className="hidden lg:flex flex-col items-center justify-center -mx-4 z-10">
           <div className="w-10 h-10 rounded-full bg-[#333538] border-2 border-[#121316] flex items-center justify-center shadow-lg shadow-black/50 text-slate-400 font-bold text-sm">
               VS
           </div>
        </div>

        {/* Version B */}
        <div className="flex-1 flex flex-col rounded-xl border border-[#0f3460] bg-[#1a1c1f] overflow-hidden shadow-[0_0_15px_rgba(15,52,96,0.3)] relative">
          <div className="absolute top-0 inset-x-0 h-1 bg-gradient-to-r from-[#0f3460] to-[#e94560]/50"></div>
          <div className="flex-shrink-0 p-4 border-b border-[#43474f]/30 bg-[#1e2023] flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-full bg-[#333538] flex items-center justify-center border border-[#43474f]/50">
                 <Smartphone size={18} className="text-slate-400" />
              </div>
              <div>
                <div className="text-[13px] font-medium text-[#e3e2e6]">版本 B (iPhone 14 Pro)</div>
                <div className="text-[11px] text-slate-500">修改时间: 今天, 10:45 AM</div>
              </div>
            </div>
            <button className="px-4 py-2 rounded-lg bg-[#e94560] text-white hover:brightness-105 transition-all text-[13px] font-medium flex items-center gap-2 hover:shadow-[0_0_8px_rgba(233,69,96,0.5)] bg-gradient-to-b from-white/10 to-transparent">
              <CheckCircle size={18} /> 保留此版本
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-4 bg-[#0d0e11] font-mono text-[15px] leading-[1.7] text-slate-400 relative">
             <div className="absolute inset-y-0 left-0 w-8 bg-[#0d0e11] border-r border-[#43474f]/20 flex flex-col items-center py-4 text-slate-600 text-xs select-none">
               {Array.from({length: 9}).map((_, i) => <span key={i}>{i+1}</span>)}
             </div>
             <div className="pl-10 space-y-2 whitespace-pre-wrap">
               <span className="text-[#a9c8fc]"># Q3 Product Roadmap</span>{'\n\n'}
               ## Core Objectives{'\n'}
               - Launch Local-First Sync Engine (Beta){'\n'}
               - Implement E2E Encryption for Vaults{'\n'}
               <span className="bg-[#a9c8fc]/20 text-[#a9c8fc] px-1 -mx-1 rounded border-l-2 border-[#a9c8fc]">- Redesign mobile navigation for iPad support</span>{'\n'}
               <span className="bg-[#a9c8fc]/20 text-[#a9c8fc] px-1 -mx-1 rounded border-l-2 border-[#a9c8fc]">- Optimize SQLite queries for large vaults</span>{'\n\n'}
               ## Timeline{'\n'}
               **August:** Sync Engine release to early access group. Gather telemetry and resolve edge case conflicts.
             </div>
          </div>
        </div>

      </div>
    </div>
  );
}

import React from 'react';
import { Trash2, RefreshCw, AlertCircle, FileText, Network, Search, MoreVertical } from 'lucide-react';

export default function TrashView() {
  const deletedFiles = [
    { name: '旧架构草案.md', size: '14 KB', deletedAt: '2 天前', origin: '笔记 / 架构', type: 'doc' },
    { name: 'Q2 路线图 (已弃用).md', size: '8 KB', deletedAt: '5 天前', origin: '笔记 / 规划', type: 'doc' },
    { name: '会议记录 - 已取消.md', size: '2 KB', deletedAt: '1 周前', origin: '笔记 / 会议', type: 'doc' },
    { name: '陈旧的想法.wiki', size: '32 KB', deletedAt: '2 周前', origin: '.ai/wiki', type: 'wiki' },
  ];

  return (
    <div className="flex flex-col h-full bg-[#1a1a2e] overflow-hidden">
      <div className="px-6 md:px-8 py-6 border-b border-[#0f3460] bg-[#16213e] flex-shrink-0 z-10 shadow-lg shadow-black/20">
        <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
          <div>
            <h1 className="text-3xl font-bold text-[#e3e2e6] flex items-center gap-3 tracking-tight">
              <Trash2 className="text-[#e94560]" size={32} /> 回收站
            </h1>
            <p className="text-slate-400 mt-2 text-sm max-w-xl">回收站中的项目会在 30 天后自动永久删除。在此之前，您可以将它们恢复到原来的位置。</p>
          </div>
          <button className="px-4 py-2 bg-[#0f3460]/30 hover:bg-[#0f3460]/60 border border-[#0f3460] text-[#e94560] rounded-lg text-[13px] font-medium transition-colors flex items-center gap-2 w-fit">
            <AlertCircle size={16} /> 清空回收站
          </button>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-6 md:p-8">
        <div className="max-w-5xl mx-auto">
          {/* Search/Filter Bar */}
          <div className="flex items-center gap-4 mb-6">
            <div className="relative flex-1 max-w-md">
              <Search size={18} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
              <input type="text" placeholder="搜索已删除的项目..." className="w-full bg-[#121316] border border-[#0f3460] rounded-lg pl-10 pr-4 py-2 text-[13px] font-sans text-white focus:outline-none focus:border-[#e94560] focus:ring-1 focus:ring-[#e94560]/50 placeholder:text-slate-500" />
            </div>
          </div>

          {/* Deleted Items List */}
          <div className="bg-[#16213e] rounded-xl border border-[#0f3460] overflow-hidden shadow-lg shadow-black/20">
            <div className="hidden md:grid grid-cols-12 gap-4 border-b border-[#0f3460] bg-[#1f2b4a]/50 p-4 text-xs font-semibold text-slate-400 uppercase tracking-wider">
              <div className="col-span-5">名称</div>
              <div className="col-span-3">原始位置</div>
              <div className="col-span-2">删除时间</div>
              <div className="col-span-2 text-right">操作</div>
            </div>
            <div className="divide-y divide-[#0f3460]">
              {deletedFiles.map((file, i) => (
                <div key={i} className="grid grid-cols-1 md:grid-cols-12 gap-4 p-4 items-center hover:bg-[#1f2b4a] transition-colors group">
                  <div className="col-span-1 md:col-span-5 flex items-center gap-3">
                    <div className="w-10 h-10 rounded-lg bg-[#121316] border border-[#0f3460] flex items-center justify-center flex-shrink-0">
                      {file.type === 'wiki' ? (
                        <Network size={20} className="text-[#a9c8fc]" />
                      ) : (
                        <FileText size={20} className="text-slate-400" />
                      )}
                    </div>
                    <div>
                      <p className={`text-[14px] font-medium ${file.type === 'wiki' ? 'text-[#a9c8fc]' : 'text-[#e3e2e6]'} truncate`}>{file.name}</p>
                      <p className="text-xs text-slate-500 md:hidden mt-0.5">{file.origin} • {file.deletedAt}</p>
                      <p className="text-[11px] font-mono text-slate-500 mt-1">{file.size}</p>
                    </div>
                  </div>
                  <div className="hidden md:flex col-span-3 text-[13px] text-slate-400 items-center">
                    {file.origin}
                  </div>
                  <div className="hidden md:flex col-span-2 text-[13px] text-slate-400 items-center">
                    {file.deletedAt}
                  </div>
                  <div className="col-span-1 md:col-span-2 flex items-center justify-end gap-2">
                    <button className="px-3 py-1.5 rounded bg-[#121316] border border-[#0f3460] text-[#a9c8fc] hover:text-white hover:bg-[#0f3460] transition-colors text-xs font-medium flex items-center gap-1.5 invisible group-hover:visible opacity-0 group-hover:opacity-100">
                      <RefreshCw size={14} /> 恢复
                    </button>
                    <button className="p-1.5 rounded text-slate-500 hover:text-[#e94560] hover:bg-[#121316] transition-colors">
                      <MoreVertical size={18} />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

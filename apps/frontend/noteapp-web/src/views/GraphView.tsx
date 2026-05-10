import React from 'react';
import { Search, Plus, Minus, LocateFixed } from 'lucide-react';

export default function GraphView() {
  return (
    <div className="flex flex-1 h-full overflow-hidden bg-[#1a1a2e]">
      {/* Search Layout */}
      <aside className="w-full md:w-96 flex-shrink-0 border-r border-[#0f3460] bg-[#1a1c1f] flex flex-col h-full overflow-hidden z-20">
        <div className="p-4 border-b border-[#43474f]/30 flex justify-between items-center bg-[#1e2023]/50">
          <h2 className="text-[13px] font-medium text-slate-400 uppercase tracking-wider">搜索结果</h2>
          <span className="text-xs text-slate-500">找到12个匹配项</span>
        </div>
        <div className="flex-1 overflow-y-auto p-4 space-y-4 bg-[#121316]">
          {/* Result Card 1 */}
          <div className="bg-[#1f2b4a] rounded-lg p-4 border border-[#e94560]/30 shadow-md shadow-[#e94560]/5 cursor-pointer relative overflow-hidden">
            <div className="absolute left-0 top-0 bottom-0 w-1 bg-[#e94560]"></div>
            <div className="flex justify-between items-start mb-2">
              <h3 className="text-[15px] text-white font-semibold flex items-center">
                 <span className="text-[#e94560] bg-[#e94560]/10 px-1 rounded ml-1">神经网络</span> 简介
              </h3>
              <span className="text-[10px] font-mono text-[#e94560] bg-[#e94560]/10 px-1.5 py-0.5 rounded">匹配度 98%</span>
            </div>
            <p className="text-[13px] text-slate-400 line-clamp-3 leading-relaxed">
              ...从根本上说，<span className="text-[#e94560] bg-[#e94560]/10 px-0.5 rounded">神经网络</span>由按层组织的互连节点（神经元）组成。激活函数决定了在给定输入的情况下节点的输出...
            </p>
            <div className="mt-3 flex flex-wrap gap-2">
              <span className="text-[11px] font-medium text-slate-300 bg-[#0f3460]/50 border border-[#0f3460] px-2 py-0.5 rounded-full">人工智能</span>
              <span className="text-[11px] font-medium text-slate-300 bg-[#0f3460]/50 border border-[#0f3460] px-2 py-0.5 rounded-full">机器学习</span>
            </div>
          </div>

          <div className="bg-[#1e2023] rounded-lg p-4 border border-[#43474f]/30 hover:bg-[#1f2b4a] cursor-pointer">
             <div className="flex justify-between items-start mb-2">
              <h3 className="text-[15px] text-[#e3e2e6] font-semibold flex items-center">
                 反向传播算法
              </h3>
              <span className="text-[10px] font-mono text-slate-400 bg-[#38393d] px-1.5 py-0.5 rounded">匹配度 85%</span>
            </div>
             <p className="text-[13px] text-slate-400 line-clamp-2 leading-relaxed">
              ...是训练人工<span className="text-white bg-white/10 px-0.5 rounded">神经网络</span>的一种基本方法。它计算误差函数相对于网络权重的梯度...
            </p>
          </div>

          <div className="bg-[#1e2023] rounded-lg p-4 border border-[#43474f]/30 hover:bg-[#1f2b4a] cursor-pointer">
             <div className="flex justify-between items-start mb-2">
              <h3 className="text-[15px] text-[#e3e2e6] font-semibold flex items-center">
                 深度学习历史
              </h3>
              <span className="text-[10px] font-mono text-slate-400 bg-[#38393d] px-1.5 py-0.5 rounded">匹配度 72%</span>
            </div>
             <p className="text-[13px] text-slate-400 line-clamp-2 leading-relaxed">
              ...从感知器到复杂多层<span className="text-white bg-white/10 px-0.5 rounded">神经网络</span>的演变，标志着人工智能研究的一个重要转折点...
            </p>
          </div>
        </div>
      </aside>

      {/* Graph Area */}
      <div className="hidden md:block flex-1 bg-[#0d0e11] relative overflow-hidden">
        {/* Background Texture */}
        <div className="absolute inset-0 opacity-[0.05]" style={{ backgroundImage: 'radial-gradient(#a9c8fc 1px, transparent 1px)', backgroundSize: '24px 24px' }}></div>
        
        {/* Graph Controls Float */}
        <div className="absolute bottom-6 right-6 flex flex-col space-y-2 z-10 bg-[#1e2023]/80 backdrop-blur border border-[#43474f]/30 rounded-lg p-1 shadow-lg">
          <button className="p-2 text-slate-400 hover:text-white hover:bg-white/10 rounded transition-colors" title="放大"><Plus size={16} /></button>
          <button className="p-2 text-slate-400 hover:text-white hover:bg-white/10 rounded transition-colors" title="缩小"><Minus size={16} /></button>
          <div className="h-px bg-[#43474f]/30 my-1 w-full"></div>
          <button className="p-2 text-slate-400 hover:text-white hover:bg-white/10 rounded transition-colors" title="居中"><LocateFixed size={16} /></button>
        </div>

        {/* SVG Edges */}
        <svg className="absolute inset-0 w-full h-full pointer-events-none" style={{zIndex: 0}}>
          <g stroke="#0f3460" strokeOpacity="0.6" strokeWidth="1.5">
            <line x1="50%" y1="50%" x2="30%" y2="20%"></line>
            <line x1="50%" y1="50%" x2="70%" y2="30%"></line>
            <line x1="50%" y1="50%" x2="65%" y2="75%"></line>
            <line x1="50%" y1="50%" x2="25%" y2="60%"></line>
            <line x1="30%" y1="20%" x2="20%" y2="10%" strokeOpacity="0.3" strokeWidth="1"></line>
            <line x1="70%" y1="30%" x2="85%" y2="25%" strokeOpacity="0.3" strokeWidth="1"></line>
          </g>
        </svg>

        {/* Nodes */}
        {/* Center Node */}
        <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 flex flex-col items-center group cursor-pointer z-10">
          <div className="w-4 h-4 rounded-full bg-[#e94560] shadow-[0_0_20px_rgba(233,69,96,0.8)] border-2 border-[#121316] ring-2 ring-[#e94560]/30 transform scale-125"></div>
           <span className="mt-2 text-[12px] font-bold text-white bg-[#121316]/80 px-2 py-1 rounded backdrop-blur whitespace-nowrap border border-[#e94560]/50 shadow-lg">神经网络简介</span>
        </div>
        
        {/* Peripheral Nodes */}
        <div className="absolute left-[30%] top-[20%] -translate-x-1/2 -translate-y-1/2 flex flex-col items-center cursor-pointer">
          <div className="w-3 h-3 rounded-full bg-[#0f3460] border-2 border-[#121316] hover:bg-[#a9c8fc] transition-colors shadow-[0_0_10px_rgba(15,52,96,0.5)]"></div>
          <span className="mt-2 text-[11px] text-slate-400 bg-[#121316]/80 px-2 py-0.5 rounded backdrop-blur whitespace-nowrap">反向传播</span>
        </div>
        <div className="absolute left-[70%] top-[30%] -translate-x-1/2 -translate-y-1/2 flex flex-col items-center cursor-pointer">
          <div className="w-3 h-3 rounded-full bg-[#0f3460] border-2 border-[#121316] hover:bg-[#a9c8fc] transition-colors shadow-[0_0_10px_rgba(15,52,96,0.5)]"></div>
          <span className="mt-2 text-[11px] text-slate-400 bg-[#121316]/80 px-2 py-0.5 rounded backdrop-blur whitespace-nowrap">激活函数</span>
        </div>
        <div className="absolute left-[65%] top-[75%] -translate-x-1/2 -translate-y-1/2 flex flex-col items-center cursor-pointer">
          <div className="w-3 h-3 rounded-full bg-[#0f3460] border-2 border-[#121316] hover:bg-[#a9c8fc] transition-colors shadow-[0_0_10px_rgba(15,52,96,0.5)]"></div>
          <span className="mt-2 text-[11px] text-slate-400 bg-[#121316]/80 px-2 py-0.5 rounded backdrop-blur whitespace-nowrap">深度学习历史</span>
        </div>
        <div className="absolute left-[25%] top-[60%] -translate-x-1/2 -translate-y-1/2 flex flex-col items-center cursor-pointer">
          <div className="w-3 h-3 rounded-full bg-[#0f3460] border-2 border-[#121316] hover:bg-[#a9c8fc] transition-colors shadow-[0_0_10px_rgba(15,52,96,0.5)]"></div>
          <span className="mt-2 text-[11px] text-slate-400 bg-[#121316]/80 px-2 py-0.5 rounded backdrop-blur whitespace-nowrap">感知器</span>
        </div>
      </div>
    </div>
  );
}

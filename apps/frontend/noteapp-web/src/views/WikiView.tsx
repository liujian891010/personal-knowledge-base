import React from 'react';
import { Brain, Filter, PlusCircle, Link as LinkIcon, Network, Send, FileText } from 'lucide-react';

export default function WikiView() {
  const wikis = [
    { title: '架构 V2', desc: '综合了向微前端架构过渡的文档。包括依赖关系图、迁移时间表和 API 协议草案...', sources: 24, time: '2小时前', status: '活跃', color: 'text-[#e94560]', bg: 'bg-[#e94560]/10' },
    { title: '用户画像', desc: '基于 Q3 用户访谈的汇总行为偏好。突出了培训流程中的摩擦点，并按用户细分映射了期望的功能。', sources: 12, time: '1天前', status: '', color: 'text-[#ffb782]', bg: 'bg-[#ffb782]/10' },
    { title: '技术栈', desc: '跟踪库版本、构建工具配置和最近采用的技术的理由的活动文档。包含自动提取的代码片段...', sources: 8, time: '4小时前', status: '', color: 'text-[#ffb2b7]', bg: 'bg-[#ffb2b7]/10' }
  ];

  return (
    <div className="flex h-full bg-[#1a1a2e] overflow-hidden">
      <div className="flex-1 flex flex-col min-w-0 bg-[#121316]">
        {/* Header */}
        <div className="px-6 py-6 border-b border-[#292a2d] bg-[#16213e] sticky top-0 z-10 shadow-lg shadow-black/40">
          <div className="flex justify-between items-end">
            <div>
              <h1 className="text-3xl font-bold text-[#e3e2e6] flex items-center gap-3">
                <Brain className="text-[#e94560]" size={32} />
                AI Wiki 库
              </h1>
              <p className="text-base text-slate-400 mt-2 max-w-2xl">从您的个人笔记和外部附件中合成的自动生成、深度链接的知识库。</p>
            </div>
            <div className="flex gap-2">
              <button className="bg-[#1e2023] hover:bg-[#292a2d] border border-[#43474f] text-[#e3e2e6] text-[13px] font-medium px-4 py-2 rounded flex items-center gap-2 transition-colors">
                <Filter size={16} /> 筛选
              </button>
              <button className="bg-[#0f3460] hover:bg-[#14427a] text-white text-[13px] font-medium px-4 py-2 rounded flex items-center gap-2 transition-colors">
                <PlusCircle size={16} /> 新建主题
              </button>
            </div>
          </div>
        </div>

        {/* Content */}
        <div className="p-6 overflow-y-auto flex-1">
          <div className="grid grid-cols-1 xl:grid-cols-2 2xl:grid-cols-3 gap-6">
            {wikis.map((wiki, i) => (
              <div key={i} className="group relative bg-[#1e2023] rounded-lg border border-[#43474f] hover:border-[#8d919a] transition-all duration-300 overflow-hidden flex flex-col h-64 cursor-pointer">
                {wiki.status && <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-[#e94560] to-transparent opacity-70"></div>}
                <div className="p-6 flex-1 flex flex-col bg-gradient-to-br from-[#16213e]/40 to-transparent">
                  <div className="flex justify-between items-start mb-4">
                    <div className={`w-10 h-10 rounded ${wiki.bg} border border-[#0f3460] flex items-center justify-center ${wiki.color} group-hover:scale-110 transition-transform`}>
                      <Network size={20} />
                    </div>
                    {wiki.status && (
                      <span className="bg-[#0f3460]/30 border border-[#0f3460] text-[#e94560] text-[11px] px-2 py-0.5 rounded font-mono uppercase tracking-wider flex items-center gap-1.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-[#e94560] animate-pulse"></span> {wiki.status}
                      </span>
                    )}
                  </div>
                  <h3 className={`text-xl font-bold mb-2 transition-colors ${wiki.status ? 'group-hover:text-[#e94560]' : 'group-hover:text-white'}`}>{wiki.title}</h3>
                  <p className="text-[15px] text-slate-400 line-clamp-3 leading-relaxed">{wiki.desc}</p>
                  <div className="mt-auto pt-4 flex items-center justify-between text-slate-500 font-mono text-[13px]">
                    <div className="flex items-center gap-2">
                      <LinkIcon size={14} /> {wiki.sources} 个来源
                    </div>
                    <div>同步于 {wiki.time}</div>
                  </div>
                </div>
              </div>
            ))}
            <div className="bg-[#121316] border-2 border-dashed border-[#43474f] rounded-lg hover:border-[#a9c8fc] hover:bg-[#1a1c1f] transition-colors duration-200 flex flex-col items-center justify-center h-64 cursor-pointer text-[#8d919a] hover:text-[#a9c8fc]">
              <PlusCircle size={48} className="mb-2 opacity-50" />
              <span className="text-[13px] font-medium">初始化新的 Wiki</span>
            </div>
          </div>
        </div>
      </div>

      {/* Right Sidebar Copilot */}
      <aside className="w-80 bg-[#1a1c1f] flex flex-col border-l border-[#292a2d]">
        <div className="h-14 border-b border-[#292a2d] flex items-center px-4 justify-between bg-[#1e2023]/50">
           <div className="flex items-center gap-2 text-white text-[13px] font-medium">
             <Brain className="text-[#e94560]" size={18} /> 副驾驶
           </div>
        </div>
        <div className="flex-1 p-4 overflow-y-auto flex flex-col gap-4">
          <div className="bg-[#121316] border border-[#292a2d] rounded-lg p-3 rounded-tl-sm shadow-md">
            <p className="text-[14px] text-[#e3e2e6] leading-relaxed">我已经从“架构 V2”会议中综合了您的笔记。您想让我提取待办事项还是生成图表草稿？</p>
          </div>
          <div className="flex flex-col gap-2 mt-2">
            <button className="bg-[#0f3460]/30 hover:bg-[#0f3460]/50 border border-[#0f3460] text-[#a9c8fc] text-[13px] font-medium py-2 px-3 rounded text-left transition-colors flex items-center gap-2">
              <FileText size={14} className="text-[#a9c8fc]" /> 提取待办事项
            </button>
            <button className="bg-[#0f3460]/30 hover:bg-[#0f3460]/50 border border-[#0f3460] text-[#a9c8fc] text-[13px] font-medium py-2 px-3 rounded text-left transition-colors flex items-center gap-2">
              <Network size={14} className="text-[#a9c8fc]" /> 生成图表
            </button>
          </div>
        </div>
        <div className="p-4 border-t border-[#292a2d] bg-[#1e2023]">
          <div className="bg-[#121316] border border-[#292a2d] rounded-lg flex items-center p-1 focus-within:border-[#e94560] transition-colors">
            <input type="text" placeholder="询问副驾驶..." className="flex-1 bg-transparent border-none text-[14px] text-white focus:ring-0 px-2 py-1 placeholder:text-slate-500 outline-none" />
            <button className="w-8 h-8 rounded hover:bg-[#333538] flex items-center justify-center text-[#e94560] transition-colors">
              <Send size={18} />
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}

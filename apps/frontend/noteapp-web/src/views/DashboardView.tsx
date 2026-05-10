import React from 'react';
import { Bolt, Sparkles, Link as LinkIcon } from 'lucide-react';

export default function DashboardView() {
  const recentDocs = [
    { title: '同步策略草案', summary: '在尝试与中央笔记库服务器合并之前，探索不同的方法来处理离线修改。', time: '2小时前', tag: '架构' },
    { title: '会议记录：UI V2', summary: '设计同步的关键要点。我们需要专注于通过色调分层来提高深度，并避免使用通用的灰色。', time: '5小时前', tag: '设计' },
    { title: '待办事项：Beta发布', summary: '- 确定深色模式颜色\n- 测试Markdown解析器的边界情况\n- 撰写发行说明', time: '昨天', tag: '计划' },
    { title: '想法：语义搜索', summary: '如果我们使用小型的LLM模型在本地索引笔记库，用户就可以通过概念搜索，而不是精确的关键字，怎么样？', time: '2天前', tag: 'AI' },
  ];

  return (
    <div className="h-full overflow-y-auto p-6 lg:p-8 shrink-0">
      <div className="max-w-5xl mx-auto space-y-8 pb-24 md:pb-0">
        <section>
          <h2 className="text-3xl font-bold text-[#e3e2e6] mb-2 font-sans tracking-tight">早上好</h2>
          <p className="text-slate-400 text-base">这是您今天笔记库的最新动态。</p>
        </section>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="bg-[#16213e] rounded-xl border border-[#0f3460] p-6 flex flex-col shadow-lg shadow-black/20">
            <div className="flex items-center space-x-2 mb-4 text-slate-300">
              <Bolt size={20} className="text-[#e94560]" />
              <h3 className="text-lg font-semibold text-[#e3e2e6]">快速记录</h3>
            </div>
            <textarea
              className="w-full bg-[#121316] border border-[#0f3460] rounded-lg p-3 font-mono text-[13px] text-[#e3e2e6] focus:outline-none focus:border-[#e94560] focus:ring-1 focus:ring-[#e94560]/30 transition-all resize-none flex-1 min-h-[120px] placeholder:text-slate-600"
              placeholder="记录一闪而过的想法..."
            ></textarea>
            <div className="flex justify-end mt-4">
              <button className="bg-[#e94560] text-white px-4 py-1.5 rounded-lg text-sm font-medium hover:brightness-105 transition-all bg-gradient-to-b from-white/10 to-transparent">
                保存
              </button>
            </div>
          </div>

          <div className="md:col-span-2 rounded-xl border border-[#e94560]/30 p-6 flex flex-col shadow-lg shadow-black/20 ai-border relative overflow-hidden">
             <div className="absolute top-0 left-0 w-1 bottom-0 bg-[#e94560]"></div>
            <div className="flex items-center space-x-2 mb-4">
              <Sparkles size={20} className="text-[#e94560]" />
              <h3 className="text-lg font-semibold text-[#e3e2e6]">AI 洞察</h3>
            </div>
            <p className="text-[15px] text-slate-300 mb-6 leading-relaxed">
              您在过去48小时内高度关注<span className="text-[#e94560] font-medium mx-1">本地优先架构</span>。我发现了一些关于冲突解决和离线优先同步策略的重复主题。
            </p>
            <div className="mt-auto space-y-3">
              <h4 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider">建议连接</h4>
              <div className="flex flex-wrap gap-2">
                <button className="bg-[#0f3460]/30 border border-[#0f3460] rounded-full px-3 py-1.5 text-xs text-[#a9c8fc] hover:bg-[#1f2b4a] transition-colors flex items-center space-x-1.5">
                  <LinkIcon size={12} />
                  <span>CRDT 实现</span>
                </button>
                <button className="bg-[#0f3460]/30 border border-[#0f3460] rounded-full px-3 py-1.5 text-xs text-[#a9c8fc] hover:bg-[#1f2b4a] transition-colors flex items-center space-x-1.5">
                  <LinkIcon size={12} />
                  <span>IndexedDB 限制</span>
                </button>
              </div>
            </div>
          </div>
        </div>

        <section>
          <div className="flex items-center justify-between mb-4">
            <h3 className="text-xl font-bold text-[#e3e2e6]">最近编辑</h3>
            <a href="#" className="text-[#e94560] text-sm hover:text-white transition-colors">查看全部</a>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {recentDocs.map((doc, i) => (
              <div key={i} className="bg-[#16213e] rounded-xl border border-[#0f3460] p-4 hover:bg-[#1f2b4a] transition-colors duration-200 cursor-pointer group flex flex-col h-44">
                <h4 className="text-base font-semibold text-[#e3e2e6] mb-2 group-hover:text-[#e94560] transition-colors line-clamp-1 truncate">{doc.title}</h4>
                <p className="text-sm text-slate-400 line-clamp-3 mb-auto leading-relaxed whitespace-pre-wrap">{doc.summary}</p>
                <div className="flex items-center justify-between mt-4 text-slate-500 font-mono text-[11px]">
                  <span>{doc.time}</span>
                  <div className="bg-[#0f3460]/30 border border-[#0f3460] rounded px-2 py-0.5 text-[#a9c8fc]">{doc.tag}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

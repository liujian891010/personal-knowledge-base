import React, { useState } from 'react';
import { Settings, Cloud, Palette, Bot, Key, Laptop, Smartphone as Phone, Monitor, ChevronRight } from 'lucide-react';

export default function SettingsView() {
  const [activeTab, setActiveTab] = useState('sync');

  return (
    <div className="flex flex-col h-full bg-[#1a1a2e] overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 md:p-8 lg:p-12 flex justify-center pb-20">
        <div className="w-full max-w-5xl flex flex-col md:flex-row gap-8">
          {/* Settings Navigation Sidebar */}
          <aside className="w-full md:w-64 flex-shrink-0">
            <h1 className="text-3xl font-bold text-[#e3e2e6] mb-8">设置</h1>
            <nav className="flex flex-col gap-2">
              <button 
                onClick={() => setActiveTab('general')}
                className={`w-full px-4 py-3 rounded-lg text-[13px] font-medium flex items-center gap-3 transition-colors ${activeTab === 'general' ? 'bg-[#0f3460]/20 text-[#e94560] border-l-2 border-[#e94560]' : 'text-slate-300 hover:bg-[#1f2b4a]'}`}
              >
                <Settings size={20} /> 常规
              </button>
              <button 
                onClick={() => setActiveTab('sync')}
                className={`w-full px-4 py-3 rounded-lg text-[13px] font-medium flex items-center gap-3 transition-colors ${activeTab === 'sync' ? 'bg-[#0f3460]/20 text-[#e94560] border-l-2 border-[#e94560]' : 'text-slate-300 hover:bg-[#1f2b4a]'}`}
              >
                <Cloud size={20} /> 同步
              </button>
              <button 
                onClick={() => setActiveTab('appearance')}
                className={`w-full px-4 py-3 rounded-lg text-[13px] font-medium flex items-center gap-3 transition-colors ${activeTab === 'appearance' ? 'bg-[#0f3460]/20 text-[#e94560] border-l-2 border-[#e94560]' : 'text-slate-300 hover:bg-[#1f2b4a]'}`}
              >
                <Palette size={20} /> 外观
              </button>
              <button 
                onClick={() => setActiveTab('ai')}
                className={`w-full px-4 py-3 rounded-lg text-[13px] font-medium flex items-center gap-3 transition-colors ${activeTab === 'ai' ? 'bg-[#0f3460]/20 text-[#e94560] border-l-2 border-[#e94560]' : 'text-slate-300 hover:bg-[#1f2b4a]'}`}
              >
                <Bot size={20} /> AI 模型
              </button>
            </nav>
          </aside>

          {/* Settings Content Panel */}
          <div className="flex-1 flex flex-col gap-8">
            
            {activeTab === 'sync' && (
              <>
                <header className="border-b border-[#0f3460] pb-4">
                  <h2 className="text-2xl font-bold text-[#e3e2e6]">同步配置</h2>
                  <p className="text-base text-slate-400 mt-2">管理您的本地优先同步和连接设备。</p>
                </header>

                <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                  {/* Core Sync Toggle */}
                  <div className="bg-[#16213e] rounded-xl p-4 md:p-6 border border-[#0f3460] shadow-lg shadow-black/20 flex flex-col justify-between col-span-1 xl:col-span-2 relative overflow-hidden">
                     <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-[#e94560] to-transparent opacity-50"></div>
                     <div className="flex items-start justify-between">
                        <div>
                           <h3 className="text-[14px] font-bold text-[#e3e2e6] flex items-center gap-2">
                              <Key className="text-[#e94560]" size={18} /> 端到端加密
                           </h3>
                           <p className="text-[14px] leading-relaxed text-slate-400 mt-2 max-w-lg">您的笔记库在同步之前会在本地加密。NoteAI 无法读取您的笔记。您完全控制密钥。</p>
                        </div>
                        <label className="relative inline-flex items-center cursor-pointer">
                           <input type="checkbox" defaultChecked className="sr-only peer" />
                           <div className="w-11 h-6 bg-[#121316] border border-[#0f3460] rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[3px] after:left-[3px] after:bg-slate-400 peer-checked:after:bg-white after:border-none after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#e94560] peer-checked:border-[#e94560]"></div>
                        </label>
                     </div>
                     <div className="mt-6 pt-4 border-t border-[#0f3460] flex flex-col sm:flex-row sm:items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                           <span className="inline-flex items-center px-2 py-0.5 bg-[#e94560]/10 border border-[#e94560]/30 rounded text-[#e94560] font-mono text-[11px] uppercase tracking-wider font-bold">已激活</span>
                           <span className="font-mono text-[11px] text-slate-500">上次同步: 2 分钟前</span>
                        </div>
                        <button className="text-[13px] font-medium text-[#a9c8fc] hover:text-white transition-colors bg-[#0f3460]/30 px-3 py-1.5 rounded border border-[#0f3460]">查看恢复密钥</button>
                     </div>
                  </div>

                  {/* Sync Server */}
                  <div className="bg-[#16213e] rounded-xl p-4 md:p-6 border border-[#0f3460] shadow-lg shadow-black/20 flex flex-col gap-4">
                     <div>
                        <h3 className="text-[14px] font-bold text-[#e3e2e6]">同步服务器</h3>
                        <p className="text-[13px] text-slate-400 mt-1">选择加密数据的存储位置。</p>
                     </div>
                     <div className="space-y-3 mt-2">
                        <label className="flex items-center gap-3 p-3 rounded-lg border border-[#e94560]/50 bg-[#1f2b4a]/30 cursor-pointer transition-colors relative overflow-hidden">
                           <div className="absolute left-0 top-0 bottom-0 w-1 bg-[#e94560]"></div>
                           <input type="radio" name="sync_server" defaultChecked className="text-[#e94560] focus:ring-[#e94560] bg-[#121316] border-[#0f3460]" />
                           <div className="flex flex-col ml-1">
                              <span className="text-[13px] font-semibold text-[#e3e2e6]">NoteAI 云端</span>
                              <span className="font-mono text-[11px] text-[#a9c8fc] mt-0.5">默认，托管存储</span>
                           </div>
                        </label>
                        <label className="flex items-center gap-3 p-3 rounded-lg border border-[#0f3460] bg-[#121316]/50 cursor-pointer hover:bg-[#1f2b4a] transition-colors">
                           <input type="radio" name="sync_server" className="text-[#e94560] focus:ring-[#e94560] bg-[#121316] border-[#0f3460]" />
                           <div className="flex flex-col">
                              <span className="text-[13px] font-semibold text-[#e3e2e6]">自定义 CouchDB</span>
                              <span className="font-mono text-[11px] text-slate-500 mt-0.5">自托管同步</span>
                           </div>
                        </label>
                     </div>
                  </div>

                  {/* AI Conflict Resolution */}
                  <div className="bg-[#16213e] rounded-xl p-4 md:p-6 border border-[#0f3460] shadow-lg shadow-black/20 flex flex-col gap-4">
                     <div className="flex items-start gap-3">
                        <Bot className="text-[#a9c8fc] mt-0.5" size={20} />
                        <div>
                            <h3 className="text-[14px] font-bold text-[#e3e2e6]">AI 冲突合并</h3>
                           <p className="text-[13px] text-slate-400 mt-1 leading-relaxed">允许本地 AI 利用语义理解智能合并轻微的文本冲突。</p>
                        </div>
                     </div>
                     <div className="mt-auto bg-[#121316] p-4 rounded-lg border border-[#0f3460] flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                        <div className="flex flex-col">
                           <span className="text-[11px] text-slate-500 font-bold uppercase tracking-wider">活动模型</span>
                           <span className="font-mono text-[12px] text-[#a9c8fc] mt-1">Llama-3-8B-Instruct</span>
                        </div>
                        <button className="text-[13px] font-medium text-[#e3e2e6] bg-[#1f2b4a] hover:bg-[#0f3460] transition-colors px-3 py-1.5 rounded border border-[#0f3460]">配置</button>
                     </div>
                  </div>
                </div>

                <div className="mt-8">
                  <h3 className="text-xl font-bold text-[#e3e2e6] mb-4">已连接的设备</h3>
                  <div className="bg-[#16213e] rounded-xl border border-[#0f3460] overflow-hidden shadow-lg shadow-black/20">
                    <div className="flex items-center justify-between p-4 md:p-5 border-b border-[#0f3460] hover:bg-[#1f2b4a] transition-colors">
                       <div className="flex items-center gap-4">
                          <div className="w-12 h-12 rounded-xl bg-[#121316] flex items-center justify-center border border-[#0f3460]">
                             <Laptop className="text-[#a9c8fc]" size={24} />
                          </div>
                          <div>
                             <h4 className="text-[14px] font-bold text-[#e3e2e6] flex items-center gap-2">
                               MacBook Pro (Work)
                               <span className="inline-flex items-center px-1.5 py-0.5 bg-[#e94560]/10 border border-[#e94560]/30 rounded text-[#e94560] font-mono text-[10px] uppercase font-bold">此设备</span>
                             </h4>
                             <p className="font-mono text-[12px] text-slate-500 mt-1">macOS • 刚刚同步</p>
                          </div>
                       </div>
                    </div>

                    <div className="flex items-center justify-between p-4 md:p-5 border-b border-[#0f3460] hover:bg-[#1f2b4a] transition-colors group">
                       <div className="flex items-center gap-4">
                          <div className="w-12 h-12 rounded-xl bg-[#121316] flex items-center justify-center border border-[#0f3460] group-hover:border-[#43474f] transition-colors">
                             <Phone className="text-slate-400 group-hover:text-[#e3e2e6] transition-colors" size={24} />
                          </div>
                          <div>
                             <h4 className="text-[14px] font-semibold text-[#e3e2e6]">iPhone 14 Pro</h4>
                             <p className="font-mono text-[12px] text-slate-500 mt-1">iOS • 2 小时前同步</p>
                          </div>
                       </div>
                       <button className="px-4 py-2 rounded-lg bg-[#121316] border border-[#0f3460] hover:border-[#e94560]/50 hover:text-[#e94560] text-slate-400 text-[13px] font-medium transition-all">撤销</button>
                    </div>

                    <div className="flex items-center justify-between p-4 md:p-5 hover:bg-[#1f2b4a] transition-colors group">
                       <div className="flex items-center gap-4">
                          <div className="w-12 h-12 rounded-xl bg-[#121316] flex items-center justify-center border border-[#0f3460] group-hover:border-[#43474f] transition-colors">
                             <Monitor className="text-slate-400 group-hover:text-[#e3e2e6] transition-colors" size={24} />
                          </div>
                          <div>
                             <h4 className="text-[14px] font-semibold text-[#e3e2e6]">Home PC</h4>
                             <p className="font-mono text-[12px] text-slate-500 mt-1">Windows 11 • 昨天同步</p>
                          </div>
                       </div>
                       <button className="px-4 py-2 rounded-lg bg-[#121316] border border-[#0f3460] hover:border-[#e94560]/50 hover:text-[#e94560] text-slate-400 text-[13px] font-medium transition-all">撤销</button>
                    </div>
                  </div>
                </div>
              </>
            )}

            {activeTab === 'appearance' && (
              <>
                <header className="border-b border-[#0f3460] pb-4">
                  <h2 className="text-2xl font-bold text-[#e3e2e6]">外观</h2>
                  <p className="text-base text-slate-400 mt-2">自定义本地笔记库的外观和风格。</p>
                </header>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  {/* Theme 1 */}
                  <div className="bg-[#16213e] rounded-xl border-2 border-[#e94560] p-4 cursor-pointer relative overflow-hidden">
                    <div className="absolute top-2 right-2 flex items-center justify-center w-5 h-5 bg-[#e94560] rounded-full">
                      <div className="w-2 h-2 bg-white rounded-full"></div>
                    </div>
                    <div className="h-24 rounded-lg bg-[#1a1a2e] border border-[#0f3460] mb-4 flex overflow-hidden">
                      <div className="w-1/4 h-full bg-[#16213e] border-r border-[#0f3460]"></div>
                      <div className="flex-1 h-full bg-[#121316] p-2 flex flex-col gap-2">
                         <div className="w-1/2 h-2 bg-[#e94560] rounded"></div>
                         <div className="w-full h-1.5 bg-[#43474f] rounded"></div>
                         <div className="w-3/4 h-1.5 bg-[#43474f] rounded"></div>
                      </div>
                    </div>
                    <h3 className="text-[14px] font-bold text-white">深邃海洋</h3>
                    <p className="text-[12px] text-slate-400 mt-1">默认的 NoteAI 深色模式。</p>
                  </div>
                  
                  {/* Theme 2 */}
                  <div className="bg-[#121316] rounded-xl border border-[#0f3460] p-4 cursor-pointer hover:border-[#43474f] transition-all">
                    <div className="h-24 rounded-lg bg-black border border-[#292a2d] mb-4 flex overflow-hidden">
                      <div className="w-1/4 h-full bg-[#0d0e11] border-r border-[#292a2d]"></div>
                      <div className="flex-1 h-full bg-black p-2 flex flex-col gap-2">
                         <div className="w-1/2 h-2 bg-white rounded"></div>
                         <div className="w-full h-1.5 bg-[#333538] rounded"></div>
                         <div className="w-3/4 h-1.5 bg-[#333538] rounded"></div>
                      </div>
                    </div>
                    <h3 className="text-[14px] font-bold text-slate-300">纯黑无垠</h3>
                    <p className="text-[12px] text-slate-500 mt-1">高对比度 OLED 优化。</p>
                  </div>

                  {/* Theme 3 */}
                  <div className="bg-white rounded-xl border border-slate-300 p-4 cursor-pointer hover:border-slate-400 transition-all">
                    <div className="h-24 rounded-lg bg-slate-50 border border-slate-200 mb-4 flex overflow-hidden">
                      <div className="w-1/4 h-full bg-slate-100 border-r border-slate-200"></div>
                      <div className="flex-1 h-full bg-white p-2 flex flex-col gap-2">
                         <div className="w-1/2 h-2 bg-blue-600 rounded"></div>
                         <div className="w-full h-1.5 bg-slate-300 rounded"></div>
                         <div className="w-3/4 h-1.5 bg-slate-300 rounded"></div>
                      </div>
                    </div>
                    <h3 className="text-[14px] font-bold text-slate-800">浅色模式</h3>
                    <p className="text-[12px] text-slate-500 mt-1">简洁明快。</p>
                  </div>
                </div>
              </>
            )}

            {activeTab === 'ai' && (
              <>
                <header className="border-b border-[#0f3460] pb-4">
                  <h2 className="text-2xl font-bold text-[#e3e2e6]">AI 模型</h2>
                  <p className="text-base text-slate-400 mt-2">管理本地 LLM、上下文窗口和图谱嵌入设置。</p>
                </header>
                <div className="bg-[#16213e] rounded-xl border border-[#0f3460] p-6 shadow-lg shadow-black/20">
                  <div className="flex items-center justify-between mb-6 border-b border-[#0f3460] pb-4">
                    <div>
                      <h4 className="text-[15px] font-bold text-white">语义搜索嵌入</h4>
                      <p className="text-[13px] text-slate-400 mt-1 mt-1">在后台本地运行以索引笔记。</p>
                    </div>
                    <button className="px-4 py-2 bg-[#0f3460] text-white rounded-lg text-[13px] font-medium border border-[#43474f]/50">MTEB/bge-small-en</button>
                  </div>
                  <div className="flex flex-col gap-4">
                    <div className="flex items-center justify-between">
                      <div>
                        <h4 className="text-[14px] font-semibold text-[#e3e2e6]">自动标记与关联</h4>
                        <p className="text-[13px] text-slate-500 mt-1">输入期间自动建议 [[wiki]] 链接。</p>
                      </div>
                      <label className="relative inline-flex items-center cursor-pointer">
                         <input type="checkbox" defaultChecked className="sr-only peer" />
                         <div className="w-11 h-6 bg-[#121316] border border-[#0f3460] rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[3px] after:left-[3px] after:bg-slate-400 peer-checked:after:bg-white after:border-none after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-[#e94560] peer-checked:border-[#e94560]"></div>
                      </label>
                    </div>
                  </div>
                </div>
              </>
            )}

            {activeTab === 'general' && (
              <>
                <header className="border-b border-[#0f3460] pb-4">
                  <h2 className="text-2xl font-bold text-[#e3e2e6]">常规设置</h2>
                  <p className="text-base text-slate-400 mt-2">应用程序首选项和笔记库管理。</p>
                </header>
                <div className="bg-[#16213e] rounded-xl border border-[#0f3460] overflow-hidden shadow-lg shadow-black/20">
                  <div className="p-6 border-b border-[#0f3460]">
                    <h3 className="text-[14px] font-bold text-white mb-4">笔记库位置</h3>
                    <div className="flex gap-3">
                      <input type="text" readOnly value="~/Documents/NoteAI/Personal_Vault" className="flex-1 bg-[#121316] border border-[#0f3460] rounded-lg px-4 py-2 font-mono text-[13px] text-slate-300 focus:outline-none" />
                      <button className="px-4 py-2 bg-[#1f2b4a] hover:bg-[#0f3460] border border-[#0f3460] text-white rounded-lg text-[13px] font-medium transition-colors">浏览</button>
                    </div>
                  </div>
                  <div className="p-6">
                    <h3 className="text-[14px] font-bold text-red-400 mb-2">危险区域</h3>
                    <p className="text-[13px] text-slate-400 mb-4">有关您的数据的不可逆转操作。</p>
                    <button className="px-4 py-2 bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/30 rounded-lg text-[13px] font-medium transition-colors">删除笔记库</button>
                  </div>
                </div>
              </>
            )}

          </div>
        </div>
      </div>
    </div>
  );
}


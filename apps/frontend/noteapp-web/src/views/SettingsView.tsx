import React, { useState } from 'react';
import {
  Activity,
  Bot,
  ChevronRight,
  Cloud,
  Palette,
  RefreshCw,
  Settings,
} from 'lucide-react';

import type { SyncShellAction, SyncShellLevel } from '../syncShell';
import { useSyncShellController } from '../useSyncShellSnapshot';

type SettingsTab = 'general' | 'sync' | 'appearance' | 'ai';

type SettingsViewProps = {
  initialTab?: SettingsTab;
};

const syncLevelClasses: Record<SyncShellLevel, string> = {
  success: 'text-emerald-300 bg-emerald-400/10 border-emerald-400/30',
  info: 'text-[#a9c8fc] bg-[#0f3460]/30 border-[#0f3460]',
  warning: 'text-[#ffb782] bg-[#ffb782]/10 border-[#ffb782]/30',
  danger: 'text-[#e94560] bg-[#e94560]/10 border-[#e94560]/30',
};

const tabs: Array<{ id: SettingsTab; label: string; icon: React.ComponentType<{ size?: number }> }> = [
  { id: 'general', label: 'General', icon: Settings },
  { id: 'sync', label: 'Sync', icon: Cloud },
  { id: 'appearance', label: 'Appearance', icon: Palette },
  { id: 'ai', label: 'AI Model', icon: Bot },
];

function formatActivityTime(ms: number): string {
  return new Intl.DateTimeFormat(undefined, {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(new Date(ms));
}

export default function SettingsView({ initialTab = 'sync' }: SettingsViewProps) {
  const [activeTab, setActiveTab] = useState<SettingsTab>(initialTab);
  const {
    summary: syncSummary,
    cards: syncCards,
    activityFeed,
    secondaryActions,
    source: syncSource,
    lastError,
    isExecuting,
    executingActionId,
    isRefreshing,
    refresh,
    executePrimaryAction,
    executeSyncAction,
  } = useSyncShellController();

  const renderActionButton = (
    action: SyncShellAction,
    variant: 'primary' | 'secondary' = 'secondary',
  ) => (
    <button
      key={action.action_id}
      disabled={!action.enabled || isExecuting || isRefreshing}
      onClick={() => executeSyncAction(action)}
      title={action.reason ?? (action.requires_confirmation ? 'Requires confirmation' : undefined)}
      className={`max-w-full truncate px-3 py-1.5 rounded border text-[13px] font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors ${
        variant === 'primary'
          ? 'bg-[#0f3460]/30 border-[#0f3460] text-[#a9c8fc] hover:text-white'
          : 'bg-[#121316] border-[#0f3460] text-slate-300 hover:text-white'
      }`}
    >
      {executingActionId === action.action_id ? 'Working...' : action.label}
    </button>
  );

  return (
    <div className="flex flex-col h-full bg-[#1a1a2e] overflow-hidden">
      <div className="flex-1 overflow-y-auto p-4 md:p-8 lg:p-12 flex justify-center pb-20">
        <div className="w-full max-w-5xl flex flex-col md:flex-row gap-8">
          <aside className="w-full md:w-64 flex-shrink-0">
            <h1 className="text-3xl font-bold text-[#e3e2e6] mb-8">Settings</h1>
            <nav className="flex flex-col gap-2">
              {tabs.map((tab) => (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`w-full px-4 py-3 rounded-lg text-[13px] font-medium flex items-center gap-3 transition-colors ${
                    activeTab === tab.id
                      ? 'bg-[#0f3460]/20 text-[#e94560] border-l-2 border-[#e94560]'
                      : 'text-slate-300 hover:bg-[#1f2b4a]'
                  }`}
                >
                  <tab.icon size={20} />
                  {tab.label}
                </button>
              ))}
            </nav>
          </aside>

          <div className="flex-1 flex flex-col gap-8">
            {activeTab === 'sync' && (
              <>
                <header className="border-b border-[#0f3460] pb-4">
                  <h2 className="text-2xl font-bold text-[#e3e2e6]">Sync Status</h2>
                  <p className="text-base text-slate-400 mt-2">
                    Live desktop sync state, executable actions, and recent activity.
                  </p>
                </header>

                <div className="bg-[#16213e] rounded-xl p-4 md:p-5 border border-[#0f3460] shadow-lg shadow-black/20 flex flex-col lg:flex-row lg:items-center gap-4">
                  <div className="w-11 h-11 rounded-lg bg-[#121316] border border-[#0f3460] flex items-center justify-center flex-shrink-0">
                    <Activity className="text-[#a9c8fc]" size={22} />
                  </div>
                  <div className="flex-1 min-w-0">
                    <div className="flex flex-wrap items-center gap-2 mb-1">
                      <span className={`inline-flex items-center px-2 py-0.5 rounded border font-mono text-[11px] uppercase tracking-wider font-bold ${syncLevelClasses[syncSummary.level]}`}>
                        {syncSummary.level}
                      </span>
                      <span className="font-mono text-[11px] text-slate-500 truncate">
                        {syncSummary.vaultId} / {syncSummary.deviceId}
                      </span>
                      <span className="font-mono text-[10px] text-slate-500 uppercase tracking-wider">
                        {syncSource}
                      </span>
                    </div>
                    <h3 className="text-[15px] font-bold text-[#e3e2e6] truncate">{syncSummary.headline}</h3>
                    <p className="text-[13px] text-slate-400 mt-1 line-clamp-2">{syncSummary.detail}</p>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mt-2 font-mono text-[11px] text-slate-500">
                      <span>Generated {formatActivityTime(syncSummary.generatedAtMs)}</span>
                      <span className="truncate" title={syncSummary.vaultRoot}>
                        {syncSummary.vaultRoot}
                      </span>
                    </div>
                    {lastError && (
                      <p className="text-[12px] text-[#ffb782] mt-2 line-clamp-2">{lastError}</p>
                    )}
                  </div>
                  <div className="flex flex-wrap lg:flex-nowrap items-center gap-2">
                    <span className="px-2 py-1 rounded bg-[#121316] border border-[#0f3460] font-mono text-[11px] text-slate-400">
                      {syncSummary.changeBadgeCount} changes
                    </span>
                    <span className="px-2 py-1 rounded bg-[#121316] border border-[#0f3460] font-mono text-[11px] text-slate-400">
                      {syncSummary.conflictBadgeCount} conflicts
                    </span>
                    <button
                      disabled={isRefreshing || isExecuting}
                      onClick={refresh}
                      title="Refresh sync status"
                      className="w-8 h-8 inline-flex items-center justify-center rounded bg-[#121316] border border-[#0f3460] text-slate-400 hover:text-white disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
                    >
                      <RefreshCw size={15} className={isRefreshing ? 'animate-spin' : ''} />
                    </button>
                    <button
                      disabled={!syncSummary.primaryActionEnabled || isExecuting || isRefreshing}
                      onClick={executePrimaryAction}
                      title={syncSummary.primaryActionRequiresConfirmation ? 'Requires confirmation' : undefined}
                      className="px-3 py-1.5 rounded bg-[#0f3460]/30 border border-[#0f3460] text-[13px] font-medium text-[#a9c8fc] disabled:opacity-50 disabled:cursor-not-allowed hover:text-white transition-colors"
                    >
                      {executingActionId === syncSummary.primaryActionId
                        ? 'Working...'
                        : syncSummary.primaryActionLabel}
                    </button>
                    {secondaryActions.map((action) => renderActionButton(action))}
                  </div>
                </div>

                <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
                  <div className="xl:col-span-2 flex flex-col gap-3">
                    <div className="flex items-center justify-between">
                      <h3 className="text-[15px] font-bold text-[#e3e2e6]">Sync cards</h3>
                      <span className="font-mono text-[11px] text-slate-500">{syncSummary.cardCount} cards</span>
                    </div>
                    {syncCards.length === 0 ? (
                      <div className="rounded-lg border border-[#0f3460] bg-[#16213e] p-4 text-[13px] text-slate-400">
                        No active sync cards.
                      </div>
                    ) : (
                      syncCards.map((card) => (
                        <div
                          key={card.card_id}
                          className="rounded-lg border border-[#0f3460] bg-[#16213e] p-4 shadow-lg shadow-black/20"
                        >
                          <div className="flex flex-col md:flex-row md:items-start justify-between gap-3">
                            <div className="min-w-0">
                              <div className="flex flex-wrap items-center gap-2 mb-2">
                                <span className={`inline-flex items-center px-2 py-0.5 rounded border font-mono text-[11px] uppercase tracking-wider font-bold ${syncLevelClasses[card.level]}`}>
                                  {card.level}
                                </span>
                                <span className="font-mono text-[11px] text-slate-500">{card.kind}</span>
                                {card.badge_count > 0 && (
                                  <span className="font-mono text-[11px] text-[#ffb782]">{card.badge_count}</span>
                                )}
                              </div>
                              <h4 className="text-[14px] font-bold text-[#e3e2e6] truncate">{card.title}</h4>
                              <p className="text-[13px] leading-relaxed text-slate-400 mt-1">{card.body}</p>
                            </div>
                            {card.actions.length > 0 && (
                              <div className="flex flex-wrap md:justify-end gap-2 md:max-w-xs">
                                {card.actions.map((action) =>
                                  renderActionButton(
                                    action,
                                    action.emphasis === 'primary' ? 'primary' : 'secondary',
                                  ),
                                )}
                              </div>
                            )}
                          </div>
                        </div>
                      ))
                    )}
                  </div>

                  <div className="rounded-lg border border-[#0f3460] bg-[#16213e] p-4 shadow-lg shadow-black/20">
                    <div className="flex items-center justify-between mb-3">
                      <h3 className="text-[15px] font-bold text-[#e3e2e6]">Activity</h3>
                      <span className="font-mono text-[11px] text-slate-500">{activityFeed.total_count} total</span>
                    </div>
                    <div className="flex flex-col divide-y divide-[#0f3460]">
                      {activityFeed.records.length === 0 ? (
                        <p className="text-[13px] text-slate-400">No sync activity yet.</p>
                      ) : (
                        activityFeed.records.map((record) => (
                          <div key={record.activity_id} className="py-3 first:pt-0 last:pb-0">
                            <div className="flex items-center justify-between gap-2">
                              <span className={`inline-flex items-center px-2 py-0.5 rounded border font-mono text-[10px] uppercase tracking-wider font-bold ${syncLevelClasses[record.level]}`}>
                                {record.status}
                              </span>
                              <span className="font-mono text-[10px] text-slate-500">
                                {formatActivityTime(record.occurred_at_ms)}
                              </span>
                            </div>
                            <p className="text-[13px] text-[#e3e2e6] mt-2 truncate">{record.action_id}</p>
                            <div className="flex items-center gap-1 text-[11px] text-slate-500 font-mono mt-1 min-w-0">
                              <span className="truncate">{record.command}</span>
                              <ChevronRight size={12} className="flex-shrink-0" />
                              <span className="truncate">{record.source}</span>
                            </div>
                            {record.message && (
                              <p className="text-[12px] text-[#ffb782] mt-1 line-clamp-2">{record.message}</p>
                            )}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              </>
            )}

            {activeTab === 'appearance' && (
              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-6 shadow-lg shadow-black/20">
                <h2 className="text-2xl font-bold text-[#e3e2e6]">Appearance</h2>
                <p className="text-[13px] text-slate-400 mt-2">
                  Theme controls will be wired after the sync shell integration is complete.
                </p>
              </section>
            )}

            {activeTab === 'ai' && (
              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-6 shadow-lg shadow-black/20">
                <h2 className="text-2xl font-bold text-[#e3e2e6]">AI Model</h2>
                <p className="text-[13px] text-slate-400 mt-2">
                  Local model configuration remains a placeholder until the AI runtime boundary lands.
                </p>
              </section>
            )}

            {activeTab === 'general' && (
              <section className="border border-[#0f3460] rounded-xl bg-[#16213e] p-6 shadow-lg shadow-black/20">
                <h2 className="text-2xl font-bold text-[#e3e2e6]">General</h2>
                <p className="text-[13px] text-slate-400 mt-2">
                  General vault preferences will be connected after the desktop settings contract is defined.
                </p>
              </section>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

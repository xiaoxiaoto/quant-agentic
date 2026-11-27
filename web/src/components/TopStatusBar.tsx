import { Pause, Play, RefreshCw, ShieldAlert, UserRound } from "lucide-react";
import type { JDAuthStatusResponse, LiveStatusResponse } from "../api/types";
import { RISK_STYLE } from "../riskStatus";
import { formatBeijingTime } from "../quotes";
import { WorkspaceNav, type WorkspaceTab } from "./WorkspaceNav";

interface TopStatusBarProps {
  activeTab: WorkspaceTab;
  onChangeTab: (tab: WorkspaceTab) => void;
  liveStatus: LiveStatusResponse | null;
  jdAuth: JDAuthStatusResponse | null;
  liveFailed: boolean;
  liveUpdatedAt?: string;
  autoRefresh: boolean;
  lastUpdated: string | null;
  refreshing: boolean;
  killBusy: boolean;
  newsFlags: number;
  onToggleAutoRefresh: () => void;
  onRefresh: () => void;
  onToggleKillSwitch: () => void;
}

export function TopStatusBar({
  activeTab, onChangeTab, liveStatus, jdAuth, liveFailed, liveUpdatedAt, autoRefresh, lastUpdated, refreshing,
  killBusy, newsFlags, onToggleAutoRefresh, onRefresh, onToggleKillSwitch,
}: TopStatusBarProps) {
  const risk = liveStatus && RISK_STYLE[liveStatus.risk_state];
  const killActive = liveStatus?.kill_switch_active === true;
  const canResume = killActive && !liveFailed;
  const riskLabel = liveFailed
    ? risk ? `上次已知状态 · ${risk.label}` : "风控状态未知"
    : risk?.label ?? (liveStatus ? "风控状态未知" : "风控读取中");
  const session = liveStatus && !liveFailed ? liveStatus.is_trading_session ? "交易时段内" : "休市" : null;
  const jdLoggedIn = jdAuth?.logged_in ?? liveStatus?.jd_logged_in ?? false;
  const jdExpired = liveStatus?.jd_token_expired === true;
  const jdReauth = jdAuth?.reauth_recommended === true;
  const jdLabel = jdExpired
    ? "京东登录已过期"
    : jdLoggedIn
      ? jdReauth
        ? "京东即将到期"
        : "京东已登录"
      : "京东未登录";
  const jdTone = jdExpired
    ? "text-rose-400 font-semibold"
    : jdLoggedIn
      ? jdReauth
        ? "text-amber-200"
        : "text-emerald-300"
      : "text-slate-300";

  return (
    <header className="workspace-header">
      <WorkspaceNav activeTab={activeTab} onChangeTab={onChangeTab} pending={liveStatus?.pending_alerts ?? 0} newsFlags={newsFlags} />
      <div className="system-strip">
        <div className="workspace-width flex min-h-11 flex-wrap items-center justify-between gap-x-4 gap-y-1 py-1">
          <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-300">
            <span className={`inline-flex items-center gap-1.5 font-semibold whitespace-nowrap ${liveFailed ? "text-slate-300" : risk?.tone ?? ""}`}
              title={liveFailed && liveUpdatedAt ? `上次读取于 ${formatBeijingTime(liveUpdatedAt)} 北京时间` : undefined}>
              <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
              {riskLabel}
            </span>
            {killActive ? (
              <span className={`whitespace-nowrap ${liveFailed ? "text-slate-300" : "text-rose-200"}`}>{liveFailed ? "上次已知 · 急停已启用" : "急停已启用"}</span>
            ) : liveFailed ? <span className="whitespace-nowrap">急停状态未知</span> : session && <span className="whitespace-nowrap">{session}</span>}
            <button
              type="button"
              onClick={() => onChangeTab("accounts")}
              className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 whitespace-nowrap transition hover:bg-white/5 ${jdTone}`}
              title="前往账号管理"
            >
              <UserRound className="size-3" aria-hidden="true" />
              <span>{jdLabel}</span>
            </button>
          </div>

          <div className="flex flex-wrap items-center gap-1 text-xs text-slate-300 shrink-0">
            {lastUpdated && (
              <span className="mr-2 hidden tabular-nums whitespace-nowrap sm:inline">页面刷新 <time dateTime={lastUpdated} title="北京时间">{formatBeijingTime(lastUpdated)}</time></span>
            )}
            <button type="button" onClick={onToggleAutoRefresh} aria-label="自动刷新" aria-pressed={autoRefresh}
              title={autoRefresh ? "暂停每 10 秒自动刷新" : "开启每 10 秒自动刷新"} className="quiet-button min-h-8 px-2 whitespace-nowrap shrink-0">
              {autoRefresh ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
              <span className="hidden sm:inline">{autoRefresh ? "10 秒" : "已暂停"}</span>
            </button>
            <button type="button" onClick={onRefresh} aria-label="立即刷新" title="立即刷新" className="quiet-button min-h-8 px-2 shrink-0">
              <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />
            </button>
            <button type="button" onClick={onToggleKillSwitch} disabled={killBusy}
              className={`quiet-button min-h-8 border border-transparent px-2 whitespace-nowrap shrink-0 ${canResume ? "" : "border-rose-300/25 bg-rose-400/8 text-rose-200"}`}>
              <ShieldAlert className="size-3.5" />
              {killBusy ? "处理中…" : canResume ? "解除急停" : "急停"}
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}

import { Lock, Plus, RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import { useMemo, useState } from "react";
import type { ModelStatusResponse, NewsOverviewResponse } from "../api/types";

interface NewsViewProps {
  newsOverview: NewsOverviewResponse | null;
  syncStatus?: ModelStatusResponse["news_sync"];
  busyAction?: string | null;
  refreshing: boolean;
  onRefresh: () => void;
  onRegisterMacro: (payload: {
    event_name: string;
    scheduled_at: string;
    lock_window_hours_before: number;
    lock_window_hours_after: number;
  }) => Promise<boolean>;
}

const TYPE_BADGE: Record<string, { label: string; cls: string }> = {
  GEOPOLITICAL: { label: "地缘政治", cls: "border-rose-500/40 bg-rose-500/15 text-rose-300" },
  MONETARY: { label: "货币政策", cls: "border-amber-500/40 bg-amber-500/15 text-amber-300" },
  MACRO: { label: "宏观经济", cls: "border-sky-500/40 bg-sky-500/15 text-sky-300" },
  MARKET: { label: "市场行情", cls: "border-violet-500/40 bg-violet-500/15 text-violet-300" },
  GENERAL: { label: "综合资讯", cls: "border-white/10 bg-white/[0.03] text-slate-300" },
};

export function NewsView({
  newsOverview,
  syncStatus,
  busyAction = null,
  refreshing,
  onRefresh,
  onRegisterMacro,
}: NewsViewProps) {
  const [typeFilter, setTypeFilter] = useState<string>("ALL");
  const [highRiskOnly, setHighRiskOnly] = useState(false);

  const [eventName, setEventName] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [hoursBefore, setHoursBefore] = useState("2");
  const [hoursAfter, setHoursAfter] = useState("2");
  const [submittingMacro, setSubmittingMacro] = useState(false);
  const [macroError, setMacroError] = useState<string | null>(null);

  const events = newsOverview?.news_events ?? [];
  const macros = newsOverview?.macro_events ?? [];
  const activeFlags = newsOverview?.active_risk_flags ?? [];

  const filteredEvents = useMemo(() => {
    return events.filter((ev) => {
      if (typeFilter !== "ALL" && ev.event_type !== typeFilter) return false;
      if (highRiskOnly && ev.severity < 0.7 && ev.risk_flags.length === 0) return false;
      return true;
    });
  }, [events, typeFilter, highRiskOnly]);

  const handleMacroSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!eventName.trim() || !scheduledAt) return;
    setMacroError(null);
    const hasTz = /(?:Z|[+-]\d{2}:\d{2})$/i.test(scheduledAt);
    const normalized = hasTz
      ? scheduledAt
      : scheduledAt.length === 16
        ? `${scheduledAt}:00+08:00`
        : `${scheduledAt}+08:00`;
    const parsedDate = new Date(normalized);
    if (Number.isNaN(parsedDate.getTime())) {
      setMacroError("请输入有效的日程时间");
      return;
    }
    const parsedBefore = hoursBefore.trim() === "" ? 2 : Number(hoursBefore);
    const parsedAfter = hoursAfter.trim() === "" ? 2 : Number(hoursAfter);
    if (
      !Number.isFinite(parsedBefore) ||
      parsedBefore < 0 ||
      parsedBefore > 72 ||
      !Number.isFinite(parsedAfter) ||
      parsedAfter < 0 ||
      parsedAfter > 72
    ) {
      setMacroError("锁定窗口时长需在 0 至 72 小时之间");
      return;
    }
    setSubmittingMacro(true);
    try {
      const iso = parsedDate.toISOString();
      const ok = await onRegisterMacro({
        event_name: eventName.trim(),
        scheduled_at: iso,
        lock_window_hours_before: parsedBefore,
        lock_window_hours_after: parsedAfter,
      });
      if (ok) {
        setEventName("");
        setScheduledAt("");
      }
    } finally {
      setSubmittingMacro(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* 顶部消息面态势条 (INTEL TELEMETRY STRIP) */}
      <section className="surface flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-6">
        <div className="flex flex-wrap items-center gap-3">
          <div
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1 text-xs ${
              newsOverview?.event_locked
                ? "border-rose-500/50 bg-rose-500/15 text-rose-300"
                : newsOverview
                  ? "border-emerald-500/35 bg-emerald-500/10 text-emerald-300"
                  : "border-white/10 bg-white/[0.03] text-slate-400"
            }`}
          >
            {newsOverview?.event_locked ? <Lock className="size-3.5" /> : <ShieldCheck className="size-3.5" />}
            {newsOverview?.event_locked ? `宏观事件已锁定 · ${newsOverview.lock_reason}` : newsOverview ? "宏观日程窗口开放" : "宏观日程状态待获取"}
          </div>

          <div
            className={`inline-flex items-center gap-1.5 rounded-lg border px-3 py-1 text-xs ${
              activeFlags.length > 0
                ? "border-amber-500/40 bg-amber-500/10 text-amber-300"
                : "border-white/10 bg-white/[0.03] text-slate-400"
            }`}
          >
            <ShieldAlert className="size-3.5" />
            生效风控项 {activeFlags.length}
          </div>

          <span className="font-mono text-xs text-slate-400">
            资讯数 <strong className="text-slate-100">{events.length}</strong>
          </span>
        </div>

        <button
          type="button"
          onClick={onRefresh}
          disabled={refreshing}
          className="secondary-button"
        >
          <RefreshCw className={`size-3.5 ${refreshing ? "animate-spin" : ""}`} />
          {refreshing ? "读取中…" : "刷新资讯"}
        </button>
      </section>

      <section className="surface p-4 sm:p-6">
        <h2 className="section-heading">京东资讯原文</h2>
        {syncStatus?.errors?.flash && <p className="mt-2 text-xs text-amber-200">快讯采集暂不可用</p>}
        {syncStatus?.errors?.news && <p className="mt-2 text-xs text-amber-200">黄金资讯采集暂不可用</p>}
        {syncStatus?.errors?.morning && <p className="mt-2 text-xs text-amber-200">
          {syncStatus.errors.morning === "AUTH_REQUIRED" ? "早报采集等待京东授权" : "早报采集暂不可用"}
        </p>}
        {newsOverview?.jd_articles?.length ? (
          <div className="mt-3 space-y-2">
            {newsOverview.jd_articles.map((article) => (
              <details key={article.article_id} className="rounded-lg border border-white/10 p-3">
                <summary>
                  <span className="mr-2 text-xs text-slate-400">
                    {{ news: "资讯", flash: "快讯", morning: "早报" }[article.channel]}
                  </span>
                  {article.title}
                </summary>
                <time className="mt-2 block text-xs text-slate-400" dateTime={article.published_at}>
                  {new Date(article.published_at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai" })} · {article.source}
                </time>
                <p className="mt-2 whitespace-pre-wrap text-sm">{article.content}</p>
              </details>
            ))}
          </div>
        ) : <p className="mt-2 text-sm text-slate-400">暂无已采集的京东资讯</p>}
      </section>

      {activeFlags.length > 0 && (
        <details className="rounded-xl border border-rose-400/25 bg-rose-500/8 px-4 py-3 text-sm text-rose-200">
          <summary className="font-medium">当前触发阻断的风控项 · {activeFlags.length} 项</summary>
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {activeFlags.map((flag) => (
              <li key={flag} className="data-label max-w-full break-all border-rose-400/25 bg-rose-500/10 text-rose-200">
                {flag}
              </li>
            ))}
          </ul>
        </details>
      )}

      {/* 左 7 右 5 双栏 */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* 左侧：AI 结构化新闻事件流 */}
        <section className="surface p-4 sm:p-6 lg:col-span-7">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] pb-3">
            <div className="flex items-center gap-2">
              <div>
                <h2 className="section-heading">
                  AI 结构化资讯与风险特征
                </h2>
              </div>
            </div>

            <div className="flex flex-wrap items-center gap-2 text-xs">
              <select
                aria-label="资讯分类"
                value={typeFilter}
                onChange={(e) => setTypeFilter(e.target.value)}
                className="field px-2.5 py-1 text-xs text-slate-200 outline-none focus:border-amber-400"
              >
                <option value="ALL">全部分类</option>
                <option value="GEOPOLITICAL">地缘政治</option>
                <option value="MONETARY">货币政策</option>
                <option value="MACRO">宏观经济</option>
                <option value="MARKET">市场行情</option>
                <option value="GENERAL">综合资讯</option>
              </select>

              <button
                type="button"
                onClick={() => setHighRiskOnly((v) => !v)}
                aria-pressed={highRiskOnly}
                className={`rounded-lg border px-2.5 py-1 text-xs transition ${
                  highRiskOnly
                    ? "border-rose-500/50 bg-rose-500/20 text-rose-200"
                    : "border-white/10 bg-black/40 text-slate-400 hover:text-slate-200"
                }`}
              >
                只看高风险（≥0.7）
              </button>
            </div>
          </div>

          {filteredEvents.length === 0 ? (
            <div className="empty-state py-12 text-center text-xs text-slate-400">
              暂无符合条件的资讯
            </div>
          ) : (
            <div>
              {filteredEvents.map((ev) => {
                const badge = TYPE_BADGE[ev.event_type] ?? {
                  label: ev.event_type,
                  cls: "border-white/10 bg-white/[0.03] text-slate-300",
                };
                const sevPct = Math.round(ev.severity * 100);
                const sevColor =
                  ev.severity >= 0.7 ? "bg-rose-500" : ev.severity >= 0.4 ? "bg-amber-400" : "bg-emerald-400";

                return (
                  <article
                    key={ev.event_id}
                    className="editorial-item py-4 first:pt-1 last:pb-0"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <div className="flex items-center gap-2">
                        <span className={`data-label ${badge.cls}`}>
                          {badge.label}
                        </span>
                        <span className="font-mono text-[11px] text-slate-400">{ev.source}</span>
                      </div>
                      <span className="font-mono text-[11px] text-slate-400">
                        {new Date(ev.published_at).toLocaleString("zh-CN", {
                          hour12: false,
                          timeZone: "Asia/Shanghai",
                        })}
                      </span>
                    </div>

                    <h3 className="mt-2.5 text-lg font-semibold leading-snug tracking-tight text-slate-100">{ev.title}</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-slate-300">{ev.summary}</p>

                    <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs">
                      <div className="flex items-center gap-3">
                        <div className="flex items-center gap-1.5">
                          <span className="text-slate-400">风险分</span>
                          <div className="h-1.5 w-16 overflow-hidden rounded-full bg-slate-800">
                            <div className={`h-full ${sevColor}`} style={{ width: `${sevPct}%` }} />
                          </div>
                          <span className="font-semibold text-slate-200">{ev.severity.toFixed(2)}</span>
                        </div>
                        <span className="text-slate-400">
                          置信度 <strong className="text-slate-300">{ev.confidence.toFixed(2)}</strong>
                        </span>
                      </div>

                      {ev.risk_flags.length > 0 && (
                        <details className="text-xs text-rose-200">
                          <summary>触发风控 · {ev.risk_flags.length} 项</summary>
                          <ul className="mt-2 space-y-1">
                            {ev.risk_flags.map((flag) => <li key={flag} className="break-all">{flag}</li>)}
                          </ul>
                        </details>
                      )}
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </section>

        {/* 右侧：宏观经济日程锁定窗口 */}
        <section className="space-y-6 lg:col-span-5">
          <div className="surface p-4 sm:p-6">
            <div className="mb-4 flex items-center justify-between border-b border-white/[0.06] pb-3">
              <div className="flex items-center gap-2">
                <div>
                  <h2 className="section-heading">
                    重大宏观日程窗口锁定
                  </h2>
                </div>
              </div>
            </div>

            <form onSubmit={handleMacroSubmit} className="space-y-3 metric-panel p-3.5 sm:p-4 text-xs">
              <label className="block">
                <span className="font-mono text-slate-400">事件名称 *</span>
                <input
                  type="text"
                  required
                  value={eventName}
                  onChange={(e) => setEventName(e.target.value)}
                  placeholder="例如：美国 11 月 CPI 公布 / FOMC 利率决议"
                  className="mt-1 w-full field px-3 py-1.5 outline-none"
                />
              </label>

              <label className="block">
                <span className="font-mono text-slate-400">公布时间（北京时间） *</span>
                <input
                  type="datetime-local"
                  required
                  value={scheduledAt}
                  onChange={(e) => setScheduledAt(e.target.value)}
                  className="mt-1 w-full field px-3 py-1.5 outline-none"
                />
              </label>

              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="font-mono text-slate-400">事件前（小时）</span>
                  <input
                    type="number"
                    step="0.5"
                    min="0"
                    max="72"
                    value={hoursBefore}
                    onChange={(e) => setHoursBefore(e.target.value)}
                    className="mt-1 w-full field px-3 py-1.5 text-slate-100"
                  />
                </label>
                <label className="block">
                  <span className="font-mono text-slate-400">事件后（小时）</span>
                  <input
                    type="number"
                    step="0.5"
                    min="0"
                    max="72"
                    value={hoursAfter}
                    onChange={(e) => setHoursAfter(e.target.value)}
                    className="mt-1 w-full field px-3 py-1.5 text-slate-100"
                  />
                </label>
              </div>

              {macroError && <p className="text-xs text-rose-400">{macroError}</p>}

              <button
                type="submit"
                disabled={submittingMacro || busyAction !== null}
                className="primary-button mt-2 w-full"
              >
                <Plus className="size-3.5" />
                {submittingMacro ? "登记中…" : "登记宏观事件"}
              </button>
            </form>

            <div className="mt-4 space-y-2">
              <div className="font-mono text-[11px] text-slate-400">已登记事件 {macros.length}</div>
              {macros.length === 0 ? (
                <div className="empty-state py-6 text-center text-xs text-slate-400">
                  暂无宏观日程
                </div>
              ) : (
                macros.map((m) => (
                  <div
                    key={m.event_id}
                    className="rounded-xl border border-white/[0.06] bg-black/40 px-3.5 py-2.5 text-xs backdrop-blur-md"
                  >
                    <div className="font-semibold text-slate-200">{m.event_name}</div>
                    <div className="mt-1 flex items-center justify-between text-[11px] text-slate-400">
                      <span>{new Date(m.scheduled_at).toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" })}</span>
                      <span className="data-label border-cyan-500/30 bg-cyan-500/10 text-cyan-300 text-[10px]">
                        -{m.lock_window_hours_before}h / +{m.lock_window_hours_after}h
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

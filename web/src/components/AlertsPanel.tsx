import { AlertTriangle, Bell, Check, CheckCircle2, X } from "lucide-react";
import { useEffect, useState } from "react";
import type { AlertPayload } from "../api/types";
import { formatBeijingTime } from "../quotes";

interface AlertsPanelProps {
  alerts: AlertPayload[];
  alertsLoaded: boolean;
  alertsFailed: boolean;
  focusAlertId: string | null;
  onAlertFocused: () => void;
  busyAction: string | null;
  onConfirm: (alertId: string) => void;
  onDecline: (alertId: string) => void;
  onExecute: (
    alertId: string,
    payload: { filled_grams: number; filled_price: number; fee?: number; bank_order_id?: string }
  ) => Promise<boolean>;
}

const STATE_LABEL: Record<string, { text: string; cls: string }> = {
  ALERT_SENT: { text: "待确认", cls: "border-amber-500/40 bg-amber-500/15 text-amber-300" },
  USER_CONFIRMED: { text: "已确认意向", cls: "border-cyan-500/40 bg-cyan-500/15 text-cyan-300" },
  USER_EXECUTED: { text: "已登记成交", cls: "border-emerald-500/40 bg-emerald-500/15 text-emerald-300" },
  USER_DECLINED: { text: "已拒绝", cls: "border-white/10 bg-white/[0.03] text-slate-400" },
  EXPIRED: { text: "已过期", cls: "border-white/10 bg-white/[0.03] text-slate-400" },
};

export function AlertsPanel({
  alerts, alertsLoaded, alertsFailed, focusAlertId, onAlertFocused, busyAction, onConfirm, onDecline, onExecute,
}: AlertsPanelProps) {
  const [filter, setFilter] = useState<"pending" | "all">("pending");
  const [execTarget, setExecTarget] = useState<AlertPayload | null>(null);
  const [grams, setGrams] = useState("");
  const [price, setPrice] = useState("");
  const [fee, setFee] = useState("");
  const [bankOrderId, setBankOrderId] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const pending = alerts.filter((alert) => alert.state === "ALERT_SENT" || alert.state === "USER_CONFIRMED");
  const visibleAlerts = filter === "pending" ? pending : alerts;

  useEffect(() => {
    if (!focusAlertId) return;
    if (filter !== "pending") {
      setFilter("pending");
      return;
    }
    const item = document.getElementById(`alert-${focusAlertId}`);
    if (item) {
      item.focus();
      onAlertFocused();
    }
  }, [alerts, filter, focusAlertId, onAlertFocused]);

  const openExecuteModal = (alert: AlertPayload) => {
    setExecTarget(alert);
    setGrams(String(alert.suggested_grams));
    setPrice(String(alert.action === "BUY" ? alert.current_ask_price : alert.current_bid_price));
    setFee("");
    setBankOrderId("");
  };

  const handleExecuteSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!execTarget || busyAction !== null) return;
    const g = Number(grams);
    const p = Number(price);
    if (!Number.isFinite(g) || g <= 0 || !Number.isFinite(p) || p <= 0) return;
    const parsedFee = fee.trim() ? Number(fee) : undefined;
    if (parsedFee !== undefined && (!Number.isFinite(parsedFee) || parsedFee < 0)) return;
    setSubmitting(true);
    try {
      const ok = await onExecute(execTarget.alert_id, {
        filled_grams: g,
        filled_price: p,
        fee: parsedFee,
        bank_order_id: bankOrderId.trim() || undefined,
      });
      if (ok) {
        setExecTarget(null);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div>
      <div className="mb-2.5 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Bell className="size-4 text-amber-400" />
          <h3 className="text-sm font-medium text-slate-200">
            交易提醒
          </h3>
        </div>
        <div className="flex items-center gap-1 rounded-lg border border-white/[0.06] bg-black/40 p-0.5 text-[11px]">
          <button
            type="button"
            onClick={() => setFilter("pending")}
            aria-pressed={filter === "pending"}
            className={`rounded-md px-2.5 py-1 transition ${
              filter === "pending"
                ? "bg-white/10 text-slate-100"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            待处理{alertsLoaded && ` (${pending.length})`}
          </button>
          <button
            type="button"
            onClick={() => setFilter("all")}
            aria-pressed={filter === "all"}
            className={`rounded-md px-2.5 py-1 transition ${
              filter === "all"
                ? "bg-white/[0.08] text-slate-100 border border-white/10"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            全部{alertsLoaded && ` (${alerts.length})`}
          </button>
        </div>
      </div>

      {visibleAlerts.length === 0 ? (
        <p className="py-2 text-sm text-slate-400">
          {alertsFailed ? "交易提醒读取失败" : !alertsLoaded ? "正在读取交易提醒…"
            : filter === "pending" ? "当前没有待处理的交易提醒。" : "暂无历史交易提醒。"}
        </p>
      ) : (
        <div className="space-y-3">
          {visibleAlerts.map((alert) => {
            const st = STATE_LABEL[alert.state] ?? {
              text: alert.state,
              cls: "border-white/10 bg-white/[0.03] text-slate-300",
            };
            const isBuy = alert.action === "BUY";
            const canDecide = alert.state === "ALERT_SENT";
            const canExecute = alert.state === "ALERT_SENT" || alert.state === "USER_CONFIRMED";

            return (
              <div
                key={alert.alert_id}
                id={`alert-${alert.alert_id}`}
                tabIndex={-1}
                className="scroll-mt-56 rounded-xl border border-white/[0.06] bg-black/40 p-4 transition-all hover:border-white/10 hover:bg-black/60 backdrop-blur-md focus:ring-1 focus:ring-amber-400/50"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span
                      className={`data-label font-bold ${
                        isBuy
                          ? "border-emerald-500/40 bg-emerald-500/15 text-emerald-300"
                          : "border-rose-500/40 bg-rose-500/15 text-rose-300"
                      }`}
                    >
                      {isBuy ? "买入" : "卖出"} {alert.suggested_grams}g
                    </span>
                    <span className={`data-label ${st.cls}`}>{st.text}</span>
                    {alert.is_reference_estimated && (
                      <span className="inline-flex items-center gap-1 data-label border-amber-500/40 bg-amber-500/10 text-amber-200">
                        <AlertTriangle className="size-3 text-amber-400" />
                        参考价估算
                      </span>
                    )}
                  </div>
                  <span className="font-mono text-[11px] text-slate-400">
                    {alert.alert_id} · 有效期至 <time dateTime={alert.expires_at}>{formatBeijingTime(alert.expires_at)}</time> 北京时间
                  </span>
                </div>

                <div className="mt-3 grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
                  <div className="rounded-lg border border-white/[0.04] bg-white/[0.02] p-2">
                    <span className="text-slate-400 text-[10px]">买入价 / 卖出价</span>
                    <div className="font-semibold text-slate-200 mt-0.5">
                      ¥{alert.current_bid_price.toFixed(2)} / ¥{alert.current_ask_price.toFixed(2)}
                    </div>
                  </div>
                  <div className="rounded-lg border border-white/[0.04] bg-white/[0.02] p-2">
                    <span className="text-slate-400 text-[10px]">点差</span>
                    <div className="font-semibold text-slate-200 mt-0.5">¥{alert.spread.toFixed(4)}/g</div>
                  </div>
                  <div className="rounded-lg border border-white/[0.04] bg-white/[0.02] p-2">
                    <span className="text-slate-400 text-[10px]">保本目标价</span>
                    <div className="font-semibold text-amber-300 mt-0.5">¥{alert.break_even_price.toFixed(2)}/g</div>
                  </div>
                  <div className="rounded-lg border border-white/[0.04] bg-white/[0.02] p-2">
                    <span className="text-slate-400 text-[10px]">净退出损失</span>
                    <div className="font-semibold text-rose-300 mt-0.5">¥{alert.net_exit_loss_cny.toFixed(2)}/g</div>
                  </div>
                </div>

                {alert.risk_flags.length > 0 && (
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {alert.risk_flags.map((flag) => (
                      <span
                        key={flag}
                        className="data-label border-amber-500/30 bg-amber-500/10 text-amber-300 text-[10px]"
                      >
                        {flag}
                      </span>
                    ))}
                  </div>
                )}

                <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-white/[0.04] pt-2.5 text-[11px]">
                  <span className="text-slate-400">{alert.disclaimer}</span>
                  <div className="flex flex-wrap items-center gap-2">
                    {canDecide && (
                      <>
                        <button
                          type="button"
                          onClick={() => onConfirm(alert.alert_id)}
                          disabled={busyAction !== null}
                          className="inline-flex items-center gap-1 rounded-lg border border-cyan-500/40 bg-cyan-500/10 px-3 py-1 text-xs font-semibold text-cyan-300 transition hover:bg-cyan-500/20 hover:border-cyan-400 disabled:opacity-40"
                        >
                          <Check className="size-3.5" />
                          确认意向
                        </button>
                        <button
                          type="button"
                          onClick={() => onDecline(alert.alert_id)}
                          disabled={busyAction !== null}
                          className="inline-flex items-center gap-1 rounded-lg border border-white/10 bg-black/40 px-3 py-1 text-xs text-slate-400 hover:text-slate-200 hover:border-white/20 disabled:opacity-40"
                        >
                          <X className="size-3.5" />
                          拒绝
                        </button>
                      </>
                    )}
                    {canExecute && (
                      <button
                        type="button"
                        onClick={() => openExecuteModal(alert)}
                        disabled={busyAction !== null}
                        className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1 text-xs font-semibold text-emerald-300 transition hover:bg-emerald-500/20 hover:border-emerald-400 disabled:opacity-40"
                      >
                        <CheckCircle2 className="size-3.5" />
                        回填成交
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {execTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-md">
          <form
            onSubmit={handleExecuteSubmit}
            className="w-full max-w-md surface p-6 shadow-2xl relative border-white/15"
          >
            <div className="flex items-center justify-between border-b border-white/[0.06] pb-3 mb-4">
              <div>
                <h3 className="text-sm font-semibold tracking-wide text-slate-100 flex items-center gap-2">
                  <span className="data-label border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
                    {execTarget.action}
                  </span>
                  回填银行实盘成交
                </h3>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  提醒编号 {execTarget.alert_id}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setExecTarget(null)}
                aria-label="关闭成交登记"
                className="text-slate-400 hover:text-slate-200 p-1"
              >
                <X className="size-4" />
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <label className="block">
                <span className="text-slate-400">实际成交克重（克） *</span>
                <input
                  type="number"
                  min="0.0001"
                  step="0.0001"
                  required
                  value={grams}
                  onChange={(e) => setGrams(e.target.value)}
                  className="mt-1 w-full field px-3 py-2 outline-none"
                />
              </label>
              <label className="block">
                <span className="text-slate-400">实际成交价（元/克） *</span>
                <input
                  type="number"
                  min="0.0001"
                  step="0.0001"
                  required
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                  className="mt-1 w-full field px-3 py-2 outline-none"
                />
              </label>
              <div className="grid grid-cols-2 gap-3">
                <label className="block">
                  <span className="text-slate-400">手续费（元，可选）</span>
                  <input
                    type="number"
                    min="0"
                    step="0.01"
                    value={fee}
                    onChange={(e) => setFee(e.target.value)}
                    placeholder="按费率自动计算"
                    className="mt-1 w-full field px-3 py-2 outline-none"
                  />
                </label>
                <label className="block">
                  <span className="text-slate-400">银行单号（可选）</span>
                  <input
                    type="text"
                    value={bankOrderId}
                    onChange={(e) => setBankOrderId(e.target.value)}
                    placeholder="留空自动生成"
                    className="mt-1 w-full field px-3 py-2 outline-none"
                  />
                </label>
              </div>
            </div>

            <div className="mt-6 flex justify-end gap-2.5 text-xs">
              <button
                type="button"
                onClick={() => setExecTarget(null)}
                className="rounded-lg border border-white/10 bg-black/40 px-4 py-2 text-slate-400 hover:text-slate-200"
              >
                取消
              </button>
              <button
                type="submit"
                disabled={submitting || busyAction !== null}
                className="rounded-lg border border-emerald-500/50 bg-emerald-500/20 px-5 py-2 font-semibold text-emerald-300 transition hover:bg-emerald-500/30 hover:border-emerald-400 disabled:opacity-50"
              >
                {submitting ? "登记中…" : "确认登记"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}

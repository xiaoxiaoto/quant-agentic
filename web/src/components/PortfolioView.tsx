import {
  ArrowDownRight,
  ArrowUpRight,
  DatabaseBackup,
  Scale,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { JDHoldingsResponse, LiveStatusResponse, OrderEvent, ReconciliationReport } from "../api/types";

type LotStatus = "HOLDING" | "PARTIAL" | "CLOSED" | "SELL";

interface EnrichedOrder {
  order: OrderEvent;
  lotStatus: LotStatus;
  remainingGrams: number;
  lotPnl: number | null;
}

function computeFifoOrders(orders: OrderEvent[], currentPrice: number | null): EnrichedOrder[] {
  const buyLots: Array<{ idx: number; filled: number; remaining: number }> = [];
  const results: EnrichedOrder[] = orders.map((order) => ({
    order,
    lotStatus: order.direction === "BUY" ? "HOLDING" : "SELL",
    remainingGrams: 0,
    lotPnl: null,
  }));

  orders.forEach((order, idx) => {
    const grams = order.filled_grams ?? order.requested_grams;
    if (order.direction === "BUY") {
      buyLots.push({ idx, filled: grams, remaining: grams });
    } else if (order.direction === "SELL") {
      let need = grams;
      for (const lot of buyLots) {
        if (lot.remaining <= 1e-7) continue;
        const take = Math.min(lot.remaining, need);
        lot.remaining = Math.max(0, Math.round((lot.remaining - take) * 10000) / 10000);
        need = Math.max(0, Math.round((need - take) * 10000) / 10000);
        if (need <= 1e-7) break;
      }
    }
  });

  for (const lot of buyLots) {
    const entry = results[lot.idx];
    entry.remainingGrams = lot.remaining;
    if (lot.remaining <= 1e-7) {
      entry.lotStatus = "CLOSED";
    } else if (lot.remaining + 1e-7 < lot.filled) {
      entry.lotStatus = "PARTIAL";
    } else {
      entry.lotStatus = "HOLDING";
    }
    if (lot.remaining > 1e-7 && currentPrice !== null && entry.order.execution_price != null) {
      entry.lotPnl = (currentPrice - entry.order.execution_price) * lot.remaining;
    }
  }

  return results;
}

interface PortfolioViewProps {
  liveStatus: LiveStatusResponse | null;
  jdHoldings: JDHoldingsResponse | null;
  jdHoldingsError: string | null;
  referenceGoldPrice: number | null;
  liveFailed: boolean;
  orders: OrderEvent[];
  reconReports: ReconciliationReport[];
  busyAction: string | null;
  onReconcileJd: () => void;
  onInitLedgerFromJd: () => void;
  onSubmitPaperOrder: (direction: "BUY" | "SELL", grams: number) => void;
}

export function PortfolioView({
  liveStatus,
  jdHoldings,
  jdHoldingsError,
  referenceGoldPrice,
  liveFailed,
  orders,
  reconReports,
  busyAction,
  onReconcileJd,
  onInitLedgerFromJd,
  onSubmitPaperOrder,
}: PortfolioViewProps) {
  const [grams, setGrams] = useState("2");
  const [onlyHolding, setOnlyHolding] = useState(false);
  const snap = liveStatus?.snapshot;
  const configuredInitialCash = liveStatus?.default_initial_cash ?? 100000;
  const sellFeeRate = liveStatus?.sell_fee_rate ?? 0.004;
  const jdTotalIncome = (jdHoldings?.holdingList ?? []).reduce((sum, item) => sum + (item.totalIncome ?? 0), 0);
  const jdGrossValue =
    jdHoldings && referenceGoldPrice !== null ? jdHoldings.totalGramAll * referenceGoldPrice : null;
  const jdSellFee = jdGrossValue !== null ? jdGrossValue * sellFeeRate : null;
  const jdSellValuation = jdGrossValue !== null && jdSellFee !== null ? jdGrossValue - jdSellFee : null;

  const enrichedOrders = useMemo(
    () => computeFifoOrders(orders, referenceGoldPrice).reverse(),
    [orders, referenceGoldPrice]
  );
  const openPositionCost = useMemo(
    () =>
      enrichedOrders.reduce((sum, item) => {
        if (item.remainingGrams <= 0 || item.order.execution_price == null) return sum;
        return sum + item.remainingGrams * item.order.execution_price;
      }, 0),
    [enrichedOrders]
  );
  const hasOpenOrders = useMemo(
    () => enrichedOrders.some((item) => item.remainingGrams > 0),
    [enrichedOrders]
  );
  const displayHoldingCost = hasOpenOrders
    ? openPositionCost
    : jdHoldings
      ? jdHoldings.totalGramAll * jdHoldings.avgCostPrice
      : null;
  const holdingCount = useMemo(
    () => enrichedOrders.filter((item) => item.lotStatus === "HOLDING" || item.lotStatus === "PARTIAL").length,
    [enrichedOrders]
  );
  const visibleOrders = useMemo(
    () =>
      onlyHolding
        ? enrichedOrders.filter((item) => item.lotStatus === "HOLDING" || item.lotStatus === "PARTIAL")
        : enrichedOrders,
    [enrichedOrders, onlyHolding]
  );

  const handleOrder = (direction: "BUY" | "SELL") => {
    const val = Number(grams);
    if (!Number.isFinite(val) || val <= 0) return;
    onSubmitPaperOrder(direction, val);
  };

  return (
    <div className="space-y-6">
      {/* 01: 京东实盘官方持仓 (CUSTODY UPLINK) */}
      <section className="surface holdings-surface p-4 sm:p-6 relative overflow-hidden">
        <div className="absolute top-0 right-0 h-32 w-32 bg-amber-500/[0.04] rounded-full blur-2xl pointer-events-none" />

        <div className="relative mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] pb-4">
          <div className="flex items-center gap-2.5">
            <div>
              <h2 className="section-heading flex items-center gap-2">
                京东实盘持仓
              </h2>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={onInitLedgerFromJd}
              disabled={busyAction !== null || !liveStatus?.jd_logged_in || liveFailed}
              className="inline-flex items-center gap-1.5 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-1.5 text-xs font-medium text-emerald-300 transition hover:bg-emerald-500/20 hover:border-emerald-400 disabled:opacity-40"
            >
              <DatabaseBackup className="size-3.5" />
              {busyAction === "init-ledger-jd" ? "重置中…" : "按实盘重置影子账本"}
            </button>
            <button
              type="button"
              onClick={onReconcileJd}
              disabled={busyAction !== null || !liveStatus?.jd_logged_in || liveFailed}
              className="inline-flex items-center gap-1.5 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-1.5 text-xs font-medium text-amber-200 transition hover:bg-amber-500/20 hover:border-amber-400 disabled:opacity-40"
            >
              <Scale className="size-3.5" />
              {busyAction === "reconcile-jd" ? "对账中…" : "京东持仓对账"}
            </button>
          </div>
        </div>

        {!liveStatus?.jd_logged_in ? (
          <div className="rounded-xl border border-dashed border-amber-500/20 bg-black/40 py-8 text-center text-xs text-slate-400">
            京东账户未授权 · 请在终端执行 <code className="text-amber-300 bg-amber-500/10 px-2 py-0.5 rounded">uv run python main.py auth login</code>
          </div>
        ) : jdHoldingsError ? (
          <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-4 py-3 text-xs text-rose-200">
            京东实盘持仓读取失败：{jdHoldingsError}
          </div>
        ) : !jdHoldings ? (
          <div className="rounded-xl border border-white/[0.05] bg-black/40 py-8 text-center text-xs text-slate-400">
            正在读取京东实盘持仓…
          </div>
        ) : (
          <div className="space-y-4">
            {/* 5-pillar Telemetry Matrix */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
              <div className="metric-panel p-3.5 sm:p-4">
                <span className="metric-label">
                  黄金持仓
                </span>
                <div className="mt-2 text-xl font-bold text-amber-300">
                  {jdHoldings.totalGramAll.toFixed(4)}
                  <span className="ml-1 text-xs font-normal text-amber-400/70">g</span>
                </div>
                <div className="mt-1 text-[10px] text-slate-400">
                  托管行 {jdHoldings.holdingList.length} 家
                </div>
              </div>

              <div className="metric-panel p-3.5 sm:p-4">
                <span className="metric-label">
                  成本均价
                </span>
                <div className="mt-2 text-xl font-bold text-slate-100">
                  {jdHoldings.avgCostPrice > 0 ? `¥${jdHoldings.avgCostPrice.toFixed(2)}` : "—"}
                </div>
                <div className="mt-1 text-[10px] text-slate-400">
                  加权均价 (CNY/g)
                </div>
              </div>

              <div className="metric-panel p-3.5 sm:p-4">
                <span className="metric-label">
                  持仓成本
                </span>
                <div className="mt-2 text-xl font-bold text-slate-100">
                  {displayHoldingCost !== null
                    ? `¥${displayHoldingCost.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                    : "—"}
                </div>
                <div className="mt-1 text-[10px] text-slate-400 truncate" title={hasOpenOrders ? "Σ(在持买入克重 × 成交价)" : "克重 × 均价"}>
                  {hasOpenOrders ? "Σ(买入批次 × 成交价)" : "均价折算"}
                </div>
              </div>

              <div className="metric-panel p-3.5 sm:p-4">
                <span className="metric-label">
                  卖出估值
                </span>
                <div className="mt-2 text-xl font-semibold text-slate-100">
                  {jdSellValuation !== null
                    ? `¥${jdSellValuation.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
                    : "—"}
                </div>
                <div className="mt-1 text-[10px] text-slate-400">
                  扣费 ({(sellFeeRate * 100).toFixed(1)}%): {jdSellFee !== null ? `¥${jdSellFee.toFixed(2)}` : "—"}
                </div>
              </div>

              <div className="metric-panel p-3.5 sm:p-4">
                <span className="metric-label">
                  累计收益
                </span>
                <div
                  className={`mt-2 text-xl font-bold ${
                    jdTotalIncome >= 0 ? "text-emerald-300" : "text-rose-300"
                  }`}
                >
                  {jdTotalIncome >= 0 ? "+" : "-"}¥{Math.abs(jdTotalIncome).toFixed(2)}
                </div>
                <div className="mt-1 text-[10px] text-slate-400">
                  官方清算口径
                </div>
              </div>
            </div>

            {/* Bank Custody Table */}
            {jdHoldings.holdingList.length > 0 && (
              <div className="overflow-x-auto rounded-xl border border-white/10 bg-[#101722]">
                <div className="px-4 py-2 bg-white/[0.02] border-b border-white/[0.04] flex items-center justify-between text-[11px] text-slate-400">
                  <span>签约行明细</span>
                  <span>共 {jdHoldings.holdingList.length}</span>
                </div>
                <table className="data-table w-full text-left text-xs">
                  <thead className="bg-black/50 text-[11px] text-slate-400">
                    <tr>
                      <th className="px-4 py-2.5">托管行</th>
                      <th className="px-4 py-2.5">银行代码</th>
                      <th className="px-4 py-2.5">持有克重</th>
                      <th className="px-4 py-2.5">持仓均价</th>
                      <th className="px-4 py-2.5">持仓总成本</th>
                      <th className="px-4 py-2.5 text-right">官方累计收益</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-white/[0.04]">
                    {jdHoldings.holdingList.map((item) => (
                      <tr key={item.bankCode} className="hover:bg-amber-500/[0.03] transition-colors">
                        <td className="px-4 py-2.5 font-sans font-medium text-slate-200">{item.bankName}</td>
                        <td className="px-4 py-2.5 text-slate-400">
                          <span className="data-label border-white/10 bg-white/[0.03] text-slate-300">
                            {item.bankCode}
                          </span>
                        </td>
                        <td className="px-4 py-2.5 font-semibold text-amber-300">{item.totalGram.toFixed(4)} g</td>
                        <td className="px-4 py-2.5 text-slate-300">¥{item.avgCostPrice.toFixed(2)}</td>
                        <td className="px-4 py-2.5 text-slate-300">
                          ¥{(item.totalGram * item.avgCostPrice).toLocaleString("zh-CN", {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 2,
                          })}
                        </td>
                        <td
                          className={`px-4 py-2.5 text-right font-medium ${
                            item.totalIncome >= 0 ? "text-emerald-300" : "text-rose-300"
                          }`}
                        >
                          {item.totalIncome >= 0 ? "+" : "-"}¥{Math.abs(item.totalIncome).toFixed(2)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
      </section>

      {/* 02: 本地影子账本 + 模拟盘仿真委托 */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
        {/* 本地影子账本 */}
        <section className="surface p-4 sm:p-6 lg:col-span-7">
          <div className="mb-4 flex items-center justify-between border-b border-white/[0.06] pb-3">
            <div className="flex items-center gap-2">
              <div>
                <h2 className="section-heading flex items-center gap-2">
                  本地影子账本 · 资产镜像
                </h2>
              </div>
            </div>
            <span className="font-mono text-[10px] text-slate-400">
              初始资金 ¥{configuredInitialCash.toLocaleString("zh-CN")}
            </span>
          </div>

          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                可用现金
              </span>
              <div className="mt-2 text-lg font-bold text-slate-100">
                {snap ? `¥${snap.cash_available.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}` : "—"}
              </div>
              <div className="mt-1 text-[10px] text-slate-400 truncate" title={`初始 ¥${configuredInitialCash.toLocaleString("zh-CN")} - 持仓成本 ¥${openPositionCost.toLocaleString("zh-CN")}`}>
                扣减持仓成本 ¥{openPositionCost.toFixed(2)}
              </div>
            </div>

            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                黄金持仓
              </span>
              <div className="mt-2 text-lg font-bold text-amber-300">
                {snap ? `${snap.gold_total_g.toFixed(4)}` : "—"}
                <span className="ml-1 text-xs font-normal text-amber-400/70">g</span>
              </div>
              <div className="mt-1 text-[10px] text-slate-400">
                可用: {snap ? snap.gold_available_g.toFixed(4) : "—"} g
              </div>
            </div>

            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                持仓均价
              </span>
              <div className="mt-2 text-lg font-bold text-slate-100">
                {snap && snap.cost_basis_per_g !== null ? `¥${snap.cost_basis_per_g.toFixed(2)}` : "—"}
              </div>
              <div className="mt-1 text-[10px] text-slate-400">
                单位成本 (元/克)
              </div>
            </div>

            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                已实现盈亏
              </span>
              <div
                className={`mt-2 text-lg font-bold ${
                  (snap?.realized_pnl ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"
                }`}
              >
                {snap ? `${snap.realized_pnl >= 0 ? "+" : "-"}¥${Math.abs(snap.realized_pnl).toFixed(2)}` : "—"}
              </div>
              <div className="mt-1 text-[10px] text-slate-400">已平仓结算</div>
            </div>

            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                浮动盈亏
              </span>
              <div
                className={`mt-2 text-lg font-bold ${
                  (snap?.unrealized_pnl ?? 0) >= 0 ? "text-emerald-300" : "text-rose-300"
                }`}
              >
                {snap ? `${snap.unrealized_pnl >= 0 ? "+" : "-"}¥${Math.abs(snap.unrealized_pnl).toFixed(2)}` : "—"}
              </div>
              <div className="mt-1 text-[10px] text-slate-400">在持浮动盯盘</div>
            </div>

            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                累计计息克重
              </span>
              <div className="mt-2 text-lg font-bold text-slate-200">
                {snap ? `${snap.interest_credited_g.toFixed(4)}` : "—"}
                <span className="ml-1 text-xs font-normal text-slate-400">g</span>
              </div>
              <div className="mt-1 text-[10px] text-slate-400">
                待结: {snap ? snap.interest_pending_g.toFixed(4) : "—"} g
              </div>
            </div>
          </div>
        </section>

        {/* 模拟盘手动委托 */}
        <section className="surface p-4 sm:p-6 lg:col-span-5 flex flex-col justify-between">
          <div>
            <div className="mb-4 flex items-center justify-between border-b border-white/[0.06] pb-3">
              <div className="flex items-center gap-2">
                <div>
                  <h2 className="section-heading">
                    影子盘 · 仿真执行
                  </h2>
                </div>
              </div>
              <span className="font-mono text-[10px] text-amber-300">
                REF: {referenceGoldPrice ? `¥${referenceGoldPrice.toFixed(2)}/g` : "N/A"}
              </span>
            </div>

            <div className="space-y-4">
              <div>
                <div className="flex items-center justify-between text-xs mb-1.5">
                  <span className="text-slate-400">委托克重</span>
                  <div className="flex items-center gap-1">
                    {["1", "2", "5", "10"].map((quickGrams) => (
                      <button
                        key={quickGrams}
                        type="button"
                        onClick={() => setGrams(quickGrams)}
                        className={`px-1.5 py-0.5 rounded text-[10px] transition ${
                          grams === quickGrams
                            ? "bg-amber-500/20 text-amber-300 border border-amber-500/40"
                            : "bg-white/[0.04] text-slate-400 hover:text-slate-200 border border-white/[0.06]"
                        }`}
                      >
                        {quickGrams}g
                      </button>
                    ))}
                  </div>
                </div>
                <div className="relative">
                  <input
                    type="number"
                    step="0.5"
                    min="0.1"
                    value={grams}
                    onChange={(e) => setGrams(e.target.value)}
                    className="field w-full px-3.5 py-2.5 text-base font-semibold outline-none"
                    placeholder="2.0000"
                  />
                  <span className="absolute right-3.5 top-1/2 -translate-y-1/2 text-xs text-slate-400">
                    克
                  </span>
                </div>
              </div>

              <div className="rounded-lg border border-white/[0.04] bg-white/[0.02] p-2.5 text-[11px] text-slate-400 space-y-1">
                <div className="flex justify-between">
                  <span>预计现金流</span>
                  <span className="text-slate-200">
                    {referenceGoldPrice && Number(grams) > 0
                      ? `¥${(Number(grams) * referenceGoldPrice).toFixed(2)}`
                      : "—"}
                  </span>
                </div>
              </div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3 pt-4">
            <button
              type="button"
              disabled={busyAction !== null}
              onClick={() => handleOrder("BUY")}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-emerald-500/50 bg-emerald-500/15 py-3 text-xs font-semibold text-emerald-300 transition hover:bg-emerald-500/25 hover:border-emerald-400 disabled:opacity-40"
            >
              <ArrowUpRight className="size-4" />
              模拟买入
            </button>
            <button
              type="button"
              disabled={busyAction !== null}
              onClick={() => handleOrder("SELL")}
              className="inline-flex items-center justify-center gap-2 rounded-xl border border-rose-500/50 bg-rose-500/15 py-3 text-xs font-semibold text-rose-300 transition hover:bg-rose-500/25 hover:border-rose-400 disabled:opacity-40"
            >
              <ArrowDownRight className="size-4" />
              模拟卖出
            </button>
          </div>
        </section>
      </div>

      {/* 03: 影子账本历史流水与 FIFO 在持批次 (ORDER STREAM) */}
      <section className="surface p-4 sm:p-6">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] pb-3">
          <div className="flex items-center gap-2">
            <div>
              <h2 className="section-heading flex items-center gap-2">
                影子账本历史流水
                <span className="text-[10px] text-slate-400">
                  在持批次按先进先出计算
                </span>
              </h2>
            </div>
          </div>

          <div className="flex items-center gap-1.5 text-xs">
            <button
              type="button"
              onClick={() => setOnlyHolding(false)}
              className={`rounded-lg px-3 py-1.5 transition ${
                !onlyHolding
                  ? "bg-amber-500/20 font-semibold text-amber-200 border border-amber-500/40"
                  : "bg-black/40 text-slate-400 hover:text-slate-200 border border-white/[0.06]"
              }`}
            >
              全部 ({orders.length})
            </button>
            <button
              type="button"
              onClick={() => setOnlyHolding(true)}
              className={`rounded-lg px-3 py-1.5 transition ${
                onlyHolding
                  ? "bg-emerald-500/20 font-semibold text-emerald-200 border border-emerald-500/40"
                  : "bg-black/40 text-slate-400 hover:text-slate-200 border border-white/[0.06]"
              }`}
            >
              仅看在持 ({holdingCount})
            </button>
          </div>
        </div>

        {visibleOrders.length === 0 ? (
          <div className="empty-state py-10 text-center text-xs text-slate-400">
            {onlyHolding ? "暂无在持批次" : "暂无历史订单"}
          </div>
        ) : (
          <div className="max-h-[480px] overflow-auto rounded-xl border border-white/[0.06] bg-[#101722]">
            <table className="data-table w-full text-left text-xs">
              <thead className="sticky top-0 bg-black/80 backdrop-blur-md text-[11px] text-slate-400 border-b border-white/[0.06]">
                <tr>
                  <th className="px-4 py-2.5">流水单号</th>
                  <th className="px-4 py-2.5">方向</th>
                  <th className="px-4 py-2.5">FIFO状态</th>
                  <th className="px-4 py-2.5">成交克重</th>
                  <th className="px-4 py-2.5">成交价</th>
                  <th className="px-4 py-2.5">手续费</th>
                  <th className="px-4 py-2.5">当前浮盈</th>
                  <th className="px-4 py-2.5 text-right">时间戳 (CST)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {visibleOrders.map(({ order: ord, lotStatus, remainingGrams, lotPnl }) => {
                  const isOpen = lotStatus === "HOLDING" || lotStatus === "PARTIAL";
                  return (
                    <tr
                      key={ord.client_order_id}
                      className={`hover:bg-amber-500/[0.03] transition-colors ${
                        isOpen ? "bg-amber-500/[0.02]" : ""
                      }`}
                    >
                      <td className="px-4 py-2.5 text-slate-300" title={ord.client_order_id}>
                        <span className="font-mono text-xs text-slate-200">
                          {ord.bank_order_id || ord.client_order_id}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        <span
                          className={`data-label ${
                            ord.direction === "BUY"
                              ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                              : "border-rose-500/40 bg-rose-500/10 text-rose-300"
                          }`}
                        >
                          {ord.direction === "BUY" ? "买入" : "卖出"}
                        </span>
                      </td>
                      <td className="px-4 py-2.5">
                        {lotStatus === "HOLDING" && (
                          <span className="data-label border-amber-500/40 bg-amber-500/15 text-amber-200">
                            在持
                          </span>
                        )}
                        {lotStatus === "PARTIAL" && (
                          <span className="data-label border-amber-500/30 bg-amber-500/10 text-amber-300">
                            部分卖出 ({remainingGrams.toFixed(4)}g)
                          </span>
                        )}
                        {lotStatus === "CLOSED" && (
                          <span className="data-label border-white/10 bg-white/[0.03] text-slate-400">
                            已平仓
                          </span>
                        )}
                        {lotStatus === "SELL" && (
                          <span className="data-label border-white/10 bg-white/[0.03] text-slate-400">
                            卖出平仓
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-2.5 font-semibold text-slate-200">
                        {(ord.filled_grams ?? ord.requested_grams).toFixed(4)} g
                      </td>
                      <td className="px-4 py-2.5 text-amber-300 font-semibold">
                        {ord.execution_price != null ? `¥${ord.execution_price.toFixed(2)}` : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-slate-400">
                        {ord.fee != null ? `¥${ord.fee.toFixed(2)}` : "—"}
                      </td>
                      <td
                        className={`px-4 py-2.5 font-semibold ${
                          lotPnl === null
                            ? "text-slate-600"
                            : lotPnl >= 0
                              ? "text-emerald-300"
                              : "text-rose-300"
                        }`}
                      >
                        {lotPnl !== null ? `${lotPnl >= 0 ? "+" : "-"}¥${Math.abs(lotPnl).toFixed(2)}` : "—"}
                      </td>
                      <td className="px-4 py-2.5 text-right text-slate-400 text-[11px]">
                        {(ord.bank_event_at ?? ord.received_at)
                          ? new Date((ord.bank_event_at ?? ord.received_at) as string).toLocaleString("zh-CN", {
                              hour12: false,
                            })
                          : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* 04: 对账历史审计报告 (AUDIT TELEMETRY) */}
      <section className="surface p-4 sm:p-6">
        <div className="mb-4 flex items-center justify-between border-b border-white/[0.06] pb-3">
          <div className="flex items-center gap-2">
            <h2 className="section-heading">
              账实对账审计记录
            </h2>
          </div>
          <span className="font-mono text-[10px] text-slate-400">
            对账记录 {reconReports.length}
          </span>
        </div>

        {reconReports.length === 0 ? (
          <div className="empty-state py-6 text-center text-xs text-slate-400">
            暂无对账记录
          </div>
        ) : (
          <div className="space-y-2">
            {reconReports.map((rep) => (
              <div
                key={rep.report_id}
                className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-white/[0.06] bg-black/40 px-4 py-3 text-xs backdrop-blur-md"
              >
                <div className="flex items-center gap-2.5">
                  {rep.matched ? (
                    <span className="flex items-center gap-1.5 text-emerald-400 bg-emerald-500/10 border border-emerald-500/20 px-2 py-0.5 rounded text-[10px]">
                      <span className="size-1.5 rounded-full bg-emerald-400" />
                      账实一致
                    </span>
                  ) : (
                    <span className="flex items-center gap-1.5 text-rose-400 bg-rose-500/10 border border-rose-500/20 px-2 py-0.5 rounded text-[10px]">
                      <span className="size-1.5 rounded-full bg-rose-400" />
                      存在差异
                    </span>
                  )}
                  <span className="font-mono text-slate-200">{rep.summary}</span>
                </div>
                <span className="font-mono text-[11px] text-slate-400">
                  {new Date(rep.as_of).toLocaleString("zh-CN", { hour12: false })}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

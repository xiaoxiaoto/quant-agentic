import { ColorType, HistogramSeries, createChart, type Time } from "lightweight-charts";
import { Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { BacktestSummary } from "../api/types";

interface BacktestLabProps {
  busyAction: string | null;
  result: BacktestSummary | null;
  onRunBacktest: (params: { initial_cash: number; trade_grams: number; dca_step_interval: number }) => void;
}

export function BacktestLab({ busyAction, result, onRunBacktest }: BacktestLabProps) {
  const [cash, setCash] = useState("100000");
  const [grams, setGrams] = useState("2");
  const [dcaStep, setDcaStep] = useState("5");
  const chartContainerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!result || !chartContainerRef.current) return;
    const container = chartContainerRef.current;
    const chart = createChart(container, {
      height: 220,
      layout: {
        background: { type: ColorType.Solid, color: "#101722" },
        textColor: "#64748b",
      },
      grid: {
        vertLines: { color: "rgba(255, 255, 255, 0.04)" },
        horzLines: { color: "rgba(255, 255, 255, 0.04)" },
      },
      rightPriceScale: {
        borderColor: "rgba(255, 255, 255, 0.06)",
      },
      timeScale: {
        borderColor: "rgba(255, 255, 255, 0.06)",
        visible: false,
      },
    });

    const series = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "percent" },
    });

    const points = [
      { label: "策略", value: result.shadow_report.total_return_pct * 100, color: "#f59e0b" },
      ...result.baseline_report.metrics.map((m, idx) => ({
        label: m.strategy_name,
        value: m.total_return_pct * 100,
        color: idx === 0 ? "#38bdf8" : "#a78bfa",
      })),
    ];

    series.setData(
      points.map((pt, idx) => ({
        time: (1700000000 + idx * 86400) as Time,
        value: Number(pt.value.toFixed(4)),
        color: pt.color,
      }))
    );
    chart.timeScale().fitContent();

    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);
    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, [result]);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onRunBacktest({
      initial_cash: Number(cash) || 100000,
      trade_grams: Number(grams) || 2,
      dca_step_interval: Number(dcaStep) || 5,
    });
  };

  return (
    <section className="surface p-4 sm:p-6 relative overflow-hidden">
      <div className="mb-5 space-y-4">
        <div className="flex items-center gap-2">
          <div>
            <h2 className="section-heading">
              历史 PIT 回测与策略基准对比
            </h2>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="metric-panel grid grid-cols-2 items-end gap-3 p-4 text-sm lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)_auto]">
          <label className="grid min-w-0 gap-1.5 text-slate-300">
            <span>初始资金</span>
            <input
              type="number"
              min="100"
              value={cash}
              onChange={(e) => setCash(e.target.value)}
              className="field px-3 py-1 outline-none"
            />
          </label>
          <label className="grid min-w-0 gap-1.5 text-slate-300">
            <span>单笔克重</span>
            <input
              type="number"
              min="0.1"
              step="0.1"
              value={grams}
              onChange={(e) => setGrams(e.target.value)}
              className="field px-3 py-1 outline-none"
            />
          </label>
          <label className="grid min-w-0 gap-1.5 text-slate-300">
            <span>定投步长</span>
            <input
              type="number"
              min="1"
              value={dcaStep}
              onChange={(e) => setDcaStep(e.target.value)}
              className="field px-3 py-1 outline-none"
            />
          </label>
          <button
            type="submit"
            disabled={busyAction !== null}
            className="primary-button"
          >
            <Play className="size-3" />
            {busyAction === "backtest" ? "回测中…" : "运行回测"}
          </button>
        </form>
      </div>

      {!result ? (
        <div className="empty-state py-12 text-center text-xs text-slate-400">
          设置参数后运行回测。
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                策略收益
              </span>
              <div className="mt-2 text-xl font-bold text-amber-300">
                {(result.shadow_report.total_return_pct * 100).toFixed(2)}%
              </div>
            </div>

            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                最大回撤
              </span>
              <div className="mt-2 text-xl font-bold text-rose-300">
                {(result.shadow_report.max_drawdown_pct * 100).toFixed(2)}%
              </div>
            </div>

            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                夏普比率
              </span>
              <div className="mt-2 text-xl font-bold text-slate-100">
                {result.shadow_report.sharpe_ratio !== null ? result.shadow_report.sharpe_ratio.toFixed(2) : "—"}
              </div>
            </div>

            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                超额收益
              </span>
              <div className="mt-2 text-xl font-bold text-cyan-300">
                {result.excess_return_vs_buy_and_hold !== null
                  ? `${(result.excess_return_vs_buy_and_hold * 100).toFixed(2)}%`
                  : "—"}
              </div>
            </div>

            <div className="metric-panel p-3.5 sm:p-4">
              <span className="metric-label">
                行情 / 交易
              </span>
              <div className="mt-2 text-xl font-bold text-slate-200">
                {result.quote_count} / {result.shadow_report.total_trades}
              </div>
            </div>
          </div>

          <div className="overflow-hidden rounded-xl border border-white/[0.06] bg-black/30 p-3 backdrop-blur-md">
            <div className="mb-2 flex items-center gap-4 px-2 text-[11px] text-slate-400">
              <span className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full bg-amber-400" />
                策略
              </span>
              {result.baseline_report.metrics.map((m, idx) => (
                <span key={m.strategy_name} className="inline-flex items-center gap-1.5">
                  <span className={`size-2 rounded-full ${idx === 0 ? "bg-cyan-400" : "bg-violet-400"}`} />
                  {m.strategy_name} ({(m.total_return_pct * 100).toFixed(2)}%)
                </span>
              ))}
            </div>
            <div ref={chartContainerRef} className="w-full" />
          </div>
        </div>
      )}
    </section>
  );
}

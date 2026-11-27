import { BrainCircuit, FlaskConical, Play, Workflow } from "lucide-react";
import { useState } from "react";
import type { BacktestSummary, TrainingPipelineReport } from "../api/types";
import { BacktestLab } from "./BacktestLab";
import { ModelWorkflowStudio } from "./workflow/ModelWorkflowStudio";

interface ResearchViewProps {
  busyAction: string | null;
  backtestResult: BacktestSummary | null;
  trainReport: TrainingPipelineReport | null;
  onNotify: (type: "ok" | "error", text: string) => void;
  onRunBacktest: (params: { initial_cash: number; trade_grams: number; dca_step_interval: number }) => void;
  onRunTrain: (params: {
    reference_product_code?: string;
    horizons: string[];
    n_splits: number;
    num_boost_round: number;
    output_model_dir?: string;
  }) => void;
}

export function ResearchView({
  busyAction,
  backtestResult,
  trainReport,
  onNotify,
  onRunBacktest,
  onRunTrain,
}: ResearchViewProps) {
  const [subTab, setSubTab] = useState<"backtest" | "train" | "workflow">("backtest");
  const [sourceMode, setSourceMode] = useState<"reference" | "quotes">("reference");
  const [refCode, setRefCode] = useState("CMBC-JCJ");
  const [horizon, setHorizon] = useState("1h");
  const [splits, setSplits] = useState("3");
  const [rounds, setRounds] = useState("60");
  const [saveModel, setSaveModel] = useState(false);

  const handleTrainSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onRunTrain({
      reference_product_code: sourceMode === "reference" ? refCode : undefined,
      horizons: [horizon],
      n_splits: Number(splits) || 3,
      num_boost_round: Number(rounds) || 60,
      output_model_dir: saveModel ? "data/models/lightgbm/v1" : undefined,
    });
  };

  return (
    <div className="space-y-6">
      {/* 实验室子导航 (SUB NAV HUD) */}
      <div className="segmented-control">
        <button
          type="button"
          onClick={() => setSubTab("backtest")}
          aria-pressed={subTab === "backtest"}
          className="segment"
        >
          <FlaskConical className="size-3.5" />
          历史回测
        </button>
        <button
          type="button"
          onClick={() => setSubTab("train")}
          aria-pressed={subTab === "train"}
          className="segment"
        >
          <BrainCircuit className="size-3.5" />
          滚动训练
        </button>
        <button
          type="button"
          onClick={() => setSubTab("workflow")}
          aria-pressed={subTab === "workflow"}
          className="segment"
        >
          <Workflow className="size-3.5" />
          模型工作流
        </button>
      </div>

      {subTab === "backtest" ? (
        <BacktestLab busyAction={busyAction} result={backtestResult} onRunBacktest={onRunBacktest} />
      ) : subTab === "workflow" ? (
        <ModelWorkflowStudio onNotify={onNotify} />
      ) : (
        <section className="surface p-4 sm:p-6 relative overflow-hidden space-y-5">
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] pb-3">
            <div className="flex items-center gap-2">
              <div>
                <h2 className="section-heading">
                  PIT 样本构建与 Walk-Forward 滚动验证
                </h2>
              </div>
            </div>
          </div>

          <form
            onSubmit={handleTrainSubmit}
            className="metric-panel grid grid-cols-2 gap-3 p-4 text-sm sm:grid-cols-3 xl:grid-cols-6"
          >
            <label className="block">
              <span className="text-slate-400">数据来源</span>
              <select
                value={sourceMode}
                onChange={(e) => setSourceMode(e.target.value as "reference" | "quotes")}
                className="mt-1 w-full field px-2.5 py-1.5 outline-none"
              >
                <option value="reference">参考行情库</option>
                <option value="quotes">双边报价库</option>
              </select>
            </label>

            <label className="block">
              <span className="text-slate-400">品种</span>
              <input
                type="text"
                value={refCode}
                disabled={sourceMode !== "reference"}
                onChange={(e) => setRefCode(e.target.value)}
                className="mt-1 w-full field px-2.5 py-1.5 outline-none disabled:opacity-40"
              />
            </label>

            <label className="block">
              <span className="text-slate-400">预测周期</span>
              <select
                value={horizon}
                onChange={(e) => setHorizon(e.target.value)}
                className="mt-1 w-full field px-2.5 py-1.5 outline-none"
              >
                <option value="1h">1h（短周期）</option>
                <option value="4h">4h</option>
                <option value="1d">1d</option>
                <option value="1w">1w（实盘主周期）</option>
              </select>
            </label>

            <label className="block">
              <span className="text-slate-400">验证折数</span>
              <input
                type="number"
                min="1"
                max="10"
                value={splits}
                onChange={(e) => setSplits(e.target.value)}
                className="mt-1 w-full field px-2.5 py-1.5 outline-none"
              />
            </label>

            <label className="block">
              <span className="text-slate-400">训练轮数</span>
              <input
                type="number"
                min="10"
                max="300"
                value={rounds}
                onChange={(e) => setRounds(e.target.value)}
                className="mt-1 w-full field px-2.5 py-1.5 outline-none"
              />
            </label>

            <div className="flex flex-col items-start justify-end gap-2">
              <label className="inline-flex items-center gap-1.5 text-[11px] text-slate-400 cursor-pointer">
                <input
                  type="checkbox"
                  checked={saveModel}
                  onChange={(e) => setSaveModel(e.target.checked)}
                  className="accent-amber-500 rounded"
                />
                保存模型
              </label>
              <button
                type="submit"
                disabled={busyAction !== null}
                className="primary-button"
              >
                <Play className="size-3" />
                {busyAction === "train" ? "训练中…" : "运行验证"}
              </button>
            </div>
          </form>

          {!trainReport ? (
            <div className="empty-state py-12 text-center text-xs text-slate-400">
              设置参数后运行样本外验证。
            </div>
          ) : (
            <div className="space-y-4 text-xs">
              <div className="flex flex-wrap items-center gap-4 rounded-xl border border-white/[0.06] bg-black/40 px-4 py-3 backdrop-blur-md">
                <span>
                  标的 <strong className="text-slate-100">{trainReport.product_code}</strong> ({trainReport.source_mode})
                </span>
                {Object.entries(trainReport.sample_counts).map(([h, cnt]) => (
                  <span key={h}>
                    {h} 样本数 <strong className="text-amber-300">{cnt}</strong>
                  </span>
                ))}
                {trainReport.model_output_dir && (
                  <span className="text-emerald-300">模型目录 {trainReport.model_output_dir}</span>
                )}
              </div>

              {Object.entries(trainReport.walk_forward_folds).map(([h, folds]) => (
                <div key={h} className="space-y-2">
                  <h3 className="font-semibold text-slate-300 flex items-center gap-2">
                    <span className="data-label border-amber-500/30 bg-amber-500/10 text-amber-300">
                      周期 {h}
                    </span>
                    <span>样本外验证 ({folds.length})</span>
                  </h3>
                  {folds.length === 0 ? (
                    <div className="rounded-xl border border-white/[0.06] bg-black/40 px-4 py-6 text-center text-slate-400">
                      样本不足，至少需要 90 条。
                    </div>
                  ) : (
                    <div className="overflow-auto rounded-xl border border-white/[0.06] bg-[#101722]">
                      <table className="data-table w-full text-left text-xs">
                        <thead className="bg-black/60 text-[11px] text-slate-400 border-b border-white/[0.06]">
                          <tr>
                            <th className="px-4 py-2.5">折次</th>
                            <th className="px-4 py-2.5">训练 / 测试样本</th>
                            <th className="px-4 py-2.5">样本外 MAE</th>
                            <th className="px-4 py-2.5">方向准确率</th>
                            <th className="px-4 py-2.5">布里尔分数</th>
                            <th className="px-4 py-2.5">下界覆盖率</th>
                          </tr>
                        </thead>
                        <tbody className="divide-y divide-white/[0.04]">
                          {folds.map((f) => (
                            <tr key={f.fold_index} className="hover:bg-cyan-500/[0.03] transition-colors">
                              <td className="px-4 py-2.5 text-slate-300">#{f.fold_index}</td>
                              <td className="px-4 py-2.5 text-slate-300">
                                {f.train_count} / {f.test_count}
                              </td>
                              <td className="px-4 py-2.5 text-amber-300 font-semibold">{(f.mae * 100).toFixed(3)}%</td>
                              <td className="px-4 py-2.5 text-emerald-300 font-semibold">{(f.directional_accuracy * 100).toFixed(1)}%</td>
                              <td className="px-4 py-2.5 text-slate-200">{f.brier_score.toFixed(4)}</td>
                              <td className="px-4 py-2.5 text-cyan-300 font-semibold">{(f.lcb_coverage * 100).toFixed(1)}%</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

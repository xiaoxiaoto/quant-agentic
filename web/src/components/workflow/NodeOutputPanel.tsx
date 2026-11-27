import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import type {
  QlibBacktestReport,
  QlibExportReport,
  QlibFactorEvaluateReport,
  QlibSignalResponse,
  QlibTrainReport,
  TimesFMBacktestReport,
  TimesFMExportReport,
  TimesFMPrepareReport,
  TimesFMTrainReport,
  WalkForwardFoldReport,
} from "../../api/types";
import type { WorkflowNodeRuntime, WorkflowPipelineId } from "./types";

function FoldsTable({ folds }: { folds: WalkForwardFoldReport[] }) {
  if (folds.length === 0) {
    return <p className="text-xs text-slate-400">暂无有效滚动验证折次。</p>;
  }
  return (
    <div className="overflow-x-auto rounded-lg border border-white/[0.06] bg-[#101722]">
      <table className="data-table w-full text-left text-[11px]">
        <thead className="border-b border-white/[0.06] bg-black/40 text-slate-400">
          <tr>
            <th className="px-2.5 py-1.5">折</th>
            <th className="px-2.5 py-1.5">训练/测试</th>
            <th className="px-2.5 py-1.5">MAE</th>
            <th className="px-2.5 py-1.5">方向率</th>
            <th className="px-2.5 py-1.5">LCB覆盖</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-white/[0.04]">
          {folds.map((f) => (
            <tr key={f.fold_index}>
              <td className="px-2.5 py-1.5 text-slate-300">#{f.fold_index}</td>
              <td className="px-2.5 py-1.5 text-slate-300">
                {f.train_count}/{f.test_count}
              </td>
              <td className="px-2.5 py-1.5 text-amber-300">{(f.mae * 100).toFixed(3)}%</td>
              <td className="px-2.5 py-1.5 text-emerald-300">{(f.directional_accuracy * 100).toFixed(1)}%</td>
              <td className="px-2.5 py-1.5 text-cyan-300">{(f.lcb_coverage * 100).toFixed(1)}%</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RawJsonViewer({ data }: { data: unknown }) {
  const [open, setOpen] = useState(false);
  const formatted = useMemo(() => (open ? JSON.stringify(data, null, 2) : ""), [open, data]);

  return (
    <div className="rounded-lg border border-white/[0.07] bg-black/30">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between px-3 py-2 text-xs text-slate-400 hover:text-slate-200"
      >
        <span>原始 JSON 输出</span>
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
      </button>
      {open && (
        <pre className="max-h-60 overflow-auto border-t border-white/[0.06] p-3 font-mono text-[11px] text-slate-300">
          {formatted}
        </pre>
      )}
    </div>
  );
}

interface NodeOutputPanelProps {
  pipeline: WorkflowPipelineId;
  nodeId: string;
  runtime: WorkflowNodeRuntime;
}

export function NodeOutputPanel({ pipeline, nodeId, runtime }: NodeOutputPanelProps) {
  const rawKey = `${pipeline}:${nodeId}`;

  if (runtime.error) {
    return (
      <div className="space-y-3">
        <div className="flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 p-3 text-xs text-rose-200">
          <AlertCircle className="mt-0.5 size-4 shrink-0 text-rose-400" />
          <div className="space-y-1">
            <p className="font-semibold">节点执行失败</p>
            <p className="leading-relaxed">{runtime.error}</p>
          </div>
        </div>
      </div>
    );
  }

  if (!runtime.output) {
    return (
      <div className="rounded-lg border border-dashed border-white/10 bg-black/20 px-4 py-10 text-center text-xs text-slate-400">
        尚未执行此节点。点击下方「执行此节点」或在顶部运行整条工作流。
      </div>
    );
  }

  if (pipeline === "timesfm") {
    if (nodeId === "prepare") {
      const rep = runtime.output as TimesFMPrepareReport;
      return (
        <div className="space-y-3 text-xs">
          <div className="grid grid-cols-2 gap-2">
            <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
              <p className="text-slate-400">数据集 ID</p>
              <p className="mt-0.5 truncate font-mono text-slate-100">{rep.manifest.dataset_id}</p>
            </div>
            <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
              <p className="text-slate-400">序列条数 / 文件</p>
              <p className="mt-0.5 font-semibold text-amber-300">
                {rep.manifest.total_bars} 条 · {rep.written_files} 文件
              </p>
            </div>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
            <p className="text-slate-400">各持有期有效样本</p>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {Object.entries(rep.manifest.sample_counts).map(([h, cnt]) => (
                <span key={h} className="rounded border border-amber-400/25 bg-amber-400/10 px-2 py-0.5 text-amber-200">
                  {h}: {cnt}
                </span>
              ))}
            </div>
          </div>
          <RawJsonViewer key={rawKey} data={rep} />
        </div>
      );
    }

    if (nodeId === "train") {
      const rep = runtime.output as TimesFMTrainReport;
      return (
        <div className="space-y-3 text-xs">
          <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
            <p className="text-slate-400">
              Run ID：<span className="font-mono text-cyan-300">{rep.experiment.run_id}</span>
            </p>
            <p className="mt-1 text-slate-400">
              暂存目录：<span className="font-mono text-slate-200">{rep.experiment.staging_dir}</span>
            </p>
          </div>
          {Object.entries(rep.walk_forward_folds).map(([h, folds]) => (
            <div key={h} className="space-y-1.5">
              <p className="font-semibold text-slate-200">持有期 {h}（样本 {rep.sample_counts[h] ?? 0}）</p>
              <FoldsTable folds={folds} />
            </div>
          ))}
          <RawJsonViewer key={rawKey} data={rep} />
        </div>
      );
    }

    if (nodeId === "backtest") {
      const rep = runtime.output as TimesFMBacktestReport;
      return (
        <div className="space-y-3 text-xs">
          {rep.backtest_summary && (
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
                <p className="text-slate-400">策略总收益</p>
                <p className="mt-0.5 text-sm font-semibold text-emerald-300">
                  {(rep.backtest_summary.shadow_report.total_return_pct * 100).toFixed(2)}%
                </p>
              </div>
              <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
                <p className="text-slate-400">最大回撤 / 成交</p>
                <p className="mt-0.5 text-sm font-semibold text-amber-200">
                  {(rep.backtest_summary.shadow_report.max_drawdown_pct * 100).toFixed(2)}% ·{" "}
                  {rep.backtest_summary.shadow_report.total_trades} 笔
                </p>
              </div>
            </div>
          )}
          <div className="overflow-x-auto rounded-lg border border-white/[0.06] bg-[#101722]">
            <table className="data-table w-full text-left text-[11px]">
              <thead className="border-b border-white/[0.06] bg-black/40 text-slate-400">
                <tr>
                  <th className="px-2.5 py-1.5">周期</th>
                  <th className="px-2.5 py-1.5">MAE</th>
                  <th className="px-2.5 py-1.5">方向率</th>
                  <th className="px-2.5 py-1.5">IC / RankIC</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {Object.entries(rep.horizon_metrics).map(([h, m]) => (
                  <tr key={h}>
                    <td className="px-2.5 py-1.5 text-slate-200">{h}</td>
                    <td className="px-2.5 py-1.5 text-amber-300">{(m.mae * 100).toFixed(3)}%</td>
                    <td className="px-2.5 py-1.5 text-emerald-300">{(m.directional_accuracy * 100).toFixed(1)}%</td>
                    <td className="px-2.5 py-1.5 text-cyan-300">
                      {m.ic !== null ? m.ic.toFixed(3) : "-"} / {m.rank_ic !== null ? m.rank_ic.toFixed(3) : "-"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <RawJsonViewer key={rawKey} data={rep} />
        </div>
      );
    }

    const rep = runtime.output as TimesFMExportReport;
    return (
      <div className="space-y-3 text-xs">
        <div className="flex items-center gap-2 rounded-lg border border-emerald-400/30 bg-emerald-500/10 p-2.5 text-emerald-200">
          <CheckCircle2 className="size-4 shrink-0" />
          <span>
            {rep.verified ? "不可变模型包回读自检通过" : "校验未通过"} · {Object.keys(rep.files).length} 个文件
          </span>
        </div>
        <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5 font-mono text-[11px] text-slate-300">
          <p className="truncate">目录: {rep.output_model_dir}</p>
          <p className="mt-1 truncate text-slate-400">校验文件: {rep.checksum_path}</p>
        </div>
        <RawJsonViewer key={rawKey} data={rep} />
      </div>
    );
  }

  if (nodeId === "export") {
    const rep = runtime.output as QlibExportReport;
    return (
      <div className="space-y-3 text-xs">
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
            <p className="text-slate-400">日历时点数</p>
            <p className="mt-0.5 font-semibold text-amber-300">{rep.manifest.calendar_count}</p>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
            <p className="text-slate-400">导出文件数</p>
            <p className="mt-0.5 font-semibold text-cyan-300">{rep.written_files}</p>
          </div>
        </div>
        <p className="truncate font-mono text-[11px] text-slate-400">provider_uri: {rep.manifest.provider_uri}</p>
        <RawJsonViewer key={rawKey} data={rep} />
      </div>
    );
  }

  if (nodeId === "factors") {
    const rep = runtime.output as QlibFactorEvaluateReport;
    return (
      <div className="space-y-3 text-xs">
        <div className="max-h-64 overflow-auto rounded-lg border border-white/[0.06] bg-[#101722]">
          <table className="data-table w-full text-left text-[11px]">
            <thead className="border-b border-white/[0.06] bg-black/40 text-slate-400">
              <tr>
                <th className="px-2.5 py-1.5">因子</th>
                <th className="px-2.5 py-1.5">IC</th>
                <th className="px-2.5 py-1.5">RankIC</th>
                <th className="px-2.5 py-1.5">覆盖率</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/[0.04]">
              {rep.metrics.map((m) => (
                <tr key={m.factor_name}>
                  <td className="px-2.5 py-1.5 font-mono text-slate-200">{m.factor_name}</td>
                  <td className="px-2.5 py-1.5 text-amber-300">{m.ic !== null ? m.ic.toFixed(4) : "-"}</td>
                  <td className="px-2.5 py-1.5 text-emerald-300">{m.rank_ic !== null ? m.rank_ic.toFixed(4) : "-"}</td>
                  <td className="px-2.5 py-1.5 text-slate-300">{(m.coverage_ratio * 100).toFixed(0)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <RawJsonViewer key={rawKey} data={rep} />
      </div>
    );
  }

  if (nodeId === "train") {
    const rep = runtime.output as QlibTrainReport;
    return (
      <div className="space-y-3 text-xs">
        <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
          <p className="text-slate-400">
            Run ID：<span className="font-mono text-cyan-300">{rep.experiment.run_id}</span>
          </p>
          {rep.experiment.model_output_dir && (
            <p className="mt-1 truncate text-emerald-300">模型产物：{rep.experiment.model_output_dir}</p>
          )}
        </div>
        {Object.entries(rep.walk_forward_folds).map(([h, folds]) => (
          <div key={h} className="space-y-1.5">
            <p className="font-semibold text-slate-200">持有期 {h}（样本 {rep.sample_counts[h] ?? 0}）</p>
            <FoldsTable folds={folds} />
          </div>
        ))}
        <RawJsonViewer key={rawKey} data={rep} />
      </div>
    );
  }

  if (nodeId === "backtest") {
    const rep = runtime.output as QlibBacktestReport;
    const s = rep.summary;
    return (
      <div className="space-y-3 text-xs">
        <div className="grid grid-cols-2 gap-2">
          <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
            <p className="text-slate-400">策略累计 / 基准</p>
            <p className="mt-0.5 font-semibold text-emerald-300">
              {(s.total_return * 100).toFixed(2)}% / {(s.benchmark_return * 100).toFixed(2)}%
            </p>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
            <p className="text-slate-400">夏普 / 最大回撤</p>
            <p className="mt-0.5 font-semibold text-amber-300">
              {s.sharpe_ratio.toFixed(2)} / {(s.max_drawdown * 100).toFixed(2)}%
            </p>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
            <p className="text-slate-400">胜率 / 回合数</p>
            <p className="mt-0.5 font-semibold text-cyan-300">
              {(s.win_rate * 100).toFixed(1)}% ({s.round_trip_count})
            </p>
          </div>
          <div className="rounded-lg border border-white/[0.06] bg-black/30 p-2.5">
            <p className="text-slate-400">成交 / 费用</p>
            <p className="mt-0.5 font-semibold text-slate-200">
              {s.trade_count} 笔 · ¥{s.total_fees.toFixed(1)}
            </p>
          </div>
        </div>
        <RawJsonViewer key={rawKey} data={rep} />
      </div>
    );
  }

  const rep = runtime.output as QlibSignalResponse;
  return (
    <div className="space-y-3 text-xs">
      <div className="rounded-lg border border-amber-400/30 bg-amber-400/10 p-3">
        <div className="flex items-center justify-between">
          <span className="font-semibold text-amber-200">{rep.signal.instrument} 盘前建议</span>
          <span className="rounded bg-black/50 px-2 py-0.5 font-mono text-sm font-bold text-white">
            {rep.signal.action}
          </span>
        </div>
        <div className="mt-2 grid grid-cols-2 gap-2 text-[11px] text-slate-300">
          <span>预期收益：{(rep.signal.predicted_return * 100).toFixed(3)}%</span>
          <span>胜率：{(rep.signal.win_probability * 100).toFixed(1)}%</span>
          <span>净收益下界：{(rep.signal.net_return_lcb * 100).toFixed(3)}%</span>
          <span>最新收盘：¥{rep.signal.latest_close.toFixed(2)}</span>
        </div>
        {rep.signal.alert_id && (
          <p className="mt-2 text-[11px] text-emerald-300">已推送人工复核提醒：{rep.signal.alert_id}</p>
        )}
      </div>
      <RawJsonViewer key={rawKey} data={rep} />
    </div>
  );
}

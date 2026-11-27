import { ArrowDownToLine, ChevronDown, ChevronRight, History } from "lucide-react";
import { useState } from "react";
import type { QlibExperimentRecord, TimesFMExperimentRecord } from "../../api/types";
import type { WorkflowPipelineId } from "./types";

interface ExperimentsDrawerProps {
  pipeline: WorkflowPipelineId;
  timesfmExperiments: TimesFMExperimentRecord[];
  qlibExperiments: QlibExperimentRecord[];
  onLoadTimesFMExperiment: (exp: TimesFMExperimentRecord) => void;
  onLoadQlibExperiment: (exp: QlibExperimentRecord) => void;
}

export function ExperimentsDrawer({
  pipeline,
  timesfmExperiments,
  qlibExperiments,
  onLoadTimesFMExperiment,
  onLoadQlibExperiment,
}: ExperimentsDrawerProps) {
  const [expanded, setExpanded] = useState(false);
  const count = pipeline === "timesfm" ? timesfmExperiments.length : qlibExperiments.length;

  return (
    <section className="rounded-xl border border-white/[0.08] bg-[#131b27]">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-left text-sm font-medium text-slate-200 hover:bg-white/[0.02]"
      >
        <span className="inline-flex items-center gap-2">
          <History className="size-4 text-[#f0d5a0]" />
          历史实验记录（{pipeline === "timesfm" ? "TimesFM" : "Qlib"}）
          <span className="rounded-md bg-white/[0.06] px-2 py-0.5 text-xs text-slate-400">{count}</span>
        </span>
        {expanded ? <ChevronDown className="size-4 text-slate-400" /> : <ChevronRight className="size-4 text-slate-400" />}
      </button>

      {expanded && (
        <div className="border-t border-white/[0.06] p-3">
          {count === 0 ? (
            <p className="py-6 text-center text-xs text-slate-400">暂无已登记的实验记录。</p>
          ) : pipeline === "timesfm" ? (
            <div className="overflow-x-auto rounded-lg border border-white/[0.06] bg-[#101722]">
              <table className="data-table w-full text-left text-xs">
                <thead className="border-b border-white/[0.06] bg-black/40 text-[11px] text-slate-400">
                  <tr>
                    <th className="px-3 py-2">实验 / Run ID</th>
                    <th className="px-3 py-2">标的 / 频率</th>
                    <th className="px-3 py-2">模式 / 融合头</th>
                    <th className="px-3 py-2">持有期</th>
                    <th className="px-3 py-2">导出产物</th>
                    <th className="px-3 py-2 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04]">
                  {timesfmExperiments.map((exp) => (
                    <tr key={exp.run_id} className="hover:bg-white/[0.02]">
                      <td className="px-3 py-2">
                        <p className="font-medium text-slate-100">{exp.experiment_name}</p>
                        <p className="font-mono text-[11px] text-slate-400">{exp.run_id}</p>
                      </td>
                      <td className="px-3 py-2 text-slate-300">
                        {exp.product_code} ({exp.freq})
                      </td>
                      <td className="px-3 py-2 text-slate-300">
                        {exp.train_mode} / {exp.head_model_type}
                      </td>
                      <td className="px-3 py-2 text-amber-200">{exp.horizons.join(", ")}</td>
                      <td className="max-w-[180px] truncate px-3 py-2 font-mono text-[11px] text-emerald-300">
                        {exp.exported_model_dir ?? "仅暂存"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => onLoadTimesFMExperiment(exp)}
                          className="secondary-button !min-h-7 px-2.5 text-[11px]"
                        >
                          <ArrowDownToLine className="size-3" />
                          载入下游
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-white/[0.06] bg-[#101722]">
              <table className="data-table w-full text-left text-xs">
                <thead className="border-b border-white/[0.06] bg-black/40 text-[11px] text-slate-400">
                  <tr>
                    <th className="px-3 py-2">实验 / Run ID</th>
                    <th className="px-3 py-2">模型 / 因子预设</th>
                    <th className="px-3 py-2">频率 / 持有期</th>
                    <th className="px-3 py-2">模型目录</th>
                    <th className="px-3 py-2 text-right">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/[0.04]">
                  {qlibExperiments.map((exp) => (
                    <tr key={exp.run_id} className="hover:bg-white/[0.02]">
                      <td className="px-3 py-2">
                        <p className="font-medium text-slate-100">{exp.experiment_name}</p>
                        <p className="font-mono text-[11px] text-slate-400">{exp.run_id}</p>
                      </td>
                      <td className="px-3 py-2 text-slate-300">
                        {exp.model_type.toUpperCase()} / {exp.factor_preset}
                      </td>
                      <td className="px-3 py-2 text-amber-200">
                        {exp.freq} · {exp.horizons.join(", ")}
                      </td>
                      <td className="max-w-[180px] truncate px-3 py-2 font-mono text-[11px] text-emerald-300">
                        {exp.model_output_dir ?? "未导出"}
                      </td>
                      <td className="px-3 py-2 text-right">
                        <button
                          type="button"
                          onClick={() => onLoadQlibExperiment(exp)}
                          className="secondary-button !min-h-7 px-2.5 text-[11px]"
                        >
                          <ArrowDownToLine className="size-3" />
                          载入下游
                        </button>
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
  );
}

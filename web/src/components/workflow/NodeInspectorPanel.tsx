import { AlertCircle, Play, SlidersHorizontal, TerminalSquare } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { NodeOutputPanel } from "./NodeOutputPanel";
import { QlibParamForm } from "./QlibParamForm";
import { TimesFMParamForm } from "./TimesFMParamForm";
import type {
  QlibNodeParamsMap,
  TimesFMNodeParamsMap,
  WorkflowNodeDefinition,
  WorkflowNodeRuntime,
  WorkflowPipelineId,
} from "./types";
import { validateWorkflowNodeParams } from "./validation";

interface NodeInspectorPanelProps {
  pipeline: WorkflowPipelineId;
  nodeDef: WorkflowNodeDefinition;
  runtime: WorkflowNodeRuntime;
  busy: boolean;
  timesfmParams: TimesFMNodeParamsMap;
  qlibParams: QlibNodeParamsMap;
  linkedHints: Record<string, string | null | undefined>;
  onUpdateTimesFMParams: <K extends keyof TimesFMNodeParamsMap>(
    nodeId: K,
    patch: Partial<TimesFMNodeParamsMap[K]>
  ) => void;
  onUpdateQlibParams: <K extends keyof QlibNodeParamsMap>(
    nodeId: K,
    patch: Partial<QlibNodeParamsMap[K]>
  ) => void;
  onRunNode: (nodeId: string) => void;
}

export function NodeInspectorPanel({
  pipeline,
  nodeDef,
  runtime,
  busy,
  timesfmParams,
  qlibParams,
  linkedHints,
  onUpdateTimesFMParams,
  onUpdateQlibParams,
  onRunNode,
}: NodeInspectorPanelProps) {
  const [tab, setTab] = useState<"params" | "output">("params");

  // 节点执行完成或报错后自动切到输出页便于第一时间检查结果
  useEffect(() => {
    if (runtime.status === "success" || runtime.status === "error") {
      setTab("output");
    }
  }, [runtime.status, runtime.updatedAt]);

  // 切换选中节点时默认回到参数配置（若尚未运行）
  useEffect(() => {
    if (runtime.output === null && runtime.error === null) {
      setTab("params");
    }
  }, [nodeDef.id, pipeline, runtime.output, runtime.error]);

  const validationError = useMemo(
    () => validateWorkflowNodeParams(pipeline, nodeDef.id, timesfmParams, qlibParams),
    [pipeline, nodeDef.id, timesfmParams, qlibParams]
  );

  return (
    <aside className="flex h-full flex-col rounded-xl border border-white/[0.08] bg-[#131b27]">
      {/* 检查器头部 */}
      <div className="flex items-center justify-between gap-2 border-b border-white/[0.06] px-4 py-3">
        <div className="min-w-0">
          <h3 className="truncate text-sm font-semibold text-white">{nodeDef.title}</h3>
          <p className="truncate font-mono text-[11px] text-slate-400">{nodeDef.endpoint}</p>
        </div>
        <button
          type="button"
          disabled={busy || validationError !== null}
          title={validationError ?? `执行「${nodeDef.title}」`}
          onClick={() => onRunNode(nodeDef.id)}
          className="primary-button !min-h-8 shrink-0 px-3 text-xs"
        >
          <Play className="size-3.5 fill-current" />
          执行此节点
        </button>
      </div>

      {/* n8n 风格双 Tab：参数配置 / 输出数据 */}
      <div className="grid grid-cols-2 border-b border-white/[0.06] bg-[#101722] p-1 text-xs">
        <button
          type="button"
          onClick={() => setTab("params")}
          className={`inline-flex items-center justify-center gap-1.5 rounded-md py-1.5 font-medium transition-colors ${
            tab === "params" ? "bg-[#f0d5a0]/15 text-[#f0d5a0]" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <SlidersHorizontal className="size-3.5" />
          参数配置
        </button>
        <button
          type="button"
          onClick={() => setTab("output")}
          className={`inline-flex items-center justify-center gap-1.5 rounded-md py-1.5 font-medium transition-colors ${
            tab === "output" ? "bg-[#f0d5a0]/15 text-[#f0d5a0]" : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <TerminalSquare className="size-3.5" />
          输出数据
          {runtime.output !== null && <span className="size-1.5 rounded-full bg-emerald-400" />}
          {runtime.error !== null && <span className="size-1.5 rounded-full bg-rose-400" />}
        </button>
      </div>

      {/* 内容滚动区 */}
      <div className="flex-1 overflow-y-auto p-4">
        {tab === "params" ? (
          <div className="space-y-3">
            {validationError && (
              <div
                role="alert"
                className="flex items-start gap-2 rounded-lg border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
              >
                <AlertCircle className="mt-0.5 size-3.5 shrink-0 text-rose-400" />
                <span>{validationError}</span>
              </div>
            )}
            {pipeline === "timesfm" ? (
              <TimesFMParamForm
                nodeId={nodeDef.id}
                params={timesfmParams}
                linkedHints={linkedHints}
                onUpdate={onUpdateTimesFMParams}
              />
            ) : (
              <QlibParamForm
                nodeId={nodeDef.id}
                params={qlibParams}
                linkedHints={linkedHints}
                onUpdate={onUpdateQlibParams}
              />
            )}
          </div>
        ) : (
          <NodeOutputPanel pipeline={pipeline} nodeId={nodeDef.id} runtime={runtime} />
        )}
      </div>
    </aside>
  );
}

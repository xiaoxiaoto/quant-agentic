import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  Cpu,
  Database,
  Loader2,
  Package,
  Play,
  Power,
  Radio,
  Sparkles,
} from "lucide-react";
import { memo } from "react";
import type { SummaryChip, WorkflowNodeIconKey, WorkflowNodeStatus } from "./types";

export interface WorkflowNodeCardData extends Record<string, unknown> {
  nodeId: string;
  title: string;
  subtitle: string;
  endpoint: string;
  icon: WorkflowNodeIconKey;
  hasInput: boolean;
  hasOutput: boolean;
  enabled: boolean;
  status: WorkflowNodeStatus;
  error: string | null;
  durationMs: number | null;
  chips: SummaryChip[];
  busy: boolean;
  onRunNode: (nodeId: string) => void;
  onToggleEnabled: (nodeId: string) => void;
}

export type WorkflowFlowNode = Node<WorkflowNodeCardData, "workflowNode">;

const ICON_MAP = {
  database: Database,
  cpu: Cpu,
  activity: Activity,
  package: Package,
  sparkles: Sparkles,
  radio: Radio,
};

const STATUS_META: Record<
  WorkflowNodeStatus,
  { label: string; borderClass: string; badgeClass: string; iconTone: string }
> = {
  idle: {
    label: "待运行",
    borderClass: "border-white/12 hover:border-amber-300/45",
    badgeClass: "bg-slate-800/90 text-slate-300 border-white/10",
    iconTone: "bg-slate-800/90 text-amber-200 border-white/10",
  },
  running: {
    label: "运行中",
    borderClass: "border-amber-300/80 shadow-[0_0_24px_rgba(240,213,160,0.22)] workflow-node-running",
    badgeClass: "bg-amber-400/15 text-amber-200 border-amber-300/40",
    iconTone: "bg-amber-400/18 text-amber-200 border-amber-300/40",
  },
  success: {
    label: "已完成",
    borderClass: "border-emerald-400/55 shadow-[0_0_18px_rgba(52,211,153,0.14)]",
    badgeClass: "bg-emerald-400/15 text-emerald-200 border-emerald-400/35",
    iconTone: "bg-emerald-500/15 text-emerald-300 border-emerald-400/35",
  },
  error: {
    label: "失败",
    borderClass: "border-rose-400/65 shadow-[0_0_18px_rgba(251,113,133,0.16)]",
    badgeClass: "bg-rose-500/15 text-rose-200 border-rose-400/35",
    iconTone: "bg-rose-500/15 text-rose-300 border-rose-400/35",
  },
  skipped: {
    label: "已跳过",
    borderClass: "border-white/8 opacity-60",
    badgeClass: "bg-slate-900/90 text-slate-400 border-white/10",
    iconTone: "bg-slate-900/90 text-slate-500 border-white/8",
  },
};

const CHIP_TONES: Record<NonNullable<SummaryChip["tone"]>, string> = {
  default: "border-white/10 bg-white/[0.04] text-slate-300",
  gold: "border-amber-400/30 bg-amber-400/10 text-amber-200",
  emerald: "border-emerald-400/30 bg-emerald-400/10 text-emerald-200",
  cyan: "border-cyan-400/30 bg-cyan-400/10 text-cyan-200",
  rose: "border-rose-400/30 bg-rose-400/10 text-rose-200",
};

export const WorkflowNodeCard = memo(function WorkflowNodeCard({ data, selected }: NodeProps<WorkflowFlowNode>) {
  const Icon = ICON_MAP[data.icon];
  const effectiveStatus: WorkflowNodeStatus = !data.enabled && data.status === "idle" ? "skipped" : data.status;
  const meta = STATUS_META[effectiveStatus];

  return (
    <div
      className={`workflow-node w-[252px] rounded-xl border bg-[#131b27]/95 p-3 text-left transition-all ${meta.borderClass} ${
        selected ? "ring-2 ring-[#f0d5a0] ring-offset-2 ring-offset-[#0b1018]" : ""
      } ${!data.enabled ? "opacity-65" : ""}`}
    >
      {data.hasInput && (
        <Handle
          type="target"
          position={Position.Left}
          className="!h-3 !w-3 !border-2 !border-[#0b1018] !bg-[#f0d5a0]"
        />
      )}

      {/* 顶部图标 + 标题 + 快捷动作 */}
      <div className="flex items-start gap-2.5">
        <div className={`flex size-9 shrink-0 items-center justify-center rounded-lg border ${meta.iconTone}`}>
          {effectiveStatus === "running" ? (
            <Loader2 className="size-4 animate-spin" />
          ) : (
            <Icon className="size-4" />
          )}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-1.5">
            <h3 className="truncate text-sm font-semibold text-slate-100">{data.title}</h3>
            <div className="nodrag nopan flex items-center gap-1">
              <button
                type="button"
                disabled={data.busy}
                title={data.enabled ? "禁用此节点（全流程时跳过）" : "启用此节点"}
                aria-label={`${data.enabled ? "禁用" : "启用"}${data.title}`}
                onClick={(e) => {
                  e.stopPropagation();
                  data.onToggleEnabled(data.nodeId);
                }}
                className={`rounded-md p-1 transition-colors disabled:opacity-40 ${
                  data.enabled
                    ? "text-slate-400 hover:bg-white/10 hover:text-slate-100"
                    : "bg-white/5 text-slate-500 hover:text-amber-200"
                }`}
              >
                <Power className="size-3.5" />
              </button>
              <button
                type="button"
                title={`单独运行「${data.title}」`}
                aria-label={`运行${data.title}`}
                disabled={data.busy}
                onClick={(e) => {
                  e.stopPropagation();
                  data.onRunNode(data.nodeId);
                }}
                className="rounded-md bg-[#f0d5a0]/15 p-1 text-[#f0d5a0] transition-colors hover:bg-[#f0d5a0] hover:text-[#21190f] disabled:opacity-40"
              >
                <Play className="size-3.5 fill-current" />
              </button>
            </div>
          </div>
          <p className="truncate text-[11px] text-slate-400">{data.subtitle}</p>
        </div>
      </div>

      {/* 状态行 */}
      <div className="mt-2.5 flex items-center justify-between gap-2 border-t border-white/[0.06] pt-2 text-[11px]">
        <span className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 font-medium ${meta.badgeClass}`}>
          {effectiveStatus === "running" && <Loader2 className="size-3 animate-spin" />}
          {effectiveStatus === "success" && <CheckCircle2 className="size-3" />}
          {effectiveStatus === "error" && <AlertCircle className="size-3" />}
          {meta.label}
        </span>
        <span className="truncate font-mono text-[10px] text-slate-500">
          {data.durationMs !== null ? `${(data.durationMs / 1000).toFixed(1)}s` : data.endpoint}
        </span>
      </div>

      {/* 结果摘要芯片或错误提示 */}
      {data.error ? (
        <p className="mt-2 line-clamp-2 rounded-md border border-rose-500/30 bg-rose-500/10 px-2 py-1 text-[11px] text-rose-200">
          {data.error}
        </p>
      ) : data.chips.length > 0 ? (
        <div className="mt-2 flex flex-wrap gap-1">
          {data.chips.map((chip) => (
            <span
              key={chip.label}
              className={`rounded border px-1.5 py-0.5 text-[10px] font-medium ${CHIP_TONES[chip.tone ?? "default"]}`}
            >
              {chip.label}
            </span>
          ))}
        </div>
      ) : null}

      {data.hasOutput && (
        <Handle
          type="source"
          position={Position.Right}
          className="!h-3 !w-3 !border-2 !border-[#0b1018] !bg-[#f0d5a0]"
        />
      )}
    </div>
  );
});

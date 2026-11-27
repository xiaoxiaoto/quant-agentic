import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  ReactFlow,
  type Edge,
  type NodeTypes,
  type OnNodeDrag,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useState } from "react";
import { WorkflowNodeCard, type WorkflowFlowNode } from "./WorkflowNodeCard";
import {
  extractNodeSummaryChips,
  type WorkflowEdgeDefinition,
  type WorkflowNodeDefinition,
  type WorkflowNodeRuntime,
  type WorkflowPipelineId,
} from "./types";

interface WorkflowCanvasProps {
  pipeline: WorkflowPipelineId;
  nodeDefs: WorkflowNodeDefinition[];
  edgeDefs: WorkflowEdgeDefinition[];
  runtimes: Record<string, WorkflowNodeRuntime>;
  selectedNodeId: string;
  busy: boolean;
  onSelectNode: (nodeId: string) => void;
  onRunNode: (nodeId: string) => void;
  onToggleEnabled: (nodeId: string) => void;
}

const NODE_TYPES: NodeTypes = {
  workflowNode: WorkflowNodeCard,
};

export function WorkflowCanvas({
  pipeline,
  nodeDefs,
  edgeDefs,
  runtimes,
  selectedNodeId,
  busy,
  onSelectNode,
  onRunNode,
  onToggleEnabled,
}: WorkflowCanvasProps) {
  // 允许用户在画布内拖拽微调节点位置，切换工作流时恢复默认拓扑坐标
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>(() =>
    Object.fromEntries(nodeDefs.map((def) => [def.id, def.position]))
  );

  useEffect(() => {
    setPositions(Object.fromEntries(nodeDefs.map((def) => [def.id, def.position])));
  }, [pipeline, nodeDefs]);

  const nodes: WorkflowFlowNode[] = useMemo(
    () =>
      nodeDefs.map((def) => {
        const rt = runtimes[def.id];
        return {
          id: def.id,
          type: "workflowNode",
          position: positions[def.id] ?? def.position,
          selected: def.id === selectedNodeId,
          data: {
            nodeId: def.id,
            title: def.title,
            subtitle: def.subtitle,
            endpoint: def.endpoint,
            icon: def.icon,
            hasInput: def.hasInput,
            hasOutput: def.hasOutput,
            enabled: rt?.enabled ?? true,
            status: rt?.status ?? "idle",
            error: rt?.error ?? null,
            durationMs: rt?.durationMs ?? null,
            chips: extractNodeSummaryChips(pipeline, def.id, rt?.output),
            busy,
            onRunNode,
            onToggleEnabled,
          },
        };
      }),
    [nodeDefs, positions, runtimes, selectedNodeId, pipeline, busy, onRunNode, onToggleEnabled]
  );

  const edges: Edge[] = useMemo(
    () =>
      edgeDefs.map((edgeDef) => {
        const srcStatus = runtimes[edgeDef.source]?.status ?? "idle";
        const tgtRuntime = runtimes[edgeDef.target];
        const tgtStatus = tgtRuntime?.status ?? "idle";
        const tgtDisabled = tgtRuntime ? !tgtRuntime.enabled : false;

        const isFlowing = tgtStatus === "running" || (srcStatus === "running" && !tgtDisabled);
        const isCompleted = srcStatus === "success" && tgtStatus === "success";
        const isReady = srcStatus === "success" && !tgtDisabled;

        const stroke = tgtDisabled
          ? "#475569"
          : isFlowing
            ? "#f0d5a0"
            : isCompleted
              ? "#34d399"
              : isReady
                ? "#93c5fd"
                : "#475569";

        return {
          id: edgeDef.id,
          source: edgeDef.source,
          target: edgeDef.target,
          type: "smoothstep",
          animated: isFlowing,
          label: edgeDef.label,
          labelStyle: {
            fill: isFlowing ? "#f0d5a0" : isCompleted ? "#a7f3d0" : "#94a3b8",
            fontSize: 10,
            fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
          },
          labelBgStyle: {
            fill: "#0b1018",
            fillOpacity: 0.9,
          },
          labelBgPadding: [6, 3] as [number, number],
          labelBgBorderRadius: 4,
          style: {
            stroke,
            strokeWidth: isFlowing || isCompleted ? 2.2 : 1.5,
            strokeDasharray: tgtDisabled ? "5 5" : undefined,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: stroke,
            width: 16,
            height: 16,
          },
        };
      }),
    [edgeDefs, runtimes]
  );

  const handleNodeDragStop: OnNodeDrag<WorkflowFlowNode> = useCallback((_event, node) => {
    setPositions((prev) => ({
      ...prev,
      [node.id]: node.position,
    }));
  }, []);

  const handleNodeClick = useCallback(
    (_event: unknown, node: WorkflowFlowNode) => {
      onSelectNode(node.id);
    },
    [onSelectNode]
  );

  return (
    <div className="workflow-canvas relative h-[430px] w-full overflow-hidden rounded-xl border border-white/[0.08] bg-[#0b1018] lg:h-[540px]">
      <ReactFlow
        key={pipeline}
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        onNodeDragStop={handleNodeDragStop}
        onNodeClick={handleNodeClick}
        fitView
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.5}
        maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} color="#253449" />
        <Controls showInteractive={false} />
      </ReactFlow>

      <div className="pointer-events-none absolute top-3 left-3 flex items-center gap-2 rounded-lg border border-white/[0.08] bg-[#0b1018]/85 px-2.5 py-1 text-[11px] text-slate-400 backdrop-blur-sm">
        <span>点击节点配置参数与查看输出 · 可拖拽节点与缩放画布</span>
      </div>
    </div>
  );
}

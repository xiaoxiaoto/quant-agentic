import { AlertTriangle, Cpu, Layers, Play, RefreshCw, RotateCcw, Square } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type {
  QlibExperimentRecord,
  QlibExportReport,
  QlibStatusResponse,
  QlibTrainReport,
  TimesFMExperimentRecord,
  TimesFMPrepareReport,
  TimesFMStatusResponse,
  TimesFMTrainReport,
} from "../../api/types";
import { ExperimentsDrawer } from "./ExperimentsDrawer";
import { NodeInspectorPanel } from "./NodeInspectorPanel";
import { WorkflowCanvas } from "./WorkflowCanvas";
import {
  QLIB_EDGE_DEFS,
  QLIB_NODE_DEFS,
  TIMESFM_EDGE_DEFS,
  TIMESFM_NODE_DEFS,
  createInitialNodeRuntime,
  createInitialQlibParams,
  createInitialTimesFMParams,
  type QlibNodeParamsMap,
  type TimesFMNodeParamsMap,
  type WorkflowNodeRuntime,
  type WorkflowPipelineId,
} from "./types";
import { validateWorkflowNodeParams } from "./validation";

interface ModelWorkflowStudioProps {
  onNotify: (type: "ok" | "error", text: string) => void;
}

function buildInitialRuntimes(ids: string[]): Record<string, WorkflowNodeRuntime> {
  return Object.fromEntries(ids.map((id) => [id, createInitialNodeRuntime()]));
}

export function ModelWorkflowStudio({ onNotify }: ModelWorkflowStudioProps) {
  const [pipeline, setPipeline] = useState<WorkflowPipelineId>("timesfm");
  const [selectedByPipeline, setSelectedByPipeline] = useState<Record<WorkflowPipelineId, string>>({
    timesfm: "prepare",
    qlib: "export",
  });

  const [timesfmStatus, setTimesfmStatus] = useState<TimesFMStatusResponse | null>(null);
  const [qlibStatus, setQlibStatus] = useState<QlibStatusResponse | null>(null);
  const [timesfmExperiments, setTimesfmExperiments] = useState<TimesFMExperimentRecord[]>([]);
  const [qlibExperiments, setQlibExperiments] = useState<QlibExperimentRecord[]>([]);
  const [loadingStatus, setLoadingStatus] = useState(false);
  const [metadataErrors, setMetadataErrors] = useState<Record<WorkflowPipelineId, string | null>>({
    timesfm: null,
    qlib: null,
  });

  const [timesfmParams, setTimesfmParams] = useState<TimesFMNodeParamsMap>(createInitialTimesFMParams);
  const [qlibParams, setQlibParams] = useState<QlibNodeParamsMap>(createInitialQlibParams);

  const [timesfmRuntimes, setTimesfmRuntimes] = useState<Record<string, WorkflowNodeRuntime>>(() =>
    buildInitialRuntimes(TIMESFM_NODE_DEFS.map((n) => n.id))
  );
  const [qlibRuntimes, setQlibRuntimes] = useState<Record<string, WorkflowNodeRuntime>>(() =>
    buildInitialRuntimes(QLIB_NODE_DEFS.map((n) => n.id))
  );

  const [runningPipeline, setRunningPipeline] = useState(false);
  const [runningNodeId, setRunningNodeId] = useState<string | null>(null);
  const stopRequestedRef = useRef(false);
  const metadataAbortRef = useRef<AbortController | null>(null);
  const metadataSeqRef = useRef(0);
  const runtimesByPipelineRef = useRef({
    timesfm: timesfmRuntimes,
    qlib: qlibRuntimes,
  });
  runtimesByPipelineRef.current = { timesfm: timesfmRuntimes, qlib: qlibRuntimes };

  const refreshMetadata = useCallback(async (target: WorkflowPipelineId) => {
    metadataAbortRef.current?.abort();
    const controller = new AbortController();
    metadataAbortRef.current = controller;
    const seq = ++metadataSeqRef.current;

    setLoadingStatus(true);
    setMetadataErrors((prev) => ({ ...prev, [target]: null }));
    try {
      if (target === "timesfm") {
        const [status, expRes] = await Promise.all([
          api.getTimesfmStatus(controller.signal),
          api.listTimesfmExperiments(20, controller.signal),
        ]);
        if (controller.signal.aborted || seq !== metadataSeqRef.current) return;
        setTimesfmStatus(status);
        setTimesfmExperiments(expRes.experiments);
      } else {
        const [status, expRes] = await Promise.all([
          api.getQlibStatus(controller.signal),
          api.listQlibExperiments(20, controller.signal),
        ]);
        if (controller.signal.aborted || seq !== metadataSeqRef.current) return;
        setQlibStatus(status);
        setQlibExperiments(expRes.experiments);
      }
    } catch (err) {
      if (controller.signal.aborted || (err instanceof DOMException && err.name === "AbortError")) {
        return;
      }
      if (seq === metadataSeqRef.current) {
        const message = err instanceof Error ? err.message : "元数据加载失败";
        setMetadataErrors((prev) => ({ ...prev, [target]: message }));
      }
    } finally {
      if (seq === metadataSeqRef.current) {
        setLoadingStatus(false);
      }
    }
  }, []);

  useEffect(() => {
    void refreshMetadata(pipeline);
    return () => {
      metadataAbortRef.current?.abort();
    };
  }, [pipeline, refreshMetadata]);

  const nodeDefs = pipeline === "timesfm" ? TIMESFM_NODE_DEFS : QLIB_NODE_DEFS;
  const edgeDefs = pipeline === "timesfm" ? TIMESFM_EDGE_DEFS : QLIB_EDGE_DEFS;
  const runtimes = pipeline === "timesfm" ? timesfmRuntimes : qlibRuntimes;
  const selectedNodeId = selectedByPipeline[pipeline];
  const selectedNodeDef = useMemo(
    () => nodeDefs.find((d) => d.id === selectedNodeId) ?? nodeDefs[0],
    [nodeDefs, selectedNodeId]
  );

  // 计算上游自动传递的上下文线索（用于 UI 展示与运行时自动回填）
  const linkedHints = useMemo(() => {
    if (pipeline === "timesfm") {
      const prepareOut = timesfmRuntimes.prepare?.output as TimesFMPrepareReport | null;
      const trainOut = timesfmRuntimes.train?.output as TimesFMTrainReport | null;
      return {
        dataset_dir: prepareOut?.manifest.dataset_dir ?? timesfmStatus?.latest_dataset?.dataset_dir ?? null,
        run_id: trainOut?.experiment.run_id ?? timesfmExperiments[0]?.run_id ?? null,
      };
    }
    const exportOut = qlibRuntimes.export?.output as QlibExportReport | null;
    const trainOut = qlibRuntimes.train?.output as QlibTrainReport | null;
    return {
      provider_uri: exportOut?.manifest.provider_uri ?? qlibStatus?.local_dataset?.provider_uri ?? null,
      model_dir: trainOut?.experiment.model_output_dir ?? qlibStatus?.active_model_dir ?? null,
    };
  }, [pipeline, timesfmRuntimes, qlibRuntimes, timesfmStatus, qlibStatus, timesfmExperiments]);

  const setNodeRuntime = useCallback(
    (targetPipeline: WorkflowPipelineId, nodeId: string, patch: Partial<WorkflowNodeRuntime>) => {
      const setter = targetPipeline === "timesfm" ? setTimesfmRuntimes : setQlibRuntimes;
      setter((prev) => ({
        ...prev,
        [nodeId]: { ...(prev[nodeId] ?? createInitialNodeRuntime()), ...patch },
      }));
    },
    []
  );

  const executeNode = useCallback(
    async (
      targetPipeline: WorkflowPipelineId,
      nodeId: string,
      overrides?: { dataset_dir?: string; run_id?: string; provider_uri?: string; model_dir?: string }
    ): Promise<unknown> => {
      const started = performance.now();
      setRunningNodeId(nodeId);
      setSelectedByPipeline((prev) => ({ ...prev, [targetPipeline]: nodeId }));
      setNodeRuntime(targetPipeline, nodeId, { status: "running", error: null });

      try {
        let result: unknown;
        if (targetPipeline === "timesfm") {
          if (nodeId === "prepare") {
            result = await api.prepareTimesfm(timesfmParams.prepare);
          } else if (nodeId === "train") {
            const prepareOut = timesfmRuntimes.prepare?.output as TimesFMPrepareReport | null;
            const datasetDir =
              timesfmParams.train.dataset_dir ?? overrides?.dataset_dir ?? prepareOut?.manifest.dataset_dir;
            result = await api.trainTimesfm({
              ...timesfmParams.train,
              dataset_dir: datasetDir,
            });
          } else if (nodeId === "backtest") {
            const trainOut = timesfmRuntimes.train?.output as TimesFMTrainReport | null;
            const runId = timesfmParams.backtest.run_id ?? overrides?.run_id ?? trainOut?.experiment.run_id;
            result = await api.backtestTimesfm({
              ...timesfmParams.backtest,
              run_id: runId,
            });
          } else if (nodeId === "export") {
            const trainOut = timesfmRuntimes.train?.output as TimesFMTrainReport | null;
            const runId = timesfmParams.export.run_id ?? overrides?.run_id ?? trainOut?.experiment.run_id;
            result = await api.exportTimesfm({
              ...timesfmParams.export,
              run_id: runId,
            });
          }
        } else {
          if (nodeId === "export") {
            result = await api.exportQlib(qlibParams.export);
          } else if (nodeId === "factors") {
            const exportOut = qlibRuntimes.export?.output as QlibExportReport | null;
            const providerUri =
              qlibParams.factors.provider_uri ?? overrides?.provider_uri ?? exportOut?.manifest.provider_uri;
            result = await api.evaluateQlibFactors({
              ...qlibParams.factors,
              provider_uri: providerUri,
            });
          } else if (nodeId === "train") {
            const exportOut = qlibRuntimes.export?.output as QlibExportReport | null;
            const providerUri =
              qlibParams.train.provider_uri ?? overrides?.provider_uri ?? exportOut?.manifest.provider_uri;
            result = await api.trainQlib({
              ...qlibParams.train,
              provider_uri: providerUri,
            });
          } else if (nodeId === "backtest") {
            const exportOut = qlibRuntimes.export?.output as QlibExportReport | null;
            const providerUri =
              qlibParams.backtest.provider_uri ?? overrides?.provider_uri ?? exportOut?.manifest.provider_uri;
            result = await api.backtestQlib({
              ...qlibParams.backtest,
              provider_uri: providerUri,
            });
          } else if (nodeId === "signal") {
            const exportOut = qlibRuntimes.export?.output as QlibExportReport | null;
            const trainOut = qlibRuntimes.train?.output as QlibTrainReport | null;
            const providerUri =
              qlibParams.signal.provider_uri ?? overrides?.provider_uri ?? exportOut?.manifest.provider_uri;
            const modelDir =
              qlibParams.signal.model_dir ?? overrides?.model_dir ?? trainOut?.experiment.model_output_dir ?? undefined;
            result = await api.generateQlibSignal({
              ...qlibParams.signal,
              provider_uri: providerUri,
              model_dir: modelDir,
            });
          }
        }

        const durationMs = Math.round(performance.now() - started);
        setNodeRuntime(targetPipeline, nodeId, {
          status: "success",
          output: result,
          error: null,
          durationMs,
          updatedAt: new Date().toISOString(),
        });
        return result;
      } catch (err) {
        const message = err instanceof Error ? err.message : "节点执行失败";
        const durationMs = Math.round(performance.now() - started);
        setNodeRuntime(targetPipeline, nodeId, {
          status: "error",
          error: message,
          durationMs,
          updatedAt: new Date().toISOString(),
        });
        throw err;
      } finally {
        setRunningNodeId(null);
      }
    },
    [timesfmParams, qlibParams, timesfmRuntimes, qlibRuntimes, setNodeRuntime]
  );

  const handleRunSingleNode = useCallback(
    async (nodeId: string) => {
      if (runningPipeline || runningNodeId !== null) return;
      const validationError = validateWorkflowNodeParams(pipeline, nodeId, timesfmParams, qlibParams);
      if (validationError) {
        onNotify("error", validationError);
        return;
      }
      const def = nodeDefs.find((n) => n.id === nodeId);
      try {
        await executeNode(pipeline, nodeId);
        onNotify("ok", `节点「${def?.title ?? nodeId}」执行完成`);
        void refreshMetadata(pipeline);
      } catch (err) {
        onNotify("error", err instanceof Error ? err.message : "节点执行失败");
      }
    },
    [
      runningPipeline,
      runningNodeId,
      pipeline,
      timesfmParams,
      qlibParams,
      nodeDefs,
      executeNode,
      onNotify,
      refreshMetadata,
    ]
  );

  const handleRunAllNodes = useCallback(async () => {
    if (runningPipeline || runningNodeId !== null) return;
    const orderedIds = nodeDefs.map((n) => n.id);
    const activeRuntimes = runtimesByPipelineRef.current[pipeline];
    for (const id of orderedIds) {
      if (!(activeRuntimes[id]?.enabled ?? true)) continue;
      const validationError = validateWorkflowNodeParams(pipeline, id, timesfmParams, qlibParams);
      if (validationError) {
        const def = nodeDefs.find((n) => n.id === id);
        onNotify("error", `节点「${def?.title ?? id}」参数无效：${validationError}`);
        return;
      }
    }

    stopRequestedRef.current = false;
    setRunningPipeline(true);

    const context: { dataset_dir?: string; run_id?: string; provider_uri?: string; model_dir?: string } = {};

    try {
      for (const id of orderedIds) {
        if (stopRequestedRef.current) {
          onNotify("ok", "已按请求停止后续节点执行");
          break;
        }
        const latestRuntimes = runtimesByPipelineRef.current[pipeline];
        if (!(latestRuntimes[id]?.enabled ?? true)) {
          setNodeRuntime(pipeline, id, { status: "skipped", error: null });
          continue;
        }
        const out = await executeNode(pipeline, id, context);
        if (pipeline === "timesfm") {
          if (id === "prepare" && out) {
            context.dataset_dir = (out as TimesFMPrepareReport).manifest.dataset_dir;
          } else if (id === "train" && out) {
            context.run_id = (out as TimesFMTrainReport).experiment.run_id;
          }
        } else {
          if (id === "export" && out) {
            context.provider_uri = (out as QlibExportReport).manifest.provider_uri;
          } else if (id === "train" && out) {
            const dir = (out as QlibTrainReport).experiment.model_output_dir;
            if (dir) context.model_dir = dir;
          }
        }
      }
      if (!stopRequestedRef.current) {
        onNotify("ok", `${pipeline === "timesfm" ? "TimesFM 3.0" : "Qlib"} 工作流执行完成`);
      }
      void refreshMetadata(pipeline);
    } catch (err) {
      onNotify("error", err instanceof Error ? err.message : "工作流执行中止");
    } finally {
      setRunningPipeline(false);
    }
  }, [
    runningPipeline,
    runningNodeId,
    nodeDefs,
    pipeline,
    timesfmParams,
    qlibParams,
    setNodeRuntime,
    executeNode,
    onNotify,
    refreshMetadata,
  ]);

  const handleToggleEnabled = useCallback(
    (nodeId: string) => {
      if (runningPipeline || runningNodeId !== null) return;
      const setter = pipeline === "timesfm" ? setTimesfmRuntimes : setQlibRuntimes;
      setter((prev) => {
        const cur = prev[nodeId] ?? createInitialNodeRuntime();
        const nextEnabled = !cur.enabled;
        return {
          ...prev,
          [nodeId]: {
            ...cur,
            enabled: nextEnabled,
            status: nextEnabled ? (cur.status === "skipped" ? "idle" : cur.status) : "skipped",
          },
        };
      });
    },
    [pipeline, runningPipeline, runningNodeId]
  );

  const handleReset = useCallback(() => {
    if (runningPipeline || runningNodeId !== null) return;
    if (pipeline === "timesfm") {
      setTimesfmRuntimes(buildInitialRuntimes(TIMESFM_NODE_DEFS.map((n) => n.id)));
    } else {
      setQlibRuntimes(buildInitialRuntimes(QLIB_NODE_DEFS.map((n) => n.id)));
    }
  }, [pipeline, runningPipeline, runningNodeId]);

  const handleLoadTimesFMExperiment = useCallback(
    (exp: TimesFMExperimentRecord) => {
      setTimesfmParams((prev) => ({
        ...prev,
        train: { ...prev.train, dataset_dir: exp.dataset_dir },
        backtest: { ...prev.backtest, run_id: exp.run_id, dataset_dir: exp.dataset_dir },
        export: { ...prev.export, run_id: exp.run_id },
      }));
      setSelectedByPipeline((prev) => ({ ...prev, timesfm: "backtest" }));
      onNotify("ok", `已将实验 ${exp.run_id} 载入回测与导出节点`);
    },
    [onNotify]
  );

  const handleLoadQlibExperiment = useCallback(
    (exp: QlibExperimentRecord) => {
      setQlibParams((prev) => ({
        ...prev,
        factors: { ...prev.factors, provider_uri: exp.provider_uri },
        backtest: { ...prev.backtest, provider_uri: exp.provider_uri },
        signal: {
          ...prev.signal,
          provider_uri: exp.provider_uri,
          model_dir: exp.model_output_dir ?? undefined,
        },
      }));
      setSelectedByPipeline((prev) => ({ ...prev, qlib: "signal" }));
      onNotify(
        "ok",
        exp.model_output_dir
          ? `已将实验 ${exp.run_id} 载入下游研究与信号节点`
          : `已将实验 ${exp.run_id} 载入下游节点（该实验尚未导出模型目录，已清除旧模型路径）`
      );
    },
    [onNotify]
  );

  const handleSelectNode = useCallback(
    (id: string) => {
      setSelectedByPipeline((prev) => ({ ...prev, [pipeline]: id }));
    },
    [pipeline]
  );

  const handleRunNodeCallback = useCallback(
    (id: string) => {
      void handleRunSingleNode(id);
    },
    [handleRunSingleNode]
  );

  const handleUpdateTimesFMParams = useCallback(
    <K extends keyof TimesFMNodeParamsMap>(nodeId: K, patch: Partial<TimesFMNodeParamsMap[K]>) => {
      setTimesfmParams((prev) => ({
        ...prev,
        [nodeId]: { ...prev[nodeId], ...patch },
      }));
    },
    []
  );

  const handleUpdateQlibParams = useCallback(
    <K extends keyof QlibNodeParamsMap>(nodeId: K, patch: Partial<QlibNodeParamsMap[K]>) => {
      setQlibParams((prev) => ({
        ...prev,
        [nodeId]: { ...prev[nodeId], ...patch },
      }));
    },
    []
  );

  const busy = runningPipeline || runningNodeId !== null;
  const activeMetadataError = metadataErrors[pipeline];

  return (
    <div className="space-y-4">
      {/* 顶部工作流控制台 */}
      <section className="surface flex flex-wrap items-center justify-between gap-3 p-4">
        <div className="flex flex-wrap items-center gap-3">
          <div className="segmented-control" role="tablist" aria-label="模型训练工作流切换">
            <button
              type="button"
              role="tab"
              disabled={busy}
              aria-selected={pipeline === "timesfm"}
              aria-pressed={pipeline === "timesfm"}
              onClick={() => setPipeline("timesfm")}
              className="segment disabled:opacity-50"
            >
              <Cpu className="size-3.5" />
              TimesFM 3.0 多因子
            </button>
            <button
              type="button"
              role="tab"
              disabled={busy}
              aria-selected={pipeline === "qlib"}
              aria-pressed={pipeline === "qlib"}
              onClick={() => setPipeline("qlib")}
              className="segment disabled:opacity-50"
            >
              <Layers className="size-3.5" />
              Qlib 量化研究
            </button>
          </div>

          {/* 引擎状态徽章 */}
          <div className="flex flex-wrap items-center gap-2 text-xs">
            {pipeline === "timesfm" ? (
              <>
                <span className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-slate-300">
                  TimesFM：
                  {activeMetadataError
                    ? "状态获取失败"
                    : timesfmStatus
                      ? timesfmStatus.timesfm_enabled
                        ? "已启用"
                        : "未启用"
                      : "待检查"}
                </span>
                <span className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-slate-400">
                  实验 {timesfmStatus?.experiment_count ?? timesfmExperiments.length}
                </span>
              </>
            ) : (
              <>
                <span
                  className={`rounded-md border px-2 py-1 ${
                    activeMetadataError
                      ? "border-rose-400/30 bg-rose-500/10 text-rose-200"
                      : qlibStatus?.pyqlib_installed
                        ? "border-emerald-400/30 bg-emerald-500/10 text-emerald-200"
                        : "border-amber-400/30 bg-amber-500/10 text-amber-200"
                  }`}
                >
                  pyqlib：
                  {activeMetadataError
                    ? "状态获取失败"
                    : qlibStatus
                      ? qlibStatus.pyqlib_installed
                        ? `v${qlibStatus.pyqlib_version ?? "ready"}`
                        : "未安装"
                      : "检测中"}
                </span>
                <span className="rounded-md border border-white/10 bg-white/[0.04] px-2 py-1 text-slate-400">
                  实验 {qlibStatus?.experiment_count ?? qlibExperiments.length}
                </span>
              </>
            )}
          </div>
        </div>

        {/* 右侧运行控制条 */}
        <div className="flex flex-wrap items-center gap-2">
          {runningPipeline ? (
            <button
              type="button"
              onClick={() => {
                stopRequestedRef.current = true;
              }}
              className="secondary-button !min-h-9 border-rose-400/40 bg-rose-500/15 px-3 text-xs text-rose-200"
            >
              <Square className="size-3.5 fill-current" />
              停止后续节点
            </button>
          ) : (
            <button
              type="button"
              disabled={busy}
              onClick={() => void handleRunAllNodes()}
              className="primary-button !min-h-9 px-3.5 text-xs"
            >
              <Play className="size-3.5 fill-current" />
              执行全部流程
            </button>
          )}
          <button
            type="button"
            disabled={busy}
            onClick={handleReset}
            title="重置当前画布状态"
            className="secondary-button !min-h-9 px-2.5 text-xs"
          >
            <RotateCcw className="size-3.5" />
            重置
          </button>
          <button
            type="button"
            disabled={loadingStatus}
            onClick={() => void refreshMetadata(pipeline)}
            title="刷新运行时状态与实验记录"
            className="secondary-button !min-h-9 px-2.5 text-xs"
          >
            <RefreshCw className={`size-3.5 ${loadingStatus ? "animate-spin" : ""}`} />
          </button>
        </div>
      </section>

      {activeMetadataError && (
        <div
          role="alert"
          className="flex items-center gap-2 rounded-xl border border-rose-400/30 bg-rose-500/10 px-3.5 py-2.5 text-xs text-rose-100"
        >
          <AlertTriangle className="size-4 shrink-0 text-rose-300" />
          <span>运行时状态刷新失败：{activeMetadataError}</span>
        </div>
      )}

      {pipeline === "qlib" && qlibStatus && !qlibStatus.pyqlib_installed && (
        <div className="flex items-center gap-2 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3.5 py-2.5 text-xs text-amber-100">
          <AlertTriangle className="size-4 shrink-0 text-amber-300" />
          <span>
            当前后端环境未检测到 pyqlib：「数据导出」节点仍可直接运行，因子评估 / 训练 / 回测 / 信号节点需要安装 pyqlib 后执行。
          </span>
        </div>
      )}

      {/* 主工作区：左侧 n8n 节点画布 + 右侧参数与输出检查器 */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="lg:col-span-7 xl:col-span-8">
          <WorkflowCanvas
            pipeline={pipeline}
            nodeDefs={nodeDefs}
            edgeDefs={edgeDefs}
            runtimes={runtimes}
            selectedNodeId={selectedNodeDef.id}
            busy={busy}
            onSelectNode={handleSelectNode}
            onRunNode={handleRunNodeCallback}
            onToggleEnabled={handleToggleEnabled}
          />
        </div>

        <div className="lg:col-span-5 xl:col-span-4">
          <NodeInspectorPanel
            pipeline={pipeline}
            nodeDef={selectedNodeDef}
            runtime={runtimes[selectedNodeDef.id] ?? createInitialNodeRuntime()}
            busy={busy}
            timesfmParams={timesfmParams}
            qlibParams={qlibParams}
            linkedHints={linkedHints}
            onUpdateTimesFMParams={handleUpdateTimesFMParams}
            onUpdateQlibParams={handleUpdateQlibParams}
            onRunNode={handleRunNodeCallback}
          />
        </div>
      </div>

      {/* 底部可折叠历史实验记录 */}
      <ExperimentsDrawer
        pipeline={pipeline}
        timesfmExperiments={timesfmExperiments}
        qlibExperiments={qlibExperiments}
        onLoadTimesFMExperiment={handleLoadTimesFMExperiment}
        onLoadQlibExperiment={handleLoadQlibExperiment}
      />
    </div>
  );
}

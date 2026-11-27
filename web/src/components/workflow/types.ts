import type {
  HoldingPeriod,
  QlibBacktestReport,
  QlibBacktestRequest,
  QlibExportReport,
  QlibExportRequest,
  QlibFactorEvaluateReport,
  QlibFactorEvaluateRequest,
  QlibSignalRequest,
  QlibSignalResponse,
  QlibTrainReport,
  QlibTrainRequest,
  TimesFMBacktestReport,
  TimesFMBacktestRequest,
  TimesFMExportReport,
  TimesFMExportRequest,
  TimesFMPrepareReport,
  TimesFMPrepareRequest,
  TimesFMTrainReport,
  TimesFMTrainRequest,
} from "../../api/types";

export type WorkflowPipelineId = "timesfm" | "qlib";
export type WorkflowNodeStatus = "idle" | "running" | "success" | "error" | "skipped";

export type TimesFMNodeId = "prepare" | "train" | "backtest" | "export";
export type QlibNodeId = "export" | "factors" | "train" | "backtest" | "signal";
export type WorkflowNodeId = TimesFMNodeId | QlibNodeId;

export type WorkflowNodeIconKey =
  | "database"
  | "cpu"
  | "activity"
  | "package"
  | "sparkles"
  | "radio";

export interface WorkflowNodeDefinition {
  id: string;
  pipeline: WorkflowPipelineId;
  title: string;
  subtitle: string;
  endpoint: string;
  icon: WorkflowNodeIconKey;
  position: { x: number; y: number };
  hasInput: boolean;
  hasOutput: boolean;
  upstreamIds: string[];
}

export interface WorkflowEdgeDefinition {
  id: string;
  source: string;
  target: string;
  label?: string;
}

export interface WorkflowNodeRuntime<TOutput = unknown> {
  enabled: boolean;
  status: WorkflowNodeStatus;
  output: TOutput | null;
  error: string | null;
  durationMs: number | null;
  updatedAt: string | null;
}

export interface TimesFMNodeParamsMap {
  prepare: TimesFMPrepareRequest;
  train: TimesFMTrainRequest;
  backtest: TimesFMBacktestRequest;
  export: TimesFMExportRequest;
}

export interface QlibNodeParamsMap {
  export: QlibExportRequest;
  factors: QlibFactorEvaluateRequest;
  train: QlibTrainRequest;
  backtest: QlibBacktestRequest;
  signal: QlibSignalRequest;
}

export interface SummaryChip {
  label: string;
  tone?: "default" | "gold" | "emerald" | "cyan" | "rose";
}

export const ALL_HOLDING_PERIODS: HoldingPeriod[] = ["1h", "4h", "1d", "1w"];

export const TIMESFM_NODE_DEFS: WorkflowNodeDefinition[] = [
  {
    id: "prepare",
    pipeline: "timesfm",
    title: "数据准备",
    subtitle: "PIT 多因子数据集",
    endpoint: "POST /timesfm/prepare",
    icon: "database",
    position: { x: 20, y: 120 },
    hasInput: false,
    hasOutput: true,
    upstreamIds: [],
  },
  {
    id: "train",
    pipeline: "timesfm",
    title: "模型训练",
    subtitle: "融合头 / 主干微调",
    endpoint: "POST /timesfm/train",
    icon: "cpu",
    position: { x: 320, y: 120 },
    hasInput: true,
    hasOutput: true,
    upstreamIds: ["prepare"],
  },
  {
    id: "backtest",
    pipeline: "timesfm",
    title: "样本外回测",
    subtitle: "严格 PIT 留出评估",
    endpoint: "POST /timesfm/backtest",
    icon: "activity",
    position: { x: 620, y: 120 },
    hasInput: true,
    hasOutput: true,
    upstreamIds: ["train"],
  },
  {
    id: "export",
    pipeline: "timesfm",
    title: "模型导出",
    subtitle: "不可变包 + SHA-256",
    endpoint: "POST /timesfm/export",
    icon: "package",
    position: { x: 920, y: 120 },
    hasInput: true,
    hasOutput: false,
    upstreamIds: ["train"],
  },
];

export const TIMESFM_EDGE_DEFS: WorkflowEdgeDefinition[] = [
  { id: "tfm-e1", source: "prepare", target: "train", label: "dataset_dir" },
  { id: "tfm-e2", source: "train", target: "backtest", label: "run_id" },
  { id: "tfm-e3", source: "backtest", target: "export", label: "run_id" },
];

export const QLIB_NODE_DEFS: WorkflowNodeDefinition[] = [
  {
    id: "export",
    pipeline: "qlib",
    title: "数据导出",
    subtitle: "SQLite → Qlib 二进制",
    endpoint: "POST /qlib/export",
    icon: "database",
    position: { x: 20, y: 165 },
    hasInput: false,
    hasOutput: true,
    upstreamIds: [],
  },
  {
    id: "factors",
    pipeline: "qlib",
    title: "因子评估",
    subtitle: "表达式 IC / RankIC",
    endpoint: "POST /qlib/factors/evaluate",
    icon: "sparkles",
    position: { x: 340, y: 20 },
    hasInput: true,
    hasOutput: false,
    upstreamIds: ["export"],
  },
  {
    id: "train",
    pipeline: "qlib",
    title: "模型训练",
    subtitle: "Purged Walk-Forward",
    endpoint: "POST /qlib/train",
    icon: "cpu",
    position: { x: 340, y: 165 },
    hasInput: true,
    hasOutput: true,
    upstreamIds: ["export"],
  },
  {
    id: "backtest",
    pipeline: "qlib",
    title: "择时回测",
    subtitle: "滚动样本外阈值仿真",
    endpoint: "POST /qlib/backtest",
    icon: "activity",
    position: { x: 340, y: 310 },
    hasInput: true,
    hasOutput: false,
    upstreamIds: ["export"],
  },
  {
    id: "signal",
    pipeline: "qlib",
    title: "盘前信号",
    subtitle: "择时动作与人工复核",
    endpoint: "POST /qlib/signal",
    icon: "radio",
    position: { x: 670, y: 165 },
    hasInput: true,
    hasOutput: false,
    upstreamIds: ["train"],
  },
];

export const QLIB_EDGE_DEFS: WorkflowEdgeDefinition[] = [
  { id: "qlib-e1", source: "export", target: "factors", label: "provider_uri" },
  { id: "qlib-e2", source: "export", target: "train", label: "provider_uri" },
  { id: "qlib-e3", source: "export", target: "backtest", label: "provider_uri" },
  { id: "qlib-e4", source: "train", target: "signal", label: "model_dir" },
];

export function createInitialTimesFMParams(): TimesFMNodeParamsMap {
  return {
    prepare: {
      source_mode: "kline",
      product_code: "SGE-Au99.99",
      freq: "1h",
      kline_interval: "m60",
      factor_preset: "gold_multifactor",
      context_length: 512,
      stride: 1,
      horizons: ["1d", "1w"],
      spread_pct: 0.002,
      exclude_synthetic: true,
    },
    train: {
      experiment_name: "gold_timesfm_multifactor",
      train_mode: "multifactor_head",
      head_model_type: "lgb",
      horizons: ["1d", "1w"],
      n_splits: 3,
      holdout_ratio: 0.2,
      num_boost_round: 80,
      confidence_level: 0.9,
      batch_size: 16,
      use_covariates: true,
      finetune_epochs: 1,
      finetune_lr: 0.0001,
      finetune_batch_size: 4,
      unfreeze_last_layers: 0,
    },
    backtest: {
      initial_cash: 100000,
      trade_grams_per_order: 2,
      dca_step_interval: 5,
      batch_size: 16,
    },
    export: {
      output_model_dir: "models/timesfm/v1",
    },
  };
}

export function createInitialQlibParams(): QlibNodeParamsMap {
  return {
    export: {
      source_mode: "kline",
      instruments: ["SGE-Au99.99"],
      freq: "day",
      kline_interval: "day",
      spread_pct: 0.002,
      exclude_synthetic: true,
      include_macro_features: true,
    },
    factors: {
      source_type: "local_sqlite",
      freq: "day",
      horizon: "1d",
      factor_preset: "au9999_timing",
      label_mode: "net",
      custom_expressions: {},
    },
    train: {
      experiment_name: "gold_qlib_research",
      source_type: "local_sqlite",
      freq: "day",
      model_type: "lgb",
      factor_preset: "au9999_timing",
      label_mode: "net",
      horizons: ["1d", "1w"],
      window_mode: "rolling",
      train_window_bars: 750,
      step_bars: 63,
      use_robust_zscore: true,
      n_splits: 3,
      num_boost_round: 80,
      lgb_max_depth: 4,
      lgb_num_leaves: 12,
      lgb_reg_alpha: 1,
      lgb_reg_lambda: 5,
      confidence_level: 0.9,
      spread_pct: 0.002,
      output_model_dir: "models/qlib/v1",
    },
    backtest: {
      source_type: "local_sqlite",
      instrument: "SGE-Au99.99",
      freq: "day",
      model_type: "lgb",
      factor_preset: "au9999_timing",
      label_mode: "net",
      horizon: "1d",
      window_mode: "rolling",
      train_window_bars: 750,
      step_bars: 63,
      use_robust_zscore: true,
      n_splits: 3,
      num_boost_round: 80,
      buy_thresh: 0.001,
      sell_thresh: -0.0005,
      use_lcb_gate: false,
      initial_cash: 100000,
      cash_utilization: 0.98,
      trade_unit_grams: 10,
      open_cost: 0.0003,
      close_cost: 0.0003,
      spread_pct: 0.002,
    },
    signal: {
      source_type: "local_sqlite",
      instrument: "SGE-Au99.99",
      freq: "day",
      horizon: "1d",
      use_lcb_gate: false,
      publish_alert: false,
      trade_grams: 10,
    },
  };
}

export function createInitialNodeRuntime(): WorkflowNodeRuntime {
  return {
    enabled: true,
    status: "idle",
    output: null,
    error: null,
    durationMs: null,
    updatedAt: null,
  };
}

export function extractNodeSummaryChips(
  pipeline: WorkflowPipelineId,
  nodeId: string,
  output: unknown
): SummaryChip[] {
  if (!output) return [];

  if (pipeline === "timesfm") {
    if (nodeId === "prepare") {
      const report = output as TimesFMPrepareReport;
      const totalSamples = Object.values(report.manifest.sample_counts).reduce((a, b) => a + b, 0);
      return [
        { label: `${report.manifest.total_bars} K线`, tone: "cyan" },
        { label: `${totalSamples} 样本`, tone: "gold" },
        { label: `${report.written_files} 文件` },
      ];
    }
    if (nodeId === "train") {
      const report = output as TimesFMTrainReport;
      const firstHorizon = Object.keys(report.walk_forward_folds)[0];
      const folds = firstHorizon ? report.walk_forward_folds[firstHorizon] : [];
      const avgAcc =
        folds.length > 0
          ? folds.reduce((sum, item) => sum + item.directional_accuracy, 0) / folds.length
          : null;
      return [
        { label: report.experiment.run_id.slice(0, 12), tone: "cyan" },
        ...(avgAcc !== null ? [{ label: `胜率 ${(avgAcc * 100).toFixed(1)}%`, tone: "emerald" as const }] : []),
      ];
    }
    if (nodeId === "backtest") {
      const report = output as TimesFMBacktestReport;
      const firstMetric = Object.values(report.horizon_metrics)[0];
      const chips: SummaryChip[] = [];
      if (firstMetric) {
        chips.push({ label: `MAE ${(firstMetric.mae * 100).toFixed(2)}%`, tone: "gold" });
        chips.push({ label: `方向 ${(firstMetric.directional_accuracy * 100).toFixed(1)}%`, tone: "emerald" });
      }
      if (report.backtest_summary) {
        chips.push({
          label: `收益 ${(report.backtest_summary.shadow_report.total_return_pct * 100).toFixed(2)}%`,
          tone: report.backtest_summary.shadow_report.total_return_pct >= 0 ? "emerald" : "rose",
        });
      }
      return chips;
    }
    if (nodeId === "export") {
      const report = output as TimesFMExportReport;
      return [
        { label: report.verified ? "SHA-256 已校验" : "校验未通过", tone: report.verified ? "emerald" : "rose" },
        { label: `${Object.keys(report.files).length} 文件`, tone: "cyan" },
      ];
    }
  }

  if (pipeline === "qlib") {
    if (nodeId === "export") {
      const report = output as QlibExportReport;
      return [
        { label: `${report.manifest.calendar_count} 时点`, tone: "cyan" },
        { label: `${report.manifest.instruments.length} 标的`, tone: "gold" },
        { label: `${report.written_files} 文件` },
      ];
    }
    if (nodeId === "factors") {
      const report = output as QlibFactorEvaluateReport;
      const top = [...report.metrics]
        .filter((m) => m.rank_ic !== null)
        .sort((a, b) => Math.abs(b.rank_ic ?? 0) - Math.abs(a.rank_ic ?? 0))[0];
      return [
        { label: `${report.metrics.length} 因子`, tone: "cyan" },
        ...(top && top.rank_ic !== null
          ? [{ label: `Top RankIC ${top.rank_ic.toFixed(3)}`, tone: "emerald" as const }]
          : []),
      ];
    }
    if (nodeId === "train") {
      const report = output as QlibTrainReport;
      const totalSamples = Object.values(report.sample_counts).reduce((a, b) => a + b, 0);
      return [
        { label: `${totalSamples} 样本`, tone: "gold" },
        { label: report.experiment.model_type.toUpperCase(), tone: "cyan" },
        ...(report.experiment.model_output_dir ? [{ label: "产物就绪", tone: "emerald" as const }] : []),
      ];
    }
    if (nodeId === "backtest") {
      const report = output as QlibBacktestReport;
      return [
        {
          label: `收益 ${(report.summary.total_return * 100).toFixed(2)}%`,
          tone: report.summary.total_return >= 0 ? "emerald" : "rose",
        },
        { label: `夏普 ${report.summary.sharpe_ratio.toFixed(2)}`, tone: "gold" },
        { label: `${report.summary.trade_count} 笔`, tone: "cyan" },
      ];
    }
    if (nodeId === "signal") {
      const report = output as QlibSignalResponse;
      return [
        {
          label: report.signal.action,
          tone:
            report.signal.action === "BUY"
              ? "emerald"
              : report.signal.action === "SELL"
                ? "rose"
                : "gold",
        },
        { label: `胜率 ${(report.signal.win_probability * 100).toFixed(1)}%`, tone: "cyan" },
      ];
    }
  }

  return [];
}

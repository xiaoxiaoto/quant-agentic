import type { KLineInterval, QlibExportFrequency } from "../../api/types";
import type { QlibNodeParamsMap, TimesFMNodeParamsMap, WorkflowPipelineId } from "./types";

export const QLIB_FREQ_TO_KLINE_INTERVAL: Record<QlibExportFrequency, KLineInterval> = {
  day: "day",
  "1h": "m60",
  "30min": "m30",
  "15min": "m15",
  "5min": "m5",
  "1min": "m1",
};

export function parseFiniteNumber(raw: string): number | undefined {
  const trimmed = raw.trim();
  if (!trimmed) return undefined;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : undefined;
}

export function validateWorkflowNodeParams(
  pipeline: WorkflowPipelineId,
  nodeId: string,
  timesfmParams: TimesFMNodeParamsMap,
  qlibParams: QlibNodeParamsMap
): string | null {
  if (pipeline === "timesfm") {
    if (nodeId === "prepare") {
      const p = timesfmParams.prepare;
      if (p.context_length === undefined || !Number.isFinite(p.context_length) || p.context_length < 32 || p.context_length > 15360) {
        return "上下文长度必须在 32 到 15360 之间";
      }
      if (p.stride === undefined || !Number.isFinite(p.stride) || p.stride < 1 || p.stride > 1000) {
        return "采样步长必须在 1 到 1000 之间";
      }
      if (p.spread_pct === undefined || !Number.isFinite(p.spread_pct) || p.spread_pct <= 0 || p.spread_pct >= 0.05) {
        return "估算点差比例必须在 (0, 0.05) 范围内";
      }
      if (!p.horizons || p.horizons.length === 0) {
        return "请至少选择一个目标持有期";
      }
      return null;
    }
    if (nodeId === "train") {
      const p = timesfmParams.train;
      if (!p.experiment_name?.trim()) {
        return "实验名称不能为空";
      }
      if (p.n_splits === undefined || !Number.isFinite(p.n_splits) || p.n_splits < 1 || p.n_splits > 10) {
        return "Walk-Forward 折数必须在 1 到 10 之间";
      }
      if (p.holdout_ratio === undefined || !Number.isFinite(p.holdout_ratio) || p.holdout_ratio < 0 || p.holdout_ratio > 0.45) {
        return "尾部留出比例必须在 0 到 0.45 之间";
      }
      if (p.num_boost_round === undefined || !Number.isFinite(p.num_boost_round) || p.num_boost_round < 10 || p.num_boost_round > 500) {
        return "迭代轮数必须在 10 到 500 之间";
      }
      if (p.batch_size === undefined || !Number.isFinite(p.batch_size) || p.batch_size < 1 || p.batch_size > 64) {
        return "推理批大小必须在 1 到 64 之间";
      }
      if (p.train_mode === "backbone_finetune") {
        if (p.finetune_epochs === undefined || !Number.isFinite(p.finetune_epochs) || p.finetune_epochs < 1 || p.finetune_epochs > 20) {
          return "微调轮数必须在 1 到 20 之间";
        }
        if (p.finetune_lr === undefined || !Number.isFinite(p.finetune_lr) || p.finetune_lr <= 0) {
          return "学习率必须为大于 0 的有限数值";
        }
        if (p.finetune_batch_size === undefined || !Number.isFinite(p.finetune_batch_size) || p.finetune_batch_size < 1 || p.finetune_batch_size > 32) {
          return "微调批大小必须在 1 到 32 之间";
        }
        if (
          p.unfreeze_last_layers === undefined ||
          !Number.isFinite(p.unfreeze_last_layers) ||
          p.unfreeze_last_layers < 0 ||
          p.unfreeze_last_layers > 8
        ) {
          return "解冻末尾层数必须在 0 到 8 之间";
        }
      }
      return null;
    }
    if (nodeId === "backtest") {
      const p = timesfmParams.backtest;
      if (p.initial_cash === undefined || !Number.isFinite(p.initial_cash) || p.initial_cash <= 0) {
        return "初始资金必须大于 0";
      }
      if (p.trade_grams_per_order === undefined || !Number.isFinite(p.trade_grams_per_order) || p.trade_grams_per_order <= 0) {
        return "单笔克重必须大于 0";
      }
      if (p.dca_step_interval === undefined || !Number.isFinite(p.dca_step_interval) || p.dca_step_interval < 1) {
        return "定投步长必须大于等于 1";
      }
      if (p.batch_size === undefined || !Number.isFinite(p.batch_size) || p.batch_size < 1 || p.batch_size > 64) {
        return "批大小必须在 1 到 64 之间";
      }
      return null;
    }
    if (nodeId === "export") {
      const p = timesfmParams.export;
      if (!p.output_model_dir?.trim()) {
        return "模型包导出目录不能为空";
      }
      return null;
    }
    return null;
  }

  // Qlib 节点校验
  if (nodeId === "export") {
    const p = qlibParams.export;
    if (p.spread_pct === undefined || !Number.isFinite(p.spread_pct) || p.spread_pct <= 0 || p.spread_pct >= 0.05) {
      return "估算点差必须在 (0, 0.05) 范围内";
    }
    return null;
  }
  if (nodeId === "factors") {
    const p = qlibParams.factors;
    if (p.factor_preset === "custom" && (!p.custom_expressions || Object.keys(p.custom_expressions).length === 0)) {
      return "自定义预设模式下请至少配置一条有效因子表达式";
    }
    return null;
  }
  if (nodeId === "train") {
    const p = qlibParams.train;
    if (!p.experiment_name?.trim()) {
      return "实验名称不能为空";
    }
    if (p.window_mode === "rolling") {
      if (
        p.train_window_bars === undefined ||
        !Number.isFinite(p.train_window_bars) ||
        p.train_window_bars < 80 ||
        p.train_window_bars > 5000
      ) {
        return "训练窗长度必须在 80 到 5000 之间";
      }
      if (p.step_bars === undefined || !Number.isFinite(p.step_bars) || p.step_bars < 10 || p.step_bars > 500) {
        return "推进步长必须在 10 到 500 之间";
      }
    } else if (p.n_splits === undefined || !Number.isFinite(p.n_splits) || p.n_splits < 1 || p.n_splits > 20) {
      return "Walk-Forward 折数必须在 1 到 20 之间";
    }
    if (p.num_boost_round === undefined || !Number.isFinite(p.num_boost_round) || p.num_boost_round < 10 || p.num_boost_round > 500) {
      return "迭代轮数必须在 10 到 500 之间";
    }
    if (!p.horizons || p.horizons.length === 0) {
      return "请至少选择一个预测持有期";
    }
    return null;
  }
  if (nodeId === "backtest") {
    const p = qlibParams.backtest;
    if (!p.instrument?.trim()) {
      return "回测单标的不能为空";
    }
    if (p.buy_thresh === undefined || !Number.isFinite(p.buy_thresh)) {
      return "买入阈值必须为有效数值";
    }
    if (p.sell_thresh === undefined || !Number.isFinite(p.sell_thresh)) {
      return "平仓阈值必须为有效数值";
    }
    if (p.buy_thresh < p.sell_thresh) {
      return "买入阈值不得低于平仓阈值";
    }
    if (p.initial_cash === undefined || !Number.isFinite(p.initial_cash) || p.initial_cash <= 0) {
      return "初始资金必须大于 0";
    }
    if (p.trade_unit_grams === undefined || !Number.isFinite(p.trade_unit_grams) || p.trade_unit_grams <= 0) {
      return "最小交易单位必须大于 0";
    }
    return null;
  }
  if (nodeId === "signal") {
    const p = qlibParams.signal;
    if (!p.instrument?.trim()) {
      return "目标品种不能为空";
    }
    if (p.trade_grams === undefined || !Number.isFinite(p.trade_grams) || p.trade_grams <= 0) {
      return "建议克重必须大于 0";
    }
    return null;
  }
  return null;
}

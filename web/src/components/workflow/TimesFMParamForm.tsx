import type {
  TimesFMBacktestRequest,
  TimesFMExportRequest,
  TimesFMFactorPreset,
  TimesFMFreq,
  TimesFMHeadModelType,
  TimesFMPrepareRequest,
  TimesFMSourceMode,
  TimesFMTrainMode,
  TimesFMTrainRequest,
} from "../../api/types";
import { HorizonMultiSelect, LinkedBadge, NumericInputField } from "./WorkflowFormFields";
import type { TimesFMNodeParamsMap } from "./types";

interface TimesFMParamFormProps {
  nodeId: string;
  params: TimesFMNodeParamsMap;
  linkedHints: Record<string, string | null | undefined>;
  onUpdate: <K extends keyof TimesFMNodeParamsMap>(
    nodeId: K,
    patch: Partial<TimesFMNodeParamsMap[K]>
  ) => void;
}

export function TimesFMParamForm({ nodeId, params, linkedHints, onUpdate }: TimesFMParamFormProps) {
  if (nodeId === "prepare") {
    const p: TimesFMPrepareRequest = params.prepare;
    return (
      <div className="grid grid-cols-2 gap-3 text-xs">
        <label className="block">
          <span className="text-slate-400">行情来源</span>
          <select
            value={p.source_mode ?? "kline"}
            onChange={(e) => onUpdate("prepare", { source_mode: e.target.value as TimesFMSourceMode })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="kline">多周期 K 线 (kline)</option>
            <option value="reference">参考行情 (reference)</option>
            <option value="quotes">双边报价 (quotes)</option>
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">标的编码</span>
          <input
            type="text"
            value={p.product_code ?? ""}
            placeholder="SGE-Au99.99"
            onChange={(e) => onUpdate("prepare", { product_code: e.target.value || undefined })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
        </label>
        <label className="block">
          <span className="text-slate-400">序列频率</span>
          <select
            value={p.freq ?? "1h"}
            onChange={(e) =>
              onUpdate("prepare", {
                freq: e.target.value as TimesFMFreq,
                kline_interval: e.target.value === "day" ? "day" : "m60",
              })
            }
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="1h">1h（小时频）</option>
            <option value="day">day（日频）</option>
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">协变量因子预设</span>
          <select
            value={p.factor_preset ?? "gold_multifactor"}
            onChange={(e) => onUpdate("prepare", { factor_preset: e.target.value as TimesFMFactorPreset })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="gold_multifactor">gold_multifactor</option>
            <option value="gold_ohlcv_multifactor">gold_ohlcv_multifactor</option>
          </select>
        </label>
        <NumericInputField
          label="上下文长度"
          min={32}
          max={15360}
          value={p.context_length}
          onChange={(context_length) => onUpdate("prepare", { context_length })}
        />
        <NumericInputField
          label="采样步长 (stride)"
          min={1}
          max={1000}
          value={p.stride}
          onChange={(stride) => onUpdate("prepare", { stride })}
        />
        <div className="col-span-2">
          <span className="text-slate-400">目标持有期</span>
          <HorizonMultiSelect
            selected={p.horizons ?? ["1d", "1w"]}
            onChange={(horizons) => onUpdate("prepare", { horizons })}
          />
        </div>
        <NumericInputField
          label="估算点差比例"
          step="0.0005"
          min={0.0001}
          max={0.049}
          value={p.spread_pct}
          onChange={(spread_pct) => onUpdate("prepare", { spread_pct })}
        />
        <label className="block">
          <span className="text-slate-400">输出子目录（可选）</span>
          <input
            type="text"
            value={p.output_dir ?? ""}
            placeholder="留空自动生成"
            onChange={(e) => onUpdate("prepare", { output_dir: e.target.value || undefined })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
        </label>
      </div>
    );
  }

  if (nodeId === "train") {
    const p: TimesFMTrainRequest = params.train;
    return (
      <div className="grid grid-cols-2 gap-3 text-xs">
        <label className="col-span-2 block">
          <span className="text-slate-400">实验名称</span>
          <input
            type="text"
            value={p.experiment_name ?? ""}
            onChange={(e) => onUpdate("train", { experiment_name: e.target.value })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
        </label>
        <label className="col-span-2 block">
          <span className="text-slate-400">数据集目录（留空继承上游）</span>
          <input
            type="text"
            value={p.dataset_dir ?? ""}
            placeholder="自动使用上游数据准备结果或最近数据集"
            onChange={(e) => onUpdate("train", { dataset_dir: e.target.value || undefined })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
          <LinkedBadge value={linkedHints.dataset_dir} label="上游数据集" />
        </label>
        <label className="block">
          <span className="text-slate-400">训练模式</span>
          <select
            value={p.train_mode ?? "multifactor_head"}
            onChange={(e) => onUpdate("train", { train_mode: e.target.value as TimesFMTrainMode })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="multifactor_head">多因子融合头</option>
            <option value="backbone_finetune">主干微调 + 融合头</option>
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">融合头模型</span>
          <select
            value={p.head_model_type ?? "lgb"}
            onChange={(e) => onUpdate("train", { head_model_type: e.target.value as TimesFMHeadModelType })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="lgb">LightGBM (lgb)</option>
            <option value="ridge">岭回归 (ridge)</option>
          </select>
        </label>
        <NumericInputField
          label="Walk-Forward 折数"
          min={1}
          max={10}
          value={p.n_splits}
          onChange={(n_splits) => onUpdate("train", { n_splits })}
        />
        <NumericInputField
          label="尾部留出比例"
          step="0.05"
          min={0}
          max={0.45}
          value={p.holdout_ratio}
          onChange={(holdout_ratio) => onUpdate("train", { holdout_ratio })}
        />
        <NumericInputField
          label="迭代轮数"
          min={10}
          max={500}
          value={p.num_boost_round}
          onChange={(num_boost_round) => onUpdate("train", { num_boost_round })}
        />
        <NumericInputField
          label="推理批大小"
          min={1}
          max={64}
          value={p.batch_size}
          onChange={(batch_size) => onUpdate("train", { batch_size })}
        />
        <label className="col-span-2 inline-flex items-center gap-2 text-slate-300">
          <input
            type="checkbox"
            checked={p.use_covariates ?? true}
            onChange={(e) => onUpdate("train", { use_covariates: e.target.checked })}
            className="accent-amber-400"
          />
          启用协变量条件预测 (use_covariates)
        </label>

        {p.train_mode === "backbone_finetune" && (
          <>
            <NumericInputField
              label="微调轮数 (epochs)"
              min={1}
              max={20}
              value={p.finetune_epochs}
              onChange={(finetune_epochs) => onUpdate("train", { finetune_epochs })}
            />
            <NumericInputField
              label="学习率 (lr)"
              step="0.00005"
              value={p.finetune_lr}
              onChange={(finetune_lr) => onUpdate("train", { finetune_lr })}
            />
            <NumericInputField
              label="微调批大小"
              min={1}
              max={32}
              value={p.finetune_batch_size}
              onChange={(finetune_batch_size) => onUpdate("train", { finetune_batch_size })}
            />
            <NumericInputField
              label="解冻末尾层数"
              min={0}
              max={8}
              value={p.unfreeze_last_layers}
              onChange={(unfreeze_last_layers) => onUpdate("train", { unfreeze_last_layers })}
            />
          </>
        )}
      </div>
    );
  }

  if (nodeId === "backtest") {
    const p: TimesFMBacktestRequest = params.backtest;
    return (
      <div className="grid grid-cols-2 gap-3 text-xs">
        <label className="col-span-2 block">
          <span className="text-slate-400">实验 Run ID（留空继承上游）</span>
          <input
            type="text"
            value={p.run_id ?? ""}
            placeholder="自动使用上游训练实验"
            onChange={(e) => onUpdate("backtest", { run_id: e.target.value || undefined })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
          <LinkedBadge value={linkedHints.run_id} label="上游实验" />
        </label>
        <NumericInputField
          label="初始资金 (元)"
          min={1000}
          value={p.initial_cash}
          onChange={(initial_cash) => onUpdate("backtest", { initial_cash })}
        />
        <NumericInputField
          label="单笔克重 (g)"
          step="0.5"
          min={0.1}
          value={p.trade_grams_per_order}
          onChange={(trade_grams_per_order) => onUpdate("backtest", { trade_grams_per_order })}
        />
        <NumericInputField
          label="定投步长"
          min={1}
          value={p.dca_step_interval}
          onChange={(dca_step_interval) => onUpdate("backtest", { dca_step_interval })}
        />
        <NumericInputField
          label="批大小"
          min={1}
          max={64}
          value={p.batch_size}
          onChange={(batch_size) => onUpdate("backtest", { batch_size })}
        />
      </div>
    );
  }

  const p: TimesFMExportRequest = params.export;
  return (
    <div className="grid grid-cols-1 gap-3 text-xs">
      <label className="block">
        <span className="text-slate-400">实验 Run ID（留空继承上游）</span>
        <input
          type="text"
          value={p.run_id ?? ""}
          placeholder="自动使用上游训练实验"
          onChange={(e) => onUpdate("export", { run_id: e.target.value || undefined })}
          className="field mt-1 w-full px-2.5 py-1.5"
        />
        <LinkedBadge value={linkedHints.run_id} label="上游实验" />
      </label>
      <label className="block">
        <span className="text-slate-400">不可变模型包导出目录（相对 DATA_DIR）</span>
        <input
          type="text"
          value={p.output_model_dir}
          onChange={(e) => onUpdate("export", { output_model_dir: e.target.value })}
          className="field mt-1 w-full px-2.5 py-1.5"
        />
      </label>
    </div>
  );
}

import { useEffect, useState } from "react";
import type {
  HoldingPeriod,
  QlibBacktestRequest,
  QlibExportFrequency,
  QlibExportRequest,
  QlibFactorEvaluateRequest,
  QlibFactorPreset,
  QlibLabelMode,
  QlibSignalRequest,
  QlibTrainRequest,
  QlibWindowMode,
} from "../../api/types";
import { HorizonMultiSelect, LinkedBadge, NumericInputField } from "./WorkflowFormFields";
import { ALL_HOLDING_PERIODS, type QlibNodeParamsMap } from "./types";
import { QLIB_FREQ_TO_KLINE_INTERVAL } from "./validation";

interface QlibParamFormProps {
  nodeId: string;
  params: QlibNodeParamsMap;
  linkedHints: Record<string, string | null | undefined>;
  onUpdate: <K extends keyof QlibNodeParamsMap>(
    nodeId: K,
    patch: Partial<QlibNodeParamsMap[K]>
  ) => void;
}

function parseInstruments(text: string): string[] {
  return text
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
}

function parseCustomExpressions(text: string): Record<string, string> {
  const map: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const idx = line.indexOf("=");
    if (idx > 0) {
      const k = line.slice(0, idx).trim();
      const v = line.slice(idx + 1).trim();
      if (k && v) map[k] = v;
    }
  }
  return map;
}

function formatCustomExpressions(exprs: Record<string, string> | undefined): string {
  if (!exprs) return "";
  return Object.entries(exprs)
    .map(([k, v]) => `${k}=${v}`)
    .join("\n");
}

export function QlibParamForm({ nodeId, params, linkedHints, onUpdate }: QlibParamFormProps) {
  const [instrumentsText, setInstrumentsText] = useState((params.export.instruments ?? []).join(", "));
  const [customExprText, setCustomExprText] = useState(() =>
    formatCustomExpressions(params.factors.custom_expressions)
  );

  // 当外部参数被重置或由历史实验更新时同步本地文本输入
  useEffect(() => {
    const external = params.export.instruments ?? [];
    const current = parseInstruments(instrumentsText);
    if (external.length !== current.length || external.some((item, idx) => item !== current[idx])) {
      setInstrumentsText(external.join(", "));
    }
  }, [params.export.instruments, instrumentsText]);

  useEffect(() => {
    const external = params.factors.custom_expressions ?? {};
    const current = parseCustomExpressions(customExprText);
    const extKeys = Object.keys(external);
    const curKeys = Object.keys(current);
    if (extKeys.length !== curKeys.length || extKeys.some((k) => external[k] !== current[k])) {
      setCustomExprText(formatCustomExpressions(external));
    }
  }, [params.factors.custom_expressions, customExprText]);

  if (nodeId === "export") {
    const p: QlibExportRequest = params.export;
    return (
      <div className="grid grid-cols-2 gap-3 text-xs">
        <label className="block">
          <span className="text-slate-400">行情来源</span>
          <select
            value={p.source_mode ?? "kline"}
            onChange={(e) =>
              onUpdate("export", {
                source_mode: e.target.value as "quotes" | "reference" | "kline",
              })
            }
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="kline">多周期 K 线 (kline)</option>
            <option value="reference">参考行情 (reference)</option>
            <option value="quotes">双边报价 (quotes)</option>
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">导出频率</span>
          <select
            value={p.freq ?? "day"}
            onChange={(e) => {
              const freq = e.target.value as QlibExportFrequency;
              onUpdate("export", {
                freq,
                kline_interval: QLIB_FREQ_TO_KLINE_INTERVAL[freq],
              });
            }}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="day">day（日频）</option>
            <option value="1h">1h（小时频）</option>
            <option value="30min">30min</option>
            <option value="15min">15min</option>
            <option value="5min">5min</option>
          </select>
        </label>
        <label className="col-span-2 block">
          <span className="text-slate-400">标的列表（逗号分隔，留空使用默认）</span>
          <input
            type="text"
            value={instrumentsText}
            placeholder="SGE-Au99.99"
            onChange={(e) => {
              const raw = e.target.value;
              setInstrumentsText(raw);
              const list = parseInstruments(raw);
              onUpdate("export", { instruments: list.length > 0 ? list : undefined });
            }}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
        </label>
        <NumericInputField
          label="估算点差"
          step="0.0005"
          min={0.0001}
          max={0.049}
          value={p.spread_pct}
          onChange={(spread_pct) => onUpdate("export", { spread_pct })}
        />
        <label className="block">
          <span className="text-slate-400">导出目录（可选）</span>
          <input
            type="text"
            value={p.output_dir ?? ""}
            placeholder="留空用默认 QLIB_PROVIDER_URI"
            onChange={(e) => onUpdate("export", { output_dir: e.target.value || undefined })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
        </label>
        <label className="col-span-2 inline-flex items-center gap-2 text-slate-300">
          <input
            type="checkbox"
            checked={p.include_macro_features ?? false}
            onChange={(e) => onUpdate("export", { include_macro_features: e.target.checked })}
            className="accent-amber-400"
          />
          日频拼入宏观特征（xau/usdcny/dxy/us10y）
        </label>
      </div>
    );
  }

  if (nodeId === "factors") {
    const p: QlibFactorEvaluateRequest = params.factors;
    return (
      <div className="grid grid-cols-2 gap-3 text-xs">
        <label className="col-span-2 block">
          <span className="text-slate-400">数据目录覆盖（留空继承上游）</span>
          <input
            type="text"
            value={p.provider_uri ?? ""}
            placeholder="自动使用上游导出目录"
            onChange={(e) => onUpdate("factors", { provider_uri: e.target.value || undefined })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
          <LinkedBadge value={linkedHints.provider_uri} label="上游数据目录" />
        </label>
        <label className="block">
          <span className="text-slate-400">因子预设</span>
          <select
            value={p.factor_preset ?? "au9999_timing"}
            onChange={(e) => onUpdate("factors", { factor_preset: e.target.value as QlibFactorPreset })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="au9999_timing">au9999_timing</option>
            <option value="gold_core">gold_core</option>
            <option value="alpha158_lite">alpha158_lite</option>
            <option value="custom">custom（自定义表达式）</option>
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">频率</span>
          <select
            value={p.freq ?? "day"}
            onChange={(e) => onUpdate("factors", { freq: e.target.value as "1h" | "day" })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="day">day</option>
            <option value="1h">1h</option>
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">目标持有期</span>
          <select
            value={p.horizon ?? "1d"}
            onChange={(e) => onUpdate("factors", { horizon: e.target.value as HoldingPeriod })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            {ALL_HOLDING_PERIODS.map((h) => (
              <option key={h} value={h}>
                {h}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">标签模式</span>
          <select
            value={p.label_mode ?? "net"}
            onChange={(e) => onUpdate("factors", { label_mode: e.target.value as QlibLabelMode })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="net">扣费净收益 (net)</option>
            <option value="gross">原始收益率 (gross)</option>
          </select>
        </label>
        {p.factor_preset === "custom" && (
          <label className="col-span-2 block">
            <span className="text-slate-400">自定义表达式（每行：因子名=表达式）</span>
            <textarea
              rows={3}
              value={customExprText}
              placeholder="mom_5=$close/Ref($close,5)-1"
              onChange={(e) => {
                const raw = e.target.value;
                setCustomExprText(raw);
                onUpdate("factors", { custom_expressions: parseCustomExpressions(raw) });
              }}
              className="field mt-1 w-full px-2.5 py-1.5 font-mono text-[11px]"
            />
          </label>
        )}
      </div>
    );
  }

  if (nodeId === "train") {
    const p: QlibTrainRequest = params.train;
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
          <span className="text-slate-400">数据目录覆盖（留空继承上游）</span>
          <input
            type="text"
            value={p.provider_uri ?? ""}
            placeholder="自动使用上游导出目录"
            onChange={(e) => onUpdate("train", { provider_uri: e.target.value || undefined })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
          <LinkedBadge value={linkedHints.provider_uri} label="上游数据目录" />
        </label>
        <label className="block">
          <span className="text-slate-400">模型类型</span>
          <select
            value={p.model_type ?? "lgb"}
            onChange={(e) => onUpdate("train", { model_type: e.target.value as "lgb" | "linear" })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="lgb">LightGBM (lgb)</option>
            <option value="linear">线性模型 (linear)</option>
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">因子预设</span>
          <select
            value={p.factor_preset ?? "au9999_timing"}
            onChange={(e) => onUpdate("train", { factor_preset: e.target.value as QlibFactorPreset })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="au9999_timing">au9999_timing</option>
            <option value="gold_core">gold_core</option>
            <option value="alpha158_lite">alpha158_lite</option>
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">频率</span>
          <select
            value={p.freq ?? "day"}
            onChange={(e) => onUpdate("train", { freq: e.target.value as "1h" | "day" })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="day">day</option>
            <option value="1h">1h</option>
          </select>
        </label>
        <label className="block">
          <span className="text-slate-400">滚动窗口模式</span>
          <select
            value={p.window_mode ?? "rolling"}
            onChange={(e) => onUpdate("train", { window_mode: e.target.value as QlibWindowMode })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="rolling">滚动固定窗 (rolling)</option>
            <option value="expanding">扩展窗 (expanding)</option>
          </select>
        </label>
        {p.window_mode === "rolling" ? (
          <>
            <NumericInputField
              label="训练窗长度 (bars)"
              min={80}
              max={5000}
              value={p.train_window_bars}
              onChange={(train_window_bars) => onUpdate("train", { train_window_bars })}
            />
            <NumericInputField
              label="推进步长 (bars)"
              min={10}
              max={500}
              value={p.step_bars}
              onChange={(step_bars) => onUpdate("train", { step_bars })}
            />
          </>
        ) : (
          <NumericInputField
            label="Walk-Forward 折数"
            min={1}
            max={20}
            value={p.n_splits}
            onChange={(n_splits) => onUpdate("train", { n_splits })}
          />
        )}
        <NumericInputField
          label="迭代轮数"
          min={10}
          max={500}
          value={p.num_boost_round}
          onChange={(num_boost_round) => onUpdate("train", { num_boost_round })}
        />
        <div className="col-span-2">
          <span className="text-slate-400">预测持有期</span>
          <HorizonMultiSelect
            selected={p.horizons ?? ["1d", "1w"]}
            onChange={(horizons) => onUpdate("train", { horizons })}
          />
        </div>
        <label className="col-span-2 block">
          <span className="text-slate-400">导出 QlibAgent 产物目录（供盘前信号使用）</span>
          <input
            type="text"
            value={p.output_model_dir ?? ""}
            placeholder="models/qlib/v1"
            onChange={(e) => onUpdate("train", { output_model_dir: e.target.value || undefined })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
        </label>
        <label className="col-span-2 inline-flex items-center gap-2 text-slate-300">
          <input
            type="checkbox"
            checked={p.use_robust_zscore ?? true}
            onChange={(e) => onUpdate("train", { use_robust_zscore: e.target.checked })}
            className="accent-amber-400"
          />
          折内启用 RobustZScoreNorm 标准化
        </label>
      </div>
    );
  }

  if (nodeId === "backtest") {
    const p: QlibBacktestRequest = params.backtest;
    return (
      <div className="grid grid-cols-2 gap-3 text-xs">
        <label className="col-span-2 block">
          <span className="text-slate-400">数据目录覆盖（留空继承上游）</span>
          <input
            type="text"
            value={p.provider_uri ?? ""}
            placeholder="自动使用上游导出目录"
            onChange={(e) => onUpdate("backtest", { provider_uri: e.target.value || undefined })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
          <LinkedBadge value={linkedHints.provider_uri} label="上游数据目录" />
        </label>
        <label className="block">
          <span className="text-slate-400">单标的</span>
          <input
            type="text"
            value={p.instrument ?? "SGE-Au99.99"}
            onChange={(e) => onUpdate("backtest", { instrument: e.target.value })}
            className="field mt-1 w-full px-2.5 py-1.5"
          />
        </label>
        <label className="block">
          <span className="text-slate-400">因子预设</span>
          <select
            value={p.factor_preset ?? "au9999_timing"}
            onChange={(e) => onUpdate("backtest", { factor_preset: e.target.value as QlibFactorPreset })}
            className="field mt-1 w-full px-2.5 py-1.5"
          >
            <option value="au9999_timing">au9999_timing</option>
            <option value="gold_core">gold_core</option>
            <option value="alpha158_lite">alpha158_lite</option>
          </select>
        </label>
        <NumericInputField
          label="买入阈值"
          step="0.0005"
          value={p.buy_thresh}
          onChange={(buy_thresh) => onUpdate("backtest", { buy_thresh })}
        />
        <NumericInputField
          label="平仓阈值"
          step="0.0005"
          value={p.sell_thresh}
          onChange={(sell_thresh) => onUpdate("backtest", { sell_thresh })}
        />
        <NumericInputField
          label="初始资金 (元)"
          min={1000}
          value={p.initial_cash}
          onChange={(initial_cash) => onUpdate("backtest", { initial_cash })}
        />
        <NumericInputField
          label="最小交易单位 (g)"
          min={1}
          value={p.trade_unit_grams}
          onChange={(trade_unit_grams) => onUpdate("backtest", { trade_unit_grams })}
        />
        <label className="col-span-2 inline-flex items-center gap-2 text-slate-300">
          <input
            type="checkbox"
            checked={p.use_lcb_gate ?? false}
            onChange={(e) => onUpdate("backtest", { use_lcb_gate: e.target.checked })}
            className="accent-amber-400"
          />
          买入要求置信下界 net_return_lcb &gt; 0
        </label>
      </div>
    );
  }

  const p: QlibSignalRequest = params.signal;
  return (
    <div className="grid grid-cols-2 gap-3 text-xs">
      <label className="col-span-2 block">
        <span className="text-slate-400">模型目录（留空继承上游训练产物）</span>
        <input
          type="text"
          value={p.model_dir ?? ""}
          placeholder="自动使用上游 Qlib 训练产物目录"
          onChange={(e) => onUpdate("signal", { model_dir: e.target.value || undefined })}
          className="field mt-1 w-full px-2.5 py-1.5"
        />
        <LinkedBadge value={linkedHints.model_dir} label="上游模型目录" />
      </label>
      <label className="block">
        <span className="text-slate-400">目标品种</span>
        <input
          type="text"
          value={p.instrument ?? "SGE-Au99.99"}
          onChange={(e) => onUpdate("signal", { instrument: e.target.value })}
          className="field mt-1 w-full px-2.5 py-1.5"
        />
      </label>
      <label className="block">
        <span className="text-slate-400">预测持有期</span>
        <select
          value={p.horizon ?? "1d"}
          onChange={(e) => onUpdate("signal", { horizon: e.target.value as HoldingPeriod })}
          className="field mt-1 w-full px-2.5 py-1.5"
        >
          {ALL_HOLDING_PERIODS.map((h) => (
            <option key={h} value={h}>
              {h}
            </option>
          ))}
        </select>
      </label>
      <NumericInputField
        label="建议克重 (g)"
        min={1}
        value={p.trade_grams}
        onChange={(trade_grams) => onUpdate("signal", { trade_grams })}
      />
      <label className="block">
        <span className="text-slate-400">频率</span>
        <select
          value={p.freq ?? "day"}
          onChange={(e) => onUpdate("signal", { freq: e.target.value as "1h" | "day" })}
          className="field mt-1 w-full px-2.5 py-1.5"
        >
          <option value="day">day</option>
          <option value="1h">1h</option>
        </select>
      </label>
      <label className="col-span-2 inline-flex items-center gap-2 text-slate-300">
        <input
          type="checkbox"
          checked={p.publish_alert ?? false}
          onChange={(e) => onUpdate("signal", { publish_alert: e.target.checked })}
          className="accent-amber-400"
        />
        推送 BUY/SELL 信号至人工复核提醒队列
      </label>
    </div>
  );
}

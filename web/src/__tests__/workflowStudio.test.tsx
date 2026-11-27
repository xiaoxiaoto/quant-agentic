import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type {
  QlibBacktestReport,
  QlibExperimentRecord,
  QlibExportReport,
  QlibFactorEvaluateReport,
  QlibSignalResponse,
  QlibStatusResponse,
  QlibTrainReport,
  TimesFMBacktestReport,
  TimesFMExportReport,
  TimesFMPrepareReport,
  TimesFMStatusResponse,
  TimesFMTrainReport,
} from "../api/types";
import { ModelWorkflowStudio } from "../components/workflow/ModelWorkflowStudio";

// jsdom 不计算布局尺寸，轻量代理 ReactFlow 容器并直接渲染真实的自定义 WorkflowNodeCard
vi.mock("@xyflow/react", () => ({
  Background: () => null,
  BackgroundVariant: { Dots: "dots" },
  Controls: () => null,
  Handle: () => null,
  MarkerType: { ArrowClosed: "arrowclosed" },
  Position: { Left: "left", Right: "right" },
  ReactFlow: ({
    nodes,
    nodeTypes,
    onNodeClick,
    children,
  }: {
    nodes: Array<{ id: string; type?: string; data: unknown; selected?: boolean }>;
    nodeTypes?: Record<string, React.ComponentType<Record<string, unknown>>>;
    onNodeClick?: (event: unknown, node: { id: string }) => void;
    children?: ReactNode;
  }) => (
    <div data-testid="mock-react-flow">
      {nodes.map((node) => {
        const Comp = node.type && nodeTypes ? nodeTypes[node.type] : null;
        return (
          <div key={node.id} onClick={(e) => onNodeClick?.(e, node)}>
            {Comp ? <Comp id={node.id} data={node.data} selected={node.selected} /> : node.id}
          </div>
        );
      })}
      {children}
    </div>
  ),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const MOCK_TIMESFM_STATUS: TimesFMStatusResponse = {
  timesfm_enabled: true,
  predictor_backend: "timesfm_multifactor",
  active_model_dir: null,
  dataset_dir: "/tmp/data/timesfm/datasets",
  experiment_dir: "/tmp/data/timesfm/experiments",
  experiment_count: 1,
  latest_dataset: null,
};

const MOCK_QLIB_STATUS: QlibStatusResponse = {
  pyqlib_installed: true,
  pyqlib_version: "0.9.6",
  qlib_enabled: true,
  predictor_backend: "qlib",
  provider_uri: "/tmp/data/qlib_local",
  external_provider_uri: null,
  active_model_dir: null,
  local_dataset: null,
  external_dataset: null,
  experiment_count: 0,
};

const MOCK_PREPARE_REPORT: TimesFMPrepareReport = {
  manifest: {
    dataset_id: "ds_001",
    dataset_dir: "/tmp/data/timesfm/datasets/ds_001",
    source_mode: "kline",
    product_code: "SGE-Au99.99",
    freq: "1h",
    factor_preset: "gold_multifactor",
    context_length: 512,
    stride: 1,
    horizons: ["1d", "1w"],
    horizon_steps: { "1d": 24, "1w": 120 },
    total_bars: 1500,
    sample_counts: { "1d": 900, "1w": 820 },
    past_only_factors: ["ret_1"],
    past_future_factors: ["dow"],
    tabular_factors: ["vol_24"],
    start_time: "2026-01-01T00:00:00Z",
    end_time: "2026-10-01T00:00:00Z",
    spread_pct: 0.002,
    buy_fee_rate: 0.0003,
    sell_fee_rate: 0.0003,
    created_at: "2026-11-27T08:00:00Z",
  },
  written_files: 4,
};

const MOCK_TRAIN_REPORT: TimesFMTrainReport = {
  experiment: {
    experiment_name: "gold_timesfm_multifactor",
    run_id: "run_tfm_001",
    dataset_id: "ds_001",
    dataset_dir: "/tmp/data/timesfm/datasets/ds_001",
    product_code: "SGE-Au99.99",
    freq: "1h",
    train_mode: "multifactor_head",
    head_model_type: "lgb",
    factor_preset: "gold_multifactor",
    use_covariates: true,
    context_length: 512,
    horizons: ["1d", "1w"],
    holdout_ratio: 0.2,
    holdout_start_at: null,
    holdout_end_at: null,
    oos_summary: {},
    finetune_summary: {
      enabled: false,
      epochs: 0,
      learning_rate: 0,
      unfreeze_last_layers: 0,
      trainable_params: 0,
      total_params: 0,
      sample_count: 0,
      initial_loss: null,
      final_loss: null,
      checkpoint_saved: false,
    },
    staging_dir: "/tmp/data/timesfm/staging/run_tfm_001",
    exported_model_dir: null,
    created_at: "2026-11-27T08:05:00Z",
  },
  sample_counts: { "1d": 720, "1w": 656 },
  walk_forward_folds: {
    "1d": [
      {
        fold_index: 1,
        train_count: 400,
        test_count: 100,
        test_start_at: "2026-06-01T00:00:00Z",
        test_end_at: "2026-07-01T00:00:00Z",
        mae: 0.0042,
        directional_accuracy: 0.58,
        brier_score: 0.23,
        mean_predicted_return: 0.002,
        mean_actual_return: 0.0025,
        lcb_coverage: 0.91,
      },
    ],
  },
  trained_model_info: null,
};

const MOCK_BACKTEST_REPORT: TimesFMBacktestReport = {
  run_id: "run_tfm_001",
  model_info: {},
  holdout_start_at: "2026-08-01T00:00:00Z",
  holdout_end_at: "2026-10-01T00:00:00Z",
  horizon_metrics: {
    "1d": {
      horizon: "1d",
      sample_count: 180,
      mae: 0.0038,
      directional_accuracy: 0.61,
      brier_score: 0.21,
      lcb_coverage: 0.9,
      ic: 0.08,
      rank_ic: 0.09,
      mean_predicted_return: 0.0018,
      mean_actual_return: 0.002,
    },
  },
  mapped_horizons: {},
  backtest_summary: null,
};

const MOCK_EXPORT_REPORT: TimesFMExportReport = {
  run_id: "run_tfm_001",
  output_model_dir: "/tmp/data/models/timesfm/v1",
  checksum_path: "/tmp/data/models/timesfm/v1/SHA256SUMS",
  files: { "model.bin": "abc123", "meta.json": "def456" },
  model_info: {},
  verified: true,
};

describe("ModelWorkflowStudio", () => {
  it("默认加载 TimesFM 4 个节点，切换至 Qlib 展示 5 个分支节点与环境状态", async () => {
    vi.spyOn(api, "getTimesfmStatus").mockResolvedValue(MOCK_TIMESFM_STATUS);
    vi.spyOn(api, "listTimesfmExperiments").mockResolvedValue({ total: 0, experiments: [] });
    vi.spyOn(api, "getQlibStatus").mockResolvedValue(MOCK_QLIB_STATUS);
    vi.spyOn(api, "listQlibExperiments").mockResolvedValue({ total: 0, experiments: [] });

    render(<ModelWorkflowStudio onNotify={vi.fn()} />);

    await waitFor(() => {
      expect(screen.getByText("TimesFM：已启用")).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "运行数据准备" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "运行模型训练" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "运行样本外回测" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "运行模型导出" })).toBeTruthy();

    fireEvent.click(screen.getByRole("tab", { name: /Qlib 量化研究/ }));

    await waitFor(() => {
      expect(screen.getByText("pyqlib：v0.9.6")).toBeTruthy();
    });
    expect(screen.getByRole("button", { name: "运行数据导出" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "运行因子评估" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "运行择时回测" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "运行盘前信号" })).toBeTruthy();
  });

  it("顺序执行 TimesFM 全部流程并自动传递上游 dataset_dir 与 run_id，且跳过已禁用节点", async () => {
    vi.spyOn(api, "getTimesfmStatus").mockResolvedValue(MOCK_TIMESFM_STATUS);
    vi.spyOn(api, "listTimesfmExperiments").mockResolvedValue({ total: 0, experiments: [] });
    const prepareSpy = vi.spyOn(api, "prepareTimesfm").mockResolvedValue(MOCK_PREPARE_REPORT);
    const trainSpy = vi.spyOn(api, "trainTimesfm").mockResolvedValue(MOCK_TRAIN_REPORT);
    const backtestSpy = vi.spyOn(api, "backtestTimesfm").mockResolvedValue(MOCK_BACKTEST_REPORT);
    const exportSpy = vi.spyOn(api, "exportTimesfm").mockResolvedValue(MOCK_EXPORT_REPORT);
    const onNotify = vi.fn();

    render(<ModelWorkflowStudio onNotify={onNotify} />);
    await waitFor(() => expect(screen.getByText("TimesFM：已启用")).toBeTruthy());

    // 禁用「模型导出」节点，验证全流程自动跳过
    fireEvent.click(screen.getByRole("button", { name: "禁用模型导出" }));
    fireEvent.click(screen.getByRole("button", { name: /执行全部流程/ }));

    await waitFor(() => {
      expect(backtestSpy).toHaveBeenCalledTimes(1);
    });

    expect(prepareSpy).toHaveBeenCalledTimes(1);
    expect(trainSpy).toHaveBeenCalledWith(
      expect.objectContaining({ dataset_dir: "/tmp/data/timesfm/datasets/ds_001" })
    );
    expect(backtestSpy).toHaveBeenCalledWith(
      expect.objectContaining({ run_id: "run_tfm_001" })
    );
    expect(exportSpy).not.toHaveBeenCalled();
    expect(onNotify).toHaveBeenCalledWith("ok", "TimesFM 3.0 工作流执行完成");
  });

  it("上游节点报错时立即中止后续节点执行并展示错误提示", async () => {
    vi.spyOn(api, "getTimesfmStatus").mockResolvedValue(MOCK_TIMESFM_STATUS);
    vi.spyOn(api, "listTimesfmExperiments").mockResolvedValue({ total: 0, experiments: [] });
    vi.spyOn(api, "prepareTimesfm").mockRejectedValue(new Error("当前已有 TimesFM 任务正在运行"));
    const trainSpy = vi.spyOn(api, "trainTimesfm");
    const onNotify = vi.fn();

    render(<ModelWorkflowStudio onNotify={onNotify} />);
    await waitFor(() => expect(screen.getByText("TimesFM：已启用")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /执行全部流程/ }));

    await waitFor(() => {
      expect(onNotify).toHaveBeenCalledWith("error", "当前已有 TimesFM 任务正在运行");
    });
    expect(trainSpy).not.toHaveBeenCalled();
  });

  it("历史实验记录点击载入下游后正确回填 run_id", async () => {
    vi.spyOn(api, "getTimesfmStatus").mockResolvedValue(MOCK_TIMESFM_STATUS);
    vi.spyOn(api, "listTimesfmExperiments").mockResolvedValue({
      total: 1,
      experiments: [MOCK_TRAIN_REPORT.experiment],
    });
    const backtestSpy = vi.spyOn(api, "backtestTimesfm").mockResolvedValue(MOCK_BACKTEST_REPORT);
    const onNotify = vi.fn();

    render(<ModelWorkflowStudio onNotify={onNotify} />);
    await waitFor(() => expect(screen.getByText("TimesFM：已启用")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /历史实验记录/ }));
    fireEvent.click(screen.getByRole("button", { name: /载入下游/ }));

    expect(onNotify).toHaveBeenCalledWith("ok", "已将实验 run_tfm_001 载入回测与导出节点");

    fireEvent.click(screen.getByRole("button", { name: "运行样本外回测" }));
    await waitFor(() => {
      expect(backtestSpy).toHaveBeenCalledWith(
        expect.objectContaining({ run_id: "run_tfm_001" })
      );
    });
  });

  it("Qlib 导出频率正确映射 kline_interval，且全流程自动传递 provider_uri 与 model_dir", async () => {
    vi.spyOn(api, "getTimesfmStatus").mockResolvedValue(MOCK_TIMESFM_STATUS);
    vi.spyOn(api, "listTimesfmExperiments").mockResolvedValue({ total: 0, experiments: [] });
    vi.spyOn(api, "getQlibStatus").mockResolvedValue(MOCK_QLIB_STATUS);
    vi.spyOn(api, "listQlibExperiments").mockResolvedValue({ total: 0, experiments: [] });

    const mockExport: QlibExportReport = {
      manifest: {
        source_type: "local_sqlite",
        provider_uri: "/tmp/data/qlib_run_1",
        source_mode: "kline",
        freq: "30min",
        instruments: ["SGE-Au99.99"],
        fields: ["close"],
        calendar_count: 200,
        start_time: "2026-01-01T00:00:00Z",
        end_time: "2026-10-01T00:00:00Z",
        spread_pct: 0.002,
        buy_fee_rate: 0.0,
        sell_fee_rate: 0.0003,
        exported_at: "2026-11-27T08:00:00Z",
      },
      written_files: 6,
      skipped_instruments: [],
    };
    const mockFactors: QlibFactorEvaluateReport = {
      source_type: "local_sqlite",
      provider_uri: "/tmp/data/qlib_run_1",
      instruments: ["SGE-Au99.99"],
      freq: "day",
      horizon: "1d",
      factor_preset: "au9999_timing",
      metrics: [],
    };
    const mockTrain: QlibTrainReport = {
      experiment: {
        experiment_name: "gold_qlib_research",
        run_id: "qlib_run_01",
        source_type: "local_sqlite",
        provider_uri: "/tmp/data/qlib_run_1",
        instruments: ["SGE-Au99.99"],
        freq: "day",
        model_type: "lgb",
        factor_preset: "au9999_timing",
        horizons: ["1d"],
        oos_summary: {},
        serving_compatible: true,
        model_output_dir: "/tmp/data/models/qlib/v1",
        qlib_recorder_id: null,
        created_at: "2026-11-27T08:05:00Z",
      },
      sample_counts: { "1d": 120 },
      walk_forward_folds: {},
      trained_model_info: null,
    };
    const mockBacktest: QlibBacktestReport = {
      instrument: "SGE-Au99.99",
      provider_uri: "/tmp/data/qlib_run_1",
      freq: "day",
      horizon: "1d",
      factor_preset: "au9999_timing",
      window_mode: "rolling",
      summary: {
        initial_cash: 100000,
        total_return: 0.12,
        benchmark_return: 0.08,
        excess_return: 0.04,
        annual_return: 0.12,
        sharpe_ratio: 1.5,
        calmar_ratio: 4.0,
        max_drawdown: 0.03,
        win_rate: 0.6,
        trade_count: 4,
        round_trip_count: 2,
        total_fees: 20,
        final_equity: 112000,
        bars_evaluated: 100,
      },
      walk_forward_folds: [],
      trades: [],
      equity_curve: [],
    };
    const mockSignal: QlibSignalResponse = {
      signal: {
        signal_id: "qsig_01",
        instrument: "SGE-Au99.99",
        as_of: "2026-11-27T00:00:00Z",
        horizon: "1d",
        predicted_return: 0.005,
        net_return_lcb: 0.002,
        win_probability: 0.65,
        action: "BUY",
        buy_thresh: 0.001,
        sell_thresh: -0.0005,
        latest_close: 610.0,
        model_dir: "/tmp/data/models/qlib/v1",
        alert_id: null,
        feature_snapshot: {},
        created_at: "2026-11-27T08:10:00Z",
      },
    };

    const exportSpy = vi.spyOn(api, "exportQlib").mockResolvedValue(mockExport);
    const factorsSpy = vi.spyOn(api, "evaluateQlibFactors").mockResolvedValue(mockFactors);
    const trainSpy = vi.spyOn(api, "trainQlib").mockResolvedValue(mockTrain);
    const backtestSpy = vi.spyOn(api, "backtestQlib").mockResolvedValue(mockBacktest);
    const signalSpy = vi.spyOn(api, "generateQlibSignal").mockResolvedValue(mockSignal);
    const onNotify = vi.fn();

    render(<ModelWorkflowStudio onNotify={onNotify} />);
    await waitFor(() => expect(screen.getByText("TimesFM：已启用")).toBeTruthy());

    fireEvent.click(screen.getByRole("tab", { name: /Qlib 量化研究/ }));
    await waitFor(() => expect(screen.getByText("pyqlib：v0.9.6")).toBeTruthy());

    // 切换导出频率为 30min，验证 kline_interval 映射为 m30
    fireEvent.change(screen.getByLabelText("导出频率"), { target: { value: "30min" } });
    fireEvent.click(screen.getByRole("button", { name: /执行全部流程/ }));

    await waitFor(() => expect(signalSpy).toHaveBeenCalledTimes(1));
    expect(exportSpy).toHaveBeenCalledWith(expect.objectContaining({ freq: "30min", kline_interval: "m30" }));
    expect(factorsSpy).toHaveBeenCalledWith(expect.objectContaining({ provider_uri: "/tmp/data/qlib_run_1" }));
    expect(trainSpy).toHaveBeenCalledWith(expect.objectContaining({ provider_uri: "/tmp/data/qlib_run_1" }));
    expect(backtestSpy).toHaveBeenCalledWith(expect.objectContaining({ provider_uri: "/tmp/data/qlib_run_1" }));
    expect(signalSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_uri: "/tmp/data/qlib_run_1",
        model_dir: "/tmp/data/models/qlib/v1",
      })
    );
  });

  it("载入未导出模型的 Qlib 历史实验时清除旧 model_dir，且元数据失败时展示错误提示", async () => {
    vi.spyOn(api, "getTimesfmStatus").mockRejectedValue(new Error("后端不可达"));
    vi.spyOn(api, "listTimesfmExperiments").mockRejectedValue(new Error("后端不可达"));

    const expWithModel: QlibExperimentRecord = {
      experiment_name: "exp_a",
      run_id: "qlib_a",
      source_type: "local_sqlite",
      provider_uri: "/tmp/qlib_a",
      instruments: ["SGE-Au99.99"],
      freq: "day",
      model_type: "lgb",
      factor_preset: "au9999_timing",
      horizons: ["1d"],
      oos_summary: {},
      serving_compatible: true,
      model_output_dir: "/tmp/models/a",
      qlib_recorder_id: null,
      created_at: "2026-11-27T08:00:00Z",
    };
    const expWithoutModel: QlibExperimentRecord = {
      ...expWithModel,
      experiment_name: "exp_b",
      run_id: "qlib_b",
      provider_uri: "/tmp/qlib_b",
      model_output_dir: null,
    };
    vi.spyOn(api, "getQlibStatus").mockResolvedValue(MOCK_QLIB_STATUS);
    vi.spyOn(api, "listQlibExperiments").mockResolvedValue({
      total: 2,
      experiments: [expWithModel, expWithoutModel],
    });
    const signalSpy = vi.spyOn(api, "generateQlibSignal").mockResolvedValue({
      signal: {
        signal_id: "qsig_02",
        instrument: "SGE-Au99.99",
        as_of: "2026-11-27T00:00:00Z",
        horizon: "1d",
        predicted_return: 0.0,
        net_return_lcb: 0.0,
        win_probability: 0.5,
        action: "HOLD",
        buy_thresh: 0.001,
        sell_thresh: -0.0005,
        latest_close: 600.0,
        model_dir: "",
        alert_id: null,
        feature_snapshot: {},
        created_at: "2026-11-27T08:10:00Z",
      },
    });
    const onNotify = vi.fn();

    render(<ModelWorkflowStudio onNotify={onNotify} />);
    await waitFor(() => {
      expect(screen.getByText("TimesFM：状态获取失败")).toBeTruthy();
      expect(screen.getByText(/运行时状态刷新失败：后端不可达/)).toBeTruthy();
    });

    fireEvent.click(screen.getByRole("tab", { name: /Qlib 量化研究/ }));
    await waitFor(() => expect(screen.getByText("pyqlib：v0.9.6")).toBeTruthy());

    fireEvent.click(screen.getByRole("button", { name: /历史实验记录/ }));
    const loadButtons = screen.getAllByRole("button", { name: /载入下游/ });
    // 先载入有模型的实验 A，再载入无模型的实验 B
    fireEvent.click(loadButtons[0]);
    fireEvent.click(loadButtons[1]);

    fireEvent.click(screen.getByRole("button", { name: "运行盘前信号" }));
    await waitFor(() => expect(signalSpy).toHaveBeenCalledTimes(1));
    expect(signalSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_uri: "/tmp/qlib_b",
        model_dir: undefined,
      })
    );
  });

  it("表单校验非法参数时禁用执行按钮并提示错误", async () => {
    vi.spyOn(api, "getTimesfmStatus").mockResolvedValue(MOCK_TIMESFM_STATUS);
    vi.spyOn(api, "listTimesfmExperiments").mockResolvedValue({ total: 0, experiments: [] });
    vi.spyOn(api, "getQlibStatus").mockResolvedValue(MOCK_QLIB_STATUS);
    vi.spyOn(api, "listQlibExperiments").mockResolvedValue({ total: 0, experiments: [] });

    render(<ModelWorkflowStudio onNotify={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("TimesFM：已启用")).toBeTruthy());

    fireEvent.click(screen.getByRole("tab", { name: /Qlib 量化研究/ }));
    await waitFor(() => expect(screen.getByText("pyqlib：v0.9.6")).toBeTruthy());

    // 选中择时回测节点，设置买入阈值 < 平仓阈值
    fireEvent.click(screen.getByText("择时回测"));
    fireEvent.change(screen.getByLabelText("买入阈值"), { target: { value: "-0.01" } });

    expect(screen.getByText("买入阈值不得低于平仓阈值")).toBeTruthy();
    const runBtn = screen.getByRole("button", { name: /执行此节点/ }) as HTMLButtonElement;
    expect(runBtn.disabled).toBe(true);
  });
});

export interface AccountSnapshot {
  cash_available: number;
  cash_frozen: number;
  gold_total_g: number;
  gold_available_g: number;
  gold_frozen_g: number;
  cost_basis_per_g: number | null;
  realized_pnl: number;
  unrealized_pnl: number;
  interest_pending_g: number;
  interest_credited_g: number;
  as_of: string | null;
}

export interface ReconciliationDiscrepancy {
  field_name: string;
  local_value: number | string;
  bank_value: number | string;
  difference: number;
  tolerance: number;
  is_breached: boolean;
}

export interface ReconciliationReport {
  report_id: string;
  as_of: string;
  matched: boolean;
  discrepancies: ReconciliationDiscrepancy[];
  unmatched_local_orders: string[];
  unmatched_bank_orders: string[];
  summary: string;
}

export interface LiveStatusResponse {
  risk_state: "NORMAL" | "NO_NEW_RISK" | "REDUCE_ONLY" | "HALT_RECONCILE" | string;
  kill_switch_active: boolean;
  live_reference_assist_enabled?: boolean;
  basis_reference_product?: string;
  default_initial_cash?: number;
  sell_fee_rate?: number;
  is_trading_session: boolean;
  total_alerts: number;
  pending_alerts: number;
  jd_logged_in: boolean;
  jd_token_expired?: boolean;
  jd_usable_seconds?: number | null;
  jd_token_expires_at?: number | null;
  last_reconciliation: ReconciliationReport | null;
  snapshot: AccountSnapshot;
}

export interface FifoLotItem {
  client_order_id: string;
  bank_order_id?: string | null;
  direction: "BUY" | "SELL";
  lot_status: "HOLDING" | "PARTIAL" | "CLOSED" | "SELL" | "UNFILLED" | string;
  requested_grams: number;
  filled_grams?: number | null;
  remaining_grams: number;
  execution_price?: number | null;
  fee?: number | null;
  lot_pnl?: number | null;
  timestamp?: string | null;
}

export interface FifoSummaryResponse {
  open_position_cost: number;
  open_grams: number;
  open_lot_count: number;
  total_orders: number;
  items: FifoLotItem[];
}

export interface ReferenceProduct {
  code: string;
  name: string;
  unit: "CNY/g" | "CNY/kg" | "USD/oz";
  category: "bank" | "index" | "international" | "sge";
  price_side: "reference" | "buy";
}

export interface ReferenceQuote {
  quote_id: string;
  source_id: string;
  product_code: string;
  unit: "CNY/g" | "CNY/kg" | "USD/oz";
  price_side: "reference" | "buy";
  price: number;
  quote_at: string | null;
  received_at: string;
  available_at: string | null;
  is_tradable: boolean;
  quality_state: "NORMAL" | "STALE" | "GAP" | "ANOMALY" | string;
}

export interface LiveQuoteStatus {
  product_code: string;
  enabled: boolean;
  running: boolean;
  interval_seconds: number;
  records_saved: number;
  failures: number;
  last_attempt_at: string | null;
  last_success_at: string | null;
  last_error: string | null;
  latest: ReferenceQuote | null;
  cny_per_g?: number | null;
  fx_rate_usd_cny?: number | null;
}

export interface AlertPayload {
  alert_id: string;
  decision_id: string;
  created_at: string;
  expires_at: string;
  action: "BUY" | "SELL" | "HOLD" | "ABSTAIN" | string;
  suggested_grams: number;
  current_bid_price: number;
  current_ask_price: number;
  spread: number;
  break_even_price: number;
  net_exit_loss_cny: number;
  horizon_primary: string;
  reason_codes: string[];
  risk_flags: string[];
  state: string;
  disclaimer: string;
  is_reference_estimated?: boolean;
  user_confirmed_at: string | null;
  user_executed_at: string | null;
}

export interface LiveStepResult {
  as_of: string;
  product_code: string;
  quote_id: string | null;
  risk_state: string;
  kill_switch_active: boolean;
  risk_violations: string[];
  alert: AlertPayload | null;
  reference_quote: ReferenceQuote | null;
  is_reference_estimated?: boolean;
  snapshot: AccountSnapshot;
  message: string;
}

export type Evaluation = { at: string; result: LiveStepResult } | { at: string; error: string };

export interface PaperStepResult {
  as_of: string;
  product_code: string;
  quote_id: string | null;
  risk_violations: string[];
  snapshot: AccountSnapshot;
  message: string;
}

export interface ModelStatusResponse {
  predictor: {
    model_version?: string;
    is_calibrated?: boolean;
    [key: string]: unknown;
  };
  timesfm: {
    enabled?: boolean;
    loaded?: boolean;
    [key: string]: unknown;
  };
  news_llm: {
    enabled: boolean;
    model: string;
    framework: string;
  };
  news_sync?: {
    enabled: boolean;
    running: boolean;
    interval_seconds: number;
    page_size: number;
    news_agent_ready: boolean;
    last_report: NewsSyncReport | null;
    errors?: Record<string, string>;
  };
  qlib?: QlibStatusResponse;
  timesfm_pipeline?: TimesFMStatusResponse;
}

export interface NewsSyncReport {
  fetched_count: number;
  ingested_count: number;
  skipped_count: number;
  failed_count: number;
  triggered_risk_flags: string[];
  synced_at: string;
}

export interface NewsEvent {
  event_id: string;
  source: string;
  title: string;
  content: string;
  published_at: string;
  available_at: string | null;
  event_type: string;
  severity: number;
  confidence: number;
  summary: string | null;
  risk_flags: string[];
}

export interface MacroCalendarEvent {
  event_id: string;
  event_name: string;
  scheduled_at: string;
  lock_window_hours_before: number;
  lock_window_hours_after: number;
}

export interface NewsOverviewResponse {
  event_locked: boolean;
  lock_reason: string | null;
  active_risk_flags: string[];
  news_events: NewsEvent[];
  jd_articles: {
    article_id: string;
    channel: "flash" | "news" | "morning";
    source: string;
    title: string;
    content: string;
    published_at: string;
  }[];
  macro_events: MacroCalendarEvent[];
}

export interface SnapshotReconcileResponse {
  report: ReconciliationReport;
  recommended_risk_state: string;
}

export interface BaselineMetric {
  strategy_name: string;
  final_equity: number;
  total_return_pct: number;
  annualized_return_pct: number;
  max_drawdown_pct: number;
  sharpe_ratio: number | null;
  total_trades: number;
  total_fee_cny: number;
}

export interface BacktestSummary {
  product_code: string;
  quote_count: number;
  shadow_report: {
    initial_cash: number;
    final_equity: number;
    total_return_pct: number;
    annualized_return_pct: number;
    max_drawdown_pct: number;
    sharpe_ratio: number | null;
    total_trades: number;
    win_rate: number;
    total_fees_cny: number;
    summary: string;
  };
  baseline_report: {
    metrics: BaselineMetric[];
    best_strategy: string;
    summary: string;
  };
  excess_return_vs_buy_and_hold: number | null;
  excess_return_vs_dca: number | null;
}

export interface OrderEvent {
  client_order_id: string;
  bank_order_id: string | null;
  order_type: string;
  direction: "BUY" | "SELL" | string;
  requested_grams: number;
  filled_grams: number | null;
  execution_price: number | null;
  fee: number | null;
  state: string;
  bank_event_at?: string | null;
  received_at: string | null;
}

export interface JDHoldingItem {
  bankCode: string;
  bankName: string;
  totalGram: number;
  avgCostPrice: number;
  totalIncome: number | null;
}

export interface JDHoldingsResponse {
  totalGramAll: number;
  avgCostPrice: number;
  holdingList: JDHoldingItem[];
}

export interface JDHoldingsSnapshot {
  data: JDHoldingsResponse | null;
  collected_at: string | null;
  last_success_at: string | null;
  status: "pending" | "fresh" | "stale" | "auth_required";
}

export interface JDAuthStatusResponse {
  logged_in: boolean;
  usable_seconds: number | null;
  expires_at: string | null;
  reauth_recommended: boolean;
  reauth_lead_seconds?: number;
  refresh_supported: boolean;
  pending_login: boolean;
  pending_login_expires_at: string | null;
  authorize_url: string | null;
  auto_renew_enabled?: boolean;
  auto_renew_in_progress?: boolean;
  last_auto_renew_at?: string | null;
  last_auto_renew_status?: "idle" | "running" | "succeeded" | "failed";
  last_auto_renew_error?: string | null;
}

export interface JDAuthLoginResponse {
  authorize_url: string;
  expires_at: string | null;
  message: string;
}

export interface JDAuthActionResponse {
  ok: boolean;
  message: string;
  status: JDAuthStatusResponse;
}

export interface WalkForwardFoldReport {
  fold_index: number;
  train_count: number;
  test_count: number;
  test_start_at: string;
  test_end_at: string;
  mae: number;
  directional_accuracy: number;
  brier_score: number;
  mean_predicted_return: number;
  mean_actual_return: number;
  lcb_coverage: number;
}

export interface TrainingPipelineReport {
  product_code: string;
  source_mode: "quotes" | "reference";
  horizons: string[];
  sample_counts: Record<string, number>;
  samples_output_path: string | null;
  model_output_dir: string | null;
  walk_forward_folds: Record<string, WalkForwardFoldReport[]>;
  oos_summary: Record<string, Record<string, number>>;
  trained_model_info: Record<string, unknown> | null;
}

export type KLineInterval = "m1" | "m5" | "m15" | "m30" | "m60" | "day" | "week" | "month" | "year";

export interface KLineBar {
  bar_time: string;
  open_price: number;
  high_price: number;
  low_price: number;
  close_price: number;
}

// ============================================================================
// TimesFM 3.0 多因子研究与训练工作流契约
// ============================================================================

export type HoldingPeriod = "1h" | "4h" | "1d" | "1w";
export type TimesFMSourceMode = "quotes" | "reference" | "kline";
export type TimesFMFreq = "1h" | "day";
export type TimesFMFactorPreset = "gold_multifactor" | "gold_ohlcv_multifactor";
export type TimesFMTrainMode = "multifactor_head" | "backbone_finetune";
export type TimesFMHeadModelType = "lgb" | "ridge";

export interface TimesFMDatasetManifest {
  dataset_id: string;
  dataset_dir: string;
  source_mode: TimesFMSourceMode;
  product_code: string;
  freq: TimesFMFreq;
  factor_preset: TimesFMFactorPreset;
  context_length: number;
  stride: number;
  horizons: HoldingPeriod[];
  horizon_steps: Record<string, number>;
  total_bars: number;
  sample_counts: Record<string, number>;
  past_only_factors: string[];
  past_future_factors: string[];
  tabular_factors: string[];
  start_time: string | null;
  end_time: string | null;
  spread_pct: number | null;
  buy_fee_rate: number;
  sell_fee_rate: number;
  created_at: string;
}

export interface TimesFMFinetuneSummary {
  enabled: boolean;
  epochs: number;
  learning_rate: number;
  unfreeze_last_layers: number;
  trainable_params: number;
  total_params: number;
  sample_count: number;
  initial_loss: number | null;
  final_loss: number | null;
  checkpoint_saved: boolean;
}

export interface TimesFMHorizonBacktestMetrics {
  horizon: HoldingPeriod;
  sample_count: number;
  mae: number;
  directional_accuracy: number;
  brier_score: number;
  lcb_coverage: number;
  ic: number | null;
  rank_ic: number | null;
  mean_predicted_return: number;
  mean_actual_return: number;
}

export interface TimesFMExperimentRecord {
  experiment_name: string;
  run_id: string;
  dataset_id: string;
  dataset_dir: string;
  product_code: string;
  freq: TimesFMFreq;
  train_mode: TimesFMTrainMode;
  head_model_type: TimesFMHeadModelType;
  factor_preset: TimesFMFactorPreset;
  use_covariates: boolean;
  context_length: number;
  horizons: HoldingPeriod[];
  holdout_ratio: number;
  holdout_start_at: string | null;
  holdout_end_at: string | null;
  oos_summary: Record<string, Record<string, number>>;
  finetune_summary: TimesFMFinetuneSummary;
  staging_dir: string;
  exported_model_dir: string | null;
  created_at: string;
}

export interface TimesFMStatusResponse {
  timesfm_enabled: boolean;
  predictor_backend: string;
  active_model_dir: string | null;
  dataset_dir: string;
  experiment_dir: string;
  experiment_count: number;
  latest_dataset: TimesFMDatasetManifest | null;
}

export interface TimesFMPrepareRequest {
  source_mode?: TimesFMSourceMode;
  product_code?: string;
  freq?: TimesFMFreq;
  kline_interval?: string;
  factor_preset?: TimesFMFactorPreset;
  context_length?: number;
  stride?: number;
  horizons?: HoldingPeriod[];
  spread_pct?: number;
  exclude_synthetic?: boolean;
  output_dir?: string;
}

export interface TimesFMPrepareReport {
  manifest: TimesFMDatasetManifest;
  written_files: number;
}

export interface TimesFMTrainRequest {
  dataset_dir?: string;
  experiment_name?: string;
  train_mode?: TimesFMTrainMode;
  head_model_type?: TimesFMHeadModelType;
  horizons?: HoldingPeriod[];
  n_splits?: number;
  holdout_ratio?: number;
  num_boost_round?: number;
  confidence_level?: number;
  batch_size?: number;
  max_samples?: number;
  use_covariates?: boolean;
  finetune_epochs?: number;
  finetune_lr?: number;
  finetune_batch_size?: number;
  unfreeze_last_layers?: number;
  output_model_dir?: string;
}

export interface TimesFMTrainReport {
  experiment: TimesFMExperimentRecord;
  sample_counts: Record<string, number>;
  walk_forward_folds: Record<string, WalkForwardFoldReport[]>;
  trained_model_info: Record<string, unknown> | null;
}

export interface TimesFMBacktestRequest {
  run_id?: string;
  model_dir?: string;
  dataset_dir?: string;
  initial_cash?: number;
  trade_grams_per_order?: number;
  dca_step_interval?: number;
  batch_size?: number;
}

export interface TimesFMBacktestReport {
  run_id: string | null;
  model_info: Record<string, unknown>;
  holdout_start_at: string;
  holdout_end_at: string;
  horizon_metrics: Record<string, TimesFMHorizonBacktestMetrics>;
  mapped_horizons: Record<string, string>;
  backtest_summary: BacktestSummary | null;
}

export interface TimesFMExportRequest {
  run_id?: string;
  output_model_dir: string;
}

export interface TimesFMExportReport {
  run_id: string;
  output_model_dir: string;
  checksum_path: string;
  files: Record<string, string>;
  model_info: Record<string, unknown>;
  verified: boolean;
}

export interface TimesFMExperimentListResponse {
  total: number;
  experiments: TimesFMExperimentRecord[];
}

// ============================================================================
// Qlib 量化研究、因子评估与训练工作流契约
// ============================================================================

export type QlibExportFrequency = "1min" | "5min" | "15min" | "30min" | "1h" | "day";
export type QlibFactorPreset = "gold_core" | "alpha158_lite" | "au9999_timing" | "custom";
export type QlibWindowMode = "expanding" | "rolling";
export type QlibLabelMode = "net" | "gross";

export interface QlibDatasetManifest {
  source_type: "local_sqlite" | "external_qlib";
  source_mode: "quotes" | "reference" | "kline" | "external";
  provider_uri: string;
  freq: string;
  instruments: string[];
  fields: string[];
  calendar_count: number;
  start_time: string | null;
  end_time: string | null;
  spread_pct: number | null;
  buy_fee_rate: number | null;
  sell_fee_rate: number | null;
  exported_at: string;
}

export interface QlibFactorMetric {
  factor_name: string;
  expression: string;
  sample_count: number;
  coverage_ratio: number;
  ic: number | null;
  rank_ic: number | null;
  icir: number | null;
}

export interface QlibExperimentRecord {
  experiment_name: string;
  run_id: string;
  source_type: "local_sqlite" | "external_qlib";
  provider_uri: string;
  model_type: "lgb" | "linear";
  factor_preset: QlibFactorPreset;
  instruments: string[];
  freq: string;
  horizons: HoldingPeriod[];
  serving_compatible: boolean;
  oos_summary: Record<string, Record<string, number>>;
  model_output_dir: string | null;
  qlib_recorder_id: string | null;
  created_at: string;
}

export interface QlibBacktestTrade {
  timestamp: string;
  action: "BUY" | "SELL";
  price: number;
  grams: number;
  fee: number;
  score: number;
  net_return_lcb: number | null;
  cash_after: number;
  position_grams_after: number;
  equity_after: number;
}

export interface QlibBacktestEquityPoint {
  timestamp: string;
  close_price: number;
  score: number;
  position_grams: number;
  strategy_equity: number;
  benchmark_equity: number;
}

export interface QlibBacktestSummary {
  initial_cash: number;
  final_equity: number;
  total_return: number;
  annual_return: number;
  sharpe_ratio: number;
  max_drawdown: number;
  calmar_ratio: number;
  benchmark_return: number;
  excess_return: number;
  trade_count: number;
  round_trip_count: number;
  win_rate: number;
  total_fees: number;
  bars_evaluated: number;
}

export interface QlibSignalRecord {
  signal_id: string;
  instrument: string;
  as_of: string;
  horizon: HoldingPeriod;
  predicted_return: number;
  net_return_lcb: number;
  win_probability: number;
  action: string;
  buy_thresh: number;
  sell_thresh: number;
  latest_close: number;
  model_dir: string;
  alert_id: string | null;
  feature_snapshot: Record<string, number | null>;
  created_at: string;
}

export interface QlibStatusResponse {
  pyqlib_installed: boolean;
  pyqlib_version: string | null;
  qlib_enabled: boolean;
  predictor_backend: string;
  provider_uri: string;
  external_provider_uri: string | null;
  active_model_dir: string | null;
  local_dataset: QlibDatasetManifest | null;
  external_dataset: QlibDatasetManifest | null;
  experiment_count: number;
}

export interface QlibExportRequest {
  source_mode?: "quotes" | "reference" | "kline";
  instruments?: string[];
  freq?: QlibExportFrequency;
  kline_interval?: string;
  spread_pct?: number;
  exclude_synthetic?: boolean;
  include_macro_features?: boolean;
  output_dir?: string;
}

export interface QlibExportReport {
  manifest: QlibDatasetManifest;
  written_files: number;
  skipped_instruments: string[];
}

export interface QlibFactorEvaluateRequest {
  source_type?: "local_sqlite" | "external_qlib";
  provider_uri?: string;
  instruments?: string[];
  freq?: "1h" | "day";
  horizon?: HoldingPeriod;
  factor_preset?: QlibFactorPreset;
  label_mode?: QlibLabelMode;
  custom_expressions?: Record<string, string>;
}

export interface QlibFactorEvaluateReport {
  source_type: "local_sqlite" | "external_qlib";
  provider_uri: string;
  freq: string;
  horizon: HoldingPeriod;
  factor_preset: string;
  instruments: string[];
  metrics: QlibFactorMetric[];
}

export interface QlibTrainRequest {
  experiment_name?: string;
  source_type?: "local_sqlite" | "external_qlib";
  provider_uri?: string;
  instruments?: string[];
  freq?: "1h" | "day";
  model_type?: "lgb" | "linear";
  factor_preset?: QlibFactorPreset;
  label_mode?: QlibLabelMode;
  custom_expressions?: Record<string, string>;
  horizons?: HoldingPeriod[];
  window_mode?: QlibWindowMode;
  train_window_bars?: number;
  step_bars?: number;
  use_robust_zscore?: boolean;
  n_splits?: number;
  num_boost_round?: number;
  lgb_max_depth?: number;
  lgb_num_leaves?: number;
  lgb_reg_alpha?: number;
  lgb_reg_lambda?: number;
  confidence_level?: number;
  spread_pct?: number;
  output_model_dir?: string;
}

export interface QlibTrainReport {
  experiment: QlibExperimentRecord;
  sample_counts: Record<string, number>;
  walk_forward_folds: Record<string, WalkForwardFoldReport[]>;
  trained_model_info: Record<string, unknown> | null;
}

export interface QlibExperimentListResponse {
  total: number;
  experiments: QlibExperimentRecord[];
}

export interface QlibBacktestRequest {
  source_type?: "local_sqlite" | "external_qlib";
  provider_uri?: string;
  instrument?: string;
  freq?: "1h" | "day";
  model_type?: "lgb" | "linear";
  factor_preset?: QlibFactorPreset;
  label_mode?: QlibLabelMode;
  horizon?: HoldingPeriod;
  window_mode?: QlibWindowMode;
  train_window_bars?: number;
  step_bars?: number;
  use_robust_zscore?: boolean;
  n_splits?: number;
  num_boost_round?: number;
  buy_thresh?: number;
  sell_thresh?: number;
  use_lcb_gate?: boolean;
  initial_cash?: number;
  cash_utilization?: number;
  trade_unit_grams?: number;
  open_cost?: number;
  close_cost?: number;
  spread_pct?: number;
}

export interface QlibBacktestReport {
  instrument: string;
  provider_uri: string;
  freq: string;
  horizon: HoldingPeriod;
  factor_preset: QlibFactorPreset;
  window_mode: QlibWindowMode;
  summary: QlibBacktestSummary;
  walk_forward_folds: WalkForwardFoldReport[];
  trades: QlibBacktestTrade[];
  equity_curve: QlibBacktestEquityPoint[];
}

export interface QlibSignalRequest {
  source_type?: "local_sqlite" | "external_qlib";
  provider_uri?: string;
  model_dir?: string;
  instrument?: string;
  freq?: "1h" | "day";
  horizon?: HoldingPeriod;
  buy_thresh?: number;
  sell_thresh?: number;
  use_lcb_gate?: boolean;
  publish_alert?: boolean;
  trade_grams?: number;
}

export interface QlibSignalResponse {
  signal: QlibSignalRecord;
}

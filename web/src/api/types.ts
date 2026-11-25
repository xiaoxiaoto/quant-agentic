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
  last_reconciliation: ReconciliationReport | null;
  snapshot: AccountSnapshot;
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
  };
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
  summary: string;
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
  macro_events: MacroCalendarEvent[];
}

export interface SnapshotReconcileResponse {
  report: ReconciliationReport;
  recommended_risk_state: string;
  kill_switch_active: boolean;
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
  totalIncome: number;
}

export interface JDHoldingsResponse {
  totalGramAll: number;
  avgCostPrice: number;
  holdingList: JDHoldingItem[];
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

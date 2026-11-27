import type {
  AccountSnapshot,
  AlertPayload,
  BacktestSummary,
  FifoSummaryResponse,
  JDAuthActionResponse,
  JDAuthLoginResponse,
  JDAuthStatusResponse,
  JDHoldingsSnapshot,
  KLineBar,
  KLineInterval,
  LiveQuoteStatus,
  LiveStatusResponse,
  LiveStepResult,
  MacroCalendarEvent,
  ModelStatusResponse,
  NewsOverviewResponse,
  OrderEvent,
  PaperStepResult,
  QlibBacktestReport,
  QlibBacktestRequest,
  QlibExperimentListResponse,
  QlibExportReport,
  QlibExportRequest,
  QlibFactorEvaluateReport,
  QlibFactorEvaluateRequest,
  QlibSignalRequest,
  QlibSignalResponse,
  QlibStatusResponse,
  QlibTrainReport,
  QlibTrainRequest,
  ReconciliationReport,
  ReferenceProduct,
  SnapshotReconcileResponse,
  TimesFMBacktestReport,
  TimesFMBacktestRequest,
  TimesFMExperimentListResponse,
  TimesFMExportReport,
  TimesFMExportRequest,
  TimesFMPrepareReport,
  TimesFMPrepareRequest,
  TimesFMStatusResponse,
  TimesFMTrainReport,
  TimesFMTrainRequest,
  TrainingPipelineReport,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const body = (await resp.json()) as { detail?: unknown };
      if (typeof body.detail === "string") {
        detail = body.detail;
      } else if (body.detail) {
        detail = JSON.stringify(body.detail);
      }
    } catch {
      // 忽略非 JSON 错误体
    }
    throw new Error(detail);
  }
  return (await resp.json()) as T;
}

export const api = {
  getLiveStatus: (signal?: AbortSignal) => request<LiveStatusResponse>("/api/v1/live/status", { signal }),
  runLiveStep: (payload?: { product_code?: string; trade_grams?: number; reference_product_code?: string }) =>
    request<LiveStepResult>("/api/v1/live/step", {
      method: "POST",
      body: JSON.stringify(payload ?? {}),
    }),
  haltLive: (reason = "WEB_DASHBOARD_EMERGENCY_HALT") =>
    request<{ kill_switch_active: boolean; message: string }>("/api/v1/live/halt", {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),
  resumeLive: () =>
    request<{ kill_switch_active: boolean; message: string }>("/api/v1/live/resume", {
      method: "POST",
    }),

  listAlerts: (limit = 30, signal?: AbortSignal) => request<AlertPayload[]>(`/api/v1/alerts?limit=${limit}`, { signal }),
  confirmAlert: (alertId: string) =>
    request<AlertPayload>(`/api/v1/alerts/${encodeURIComponent(alertId)}/confirm`, { method: "POST" }),
  declineAlert: (alertId: string) =>
    request<AlertPayload>(`/api/v1/alerts/${encodeURIComponent(alertId)}/decline`, { method: "POST" }),
  executeAlert: (
    alertId: string,
    payload: { filled_grams: number; filled_price: number; fee?: number; bank_order_id?: string }
  ) =>
    request<AlertPayload>(`/api/v1/alerts/${encodeURIComponent(alertId)}/execute`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  getReferenceProducts: (signal?: AbortSignal) => request<ReferenceProduct[]>("/api/v1/quotes/live/products", { signal }),
  getAllQuoteStatuses: (signal?: AbortSignal) => request<LiveQuoteStatus[]>("/api/v1/quotes/live/status/all", { signal }),
  getKLines: (productCode: string, interval: KLineInterval, signal: AbortSignal, limit = 240) => {
    const query = new URLSearchParams({ product_code: productCode, interval, limit: String(limit) });
    // 伦敦金的合并序列最高支持日线。
    if (productCode !== "WG-XAUUSD" || ["week", "month", "year"].includes(interval)) {
      query.set("source", "jd_public_kline");
    }
    return request<KLineBar[]>(`/api/v1/quotes/bars/kline?${query}`, { signal });
  },
  getQuoteHistory: (productCode: string, signal: AbortSignal) =>
    request<[string, number][]>(`/api/v1/quotes/live/history?product_code=${encodeURIComponent(productCode)}`, { signal }),

  getModelStatus: (signal?: AbortSignal) => request<ModelStatusResponse>("/api/v1/models/status", { signal }),
  getNewsOverview: (limit = 50, signal?: AbortSignal) => request<NewsOverviewResponse>(`/api/v1/models/news/overview?limit=${limit}`, { signal }),
  registerMacroEvent: (payload: {
    event_name: string;
    scheduled_at: string;
    lock_window_hours_before?: number;
    lock_window_hours_after?: number;
  }) =>
    request<MacroCalendarEvent>("/api/v1/models/news/macro-events", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  reconcileJdHoldings: (bankCash?: number) => {
    const query = bankCash !== undefined ? `?bank_cash=${encodeURIComponent(bankCash)}` : "";
    return request<SnapshotReconcileResponse>(`/api/v1/reconciliation/jd-holdings${query}`, {
      method: "POST",
    });
  },
  getJdHoldings: (signal?: AbortSignal) => request<JDHoldingsSnapshot>("/api/v1/jd/holdings/snapshot", { signal }),
  getJdAuthStatus: (signal?: AbortSignal) => request<JDAuthStatusResponse>("/api/v1/jd/auth/status", { signal }),
  startJdLogin: () => request<JDAuthLoginResponse>("/api/v1/jd/auth/login", { method: "POST" }),
  completeJdLogin: () => request<JDAuthActionResponse>("/api/v1/jd/auth/complete", { method: "POST" }),
  renewJdAuth: () => request<JDAuthActionResponse>("/api/v1/jd/auth/renew", { method: "POST" }),
  verifyJdAuth: () => request<JDAuthActionResponse>("/api/v1/jd/auth/verify", { method: "POST" }),
  logoutJdAuth: () => request<JDAuthActionResponse>("/api/v1/jd/auth/logout", { method: "POST" }),
  listReconciliationReports: (limit = 20, signal?: AbortSignal) =>
    request<ReconciliationReport[]>(`/api/v1/reconciliation/reports?limit=${limit}`, { signal }),
  listOrders: (signal?: AbortSignal, limit?: number) =>
    request<OrderEvent[]>(`/api/v1/ledger/orders${limit !== undefined ? `?limit=${limit}` : ""}`, { signal }),
  getLedgerFifoSummary: (
    params?: { only_open?: boolean; reference_price?: number; offset?: number; limit?: number },
    signal?: AbortSignal
  ) => {
    const q = new URLSearchParams();
    if (params?.only_open !== undefined) q.set("only_open", String(params.only_open));
    if (params?.reference_price !== undefined) q.set("reference_price", String(params.reference_price));
    if (params?.offset !== undefined) q.set("offset", String(params.offset));
    if (params?.limit !== undefined) q.set("limit", String(params.limit));
    const suffix = q.toString() ? `?${q.toString()}` : "";
    return request<FifoSummaryResponse>(`/api/v1/ledger/fifo-summary${suffix}`, { signal });
  },
  initLedgerFromJd: (initialCash?: number) =>
    request<AccountSnapshot>(
      initialCash !== undefined ? `/api/v1/ledger/init-from-jd?initial_cash=${initialCash}` : "/api/v1/ledger/init-from-jd",
      { method: "POST" }
    ),

  stepPaper: (payload?: { product_code?: string; trade_grams?: number }) =>
    request<PaperStepResult>("/api/v1/paper/step", {
      method: "POST",
      body: JSON.stringify(payload ?? {}),
    }),
  submitLocalPaperOrder: (payload: { direction: "BUY" | "SELL"; grams: number; product_code?: string }) =>
    request<unknown>("/api/v1/paper/orders", {
      method: "POST",
      body: JSON.stringify({ engine: "local", ...payload }),
    }),

  runBacktest: (payload: {
    product_code?: string;
    initial_cash?: number;
    trade_grams?: number;
    dca_step_interval?: number;
  }) =>
    request<BacktestSummary>("/api/v1/backtest/run", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  runTrainingPipeline: (payload: {
    reference_product_code?: string;
    product_code?: string;
    spread_pct?: number;
    horizons?: string[];
    n_splits?: number;
    num_boost_round?: number;
    confidence_level?: number;
    output_model_dir?: string;
  }) =>
    request<TrainingPipelineReport>("/api/v1/models/train", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // TimesFM 3.0 多因子训练工作流
  getTimesfmStatus: (signal?: AbortSignal) => request<TimesFMStatusResponse>("/api/v1/timesfm/status", { signal }),
  prepareTimesfm: (payload: TimesFMPrepareRequest) =>
    request<TimesFMPrepareReport>("/api/v1/timesfm/prepare", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  trainTimesfm: (payload: TimesFMTrainRequest) =>
    request<TimesFMTrainReport>("/api/v1/timesfm/train", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  backtestTimesfm: (payload: TimesFMBacktestRequest) =>
    request<TimesFMBacktestReport>("/api/v1/timesfm/backtest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  exportTimesfm: (payload: TimesFMExportRequest) =>
    request<TimesFMExportReport>("/api/v1/timesfm/export", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listTimesfmExperiments: (limit = 20, signal?: AbortSignal) =>
    request<TimesFMExperimentListResponse>(`/api/v1/timesfm/experiments?limit=${limit}`, { signal }),

  // Qlib 量化研究与训练工作流
  getQlibStatus: (signal?: AbortSignal) => request<QlibStatusResponse>("/api/v1/qlib/status", { signal }),
  exportQlib: (payload: QlibExportRequest) =>
    request<QlibExportReport>("/api/v1/qlib/export", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  evaluateQlibFactors: (payload: QlibFactorEvaluateRequest) =>
    request<QlibFactorEvaluateReport>("/api/v1/qlib/factors/evaluate", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  trainQlib: (payload: QlibTrainRequest) =>
    request<QlibTrainReport>("/api/v1/qlib/train", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  backtestQlib: (payload: QlibBacktestRequest) =>
    request<QlibBacktestReport>("/api/v1/qlib/backtest", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  generateQlibSignal: (payload: QlibSignalRequest) =>
    request<QlibSignalResponse>("/api/v1/qlib/signal", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  listQlibExperiments: (limit = 20, signal?: AbortSignal) =>
    request<QlibExperimentListResponse>(`/api/v1/qlib/experiments?limit=${limit}`, { signal }),
};

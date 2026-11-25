import type {
  AccountSnapshot,
  AlertPayload,
  BacktestSummary,
  JDHoldingsResponse,
  LiveQuoteStatus,
  LiveStatusResponse,
  LiveStepResult,
  MacroCalendarEvent,
  ModelStatusResponse,
  NewsOverviewResponse,
  NewsSyncReport,
  OrderEvent,
  PaperStepResult,
  ReconciliationReport,
  ReferenceProduct,
  SnapshotReconcileResponse,
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
  collectAllQuotes: () => request<LiveQuoteStatus[]>("/api/v1/quotes/live/collect/all", { method: "POST" }),

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
  syncNewsFlash: (pageSize = 10) =>
    request<NewsSyncReport>(`/api/v1/models/news/sync?page_size=${pageSize}`, { method: "POST" }),

  reconcileJdHoldings: (bankCash?: number) =>
    request<SnapshotReconcileResponse>("/api/v1/reconciliation/jd-holdings", {
      method: "POST",
      body: JSON.stringify(bankCash !== undefined ? { bank_cash: bankCash } : {}),
    }),
  getJdHoldings: (signal?: AbortSignal) => request<JDHoldingsResponse>("/api/v1/jd/holdings", { signal }),
  listReconciliationReports: (limit = 20, signal?: AbortSignal) =>
    request<ReconciliationReport[]>(`/api/v1/reconciliation/reports?limit=${limit}`, { signal }),
  listOrders: (signal?: AbortSignal, limit?: number) =>
    request<OrderEvent[]>(`/api/v1/ledger/orders${limit !== undefined ? `?limit=${limit}` : ""}`, { signal }),
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
};

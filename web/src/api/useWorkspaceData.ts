import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./client";
import type { AlertPayload, JDHoldingsResponse } from "./types";

const QUERIES = {
  live: api.getLiveStatus,
  models: api.getModelStatus,
  news: (signal: AbortSignal) => api.getNewsOverview(50, signal),
  products: api.getReferenceProducts,
  quotes: api.getAllQuoteStatuses,
  alerts: (signal: AbortSignal) => api.listAlerts(30, signal),
  orders: (signal: AbortSignal) => api.listOrders(signal, 200),
  reconciliation: (signal: AbortSignal) => api.listReconciliationReports(15, signal),
};

export const QUERY_LABELS: Record<keyof typeof QUERIES, string> = {
  live: "实盘状态",
  models: "模型状态",
  news: "资讯",
  products: "品种清单",
  quotes: "行情",
  alerts: "交易提醒",
  orders: "订单",
  reconciliation: "对账记录",
};

type QueryKey = keyof typeof QUERIES;
const SCREEN_QUERIES: QueryKey[] = ["live", "products", "quotes"];
const CORE_QUERIES: QueryKey[] = ["live", "models", "news", "products", "quotes", "alerts", "reconciliation"];

type QueryState = {
  [K in QueryKey]?: {
    data?: Awaited<ReturnType<(typeof QUERIES)[K]>>;
    updatedAt?: string;
    failed: boolean;
  };
};

export function useWorkspaceData(autoRefresh: boolean, screenOnly = false, includeOrders = false) {
  const [queries, setQueries] = useState<QueryState>({});
  const [jdHoldings, setJdHoldings] = useState<JDHoldingsResponse | null>(null);
  const [jdHoldingsError, setJdHoldingsError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);

  const refresh = useCallback(async (priority = true) => {
    if (!priority && active.current) return;
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]);
    if (priority) setRefreshing(true);

    let liveLoggedIn = false;
    const read = async <K extends QueryKey>(key: K) => {
      try {
        const data = await QUERIES[key](signal);
        if (controller.signal.aborted) return false;
        if (key === "live" && data && typeof data === "object" && "jd_logged_in" in data) {
          liveLoggedIn = Boolean(data.jd_logged_in);
        }
        const updatedAt = new Date().toISOString();
        setQueries((previous) => ({ ...previous, [key]: { data, updatedAt, failed: false } }));
        return true;
      } catch {
        if (!controller.signal.aborted) {
          setQueries((previous) => ({ ...previous, [key]: { ...previous[key], failed: true } }));
        }
        return false;
      }
    };

    const keys: QueryKey[] = screenOnly
      ? SCREEN_QUERIES
      : includeOrders
        ? [...CORE_QUERIES, "orders"]
        : CORE_QUERIES;
    const results = await Promise.all(keys.map(read));
    if (!screenOnly && !controller.signal.aborted) {
      if (liveLoggedIn) {
        try {
          const holdings = await api.getJdHoldings(signal);
          if (!controller.signal.aborted) {
            setJdHoldings(holdings);
            setJdHoldingsError(null);
          }
        } catch (err) {
          if (!controller.signal.aborted) {
            setJdHoldingsError(err instanceof Error ? err.message : "获取京东实盘持仓失败");
          }
        }
      } else {
        setJdHoldings(null);
        setJdHoldingsError(null);
      }
    }
    if (active.current === controller) {
      active.current = null;
      if (results.some(Boolean)) setLastUpdated(new Date().toISOString());
      setRefreshing(false);
    }
  }, [screenOnly, includeOrders]);

  useEffect(() => {
    void refresh();
    return () => {
      active.current?.abort();
      active.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => void refresh(false), 10000);
    return () => window.clearInterval(timer);
  }, [autoRefresh, refresh]);

  function rememberAlert(alert: AlertPayload) {
    setQueries((previous) => {
      const data = previous.alerts?.data ?? [];
      if (data.some((item) => item.alert_id === alert.alert_id)) return previous;
      return { ...previous, alerts: { failed: false, ...previous.alerts, data: [alert, ...data] } };
    });
  }

  return { queries, jdHoldings, jdHoldingsError, lastUpdated, refreshing, refresh, rememberAlert };
}

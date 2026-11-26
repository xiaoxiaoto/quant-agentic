import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./client";
import type { AlertPayload } from "./types";

const QUERIES = {
  live: api.getLiveStatus,
  models: api.getModelStatus,
  news: (signal: AbortSignal) => api.getNewsOverview(50, signal),
  products: api.getReferenceProducts,
  holdings: api.getJdHoldings,
  jdAuth: api.getJdAuthStatus,
  quotes: api.getAllQuoteStatuses,
  alerts: (signal: AbortSignal) => api.listAlerts(30, signal),
  orders: (signal: AbortSignal) => api.listOrders(signal),
  reconciliation: (signal: AbortSignal) => api.listReconciliationReports(15, signal),
};

export const QUERY_LABELS: Record<keyof typeof QUERIES, string> = {
  live: "实盘状态",
  models: "模型状态",
  news: "资讯",
  products: "品种清单",
  holdings: "京东持仓",
  jdAuth: "京东账号",
  quotes: "行情",
  alerts: "交易提醒",
  orders: "订单",
  reconciliation: "对账记录",
};

type QueryKey = keyof typeof QUERIES;
const SCREEN_QUERIES: QueryKey[] = ["live", "products", "quotes"];
const CORE_QUERIES: QueryKey[] = [
  "live",
  "models",
  "news",
  "products",
  "quotes",
  "holdings",
  "jdAuth",
  "alerts",
  "reconciliation",
];

type QueryState = {
  [K in QueryKey]?: {
    data?: Awaited<ReturnType<(typeof QUERIES)[K]>>;
    updatedAt?: string;
    failed: boolean;
  };
};

import type { WorkspaceTab } from "../components/WorkspaceNav";

export function useWorkspaceData(
  autoRefresh: boolean,
  screenOnly = false,
  includeOrders = false,
  activeTab?: WorkspaceTab
) {
  const [queries, setQueries] = useState<QueryState>({});
  const [refreshing, setRefreshing] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);
  const ordersControllerRef = useRef<AbortController | null>(null);
  const seqRef = useRef<Partial<Record<QueryKey, number>>>({});
  const productsLoadedRef = useRef(false);
  const includeOrdersRef = useRef(includeOrders);
  includeOrdersRef.current = includeOrders;
  const activeTabRef = useRef<WorkspaceTab | undefined>(activeTab);
  activeTabRef.current = activeTab;

  const activeKeys: QueryKey[] = screenOnly
    ? SCREEN_QUERIES
    : includeOrders
      ? [...CORE_QUERIES, "orders"]
      : CORE_QUERIES;

  const nextSeq = (key: QueryKey): number => {
    const next = (seqRef.current[key] ?? 0) + 1;
    seqRef.current[key] = next;
    return next;
  };

  const refresh = useCallback(async (priority = true) => {
    if (!priority && active.current) return;
    if (!priority && typeof document !== "undefined" && document.hidden) return;
    const includesOrders = !screenOnly && priority && includeOrdersRef.current;
    active.current?.abort();
    if (includesOrders) {
      ordersControllerRef.current?.abort();
      ordersControllerRef.current = null;
    }
    const controller = new AbortController();
    active.current = controller;
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]);
    if (priority) setRefreshing(true);

    const baseKeys: QueryKey[] = screenOnly
      ? SCREEN_QUERIES
      : includesOrders
        ? [...CORE_QUERIES, "orders"]
        : CORE_QUERIES;
    const tab = activeTabRef.current;
    const keys = priority
      ? baseKeys
      : baseKeys.filter((k) => {
          if (k === "products" && productsLoadedRef.current) return false;
          if (!tab) return true;
          if (k === "models") return tab === "news" || tab === "research";
          if (k === "alerts") return tab === "overview";
          if (k === "reconciliation") return tab === "portfolio";
          if (k === "quotes") return tab === "overview" || tab === "markets" || tab === "portfolio";
          if (k === "holdings") return tab === "overview" || tab === "portfolio" || tab === "accounts";
          return true;
        });

    const seqs = Object.fromEntries(keys.map((key) => [key, nextSeq(key)])) as Record<QueryKey, number>;
    const updates: QueryState = {};

    const read = async <K extends QueryKey>(key: K) => {
      try {
        const data = await QUERIES[key](signal);
        if (controller.signal.aborted || seqRef.current[key] !== seqs[key]) return false;
        const updatedAt = new Date().toISOString();
        if (key === "products") productsLoadedRef.current = true;
        updates[key] = { data, updatedAt, failed: false } as QueryState[K];
        return true;
      } catch {
        if (!controller.signal.aborted && seqRef.current[key] === seqs[key]) {
          updates[key] = { failed: true } as QueryState[K];
        }
        return false;
      }
    };

    const results = await Promise.all(keys.map(read));
    if (active.current === controller) {
      active.current = null;
      if (Object.keys(updates).length > 0) {
        setQueries((previous) => {
          const merged: QueryState = { ...previous };
          const applyPatch = <K extends QueryKey>(key: K) => {
            const patch = updates[key];
            if (!patch) return;
            merged[key] = patch.failed ? { ...previous[key], failed: true } : patch;
          };
          for (const key of Object.keys(updates) as QueryKey[]) {
            applyPatch(key);
          }
          return merged;
        });
      }
      if (results.some(Boolean)) setLastUpdated(new Date().toISOString());
      setRefreshing(false);
    }
  }, [screenOnly]);

  useEffect(() => {
    void refresh();
    return () => {
      active.current?.abort();
      active.current = null;
      ordersControllerRef.current?.abort();
      ordersControllerRef.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    if (screenOnly || !includeOrders) return;
    ordersControllerRef.current?.abort();
    const controller = new AbortController();
    ordersControllerRef.current = controller;
    const seq = nextSeq("orders");
    const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]);
    void (async () => {
      try {
        const data = await QUERIES.orders(signal);
        if (controller.signal.aborted || seqRef.current.orders !== seq) return;
        const updatedAt = new Date().toISOString();
        setQueries((previous) => ({ ...previous, orders: { data, updatedAt, failed: false } }));
      } catch {
        if (!controller.signal.aborted && seqRef.current.orders === seq) {
          setQueries((previous) => ({ ...previous, orders: { ...previous.orders, failed: true } }));
        }
      } finally {
        if (ordersControllerRef.current === controller) {
          ordersControllerRef.current = null;
        }
      }
    })();
    return () => {
      controller.abort();
      if (ordersControllerRef.current === controller) {
        ordersControllerRef.current = null;
      }
    };
  }, [includeOrders, screenOnly]);

  const prevTabRef = useRef<WorkspaceTab | undefined>(activeTab);
  useEffect(() => {
    if (screenOnly || !activeTab) return;
    if (prevTabRef.current === activeTab) return;
    prevTabRef.current = activeTab;
    void refresh(false);
  }, [activeTab, refresh, screenOnly]);

  useEffect(() => {
    if (!autoRefresh) return;
    const timer = window.setInterval(() => {
      if (typeof document === "undefined" || !document.hidden) {
        void refresh(false);
      }
    }, 10000);
    const handleVisibilityChange = () => {
      if (typeof document !== "undefined" && !document.hidden) {
        void refresh(false);
      }
    };
    document.addEventListener("visibilitychange", handleVisibilityChange);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", handleVisibilityChange);
    };
  }, [autoRefresh, refresh]);

  function rememberAlert(alert: AlertPayload) {
    setQueries((previous) => {
      const data = previous.alerts?.data ?? [];
      if (data.some((item) => item.alert_id === alert.alert_id)) return previous;
      return { ...previous, alerts: { ...previous.alerts, failed: false, data: [alert, ...data] } };
    });
  }

  return { queries, activeKeys, lastUpdated, refreshing, refresh, rememberAlert };
}

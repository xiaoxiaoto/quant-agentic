import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AlertPayload } from "../api/types";
import { useWorkspaceData } from "../api/useWorkspaceData";

describe("useWorkspaceData", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("rememberAlert 清除陈旧 failed 标志并去重", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "临时失败" }), {
        status: 500,
        headers: { "Content-Type": "application/json" },
      })
    );

    const { result } = renderHook(() => useWorkspaceData(false, false, false));
    await waitFor(() => {
      expect(result.current.queries.alerts?.failed).toBe(true);
    });

    const alert: AlertPayload = {
      alert_id: "alt_1",
      decision_id: "dec_1",
      created_at: "2026-05-10T00:00:00Z",
      expires_at: "2026-05-10T01:00:00Z",
      action: "BUY",
      suggested_grams: 2,
      current_bid_price: 600,
      current_ask_price: 601,
      spread: 1,
      break_even_price: 603,
      net_exit_loss_cny: 3,
      horizon_primary: "1w",
      reason_codes: [],
      risk_flags: [],
      state: "ALERT_SENT",
      disclaimer: "研究输出",
      is_reference_estimated: false,
      user_confirmed_at: null,
      user_executed_at: null,
    };

    act(() => {
      result.current.rememberAlert(alert);
      result.current.rememberAlert(alert);
    });

    expect(result.current.queries.alerts?.failed).toBe(false);
    expect(result.current.queries.alerts?.data).toHaveLength(1);
    expect(result.current.activeKeys).not.toContain("orders");
  });

  it("后台轮询在页面隐藏时暂停，可见后跳过已加载的 products、orders 及非当前页签接口", async () => {
    vi.useFakeTimers();
    try {
      const requestedUrls: string[] = [];
      vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
        requestedUrls.push(url);
        if (url.includes("/quotes/live/products")) {
          return new Response(JSON.stringify([]), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          });
        }
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      });

      const hiddenSpy = vi.spyOn(document, "hidden", "get").mockReturnValue(false);
      renderHook(() => useWorkspaceData(true, false, true, "portfolio"));

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      // 首轮加载完成，清空记录
      expect(requestedUrls.some((u) => u.includes("/quotes/live/products"))).toBe(true);
      expect(requestedUrls.some((u) => u.includes("/ledger/orders"))).toBe(true);
      expect(requestedUrls.some((u) => u.includes("/api/v1/alerts"))).toBe(true);
      requestedUrls.length = 0;

      // 页面隐藏期间不触发后台轮询
      hiddenSpy.mockReturnValue(true);
      await act(async () => {
        vi.advanceTimersByTime(10000);
        await Promise.resolve();
      });
      expect(requestedUrls).toHaveLength(0);

      // 页面恢复可见触发后台轮询：跳过 products、orders 以及非 portfolio 页签的 models/alerts
      hiddenSpy.mockReturnValue(false);
      await act(async () => {
        document.dispatchEvent(new Event("visibilitychange"));
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(requestedUrls.length).toBeGreaterThan(0);
      expect(requestedUrls.some((u) => u.includes("/quotes/live/products"))).toBe(false);
      expect(requestedUrls.some((u) => u.includes("/ledger/orders"))).toBe(false);
      expect(requestedUrls.some((u) => u.includes("/models/status"))).toBe(false);
      expect(requestedUrls.some((u) => u.includes("/api/v1/alerts"))).toBe(false);
      expect(requestedUrls.some((u) => u.includes("/reconciliation"))).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("后台轮询 refresh(false) 不应中断正在进行中的 orders 懒加载请求", async () => {
    let resolveOrders!: (res: Response) => void;
    let ordersAborted = false;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
      if (url.includes("/ledger/orders")) {
        init?.signal?.addEventListener("abort", () => {
          ordersAborted = true;
        });
        return new Promise<Response>((resolve) => {
          resolveOrders = resolve;
        });
      }
      return Promise.resolve(
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );
    });

    const { result, rerender } = renderHook(
      ({ includeOrders, tab }: { includeOrders: boolean; tab: "overview" | "portfolio" }) =>
        useWorkspaceData(false, false, includeOrders, tab),
      { initialProps: { includeOrders: false, tab: "overview" as "overview" | "portfolio" } }
    );
    await waitFor(() => {
      expect(result.current.refreshing).toBe(false);
    });

    rerender({ includeOrders: true, tab: "portfolio" });
    await act(async () => {
      await Promise.resolve();
    });

    // 触发后台轮询，不应 abort 进行中的 orders 请求
    await act(async () => {
      await result.current.refresh(false);
    });
    expect(ordersAborted).toBe(false);

    await act(async () => {
      resolveOrders(
        new Response(JSON.stringify([{ order_id: "ord_1" }]), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        })
      );
      await Promise.resolve();
    });
    await waitFor(() => {
      expect(result.current.queries.orders?.data).toHaveLength(1);
    });
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";

describe("api client contracts", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("reconcileJdHoldings 将 bank_cash 作为 Query 参数发送而非 JSON Body", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ report: { summary: "ok" }, recommended_risk_state: "NORMAL" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await api.reconcileJdHoldings(50000);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/reconciliation/jd-holdings?bank_cash=50000");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBeUndefined();
  });

  it("listOrders 未传 limit 时请求全量流水接口", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await api.listOrders();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/ledger/orders");
  });

  it("服务端返回 JSON detail 时抛出可读中文错误", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "京东账户尚未授权" }), {
        status: 401,
        headers: { "Content-Type": "application/json" },
      })
    );

    await expect(api.getJdHoldings()).rejects.toThrow("京东账户尚未授权");
  });
});

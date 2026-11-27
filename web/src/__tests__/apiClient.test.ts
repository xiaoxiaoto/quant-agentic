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

  it("getKLines 按品种与周期正确设置 source 查询参数", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );
    const controller = new AbortController();

    await api.getKLines("WG-XAUUSD", "m5", controller.signal, 240);
    await api.getKLines("WG-XAUUSD", "week", controller.signal, 240);
    await api.getKLines("CMBC-JCJ", "day", controller.signal, 100);
    await api.getQuoteHistory("CMBC-JCJ", controller.signal);

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/quotes/bars/kline?product_code=WG-XAUUSD&interval=m5&limit=240");
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v1/quotes/bars/kline?product_code=WG-XAUUSD&interval=week&limit=240&source=jd_public_kline"
    );
    expect(fetchMock.mock.calls[2][0]).toBe(
      "/api/v1/quotes/bars/kline?product_code=CMBC-JCJ&interval=day&limit=100&source=jd_public_kline"
    );
    expect(fetchMock.mock.calls[3][0]).toBe("/api/v1/quotes/live/history?product_code=CMBC-JCJ");
  });

  it("TimesFM 与 Qlib 工作流接口路由与请求方法正确", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      new Response(JSON.stringify({}), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await api.getTimesfmStatus();
    await api.prepareTimesfm({ source_mode: "kline", freq: "1h" });
    await api.trainTimesfm({ experiment_name: "exp1" });
    await api.backtestTimesfm({ run_id: "r1" });
    await api.exportTimesfm({ run_id: "r1", output_model_dir: "models/timesfm/v1" });
    await api.getQlibStatus();
    await api.exportQlib({ source_mode: "kline", freq: "day" });
    await api.evaluateQlibFactors({ factor_preset: "au9999_timing" });
    await api.trainQlib({ experiment_name: "q1" });
    await api.backtestQlib({ instrument: "SGE-Au99.99" });
    await api.generateQlibSignal({ instrument: "SGE-Au99.99" });

    expect(fetchMock.mock.calls.map(([url, init]) => [url, init?.method ?? "GET"])).toEqual([
      ["/api/v1/timesfm/status", "GET"],
      ["/api/v1/timesfm/prepare", "POST"],
      ["/api/v1/timesfm/train", "POST"],
      ["/api/v1/timesfm/backtest", "POST"],
      ["/api/v1/timesfm/export", "POST"],
      ["/api/v1/qlib/status", "GET"],
      ["/api/v1/qlib/export", "POST"],
      ["/api/v1/qlib/factors/evaluate", "POST"],
      ["/api/v1/qlib/train", "POST"],
      ["/api/v1/qlib/backtest", "POST"],
      ["/api/v1/qlib/signal", "POST"],
    ]);
  });
});

import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../api/client";
import type { KLineBar } from "../api/types";
import LiveScreen from "../components/LiveScreen";
import { ScreenTrend } from "../components/ScreenTrend";

const { setData, createChartMock, removeMock } = vi.hoisted(() => ({
  setData: vi.fn(),
  createChartMock: vi.fn(),
  removeMock: vi.fn(),
}));
vi.mock("lightweight-charts", () => ({
  CandlestickSeries: "candlestick",
  LineSeries: "line",
  ColorType: { Solid: "solid" },
  CrosshairMode: { Normal: 0 },
  createChart: (...args: unknown[]) => {
    createChartMock(...args);
    return {
      addSeries: (type: string) => ({
        setData: (data: unknown) => setData(type, data),
        applyOptions: vi.fn(),
      }),
      timeScale: () => ({ fitContent: vi.fn() }),
      remove: removeMock,
    };
  },
}));

afterEach(() => {
  vi.useRealTimers();
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  cleanup();
  vi.restoreAllMocks();
  setData.mockReset();
  createChartMock.mockClear();
  removeMock.mockClear();
});

it("两张图独立切换，民生五分钟 K 线按源时间聚合有效报价", async () => {
  const bar: KLineBar = {
    bar_time: "2026-11-26T09:00:00+08:00",
    open_price: 2700, high_price: 2720, low_price: 2690, close_price: 2710,
  };
  vi.spyOn(api, "getKLines").mockImplementation(async (product) => product === "WG-XAUUSD" ? [bar] : []);
  vi.spyOn(api, "getQuoteHistory").mockResolvedValue([
    ["2026-11-26T09:04:00+08:00", 762],
    ["2026-11-26T09:00:00+08:00", 760],
    ["2026-11-26T09:01:00+08:00", 757],
    ["2026-11-26T09:02:00+08:00", 764],
  ]);
  render(
    <>
      <ScreenTrend productCode="CMBC-JCJ" title="民生积存金" unit="元/克" refreshKey={0} />
      <ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={0} />
    </>,
  );
  const domestic = within(screen.getByRole("group", { name: "民生积存金周期" }));
  const london = within(screen.getByRole("group", { name: "伦敦金周期" }));

  fireEvent.click(domestic.getByRole("button", { name: "5分" }));
  await screen.findByText(/民生参考报价聚合/);

  expect(london.getByRole("button", { name: "分时" }).getAttribute("aria-pressed")).toBe("true");
  expect(setData).toHaveBeenCalledWith("candlestick", [
    { time: Date.parse(bar.bar_time) / 1000, open: 760, high: 764, low: 757, close: 762 },
  ]);
});

it("快速切换时丢弃上一个周期的迟到响应", async () => {
  let resolveFirst!: (bars: KLineBar[]) => void;
  const first = new Promise<KLineBar[]>((resolve) => { resolveFirst = resolve; });
  const requests = vi.spyOn(api, "getKLines").mockReturnValueOnce(first).mockResolvedValue([]);
  render(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={0} />);
  fireEvent.click(screen.getByRole("button", { name: "5分" }));
  await screen.findByText("该周期暂无行情");

  expect(requests.mock.calls[0][2].aborted).toBe(true);
  await act(async () => resolveFirst([{
    bar_time: "2026-11-26T09:00:00+08:00",
    open_price: 2700, high_price: 2720, low_price: 2690, close_price: 2710,
  }]));
  expect(screen.getByText("该周期暂无行情")).toBeTruthy();
  expect(setData).not.toHaveBeenCalled();
});

it("加载失败显示空态，刷新失败保留旧图且不销毁重建图表实例", async () => {
  const bar: KLineBar = {
    bar_time: "2026-11-26T09:00:00+08:00",
    open_price: 2700, high_price: 2720, low_price: 2690, close_price: 2710,
  };
  vi.spyOn(api, "getKLines")
    .mockRejectedValueOnce(new Error("network"))
    .mockResolvedValueOnce([bar])
    .mockRejectedValueOnce(new Error("timeout"));

  const { rerender } = render(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={0} />);
  await screen.findByText("行情图加载失败");

  rerender(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={1} />);
  await screen.findByText(/合并行情/);
  expect(createChartMock).toHaveBeenCalledTimes(1);

  rerender(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={2} />);
  await screen.findByText(/更新失败，显示上次数据/);
  expect(createChartMock).toHaveBeenCalledTimes(1);
});

it("年 K 空数据回退月 K 按北京年份聚合，并过滤非法/乱序重复时间戳", async () => {
  vi.spyOn(api, "getKLines").mockImplementation(async (_product, interval) => {
    if (interval === "year") return [];
    if (interval === "month") {
      return [
        { bar_time: "invalid-date", open_price: 1, high_price: 2, low_price: 1, close_price: 2 },
        { bar_time: "2025-12-01T00:00:00+08:00", open_price: 600, high_price: 650, low_price: 590, close_price: 640 },
        { bar_time: "2025-01-01T00:00:00+08:00", open_price: 500, high_price: 520, low_price: 495, close_price: 515 },
      ];
    }
    return [
      { bar_time: "2026-11-25T23:59:00+08:00", open_price: 2680, high_price: 2685, low_price: 2675, close_price: 2682 },
      { bar_time: "2026-11-26T00:01:00+08:00", open_price: 2700, high_price: 2710, low_price: 2695, close_price: 2705 },
      { bar_time: "2026-11-26T00:01:00+08:00", open_price: 2705, high_price: 2720, low_price: 2690, close_price: 2712 },
    ];
  });

  render(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={0} />);
  await screen.findByText(/1分钟收盘价/);
  expect(setData).toHaveBeenLastCalledWith("line", [
    { time: Date.parse("2026-11-26T00:01:00+08:00") / 1000, value: 2712 },
  ]);

  fireEvent.click(screen.getByRole("button", { name: "年K" }));
  await screen.findByText(/京东月 K 聚合/);
  expect(setData).toHaveBeenLastCalledWith("candlestick", [
    {
      time: Date.parse("2025-01-01T00:00:00+08:00") / 1000,
      open: 500, high: 650, low: 495, close: 640,
    },
  ]);
});

it("卸载时中止在途请求并销毁图表实例", async () => {
  const requests = vi.spyOn(api, "getKLines").mockImplementation(() => new Promise(() => {}));
  const { unmount } = render(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={0} />);
  expect(requests).toHaveBeenCalledTimes(1);
  unmount();
  expect(requests.mock.calls[0][2].aborted).toBe(true);
  expect(removeMock).toHaveBeenCalledTimes(1);
});

it("切换 productCode 时不复用旧品种图表数据，新品种加载失败显示失败态", async () => {
  const bar: KLineBar = {
    bar_time: "2026-11-26T09:00:00+08:00",
    open_price: 2700, high_price: 2720, low_price: 2690, close_price: 2710,
  };
  vi.spyOn(api, "getKLines")
    .mockResolvedValueOnce([bar])
    .mockRejectedValueOnce(new Error("boom"));

  const { rerender } = render(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={0} />);
  await screen.findByText(/合并行情/);

  rerender(<ScreenTrend productCode="SGE-Au99.99" title="上金所" unit="元/克" refreshKey={0} />);
  await screen.findByText("行情图加载失败");
  expect(screen.queryByText(/合并行情/)).toBeNull();
});

it("支持定时轮询、跳过重复空探测、页面隐藏中止与切回冷却", async () => {
  vi.useFakeTimers();
  const klinesSpy = vi.spyOn(api, "getKLines").mockResolvedValue([]);
  const historySpy = vi.spyOn(api, "getQuoteHistory").mockResolvedValue([
    ["2026-11-26T09:00:00+08:00", 760],
  ]);

  render(<ScreenTrend productCode="CMBC-JCJ" title="民生积存金" unit="元/克" refreshKey={0} />);
  await act(async () => { await Promise.resolve(); await Promise.resolve(); });
  expect(klinesSpy).toHaveBeenCalledTimes(1);
  expect(historySpy).toHaveBeenCalledTimes(1);

  // 60 秒后第二轮轮询应跳过已知必空的 getKLines 探测，直接拉取 getQuoteHistory
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(klinesSpy).toHaveBeenCalledTimes(1);
  expect(historySpy).toHaveBeenCalledTimes(2);

  // 切后台暂停轮询，5 秒内切回前台受 15 秒冷却保护不立刻重拉
  Object.defineProperty(document, "hidden", { configurable: true, value: true });
  fireEvent(document, new Event("visibilitychange"));
  await act(async () => { await vi.advanceTimersByTimeAsync(5_000); });
  Object.defineProperty(document, "hidden", { configurable: true, value: false });
  fireEvent(document, new Event("visibilitychange"));
  expect(historySpy).toHaveBeenCalledTimes(2);

  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(historySpy).toHaveBeenCalledTimes(3);
});

it("请求超过 10 秒超时后中止并进入失败状态", async () => {
  vi.useFakeTimers();
  const requests = vi.spyOn(api, "getKLines").mockImplementation(
    (_product, _interval, signal) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }),
  );

  render(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={0} />);
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(requests.mock.calls[0][2].aborted).toBe(true);
  expect(screen.getByText("行情图加载失败")).toBeTruthy();
});

it("旧请求晚于新请求启动后 settle 时不会取消新请求的 10 秒超时保护", async () => {
  vi.useFakeTimers();
  let rejectFirst!: (reason?: unknown) => void;
  const requests = vi.spyOn(api, "getKLines")
    .mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectFirst = reject; }))
    .mockImplementationOnce((_product, _interval, signal) => new Promise((_resolve, reject) => {
      signal.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
    }));

  const { rerender } = render(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={0} />);
  // 触发第二次请求（中止第一次请求，但第一次请求的 Promise 延迟 settle）
  rerender(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={1} />);
  await act(async () => {
    rejectFirst(new DOMException("Aborted", "AbortError"));
    await Promise.resolve();
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
  expect(requests).toHaveBeenCalledTimes(2);
  expect(requests.mock.calls[1][2].aborted).toBe(true);
  expect(screen.getByText("行情图加载失败")).toBeTruthy();
});

it("手动刷新 refreshKey 递增时绕过空探测缓存并重新查询 K 线", async () => {
  const klinesSpy = vi.spyOn(api, "getKLines")
    .mockResolvedValueOnce([])
    .mockResolvedValueOnce([
      { bar_time: "2026-11-26T09:00:00+08:00", open_price: 760, high_price: 762, low_price: 759, close_price: 761 },
    ]);
  vi.spyOn(api, "getQuoteHistory").mockResolvedValue([["2026-11-26T09:00:00+08:00", 760]]);

  const { rerender } = render(<ScreenTrend productCode="CMBC-JCJ" title="民生积存金" unit="元/克" refreshKey={0} />);
  await screen.findByText(/民生参考报价/);
  expect(klinesSpy).toHaveBeenCalledTimes(1);

  rerender(<ScreenTrend productCode="CMBC-JCJ" title="民生积存金" unit="元/克" refreshKey={1} />);
  await screen.findByText(/京东 K 线/);
  expect(klinesSpy).toHaveBeenCalledTimes(2);
});

it("图表渲染异常时展示渲染失败遮罩而非静默吞错", async () => {
  setData.mockImplementationOnce(() => { throw new Error("render error"); });
  vi.spyOn(api, "getKLines").mockResolvedValue([
    { bar_time: "2026-11-26T09:00:00+08:00", open_price: 2700, high_price: 2720, low_price: 2690, close_price: 2710 },
  ]);

  render(<ScreenTrend productCode="WG-XAUUSD" title="伦敦金" unit="美元/盎司" refreshKey={0} />);
  await screen.findByText("行情图渲染失败");
});

it("LiveScreen 立即刷新按钮在刷新中禁用，空闲点击同时触发 onRefresh 与双图重拉", async () => {
  const klinesSpy = vi.spyOn(api, "getKLines").mockResolvedValue([
    { bar_time: "2026-11-26T09:00:00+08:00", open_price: 2700, high_price: 2720, low_price: 2690, close_price: 2710 },
  ]);
  const onRefresh = vi.fn();

  const { rerender } = render(
    <LiveScreen
      products={[]}
      quotes={[]}
      liveStatus={null}
      quotesFailed={false}
      liveFailed={false}
      quotesUpdatedAt={undefined}
      refreshing={true}
      onRefresh={onRefresh}
    />,
  );
  const refreshBtn = screen.getByRole("button", { name: "立即刷新" }) as HTMLButtonElement;
  expect(refreshBtn.disabled).toBe(true);

  rerender(
    <LiveScreen
      products={[]}
      quotes={[]}
      liveStatus={null}
      quotesFailed={false}
      liveFailed={false}
      quotesUpdatedAt={undefined}
      refreshing={false}
      onRefresh={onRefresh}
    />,
  );
  expect(refreshBtn.disabled).toBe(false);
  const before = klinesSpy.mock.calls.length;
  fireEvent.click(refreshBtn);
  expect(onRefresh).toHaveBeenCalledTimes(1);
  await act(async () => { await Promise.resolve(); });
  expect(klinesSpy.mock.calls.length).toBeGreaterThan(before);
});

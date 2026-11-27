import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import { memo, useEffect, useId, useRef, useState } from "react";
import { api } from "../api/client";
import type { KLineInterval } from "../api/types";

const PERIODS = [
  ["intraday", "分时"],
  ["m1", "1分"], ["m5", "5分"], ["m15", "15分"], ["m30", "30分"], ["m60", "60分"],
  ["day", "日K"], ["week", "周K"], ["month", "月K"], ["year", "年K"],
] as const;

const FAST_POLL_MS = 60_000;
const SLOW_POLL_MS = 300_000;
const VISIBILITY_COOLDOWN_MS = 15_000;
const REQUEST_TIMEOUT_MS = 10_000;
const EMPTY_PROBE_TTL_MS = 300_000;
const BEIJING_OFFSET_S = 28_800;
const SECONDS_PER_DAY = 86_400;

type Period = (typeof PERIODS)[number][0];
type Bar = { time: UTCTimestamp; open: number; high: number; low: number; close: number };
type ChartData = { bars: Bar[]; source: string };
type ChartSeriesHandle =
  | { kind: "line"; series: ISeriesApi<"Line"> }
  | { kind: "candlestick"; series: ISeriesApi<"Candlestick"> };

const beijingDay = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
});
const beijingMinute = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const beijingDateTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});
const toSeconds = (value: string) => Math.floor(Date.parse(value) / 1000) as UTCTimestamp;
const formatTime = (formatter: Intl.DateTimeFormat, time: Time) => formatter.format(Number(time) * 1000);

function rollUp(records: Bar[], bucket: (time: UTCTimestamp) => UTCTimestamp): Bar[] {
  const bars = new Map<number, Bar>();
  for (const record of records) {
    const time = bucket(record.time);
    const bar = bars.get(time);
    if (bar) {
      bar.high = Math.max(bar.high, record.high);
      bar.low = Math.min(bar.low, record.low);
      bar.close = record.close;
    } else {
      bars.set(time, { ...record, time });
    }
  }
  return [...bars.values()];
}

function normalizeBars(records: Bar[]): Bar[] {
  const valid = records.filter(
    (bar) =>
      Number.isFinite(bar.time) &&
      Number.isFinite(bar.open) &&
      Number.isFinite(bar.high) &&
      Number.isFinite(bar.low) &&
      Number.isFinite(bar.close),
  );
  valid.sort((a, b) => a.time - b.time);
  return rollUp(valid, (time) => time);
}

function getPollIntervalMs(period: Period): number {
  return period === "intraday" || /^m\d/.test(period) ? FAST_POLL_MS : SLOW_POLL_MS;
}

function isEmptyProbeCached(cache: Map<string, number>, key: string, now: number): boolean {
  const expiresAt = cache.get(key);
  if (expiresAt === undefined) return false;
  if (expiresAt <= now) {
    cache.delete(key);
    return false;
  }
  return true;
}

async function loadChart(
  productCode: string,
  period: Period,
  signal: AbortSignal,
  knownEmptyProbes: Map<string, number>,
  bypassEmptyCache: boolean,
): Promise<ChartData> {
  const interval: KLineInterval = period === "intraday" ? "m1" : period;
  const probeKey = `${productCode}:${interval}`;
  const now = Date.now();
  const cachedEmpty = !bypassEmptyCache && isEmptyProbeCached(knownEmptyProbes, probeKey, now);
  const skipCmbcMinuteProbe = productCode === "CMBC-JCJ" && /^m\d/.test(interval) && cachedEmpty;
  const skipYearProbe = period === "year" && cachedEmpty;

  let records = skipCmbcMinuteProbe || skipYearProbe
    ? []
    : await api.getKLines(productCode, interval, signal, period === "intraday" ? 1440 : 240);
  if (records.length === 0 && (period === "year" || (productCode === "CMBC-JCJ" && /^m\d/.test(interval)))) {
    if (knownEmptyProbes.size >= 32) knownEmptyProbes.clear();
    knownEmptyProbes.set(probeKey, now + EMPTY_PROBE_TTL_MS);
  } else if (records.length > 0) {
    knownEmptyProbes.delete(probeKey);
  }
  const yearlyFallback = period === "year" && records.length === 0;
  if (yearlyFallback) records = await api.getKLines(productCode, "month", signal, 5000);
  let bars = normalizeBars(records.map((bar) => ({
    time: toSeconds(bar.bar_time),
    open: bar.open_price, high: bar.high_price, low: bar.low_price, close: bar.close_price,
  })));
  let source = productCode === "WG-XAUUSD" && !["week", "month", "year"].includes(interval)
    ? "合并行情"
    : "京东 K 线";

  if (yearlyFallback) {
    bars = normalizeBars(rollUp(bars, (time) => {
      const year = Number(beijingDay.formatToParts(time * 1000).find(({ type }) => type === "year")?.value);
      return toSeconds(`${year}-01-01T00:00:00+08:00`);
    }));
    source = "京东月 K 聚合";
  } else if (bars.length === 0 && productCode === "CMBC-JCJ" && /^m\d/.test(interval)) {
    const quotes = await api.getQuoteHistory(productCode, signal);
    const ordered = quotes
      .map(([timestamp, price]) => ({ ms: Date.parse(timestamp), price }))
      .filter(({ ms, price }) => Number.isFinite(ms) && Number.isFinite(price))
      .sort((a, b) => a.ms - b.ms)
      .map(({ ms, price }) => ({
        time: Math.floor(ms / 1000) as UTCTimestamp,
        open: price, high: price, low: price, close: price,
      }));
    const seconds = period === "intraday" ? 1 : Number(interval.slice(1)) * 60;
    bars = rollUp(ordered, (time) => (Math.floor(time / seconds) * seconds) as UTCTimestamp);
    source = period === "intraday" ? "民生参考报价" : "民生参考报价聚合";
  } else if (period === "intraday") {
    source += " · 1分钟收盘价";
  }

  const last = bars.at(-1);
  if (period === "intraday" && last) {
    const dayStart =
      Math.floor((last.time + BEIJING_OFFSET_S) / SECONDS_PER_DAY) * SECONDS_PER_DAY - BEIJING_OFFSET_S;
    bars = bars.filter((bar) => bar.time >= dayStart);
  } else {
    bars = bars.slice(-240);
  }
  return { bars, source };
}

export const ScreenTrend = memo(function ScreenTrend({ productCode, title, unit, refreshKey }: {
  productCode: string;
  title: string;
  unit: string;
  refreshKey: number;
}) {
  const titleId = useId();
  const host = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ChartSeriesHandle | null>(null);
  const hasDataRef = useRef(false);
  const emptyProbeCacheRef = useRef<Map<string, number>>(new Map());
  const lastRefreshKeyRef = useRef(refreshKey);
  const [period, setPeriod] = useState<Period>("intraday");
  const [result, setResult] = useState<{ productCode: string; period: Period; data?: ChartData; failed: boolean }>();
  const [renderFailed, setRenderFailed] = useState(false);
  const current = result?.productCode === productCode && result?.period === period ? result : undefined;
  const data = current?.data;

  useEffect(() => {
    let disposed = false;
    let activeController: AbortController | null = null;
    let timer: number | undefined;
    let lastSuccessAt = 0;
    const manualRefresh = refreshKey !== lastRefreshKeyRef.current;
    if (manualRefresh) {
      lastRefreshKeyRef.current = refreshKey;
      emptyProbeCacheRef.current.clear();
    }

    function clearScheduled() {
      if (timer !== undefined) {
        window.clearTimeout(timer);
        timer = undefined;
      }
    }

    function scheduleNext(delayMs: number) {
      clearScheduled();
      if (!disposed && !document.hidden) {
        timer = window.setTimeout(() => void load(false), delayMs);
      }
    }

    async function load(bypassEmptyCache: boolean) {
      clearScheduled();
      const previous = activeController;
      activeController = null;
      previous?.abort();

      const controller = new AbortController();
      activeController = controller;
      let timedOut = false;
      const requestTimeout = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, REQUEST_TIMEOUT_MS);
      controller.signal.addEventListener("abort", () => window.clearTimeout(requestTimeout), { once: true });

      try {
        const nextData = await loadChart(
          productCode,
          period,
          controller.signal,
          emptyProbeCacheRef.current,
          bypassEmptyCache,
        );
        if (!disposed && activeController === controller) {
          if (timedOut) {
            setResult((prev) => ({
              productCode,
              period,
              data: prev?.productCode === productCode && prev?.period === period ? prev.data : undefined,
              failed: true,
            }));
          } else if (!controller.signal.aborted) {
            lastSuccessAt = Date.now();
            setResult({ productCode, period, data: nextData, failed: false });
          }
        }
      } catch {
        if (!disposed && activeController === controller) {
          setResult((prev) => ({
            productCode,
            period,
            data: prev?.productCode === productCode && prev?.period === period ? prev.data : undefined,
            failed: true,
          }));
        }
      } finally {
        window.clearTimeout(requestTimeout);
        if (!disposed && activeController === controller && !document.hidden) {
          scheduleNext(getPollIntervalMs(period));
        }
      }
    }

    function onVisibilityChange() {
      if (document.hidden) {
        clearScheduled();
        const inflight = activeController;
        activeController = null;
        inflight?.abort();
      } else if (!disposed) {
        const elapsed = Date.now() - lastSuccessAt;
        if (lastSuccessAt > 0 && elapsed < VISIBILITY_COOLDOWN_MS) {
          scheduleNext(VISIBILITY_COOLDOWN_MS - elapsed);
        } else {
          void load(false);
        }
      }
    }

    document.addEventListener("visibilitychange", onVisibilityChange);
    if (!document.hidden) {
      void load(manualRefresh);
    }
    return () => {
      disposed = true;
      const inflight = activeController;
      activeController = null;
      inflight?.abort();
      clearScheduled();
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, [productCode, period, refreshKey]);

  const intraday = period === "intraday";
  const minuteScale = /^m\d/.test(period);

  useEffect(() => {
    const element = host.current;
    if (!element) return;

    const chart = createChart(element, {
      autoSize: true,
      layout: {
        textColor: "#b6bfcb",
        background: { type: ColorType.Solid, color: "#131b27" },
        attributionLogo: true,
      },
      grid: { vertLines: { color: "#263247" }, horzLines: { color: "#27354a" } },
      rightPriceScale: { borderColor: "#2c3a50" },
      timeScale: {
        timeVisible: intraday || minuteScale,
        secondsVisible: false,
        borderColor: "#2c3a50",
        tickMarkFormatter: (time: Time) => formatTime(intraday ? beijingMinute : minuteScale ? beijingDateTime : beijingDay, time),
      },
      localization: { timeFormatter: (time: Time) => formatTime(intraday || minuteScale ? beijingDateTime : beijingDay, time) },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: false,
      handleScale: false,
    });
    const handle: ChartSeriesHandle = intraday
      ? { kind: "line", series: chart.addSeries(LineSeries, { color: "#f0d5a0", lineWidth: 2, priceLineVisible: false }) }
      : {
          kind: "candlestick",
          series: chart.addSeries(CandlestickSeries, {
            upColor: "#f47b77", downColor: "#51b79a", borderVisible: false,
            wickUpColor: "#f47b77", wickDownColor: "#51b79a",
          }),
        };
    chartRef.current = chart;
    seriesRef.current = handle;
    hasDataRef.current = false;
    return () => {
      chartRef.current = null;
      seriesRef.current = null;
      hasDataRef.current = false;
      chart.remove();
    };
  }, [intraday, minuteScale]);

  useEffect(() => {
    const chart = chartRef.current;
    const handle = seriesRef.current;
    if (!chart || !handle) return;
    const bars = data?.bars ?? [];
    if (bars.length === 0) {
      setRenderFailed(false);
      if (hasDataRef.current) {
        hasDataRef.current = false;
        try {
          handle.series.setData([]);
        } catch {
          // 忽略图表清空异常
        }
      }
      return;
    }
    try {
      if (handle.kind === "line") {
        handle.series.applyOptions?.({ pointMarkersVisible: bars.length === 1 });
        handle.series.setData(bars.map(({ time, close }) => ({ time, value: close })));
      } else {
        handle.series.setData(bars);
      }
      chart.timeScale().fitContent();
      hasDataRef.current = true;
      setRenderFailed(false);
    } catch {
      hasDataRef.current = false;
      try {
        handle.series.setData([]);
      } catch {
        // 忽略清理旧图时的二次异常
      }
      setRenderFailed(true);
    }
  }, [data, intraday, minuteScale]);

  const latest = data?.bars.at(-1);
  return (
    <section className="surface screen-panel flex min-w-0 flex-col" aria-labelledby={titleId}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id={titleId} className="text-lg font-semibold">{title}</h2>
        <p className="text-xs text-slate-400">{unit} · 北京时间</p>
      </div>
      <div role="group" aria-label={`${title}周期`} className="mt-3 flex flex-wrap gap-1">
        {PERIODS.map(([value, label]) => (
          <button key={value} type="button" aria-pressed={period === value} onClick={() => {
            if (value !== period) {
              setResult(undefined);
              setRenderFailed(false);
              setPeriod(value);
            }
          }} className="min-h-8 rounded border border-transparent px-2 text-xs text-slate-300 hover:bg-white/8 aria-pressed:border-amber-200/30 aria-pressed:bg-amber-200/10 aria-pressed:text-amber-100">
            {label}
          </button>
        ))}
      </div>
      <div className="relative mt-3 min-h-56 flex-1">
        <div ref={host} className="absolute inset-0" aria-hidden="true" />
        {(!latest || renderFailed) && (
          <p role="status" className="absolute inset-0 grid place-items-center bg-[#131b27] p-3 text-center text-sm text-slate-300">
            {renderFailed ? "行情图渲染失败" : current?.failed ? "行情图加载失败" : current ? "该周期暂无行情" : "正在读取行情…"}
          </p>
        )}
      </div>
      {data && latest && !renderFailed && (
        <p className="mt-2 text-xs text-slate-400" role="status">
          {data.source} · {intraday ? "行情日期" : "末根"} {formatTime(minuteScale ? beijingDateTime : beijingDay, latest.time)}
          {current?.failed && <span className="text-amber-200"> · 更新失败，显示上次数据</span>}
        </p>
      )}
    </section>
  );
});

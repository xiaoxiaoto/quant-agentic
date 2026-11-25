import { ColorType, createChart, CrosshairMode, LineSeries, type IChartApi, type ISeriesApi, type Time, type UTCTimestamp } from "lightweight-charts";
import { useEffect, useRef, useState } from "react";
import type { ReferenceQuote } from "../api/types";
import { comparableQuote } from "../quotes";

const chartTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23",
});

export function ScreenTrend({ quote, quotesFailed }: { quote?: ReferenceQuote | null; quotesFailed: boolean }) {
  const host = useRef<HTMLDivElement>(null);
  const [points, setPoints] = useState<{ time: UTCTimestamp; value: number }[]>([]);

  useEffect(() => {
    if (quotesFailed || !comparableQuote(quote) || !quote.quote_at) return;
    const seconds = Math.floor(Date.parse(quote.quote_at) / 1000);
    if (!Number.isFinite(seconds)) return;

    setPoints((previous) => {
      const last = previous.at(-1);
      if (last && seconds < last.time) return previous;
      if (last && seconds === last.time) {
        if (last.value === quote.price) return previous;
        return [...previous.slice(0, -1), { time: last.time, value: quote.price }];
      }
      return [...previous.slice(-119), { time: seconds as UTCTimestamp, value: quote.price }];
    });
  }, [quote, quotesFailed]);

  const series = useRef<ISeriesApi<"Line"> | null>(null);
  const chartRef = useRef<IChartApi | null>(null);

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
        timeVisible: true,
        secondsVisible: true,
        borderColor: "#2c3a50",
        tickMarkFormatter: (time: Time) => chartTime.format(Number(time) * 1000),
      },
      localization: { timeFormatter: (time: Time) => chartTime.format(Number(time) * 1000) },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll: false,
      handleScale: false,
    });
    chartRef.current = chart;
    series.current = chart.addSeries(LineSeries, {
      color: "#f0d5a0",
      lineWidth: 2,
      priceLineVisible: false,
      crosshairMarkerRadius: 4,
    });
    return () => {
      chart.remove();
      chartRef.current = null;
      series.current = null;
    };
  }, []);

  useEffect(() => {
    series.current?.setData(points);
    chartRef.current?.timeScale().fitContent();
  }, [points]);

  return (
    <section className="surface screen-panel flex min-w-0 flex-col" aria-labelledby="screen-trend-title">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h2 id="screen-trend-title" className="text-lg font-semibold">民生金价走势</h2>
        <p className="text-sm text-slate-400">本次打开后积累 · 北京时间</p>
      </div>
      <div className="relative mt-4 min-h-44 flex-1">
        <div ref={host} className="absolute inset-0" aria-hidden="true" />
        {points.length < 2 && (
          <div className="pointer-events-none absolute inset-0 grid place-items-center bg-[#131b27]/75 p-3 text-center text-sm text-slate-300">
            {points.length === 0 ? "等待带源时间的正常报价" : "正在积累报价，收到下一笔后显示走势"}
          </div>
        )}
      </div>
      <p className="mt-2 text-xs text-slate-400">有效报价 {points.length} 条 · 元/克</p>
    </section>
  );
}

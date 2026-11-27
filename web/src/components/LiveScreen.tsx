import { Activity, ArrowLeft, Maximize, Minimize, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import type { LiveQuoteStatus, LiveStatusResponse, ReferenceProduct, ReferenceQuote } from "../api/types";
import { comparableQuote, formatBeijingTime, londonSpread, quoteBasis } from "../quotes";
import { RISK_STYLE } from "../riskStatus";
import { QuoteQuality, QuoteTime } from "./QuoteMetadata";
import { ScreenTrend } from "./ScreenTrend";

const WATCHLIST = [
  { code: "CZB-JCJ", name: "浙商积存金" },
  { code: "ICBC-JCJ", name: "工行积存金" },
  { code: "SGE-Au99.99", name: "黄金9999" },
  { code: "WG-XAUUSD", name: "伦敦金" },
];

interface LiveScreenProps {
  quotes: LiveQuoteStatus[];
  products: ReferenceProduct[];
  liveStatus: LiveStatusResponse | null;
  quotesUpdatedAt?: string;
  quotesFailed: boolean;
  liveFailed: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}

function ScreenClock() {
  const [now, setNow] = useState(() => new Date().toISOString());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date().toISOString()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  return (
    <div className="text-right">
      <time dateTime={now} className="text-lg font-medium tabular-nums sm:text-xl">{formatBeijingTime(now)}</time>
      <div className="text-xs text-slate-400">北京时间</div>
    </div>
  );
}

function ScreenQuoteMeta({ quote }: { quote?: ReferenceQuote | null }) {
  return (
    <p className="flex flex-wrap gap-x-2.5 gap-y-1 text-sm text-slate-300">
      {quote && <span>{quoteBasis(quote.price_side)}</span>}
      <QuoteQuality quote={quote} />
      {quote && <QuoteTime quote={quote} showLabel />}
    </p>
  );
}

export default function LiveScreen({
  quotes, products, liveStatus, quotesUpdatedAt, quotesFailed, liveFailed, refreshing, onRefresh,
}: LiveScreenProps) {
  const [fullscreen, setFullscreen] = useState(!!document.fullscreenElement);
  const [fullscreenError, setFullscreenError] = useState(false);
  const [chartRefresh, setChartRefresh] = useState(0);
  useEffect(() => {
    document.title = "GoldQuant · 实时大屏";
    const sync = () => setFullscreen(!!document.fullscreenElement);
    document.addEventListener("fullscreenchange", sync);
    return () => document.removeEventListener("fullscreenchange", sync);
  }, []);

  async function toggleFullscreen() {
    setFullscreenError(false);
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await document.documentElement.requestFullscreen();
    } catch {
      setFullscreenError(true);
    }
  }

  const quoteMap = new Map(quotes.map((status) => [status.product_code, status]));
  const productsByCode = new Map(products.map((product) => [product.code, product.name]));
  const domestic = quoteMap.get("CMBC-JCJ");
  const london = quoteMap.get("WG-XAUUSD");
  const quote = domestic?.latest;
  const spread = londonSpread(domestic, london, quotesFailed);
  const normalQuotes = quotes.filter((status) => comparableQuote(status.latest)).length;
  const risk = liveStatus && RISK_STYLE[liveStatus.risk_state];
  const riskText = liveFailed
    ? risk ? `上次已知 · ${risk.label}` : "风控状态未知"
    : risk?.label ?? (liveStatus ? "风控状态未知" : "正在读取风控");
  const fullscreenAvailable = document.fullscreenEnabled;

  return (
    <main className="live-screen">
      <header className="screen-header flex flex-wrap items-center justify-between gap-4 border-b border-white/10 pb-4">
        <div className="flex items-center gap-3">
          <span className="brand-mark size-10"><Activity className="size-5" /></span>
          <div>
            <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">黄金实时大屏</h1>
            <p className="text-xs text-slate-400">GoldQuant</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 sm:gap-3">
          <ScreenClock />
          <button type="button" aria-label="立即刷新" title="立即刷新" disabled={refreshing} onClick={() => {
            setChartRefresh((value) => value + 1);
            onRefresh();
          }}
            className="secondary-button min-h-10 px-2.5">
            <RefreshCw className={`size-4 ${refreshing ? "animate-spin" : ""}`} />
          </button>
          <button type="button" onClick={() => void toggleFullscreen()} disabled={!fullscreenAvailable}
            title={fullscreenAvailable ? undefined : "当前浏览器无法进入全屏"} className="secondary-button min-h-10">
            {fullscreen ? <Minimize className="size-4" /> : <Maximize className="size-4" />}
            {fullscreen ? "退出全屏" : "全屏"}
          </button>
          <a href="/" className="quiet-button min-h-10 px-2 text-sm text-slate-300">
            <ArrowLeft className="size-4" /> 工作台
          </a>
        </div>
      </header>

      <div className="space-y-1.5">
        <div className="flex flex-wrap items-center justify-between gap-x-5 gap-y-1.5 text-sm text-slate-300">
          <p className="flex flex-wrap items-center gap-x-3 gap-y-1" role="status">
            <span className={`inline-flex items-center gap-2 font-medium ${liveFailed ? "text-amber-200" : risk?.tone ?? ""}`}>
              <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />{riskText}
            </span>
            {liveStatus?.kill_switch_active && (
              <span className={liveFailed ? "text-slate-300" : "text-rose-200"}>{liveFailed ? "上次已知 · 急停已启用" : "急停已启用"}</span>
            )}
            {liveStatus && !liveFailed && <span>{liveStatus.is_trading_session ? "交易时段内" : "休市"}</span>}
          </p>
          <p className="flex flex-wrap items-center gap-x-2 gap-y-1" role="status">
            <span>报价每 10 秒刷新 · 图表分时每分钟刷新</span>
            {quotesFailed && <strong className="font-medium text-amber-200">{quotesUpdatedAt ? "行情更新失败 · 显示上次数据" : "行情读取失败"}</strong>}
            {quotesUpdatedAt
              ? <span>{quotesFailed ? "上次刷新" : "行情刷新"} <time dateTime={quotesUpdatedAt}>{formatBeijingTime(quotesUpdatedAt)}</time></span>
              : !quotesFailed && <span>正在读取行情</span>}
          </p>
        </div>
        {fullscreenError && <p role="alert" className="text-sm text-amber-200">浏览器未能切换全屏，可使用浏览器菜单切换全屏。</p>}
      </div>

      <section className="market-stage screen-panel flex flex-wrap items-center justify-between gap-4" aria-labelledby="screen-benchmark-title">
        <div className="market-orbit" aria-hidden="true"><span /></div>
        <div className="relative">
          <h2 id="screen-benchmark-title" className="text-lg font-medium text-slate-100 sm:text-xl">民生银行积存金</h2>
          <p className="market-price screen-price mt-2 flex flex-wrap items-baseline gap-x-2">
            <span>{quote ? `¥${quote.price.toFixed(2)}` : "—"}</span>
            <span className="text-base font-medium tracking-normal text-slate-300 sm:text-lg">元/克</span>
          </p>
          <div className="mt-3"><ScreenQuoteMeta quote={quote} /></div>
        </div>

        <dl className="relative grid grid-cols-2 gap-3 sm:gap-5">
          <div className="min-w-0">
            <dt className="text-sm text-slate-300">伦敦金折合</dt>
            <dd className="mt-1 text-xl font-semibold tabular-nums sm:text-2xl">
              {london?.cny_per_g != null ? `¥${london.cny_per_g.toFixed(2)}` : "—"}
              <span className="ml-1 text-xs font-normal text-slate-300">元/克</span>
            </dd>
          </div>
          <div className="min-w-0 border-l border-white/12 pl-3 sm:pl-5">
            <dt className="text-sm text-slate-300">{spread.value == null ? "价差不可用" : spread.reason ? "参考价差" : "溢价"}</dt>
            <dd className="mt-1 text-xl font-semibold tabular-nums sm:text-2xl">
              {spread.value == null ? "—" : `${spread.value >= 0 ? "+" : ""}${spread.value.toFixed(2)}`}
              <span className="ml-1 text-xs font-normal text-slate-300">元/克</span>
            </dd>
            {spread.reason && <dd className="mt-1 text-xs text-slate-300">{spread.reason}</dd>}
          </div>
        </dl>
      </section>
      <div className="screen-main">
        <ScreenTrend productCode="CMBC-JCJ" title="民生积存金走势" unit="元/克" refreshKey={chartRefresh} />
        <ScreenTrend productCode="WG-XAUUSD" title="伦敦金走势" unit="美元/盎司" refreshKey={chartRefresh} />
      </div>

      <section className="surface screen-panel" aria-labelledby="screen-watchlist-title">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-white/10 pb-3">
          <h2 id="screen-watchlist-title" className="section-heading">重点行情</h2>
          <p className="text-sm text-slate-400">
            {quotes.length > 0 ? `正常报价 ${normalQuotes} / ${quotes.length} 品种` : "暂无报价统计"}
          </p>
        </div>
        <ul className="screen-watchlist">
          {WATCHLIST.map(({ code, name }) => {
            const current = quoteMap.get(code)?.latest;
            const unit = current?.unit === "USD/oz" ? "美元/盎司" : "元/克";
            return (
              <li key={code} className="min-w-0">
                <p className="text-sm text-slate-300">{productsByCode.get(code) ?? name}</p>
                <p className="mt-1 text-xl font-semibold tabular-nums sm:text-2xl">
                  {current ? current.price.toFixed(2) : "—"}
                  {current && <span className="ml-1.5 text-xs font-normal text-slate-300">{unit}</span>}
                </p>
                <div className="mt-1.5"><ScreenQuoteMeta quote={current} /></div>
              </li>
            );
          })}
        </ul>
      </section>
    </main>
  );
}

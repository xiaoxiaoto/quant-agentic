import { ArrowRight, ArrowUpRight, MoreHorizontal, Newspaper, Play, Scale } from "lucide-react";
import type {
  AlertPayload,
  Evaluation,
  JDHoldingsResponse,
  LiveQuoteStatus,
  LiveStatusResponse,
  NewsOverviewResponse,
  ReferenceProduct,
} from "../api/types";
import { formatBeijingTime, londonSpread, quoteBasis } from "../quotes";
import { AlertsPanel } from "./AlertsPanel";
import { QuoteQuality, QuoteTime } from "./QuoteMetadata";
import type { WorkspaceTab } from "./WorkspaceNav";

interface OverviewViewProps {
  liveStatus: LiveStatusResponse | null;
  jdHoldings: JDHoldingsResponse | null;
  jdHoldingsMessage: string | null;
  holdingsConfirmedAt: string | null;
  liveFailed: boolean;
  quotesFailed: boolean;
  newsOverview: NewsOverviewResponse | null;
  products: ReferenceProduct[];
  quoteStatuses: LiveQuoteStatus[];
  alerts: AlertPayload[];
  alertsLoaded: boolean;
  alertsFailed: boolean;
  evaluation: Evaluation | null;
  focusAlertId: string | null;
  onAlertFocused: () => void;
  busyAction: string | null;
  onNavigate: (tab: WorkspaceTab) => void;
  onEvaluateLive: () => void;
  onConfirmAlert: (id: string) => void;
  onDeclineAlert: (id: string) => void;
  onExecuteAlert: (
    id: string,
    payload: { filled_grams: number; filled_price: number; fee?: number; bank_order_id?: string }
  ) => Promise<boolean>;
  onReconcileJd: () => void;
  onStepPaper: () => void;
  onRefresh: () => void;
  refreshing: boolean;
}

const CORE_QUOTES = [
  { code: "CZB-JCJ", name: "浙商积存金" },
  { code: "ICBC-JCJ", name: "工行积存金" },
  { code: "SGE-Au99.99", name: "上金所 Au99.99" },
];
const formatCny = (value: number) => value.toLocaleString("zh-CN", {
  style: "currency", currency: "CNY", minimumFractionDigits: 2, maximumFractionDigits: 2,
});

const RISK_REASONS: Record<string, string> = {
  KILL_SWITCH_ACTIVE: "急停已启用",
  HALT_RECONCILE_ACTIVE: "对账熔断保护",
  WEEKLY_LOSS_LIMIT_BREACHED: "触及周止损上限",
  DAILY_LOSS_LIMIT_BREACHED: "触及日止损上限",
  NO_NEW_RISK_BLOCKS_BUY: "当前禁止开新仓",
  REDUCE_ONLY_BLOCKS_BUY: "当前仅允许减仓",
  INVALID_TRADE_PRICE_OR_QUANTITY: "价格或克重无效",
  INVALID_TRADE_DIRECTION: "交易方向无效",
  INSUFFICIENT_CASH: "可用资金不足",
  INSUFFICIENT_GOLD_INVENTORY: "可用持仓不足",
  MAX_GOLD_EXPOSURE_EXCEEDED: "敞口上限保护",
};

function LatestEvaluation({ evaluation }: { evaluation: Evaluation | null }) {
  if (!evaluation) return <p className="text-sm text-slate-400">尚未运行评估。</p>;
  return (
    <div className="space-y-1 text-sm">
      <p className="break-words">
        {"error" in evaluation
          ? <span className="text-rose-200">评估失败 · {evaluation.error}</span>
          : evaluation.result.message}
      </p>
      <div className="flex flex-wrap gap-x-2 text-xs text-slate-400">
        <time dateTime={evaluation.at}>{formatBeijingTime(evaluation.at)} 北京时间</time>
        {"result" in evaluation && <span>{evaluation.result.product_code}</span>}
      </div>
      {"result" in evaluation && evaluation.result.risk_violations.length > 0 && (
        <p className="break-words text-xs text-rose-200">
          风控原因 · {evaluation.result.risk_violations.map((reason) => RISK_REASONS[reason] ?? reason).join("、")}
        </p>
      )}
    </div>
  );
}

export function OverviewView({
  liveStatus, jdHoldings, jdHoldingsMessage, holdingsConfirmedAt, liveFailed, quotesFailed, newsOverview,
  products, quoteStatuses, alerts, alertsLoaded, alertsFailed, evaluation, focusAlertId, onAlertFocused,
  busyAction, onNavigate, onEvaluateLive, onConfirmAlert, onDeclineAlert, onExecuteAlert,
  onReconcileJd, onStepPaper, onRefresh, refreshing,
}: OverviewViewProps) {
  const statusMap = new Map(quoteStatuses.map((status) => [status.product_code, status]));
  const productMap = new Map(products.map((product) => [product.code, product]));
  const domestic = statusMap.get("CMBC-JCJ");
  const london = statusMap.get("WG-XAUUSD");
  const domesticQuote = domestic?.latest;
  const londonQuote = london?.latest;
  const spread = londonSpread(domestic, london, quotesFailed);
  const snapshot = liveStatus?.snapshot;
  const equity = snapshot && (snapshot.gold_total_g === 0 || domesticQuote)
    ? snapshot.cash_available + snapshot.cash_frozen + snapshot.gold_total_g * (domesticQuote?.price ?? 0)
    : null;
  const reconciliation = liveStatus?.last_reconciliation;
  const recentNews = newsOverview?.news_events.slice(0, 3);

  return (
    <div className="flex flex-col gap-6 sm:gap-8">
      <section className="market-stage" aria-labelledby="market-title">
        <div className="market-orbit" aria-hidden="true"><span /></div>
        <div className="relative p-5 sm:p-9 lg:px-11 lg:pt-10">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h1 id="market-title" className="text-[1.65rem] font-semibold tracking-tight sm:text-3xl">黄金行情</h1>
            <button type="button" onClick={() => onNavigate("markets")} className="quiet-button min-h-9 px-2 text-sm">
              查看全部行情 <ArrowUpRight className="size-4" />
            </button>
          </div>
          <div className="mt-7 sm:mt-9">
            <p className="text-sm text-slate-300">民生银行积存金</p>
            <p className="market-price overview-price mt-1 flex items-baseline gap-2">
              <span>{domesticQuote ? formatCny(domesticQuote.price) : "—"}</span>
              <span className="text-sm font-medium tracking-normal text-slate-300 sm:text-base">元/克</span>
            </p>
            <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-slate-300 sm:text-sm">
              <span>{quoteBasis(domesticQuote?.price_side)}</span>
              <QuoteQuality quote={domesticQuote} />
              <QuoteTime quote={domesticQuote} showLabel />
              {domesticQuote && <span>北京时间</span>}
            </div>
          </div>
          <div className="mt-7 grid grid-cols-[minmax(0,1.1fr)_minmax(0,1fr)] gap-x-3 border-t border-white/12 pt-4 sm:mt-9 sm:gap-x-8 sm:pt-5 lg:max-w-3xl">
            <div>
              <p className="text-sm text-slate-300">伦敦金折合</p>
              <p className="mt-1 text-xl font-medium tracking-tight tabular-nums">
                <span>{london?.cny_per_g != null ? formatCny(london.cny_per_g) : "—"}</span>
                <span className="ml-1.5 text-xs font-normal text-slate-300">元/克</span>
              </p>
              <p className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1 text-xs text-slate-300">
                <QuoteQuality quote={londonQuote} />
                <QuoteTime quote={londonQuote} showLabel />
              </p>
            </div>
            <div className="min-w-0 border-l border-white/12 pl-3 sm:pl-6">
              <p className="text-sm text-slate-300">{spread.value === null ? "价差不可用" : spread.reason ? "参考价差" : "溢价"}</p>
              {spread.value !== null && (
                <p className="mt-1 text-xl font-medium tracking-tight tabular-nums">
                  {spread.value >= 0 ? "+" : ""}{spread.value.toFixed(2)}
                  <span className="ml-1.5 text-xs font-normal text-slate-300">元/克</span>
                </p>
              )}
              {spread.reason && <p className="mt-1.5 text-xs text-slate-300">{spread.reason}</p>}
            </div>
          </div>
        </div>
      </section>

      <ul aria-label="更多黄金报价" className="quote-tape order-1 grid divide-y divide-white/10 sm:order-none sm:grid-cols-3 sm:divide-x sm:divide-y-0">
        {CORE_QUOTES.map(({ code, name }) => {
          const quote = statusMap.get(code)?.latest;
          const product = productMap.get(code);
          return (
            <li key={code} className="min-w-0 px-4 py-3.5 sm:px-5">
              <div className="flex flex-wrap items-baseline justify-between gap-x-2 gap-y-1 text-sm">
                <span className="text-slate-200">{product?.name ?? name}</span>
                <span className="font-semibold tabular-nums">{quote ? formatCny(quote.price) : "—"}</span>
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-2 gap-y-1 text-xs text-slate-400">
                <span>{quoteBasis(quote?.price_side ?? product?.price_side)}{quote && " · 元/克"}</span>
                <QuoteQuality quote={quote} />
                <QuoteTime quote={quote} showLabel />
              </div>
            </li>
          );
        })}
      </ul>

      <div className="grid items-start gap-8 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
        <section className="surface p-5 sm:p-6" aria-labelledby="signals-title">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 id="signals-title" className="section-heading">信号与待办</h2>
              <p className="mt-1 text-sm text-slate-400">评估可能生成待人工处理的交易提醒。</p>
            </div>
            <button type="button" onClick={onEvaluateLive} disabled={busyAction !== null}
              className="primary-button">
              {busyAction === "live-step" ? "评估中…" : "评估实盘信号"} <ArrowRight className="size-4" />
            </button>
          </div>
          <div className="mt-4 border-t border-white/10 pt-4">
            <AlertsPanel alerts={alerts} alertsLoaded={alertsLoaded} alertsFailed={alertsFailed}
              focusAlertId={focusAlertId} onAlertFocused={onAlertFocused}
              busyAction={busyAction} onConfirm={onConfirmAlert} onDecline={onDeclineAlert} onExecute={onExecuteAlert} />
          </div>
          <div className="mt-4 border-t border-white/10 pt-4" role="status">
            <h3 className="mb-1.5 text-sm font-medium text-slate-200">最近一次评估</h3>
            <LatestEvaluation evaluation={evaluation} />
          </div>
          <details className="mt-4 border-t border-white/10 pt-2.5">
            <summary className="quiet-button min-h-9 cursor-pointer select-none px-1 text-sm">
              <MoreHorizontal className="size-4" /> 快捷操作
            </summary>
            <div className="mt-1 flex flex-wrap gap-2">
              <button type="button" onClick={onStepPaper} disabled={busyAction !== null} className="secondary-button">
                <Play className="size-4" />{busyAction === "paper-step" ? "模拟中…" : "运行一次本地模拟盘"}
              </button>
              <button type="button" onClick={onRefresh} disabled={refreshing} className="secondary-button">
                <Newspaper className="size-4" />{refreshing ? "读取中…" : "刷新资讯"}
              </button>
            </div>
          </details>
        </section>

        <div className="min-w-0 space-y-7">
          <section aria-labelledby="holdings-title">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="holdings-title" className="section-heading">持仓与账本</h2>
              <button type="button" onClick={() => onNavigate("portfolio")} className="quiet-button min-h-9 px-1 text-sm">
                查看账本 <ArrowUpRight className="size-4" />
              </button>
            </div>
            <dl className="mt-3 grid grid-cols-2 gap-3 border-y border-white/10 py-4">
              <div>
                <dt className="text-xs text-slate-400">京东实盘持仓</dt>
                <dd className="mt-1 text-lg font-medium tabular-nums">
                  {jdHoldings ? `${jdHoldings.totalGramAll.toFixed(4)} 克` : "—"}
                  {jdHoldingsMessage && <span className="mt-1 block text-xs text-amber-200">{jdHoldingsMessage}</span>}
                  {holdingsConfirmedAt && <time className="mt-1 block text-xs text-slate-400" dateTime={holdingsConfirmedAt}>{formatBeijingTime(holdingsConfirmedAt)}</time>}
                </dd>
              </div>
              <div className="border-l border-white/10 pl-3">
                <dt className="text-xs text-slate-400">影子账本权益</dt>
                <dd className="mt-1 text-lg font-medium tabular-nums">
                  {equity === null ? "—" : formatCny(equity)}
                  {snapshot && <span className="mt-1 block text-xs text-slate-400">持仓 {snapshot.gold_total_g.toFixed(4)} 克</span>}
                </dd>
              </div>
            </dl>
            {jdHoldings && (
              <p className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-400">
                <span>京东成本均价 {formatCny(jdHoldings.avgCostPrice)}/克</span>
                <span>累计收益 {formatCny(jdHoldings.holdingList.reduce((sum, item) => sum + (item.totalIncome ?? 0), 0))}</span>
              </p>
            )}
            <div className="mt-2 flex flex-wrap items-center justify-between gap-x-2 gap-y-1 text-xs text-slate-300">
              <p>
                {liveFailed && liveStatus && "上次已知 · "}
                <span>{reconciliation ? reconciliation.matched ? "账实一致" : "对账存在差异" : liveStatus ? "待对账" : "对账状态未知"}</span>
                {reconciliation && <> · <time dateTime={reconciliation.as_of}>{formatBeijingTime(reconciliation.as_of)}</time></>}
              </p>
              {liveStatus?.jd_logged_in && !liveFailed ? (
                <button type="button" onClick={onReconcileJd} disabled={busyAction !== null} className="quiet-button min-h-8 px-1">
                  <Scale className="size-3.5" />{busyAction === "reconcile-jd" ? "对账中…" : "京东持仓对账"}
                </button>
              ) : liveStatus && !liveFailed && <span>京东未授权</span>}
            </div>
          </section>
          <section aria-labelledby="news-title">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 id="news-title" className="section-heading">最新资讯</h2>
              <button type="button" onClick={() => onNavigate("news")} className="quiet-button min-h-9 px-1 text-sm">
                查看资讯 <ArrowUpRight className="size-4" />
              </button>
            </div>
            {recentNews?.length ? (
              <ul className="mt-2 divide-y divide-white/10 border-t border-white/10">
                {recentNews.map((event) => (
                  <li key={event.event_id} className="py-3">
                    <p className="text-sm font-medium text-slate-100">{event.title}</p>
                    <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-400">{event.summary}</p>
                  </li>
                ))}
              </ul>
            ) : <p className="mt-2 text-sm text-slate-400">{newsOverview ? "暂无资讯。" : "资讯待获取。"}</p>}
          </section>
        </div>
      </div>
    </div>
  );
}

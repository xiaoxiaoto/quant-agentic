import { RefreshCw, Search } from "lucide-react";
import { useState } from "react";
import type { LiveQuoteStatus, ReferenceProduct } from "../api/types";
import { comparableQuote, londonSpread, quoteBasis } from "../quotes";
import { QuoteQuality, QuoteTime } from "./QuoteMetadata";

interface MarketsViewProps {
  products: ReferenceProduct[];
  statuses: LiveQuoteStatus[];
  busyAction: string | null;
  loading: boolean;
  quotesFailed: boolean;
  onCollectAll: () => void;
}

const CATEGORIES = {
  all: "全部",
  bank: "银行积存金",
  international: "国际贵金属",
  sge: "上金所",
  index: "指数",
};
const BANK_SIDES = ["reference", "buy"] as const;

export function MarketsView({ products, statuses, busyAction, loading, quotesFailed, onCollectAll }: MarketsViewProps) {
  const [category, setCategory] = useState("all");
  const [keyword, setKeyword] = useState("");
  const statusMap = new Map(statuses.map((status) => [status.product_code, status]));
  const banks = products.filter((product) => product.category === "bank");
  const international = products.filter((product) => product.category === "international");
  const london = statusMap.get("WG-XAUUSD");
  const query = keyword.trim().toLowerCase();
  const filtered = products.filter((product) =>
    (category === "all" || product.category === category) &&
    (product.code.toLowerCase().includes(query) || product.name.toLowerCase().includes(query))
  );
  const empty = loading ? "正在读取行情…" : "暂无行情";

  return (
    <div className="space-y-6">
      {/* 01: 银行积存金价差比价 (BANK ACCUMULATION SPREAD) */}
      <section className="surface p-4 sm:p-6 relative overflow-hidden">
        <div className="relative mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] pb-4">
          <div className="flex items-center gap-2.5">
            <div>
              <h2 className="section-heading flex items-center gap-2">
                银行积存金比价
              </h2>
              <p className="text-[11px] text-slate-400 mt-0.5">
                元/克 · 北京时间
                {london?.cny_per_g != null && ` · 伦敦金折合基准 ¥${london.cny_per_g.toFixed(2)}/g`}
              </p>
            </div>
          </div>

          <button
            type="button"
            onClick={onCollectAll}
            disabled={busyAction !== null}
            className="secondary-button"
          >
            <RefreshCw className={`size-3.5 ${busyAction === "collect-all" ? "animate-spin" : ""}`} />
            {busyAction === "collect-all" ? "采集中…" : "采集全部行情"}
          </button>
        </div>

        {banks.length === 0 ? (
          <p className="empty-state px-4 py-8 text-center text-sm">{empty}</p>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-white/[0.06] bg-[#101722]" tabIndex={0} role="region" aria-label="银行积存金比价表">
            <table className="data-table w-full min-w-[700px] text-left text-sm">
              <thead className="bg-black/60 text-[11px] text-slate-400 border-b border-white/[0.06]">
                <tr>
                  <th scope="col" className="px-4 py-2.5">商业银行</th>
                  <th scope="col" className="px-4 py-2.5 text-right">参考报价 (CNY/g)</th>
                  <th scope="col" className="px-4 py-2.5">价格口径</th>
                  <th scope="col" className="px-4 py-2.5">与伦敦金价差</th>
                  <th scope="col" className="px-4 py-2.5">源报价时间</th>
                  <th scope="col" className="px-4 py-2.5 text-right">数据质量</th>
                </tr>
              </thead>
              {BANK_SIDES.map((side) => {
                const group = banks.filter((product) => product.price_side === side);
                if (group.length === 0) return null;
                const eligible = group.map((product) => statusMap.get(product.code)?.latest)
                  .filter(comparableQuote)
                  .filter((quote) => quote.unit === "CNY/g" && quote.price_side === side);
                const minimum = !quotesFailed && eligible.length >= 2
                  ? Math.min(...eligible.map((quote) => quote.price))
                  : null;
                return (
                  <tbody key={side} className="divide-y divide-white/[0.04] border-t border-white/[0.08]">
                    {group.map((product) => {
                      const status = statusMap.get(product.code);
                      const quote = status?.latest;
                      const lowest = minimum !== null && quote != null && quote.price === minimum && eligible.includes(quote);
                      const spread = londonSpread(status, london, quotesFailed);
                      return (
                        <tr key={product.code} className="hover:bg-white/4 transition-colors">
                          <th scope="row" className="whitespace-nowrap px-4 py-2.5 font-sans font-medium text-slate-200">
                            {product.name}
                            <span className="ml-2 text-[10px] text-slate-400">{product.code}</span>
                          </th>
                          <td className="whitespace-nowrap px-4 py-2.5 text-right text-sm font-semibold text-slate-100 tabular-nums">
                            {quote ? `¥${quote.price.toFixed(2)}` : "—"}
                            {lowest && (
                              <span className="ml-2 data-label border-emerald-500/40 bg-emerald-500/10 text-emerald-300">
                                组内最低
                              </span>
                            )}
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5 text-slate-400 font-sans text-xs">
                            {quoteBasis(side)}
                          </td>
                          <td className="px-4 py-2.5">
                            {spread.value === null ? (
                              <span className="text-slate-400">不可用</span>
                            ) : (
                              <span className="font-semibold tabular-nums text-slate-200">
                                {spread.value >= 0 ? "+" : ""}{spread.value.toFixed(2)}
                              </span>
                            )}
                            {spread.reason && <div className="text-[10px] text-slate-400">{spread.value !== null && "参考 · "}{spread.reason}</div>}
                          </td>
                          <td className="whitespace-nowrap px-4 py-2.5 text-slate-400 text-[11px]"><QuoteTime quote={quote} /></td>
                          <td className="whitespace-nowrap px-4 py-2.5 text-right text-xs">
                            <QuoteQuality quote={quote} />
                            {status?.last_error && <div className="text-rose-400 text-[10px]" title={status.last_error}>采集失败</div>}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                );
              })}
            </table>
          </div>
        )}
      </section>

      {/* 02: 国际贵金属 (GLOBAL PRECIOUS METALS) */}
      <section className="surface p-4 sm:p-6 relative overflow-hidden">
        <div className="relative mb-4 flex items-center justify-between border-b border-white/[0.06] pb-3">
          <div className="flex items-center gap-2">
            <div>
              <h2 className="section-heading">
                国际贵金属
              </h2>
            </div>
          </div>
        </div>

        {international.length === 0 && <p className="py-4 text-center text-xs text-slate-400">{empty}</p>}
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {international.map((product) => {
            const status = statusMap.get(product.code);
            const quote = status?.latest;
            return (
              <div key={product.code} className="metric-panel p-4 sm:p-5">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-200">{product.name}</span>
                  <span className="data-label border-white/10 bg-white/[0.03] text-slate-400">
                    {product.code}
                  </span>
                </div>
                <div className="mt-2 text-2xl font-bold text-slate-100">
                  {quote ? `$${quote.price.toFixed(2)}` : "—"}
                  <span className="ml-1 text-xs font-normal text-slate-400">USD/oz</span>
                </div>
                <div className="mt-1 text-sm font-medium text-slate-200">
                  {status?.cny_per_g != null ? `≈ ¥${status.cny_per_g.toFixed(2)}/g` : quote ? "缺少汇率" : "无折合"}
                  {status?.fx_rate_usd_cny != null && (
                    <span className="ml-2 text-[10px] font-normal text-slate-400">
                      FX: {status.fx_rate_usd_cny.toFixed(4)}
                    </span>
                  )}
                </div>
                <div className="mt-3 pt-2.5 border-t border-white/[0.04] flex items-center justify-between text-[11px] text-slate-400">
                  <QuoteQuality quote={quote} />
                  <QuoteTime quote={quote} showLabel />
                </div>
              </div>
            );
          })}
        </div>
      </section>

      {/* 03: 全品种行情 (MULTI-ASSET MATRIX) */}
      <section className="surface p-4 sm:p-6 relative overflow-hidden">
        <div className="relative mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] pb-3">
          <div className="flex items-center gap-2">
            <h2 className="section-heading">
              全品种行情
            </h2>
          </div>

          <div className="relative w-full sm:w-64">
            <Search className="pointer-events-none absolute top-1/2 left-3 size-3.5 -translate-y-1/2 text-slate-400" />
            <input
              type="search"
              value={keyword}
              onChange={(event) => setKeyword(event.target.value)}
              aria-label="搜索品种编码或名称"
              placeholder="搜索品种或代码"
              className="w-full field py-1.5 pr-3 pl-8 text-xs text-slate-100 outline-none placeholder:text-slate-600 focus:border-amber-400"
            />
          </div>
        </div>

        <div className="mb-4 flex flex-wrap gap-1.5" role="group" aria-label="行情分类">
          {Object.entries(CATEGORIES).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setCategory(key)}
              aria-pressed={category === key}
              className={`rounded-lg px-3 py-1 text-xs transition ${
                category === key
                  ? "bg-amber-500/20 font-semibold text-amber-200 border border-amber-500/40"
                  : "bg-black/40 text-slate-400 hover:text-slate-200 border border-white/[0.06]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {filtered.length === 0 ? (
          <p className="py-8 text-center text-xs text-slate-400" role="status">
            {products.length ? "没有匹配的品种" : empty}
          </p>
        ) : (
          <div className="max-h-96 overflow-auto rounded-xl border border-white/[0.06] bg-[#101722]" tabIndex={0} role="region" aria-label="全品种行情表">
            <table className="data-table w-full min-w-[780px] text-left text-sm">
              <thead className="sticky top-0 bg-black/80 backdrop-blur-md text-[11px] text-slate-400 border-b border-white/[0.06]">
                <tr>
                  {["品种标的", "原始报价", "价格口径", "折合元/克", "源时间", "质量", "采集流水 / 状态"].map((label) => (
                    <th key={label} scope="col" className="px-4 py-2.5">{label}</th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-white/[0.04]">
                {filtered.map((product) => {
                  const status = statusMap.get(product.code);
                  const quote = status?.latest;
                  return (
                    <tr key={product.code} className="hover:bg-white/4 transition-colors">
                      <th scope="row" className="whitespace-nowrap px-4 py-2.5 font-sans font-medium text-slate-200">
                        {product.name}
                        <span className="ml-2 text-[10px] text-slate-400">{product.code}</span>
                      </th>
                      <td className="whitespace-nowrap px-4 py-2.5 font-semibold text-slate-200">
                        {quote ? `${quote.price.toFixed(2)} ${quote.unit}` : "—"}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2.5 text-slate-400 font-sans text-xs">
                        {quoteBasis(quote?.price_side ?? product.price_side)}
                      </td>
                      <td className="px-4 py-2.5 text-amber-300 font-semibold">
                        {status?.cny_per_g == null ? "—" : `¥${status.cny_per_g.toFixed(2)}`}
                      </td>
                      <td className="whitespace-nowrap px-4 py-2.5 text-slate-400 text-[11px]">
                        <QuoteTime quote={quote} />
                      </td>
                      <td className="whitespace-nowrap px-4 py-2.5 text-xs">
                        <QuoteQuality quote={quote} />
                      </td>
                      <td className="px-4 py-2.5 text-xs text-slate-400">
                        {status ? `${status.records_saved} / ${status.failures}` : "—"}
                        {status?.last_error && <div className="break-words text-rose-400 text-[10px]">{status.last_error}</div>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}

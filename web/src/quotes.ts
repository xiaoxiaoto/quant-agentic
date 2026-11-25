import type { LiveQuoteStatus, ReferenceQuote } from "./api/types";

const beijingDateTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

export function formatBeijingTime(value: string) {
  return beijingDateTime.format(new Date(value));
}

export function quoteBasis(side?: ReferenceQuote["price_side"]) {
  return side === "buy" ? "买入参考价" : "综合参考价";
}

export function quoteQuality(quote?: ReferenceQuote | null) {
  if (!quote) return { label: "无报价", tone: "text-slate-400" };
  if (quote.quality_state === "ANOMALY") return { label: "报价异常", tone: "text-rose-300" };
  if (!quote.quote_at) return { label: "无源时间", tone: "text-amber-200" };

  switch (quote.quality_state) {
    case "NORMAL":
      return { label: "正常", tone: "text-emerald-300" };
    case "STALE":
      return { label: "已过期", tone: "text-amber-200" };
    case "GAP":
      return { label: "数据缺口", tone: "text-amber-200" };
    default:
      return { label: "质量未知", tone: "text-slate-400" };
  }
}

export function comparableQuote(quote?: ReferenceQuote | null): quote is ReferenceQuote {
  return !!quote?.quote_at && quote.quality_state === "NORMAL" && Number.isFinite(quote.price) && quote.price > 0;
}

export function londonSpread(local?: LiveQuoteStatus, london?: LiveQuoteStatus, refreshFailed = false) {
  const base = local?.latest;
  const benchmark = london?.latest;
  if (!base || !london || !benchmark || !Number.isFinite(base.price) || base.price <= 0 ||
      !Number.isFinite(benchmark.price) || benchmark.price <= 0) {
    return { value: null, reason: "缺少报价" };
  }
  if (base.quality_state === "ANOMALY" || benchmark.quality_state === "ANOMALY") {
    return { value: null, reason: "报价异常" };
  }
  if (london.cny_per_g == null || !Number.isFinite(london.cny_per_g) || london.cny_per_g <= 0) {
    return { value: null, reason: "缺少汇率折合价" };
  }
  const reasons = [
    refreshFailed && "行情更新失败",
    !comparableQuote(base) && `银行报价${quoteQuality(base).label}`,
    !comparableQuote(benchmark) && `伦敦金${quoteQuality(benchmark).label}`,
  ].filter(Boolean);
  return { value: base.price - london.cny_per_g, reason: reasons.join("、") };
}

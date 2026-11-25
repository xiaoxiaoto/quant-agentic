import type { ReferenceQuote } from "../api/types";
import { formatBeijingTime, quoteQuality } from "../quotes";

export function QuoteQuality({ quote }: { quote?: ReferenceQuote | null }) {
  const { label, tone } = quoteQuality(quote);
  return <span className={`font-medium ${tone}`}>{label}</span>;
}

export function QuoteTime({
  quote, showLabel = false,
}: { quote?: ReferenceQuote | null; showLabel?: boolean }) {
  if (!quote) return <span>—</span>;
  return quote.quote_at ? (
    <span>{showLabel && "源时间 "}<time dateTime={quote.quote_at}>{formatBeijingTime(quote.quote_at)}</time></span>
  ) : (
    <span>接收 <time dateTime={quote.received_at}>{formatBeijingTime(quote.received_at)}</time></span>
  );
}

import { describe, expect, it } from "vitest";
import type { LiveQuoteStatus, ReferenceQuote } from "../api/types";
import { comparableQuote, formatBeijingTime, londonSpread, quoteBasis, quoteQuality } from "../quotes";

function makeQuote(overrides: Partial<ReferenceQuote> = {}): ReferenceQuote {
  return {
    quote_id: "q_1",
    source_id: "JD",
    product_code: "CMBC-JCJ",
    price: 620.5,
    unit: "CNY/g",
    price_side: "reference",
    quote_at: "2026-05-10T02:00:00Z",
    received_at: "2026-05-10T02:00:01Z",
    available_at: "2026-05-10T02:00:01Z",
    is_tradable: false,
    quality_state: "NORMAL",
    ...overrides,
  };
}

function makeStatus(quote: ReferenceQuote | null, cnyPerG: number | null = null): LiveQuoteStatus {
  return {
    product_code: quote?.product_code ?? "CMBC-JCJ",
    enabled: true,
    running: true,
    interval_seconds: 10,
    records_saved: 1,
    failures: 0,
    last_attempt_at: "2026-05-10T02:00:01Z",
    last_success_at: "2026-05-10T02:00:01Z",
    last_error: null,
    cny_per_g: cnyPerG,
    latest: quote,
  };
}

describe("quotes helpers", () => {
  it("按北京时间格式化 UTC 时间", () => {
    expect(formatBeijingTime("2026-05-10T02:05:09Z")).toContain("10:05:09");
  });

  it("正确识别价格口径与质量标签", () => {
    expect(quoteBasis("buy")).toBe("买入参考价");
    expect(quoteBasis("reference")).toBe("综合参考价");
    expect(quoteQuality(null).label).toBe("无报价");
    expect(quoteQuality(makeQuote({ quality_state: "ANOMALY" })).label).toBe("报价异常");
    expect(quoteQuality(makeQuote({ quote_at: null, quality_state: "GAP" })).label).toBe("无源时间");
    expect(comparableQuote(makeQuote())).toBe(true);
    expect(comparableQuote(makeQuote({ quality_state: "STALE" }))).toBe(false);
  });

  it("计算伦敦金溢价并拦截异常或缺失汇率", () => {
    const local = makeStatus(makeQuote({ price: 625.0 }));
    const london = makeStatus(makeQuote({ product_code: "INTL-XAU", price: 2700, unit: "USD/oz" }), 620.0);
    expect(londonSpread(local, london)).toEqual({ value: 5.0, reason: "" });

    const anomalyLocal = makeStatus(makeQuote({ price: 9999, quality_state: "ANOMALY" }));
    expect(londonSpread(anomalyLocal, london)).toEqual({ value: null, reason: "报价异常" });

    const noFxLondon = makeStatus(makeQuote({ product_code: "INTL-XAU", price: 2700, unit: "USD/oz" }), null);
    expect(londonSpread(local, noFxLondon)).toEqual({ value: null, reason: "缺少汇率折合价" });
  });
});

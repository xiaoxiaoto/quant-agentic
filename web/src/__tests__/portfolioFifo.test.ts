import { describe, expect, it } from "vitest";
import type { OrderEvent } from "../api/types";
import { computeFifoOrders } from "../components/PortfolioView";

function makeOrder(overrides: Partial<OrderEvent>): OrderEvent {
  return {
    client_order_id: "ord_1",
    bank_order_id: null,
    order_type: "MARKET",
    direction: "BUY",
    requested_grams: 10,
    filled_grams: 10,
    execution_price: 600,
    fee: 0,
    state: "FILLED",
    bank_event_at: "2026-05-01T00:00:00Z",
    received_at: "2026-05-01T00:00:00Z",
    ...overrides,
  };
}

describe("computeFifoOrders", () => {
  it("按先进先出匹配买卖批次并核算剩余浮盈", () => {
    const orders: OrderEvent[] = [
      makeOrder({ client_order_id: "b1", direction: "BUY", filled_grams: 10, execution_price: 600 }),
      makeOrder({ client_order_id: "b2", direction: "BUY", filled_grams: 10, execution_price: 610 }),
      makeOrder({ client_order_id: "s1", direction: "SELL", filled_grams: 15, execution_price: 630 }),
    ];

    const enriched = computeFifoOrders(orders, 620);
    expect(enriched[0].lotStatus).toBe("CLOSED");
    expect(enriched[0].remainingGrams).toBe(0);
    expect(enriched[0].lotPnl).toBeNull();

    expect(enriched[1].lotStatus).toBe("PARTIAL");
    expect(enriched[1].remainingGrams).toBe(5);
    expect(enriched[1].lotPnl).toBeCloseTo(50, 6);

    expect(enriched[2].lotStatus).toBe("SELL");
  });

  it("忽略未成交订单（REJECTED / CANCELED），不污染持仓批次", () => {
    const orders: OrderEvent[] = [
      makeOrder({ client_order_id: "b1", direction: "BUY", filled_grams: 10, execution_price: 600, state: "FILLED" }),
      makeOrder({ client_order_id: "s_rej", direction: "SELL", filled_grams: 10, execution_price: 620, state: "REJECTED" }),
      makeOrder({ client_order_id: "b_can", direction: "BUY", filled_grams: 5, execution_price: 605, state: "CANCELED" }),
    ];

    const enriched = computeFifoOrders(orders, 610);
    expect(enriched[0].lotStatus).toBe("HOLDING");
    expect(enriched[0].remainingGrams).toBe(10);
    expect(enriched[0].lotPnl).toBeCloseTo(100, 6);

    expect(enriched[1].lotStatus).toBe("UNFILLED");
    expect(enriched[1].remainingGrams).toBe(0);
    expect(enriched[2].lotStatus).toBe("UNFILLED");
    expect(enriched[2].remainingGrams).toBe(0);
  });
});

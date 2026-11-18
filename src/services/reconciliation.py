"""对账服务模块。

对比本地影子账本与银行官方账单/持仓流水。
任何对账不一致均将触发对账差异报告，并驱动风控进入 HALT_RECONCILE 状态。
"""

import uuid
from datetime import UTC, datetime

from src.types.domain.account import (
    AccountSnapshot,
    BankStatementItem,
    OrderEvent,
    ReconciliationDiscrepancy,
    ReconciliationReport,
)
from src.types.enums.trading import OrderState, RiskState


class ReconciliationService:
    """对账与一致性校验核心服务。

    支持账户资产快照比对与单笔交易流水逐笔核验。
    """

    def __init__(
        self,
        cash_tolerance: float = 0.01,
        gold_tolerance: float = 0.0001,
        cost_tolerance: float = 0.01,
    ) -> None:
        self._cash_tolerance = cash_tolerance
        self._gold_tolerance = gold_tolerance
        self._cost_tolerance = cost_tolerance

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _compare_numeric(
        self,
        field_name: str,
        local_val: float,
        bank_val: float,
        tolerance: float,
    ) -> ReconciliationDiscrepancy:
        diff = abs(local_val - bank_val)
        breached = diff > tolerance
        return ReconciliationDiscrepancy(
            field_name=field_name,
            local_value=local_val,
            bank_value=bank_val,
            difference=round(diff, 6),
            tolerance=tolerance,
            is_breached=breached,
        )

    def reconcile_snapshot(
        self,
        local_snapshot: AccountSnapshot,
        bank_cash: float,
        bank_gold_g: float,
        bank_cost_basis: float | None = None,
        as_of: datetime | None = None,
    ) -> tuple[ReconciliationReport, RiskState]:
        """核对影子账本快照与银行官方账户资产。

        若存在超出容差的差异，返回 HALT_RECONCILE 状态。
        """
        report_id = f"recon_snap_{uuid.uuid4().hex[:8]}"
        timestamp = as_of or self._now()
        discrepancies: list[ReconciliationDiscrepancy] = []

        # 校验现金
        cash_item = self._compare_numeric(
            field_name="cash_available",
            local_val=local_snapshot.cash_available,
            bank_val=bank_cash,
            tolerance=self._cash_tolerance,
        )
        if cash_item.is_breached:
            discrepancies.append(cash_item)

        # 校验黄金持仓克重
        gold_item = self._compare_numeric(
            field_name="gold_total_g",
            local_val=local_snapshot.gold_total_g,
            bank_val=bank_gold_g,
            tolerance=self._gold_tolerance,
        )
        if gold_item.is_breached:
            discrepancies.append(gold_item)

        # 校验单位成本基准（若银行提供）
        if bank_cost_basis is not None and local_snapshot.cost_basis_per_g is not None:
            cost_item = self._compare_numeric(
                field_name="cost_basis_per_g",
                local_val=local_snapshot.cost_basis_per_g,
                bank_val=bank_cost_basis,
                tolerance=self._cost_tolerance,
            )
            if cost_item.is_breached:
                discrepancies.append(cost_item)

        matched = len(discrepancies) == 0
        recommended_risk = RiskState.NORMAL if matched else RiskState.HALT_RECONCILE
        summary = (
            "资产快照对账完全一致" if matched else f"资产快照发现 {len(discrepancies)} 项差异，触发 HALT_RECONCILE"
        )

        report = ReconciliationReport(
            report_id=report_id,
            as_of=timestamp,
            matched=matched,
            discrepancies=discrepancies,
            summary=summary,
        )
        return report, recommended_risk

    def reconcile_orders(
        self,
        local_orders: list[OrderEvent],
        bank_statements: list[BankStatementItem],
        as_of: datetime | None = None,
    ) -> tuple[ReconciliationReport, RiskState]:
        """逐笔核对本地订单流水与银行官方交易流水。"""
        report_id = f"recon_orders_{uuid.uuid4().hex[:8]}"
        timestamp = as_of or self._now()
        discrepancies: list[ReconciliationDiscrepancy] = []

        seen_bank_ids: set[str] = set()
        seen_client_ids: set[str] = set()
        for item in bank_statements:
            duplicate_bank = item.bank_order_id in seen_bank_ids
            duplicate_client = item.client_order_id is not None and item.client_order_id in seen_client_ids
            if duplicate_bank or duplicate_client:
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        field_name=f"duplicate_bank_order_{item.bank_order_id}",
                        local_value="UNIQUE",
                        bank_value=item.bank_order_id,
                        difference=1.0,
                        tolerance=0.0,
                        is_breached=True,
                    )
                )
            seen_bank_ids.add(item.bank_order_id)
            if item.client_order_id is not None:
                seen_client_ids.add(item.client_order_id)
        bank_dict_by_id = {item.bank_order_id: item for item in bank_statements}
        bank_dict_by_client = {item.client_order_id: item for item in bank_statements if item.client_order_id}

        unmatched_local: list[str] = []
        matched_bank_ids: set[str] = set()

        for local_order in local_orders:
            # 优先根据银行订单号比对，其次根据本地全局幂等订单号比对
            matched_bank_item: BankStatementItem | None = None
            if local_order.bank_order_id and local_order.bank_order_id in bank_dict_by_id:
                matched_bank_item = bank_dict_by_id[local_order.bank_order_id]
            elif local_order.client_order_id in bank_dict_by_client:
                matched_bank_item = bank_dict_by_client[local_order.client_order_id]

            if not matched_bank_item:
                unmatched_local.append(local_order.client_order_id)
                continue

            if matched_bank_item.bank_order_id in matched_bank_ids:
                unmatched_local.append(local_order.client_order_id)
                continue
            matched_bank_ids.add(matched_bank_item.bank_order_id)

            fee_item = self._compare_numeric(
                f"order_{local_order.client_order_id}_fee",
                local_order.fee or 0.0,
                matched_bank_item.fee,
                self._cash_tolerance,
            )
            if fee_item.is_breached:
                discrepancies.append(fee_item)
            if local_order.state != OrderState.FILLED or matched_bank_item.status != "SUCCESS":
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        field_name=f"order_{local_order.client_order_id}_status",
                        local_value=local_order.state.value,
                        bank_value=matched_bank_item.status,
                        difference=1.0,
                        tolerance=0.0,
                        is_breached=True,
                    )
                )

            # 核验成交方向
            if local_order.direction != matched_bank_item.direction:
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        field_name=f"order_{local_order.client_order_id}_direction",
                        local_value=local_order.direction.value,
                        bank_value=matched_bank_item.direction.value,
                        difference=1.0,
                        tolerance=0.0,
                        is_breached=True,
                    )
                )

            # 核验成交克重
            loc_grams = local_order.filled_grams or 0.0
            gram_diff = abs(loc_grams - matched_bank_item.grams)
            if gram_diff > self._gold_tolerance:
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        field_name=f"order_{local_order.client_order_id}_grams",
                        local_value=loc_grams,
                        bank_value=matched_bank_item.grams,
                        difference=gram_diff,
                        tolerance=self._gold_tolerance,
                        is_breached=True,
                    )
                )

            # 核验成交价格
            loc_price = local_order.execution_price or 0.0
            price_diff = abs(loc_price - matched_bank_item.price)
            if price_diff > self._cost_tolerance:
                discrepancies.append(
                    ReconciliationDiscrepancy(
                        field_name=f"order_{local_order.client_order_id}_price",
                        local_value=loc_price,
                        bank_value=matched_bank_item.price,
                        difference=price_diff,
                        tolerance=self._cost_tolerance,
                        is_breached=True,
                    )
                )

        unmatched_bank = [b_id for b_id in bank_dict_by_id if b_id not in matched_bank_ids]

        matched = len(discrepancies) == 0 and len(unmatched_local) == 0 and len(unmatched_bank) == 0
        recommended_risk = RiskState.NORMAL if matched else RiskState.HALT_RECONCILE

        summary = (
            "所有订单流水核对完全一致"
            if matched
            else (
                f"流水存在差异：{len(discrepancies)} 项明细不符，"
                f"本地未对账 {len(unmatched_local)} 笔，银行未匹配 {len(unmatched_bank)} 笔"
            )
        )

        report = ReconciliationReport(
            report_id=report_id,
            as_of=timestamp,
            matched=matched,
            discrepancies=discrepancies,
            unmatched_local_orders=unmatched_local,
            unmatched_bank_orders=unmatched_bank,
            summary=summary,
        )
        return report, recommended_risk

"""历史回测与经济可行性分析 RESTful 接口路由。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from src.contracts.domains.benchmark import BacktestSummary
from src.contracts.domains.product import FeeRule, ProductConfig
from src.contracts.schemas import (
    BacktestRunRequest,
    FeasibilityReportResponse,
    FeasibilityRequest,
    FeasibilityRowResponse,
)
from src.runtime.dependencies import RuntimeDependencies
from src.services.decision.feasibility import FeasibilityAnalyzer

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("/run", response_model=BacktestSummary)
def run_backtest(
    payload: BacktestRunRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> BacktestSummary:
    """运行历史 PIT 回测并与被动基准策略进行横向对比。"""
    settings = runtime.settings
    try:
        quotes_file = runtime.resolve_data_path(payload.quotes_file) if payload.quotes_file is not None else None
        return runtime.backtest.run(
            product_code=payload.product_code or settings.default_product_code,
            start_time=payload.start_time,
            end_time=payload.end_time,
            quotes_file=quotes_file,
            initial_cash=payload.initial_cash or settings.default_initial_cash,
            trade_grams_per_order=payload.trade_grams or settings.default_trade_grams,
            dca_step_interval=payload.dca_step_interval,
        )
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/feasibility", response_model=FeasibilityReportResponse)
def run_feasibility(
    payload: FeasibilityRequest,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> FeasibilityReportResponse:
    """按当前配置费率与指定波动率/点差情景运行经济可行性测算。"""
    fee_rule = FeeRule(
        buy_fee_rate=runtime.settings.buy_fee_rate,
        sell_fee_rate=runtime.settings.sell_fee_rate,
    )
    analyzer = FeasibilityAnalyzer(product_config=ProductConfig(fee_rule=fee_rule))
    report = analyzer.analyze(
        annual_volatility=payload.annual_volatility,
        spread_scenarios=tuple(payload.spread_scenarios),
    )
    return FeasibilityReportResponse(
        annual_volatility=report.annual_volatility,
        trading_hours_per_year=report.trading_hours_per_year,
        buy_fee_rate=report.buy_fee_rate,
        sell_fee_rate=report.sell_fee_rate,
        spread_scenarios=report.spread_scenarios,
        has_viable_period=report.has_viable_period(),
        rows=[
            FeasibilityRowResponse(
                holding_period=row.holding_period,
                trading_hours=row.trading_hours,
                sigma_h=row.sigma_h,
                typical_move_m=row.typical_move_m,
                spread_pct=row.spread_pct,
                total_cost_pct=row.total_cost_pct,
                required_accuracy=row.required_accuracy,
                assessment=row.assessment,
            )
            for row in report.rows
        ],
    )

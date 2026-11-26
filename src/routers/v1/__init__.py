"""V1 版本 API 路由汇总模块。"""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from src.routers.v1.alerts import router as alerts_router
from src.routers.v1.backtest import router as backtest_router
from src.routers.v1.decisions import router as decisions_router
from src.routers.v1.jd import router as jd_router
from src.routers.v1.ledger import router as ledger_router
from src.routers.v1.live import router as live_router
from src.routers.v1.models import router as models_router
from src.routers.v1.paper import router as paper_router
from src.routers.v1.qlib import router as qlib_router
from src.routers.v1.quotes import router as quotes_router
from src.routers.v1.reconciliation import router as reconciliation_router
from src.runtime.dependencies import RuntimeDependencies


def _verify_api_access(
    request: Request,
    runtime: Annotated[RuntimeDependencies, Depends(RuntimeDependencies.from_request)],
) -> None:
    """统一校验 V1 API 访问令牌与跨站写入防护。"""
    RuntimeDependencies.verify_api_access(request, runtime)


api_v1_router = APIRouter(prefix="/api/v1", dependencies=[Depends(_verify_api_access)])

api_v1_router.include_router(quotes_router)
api_v1_router.include_router(decisions_router)
api_v1_router.include_router(alerts_router)
api_v1_router.include_router(ledger_router)
api_v1_router.include_router(reconciliation_router)
api_v1_router.include_router(models_router)
api_v1_router.include_router(backtest_router)
api_v1_router.include_router(paper_router)
api_v1_router.include_router(live_router)
api_v1_router.include_router(jd_router)
api_v1_router.include_router(qlib_router)

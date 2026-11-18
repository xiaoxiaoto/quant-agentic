"""V1 版本 API 路由汇总模块。"""

from fastapi import APIRouter

from src.routers.v1.alerts import router as alerts_router
from src.routers.v1.decisions import router as decisions_router
from src.routers.v1.ledger import router as ledger_router
from src.routers.v1.models import router as models_router
from src.routers.v1.quotes import router as quotes_router
from src.routers.v1.reconciliation import router as reconciliation_router

api_v1_router = APIRouter(prefix="/api/v1")

api_v1_router.include_router(quotes_router)
api_v1_router.include_router(decisions_router)
api_v1_router.include_router(alerts_router)
api_v1_router.include_router(ledger_router)
api_v1_router.include_router(reconciliation_router)
api_v1_router.include_router(models_router)

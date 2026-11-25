"""健康检查路由。"""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    """返回服务存活状态。"""
    return {"status": "ok"}

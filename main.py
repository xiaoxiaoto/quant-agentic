from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from src.configs.settings import Settings
from src.routers.health import router as health_router
from src.routers.v1 import api_v1_router
from src.runtime.dependencies import RuntimeDependencies

settings = Settings()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """关闭应用持有的新闻模型异步连接。"""
    try:
        yield
    finally:
        if application.state.runtime.news_agent is not None:
            await application.state.runtime.news_agent.close()


app = FastAPI(title=settings.name, lifespan=lifespan)
app.state.runtime = RuntimeDependencies(settings)
app.exception_handler(RequestValidationError)(RuntimeDependencies.validation_error)

app.include_router(health_router)
app.include_router(api_v1_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=settings.debug)

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from src.configs.settings import Settings
from src.routers.health import router as health_router
from src.routers.v1 import api_v1_router
from src.routers.web_ui import router as web_ui_router
from src.runtime.cli import QuantCLI
from src.runtime.dependencies import RuntimeDependencies

settings = Settings()


@asynccontextmanager
async def lifespan(application: FastAPI):
    """随应用启停京东采集和授权检查，并释放异步连接。"""
    owned = application.state.runtime is None
    if owned:
        application.state.runtime = RuntimeDependencies(settings)
    runtime: RuntimeDependencies = application.state.runtime
    try:
        async with runtime.background_collection():
            yield
    finally:
        try:
            await runtime.close()
        finally:
            if owned:
                application.state.runtime = None


app = FastAPI(title=settings.name, lifespan=lifespan)
app.state.runtime = None
app.exception_handler(RequestValidationError)(RuntimeDependencies.validation_error)

app.include_router(health_router)
app.include_router(api_v1_router)
app.include_router(web_ui_router)

if __name__ == "__main__":
    QuantCLI.main()

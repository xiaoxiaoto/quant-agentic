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
    """随应用启停行情轮询和授权检查，并释放异步连接。"""
    owned = application.state.runtime is None
    if owned:
        application.state.runtime = RuntimeDependencies(settings)
    runtime: RuntimeDependencies = application.state.runtime
    try:
        await runtime.jd_gold.start()
        await runtime.live_quotes.start()
        await runtime.news_sync.start()
        yield
    finally:
        try:
            try:
                await runtime.news_sync.stop()
            finally:
                try:
                    await runtime.live_quotes.stop()
                finally:
                    await runtime.jd_gold.stop()
        finally:
            await runtime.close()
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

import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError

from src.config.settings import Settings
from src.routers.health import router as health_router
from src.routers.v1 import api_v1_router
from src.runtime.dependencies import RuntimeDependencies

settings = Settings()

app = FastAPI(title=settings.name)
app.state.runtime = RuntimeDependencies(settings)
app.exception_handler(RequestValidationError)(RuntimeDependencies.validation_error)

app.include_router(health_router)
app.include_router(api_v1_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=settings.debug)

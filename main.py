import uvicorn
from fastapi import FastAPI

from src.config.settings import Settings
from src.routers.health import router as health_router

settings = Settings()

app = FastAPI(title=settings.name)

app.include_router(health_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host=settings.host, port=settings.port, reload=settings.debug)

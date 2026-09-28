from fastapi import FastAPI

from jyj.api.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(title="jyj")
    app.include_router(health_router)
    return app


app = create_app()

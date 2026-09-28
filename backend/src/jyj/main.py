from fastapi import FastAPI

from jyj.api.auth import router as auth_router
from jyj.api.csrf import CSRFHeaderMiddleware
from jyj.api.health import router as health_router
from jyj.api.problems import install_problem_handlers
from jyj.services.rate_limit import LoginRateLimiter

API_PREFIX = "/api/v1"


def create_app() -> FastAPI:
    app = FastAPI(title="jyj")
    app.state.login_limiter = LoginRateLimiter()
    install_problem_handlers(app)
    app.add_middleware(CSRFHeaderMiddleware, prefix=API_PREFIX)
    app.include_router(health_router)
    app.include_router(auth_router, prefix=API_PREFIX)
    return app


app = create_app()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from jyj.api.auth import router as auth_router
from jyj.api.csrf import CSRFHeaderMiddleware
from jyj.api.health import router as health_router
from jyj.api.ingredients import router as ingredients_router
from jyj.api.meal_slots import router as meal_slots_router
from jyj.api.planned_meals import router as planned_meals_router
from jyj.api.problems import install_problem_handlers, problem
from jyj.api.recipes import router as recipes_router
from jyj.api.stock import router as stock_router
from jyj.api.units import router as units_router
from jyj.chat.router import router as chat_router
from jyj.services.errors import ServiceError
from jyj.services.rate_limit import LoginRateLimiter

API_PREFIX = "/api/v1"


async def _service_error(_: Request, exc: ServiceError) -> JSONResponse:
    return problem(exc.status, exc.detail, **exc.extensions)


def create_app() -> FastAPI:
    app = FastAPI(title="jyj")
    app.state.login_limiter = LoginRateLimiter()
    install_problem_handlers(app)
    app.add_exception_handler(ServiceError, _service_error)  # type: ignore[arg-type]
    app.add_middleware(CSRFHeaderMiddleware, prefix=API_PREFIX)
    app.include_router(health_router)
    app.include_router(auth_router, prefix=API_PREFIX)
    app.include_router(units_router, prefix=API_PREFIX)
    app.include_router(ingredients_router, prefix=API_PREFIX)
    app.include_router(stock_router, prefix=API_PREFIX)
    app.include_router(recipes_router, prefix=API_PREFIX)
    app.include_router(meal_slots_router, prefix=API_PREFIX)
    app.include_router(planned_meals_router, prefix=API_PREFIX)
    app.include_router(chat_router, prefix=API_PREFIX)
    return app


app = create_app()

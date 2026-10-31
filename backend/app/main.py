import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.responses import Response

from app.api.routes import auth, google, integrations, tenants
from app.api.routes.health import router
from app.core.config import get_settings
from app.core.logging import RedactOAuthQuery
from app.db.session import assert_runtime_role, get_engine


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    assert_runtime_role(get_engine())
    yield
    get_engine().dispose()


def create_app() -> FastAPI:
    if not any(
        isinstance(item, RedactOAuthQuery) for item in logging.getLogger("uvicorn.access").filters
    ):
        logging.getLogger("uvicorn.access").addFilter(RedactOAuthQuery())
    application = FastAPI(title="ReSlot AI", version="0.4.1", lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=get_settings().cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @application.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @application.exception_handler(IntegrityError)
    async def integrity_error(request: Request, exc: IntegrityError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={"detail": "Record conflicts with existing integration configuration"},
        )

    application.include_router(router)
    application.include_router(auth.router)
    application.include_router(tenants.router)
    application.include_router(google.router)
    application.include_router(integrations.router)
    return application


app = create_app()

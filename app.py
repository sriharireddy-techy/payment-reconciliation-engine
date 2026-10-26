"""FastAPI entry point: routers, error handling and the single-page dashboard.

Run with:  uvicorn app:app --reload
Dashboard: http://127.0.0.1:8000/      API docs: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import PROJECT_ROOT
from database.connection import close_pool
from routes import (
    accounts,
    audit,
    concurrency,
    dashboard,
    failure_simulation,
    health,
    payments,
    reconciliation,
    transactions,
)
from services.errors import AppError

log = logging.getLogger("payment_reconciliation")

DESCRIPTION = """
Academic DBMS prototype: concurrent payment processing, a double-entry ledger,
idempotent requests, failure/rollback demonstration and reconciliation against
**simulated** gateway records, on Oracle (FreeSQL).

No real money moves and no real bank or gateway is contacted.
"""


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    close_pool()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Payment Transaction & Reconciliation Database",
        version="1.0.0",
        description=DESCRIPTION,
        lifespan=lifespan,
    )

    @app.exception_handler(AppError)
    async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.to_dict()})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"field": ".".join(str(part) for part in err.get("loc", ()) if part != "body"), "message": err.get("msg")}
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "VALIDATION_ERROR", "message": "The request is not valid.", "details": problems}},
        )

    @app.exception_handler(Exception)
    async def unexpected_handler(_: Request, exc: Exception) -> JSONResponse:
        reference = uuid.uuid4().hex[:12]
        log.exception("Unhandled error (reference %s)", reference, exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": f"Unexpected server error. See the server log for reference {reference}.",
                }
            },
        )

    for module in (health, dashboard, accounts, payments, transactions, concurrency,
                   failure_simulation, reconciliation, audit):
        app.include_router(module.router)

    app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(PROJECT_ROOT / "templates" / "index.html")

    return app


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = create_app()

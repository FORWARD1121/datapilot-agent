"""FastAPI application factory; no business state is initialized at import time."""

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, FastAPI, File, Form, Request, Security, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from sqlalchemy import text
from starlette.exceptions import HTTPException

from app import __version__
from app.coze import create_coze_router
from app.core import AppError, Settings
from app.llm import Provider
from app.middleware import RequestGuard, authorized
from app.reports import Report
from app.schemas import AnalysisRequest, Contract
from app.service import DataPilot


class DatasetView(Contract):
    dataset_id: UUID
    filename: str
    profile: dict
    created_at: str


class ProfileView(Contract):
    dataset_id: UUID
    profile: dict


class TaskView(Contract):
    analysis_id: UUID
    dataset_id: UUID
    query: str
    status: Literal["running", "completed", "failed"]
    plan: dict
    error_code: str | None
    created_at: str
    tool_results: list[dict]
    report: Report | None
    trace: list[dict]


class ErrorView(Contract):
    error: dict


def create_app(settings: Settings | None = None, provider: Provider | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(application):
        application.state.service = DataPilot(settings, provider)
        try:
            yield
        finally:
            application.state.service.close()

    app = FastAPI(title="DataPilot", version=__version__, lifespan=lifespan,
                  description="Bounded analysis workflow. Single-workspace service; offline mock by default.")
    app.add_middleware(RequestGuard, settings=settings)

    def error_response(request, code, message, status, **extra):
        payload = {"code": code, "message": message, "request_id": getattr(request.state, "request_id", None), **extra}
        return JSONResponse({"error": payload}, status_code=status)

    @app.exception_handler(AppError)
    async def application_error(request: Request, exc: AppError):
        extra = {"analysis_id": exc.analysis_id} if hasattr(exc, "analysis_id") else {}
        return error_response(request, exc.code, exc.message, exc.status, **extra)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(request, "invalid_request", "Request parameters failed validation", 422,
                              fields=[{"location": list(e["loc"]), "type": e["type"]} for e in exc.errors()[:10]])

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(request, "http_error", "The requested operation is unavailable", exc.status_code)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception):
        logging.getLogger("datapilot").error("request_failed request_id=%s type=%s",
            getattr(request.state, "request_id", "unknown"), type(exc).__name__)
        return error_response(request, "internal_error", "An internal error occurred", 500)

    @app.get("/health", operation_id="health", tags=["health"])
    def health():
        try:
            with app.state.service.store.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            raise AppError("database_unavailable", "Database health check failed", 503) from None
        return {"status": "ok", "version": __version__, "provider": settings.llm_provider, "mode": settings.app_env}

    key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    def require_key(key: str | None = Security(key_header)):
        if not authorized(settings, key):
            raise AppError("unauthorized", "A valid X-API-Key is required", 401)

    router = APIRouter(dependencies=[Security(require_key)],
                      responses={code: {"model": ErrorView} for code in (401, 404, 409, 413, 415, 422, 500, 503)})

    @router.post("/datasets/upload", status_code=201, response_model=DatasetView,
                 operation_id="uploadDataset", tags=["datasets"])
    def upload_dataset(file: Annotated[UploadFile, File()], drop_duplicates: Annotated[bool, Form()] = False):
        try:
            raw = file.file.read(settings.max_upload_bytes + 1)
        finally:
            file.file.close()
        return app.state.service.upload(file.filename or "", raw, drop_duplicates)

    @router.get("/datasets/{dataset_id}/profile", response_model=ProfileView,
                operation_id="getDatasetProfile", tags=["datasets"])
    def get_profile(dataset_id: UUID):
        record = app.state.service.store.dataset(str(dataset_id))
        return {"dataset_id": record.id, "profile": record.profile}

    @router.post("/analysis", status_code=201, response_model=TaskView,
                 operation_id="createAnalysis", tags=["analysis"])
    def create_analysis(request: AnalysisRequest):
        return app.state.service.analyze(request)

    @router.get("/analysis/{analysis_id}", response_model=TaskView,
                operation_id="getAnalysis", tags=["analysis"])
    def get_analysis(analysis_id: UUID):
        return app.state.service.store.task(str(analysis_id))

    @router.get("/analysis/{analysis_id}/report", response_model=Report,
                operation_id="getAnalysisReport", tags=["analysis"])
    def get_report(analysis_id: UUID):
        task = app.state.service.store.task(str(analysis_id))
        if task["status"] != "completed":
            raise AppError("analysis_not_complete", "The analysis has no completed report", 409)
        return task["report"]

    app.include_router(router)
    app.include_router(create_coze_router(require_key))
    return app

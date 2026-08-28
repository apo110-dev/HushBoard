"""FastAPI entry point for HushBoard.

Run from the repository root with::

    uvicorn app.main:app --host 127.0.0.1 --port 4173
"""
from __future__ import annotations

import hmac
import ipaddress
import sqlite3
from collections.abc import Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Header, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import Settings
from .database import DatabaseError, NotFound, StateConflict
from .schemas import DemoSendRequest, ModerationRequest, SeedRequest, SubmissionCreate
from .service import (
    FeatureDisabled,
    HushBoardService,
    InputRejected,
    InvalidAction,
    ServiceError,
)
from .wallet import WalletError, public_wallet_error
from .watcher import BackgroundWatcher


class ApiProblem(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}
        super().__init__(message)


def _error(status_code: int, code: str, message: str, *, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}, "detail": message},
        headers=headers,
    )


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    if host in {"localhost", "testclient"}:
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _require_admin(request: Request, x_admin_key: str | None = Header(default=None)) -> None:
    service: HushBoardService = request.app.state.service
    settings = service.settings
    local_development_exception = bool(
        settings.demo_open_admin
        and _is_loopback(request)
        and (service.mode == "mock" or settings.admin_key == "local-demo-only")
    )
    if local_development_exception:
        return
    supplied = x_admin_key or ""
    if settings.admin_key and hmac.compare_digest(
        supplied.encode("utf-8", errors="ignore"),
        settings.admin_key.encode("utf-8"),
    ):
        return
    raise ApiProblem(
        status.HTTP_401_UNAUTHORIZED,
        "admin_required",
        "administrator authorization is required",
        {"WWW-Authenticate": "HushBoard-Admin"},
    )


def create_app(
    settings: Settings | None = None,
    *,
    service: HushBoardService | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    service = service or HushBoardService(settings)
    watcher = BackgroundWatcher(service, settings.watcher_interval)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        watcher.start()
        try:
            yield
        finally:
            await watcher.stop()

    application = FastAPI(
        title="HushBoard API",
        summary="Accountless, bonded feedback with centralized moderation",
        description=(
            "Every invoice is a unique Orchard-only testnet UA and a ZIP-321 request for "
            "exactly 1,000,000 zats. Check `mode` before treating demo data as on-chain."
        ),
        version="1.0.0",
        # The default Swagger page relies on third-party/inline assets that the strict
        # loopback CSP intentionally blocks. Keep the machine-readable local schema only.
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    application.state.settings = settings
    application.state.service = service
    application.state.watcher = watcher

    application.add_middleware(
        CORSMiddleware,
        allow_origin_regex=settings.cors_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Admin-Key"],
        expose_headers=["X-HushBoard-Mode"],
        max_age=600,
    )
    application.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver", "*.localhost"],
    )

    @application.middleware("http")
    async def security_headers(request: Request, call_next: Callable[..., Any]):
        if not _is_loopback(request):
            return _error(403, "loopback_only", "HushBoard accepts requests only from this machine")
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > 16 * 1024:
                    return _error(413, "request_too_large", "request body is too large")
            except ValueError:
                return _error(400, "invalid_content_length", "invalid Content-Length header")
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["X-HushBoard-Mode"] = service.mode
        return response

    @application.exception_handler(ApiProblem)
    async def handle_problem(_: Request, exc: ApiProblem):
        return _error(exc.status_code, exc.code, exc.message, headers=exc.headers)

    @application.exception_handler(RequestValidationError)
    async def handle_validation(_: Request, exc: RequestValidationError):
        # Do not echo request inputs (which may contain a refund address or an admin typo).
        errors = [
            {
                "location": [str(part) for part in item.get("loc", ())],
                "message": item.get("msg", "invalid value"),
                "type": item.get("type", "validation_error"),
            }
            for item in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content={
                "error": {"code": "validation_error", "message": "request validation failed"},
                "detail": errors,
            },
        )

    @application.exception_handler(NotFound)
    async def handle_not_found(_: Request, exc: NotFound):
        return _error(404, "not_found", str(exc))

    @application.exception_handler(StateConflict)
    async def handle_state_conflict(_: Request, exc: StateConflict):
        return _error(409, "invalid_state", str(exc))

    @application.exception_handler(InvalidAction)
    async def handle_invalid_action(_: Request, exc: InvalidAction):
        return _error(409, exc.code, str(exc))

    @application.exception_handler(FeatureDisabled)
    async def handle_disabled(_: Request, exc: FeatureDisabled):
        return _error(409, exc.code, str(exc))

    @application.exception_handler(InputRejected)
    async def handle_rejected(_: Request, exc: InputRejected):
        return _error(422, exc.code, str(exc))

    @application.exception_handler(WalletError)
    async def handle_wallet(_: Request, exc: WalletError):
        return _error(503, "wallet_unavailable", public_wallet_error(exc))

    @application.exception_handler(sqlite3.Error)
    async def handle_sqlite(_: Request, __: sqlite3.Error):
        return _error(500, "database_error", "database operation failed")

    @application.exception_handler(DatabaseError)
    async def handle_database(_: Request, __: DatabaseError):
        return _error(500, "database_error", "database operation failed")

    @application.exception_handler(ServiceError)
    async def handle_service(_: Request, exc: ServiceError):
        return _error(500, exc.code, str(exc))

    static_dir = settings.root / "static"
    if static_dir.is_dir():
        # Mount frontend assets without mutating the source directory.
        application.mount("/static", StaticFiles(directory=static_dir, check_dir=True), name="static")

    @application.get("/", include_in_schema=False)
    def root():
        index = static_dir / "index.html"
        if index.is_file():
            return FileResponse(index, media_type="text/html; charset=utf-8")
        return {
            "service": "HushBoard",
            "api": "/api",
            "health": "/api/health",
            "openapi": "/api/openapi.json",
            "mode": service.mode,
            "mode_label": service.mode_label,
        }

    @application.get("/health", include_in_schema=False)
    @application.get("/api/health", tags=["system"])
    @application.get("/api/status", include_in_schema=False)
    def health() -> dict[str, Any]:
        return service.health()

    @application.get("/api/submissions", tags=["submissions"])
    def list_submissions(
        submission_status: str | None = Query(default=None, alias="status", max_length=32),
        q: str | None = Query(default=None, max_length=100),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=1_000_000),
    ) -> dict[str, Any]:
        return service.list_submissions(
            status=submission_status,
            query=q,
            limit=limit,
            offset=offset,
        )

    @application.post(
        "/api/submissions",
        tags=["submissions"],
        status_code=status.HTTP_201_CREATED,
    )
    def create_submission(payload: SubmissionCreate) -> dict[str, Any]:
        return service.create_submission(payload.title, payload.body, payload.refund_address)

    @application.get("/api/submissions/{public_id}", tags=["submissions"])
    def get_submission(public_id: str) -> dict[str, Any]:
        return service.get_submission(public_id)

    @application.post("/api/submissions/{public_id}/demo-send", tags=["participant"])
    @application.post("/api/submissions/{public_id}/pay", include_in_schema=False)
    def demo_send(public_id: str, _: DemoSendRequest | None = None) -> dict[str, Any]:
        return service.demo_send(public_id)

    @application.post("/api/sync", tags=["system"])
    def sync() -> dict[str, Any]:
        return service.sync()

    @application.post("/api/submissions/{public_id}/moderate", tags=["moderation"])
    def moderate(
        public_id: str,
        payload: ModerationRequest,
        _: None = Depends(_require_admin),
    ) -> dict[str, Any]:
        return service.moderate(public_id, decision=payload.decision, note=payload.note)

    @application.post("/api/reset", tags=["demo"])
    @application.post("/api/demo/reset", include_in_schema=False)
    def reset(_: None = Depends(_require_admin)) -> dict[str, Any]:
        return service.reset()

    @application.post("/api/seed", tags=["demo"])
    @application.post("/api/demo/seed", include_in_schema=False)
    def seed(
        payload: SeedRequest | None = None,
        _: None = Depends(_require_admin),
    ) -> dict[str, Any]:
        payload = payload or SeedRequest()
        return service.seed(reset=payload.reset, count=payload.count)

    return application


app = create_app()

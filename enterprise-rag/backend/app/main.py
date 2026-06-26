"""
Enterprise Knowledge Assistant - FastAPI Application

Main entry point. Configures:
- CORS, security headers, CSP
- Rate limiting
- Structured logging
- OpenTelemetry tracing
- Startup/shutdown lifecycle
- All API routers
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from app.core.config.settings import get_settings
from app.core.logging.setup import setup_logging
from app.infrastructure.database.session import close_db, init_db

# Import all routers
from app.api.v1.auth.router import router as auth_router
from app.api.v1.documents.router import router as documents_router
from app.api.v1.chat.router import router as chat_router
from app.api.v1.admin.router import router as admin_router
from app.api.v1.analytics.router import router as analytics_router
from app.api.v1.search.router import router as search_router

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter("http_requests_total", "Total HTTP requests", ["method", "endpoint", "status"])
REQUEST_LATENCY = Histogram("http_request_duration_seconds", "HTTP request latency")


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown lifecycle."""
    settings = get_settings()
    log = structlog.get_logger("app.startup")

    # Startup
    log.info("Starting Enterprise Knowledge Assistant", version=settings.app_version, env=settings.environment)
    setup_logging(settings.log_level)
    await init_db()
    log.info("Database initialized")

    yield  # Application runs here

    # Shutdown
    log.info("Shutting down...")
    await close_db()
    log.info("Shutdown complete")


# ---------------------------------------------------------------------------
# App Factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """
    Create and configure the FastAPI application.

    Following the factory pattern allows easy testing with different configs.
    """
    settings = get_settings()

    app = FastAPI(
        title="Enterprise Knowledge Assistant",
        description="AI-powered enterprise RAG system for internal knowledge management",
        version=settings.app_version,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
        openapi_url="/openapi.json" if not settings.is_production else None,
        lifespan=lifespan,
    )

    # ── Rate Limiting ────────────────────────────────────────────────────────
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)

    # ── CORS ─────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
        expose_headers=["X-Request-ID"],
    )

    # ── Trusted Host ─────────────────────────────────────────────────────────
    if settings.is_production:
        # Extract hostnames from cors_origins for trusted host check
        trusted_hosts = [
            origin.replace("https://", "").replace("http://", "")
            for origin in settings.cors_origins
        ] + ["localhost", "127.0.0.1"]
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)

    # ── Security Headers Middleware ───────────────────────────────────────────
    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self'; "
            "connect-src 'self';"
        )
        return response

    # ── Request ID & Metrics Middleware ────────────────────────────────────────
    @app.middleware("http")
    async def request_instrumentation(request: Request, call_next):
        import uuid
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        start = time.perf_counter()

        try:
            response = await call_next(request)
            status = response.status_code
        except Exception:
            status = 500
            raise
        finally:
            latency = time.perf_counter() - start
            REQUEST_COUNT.labels(
                method=request.method,
                endpoint=request.url.path,
                status=status,
            ).inc()
            REQUEST_LATENCY.observe(latency)

        response.headers["X-Request-ID"] = request_id
        return response

    # ── Routers ───────────────────────────────────────────────────────────────
    API_PREFIX = "/api/v1"
    app.include_router(auth_router, prefix=f"{API_PREFIX}/auth", tags=["Authentication"])
    app.include_router(documents_router, prefix=f"{API_PREFIX}/documents", tags=["Documents"])
    app.include_router(chat_router, prefix=f"{API_PREFIX}/chat", tags=["Chat"])
    app.include_router(admin_router, prefix=f"{API_PREFIX}/admin", tags=["Admin"])
    app.include_router(analytics_router, prefix=f"{API_PREFIX}/analytics", tags=["Analytics"])
    app.include_router(search_router, prefix=f"{API_PREFIX}/search", tags=["Search"])

    # ── Health & Metrics Endpoints ─────────────────────────────────────────────
    @app.get("/health", include_in_schema=False)
    async def health_check():
        """Health check endpoint for load balancers and Docker HEALTHCHECK."""
        return {"status": "healthy", "version": settings.app_version}

    @app.get("/metrics", include_in_schema=False)
    async def metrics():
        """Prometheus metrics endpoint."""
        return Response(
            content=generate_latest(),
            media_type=CONTENT_TYPE_LATEST,
        )

    # ── Global Exception Handler ───────────────────────────────────────────────
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """
        Global error handler. Never expose internal stack traces in production.
        Always return a sanitized error response.
        """
        log = structlog.get_logger("app.error")
        log.error("Unhandled exception", path=request.url.path, error=str(exc), exc_info=True)

        if settings.is_production:
            return JSONResponse(
                status_code=500,
                content={"detail": "An internal error occurred. Please try again."},
            )
        else:
            import traceback
            return JSONResponse(
                status_code=500,
                content={"detail": str(exc), "traceback": traceback.format_exc()},
            )

    return app


app = create_app()

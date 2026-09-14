"""Establish tenancy before FastAPI copies context into endpoint worker threads."""
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from services.tenancy import tenant_scope, tenant_names


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        token = request.cookies.get("admin_session", "")
        name = token.split(".", 1)[0] if "." in token else "default"
        if request.url.path == "/api/admin/auth/login":
            name = request.query_params.get("tenant", "default")
        if name not in tenant_names():
            return JSONResponse({"detail": "Invalid tenant or session"}, status_code=401)
        if name in getattr(request.app.state, "unavailable_tenants", set()):
            return JSONResponse({"detail": "Tenant database initialization unavailable; contact the operator"}, status_code=503)
        with tenant_scope(name):
            request.state.tenant_id = name
            # Close legacy anonymous inspection/report APIs. All tenant data
            # routes now verify both bearer session and CSRF on unsafe methods.
            if request.url.path.startswith("/api/") and request.url.path != "/api/admin/auth/login":
                from starlette.concurrency import run_in_threadpool
                from fastapi import HTTPException
                from app.admin_auth import require_admin
                from db.session import SessionLocal
                def authenticate():
                    with SessionLocal() as db:
                        require_admin(request, db)
                try:
                    await run_in_threadpool(authenticate)
                except HTTPException as exc:
                    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
            return await call_next(request)

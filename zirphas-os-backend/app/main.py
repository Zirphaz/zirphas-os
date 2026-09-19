from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.api.dependencies import DatabaseSession
from app.api.v1.endpoints import attachments, auth, clients, dashboard, inventory, os, public, users
from app.core.config import get_settings

settings = get_settings()
app = FastAPI(title="ZirphasOS — Smart Repair Management", version="1.0.0",
              description="API de assistência técnica com State Pattern, RBAC, estoque e financeiro.")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
                   allow_headers=["Authorization", "Content-Type"])
for router in (auth.router, users.router, clients.router, inventory.router, os.router,
               attachments.router, public.router, dashboard.router):
    app.include_router(router, prefix="/api/v1")

static = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=static), name="assets")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith(("/api/", "/status/")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(IntegrityError)
async def integrity_error(request: Request, exc: IntegrityError):
    return JSONResponse(status_code=409, content={"detail": "Registro duplicado ou operação incompatível com os vínculos existentes."})


@app.get("/health", tags=["Infraestrutura"])
def health(db: DatabaseSession):
    try:
        db.execute(text("SELECT 1"))
    except OperationalError:
        raise HTTPException(503, "Banco de dados indisponível.") from None
    return {"status": "ok", "service": "ZirphasOS"}


@app.get("/status/{token}", include_in_schema=False)
def tracking_page(token: str, db: DatabaseSession):
    public.find_public_order(db, token)
    return FileResponse(static / "portal.html", media_type="text/html", headers={
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
    })

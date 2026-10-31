"""FastAPI localhost workspace. PDF parsing runs in one dedicated worker."""
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from .storage import ReviewConflictError, StorageError
from .workspace import MAX_UPLOAD_BYTES, Workspace

ASSETS = Path(__file__).resolve().parent / "web_static"


def create_app(database: Path, *, demo=False, uploads=Path("data/uploads"),
               exports=Path("output/invoices"), env_file=Path(".env"), today=None):
    workspace = Workspace(database, demo=demo, uploads=uploads, exports=exports, env_file=env_file, today=today)
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        workspace.close()

    app = FastAPI(title="AI Document Automation", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.workspace = workspace

    @app.middleware("http")
    async def local_session(request: Request, call_next):
        if request.url.hostname not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"error": "Use the localhost workspace URL."}, status_code=403)
        if request.url.path.startswith("/api/"):
            if not secrets.compare_digest(request.cookies.get("doc_workspace", ""), token):
                return JSONResponse({"error": "Reload the workspace to start a session."}, status_code=403)
            if request.method not in {"GET", "HEAD"}:
                origin = request.headers.get("origin")
                if (not secrets.compare_digest(request.headers.get("x-csrf-token", ""), token)
                        or (origin is not None and origin.rstrip("/") != str(request.base_url).rstrip("/"))):
                    return JSONResponse({"error": "Reload the workspace before saving."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self'; frame-ancestors 'self'; base-uri 'self'"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        return JSONResponse({"error": str(exc.detail)}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def parameter_error(request, exc):
        return JSONResponse({"error": "Invalid request parameters."}, status_code=422)

    @app.exception_handler(ReviewConflictError)
    async def conflict(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(StorageError)
    async def storage_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.exception_handler(ValueError)
    async def input_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=400)

    @app.get("/", response_class=HTMLResponse)
    def index():
        html = (ASSETS / "index.html").read_text(encoding="utf-8").replace("__CSRF__", token)
        response = HTMLResponse(html)
        response.set_cookie("doc_workspace", token, httponly=True, samesite="strict")
        return response

    @app.get("/api/documents")
    def documents(q: str = Query("", max_length=200), status: str = "all", page: int = Query(1, ge=1)):
        if status not in {"all", "pending", "confirmed", "needs_review"}:
            raise HTTPException(400, "Unknown document filter.")
        return workspace.list_documents(q, status, page)

    @app.get("/api/documents/{document_id}")
    def document(document_id: int):
        return workspace.document(document_id)

    @app.get("/api/documents/{document_id}/pdf")
    def pdf(document_id: int):
        return Response(workspace.pdf_bytes(document_id), media_type="application/pdf",
                        headers={"Content-Disposition": "inline"})

    @app.post("/api/documents/{document_id}/review")
    async def review(document_id: int, request: Request):
        if len(await request.body()) > 32_000:
            raise HTTPException(413, "Review form is too large.")
        return workspace.save_review(document_id, await request.json())

    @app.get("/api/export.csv")
    def export(scope: str = "confirmed"):
        if scope not in {"confirmed", "all"}:
            raise HTTPException(400, "Choose confirmed or all results.")
        return Response(workspace.export_csv(scope), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="invoice-results.csv"'})

    @app.post("/api/uploads", status_code=202)
    async def upload(request: Request, filename: str = Query(..., max_length=250)):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "PDF exceeds the 20 MB limit.")
        return workspace.submit(filename, bytes(content))

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return workspace.job(job_id)

    app.mount("/static", StaticFiles(directory=ASSETS), name="static")
    return app

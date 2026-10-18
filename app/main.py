import logging
import os
import sys
from contextlib import asynccontextmanager

if __package__ in (None, ""):
    # Allow `python app/main.py` from the project root.
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers.api import router as api_router
from app.security import PUBLIC_API_PATHS, add_security_headers, request_has_valid_auth
from app.services.executor_service import sandbox_status
from app.services.pipeline_service import FILTERED_DIR, REPORT_DIR, SCRIPT_DIR
from app.services.session_manager import recover_interrupted_sessions
from app.services.web_helpers import UPLOAD_DIR

load_dotenv()
logger = logging.getLogger(__name__)

for directory in [UPLOAD_DIR, FILTERED_DIR, SCRIPT_DIR, REPORT_DIR]:
    os.makedirs(directory, exist_ok=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Skipped under tests: they share workspace/ with a possibly running dev server.
    if os.getenv("PDF_REPORT_RECOVER_SESSIONS_ON_START", "0" if os.getenv("APP_ENV") == "test" else "1") == "1":
        recover_interrupted_sessions()
    status = sandbox_status()
    if not status["bubblewrap_available"]:
        if status["unsafe_fallback_allowed"]:
            logger.warning("bubblewrap unavailable (%s); generated code runs under the weaker python-guard.", status["bubblewrap_error"])
        else:
            logger.error("bubblewrap unavailable (%s); report generation will fail until it is fixed.", status["bubblewrap_error"])
    yield


app = FastAPI(title="Report Generator v0.3.6", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
app.include_router(api_router)


@app.middleware("http")
async def require_api_access(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.url.path not in PUBLIC_API_PATHS:
        if not request_has_valid_auth(request):
            response = JSONResponse({"detail": "Unauthorized"}, status_code=401)
            add_security_headers(response)
            return response
    response = await call_next(request)
    add_security_headers(response)
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True, reload_dirs=["app", "static"])

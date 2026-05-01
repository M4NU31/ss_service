"""
Screenshot microservice — FastAPI entry point.
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, Request, Security, status
from fastapi.responses import Response
from fastapi.security import APIKeyHeader

from .config import settings
from .middleware import rate_limit
from .schemas import PageScreenshotRequest, TaskScreenshotRequest
from .screenshot import ScreenshotEngine
from .security import validate_url


# ---------------------------------------------------------------------------
# Application lifespan — browser starts once, stops once.
# ---------------------------------------------------------------------------

engine = ScreenshotEngine()


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, engine.start)
    yield
    await loop.run_in_executor(None, engine.stop)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Screenshot Service",
    description="Headless browser screenshot microservice.",
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Auth dependency (no-op when API_KEY is not set)
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(key: str | None = Security(_api_key_header)) -> None:
    if not settings.api_key:
        return
    if key != settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
        )


# Combine auth + rate limit into a single dependency list for DRY endpoints.
_guards = [Depends(require_api_key), Depends(rate_limit)]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _image_response(data: bytes, fmt: str) -> Response:
    media_type = "image/jpeg" if fmt == "jpeg" else "image/png"
    return Response(content=data, media_type=media_type)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Liveness probe. Returns 200 when the service is ready."""
    return {"status": "ok"}


@app.post(
    "/screenshot/page",
    tags=["screenshot"],
    summary="Capture a full-page or viewport screenshot",
    dependencies=_guards,
)
async def screenshot_page(req: PageScreenshotRequest) -> Response:
    """
    Render *url* in a headless browser and return the screenshot as an image.

    - Default format: **JPEG** (quality 80).
    - Set `full_page: true` to capture the entire scrollable page.
    - Response `Content-Type` is `image/jpeg` or `image/png`.
    """
    validate_url(str(req.url))
    try:
        data = await engine.page_screenshot(req)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    return _image_response(data, req.format)


@app.post(
    "/screenshot/task",
    tags=["screenshot"],
    summary="Capture a screenshot focused on a click coordinate",
    dependencies=_guards,
)
async def screenshot_task(req: TaskScreenshotRequest) -> Response:
    """
    Render *url*, optionally scroll to a position, then return:

    - **Crop** *(default)* — close-up around `(x, y)`.
    - **Highlight** — full viewport with a crosshair at `(x, y)`.
    - **Dual** (`dual: true`) — JSON with both images as base64:
      `cropped` (close-up) and `full` (viewport with crosshair).

    Coordinates are **viewport-relative** (pixels from the top-left of the
    visible window after `scroll` has been applied).
    """
    validate_url(str(req.url))
    try:
        result = await engine.task_screenshot(req)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))

    return _image_response(result, req.format)

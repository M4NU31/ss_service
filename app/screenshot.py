"""
Screenshot engine — sync Playwright in a thread pool.

Why sync instead of async Playwright:
  Python 3.14 on Windows broke asyncio subprocess spawning (the mechanism
  async Playwright uses to launch the browser). The sync API runs the browser
  in its own OS thread and is unaffected. FastAPI awaits each request via
  asyncio.to_thread(), so the async interface is preserved end-to-end.

Design:
  - One browser process shared across all requests.
  - One fresh browser context per request (isolated cookies/cache/storage).
  - threading.Semaphore caps concurrent contexts to avoid OOM.
  - Contexts are always closed in a finally block.
"""

import io
import threading

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, Browser, Playwright

from .config import settings
from .schemas import PageScreenshotRequest, TaskScreenshotRequest


class ScreenshotEngine:

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._semaphore: threading.Semaphore | None = None
        self._browser_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Lifecycle (called via asyncio.to_thread from the FastAPI lifespan)
    # ------------------------------------------------------------------

    def start(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._launch_browser()
        self._semaphore = threading.Semaphore(settings.browser_max_concurrent)

    def stop(self) -> None:
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()

    def _launch_browser(self) -> Browser:
        launcher = getattr(self._playwright, settings.browser_engine)
        return launcher.launch(headless=True, args=settings.browser_args)

    def _ensure_browser(self) -> None:
        """Restart the browser process if it has crashed or disconnected."""
        if self._browser and self._browser.is_connected():
            return
        with self._browser_lock:
            # Re-check inside the lock — another thread may have restarted it.
            if self._browser and self._browser.is_connected():
                return
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = self._launch_browser()

    # ------------------------------------------------------------------
    # Public API  (sync — each called via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _page_screenshot_sync(self, req: PageScreenshotRequest) -> bytes:
        self._ensure_browser()
        with self._semaphore:
            ctx = self._browser.new_context(
                viewport={"width": req.viewport.width, "height": req.viewport.height},
            )
            try:
                page = ctx.new_page()
                self._navigate(page, str(req.url))
                if req.delay_ms:
                    page.wait_for_timeout(req.delay_ms)
                raw = page.screenshot(
                    full_page=req.full_page,
                    type="png",
                    animations="disabled",
                )
            finally:
                ctx.close()
        return _encode(raw, req.format, req.quality)

    def _task_screenshot_sync(self, req: TaskScreenshotRequest) -> bytes:
        self._ensure_browser()
        with self._semaphore:
            ctx = self._browser.new_context(
                viewport={"width": req.viewport.width, "height": req.viewport.height},
            )
            try:
                page = ctx.new_page()
                self._navigate(page, str(req.url))

                if req.scroll:
                    page.evaluate(
                        "([x, y]) => window.scrollTo(x, y)",
                        [req.scroll.x, req.scroll.y],
                    )
                    # Wait for the browser to repaint and for any lazy-loaded
                    # content triggered by the scroll to finish loading.
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        page.wait_for_timeout(800)

                # delay_ms runs after scroll so it applies to the final position.
                if req.delay_ms:
                    page.wait_for_timeout(req.delay_ms)

                raw = page.screenshot(
                    full_page=False,
                    type="png",
                    animations="disabled",
                )
            finally:
                ctx.close()

        img = Image.open(io.BytesIO(raw))
        crop_w = req.crop_size.width if req.crop_size else settings.task_crop_width
        crop_h = req.crop_size.height if req.crop_size else settings.task_crop_height

        if req.highlight:
            img = _draw_crosshair(img, req.x, req.y)
        else:
            img = _crop_around(img, req.x, req.y, crop_w, crop_h)

        return _encode_pil(img, req.format, req.quality)

    # ------------------------------------------------------------------
    # Async wrappers (called by FastAPI endpoint handlers)
    # ------------------------------------------------------------------

    async def page_screenshot(self, req: PageScreenshotRequest) -> bytes:
        import asyncio
        return await asyncio.to_thread(self._page_screenshot_sync, req)

    async def task_screenshot(self, req: TaskScreenshotRequest) -> bytes:
        import asyncio
        return await asyncio.to_thread(self._task_screenshot_sync, req)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _navigate(self, page, url: str) -> None:
        timeout = settings.browser_timeout_ms
        try:
            page.goto(url, wait_until="networkidle", timeout=timeout)
        except Exception:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            except Exception as exc:
                raise RuntimeError(f"Failed to load page: {exc}") from exc


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

def _crop_around(img: Image.Image, cx: int, cy: int, crop_w: int, crop_h: int) -> Image.Image:
    iw, ih = img.size
    left   = max(0, cx - crop_w // 2)
    top    = max(0, cy - crop_h // 2)
    right  = min(iw, cx + crop_w // 2)
    bottom = min(ih, cy + crop_h // 2)
    return img.crop((left, top, right, bottom))


def _draw_crosshair(
    img: Image.Image,
    cx: int, cy: int,
    radius: int = 14,
    line_len: int = 22,
    color: tuple = (220, 30, 30, 230),
    width: int = 2,
) -> Image.Image:
    original_mode = img.mode
    overlay = img.convert("RGBA")
    draw = ImageDraw.Draw(overlay, "RGBA")
    draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=color, width=width)
    draw.line((cx - line_len, cy, cx + line_len, cy), fill=color, width=width)
    draw.line((cx, cy - line_len, cx, cy + line_len), fill=color, width=width)
    return overlay.convert(original_mode)


def _encode(raw_png: bytes, fmt: str, quality: int) -> bytes:
    if fmt == "png":
        return raw_png
    return _encode_pil(Image.open(io.BytesIO(raw_png)), fmt, quality)


def _encode_pil(img: Image.Image, fmt: str, quality: int) -> bytes:
    buf = io.BytesIO()
    if fmt == "jpeg":
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGB")
        img.save(buf, format="JPEG", quality=quality, optimize=True)
    else:
        img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()

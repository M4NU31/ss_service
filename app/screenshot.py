"""
Screenshot engine — sync Playwright in a dedicated single thread.

Why a single dedicated thread instead of asyncio.to_thread():
  sync_playwright uses greenlets that are bound to the thread where they
  were created. asyncio.to_thread() dispatches to a random thread-pool
  thread, causing "Cannot switch to a different thread" errors when the
  browser restarts or operations land on a different thread than startup.

  A ThreadPoolExecutor(max_workers=1) guarantees all Playwright calls
  always run in the same persistent thread, eliminating the greenlet
  cross-thread issue entirely.

Design:
  - One persistent thread for all Playwright work.
  - One browser process shared across all requests.
  - One fresh browser context per request (isolated cookies/cache/storage).
  - Contexts are always closed in a finally block.
  - On browser crash, both playwright and browser are restarted in-thread.
"""

import asyncio
import io
import threading
from concurrent.futures import ThreadPoolExecutor

from PIL import Image, ImageDraw
from playwright.sync_api import sync_playwright, Browser, Playwright

from .config import settings
from .schemas import PageScreenshotRequest, TaskScreenshotRequest


class ScreenshotEngine:

    def __init__(self) -> None:
        # Single-thread executor — all Playwright calls run in this one thread.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="playwright")
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        future = self._executor.submit(self._start_sync)
        future.result()

    def stop(self) -> None:
        future = self._executor.submit(self._stop_sync)
        future.result(timeout=10)
        self._executor.shutdown(wait=False)

    def _start_sync(self) -> None:
        self._playwright = sync_playwright().start()
        self._browser = self._launch_browser_sync()

    def _stop_sync(self) -> None:
        try:
            if self._browser:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass

    def _launch_browser_sync(self) -> Browser:
        launcher = getattr(self._playwright, settings.browser_engine)
        return launcher.launch(headless=True, args=settings.browser_args)

    def _restart_sync(self) -> None:
        """Restart playwright + browser entirely within the dedicated thread."""
        self._stop_sync()
        self._playwright = sync_playwright().start()
        self._browser = self._launch_browser_sync()

    # ------------------------------------------------------------------
    # Sync workers (always run inside self._executor)
    # ------------------------------------------------------------------

    def _page_screenshot_sync(self, req: PageScreenshotRequest) -> bytes:
        last_exc: Exception | None = None
        for attempt in range(2):
            if not (self._browser and self._browser.is_connected()):
                self._restart_sync()
            try:
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
            except Exception as exc:
                last_exc = exc
                self._restart_sync()
        raise RuntimeError(f"Screenshot failed after retry: {last_exc}") from last_exc

    def _task_screenshot_sync(self, req: TaskScreenshotRequest) -> bytes:
        last_exc: Exception | None = None
        for attempt in range(2):
            if not (self._browser and self._browser.is_connected()):
                self._restart_sync()
            try:
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
                        try:
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            page.wait_for_timeout(800)

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
            except Exception as exc:
                last_exc = exc
                self._restart_sync()
        raise RuntimeError(f"Screenshot failed after retry: {last_exc}") from last_exc

    # ------------------------------------------------------------------
    # Async wrappers (called by FastAPI endpoint handlers)
    # ------------------------------------------------------------------

    async def page_screenshot(self, req: PageScreenshotRequest) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._page_screenshot_sync, req)

    async def task_screenshot(self, req: TaskScreenshotRequest) -> bytes:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self._task_screenshot_sync, req)

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

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

                    # Kill all CSS animations & transitions before they paint.
                    # More aggressive than Playwright's animations="disabled"
                    # (which only freezes them at screenshot time) — every
                    # element starts directly in its final state.
                    page.add_style_tag(content=_ANIMATION_KILL_CSS)

                    if req.scroll:
                        # Pre-fire IntersectionObservers by scrolling through
                        # the page first. Sites with reveal-on-scroll patterns
                        # (very common) keep elements at opacity:0 / pre-transform
                        # until their IO callback fires. Without this prefire,
                        # those elements render blank.
                        _prefire_observers(page, req.scroll.y)

                        page.evaluate(
                            "([x, y]) => window.scrollTo(x, y)",
                            [req.scroll.x, req.scroll.y],
                        )
                        try:
                            page.wait_for_load_state("networkidle", timeout=5000)
                        except Exception:
                            page.wait_for_timeout(500)

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
                    img = _draw_pin(img, req.x, req.y)
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


def _draw_pin(
    img: Image.Image,
    cx: int, cy: int,
    radius: int = 11,
    color: tuple = (255, 8, 79, 230),       # hsl(348, 100%, 52%) ≈ punchbug red
    inner_color: tuple = (255, 255, 255, 230),
) -> Image.Image:
    """Draw a teardrop pin at (cx, cy). Tip is at (cx, cy); circle sits above."""
    original_mode = img.mode
    overlay = img.convert("RGBA")
    draw = ImageDraw.Draw(overlay, "RGBA")

    # Circle body: above the tip
    bx = cx
    by = cy - radius - 10
    draw.ellipse((bx - radius, by - radius, bx + radius, by + radius), fill=color)
    # Triangle pointing down from circle to tip
    triangle = [
        (bx - int(radius * 0.65), by + int(radius * 0.6)),
        (cx, cy),
        (bx + int(radius * 0.65), by + int(radius * 0.6)),
    ]
    draw.polygon(triangle, fill=color)
    # White inner dot
    inner_r = max(2, int(radius * 0.38))
    draw.ellipse((bx - inner_r, by - inner_r, bx + inner_r, by + inner_r), fill=inner_color)

    return overlay.convert(original_mode)


# CSS injected before screenshot to make every animation and transition
# complete in 1ms. Elements jump straight to their end state, so we never
# capture mid-fade or pre-reveal artifacts.
_ANIMATION_KILL_CSS = """
*, *::before, *::after {
    animation-duration: 1ms !important;
    animation-delay: 0s !important;
    animation-iteration-count: 1 !important;
    transition-duration: 1ms !important;
    transition-delay: 0s !important;
    scroll-behavior: auto !important;
}
html { scroll-behavior: auto !important; }
"""


def _prefire_observers(page, target_y: int) -> None:
    """
    Scroll through the page from top to target_y in steps, giving any
    IntersectionObservers along the way a chance to fire and apply their
    reveal classes. Without this, sites with reveal-on-scroll patterns
    (Locomotive, GSAP ScrollTrigger, AOS, etc.) leave elements at their
    pre-reveal state when we jump directly to a deep scroll position.
    """
    try:
        page.evaluate(
            """
            ([targetY, viewportH]) => new Promise((resolve) => {
                const total = Math.max(targetY + viewportH, viewportH);
                const step = Math.max(viewportH / 2, 200);
                let pos = 0;
                const tick = () => {
                    window.scrollTo(0, pos);
                    if (pos >= total) { resolve(); return; }
                    pos += step;
                    requestAnimationFrame(tick);
                };
                tick();
            });
            """,
            [target_y, page.viewport_size["height"] if page.viewport_size else 720],
        )
    except Exception:
        # Non-fatal: prefire is a best-effort optimization
        pass


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

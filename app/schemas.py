from typing import Literal
from pydantic import BaseModel, HttpUrl, Field, model_validator


class Viewport(BaseModel):
    width: int = Field(default=1280, ge=320, le=3840)
    height: int = Field(default=720, ge=240, le=2160)


class ScrollPosition(BaseModel):
    x: int = Field(default=0, ge=0)
    y: int = Field(default=0, ge=0)


class CropSize(BaseModel):
    width: int = Field(default=600, ge=10, le=3840)
    height: int = Field(default=400, ge=10, le=2160)


# ---------------------------------------------------------------------------
# POST /screenshot/page
# ---------------------------------------------------------------------------

class PageScreenshotRequest(BaseModel):
    url: HttpUrl

    viewport: Viewport = Field(default_factory=Viewport)

    # Capture the full scrollable page instead of just the visible viewport.
    full_page: bool = False

    # Wait this many milliseconds after the page loads before capturing.
    # Useful for pages with animations or lazy-loaded content.
    delay_ms: int = Field(default=0, ge=0, le=30_000)

    format: Literal["jpeg", "png"] = "jpeg"

    # Only applied when format == "jpeg". Ignored for PNG (lossless).
    quality: int = Field(default=80, ge=1, le=100)


# ---------------------------------------------------------------------------
# POST /screenshot/task
# ---------------------------------------------------------------------------

class TaskScreenshotRequest(BaseModel):
    url: HttpUrl

    viewport: Viewport = Field(default_factory=Viewport)

    # Click point in **viewport coordinates** (pixels from top-left of the
    # visible browser window, after any scroll has been applied).
    x: int = Field(..., ge=0)
    y: int = Field(..., ge=0)

    # Optional: scroll the page to this position before capturing.
    # Coordinates here are page (document) coordinates in pixels.
    scroll: ScrollPosition | None = None

    # Optional: CSS selector of the clicked element. When provided together
    # with element_rect, ss_service compares the element's server-side
    # bounding rect to the client's rect and adjusts scroll so the element
    # ends up at the same viewport y the user saw — fixing imprecision on
    # smooth-scroll sites (Lenis, Locomotive) where window.scrollTo lands
    # mid-animation.
    selector: str | None = None

    # Optional: getBoundingClientRect() of the clicked element at click time
    # on the client. {top, left, width, height} in client viewport coords.
    element_rect: dict | None = None

    # Region to crop around (x, y). Omit to use the service default.
    crop_size: CropSize | None = None

    # When True: skip cropping, return the full viewport with a crosshair
    # drawn at (x, y). Useful when you need layout context instead of detail.
    highlight: bool = False

    # Wait this many milliseconds after the page loads before capturing.
    delay_ms: int = Field(default=0, ge=0, le=30_000)

    format: Literal["jpeg", "png"] = "jpeg"
    quality: int = Field(default=80, ge=1, le=100)

    @model_validator(mode="after")
    def coords_within_viewport(self) -> "TaskScreenshotRequest":
        if self.x >= self.viewport.width:
            raise ValueError(
                f"x ({self.x}) must be < viewport width ({self.viewport.width})"
            )
        if self.y >= self.viewport.height:
            raise ValueError(
                f"y ({self.y}) must be < viewport height ({self.viewport.height})"
            )
        return self

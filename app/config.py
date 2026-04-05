from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # -------------------------------------------------------------------
    # Browser
    # -------------------------------------------------------------------
    # Maximum number of Playwright browser contexts open simultaneously.
    # Each concurrent request consumes one context. Keep this in line with
    # your available RAM (each context ~30-80 MB depending on page weight).
    browser_max_concurrent: int = 5

    # How long (ms) to wait for page navigation before giving up.
    browser_timeout_ms: int = 30_000

    # Which Playwright browser engine to use: chromium | firefox | webkit
    browser_engine: str = "chromium"

    # Extra Chromium flags. Useful in Docker / low-memory environments.
    # Passed as a list; the default set is good for headless server use.
    browser_args: list[str] = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]

    # -------------------------------------------------------------------
    # Screenshot defaults
    # -------------------------------------------------------------------
    default_viewport_width: int = 1280
    default_viewport_height: int = 720
    default_jpeg_quality: int = 80

    # Default crop size (width × height) around the click point for task shots.
    task_crop_width: int = 600
    task_crop_height: int = 400

    # -------------------------------------------------------------------
    # Security
    # -------------------------------------------------------------------
    # Set a non-empty string to enable X-API-Key header authentication.
    api_key: str = ""

    # -------------------------------------------------------------------
    # Rate limiting (in-memory, per-IP sliding window)
    # -------------------------------------------------------------------
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 60     # max requests …
    rate_limit_window_seconds: int = 60  # … per this many seconds


settings = Settings()

# CLAUDE.md — Screenshot Service (ss_service)

This file gives Claude Code full context to continue development without prior conversation history.

---

## What This Project Is

Standalone headless-browser screenshot microservice for [Punch Site QA (pbg)](https://github.com/M4NU41/pbg). PBG calls this service server-side to capture screenshots of client websites when tasks are reported.

---

## Tech Stack

- **Runtime:** Python 3.12
- **Framework:** FastAPI + Uvicorn
- **Browser:** Playwright with Chromium
- **Image processing:** Pillow
- **Config:** Pydantic Settings (reads from `.env`)
- **Containerized:** Docker (multi-stage build, non-root user, `shm_size: 1gb`)

---

## Project Structure

```
app/
  main.py        → FastAPI app, endpoints, lifespan (browser start/stop)
  config.py      → Pydantic settings — all env vars with defaults
  schemas.py     → Request/response Pydantic models
  screenshot.py  → ScreenshotEngine — Playwright logic
  security.py    → URL validation (blocks private IPs, localhost)
  middleware.py  → Rate limiting (in-memory sliding window per IP)
  __init__.py
run.py           → Entry point for manual run (uvicorn)
requirements.txt
Dockerfile       → Multi-stage: builder (pip install) → slim runtime
docker-compose.yml
.env.example
```

---

## Key Files

| File | Purpose |
|---|---|
| `app/main.py` | FastAPI app — `/health`, `/screenshot/page`, `/screenshot/task` endpoints; browser lifespan |
| `app/screenshot.py` | `ScreenshotEngine` — starts/stops Playwright browser; `page_screenshot()` and `task_screenshot()` async methods |
| `app/schemas.py` | `PageScreenshotRequest`, `TaskScreenshotRequest` — Pydantic input models |
| `app/config.py` | `Settings` — all configuration via env vars with sensible defaults |
| `app/security.py` | `validate_url()` — rejects private/loopback IPs to prevent SSRF |
| `app/middleware.py` | `rate_limit()` FastAPI dependency — sliding window, per-IP, in-memory |

---

## API Endpoints

### `GET /health`
Liveness probe. Returns `{"status": "ok"}`. Used by Docker healthcheck.

### `POST /screenshot/page`
Full-page or viewport screenshot.

```json
{
  "url": "https://example.com",
  "full_page": false,
  "format": "jpeg",
  "viewport_width": 1280,
  "viewport_height": 720
}
```
Returns: binary image (`image/jpeg` or `image/png`)

### `POST /screenshot/task`
Screenshot focused around a click coordinate.

```json
{
  "url": "https://example.com",
  "x": 400,
  "y": 300,
  "scroll": 0,
  "dual": false,
  "format": "jpeg"
}
```

- Default: returns cropped image around `(x, y)`
- `dual: true`: returns JSON `{ "cropped": "<base64>", "full": "<base64>" }`

---

## Authentication

Optional API key via `X-API-Key` header. Set `API_KEY=` in `.env` to enable. Leave empty to disable (open service, rely on network-level access control).

If enabled in PBG, set `SCREENSHOT_SERVICE_API_KEY` in pbg's `.env`.

---

## Environment Variables

All variables are defined in `app/config.py` with defaults. Copy `.env.example` to `.env`.

| Variable | Default | Description |
|---|---|---|
| `BROWSER_MAX_CONCURRENT` | `5` | Max simultaneous browser contexts |
| `BROWSER_TIMEOUT_MS` | `30000` | Navigation timeout in ms |
| `BROWSER_ENGINE` | `chromium` | `chromium`, `firefox`, or `webkit` |
| `DEFAULT_VIEWPORT_WIDTH` | `1280` | Default viewport width px |
| `DEFAULT_VIEWPORT_HEIGHT` | `720` | Default viewport height px |
| `DEFAULT_JPEG_QUALITY` | `80` | JPEG quality (1–100) |
| `TASK_CROP_WIDTH` | `600` | Crop width around task pin |
| `TASK_CROP_HEIGHT` | `400` | Crop height around task pin |
| `API_KEY` | *(empty)* | Leave empty to disable auth |
| `RATE_LIMIT_ENABLED` | `true` | Enable per-IP rate limiting |
| `RATE_LIMIT_REQUESTS` | `60` | Max requests per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Window duration in seconds |

---

## Running Locally

```bash
# Manual
pip install -r requirements.txt
playwright install chromium
python run.py

# Docker
cp .env.example .env
docker compose up --build
```

Service available at `http://localhost:8000`.  
Swagger docs at `http://localhost:8000/docs`.

---

## Docker Notes

- **`shm_size: 1gb`** in docker-compose is critical — Chromium crashes without enough shared memory
- Chromium binaries installed to `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright` so they're accessible to the non-root user
- Single worker (`--workers 1`) by design — the browser singleton is not thread-safe. Scale horizontally with multiple container replicas instead
- Healthcheck: `GET /health` every 30s

---

## Integration with PBG

PBG calls this service from `apps/web/src/lib/screenshot.ts` (server-side, Node.js).

In pbg's `.env`:
```env
SCREENSHOT_SERVICE_URL=http://<this_service_host>:8000
```

The two services communicate server-to-server. PBG never exposes this service URL to the browser.

---

## Conventions

- Pydantic v2 for all models and settings
- Async FastAPI handlers — use `asyncio.to_thread()` for Playwright blocking calls
- `ScreenshotEngine` is a singleton started in `lifespan` — do not instantiate per-request
- Security-first: always validate URLs through `security.validate_url()` before passing to browser
- Rate limiting is in-memory — does not persist across restarts and is not shared across replicas

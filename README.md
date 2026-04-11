# Screenshot Service (`ss_service`)

Headless browser screenshot microservice for [Punch - Site QA Tool](https://github.com/M4NU41/pbg). Built with FastAPI and Playwright/Chromium.

---

## Features

- **Page screenshot** — full-page or viewport capture of any URL
- **Task screenshot** — crop or highlight around a specific `(x, y)` coordinate
- **Dual mode** — returns both a close-up crop and a full viewport with crosshair as base64 JSON
- **API key auth** — optional bearer key via `X-API-Key` header
- **Rate limiting** — configurable requests per window
- **Health endpoint** — `/health` for container probes

---

## Quick Start (Docker)

```bash
git clone https://github.com/M4NU41/ss_service.git
cd ss_service
cp .env.example .env   # adjust if needed
docker compose up --build
```

Service available at [http://localhost:8000](http://localhost:8000).  
API docs at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## Manual Setup

### Prerequisites
- Python 3.12+
- pip

### Install

```bash
pip install -r requirements.txt
playwright install chromium
```

### Run

```bash
python run.py
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `BROWSER_MAX_CONCURRENT` | `5` | Max concurrent browser sessions |
| `BROWSER_TIMEOUT_MS` | `30000` | Page load timeout in ms |
| `BROWSER_ENGINE` | `chromium` | Browser engine |
| `DEFAULT_VIEWPORT_WIDTH` | `1280` | Default viewport width |
| `DEFAULT_VIEWPORT_HEIGHT` | `720` | Default viewport height |
| `DEFAULT_JPEG_QUALITY` | `80` | JPEG output quality (1–100) |
| `TASK_CROP_WIDTH` | `600` | Crop width around task pin |
| `TASK_CROP_HEIGHT` | `400` | Crop height around task pin |
| `API_KEY` | *(empty)* | Leave empty to disable auth |
| `RATE_LIMIT_ENABLED` | `true` | Enable rate limiting |
| `RATE_LIMIT_REQUESTS` | `60` | Max requests per window |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Rate limit window in seconds |

---

## API Endpoints

### `GET /health`
Liveness probe. Returns `{ "status": "ok" }`.

### `POST /screenshot/page`
Capture a full-page or viewport screenshot.

```json
{
  "url": "https://example.com",
  "full_page": false,
  "format": "jpeg",
  "viewport_width": 1280,
  "viewport_height": 720
}
```

### `POST /screenshot/task`
Capture a screenshot focused on a click coordinate.

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

Set `dual: true` to get both cropped and full images as base64 in a single JSON response.

---

## Integration with PBG

In your PBG `.env`, set:

```env
SCREENSHOT_SERVICE_URL=http://<ss_service_host>:8000
```

If `API_KEY` is set in ss_service, also add:

```env
SCREENSHOT_SERVICE_API_KEY=<your-key>
```

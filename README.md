# Screenshot Service (`ss_service`)

Headless browser screenshot microservice for [Punch Site QA](../punch-siteqa-backend). The embed widget never takes its own screenshots — it asks the backend, which proxies to this service. Built with FastAPI + Playwright/Chromium.

---

## Run locally

```bash
# Docker (recommended)
cp .env.example .env
docker compose up --build

# Manual
pip install -r requirements.txt
playwright install chromium
python run.py
```

Serves on `:8000`. Swagger at `/docs`, healthcheck at `/health`.

**Docker note:** `shm_size: 1gb` is set in `docker-compose.yml` and is required — Chromium crashes without it. Only one Uvicorn worker by design (the Playwright browser singleton is not thread-safe); scale horizontally with replicas.

---

## Deploy

Runs on its own DigitalOcean droplet. On the droplet:

```bash
cd /root/ss_service
git pull origin main
docker compose up -d --build
```

The backend reaches it via `SCREENSHOT_SERVICE_URL` env var, with `SCREENSHOT_SERVICE_API_KEY` for auth.

---

## Env vars

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_MAX_CONCURRENT` | `5` | Max simultaneous Chromium contexts |
| `BROWSER_TIMEOUT_MS` | `30000` | Navigation timeout |
| `BROWSER_ENGINE` | `chromium` | `chromium` / `firefox` / `webkit` |
| `DEFAULT_VIEWPORT_WIDTH/HEIGHT` | `1280` / `720` | Viewport size |
| `DEFAULT_JPEG_QUALITY` | `80` | JPEG quality 1–100 |
| `TASK_CROP_WIDTH/HEIGHT` | `600` / `400` | Crop window around the pin (only used when `highlight=false`) |
| `API_KEY` | *(empty)* | If set, requests need `X-API-Key` header. Empty = open (rely on network ACL) |
| `RATE_LIMIT_ENABLED` | `true` | Per-IP sliding window |
| `RATE_LIMIT_REQUESTS` | `60` | Per `RATE_LIMIT_WINDOW_SECONDS` |
| `RATE_LIMIT_WINDOW_SECONDS` | `60` | Window duration |

---

## Endpoints

### `GET /health`
`{ "status": "ok" }`. Used by docker healthcheck.

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
Returns binary `image/jpeg` (or `png`).

### `POST /screenshot/task`
Screenshot focused on a `(x, y)` pin. Used by the Punch backend for every embed-widget bug report.

```json
{
  "url": "https://example.com",
  "viewport": { "width": 1280, "height": 720 },
  "x": 400,
  "y": 300,
  "scroll": { "x": 0, "y": 0 },

  "selector": "main .hero .cta",
  "element_rect": { "top": 100, "left": 80, "width": 200, "height": 60 },

  "highlight": true,
  "crop_size": { "width": 1280, "height": 720 },

  "delay_ms": 3000,
  "format": "jpeg",
  "quality": 85
}
```

Fields beyond the basic `url`/`x`/`y`:
- **`selector` + `element_rect`** — scroll-drift correction for sites with custom scrollers (Lenis, Locomotive, libraries that hijack `window.scroll`). If the captured element ends up at a different on-screen position than `element_rect` claimed, the engine re-aligns before cropping.
- **`highlight: true`** — skips the default crop around `(x, y)` and instead draws a pin on top of the full viewport, returning the full viewport-sized image. Default behavior in the Punch flow.
- **`crop_size`** — defensive override that matches the viewport (so a future change to default crop behavior doesn't silently start cropping highlighted captures).
- **`delay_ms`** — settle time after navigation. The backend floors this at 3000 ms; the engine itself runs `_freeze_animations()` (advances all WAAPI animations to their final keyframe and disables `requestAnimationFrame`) right before capture so the result is deterministic regardless of how long we waited.
- **`format`** / **`quality`** — output controls.

Returns binary image. `dual: true` mode returns JSON `{ cropped, full }` as base64 — not used by the current Punch flow but kept for legacy / debugging.

---

## How the engine handles tricky pages

- **URL allowlist** — `app/security.py:validate_url()` blocks loopback and private IPs to prevent SSRF.
- **Animation freeze** — every WAAPI animation is fast-forwarded to its end keyframe and `requestAnimationFrame` is replaced with a no-op right before screenshot. This makes captures deterministic on pages with heavy GSAP / Framer Motion / CSS keyframes.
- **Custom-scroll sites** — the backend tells us the precise `(scroll.x, scroll.y)` the user saw plus the `element_rect` they clicked on. We scroll to that position, then verify the element landed where the client said it would; if not, we nudge before cropping.
- **Lazy-loaded content** — `delay_ms` (floored at 3000 ms by the backend) is your knob if a site streams content after `domcontentloaded`.

---

## Project structure

```
app/
├── main.py        FastAPI app, lifespan (browser start/stop), endpoints
├── screenshot.py  ScreenshotEngine — Playwright logic, animation freeze, drift correction
├── schemas.py     Pydantic request/response models
├── config.py      Settings (env vars with defaults)
├── security.py    URL validation
└── middleware.py  Rate limiting (in-memory sliding window per IP)
run.py             Manual entry point
Dockerfile         Multi-stage: builder → slim runtime, non-root user
docker-compose.yml shm_size: 1gb (critical for Chromium)
```

---

## Integration

The Punch backend calls this service from `src/routes/embed.ts:281` (`POST /embed/screenshot`). The widget never reaches this service directly — the backend signs and rate-limits all calls.

```env
# punch-siteqa-backend/.env
SCREENSHOT_SERVICE_URL=http://<ss_service_host>:8000
SCREENSHOT_SERVICE_API_KEY=<value of API_KEY here>
```

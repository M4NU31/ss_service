# Changelog

All notable changes to Screenshot Service are documented here.

---

## [Unreleased]

---

## [1.0.0] — 2026-04-10

### Added
- Initial release as standalone Docker service
- FastAPI app with Playwright/Chromium headless browser
- `POST /screenshot/page` — full-page or viewport screenshot
- `POST /screenshot/task` — crop or highlight around a `(x, y)` coordinate with optional dual mode (crop + full viewport as base64 JSON)
- `GET /health` — liveness probe for container healthchecks
- Optional API key authentication via `X-API-Key` header
- Configurable rate limiting (requests per window)
- Multi-stage `Dockerfile` — slim Python 3.12 runtime with pre-installed Chromium at fixed path
- `docker-compose.yml` with `shm_size: 1gb` for Chromium stability and healthcheck
- `.env.example` with all configurable options documented
- `README.md` with setup, API reference, and PBG integration guide

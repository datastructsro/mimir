"""Foreqcast Server — minimal FastAPI application.

Serves forecast Parquet files with API key authentication.

Endpoints:
    GET /health                     — healthcheck (no auth)
    GET /forecasts                  — list available forecast files
    GET /forecasts/{filename}       — download a specific forecast parquet file
    GET /forecasts/latest           — download the most recently modified forecast file
"""

from __future__ import annotations

import logging
from pathlib import Path

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse

from .auth import get_api_key_validator
from .config import ServerConfig, load_config

logger = logging.getLogger(__name__)

app = FastAPI(
    title="foreqcast-server",
    description="Minimal forecast Parquet server with API key auth",
    version="0.1.0",
)

# Resolved at startup
_config: ServerConfig | None = None
_require_api_key = None


@app.on_event("startup")
async def _startup() -> None:
    global _config, _require_api_key
    _config = load_config()
    _require_api_key = get_api_key_validator(_config)
    logger.info("Serving forecasts from %s", _config.forecast_dir)
    logger.info("Loaded %d API keys", len(_config.api_keys))


def _get_config() -> ServerConfig:
    assert _config is not None
    return _config


def _get_auth():
    assert _require_api_key is not None
    return _require_api_key


# ── Health ──────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, str]:
    """Healthcheck endpoint (no auth required)."""
    return {"status": "ok"}


# ── Forecasts ───────────────────────────────────────────────────────────

@app.get("/forecasts")
async def list_forecasts(
    _api_key: str = Depends(_get_auth()),
    config: ServerConfig = Depends(_get_config),
) -> JSONResponse:
    """List available forecast Parquet files."""
    files = sorted(config.forecast_dir.glob("*.parquet"))
    return JSONResponse(
        content={
            "files": [
                {
                    "name": f.name,
                    "size_bytes": f.stat().st_size,
                    "modified": f.stat().st_mtime,
                }
                for f in files
            ]
        }
    )


@app.get("/forecasts/latest")
async def download_latest_forecast(
    _api_key: str = Depends(_get_auth()),
    config: ServerConfig = Depends(_get_config),
) -> FileResponse:
    """Download the most recently modified forecast Parquet file."""
    files = list(config.forecast_dir.glob("*.parquet"))
    if not files:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No forecast files available",
        )
    latest = max(files, key=lambda f: f.stat().st_mtime)
    return FileResponse(
        path=str(latest),
        media_type="application/octet-stream",
        filename=latest.name,
    )


@app.get("/forecasts/{filename}")
async def download_forecast(
    filename: str,
    _api_key: str = Depends(_get_auth()),
    config: ServerConfig = Depends(_get_config),
) -> FileResponse:
    """Download a specific forecast Parquet file by name."""
    # Sanitize: prevent path traversal
    safe_name = Path(filename).name
    filepath = config.forecast_dir / safe_name

    if not filepath.exists() or not filepath.suffix == ".parquet":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Forecast file not found: {safe_name}",
        )

    return FileResponse(
        path=str(filepath),
        media_type="application/octet-stream",
        filename=safe_name,
    )


# ── Entrypoint ──────────────────────────────────────────────────────────

def run() -> None:
    """CLI entrypoint for foreqcast-server."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Validate config eagerly so we fail fast
    cfg = load_config()
    uvicorn.run(
        "foreqcast_server.app:app",
        host=cfg.host,
        port=cfg.port,
        log_level="info",
    )

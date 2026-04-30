"""API key authentication dependency for FastAPI."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

if TYPE_CHECKING:
    from .config import ServerConfig

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def get_api_key_validator(config: ServerConfig):
    """Create a FastAPI dependency that validates the X-API-Key header."""

    async def _validate(api_key: str | None = Security(_api_key_header)) -> str:
        if api_key is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Missing X-API-Key header",
            )
        if api_key not in config.api_keys:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid API key",
            )
        return api_key

    return _validate

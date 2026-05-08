"""HTTP client helpers for warehouse-scoped mimir-server artifacts."""

from __future__ import annotations

import io
import json
import logging
import re
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

import pyarrow as pa
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

_WAREHOUSE_ARTIFACT_RE = re.compile(r"/warehouses/[^/]+/(?:rules|forecast)/latest$")


def normalize_server_base_uri(uri: str) -> str:
    """Normalize a configured server URI to the artifact server root."""
    raw_uri = uri.strip()
    if not raw_uri:
        return ""

    parts = urlsplit(raw_uri)
    path = parts.path.rstrip("/")
    path = _WAREHOUSE_ARTIFACT_RE.sub("", path)
    for suffix in (
        "/forecasts/latest",
        "/forecasts",
        "/forecast/latest",
        "/rules/latest",
        "/lead-times/latest",
        "/lead-times",
        "/warehouses",
    ):
        if path.endswith(suffix):
            path = path[: -len(suffix)]
            break

    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


def warehouse_aliases(selected_warehouse_id: int | None, warehouse_code: str | None = None) -> list[str]:
    """Return the possible identifiers an artifact may use for the warehouse."""
    aliases: list[str] = []
    if selected_warehouse_id is not None:
        aliases.append(str(selected_warehouse_id))

    if warehouse_code:
        normalized_code = warehouse_code.strip()
        if normalized_code and normalized_code not in aliases:
            aliases.append(normalized_code)

    return aliases


def _decode_error_detail(exc: HTTPError) -> str:
    body = exc.read()
    if not body:
        return ""

    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body.decode("utf-8", errors="replace").strip()

    detail = payload.get("detail")
    if isinstance(detail, str):
        return detail

    return json.dumps(payload, sort_keys=True)


@dataclass(frozen=True)
class ServerPreflight:
    """Result of a remote server preflight check."""

    health_status: str
    available_warehouses: list[str]
    selected_warehouse_ref: str


class MimirServerClient:
    """Minimal HTTP client for mimir-server endpoints."""

    def __init__(self, base_uri: str, api_key: str, *, timeout: int = 60) -> None:
        normalized = normalize_server_base_uri(base_uri)
        if not normalized:
            raise ValueError("external_forecast_uri must be configured when using the remote server")

        self.base_uri = normalized
        self.api_key = api_key.strip()
        self.timeout = timeout

    def _build_url(self, path: str) -> str:
        return f"{self.base_uri}{path}"

    def _request_bytes(self, path: str, *, require_auth: bool = True) -> bytes:
        if require_auth and not self.api_key:
            raise ValueError("external_forecast_api_key must be configured when using the remote server")

        uri = self._build_url(path)
        req = Request(uri)
        if self.api_key:
            req.add_header("X-API-Key", self.api_key)

        try:
            with urlopen(req, timeout=self.timeout) as resp:  # noqa: S310
                return resp.read()
        except HTTPError as exc:
            detail = _decode_error_detail(exc)
            if detail:
                raise ValueError(f"Remote server request failed for {uri}: HTTP {exc.code} - {detail}") from exc
            raise ValueError(f"Remote server request failed for {uri}: HTTP {exc.code}") from exc
        except URLError as exc:
            raise ValueError(f"Could not reach remote server at {self.base_uri}: {exc.reason}") from exc

    def _request_json(
        self,
        path: str,
        *,
        require_auth: bool = True,
    ) -> dict[str, object]:
        payload = self._request_bytes(path, require_auth=require_auth)
        try:
            data = json.loads(payload.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"Remote server returned invalid JSON for {self._build_url(path)}") from exc

        if not isinstance(data, dict):
            raise ValueError(f"Remote server returned unexpected JSON for {self._build_url(path)}")
        return data

    def _request_table(self, path: str) -> pa.Table:
        payload = self._request_bytes(path)
        try:
            return pq.read_table(io.BytesIO(payload))
        except Exception as exc:
            raise ValueError(f"Remote server returned invalid parquet for {self._build_url(path)}") from exc

    def health(self) -> str:
        """Return the server health status string."""
        payload = self._request_json("/health", require_auth=False)
        status = payload.get("status")
        if not isinstance(status, str) or not status:
            raise ValueError(f"Remote server returned invalid health response for {self._build_url('/health')}")
        return status

    def list_warehouses(self) -> list[str]:
        """List warehouse identifiers published by the server."""
        payload = self._request_json("/warehouses")
        warehouses = payload.get("warehouses")
        if not isinstance(warehouses, list) or not all(isinstance(item, str) for item in warehouses):
            raise ValueError(f"Remote server returned invalid warehouse list for {self._build_url('/warehouses')}")
        return warehouses

    def resolve_warehouse_ref(
        self,
        *,
        selected_warehouse_id: int | None,
        warehouse_code: str | None = None,
        available_warehouses: list[str] | None = None,
    ) -> str:
        """Resolve the remote warehouse identifier used by published artifacts."""
        warehouses = available_warehouses if available_warehouses is not None else self.list_warehouses()
        if not warehouses:
            raise ValueError("Remote server is healthy but has no published warehouse-scoped artifacts")

        aliases = warehouse_aliases(selected_warehouse_id, warehouse_code)
        for alias in aliases:
            if alias in warehouses:
                return alias

        if selected_warehouse_id is None and len(warehouses) == 1:
            return warehouses[0]

        available_text = ", ".join(warehouses)
        warehouse_suffix = f" ({warehouse_code})" if warehouse_code else ""
        if selected_warehouse_id is None:
            raise ValueError(f"Select a warehouse before running Mimir. Available facilities: {available_text}")

        raise ValueError(
            "Selected warehouse "
            f"{selected_warehouse_id}{warehouse_suffix} is not published by the remote server. "
            f"Available facilities: {available_text}"
        )

    def preflight_warehouse(
        self,
        *,
        selected_warehouse_id: int | None,
        warehouse_code: str | None = None,
    ) -> ServerPreflight:
        """Validate server health and warehouse publication before starting a run."""
        health_status = self.health()
        if health_status != "ok":
            raise ValueError(
                f"Remote server health check failed for {self._build_url('/health')}: status={health_status}"
            )

        warehouses = self.list_warehouses()
        selected_ref = self.resolve_warehouse_ref(
            selected_warehouse_id=selected_warehouse_id,
            warehouse_code=warehouse_code,
            available_warehouses=warehouses,
        )
        return ServerPreflight(
            health_status=health_status,
            available_warehouses=warehouses,
            selected_warehouse_ref=selected_ref,
        )

    def download_rules_table(self, warehouse_ref: str) -> pa.Table:
        """Download the latest rules artifact for a warehouse."""
        return self._request_table(f"/warehouses/{warehouse_ref}/rules/latest")

    def download_forecast_table(self, warehouse_ref: str) -> pa.Table:
        """Download the latest forecast evidence artifact for a warehouse."""
        return self._request_table(f"/warehouses/{warehouse_ref}/forecast/latest")

    def download_lead_times_table(self) -> pa.Table:
        """Download the latest empirical lead times artifact."""
        return self._request_table("/lead-times/latest")

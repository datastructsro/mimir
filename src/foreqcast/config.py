"""SQLite-backed configuration for foreqcast.

Stores global settings, per-category overrides, per-product overrides,
and run history with per-product audit trail.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict

DEFAULT_DB_PATH = Path("foreqcast_config.db")


class CategoryOverride(TypedDict):
    safety_factor: float | None
    min_data_points: int | None
    lead_time_override_days: int | None
    review_period_override_days: int | None


class ProductOverride(TypedDict):
    safety_factor: float | None
    min_qty_floor: float | None
    max_qty_ceiling: float | None
    lead_time_override_days: int | None
    excluded: bool

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS category_overrides (
    category_id            INTEGER PRIMARY KEY,
    category_name          TEXT,
    safety_factor          REAL,
    min_data_points        INTEGER,
    lead_time_override_days INTEGER,
    review_period_override_days INTEGER,
    excluded               INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS product_overrides (
    product_id    INTEGER PRIMARY KEY,
    product_name  TEXT,
    safety_factor REAL,
    min_qty_floor REAL,
    max_qty_ceiling REAL,
    lead_time_override_days INTEGER,
    excluded      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS forecast_runs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date             TEXT NOT NULL,
    parquet_source_dir   TEXT,
    products_analyzed    INTEGER DEFAULT 0,
    products_forecasted  INTEGER DEFAULT 0,
    rules_created        INTEGER DEFAULT 0,
    rules_updated        INTEGER DEFAULT 0,
    rules_skipped        INTEGER DEFAULT 0,
    duration_seconds     REAL,
    status               TEXT DEFAULT 'running',
    error_message        TEXT
);

CREATE TABLE IF NOT EXISTS forecast_results (
    run_id               INTEGER REFERENCES forecast_runs(id),
    product_id           INTEGER,
    warehouse_id         INTEGER,
    avg_daily_demand     REAL,
    trend_slope          REAL,
    r_squared            REAL,
    data_points          INTEGER,
    forecasted_daily     REAL,
    computed_min_qty     REAL,
    computed_max_qty     REAL,
    lead_time_days       INTEGER,
    safety_factor_used   REAL,
    action               TEXT,
    PRIMARY KEY (run_id, product_id, warehouse_id)
);
"""

DEFAULTS = {
    "forecast_horizon_days": ("30", "Number of days to project demand forward"),
    "time_bucket": ("daily", "Aggregation bucket: 'daily' or 'weekly'"),
    "min_data_points": ("4", "Minimum observations to fit regression"),
    "service_level": ("0.85", "Target service level as quantile (0.0-1.0, e.g. 0.85 = 85% fill rate)"),
    "review_period_days": ("7", "Days between replenishment reviews (affects max_qty)"),
    "default_lead_time_days": ("7", "Fallback lead time when no supplier info exists"),
    "min_demand_threshold": ("0.1", "Skip products with avg daily demand below this"),
    "odoo_url": ("", "Odoo instance URL for pushing orderpoints"),
    "odoo_db": ("", "Odoo database name"),
    "odoo_user": ("", "Odoo API username"),
    "odoo_password": ("", "Odoo API password or key"),
    # Inventory position settings
    "inventory_mode": ("ignore", "ignore=forecast only, analyze=add inventory analysis, adjust=modify replenishment behavior"),
    "respect_reservations": ("true", "Subtract reserved_qty from on-hand (true=conservative, false=optimistic)"),
    "include_incoming_supply": ("true", "Count open PO/in-transit quantities as available"),
    "include_outgoing_demand": ("false", "Subtract confirmed SO quantities (false=avoid double-counting with forecast)"),
    "incoming_sources": ("purchase_orders,incoming_moves", "Comma-separated: purchase_orders, incoming_moves, manufacturing_orders"),
    "outgoing_sources": ("sale_orders", "Comma-separated: sale_orders, outgoing_moves, manufacturing_consumption"),
    "overstock_threshold_days": ("90", "Products with coverage above this are flagged as overstock"),
    "understock_threshold_days": ("3", "Products with coverage below this are flagged as understock"),
    "overstock_skip": ("true", "In adjust mode, skip creating orderpoints for overstocked products"),
    "understock_service_level_bump": ("0.03", "In adjust mode, raise service_level by this for understocked products"),
    # Forecast Source
    "forecast_source": ("internal", "Source of forecasts: 'internal' (linear regression) or 'external'"),
    "external_forecast_uri": ("", "Path or URI to external forecast parquet file (if forecast_source=external)"),
    "external_forecast_api_key": ("", "API key for HTTP-based external forecast server (sent as X-API-Key header)"),
}


@dataclass
class ForeqcastConfig:
    """Resolved configuration values for a single run."""

    forecast_horizon_days: int = 30
    time_bucket: str = "daily"
    min_data_points: int = 4
    service_level: float = 0.85
    review_period_days: int = 7
    default_lead_time_days: int = 7
    min_demand_threshold: float = 0.1
    odoo_url: str = ""
    odoo_db: str = ""
    odoo_user: str = ""
    odoo_password: str = ""

    # Inventory position settings
    inventory_mode: str = "ignore"  # ignore | analyze | adjust
    respect_reservations: bool = True
    include_incoming_supply: bool = True
    include_outgoing_demand: bool = False
    incoming_sources: set[str] = field(default_factory=lambda: {"purchase_orders", "incoming_moves"})
    outgoing_sources: set[str] = field(default_factory=lambda: {"sale_orders"})
    overstock_threshold_days: float = 90.0
    understock_threshold_days: float = 3.0
    overstock_skip: bool = True
    understock_service_level_bump: float = 0.03

    # Forecast Source
    forecast_source: str = "internal"
    external_forecast_uri: str = ""
    external_forecast_api_key: str = ""

    # Loaded from override tables
    category_overrides: dict[int, CategoryOverride] = field(default_factory=dict)
    product_overrides: dict[int, ProductOverride] = field(default_factory=dict)


def init_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Create config database and seed defaults."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript(SCHEMA_SQL)

    for key, (default_value, description) in DEFAULTS.items():
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)",
            (key, default_value, description),
        )
    conn.commit()
    return conn


def load_config(conn: sqlite3.Connection) -> ForeqcastConfig:
    """Load all settings and overrides into a ForeqcastConfig."""
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    kv = dict(rows)

    config = ForeqcastConfig(
        forecast_horizon_days=int(kv.get("forecast_horizon_days", "30")),
        time_bucket=kv.get("time_bucket", "daily"),
        min_data_points=int(kv.get("min_data_points", "4")),
        service_level=float(kv.get("service_level", "0.95")),
        review_period_days=int(kv.get("review_period_days", "7")),
        default_lead_time_days=int(kv.get("default_lead_time_days", "7")),
        min_demand_threshold=float(kv.get("min_demand_threshold", "0.1")),
        odoo_url=kv.get("odoo_url", ""),
        odoo_db=kv.get("odoo_db", ""),
        odoo_user=kv.get("odoo_user", ""),
        odoo_password=kv.get("odoo_password", ""),
        # Inventory settings
        inventory_mode=kv.get("inventory_mode", "ignore"),
        respect_reservations=kv.get("respect_reservations", "true").lower() == "true",
        include_incoming_supply=kv.get("include_incoming_supply", "true").lower() == "true",
        include_outgoing_demand=kv.get("include_outgoing_demand", "false").lower() == "true",
        incoming_sources=set(kv.get("incoming_sources", "purchase_orders,incoming_moves").split(",")),
        outgoing_sources=set(kv.get("outgoing_sources", "sale_orders").split(",")),
        overstock_threshold_days=float(kv.get("overstock_threshold_days", "90")),
        understock_threshold_days=float(kv.get("understock_threshold_days", "3")),
        overstock_skip=kv.get("overstock_skip", "true").lower() == "true",
        understock_service_level_bump=float(kv.get("understock_service_level_bump", "0.03")),
        forecast_source=kv.get("forecast_source", "internal"),
        external_forecast_uri=kv.get("external_forecast_uri", ""),
        external_forecast_api_key=kv.get("external_forecast_api_key", ""),
    )

    # Load category overrides
    for row in conn.execute("SELECT * FROM category_overrides WHERE excluded = 0").fetchall():
        config.category_overrides[row[0]] = {
            "safety_factor": row[2],
            "min_data_points": row[3],
            "lead_time_override_days": row[4],
            "review_period_override_days": row[5],
        }

    # Load product overrides
    for row in conn.execute("SELECT * FROM product_overrides").fetchall():
        config.product_overrides[row[0]] = {
            "safety_factor": row[2],
            "min_qty_floor": row[3],
            "max_qty_ceiling": row[4],
            "lead_time_override_days": row[5],
            "excluded": bool(row[6]),
        }

    return config


def get_excluded_product_ids(conn: sqlite3.Connection) -> set[int]:
    """Return set of product IDs that are excluded from forecasting."""
    rows = conn.execute("SELECT product_id FROM product_overrides WHERE excluded = 1").fetchall()
    return {r[0] for r in rows}


def get_excluded_category_ids(conn: sqlite3.Connection) -> set[int]:
    """Return set of category IDs that are excluded from forecasting."""
    rows = conn.execute("SELECT category_id FROM category_overrides WHERE excluded = 1").fetchall()
    return {r[0] for r in rows}


def start_run(conn: sqlite3.Connection, parquet_dir: str) -> int:
    """Record a new forecast run, return run_id."""
    from datetime import datetime, timezone

    cursor = conn.execute(
        "INSERT INTO forecast_runs (run_date, parquet_source_dir) VALUES (?, ?)",
        (datetime.now(timezone.utc).isoformat(), parquet_dir),
    )
    conn.commit()
    run_id = cursor.lastrowid
    if run_id is None:
        raise RuntimeError("INSERT did not yield a row id for forecast_runs")
    return run_id


def finish_run(conn: sqlite3.Connection, run_id: int, **stats):
    """Update a forecast run with final stats."""
    sets = ", ".join(f"{k} = ?" for k in stats)
    vals = list(stats.values()) + [run_id]
    conn.execute(f"UPDATE forecast_runs SET {sets} WHERE id = ?", vals)
    conn.commit()

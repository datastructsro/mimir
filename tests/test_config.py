"""Tests for SQLite configuration management."""

import tempfile
from pathlib import Path

from foreqcast.config import (
    DEFAULTS,
    finish_run,
    get_excluded_category_ids,
    get_excluded_product_ids,
    init_db,
    load_config,
    start_run,
)


def _init_temp_db():
    """Create a temp SQLite DB and return (conn, path)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    conn = init_db(Path(tmp.name))
    return conn, Path(tmp.name)


def test_init_db_creates_tables():
    conn, _ = _init_temp_db()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {t[0] for t in tables}
    assert "settings" in table_names
    assert "category_overrides" in table_names
    assert "product_overrides" in table_names
    assert "forecast_runs" in table_names
    assert "forecast_results" in table_names


def test_init_db_seeds_all_defaults():
    conn, _ = _init_temp_db()
    count = conn.execute("SELECT count(*) FROM settings").fetchone()[0]
    assert count == len(DEFAULTS)


def test_load_config_reads_all_settings():
    conn, _ = _init_temp_db()
    config = load_config(conn)
    assert config.forecast_horizon_days == 30
    assert config.time_bucket == "daily"
    assert config.service_level == 0.85
    assert config.min_data_points == 4
    assert config.review_period_days == 7
    assert config.default_lead_time_days == 7
    assert config.min_demand_threshold == 0.1
    assert config.inventory_mode == "ignore"
    assert config.respect_reservations is True
    assert config.include_incoming_supply is True
    assert config.include_outgoing_demand is False
    assert config.overstock_threshold_days == 90.0
    assert config.understock_threshold_days == 3.0
    assert config.overstock_skip is True
    assert config.understock_service_level_bump == 0.03


def test_incoming_sources_parsed_as_set():
    conn, _ = _init_temp_db()
    conn.execute(
        "UPDATE settings SET value = ? WHERE key = ?",
        ("purchase_orders,incoming_moves,manufacturing_orders", "incoming_sources"),
    )
    conn.commit()
    config = load_config(conn)
    assert config.incoming_sources == {"purchase_orders", "incoming_moves", "manufacturing_orders"}


def test_boolean_parsing():
    conn, _ = _init_temp_db()
    # Set to false
    conn.execute("UPDATE settings SET value = 'false' WHERE key = 'respect_reservations'")
    conn.commit()
    config = load_config(conn)
    assert config.respect_reservations is False

    # Set back to True (mixed case)
    conn.execute("UPDATE settings SET value = 'True' WHERE key = 'respect_reservations'")
    conn.commit()
    config = load_config(conn)
    assert config.respect_reservations is True


def test_get_excluded_product_ids():
    conn, _ = _init_temp_db()
    conn.execute(
        "INSERT INTO product_overrides (product_id, product_name, excluded) VALUES (?, ?, ?)",
        (42, "Excluded Product", 1),
    )
    conn.execute(
        "INSERT INTO product_overrides (product_id, product_name, excluded) VALUES (?, ?, ?)",
        (99, "Normal Product", 0),
    )
    conn.commit()
    excluded = get_excluded_product_ids(conn)
    assert excluded == {42}


def test_get_excluded_category_ids():
    conn, _ = _init_temp_db()
    conn.execute(
        "INSERT INTO category_overrides (category_id, category_name, excluded) VALUES (?, ?, ?)",
        (5, "Dead Stock", 1),
    )
    conn.commit()
    excluded = get_excluded_category_ids(conn)
    assert excluded == {5}


def test_start_and_finish_run():
    conn, _ = _init_temp_db()
    run_id = start_run(conn, "/tmp/parqcast")
    assert run_id is not None

    finish_run(conn, run_id, status="complete", products_analyzed=100, rules_created=50)

    row = conn.execute(
        "SELECT status, products_analyzed, rules_created FROM forecast_runs WHERE id = ?", (run_id,)
    ).fetchone()
    assert row[0] == "complete"
    assert row[1] == 100
    assert row[2] == 50


def test_init_db_idempotent():
    """Calling init_db twice on the same path should not error or duplicate settings."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)

    conn1 = init_db(path)
    count1 = conn1.execute("SELECT count(*) FROM settings").fetchone()[0]

    conn2 = init_db(path)
    count2 = conn2.execute("SELECT count(*) FROM settings").fetchone()[0]

    assert count1 == count2 == len(DEFAULTS)

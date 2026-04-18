"""Command-line interface for foreqcast."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import DEFAULT_DB_PATH, init_db, load_config
from .pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(
        prog="foreqcast",
        description="Linear regression demand forecaster for parqcast exports",
    )
    parser.add_argument("input_dir", help="Directory containing parqcast parquet exports")
    parser.add_argument("output_dir", help="Directory to write forecast parquet files")
    parser.add_argument("--config-db", default=str(DEFAULT_DB_PATH), help="Path to SQLite config database")
    parser.add_argument("--push", action="store_true", help="Push replenishment rules to Odoo after writing parquet")
    parser.add_argument("--excel", action="store_true", help="Also export an Odoo-importable .xlsx file for manual review and import")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    input_dir = Path(args.input_dir)
    if not input_dir.is_dir():
        print(f"Error: input directory does not exist: {input_dir}", file=sys.stderr)
        sys.exit(1)

    conn = init_db(Path(args.config_db))
    config = load_config(conn)

    stats = run_pipeline(
        parquet_input_dir=input_dir,
        parquet_output_dir=args.output_dir,
        config=config,
        db_conn=conn,
        push_to_odoo=args.push,
    )

    # Excel export
    if args.excel:
        from .excel_export import export_to_excel

        rules_path = Path(args.output_dir) / "replenishment_rules.parquet"
        forecast_path = Path(args.output_dir) / "forecast.parquet"
        xlsx_path = export_to_excel(
            rules_parquet=rules_path,
            forecast_parquet=forecast_path if forecast_path.exists() else None,
        )
        print(f"\nExcel export: {xlsx_path}")
        print("  Sheet 'Odoo Import' — upload directly to Odoo via Import button")
        print("  Sheet 'Review' — human-readable with forecasts and inventory flags")

    print(f"\nForecast complete:")
    print(f"  Products analyzed:  {stats['products_analyzed']}")
    print(f"  Products forecasted: {stats['products_forecasted']}")
    print(f"  Rules to create:    {stats['rules_created']}")
    print(f"  Rules to update:    {stats['rules_updated']}")
    print(f"  Rules skipped:      {stats['rules_skipped']}")
    inv_mode = stats.get("inventory_mode")
    if inv_mode and inv_mode != "ignore":
        print(f"  Inventory mode:     {inv_mode}")
        print(f"  Positions loaded:   {stats.get('inventory_positions_loaded', 0)}")
        print(f"  Overstocked:        {stats.get('overstock_count', 0)}")
        print(f"  Understocked:       {stats.get('understock_count', 0)}")
    print(f"  Duration:           {stats['duration_seconds']}s")


if __name__ == "__main__":
    main()

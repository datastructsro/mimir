"""Command-line interface for mimir."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .config import MimirConfig
from .excel_export import export_forecast_evidence_to_excel, export_to_excel
from .pipeline import run_pipeline


def main():
    parser = argparse.ArgumentParser(
        prog="mimir",
        description="Warehouse-scoped replenishment rule importer for parqcast exports",
    )
    parser.add_argument("input_dir", help="Directory containing parqcast parquet exports")
    parser.add_argument("output_dir", help="Directory to write rule and evidence outputs")

    parser.add_argument(
        "--excel",
        action="store_true",
        help="Also export an Odoo-importable .xlsx file for manual review and import",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument("--warehouse", type=int, help="Odoo warehouse ID to import for this run")
    parser.add_argument(
        "--external-uri",
        help="Optional base URL of a warehouse-scoped rules server, e.g. https://mimir.datastruct.tech",
    )
    parser.add_argument("--external-api-key", help="API key for the external rules server")

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

    config = MimirConfig(
        selected_warehouse_id=args.warehouse,
        external_forecast_uri=args.external_uri or "",
        external_forecast_api_key=args.external_api_key or "",
    )

    stats = run_pipeline(
        parquet_input_dir=input_dir,
        parquet_output_dir=args.output_dir,
        config=config,
    )

    # Excel export
    if args.excel:
        rules_path = Path(args.output_dir) / "replenishment_rules.parquet"
        xlsx_path = export_to_excel(
            rules_parquet=rules_path,
            decisions_parquet=Path(args.output_dir) / "decisions.parquet",
        )
        print(f"\nExcel export: {xlsx_path}")
        print("  Sheet 'Odoo Import' — upload directly to Odoo via Import button")
        print("  Sheet 'Review' — human-readable rule review")

        forecast_path = Path(args.output_dir) / "forecast_evidence.parquet"
        if forecast_path.exists():
            forecast_xlsx_path = export_forecast_evidence_to_excel(forecast_path)
            print(f"Forecast evidence workbook: {forecast_xlsx_path}")

    print("\nReplenishment import complete:")
    print(f"  Warehouse:          {stats.get('warehouse_id', 'n/a')}")
    print(f"  Rules imported:     {stats['products_analyzed']}")
    print(f"  Forecast rows:      {stats['products_forecasted']}")
    print(f"  Rules to create:    {stats['rules_created']}")
    print(f"  Rules to update:    {stats['rules_updated']}")
    print(f"  Rules skipped:      {stats['rules_skipped']}")
    print(f"  Duration:           {stats['duration_seconds']}s")


if __name__ == "__main__":
    main()

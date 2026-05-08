"""Command-line interface for the remote-only Mimir import flow."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import MimirConfig
from .excel_export import export_forecast_evidence_to_excel, export_to_excel
from .importer import (
    fetch_remote_empirical_lead_times_table,
    fetch_remote_forecast_evidence_table,
    fetch_remote_minmax_table,
)
from .pipeline import run_pipeline
from .runtime_context import build_runtime_context_from_xmlrpc


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="mimir",
        description="Warehouse-scoped replenishment rule importer for mimir-server artifacts",
    )
    parser.add_argument("output_dir", help="Directory to write rule and evidence outputs")

    parser.add_argument(
        "--excel",
        action="store_true",
        help="Also export an Odoo-importable .xlsx file for manual review and import",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    parser.add_argument("--warehouse", type=int, required=True, help="Odoo warehouse ID to import for this run")
    parser.add_argument(
        "--external-uri",
        required=True,
        help="Base URL of the warehouse-scoped rules server, e.g. https://mimir.datastruct.tech",
    )
    parser.add_argument("--external-api-key", required=True, help="API key for the external rules server")
    parser.add_argument("--odoo-url", required=True, help="Base URL of the target Odoo instance")
    parser.add_argument("--odoo-db", required=True, help="Odoo database name")
    parser.add_argument("--odoo-user", required=True, help="Odoo login")
    parser.add_argument("--odoo-password", required=True, help="Odoo password")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    config = MimirConfig(
        selected_warehouse_id=args.warehouse,
        external_forecast_uri=args.external_uri,
        external_forecast_api_key=args.external_api_key,
        odoo_url=args.odoo_url,
        odoo_db=args.odoo_db,
        odoo_user=args.odoo_user,
        odoo_password=args.odoo_password,
    )

    # Build minimal Odoo metadata only after the selected warehouse's rules are known.
    minmax_table = fetch_remote_minmax_table(config)
    runtime_context = build_runtime_context_from_xmlrpc(
        odoo_url=args.odoo_url,
        odoo_db=args.odoo_db,
        odoo_user=args.odoo_user,
        odoo_password=args.odoo_password,
        selected_warehouse_id=args.warehouse,
        minmax_table=minmax_table,
    )
    forecast_evidence = fetch_remote_forecast_evidence_table(
        config,
        selected_warehouse_code=runtime_context.warehouse_code,
    )
    empirical_lead_times = fetch_remote_empirical_lead_times_table(config)

    output_dir = Path(args.output_dir)
    stats = run_pipeline(
        parquet_output_dir=output_dir,
        config=config,
        runtime_context=runtime_context,
        minmax_table=minmax_table,
        forecast_evidence=forecast_evidence,
        empirical_lead_times=empirical_lead_times,
    )

    if args.excel:
        rules_path = output_dir / "replenishment_rules.parquet"
        xlsx_path = export_to_excel(
            rules_parquet=rules_path,
            decisions_parquet=output_dir / "decisions.parquet",
        )
        print(f"\nExcel export: {xlsx_path}")
        print("  Sheet 'Odoo Import' — upload directly to Odoo via Import button")
        print("  Sheet 'Review' — human-readable rule review")

        forecast_path = output_dir / "forecast_evidence.parquet"
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

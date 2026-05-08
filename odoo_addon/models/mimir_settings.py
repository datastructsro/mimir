import base64
import logging
import tempfile
import uuid
from pathlib import Path

from odoo import api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)


class MimirSettings(models.TransientModel):
    _inherit = "res.config.settings"

    mimir_enabled = fields.Boolean(
        string="Enable Mimir",
        config_parameter="mimir.enabled",
    )
    mimir_horizon_days = fields.Integer(
        string="Forecast Horizon (days)",
        config_parameter="mimir.horizon_days",
        default=30,
        help="Number of days to project demand forward",
    )
    mimir_time_bucket = fields.Selection(
        [("daily", "Daily"), ("weekly", "Weekly")],
        string="Time Bucket",
        config_parameter="mimir.time_bucket",
        default="daily",
        help="Granularity of demand requested from the forecast server (Daily vs. Weekly)",
    )
    mimir_service_level = fields.Float(
        string="Service Level",
        config_parameter="mimir.service_level",
        default=0.85,
        help="Target fill rate (0.0-1.0). Set to 0.90 for standard service or 0.95 for critical items. "
        "Higher values require more safety stock. Start at 0.85 and increase per category as needed.",
    )
    mimir_review_period_days = fields.Integer(
        string="Review Period (days)",
        config_parameter="mimir.review_period_days",
        default=7,
        help="Days between replenishment reviews — affects max_qty calculation",
    )
    mimir_default_lead_time = fields.Integer(
        string="Default Lead Time (days)",
        config_parameter="mimir.default_lead_time",
        default=7,
        help="Fallback lead time when no supplier info exists for a product",
    )
    mimir_min_data_points = fields.Integer(
        string="Minimum Data Points",
        config_parameter="mimir.min_data_points",
        default=4,
        help="Minimum number of observations required to fit a regression",
    )
    mimir_min_history_days = fields.Integer(
        string="Minimum History Days",
        config_parameter="mimir.min_history_days",
        default=36,
        help="Minimum number of days of history required to fit a regression",
    )
    mimir_default_min_qty = fields.Float(
        string="Default Min Qty (Fallback)",
        config_parameter="mimir.default_min_qty",
        default=0.0,
        help="Default minimum quantity if insufficient history",
    )
    mimir_default_max_qty = fields.Float(
        string="Default Max Qty (Fallback)",
        config_parameter="mimir.default_max_qty",
        default=0.0,
        help="Default maximum quantity if insufficient history",
    )
    mimir_min_demand_threshold = fields.Float(
        string="Minimum Demand Threshold",
        config_parameter="mimir.min_demand_threshold",
        default=0.1,
        help="Skip products with average daily demand below this value",
    )
    mimir_auto_push = fields.Boolean(
        string="Auto-push to Orderpoints",
        config_parameter="mimir.auto_push",
        default=False,
        help="Automatically update orderpoints after forecast run (vs. parquet-only)",
    )
    mimir_external_forecast_uri = fields.Char(
        string="External Server Base URL",
        config_parameter="mimir.external_forecast_uri",
        default="https://mimir.datastruct.tech",
        help="Base URL for the optional warehouse-scoped rules server",
    )
    mimir_external_api_key = fields.Char(
        string="External API Key (UUID)",
        config_parameter="mimir.external_api_key",
        help="API Key for the optional warehouse-scoped rules server (Must be a UUID)",
    )
    mimir_warehouse_id = fields.Many2one(
        "stock.warehouse",
        string="Warehouse",
        help="Mimir imports one warehouse at a time and stages rules only for this warehouse.",
    )

    @api.constrains("mimir_external_api_key")
    def _check_external_api_key(self):
        for record in self:
            if record.mimir_external_api_key:
                try:
                    uuid.UUID(record.mimir_external_api_key)
                except ValueError:
                    raise ValidationError(
                        "External API Key must be a valid UUID format (e.g. 123e4567-e89b-12d3-a456-426614174000)."
                    )

    @api.model
    def get_values(self):
        res = super().get_values()
        warehouse_id = self.env["ir.config_parameter"].sudo().get_param("mimir.warehouse_id", "")
        res["mimir_warehouse_id"] = int(warehouse_id) if warehouse_id else False
        return res

    def set_values(self):
        super().set_values()
        warehouse_id = self.mimir_warehouse_id.id if self.mimir_warehouse_id else ""
        self.env["ir.config_parameter"].sudo().set_param("mimir.warehouse_id", warehouse_id)

    # Removed config_db_path as we use ORM models now

    # Inventory position settings
    mimir_inventory_mode = fields.Selection(
        [
            ("ignore", "Ignore (forecast only)"),
            ("analyze", "Analyze (add inventory columns)"),
            ("adjust", "Adjust (modify replenishment)"),
        ],
        string="Inventory Mode",
        config_parameter="mimir.inventory_mode",
        default="ignore",
        help="How to account for current inventory when computing replenishment rules",
    )
    mimir_respect_reservations = fields.Boolean(
        string="Respect Reservations",
        config_parameter="mimir.respect_reservations",
        default=True,
        help="Subtract reserved quantities from on-hand (conservative). Disable to let planner reallocate all stock.",
    )
    mimir_include_incoming = fields.Boolean(
        string="Include Incoming Supply",
        config_parameter="mimir.include_incoming_supply",
        default=True,
        help="Count open PO and in-transit quantities as available inventory",
    )
    mimir_include_outgoing = fields.Boolean(
        string="Include Outgoing Demand",
        config_parameter="mimir.include_outgoing_demand",
        default=False,
        help="Subtract confirmed SO quantities. Off by default to avoid double-counting with forecast.",
    )
    mimir_overstock_days = fields.Float(
        string="Overstock Threshold (days)",
        config_parameter="mimir.overstock_threshold_days",
        default=90,
        help="Products with more than this many days of coverage are flagged as overstock",
    )
    mimir_understock_days = fields.Float(
        string="Understock Threshold (days)",
        config_parameter="mimir.understock_threshold_days",
        default=3,
        help="Products with fewer than this many days of coverage are flagged as understock",
    )
    mimir_overstock_skip = fields.Boolean(
        string="Skip Overstocked Products",
        config_parameter="mimir.overstock_skip",
        default=True,
        help="In adjust mode, don't create orderpoints for overstocked products",
    )
    mimir_understock_bump = fields.Float(
        string="Understock Service Level Bump",
        config_parameter="mimir.understock_service_level_bump",
        default=0.03,
        help="In adjust mode, raise service level by this amount for understocked products (e.g. 0.95 → 0.98)",
    )

    def _build_runtime_context(self, selected_warehouse, minmax_table):
        """Build minimal local Odoo metadata needed to normalize remote rules."""
        from mimir.runtime_context import PipelineRuntimeContext, extract_rule_product_ids

        product_ids = extract_rule_product_ids(minmax_table)
        products = self.env["product.product"].sudo().browse(product_ids).exists()
        product_names = {product.id: product.display_name or product.name or str(product.id) for product in products}

        missing_product_ids = [product_id for product_id in product_ids if product_id not in product_names]
        if missing_product_ids:
            missing_text = ", ".join(str(product_id) for product_id in missing_product_ids)
            raise ValueError(f"Imported rules reference unknown Odoo product IDs: {missing_text}")

        orderpoints = self.env["stock.warehouse.orderpoint"].sudo().search(
            [
                ("warehouse_id", "=", selected_warehouse.id),
                ("product_id", "in", product_ids),
            ]
        )
        existing_orderpoint_keys = {(orderpoint.product_id.id, orderpoint.warehouse_id.id) for orderpoint in orderpoints}

        return PipelineRuntimeContext(
            warehouse_code=selected_warehouse.code or "",
            location_id=selected_warehouse.lot_stock_id.id,
            product_names=product_names,
            existing_orderpoint_keys=existing_orderpoint_keys,
        )

    def _store_output_attachments(self, run_record, output_dir):
        """Store output parquet and Excel files as ir.attachment on the run record."""
        output_path = Path(output_dir)
        stored = 0
        for pattern in ["*.parquet", "*.xlsx"]:
            for fpath in output_path.glob(pattern):
                self.env["ir.attachment"].sudo().create(
                    {
                        "name": fpath.name,
                        "datas": base64.b64encode(fpath.read_bytes()).decode(),
                        "res_model": "mimir.run",
                        "res_id": run_record.id,
                        "mimetype": "application/octet-stream",
                    }
                )
                stored += 1
        _logger.info("Stored %d output files as attachments on run %d", stored, run_record.id)

    def action_run_mimir(self):
        """Trigger a warehouse-scoped server-backed mimir import run from Settings."""
        ICP = self.env["ir.config_parameter"].sudo()
        auto_push = ICP.get_param("mimir.auto_push", "False") == "True"
        selected_warehouse_raw = ICP.get_param("mimir.warehouse_id", "").strip()
        external_uri = ICP.get_param("mimir.external_forecast_uri", "").strip()
        external_api_key = ICP.get_param("mimir.external_api_key", "").strip()
        output_dir = None

        try:
            if not selected_warehouse_raw:
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Mimir",
                        "message": "Please select a warehouse first.",
                        "type": "warning",
                        "sticky": False,
                    },
                }

            if not external_uri:
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Mimir Server",
                        "message": "Please configure External Server Base URL first.",
                        "type": "warning",
                        "sticky": False,
                    },
                }

            selected_warehouse_id = int(selected_warehouse_raw)
            selected_warehouse = self.env["stock.warehouse"].sudo().browse(selected_warehouse_id).exists()
            if not selected_warehouse:
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Mimir",
                        "message": f"Warehouse {selected_warehouse_id} no longer exists.",
                        "type": "warning",
                        "sticky": False,
                    },
                }

            from mimir.server_client import MimirServerClient

            try:
                client = MimirServerClient(external_uri, external_api_key)
                client.preflight_warehouse(
                    selected_warehouse_id=selected_warehouse_id,
                    warehouse_code=selected_warehouse.code,
                )
            except ValueError as exc:
                return {
                    "type": "ir.actions.client",
                    "tag": "display_notification",
                    "params": {
                        "title": "Mimir Server",
                        "message": str(exc),
                        "type": "warning",
                        "sticky": False,
                    },
                }

            from mimir.config import MimirConfig
            from mimir.excel_export import export_forecast_evidence_to_excel, export_to_excel
            from mimir.importer import (
                fetch_remote_empirical_lead_times_table,
                fetch_remote_forecast_evidence_table,
                fetch_remote_minmax_table,
            )
            from mimir.pipeline import run_pipeline

            config = MimirConfig(
                selected_warehouse_id=selected_warehouse_id,
                odoo_db=self.env.cr.dbname,
                external_forecast_uri=external_uri,
                external_forecast_api_key=external_api_key,
            )

            minmax_table = fetch_remote_minmax_table(config, selected_warehouse_code=selected_warehouse.code)
            runtime_context = self._build_runtime_context(selected_warehouse, minmax_table)
            forecast_evidence = fetch_remote_forecast_evidence_table(
                config,
                selected_warehouse_code=runtime_context.warehouse_code,
            )
            empirical_lead_times = fetch_remote_empirical_lead_times_table(config)
            output_dir = str(Path(tempfile.mkdtemp(prefix="mimir_out_")))

            stats = run_pipeline(
                parquet_output_dir=output_dir,
                config=config,
                runtime_context=runtime_context,
                minmax_table=minmax_table,
                forecast_evidence=forecast_evidence,
                empirical_lead_times=empirical_lead_times,
            )

            try:
                rules_path = Path(output_dir) / "replenishment_rules.parquet"
                decisions_path = Path(output_dir) / "decisions.parquet"
                if rules_path.exists():
                    export_to_excel(
                        rules_parquet=rules_path,
                        decisions_parquet=decisions_path if decisions_path.exists() else None,
                    )
                forecast_evidence_path = Path(output_dir) / "forecast_evidence.parquet"
                if forecast_evidence_path.exists():
                    export_forecast_evidence_to_excel(forecast_evidence_path)
            except Exception:
                _logger.warning("Excel export failed (non-fatal)", exc_info=True)

            source_label = external_uri
            run_record = (
                self.env["mimir.run"]
                .sudo()
                .create(
                    {
                        "warehouse_id": selected_warehouse_id,
                        "parquet_source_dir": source_label,
                        "products_analyzed": stats.get("products_analyzed", 0),
                        "products_forecasted": stats.get("products_forecasted", 0),
                        "rules_created": stats.get("rules_created", 0),
                        "rules_updated": stats.get("rules_updated", 0),
                        "rules_skipped": stats.get("rules_skipped", 0),
                        "duration_seconds": stats.get("duration_seconds", 0),
                        "status": "complete",
                    }
                )
            )

            # Parse decisions.parquet to create staging records
            decisions_path = Path(output_dir) / "decisions.parquet"
            if decisions_path.exists():
                import pyarrow.parquet as pq

                table = pq.read_table(decisions_path)
                decision_dicts = table.to_pylist()
                DecisionRequest = self.env["mimir.decision.request"].sudo()
                for d in decision_dicts:
                    if d.get("decision_type") == "ORDERPOINT":
                        DecisionRequest.create(
                            {
                                "run_id": run_record.id,
                                "decision_id": d["decision_id"],
                                "product_id": d["_odoo_product_id"],
                                "location_id": d["_odoo_location_id"],
                                "proposed_min_qty": d["min_quantity"],
                                "proposed_max_qty": d["max_quantity"],
                                "planned_lead_time": d.get("planned_lead_time_days", d.get("delay", 0)),
                                "empirical_lead_time": d.get("empirical_lead_time_days", 0),
                                "status": "draft",
                            }
                        )

            # Auto-approve if configured
            if auto_push:
                run_record.env["mimir.decision.request"].search(
                    [("run_id", "=", run_record.id), ("status", "=", "draft")]
                ).action_approve()

            # Store output files as attachments
            self._store_output_attachments(run_record, output_dir)

            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Mimir Complete",
                    "message": (
                        f"Imported {stats['products_analyzed']} rules for "
                        f"{selected_warehouse.display_name}, "
                        f"created {stats['rules_created']} rules, "
                        f"updated {stats['rules_updated']} rules "
                        f"in {stats['duration_seconds']}s."
                    ),
                    "type": "success",
                    "sticky": False,
                },
            }
        except Exception as e:
            _logger.exception("Mimir pipeline failed")
            self.env["mimir.run"].sudo().create(
                {
                    "warehouse_id": int(selected_warehouse_raw) if selected_warehouse_raw else False,
                    "parquet_source_dir": external_uri,
                    "status": "error",
                    "error_message": str(e),
                }
            )
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Mimir Error",
                    "message": str(e),
                    "type": "danger",
                    "sticky": True,
                },
            }
        finally:
            import shutil

            if output_dir:
                shutil.rmtree(output_dir, ignore_errors=True)

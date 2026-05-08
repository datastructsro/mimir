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
    mimir_input_source = fields.Selection(
        [
            ("attachment", "Odoo Attachments (from Parqcast)"),
            ("filesystem", "Server Filesystem"),
            ("s3", "S3 (from Parqcast)"),
        ],
        string="Input Source",
        config_parameter="mimir.input_source",
        default="attachment",
        help="Where to read parqcast export files from",
    )
    mimir_s3_bucket = fields.Char(
        string="Mimir S3 Bucket",
        config_parameter="mimir.s3_bucket",
        help="Bucket where Parqcast wrote its export (e.g. parqcast-v18-demo)",
    )
    mimir_s3_prefix = fields.Char(
        string="Mimir S3 Prefix",
        config_parameter="mimir.s3_prefix",
        default="parqcast",
        help="Prefix under the bucket; Mimir looks at <prefix>/outbound/<run_uuid>/",
    )
    mimir_s3_endpoint_url = fields.Char(
        string="Mimir S3 Endpoint URL",
        config_parameter="mimir.s3_endpoint_url",
        help="For S3-compatible stores (MinIO, LocalStack). Leave empty for AWS.",
    )
    mimir_s3_access_key_id = fields.Char(
        string="S3 Access Key ID",
        config_parameter="mimir.s3_access_key_id",
        help="Leave empty to use the boto3 default credential chain",
    )
    mimir_s3_secret_access_key = fields.Char(
        string="S3 Secret Access Key",
        config_parameter="mimir.s3_secret_access_key",
    )
    mimir_s3_region = fields.Char(
        string="S3 Region",
        config_parameter="mimir.s3_region",
        help="e.g. eu-central-1. Ignored for most S3-compatible stores.",
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
    mimir_parquet_input_dir = fields.Char(
        string="Parquet Input Directory",
        config_parameter="mimir.parquet_input_dir",
        help="Path to parqcast export directory",
    )
    mimir_parquet_output_dir = fields.Char(
        string="Parquet Output Directory",
        config_parameter="mimir.parquet_output_dir",
        help="Path to write forecast and rules parquet files",
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

    @api.constrains('mimir_external_api_key')
    def _check_external_api_key(self):
        for record in self:
            if record.mimir_external_api_key:
                try:
                    uuid.UUID(record.mimir_external_api_key)
                except ValueError:
                    raise ValidationError("External API Key must be a valid UUID format (e.g. 123e4567-e89b-12d3-a456-426614174000).")

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
        [("ignore", "Ignore (forecast only)"), ("analyze", "Analyze (add inventory columns)"), ("adjust", "Adjust (modify replenishment)")],
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

    def _latest_parqcast_run(self):
        """Return (run_id, run_uuid) of the most recent completed parqcast export.

        Raises ValueError if no completed run exists.
        """
        self.env.cr.execute(
            "SELECT id, run_uuid FROM parqcast_export_run "
            "WHERE state = 'done' ORDER BY finished_at DESC NULLS LAST LIMIT 1"
        )
        row = self.env.cr.fetchone()
        if not row:
            raise ValueError("No completed Parqcast export run found. Run Parqcast export first.")
        return row[0], row[1]

    def _get_parqcast_input_dir(self):
        """Materialize parqcast attachment files into a temp directory.

        Finds the latest completed parqcast export run, downloads its
        ir.attachment parquet files, and writes them to a temporary directory.
        Returns the Path to the temp dir.
        """
        run_id, run_uuid = self._latest_parqcast_run()
        attachments = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "parqcast.run"),
            ("res_id", "=", run_id),
        ])
        if not attachments:
            raise ValueError(
                f"Parqcast run {run_uuid[:8]} has no attachment files. "
                "Check that Parqcast transport is set to 'Odoo Attachments'."
            )

        tmp_dir = Path(tempfile.mkdtemp(prefix="mimir_"))
        for att in attachments:
            (tmp_dir / att.name).write_bytes(base64.b64decode(att.datas))

        _logger.info(
            "Materialized %d parqcast attachments from run %s into %s",
            len(attachments), run_uuid[:8], tmp_dir,
        )
        return tmp_dir

    def _get_parqcast_input_dir_s3(self):
        """Download the latest parqcast export from S3 into a temp directory.

        Expects the parqcast layout <prefix>/outbound/<run_uuid>/*.parquet under
        the configured bucket. Returns the Path to the temp dir.
        """
        try:
            import boto3
        except ImportError as e:
            raise ValueError(
                "S3 input source requires boto3. Install it in the Odoo environment."
            ) from e

        ICP = self.env["ir.config_parameter"].sudo()
        bucket = ICP.get_param("mimir.s3_bucket", "").strip()
        if not bucket:
            raise ValueError("Please configure S3 Bucket in Mimir settings.")
        prefix = ICP.get_param("mimir.s3_prefix", "parqcast").strip().rstrip("/") or "parqcast"
        endpoint_url = ICP.get_param("mimir.s3_endpoint_url", "").strip() or None
        access_key = ICP.get_param("mimir.s3_access_key_id", "").strip() or None
        secret_key = ICP.get_param("mimir.s3_secret_access_key", "").strip() or None
        region = ICP.get_param("mimir.s3_region", "").strip() or None

        run_id, run_uuid = self._latest_parqcast_run()

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

        s3_prefix = f"{prefix}/outbound/{run_uuid}/"
        tmp_dir = Path(tempfile.mkdtemp(prefix="mimir_s3_"))
        downloaded = 0
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=s3_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                rel = key[len(s3_prefix):]
                if not rel or rel.endswith("/"):
                    continue
                dest = tmp_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                s3.download_file(bucket, key, str(dest))
                downloaded += 1

        if not downloaded:
            raise ValueError(
                f"No files found at s3://{bucket}/{s3_prefix} for parqcast run {run_uuid[:8]}"
            )

        _logger.info(
            "Downloaded %d S3 objects from s3://%s/%s (run %s) into %s",
            downloaded, bucket, s3_prefix, run_uuid[:8], tmp_dir,
        )
        return tmp_dir

    def _store_output_attachments(self, run_record, output_dir):
        """Store output parquet and Excel files as ir.attachment on the run record."""
        output_path = Path(output_dir)
        stored = 0
        for pattern in ["*.parquet", "*.xlsx"]:
            for fpath in output_path.glob(pattern):
                self.env["ir.attachment"].sudo().create({
                    "name": fpath.name,
                    "datas": base64.b64encode(fpath.read_bytes()).decode(),
                    "res_model": "mimir.run",
                    "res_id": run_record.id,
                    "mimetype": "application/octet-stream",
                })
                stored += 1
        _logger.info("Stored %d output files as attachments on run %d", stored, run_record.id)

    def action_run_mimir(self):
        """Trigger a warehouse-scoped mimir import run from Settings."""
        ICP = self.env["ir.config_parameter"].sudo()
        input_source = ICP.get_param("mimir.input_source", "attachment")
        auto_push = ICP.get_param("mimir.auto_push", "False") == "True"
        selected_warehouse_raw = ICP.get_param("mimir.warehouse_id", "").strip()

        input_dir = None
        tmp_dir = None
        output_dir = None

        try:
            if input_source == "attachment":
                tmp_dir = self._get_parqcast_input_dir()
                input_dir = str(tmp_dir)
            elif input_source == "s3":
                tmp_dir = self._get_parqcast_input_dir_s3()
                input_dir = str(tmp_dir)
            else:
                input_dir = ICP.get_param("mimir.parquet_input_dir", "")
                if not input_dir:
                    return {
                        "type": "ir.actions.client",
                        "tag": "display_notification",
                        "params": {
                            "title": "Mimir",
                            "message": "Please configure Parquet Input Directory first.",
                            "type": "warning",
                            "sticky": False,
                        },
                    }

            # For attachment/s3 modes, use a temp output dir too
            if input_source in ("attachment", "s3"):
                output_dir = str(Path(tempfile.mkdtemp(prefix="mimir_out_")))
            else:
                output_dir = ICP.get_param("mimir.parquet_output_dir", "")
                if not output_dir:
                    return {
                        "type": "ir.actions.client",
                        "tag": "display_notification",
                        "params": {
                            "title": "Mimir",
                            "message": "Please configure Parquet Output Directory first.",
                            "type": "warning",
                            "sticky": False,
                        },
                    }

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

            selected_warehouse_id = int(selected_warehouse_raw)
            selected_warehouse = self.env["stock.warehouse"].browse(selected_warehouse_id)

            from mimir.config import MimirConfig
            from mimir.excel_export import export_forecast_evidence_to_excel, export_to_excel
            from mimir.pipeline import run_pipeline

            config = MimirConfig(
                selected_warehouse_id=selected_warehouse_id,
                odoo_db=self.env.cr.dbname,
                external_forecast_uri=ICP.get_param("mimir.external_forecast_uri", "").strip(),
                external_forecast_api_key=ICP.get_param("mimir.external_api_key", "").strip(),
            )

            stats = run_pipeline(
                parquet_input_dir=input_dir,
                parquet_output_dir=output_dir,
                config=config,
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

            if input_source == "attachment":
                source_label = "parqcast attachments"
            elif input_source == "s3":
                source_label = f"parqcast s3://{ICP.get_param('mimir.s3_bucket', '')}"
            else:
                source_label = input_dir
            run_record = self.env["mimir.run"].sudo().create({
                "warehouse_id": selected_warehouse_id,
                "parquet_source_dir": source_label,
                "products_analyzed": stats.get("products_analyzed", 0),
                "products_forecasted": stats.get("products_forecasted", 0),
                "rules_created": stats.get("rules_created", 0),
                "rules_updated": stats.get("rules_updated", 0),
                "rules_skipped": stats.get("rules_skipped", 0),
                "duration_seconds": stats.get("duration_seconds", 0),
                "status": "complete",
            })

            # Parse decisions.parquet to create staging records
            decisions_path = Path(output_dir) / "decisions.parquet"
            if decisions_path.exists():
                import pyarrow.parquet as pq
                table = pq.read_table(decisions_path)
                decision_dicts = table.to_pylist()
                DecisionRequest = self.env["mimir.decision.request"].sudo()
                for d in decision_dicts:
                    if d.get("decision_type") == "ORDERPOINT":
                        DecisionRequest.create({
                            "run_id": run_record.id,
                            "decision_id": d["decision_id"],
                            "product_id": d["_odoo_product_id"],
                            "location_id": d["_odoo_location_id"],
                            "proposed_min_qty": d["min_quantity"],
                            "proposed_max_qty": d["max_quantity"],
                            "planned_lead_time": d.get("planned_lead_time_days", d.get("delay", 0)),
                            "empirical_lead_time": d.get("empirical_lead_time_days", 0),
                            "status": "draft",
                        })

            # Auto-approve if configured
            if auto_push:
                run_record.env["mimir.decision.request"].search([("run_id", "=", run_record.id), ("status", "=", "draft")]).action_approve()

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
            if input_source == "attachment":
                source_label = "parqcast attachments"
            elif input_source == "s3":
                source_label = f"parqcast s3://{ICP.get_param('mimir.s3_bucket', '')}"
            else:
                source_label = input_dir or ""
            self.env["mimir.run"].sudo().create({
                "warehouse_id": int(selected_warehouse_raw) if selected_warehouse_raw else False,
                "parquet_source_dir": source_label,
                "status": "error",
                "error_message": str(e),
            })
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
            # Clean up temp directories
            import shutil
            if tmp_dir and Path(tmp_dir).exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
            if input_source in ("attachment", "s3") and output_dir:
                shutil.rmtree(output_dir, ignore_errors=True)

import base64
import logging
import tempfile
from pathlib import Path

from odoo import fields, models

_logger = logging.getLogger(__name__)


class ForeqcastSettings(models.TransientModel):
    _inherit = "res.config.settings"

    foreqcast_enabled = fields.Boolean(
        string="Enable Foreqcast",
        config_parameter="foreqcast.enabled",
    )
    foreqcast_input_source = fields.Selection(
        [
            ("attachment", "Odoo Attachments (from Parqcast)"),
            ("filesystem", "Server Filesystem"),
            ("s3", "S3 (from Parqcast)"),
        ],
        string="Input Source",
        config_parameter="foreqcast.input_source",
        default="attachment",
        help="Where to read parqcast export files from",
    )
    foreqcast_s3_bucket = fields.Char(
        string="Foreqcast S3 Bucket",
        config_parameter="foreqcast.s3_bucket",
        help="Bucket where Parqcast wrote its export (e.g. parqcast-v18-demo)",
    )
    foreqcast_s3_prefix = fields.Char(
        string="Foreqcast S3 Prefix",
        config_parameter="foreqcast.s3_prefix",
        default="parqcast",
        help="Prefix under the bucket; Foreqcast looks at <prefix>/outbound/<run_uuid>/",
    )
    foreqcast_s3_endpoint_url = fields.Char(
        string="Foreqcast S3 Endpoint URL",
        config_parameter="foreqcast.s3_endpoint_url",
        help="For S3-compatible stores (MinIO, LocalStack). Leave empty for AWS.",
    )
    foreqcast_s3_access_key_id = fields.Char(
        string="S3 Access Key ID",
        config_parameter="foreqcast.s3_access_key_id",
        help="Leave empty to use the boto3 default credential chain",
    )
    foreqcast_s3_secret_access_key = fields.Char(
        string="S3 Secret Access Key",
        config_parameter="foreqcast.s3_secret_access_key",
    )
    foreqcast_s3_region = fields.Char(
        string="S3 Region",
        config_parameter="foreqcast.s3_region",
        help="e.g. eu-central-1. Ignored for most S3-compatible stores.",
    )
    foreqcast_horizon_days = fields.Integer(
        string="Forecast Horizon (days)",
        config_parameter="foreqcast.horizon_days",
        default=30,
        help="Number of days to project demand forward",
    )
    foreqcast_time_bucket = fields.Selection(
        [("daily", "Daily"), ("weekly", "Weekly")],
        string="Time Bucket",
        config_parameter="foreqcast.time_bucket",
        default="daily",
        help="Granularity of demand aggregation",
    )
    foreqcast_service_level = fields.Float(
        string="Service Level",
        config_parameter="foreqcast.service_level",
        default=0.85,
        help="Target fill rate (0.0-1.0). Set to 0.90 for standard service or 0.95 for critical items. "
             "Higher values require more safety stock. Start at 0.85 and increase per category as needed.",
    )
    foreqcast_review_period_days = fields.Integer(
        string="Review Period (days)",
        config_parameter="foreqcast.review_period_days",
        default=7,
        help="Days between replenishment reviews — affects max_qty calculation",
    )
    foreqcast_default_lead_time = fields.Integer(
        string="Default Lead Time (days)",
        config_parameter="foreqcast.default_lead_time",
        default=7,
        help="Fallback lead time when no supplier info exists for a product",
    )
    foreqcast_min_data_points = fields.Integer(
        string="Minimum Data Points",
        config_parameter="foreqcast.min_data_points",
        default=4,
        help="Minimum number of observations required to fit a regression",
    )
    foreqcast_min_demand_threshold = fields.Float(
        string="Minimum Demand Threshold",
        config_parameter="foreqcast.min_demand_threshold",
        default=0.1,
        help="Skip products with average daily demand below this value",
    )
    foreqcast_auto_push = fields.Boolean(
        string="Auto-push to Orderpoints",
        config_parameter="foreqcast.auto_push",
        default=False,
        help="Automatically update orderpoints after forecast run (vs. parquet-only)",
    )
    foreqcast_parquet_input_dir = fields.Char(
        string="Parquet Input Directory",
        config_parameter="foreqcast.parquet_input_dir",
        help="Path to parqcast export directory",
    )
    foreqcast_parquet_output_dir = fields.Char(
        string="Parquet Output Directory",
        config_parameter="foreqcast.parquet_output_dir",
        help="Path to write forecast and rules parquet files",
    )
    foreqcast_config_db_path = fields.Char(
        string="Config Database Path",
        config_parameter="foreqcast.config_db_path",
        help="Path to foreqcast SQLite config database (for per-product overrides)",
    )

    # Inventory position settings
    foreqcast_inventory_mode = fields.Selection(
        [("ignore", "Ignore (forecast only)"), ("analyze", "Analyze (add inventory columns)"), ("adjust", "Adjust (modify replenishment)")],
        string="Inventory Mode",
        config_parameter="foreqcast.inventory_mode",
        default="ignore",
        help="How to account for current inventory when computing replenishment rules",
    )
    foreqcast_respect_reservations = fields.Boolean(
        string="Respect Reservations",
        config_parameter="foreqcast.respect_reservations",
        default=True,
        help="Subtract reserved quantities from on-hand (conservative). Disable to let planner reallocate all stock.",
    )
    foreqcast_include_incoming = fields.Boolean(
        string="Include Incoming Supply",
        config_parameter="foreqcast.include_incoming_supply",
        default=True,
        help="Count open PO and in-transit quantities as available inventory",
    )
    foreqcast_include_outgoing = fields.Boolean(
        string="Include Outgoing Demand",
        config_parameter="foreqcast.include_outgoing_demand",
        default=False,
        help="Subtract confirmed SO quantities. Off by default to avoid double-counting with forecast.",
    )
    foreqcast_overstock_days = fields.Float(
        string="Overstock Threshold (days)",
        config_parameter="foreqcast.overstock_threshold_days",
        default=90,
        help="Products with more than this many days of coverage are flagged as overstock",
    )
    foreqcast_understock_days = fields.Float(
        string="Understock Threshold (days)",
        config_parameter="foreqcast.understock_threshold_days",
        default=3,
        help="Products with fewer than this many days of coverage are flagged as understock",
    )
    foreqcast_overstock_skip = fields.Boolean(
        string="Skip Overstocked Products",
        config_parameter="foreqcast.overstock_skip",
        default=True,
        help="In adjust mode, don't create orderpoints for overstocked products",
    )
    foreqcast_understock_bump = fields.Float(
        string="Understock Service Level Bump",
        config_parameter="foreqcast.understock_service_level_bump",
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

        tmp_dir = Path(tempfile.mkdtemp(prefix="foreqcast_"))
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
        bucket = ICP.get_param("foreqcast.s3_bucket", "").strip()
        if not bucket:
            raise ValueError("Please configure S3 Bucket in Foreqcast settings.")
        prefix = ICP.get_param("foreqcast.s3_prefix", "parqcast").strip().rstrip("/") or "parqcast"
        endpoint_url = ICP.get_param("foreqcast.s3_endpoint_url", "").strip() or None
        access_key = ICP.get_param("foreqcast.s3_access_key_id", "").strip() or None
        secret_key = ICP.get_param("foreqcast.s3_secret_access_key", "").strip() or None
        region = ICP.get_param("foreqcast.s3_region", "").strip() or None

        run_id, run_uuid = self._latest_parqcast_run()

        s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )

        s3_prefix = f"{prefix}/outbound/{run_uuid}/"
        tmp_dir = Path(tempfile.mkdtemp(prefix="foreqcast_s3_"))
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
                    "res_model": "foreqcast.run",
                    "res_id": run_record.id,
                    "mimetype": "application/octet-stream",
                })
                stored += 1
        _logger.info("Stored %d output files as attachments on run %d", stored, run_record.id)

    def action_run_foreqcast(self):
        """Trigger a foreqcast pipeline run from Settings."""
        ICP = self.env["ir.config_parameter"].sudo()
        input_source = ICP.get_param("foreqcast.input_source", "attachment")
        config_db = ICP.get_param("foreqcast.config_db_path", "")
        auto_push = ICP.get_param("foreqcast.auto_push", "False") == "True"

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
                input_dir = ICP.get_param("foreqcast.parquet_input_dir", "")
                if not input_dir:
                    return {
                        "type": "ir.actions.client",
                        "tag": "display_notification",
                        "params": {
                            "title": "Foreqcast",
                            "message": "Please configure Parquet Input Directory first.",
                            "type": "warning",
                            "sticky": False,
                        },
                    }

            # For attachment/s3 modes, use a temp output dir too
            if input_source in ("attachment", "s3"):
                output_dir = str(Path(tempfile.mkdtemp(prefix="foreqcast_out_")))
            else:
                output_dir = ICP.get_param("foreqcast.parquet_output_dir", "")
                if not output_dir:
                    return {
                        "type": "ir.actions.client",
                        "tag": "display_notification",
                        "params": {
                            "title": "Foreqcast",
                            "message": "Please configure Parquet Output Directory first.",
                            "type": "warning",
                            "sticky": False,
                        },
                    }

            from foreqcast.config import init_db, load_config
            from foreqcast.pipeline import run_pipeline

            conn = init_db(Path(config_db)) if config_db else init_db()
            config = load_config(conn)
            stats = run_pipeline(
                parquet_input_dir=input_dir,
                parquet_output_dir=output_dir,
                config=config,
                db_conn=conn,
                push_to_odoo=auto_push,
            )

            # Generate Excel export
            try:
                from foreqcast.excel_export import export_to_excel
                rules_path = Path(output_dir) / "replenishment_rules.parquet"
                forecast_path = Path(output_dir) / "forecast.parquet"
                if rules_path.exists():
                    export_to_excel(
                        rules_parquet=rules_path,
                        forecast_parquet=forecast_path if forecast_path.exists() else None,
                    )
            except Exception:
                _logger.warning("Excel export failed (non-fatal)", exc_info=True)

            if input_source == "attachment":
                source_label = "parqcast attachments"
            elif input_source == "s3":
                source_label = f"parqcast s3://{ICP.get_param('foreqcast.s3_bucket', '')}"
            else:
                source_label = input_dir
            run_record = self.env["foreqcast.run"].sudo().create({
                "parquet_source_dir": source_label,
                "products_analyzed": stats.get("products_analyzed", 0),
                "products_forecasted": stats.get("products_forecasted", 0),
                "rules_created": stats.get("rules_created", 0),
                "rules_updated": stats.get("rules_updated", 0),
                "rules_skipped": stats.get("rules_skipped", 0),
                "duration_seconds": stats.get("duration_seconds", 0),
                "status": "complete",
            })

            # Store output files as attachments
            self._store_output_attachments(run_record, output_dir)

            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Foreqcast Complete",
                    "message": (
                        f"Analyzed {stats['products_analyzed']} products, "
                        f"created {stats['rules_created']} rules, "
                        f"updated {stats['rules_updated']} rules "
                        f"in {stats['duration_seconds']}s."
                    ),
                    "type": "success",
                    "sticky": False,
                },
            }
        except Exception as e:
            _logger.exception("Foreqcast pipeline failed")
            if input_source == "attachment":
                source_label = "parqcast attachments"
            elif input_source == "s3":
                source_label = f"parqcast s3://{ICP.get_param('foreqcast.s3_bucket', '')}"
            else:
                source_label = input_dir or ""
            self.env["foreqcast.run"].sudo().create({
                "parquet_source_dir": source_label,
                "status": "error",
                "error_message": str(e),
            })
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": "Foreqcast Error",
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

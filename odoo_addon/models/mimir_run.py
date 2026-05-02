import base64
import io
import zipfile

from odoo import fields, models


class MimirRun(models.Model):
    _name = "mimir.run"
    _description = "Mimir Run History"
    _order = "run_date desc"

    run_date = fields.Datetime(string="Run Date", required=True, default=fields.Datetime.now)
    parquet_source_dir = fields.Char(string="Input Source")
    products_analyzed = fields.Integer(string="Products Analyzed", default=0)
    products_forecasted = fields.Integer(string="Products Forecasted", default=0)
    rules_created = fields.Integer(string="Rules Created", default=0)
    rules_updated = fields.Integer(string="Rules Updated", default=0)
    rules_skipped = fields.Integer(string="Rules Skipped", default=0)
    duration_seconds = fields.Float(string="Duration (s)")
    status = fields.Selection(
        [("running", "Running"), ("complete", "Complete"), ("error", "Error")],
        string="Status",
        default="running",
    )
    error_message = fields.Text(string="Error Message")
    forecast_horizon_days = fields.Integer(string="Horizon (days)")
    safety_factor = fields.Float(string="Safety Factor Used")
    time_bucket = fields.Char(string="Time Bucket")
    attachment_count = fields.Integer(string="Output Files", compute="_compute_attachment_count")

    decision_count = fields.Integer(string="Decisions", compute="_compute_decision_count")

    def _compute_attachment_count(self):
        for run in self:
            run.attachment_count = self.env["ir.attachment"].sudo().search_count([
                ("res_model", "=", "mimir.run"),
                ("res_id", "=", run.id),
            ])

    def _compute_decision_count(self):
        for run in self:
            run.decision_count = self.env["mimir.decision.request"].search_count([("run_id", "=", run.id)])

    def action_view_decisions(self):
        self.ensure_one()
        return {
            "name": "Decision Requests",
            "type": "ir.actions.act_window",
            "res_model": "mimir.decision.request",
            "view_mode": "list,form",
            "domain": [("run_id", "=", self.id)],
            "context": {"default_run_id": self.id},
        }

    def action_open_excel_wizard(self):
        self.ensure_one()
        return {
            "name": "Upload Excel Review",
            "type": "ir.actions.act_window",
            "res_model": "mimir.excel.upload",
            "view_mode": "form",
            "target": "new",
            "context": {"default_run_id": self.id},
        }

    def _get_output_attachment(self, filename):
        """Find a specific output attachment by filename."""
        return self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "mimir.run"),
            ("res_id", "=", self.id),
            ("name", "=", filename),
        ], limit=1)

    def action_download_forecast(self):
        self.ensure_one()
        att = self._get_output_attachment("forecast.parquet")
        if not att:
            return self._no_file_notification("forecast.parquet")
        return self._download_action(att)

    def action_download_rules(self):
        self.ensure_one()
        att = self._get_output_attachment("replenishment_rules.parquet")
        if not att:
            return self._no_file_notification("replenishment_rules.parquet")
        return self._download_action(att)

    def action_download_excel(self):
        self.ensure_one()
        att = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "mimir.run"),
            ("res_id", "=", self.id),
            ("name", "=like", "%.xlsx"),
        ], limit=1)
        if not att:
            return self._no_file_notification("Excel file")
        return self._download_action(att)

    def action_download_inventory(self):
        self.ensure_one()
        att = self._get_output_attachment("inventory_analysis.parquet")
        if not att:
            return self._no_file_notification("inventory_analysis.parquet")
        return self._download_action(att)

    def action_download_all_zip(self):
        """Download all output files as a single ZIP."""
        self.ensure_one()
        attachments = self.env["ir.attachment"].sudo().search([
            ("res_model", "=", "mimir.run"),
            ("res_id", "=", self.id),
        ])
        if not attachments:
            return self._no_file_notification("output files")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for att in attachments:
                zf.writestr(att.name, base64.b64decode(att.datas))
        buf.seek(0)

        zip_att = self.env["ir.attachment"].sudo().create({
            "name": f"mimir_run_{self.id}.zip",
            "datas": base64.b64encode(buf.read()).decode(),
            "mimetype": "application/zip",
        })
        return self._download_action(zip_att)

    def _download_action(self, attachment):
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{attachment.id}?download=true",
            "target": "self",
        }

    def _no_file_notification(self, filename):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Mimir",
                "message": f"No {filename} found for this run.",
                "type": "warning",
                "sticky": False,
            },
        }

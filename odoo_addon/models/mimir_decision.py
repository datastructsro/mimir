from odoo import api, fields, models


class MimirDecisionRequest(models.Model):
    _name = "mimir.decision.request"
    _description = "Mimir Decision Request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "run_id desc, product_id"

    run_id = fields.Many2one("mimir.run", string="Forecast Run", required=True, ondelete="cascade", tracking=True)
    decision_id = fields.Char(string="Decision ID", required=True, index=True, tracking=True)
    product_id = fields.Many2one("product.product", string="Product", required=True, tracking=True)
    location_id = fields.Many2one("stock.location", string="Location", required=True, tracking=True)

    proposed_min_qty = fields.Float(string="Proposed Min Qty", tracking=True)
    proposed_max_qty = fields.Float(string="Proposed Max Qty", tracking=True)
    current_min_qty = fields.Float(string="Current Min Qty", compute="_compute_current_qty", store=True)
    current_max_qty = fields.Float(string="Current Max Qty", compute="_compute_current_qty", store=True)

    planned_lead_time = fields.Integer(string="Planned Lead Time (Days)", tracking=True)
    empirical_lead_time = fields.Integer(string="Empirical Lead Time (Days)", tracking=True)

    status = fields.Selection(
        [
            ("draft", "Proposed by AI"),
            ("review", "Under Review"),
            ("approved", "Approved & Applied"),
            ("rejected", "Rejected"),
        ],
        string="Status",
        default="draft",
        tracking=True,
    )

    @api.depends("product_id", "location_id")
    def _compute_current_qty(self):
        for rec in self:
            if rec.product_id and rec.location_id:
                orderpoint = self.env["stock.warehouse.orderpoint"].search(
                    [("product_id", "=", rec.product_id.id), ("location_id", "=", rec.location_id.id)], limit=1
                )
                rec.current_min_qty = orderpoint.product_min_qty if orderpoint else 0.0
                rec.current_max_qty = orderpoint.product_max_qty if orderpoint else 0.0
            else:
                rec.current_min_qty = 0.0
                rec.current_max_qty = 0.0

    def action_approve(self):
        """Approve the decision and apply it to stock.warehouse.orderpoint."""
        for rec in self:
            if rec.status in ("approved", "rejected"):
                continue

            orderpoint = self.env["stock.warehouse.orderpoint"].search(
                [("product_id", "=", rec.product_id.id), ("location_id", "=", rec.location_id.id)], limit=1
            )

            vals = {
                "product_min_qty": rec.proposed_min_qty,
                "product_max_qty": rec.proposed_max_qty,
            }

            if orderpoint:
                orderpoint.write(vals)
                rec.message_post(
                    body=f"Updated existing orderpoint with Min: {rec.proposed_min_qty}, Max: {rec.proposed_max_qty}"
                )
            else:
                vals.update(
                    {
                        "product_id": rec.product_id.id,
                        "location_id": rec.location_id.id,
                        "name": f"Mimir {rec.product_id.display_name}",
                    }
                )
                self.env["stock.warehouse.orderpoint"].create(vals)
                rec.message_post(
                    body=f"Created new orderpoint with Min: {rec.proposed_min_qty}, Max: {rec.proposed_max_qty}"
                )

            rec.status = "approved"

    def action_reject(self):
        """Reject the decision."""
        for rec in self:
            if rec.status == "draft" or rec.status == "review":
                rec.status = "rejected"

    def action_review(self):
        """Mark as under review."""
        for rec in self:
            if rec.status == "draft":
                rec.status = "review"

from odoo import fields, models


class ForeqcastCategoryOverride(models.Model):
    _name = "foreqcast.category.override"
    _description = "Foreqcast Category Override"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    category_id = fields.Many2one("product.category", string="Product Category", required=True, ondelete="cascade", tracking=True)
    safety_factor = fields.Float(string="Safety Factor Override", tracking=True)
    min_data_points = fields.Integer(string="Min Data Points Override", tracking=True)
    lead_time_override_days = fields.Integer(string="Lead Time (Days)", tracking=True)
    review_period_override_days = fields.Integer(string="Review Period (Days)", tracking=True)
    excluded = fields.Boolean(string="Exclude from Forecast", default=False, tracking=True)
    service_level = fields.Float(string="Service Level", tracking=True, help="E.g., 0.95 for 95% service level")

    _sql_constraints = [
        ("category_unique", "unique(category_id)", "An override already exists for this category."),
    ]

    def name_get(self):
        return [(rec.id, f"Override: {rec.category_id.name}") for rec in self]


class ForeqcastProductOverride(models.Model):
    _name = "foreqcast.product.override"
    _description = "Foreqcast Product Override"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    product_id = fields.Many2one("product.product", string="Product", required=True, ondelete="cascade", tracking=True)
    safety_factor = fields.Float(string="Safety Factor Override", tracking=True)
    min_qty_floor = fields.Float(string="Min Qty Floor", tracking=True)
    max_qty_ceiling = fields.Float(string="Max Qty Ceiling", tracking=True)
    lead_time_override_days = fields.Integer(string="Lead Time (Days)", tracking=True)
    excluded = fields.Boolean(string="Exclude from Forecast", default=False, tracking=True)
    service_level = fields.Float(string="Service Level", tracking=True, help="E.g., 0.95 for 95% service level")

    _sql_constraints = [
        ("product_unique", "unique(product_id)", "An override already exists for this product."),
    ]

    def name_get(self):
        return [(rec.id, f"Override: {rec.product_id.display_name}") for rec in self]

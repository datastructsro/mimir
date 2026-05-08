{
    "name": "Mimir — Replenishment Review",
    "version": "18.0.5.0.0",
    "category": "Supply Chain",
    "summary": "Warehouse-scoped replenishment rule import and review for Parqcast exports",
    "description": """
        Mimir reads parqcast parquet exports, imports precomputed min/max
        replenishment rules, and stages them for review in Odoo.

        Features:
        - Single-warehouse review workflow
        - Optional warehouse-scoped remote rules server
        - Excel exports for rule import and forecast evidence review
        - Automatic or manual approval to stock.warehouse.orderpoint
    """,
    "author": "DataStruct s.r.o.",
    "website": "https://datastruct.tech",
    "support": "info@datastruct.tech",
    "depends": ["stock", "parqcast"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "views/mimir_run_views.xml",
        "views/mimir_settings_views.xml",
        "views/mimir_override_views.xml",
        "views/mimir_decision_views.xml",
        "views/mimir_excel_wizard_views.xml",
    ],
    "external_dependencies": {"python": ["pyarrow", "numpy", "openpyxl", "boto3"]},
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}

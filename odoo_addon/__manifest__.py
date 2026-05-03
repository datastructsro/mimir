{
    "name": "Mimir — Demand Forecasting",
    "version": "18.0.3.0.1",
    "category": "Supply Chain",
    "summary": "Linear regression demand forecasting with automatic replenishment rule updates",
    "description": """
        Mimir reads parqcast parquet exports, runs linear regression on historical
        demand, and computes min/max replenishment rules for stock.warehouse.orderpoint.

        Features:
        - Configurable forecast horizon (days/weeks)
        - Per-product and per-category safety factor overrides
        - Automatic or manual push to orderpoints
        - Full audit trail of forecast runs
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

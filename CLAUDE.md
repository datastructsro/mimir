# Foreqcast

Linear regression demand forecaster for the parqcast ecosystem. Reads parqcast parquet exports, projects demand via linear regression, computes min/max replenishment rules, and writes results to both parquet and Odoo 19 orderpoints. Optionally accounts for current inventory position (on-hand, reserved, incoming, outgoing).

## Architecture

```
parqcast parquet exports/          foreqcast pipeline
  sale_order_line.parquet    ──►  reader.py (read parquet inputs)
  product.parquet            ──►  aggregator.py (time series by product x warehouse)
  product_supplierinfo.parquet ►  forecaster.py (linear regression per series)
  stock_warehouse.parquet    ──►  calculator.py (min/max from forecast + lead time)
  orderpoint.parquet         ──►  inventory.py (net position, coverage, flags)
  stock_quant.parquet        ──►  writer.py (output parquet files)
  stock_move.parquet         ──►  pusher.py (XML-RPC to Odoo orderpoints)
  purchase_order_line.parquet ►        │
                                       ▼
                              forecast.parquet
                              replenishment_rules.parquet
                              inventory_analysis.parquet  (when inventory_mode != 'ignore')
                              Odoo stock.warehouse.orderpoint
```

**Two input modes:**
1. **Odoo Attachments (default)** — reads parquet files directly from parqcast's latest `ir.attachment` export. Zero config.
2. **Server Filesystem** — reads from a directory path on the server.

**Output:** Parquet + Excel files stored as `ir.attachment` on the `foreqcast.run` record, with download buttons (ZIP, Excel, Rules, Forecast, Inventory). Optionally push to Odoo orderpoints via XML-RPC.

## Odoo addon integration

The Odoo addon depends on `parqcast`. Both default to "Odoo Attachments" transport/input — no filesystem paths needed for the standard single-instance flow:

1. Parqcast exports data → stores as `ir.attachment` on export run
2. Foreqcast reads attachments from latest parqcast export → runs pipeline
3. Output files stored as `ir.attachment` on forecast run → downloadable from UI

For external server forecasting (advanced), switch both to filesystem/HTTP/S3 transport.

**Localizations:** Czech (cs), Slovak (sk), German (de), Spanish (es) — 134 strings each.

## Setup

```bash
uv sync
uv run pytest tests/ -x -q
uv run foreqcast /path/to/parqcast/exports /path/to/output --verbose
```

## Modules

| File | Purpose |
|------|---------|
| `reader.py` | Read 5+ parqcast parquet files (SOL, product, warehouse, supplierinfo, orderpoint, quant, move, POL) |
| `aggregator.py` | Group SOL demand into daily/weekly buckets per product x warehouse |
| `forecaster.py` | Fit numpy linear regression (polyfit deg=1) per series |
| `calculator.py` | Compute min/max using reorder-point formula with configurable overrides |
| `inventory.py` | Compute net inventory position per product x warehouse, classify health |
| `writer.py` | Write forecast.parquet, replenishment_rules.parquet, inventory_analysis.parquet |
| `excel_export.py` | Export Odoo-importable .xlsx with "Odoo Import" + "Review" sheets |
| `pusher.py` | Push rules to Odoo via XML-RPC create/write on stock.warehouse.orderpoint |
| `pipeline.py` | Orchestrate the full flow end-to-end |
| `cli.py` | Command-line entry point |
| `config.py` | SQLite-backed config with defaults, overrides, and audit trail |
| `schemas.py` | PyArrow output schemas (FORECAST, REPLENISHMENT_RULES, INVENTORY_ANALYSIS) |

## Two config stores

1. **Odoo addon** (`odoo_addon/`) — `res.config.settings` for basic parameters (horizon, safety factor, paths, inventory mode). Install as Odoo 19 addon.
2. **SQLite database** (`foreqcast_config.db`) — per-product overrides, per-category overrides, exclusions, and run audit trail. Managed by `config.py`.

## Key formulas

```
min_qty = forecasted_daily_demand x lead_time_days x safety_factor
max_qty = forecasted_daily_demand x (lead_time_days + review_period_days) x safety_factor
```

## Inventory position (see INVENTORY_CONFIG.md)

Three modes: `ignore` (pure forecast), `analyze` (add inventory columns), `adjust` (modify behavior).

```
net_position = on_hand
             - reserved_qty          (if respect_reservations = true)
             + incoming_supply       (if include_incoming_supply = true)
             - outgoing_demand       (if include_outgoing_demand = true)

coverage_days = net_position / forecasted_daily_demand
```

Products classified as: `ok`, `overstock`, `understock`, `at_risk`, `no_stock`, `no_demand`, `no_data`.

## Conventions

- Python 3.12+, ruff for linting/formatting
- Dependencies: pyarrow, numpy, openpyxl (no sklearn/pandas)
- All timestamps in UTC
- Parquet output uses snappy compression
- Every forecast decision is traceable in forecast.parquet (slope, r², confidence)
- Every inventory decision is traceable in replenishment_rules.parquet (coverage_days, inventory_flag)
- `--excel` flag produces .xlsx with two sheets: "Odoo Import" (uploadable) and "Review" (color-coded analysis)

## Excel export for Odoo import

The `--excel` flag (or `export_to_excel()`) produces a workbook with:
- **"Odoo Import" sheet**: columns `id`, `product_id/.id`, `warehouse_id/.id`, `location_id/.id`, `product_min_qty`, `product_max_qty`, `trigger` — paste directly into Odoo's Import dialog
- **"Review" sheet**: all detail including product names, forecast stats (slope, R², confidence), inventory flags (color-coded), and the computed min/max with parameters used
- External ID pattern `foreqcast.op_{product_id}_{warehouse_id}` ensures re-import updates rather than duplicates
- Odoo 19 natively supports .xlsx import via openpyxl (base_import module)

## Supported Languages

The Odoo addon includes full UI translations for 20 languages:

| Code | Language | Native Name |
|------|----------|-------------|
| ar | Arabic | العربية |
| cs | Czech | Čeština |
| da | Danish | Dansk |
| de | German | Deutsch |
| el | Greek | Ελληνικά |
| es | Spanish | Español |
| et | Estonian | Eesti |
| fi | Finnish | Suomi |
| hr | Croatian | Hrvatski |
| ku | Kurdish (Sorani) | کوردی |
| hu | Hungarian | Magyar |
| nb | Norwegian Bokmål | Norsk bokmål |
| nl | Dutch | Nederlands |
| pl | Polish | Polski |
| pt | Portuguese | Português |
| ro | Romanian | Română |
| sk | Slovak | Slovenčina |
| sl | Slovenian | Slovenščina |
| sv | Swedish | Svenska |

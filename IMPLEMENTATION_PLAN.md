# Foreqcast — Implementation Plan

Instructions for a coding agent to complete, refine, and extend this codebase.

## Current state

The codebase has all core modules implemented as a working first draft.

### Core pipeline
- `reader.py` — reads parqcast parquet files (SOL, product, warehouse, supplierinfo, orderpoint)
- `aggregator.py` — groups SOL demand into daily/weekly buckets per product x warehouse
- `forecaster.py` — fits numpy linear regression (polyfit deg=1) per series
- `calculator.py` — computes min/max using reorder-point formula with configurable overrides
- `writer.py` — writes forecast.parquet and replenishment_rules.parquet
- `pusher.py` — pushes rules to Odoo via XML-RPC create/write on stock.warehouse.orderpoint
- `pipeline.py` — orchestrates the full flow
- `cli.py` — command-line entry point

### Inventory position (v0.2.0)
- `inventory.py` — reads stock_quant, stock_move, purchase_order_line parquets; computes net position per product x warehouse; classifies inventory health (overstock/understock/ok/at_risk/no_stock/no_demand)
- Three modes: `ignore` (pure forecast), `analyze` (add inventory columns to output), `adjust` (skip overstock, boost understock safety)
- `respect_reservations` flag: subtract reserved_qty from on-hand (conservative) or ignore reservations (optimistic replanning)
- See INVENTORY_CONFIG.md for full documentation

### Configuration
- `config.py` — SQLite-backed config with ~21 settings, per-product/category overrides, exclusion lists, and run audit trail
- `schemas.py` — PyArrow output schemas: FORECAST_SCHEMA, REPLENISHMENT_RULES_SCHEMA (with 7 inventory columns), INVENTORY_ANALYSIS_SCHEMA

### Odoo 19 addon
- `odoo_addon/` — `res.config.settings` with all settings exposed in a Settings UI
- Forecast parameters, data quality, integration paths, and inventory position sections
- `foreqcast.run` model for run history with list/form views
- Default `ir.config_parameter` records seeded on install

### Tests
- `tests/test_forecaster.py` — linear regression: constant, growing, declining demand; insufficient data; weekly normalization
- `tests/test_calculator.py` — min/max formula; threshold skipping; product overrides; floor/ceiling; exclusion; max >= min invariant

---

## What needs work

### Priority 1: Wire inventory.py into the pipeline

The `inventory.py` module exists but is NOT yet called from `pipeline.py`. This is the most important integration task.

1. **pipeline.py** — After step 3 (forecast) and before step 4 (calculate), add:
   ```python
   # Between forecasting and calculating:
   inventory_positions = {}
   if config.inventory_mode != "ignore":
       from .inventory import InventoryConfig, load_inventory_positions, classify_inventory
       inv_config = InventoryConfig(
           inventory_mode=config.inventory_mode,
           respect_reservations=config.respect_reservations,
           include_incoming_supply=config.include_incoming_supply,
           include_outgoing_demand=config.include_outgoing_demand,
           incoming_sources=config.incoming_sources,
           outgoing_sources=config.outgoing_sources,
           overstock_threshold_days=config.overstock_threshold_days,
           understock_threshold_days=config.understock_threshold_days,
           overstock_skip=config.overstock_skip,
           understock_urgency_multiplier=config.understock_urgency_multiplier,
       )
       inventory_positions = load_inventory_positions(parquet_input_dir, warehouse_locations, inv_config)
   ```

2. **calculator.py** — `calculate_rule()` needs to accept an optional `InventoryPosition` and `InventoryConfig`. In `adjust` mode:
   - If product is overstocked and `overstock_skip=true` → `action='skip'`, `skip_reason='overstocked'`
   - If product is understocked → multiply `safety_factor` by `understock_urgency_multiplier`
   - Always populate the 7 inventory columns on the returned `ReplenishmentRule`

3. **writer.py** — `write_rules()` must populate the 7 new inventory columns in REPLENISHMENT_RULES_SCHEMA: `on_hand_qty`, `reserved_qty`, `incoming_supply_qty`, `outgoing_demand_qty`, `net_available_qty`, `coverage_days`, `inventory_flag`. Currently these columns exist in the schema but are not written.

4. **writer.py** — Add `write_inventory_analysis()` function that writes `inventory_analysis.parquet` using INVENTORY_ANALYSIS_SCHEMA. Called from pipeline when `inventory_mode != 'ignore'`.

5. **reader.py** — Extend `ParqcastData` and `read_parqcast_export()` to optionally load `stock_quant.parquet`, `stock_move.parquet`, `purchase_order_line.parquet` when inventory_mode is not `ignore`. These files should be loaded with only the columns needed by `inventory.py`.

6. **ReplenishmentRule dataclass** in `calculator.py` — Add the 7 inventory fields with defaults (0.0 / None / "no_data").

6b. **excel_export.py** — Already implemented. The `--excel` CLI flag triggers it after parquet write. Produces two-sheet .xlsx:
   - "Odoo Import" sheet: `id`, `product_id/.id`, `warehouse_id/.id`, `location_id/.id`, `product_min_qty`, `product_max_qty`, `trigger` — uses database IDs for unambiguous import, external ID `foreqcast.op_{pid}_{wid}` for idempotent create/update
   - "Review" sheet: full detail with color-coded inventory flags, forecast quality stats
   - **Integration needed in pipeline.py**: call `export_to_excel()` after `write_rules()` when config enables it (or always when `--excel` flag is passed). Currently only CLI calls it.
   - **Testing needed**: `tests/test_excel_export.py` — create a minimal rules parquet, export to xlsx, verify sheet names, column headers match Odoo expectations, row count matches non-skipped rules

### Priority 2: Make the pipeline robust

7. **aggregator.py** — The current implementation converts entire PyArrow columns to Python lists. This works but uses ~2GB RAM for 10M SOLs. Options:
   - Use PyArrow compute `pc.group_by()` (pyarrow >= 15.0)
   - Or chunked processing: `pq.read_table(path, batch_size=100_000)` and aggregate incrementally
   - Handle timezone-aware vs naive datetime edge cases (`date_order` comes as `timestamp[us, UTC]`)

8. **forecaster.py** — Edge cases:
   - Products with all-zero demand (division by zero in R²) — return `r_squared=0`
   - Products with demand in only 1 bucket — can't fit a line, return `confidence='insufficient_data'`
   - Very large slopes (extrapolation unreliable) — cap `forecasted_daily_demand` at `3 * avg_daily_demand`
   - NaN/inf from numpy — wrap `polyfit` in try/except, fall back to average

9. **writer.py** — `product_name` and `warehouse_code` fields are empty strings in `write_forecasts()`. Pass lookup dicts from the pipeline and populate them.

10. **pusher.py** — Currently does one XML-RPC roundtrip per product. For 53K products this is ~53K HTTP calls. Optimize:
    - Batch `search_read` all existing orderpoints at start
    - Group creates into batches of ~100 using Odoo's batch create support
    - Add retry logic with exponential backoff for connection failures

### Priority 3: Odoo addon completeness

11. **"Run Now" button** — Add a button to `res.config.settings` that triggers the pipeline:
    - Method reads config parameters via `self.env['ir.config_parameter'].sudo().get_param()`
    - Instantiates pipeline and runs it
    - Creates a `foreqcast.run` record
    - Returns a notification action with results

12. **Security** — Add `security/ir.model.access.csv`:
    ```csv
    id,name,model_id:id,group_id:id,perm_read,perm_write,perm_create,perm_unlink
    access_foreqcast_run_manager,foreqcast.run.manager,model_foreqcast_run,stock.group_stock_manager,1,1,1,1
    access_foreqcast_run_user,foreqcast.run.user,model_foreqcast_run,stock.group_stock_user,1,0,0,0
    ```
    Update `__manifest__.py` data list to include this file.

13. **Odoo 19 XML validation** — Verify all view XML:
    - `invisible` uses new Odoo 19 Python expression syntax (not old `attrs` dict)
    - Settings page renders under Settings, not as standalone menu
    - Inventory Position section conditional visibility works correctly

14. **Inventory config in Odoo addon ir_config_parameter.xml** — Add default records for the 10 inventory settings (inventory_mode, respect_reservations, etc.). Currently only the original 9 settings have defaults seeded.

### Priority 4: Testing

15. **tests/test_inventory.py** — Test the inventory module:
    - `InventoryPosition.net_position()` with all flag combinations
    - `coverage_days()` with zero demand (returns None)
    - `classify_inventory()` threshold classifications
    - `load_inventory_positions()` with mock parquet files

16. **tests/test_aggregator.py** — Test demand aggregation:
    - Daily vs weekly bucketing
    - Filtering cancelled orders (`exclude_states`)
    - Null product_id/warehouse_id rows handled gracefully
    - Empty input table returns empty dict

17. **tests/test_pipeline.py** — Integration test:
    - Create minimal parquet files with known data (5 products, 2 warehouses, 30 days of SOLs)
    - Run pipeline with inventory_mode='analyze'
    - Read output parquet files and verify:
      - forecast.parquet has correct slopes
      - replenishment_rules.parquet has correct min/max
      - inventory columns are populated when mode is 'analyze'
    - Run again with inventory_mode='adjust' and verify overstock skipping works

18. **tests/test_config.py** — Test SQLite config:
    - `init_db` creates tables and seeds all 21 defaults
    - `load_config` reads all settings including inventory settings
    - `incoming_sources` parsed as comma-separated set
    - Boolean settings parsed correctly from strings

### Priority 5: JSON-2 API

19. **JSON-2 API support** — Odoo 19 introduced `/api/` endpoint. XML-RPC deprecated, removal in Odoo 22.
    - Add `pusher_json2.py` using `requests` library
    - `POST {url}/api/stock.warehouse.orderpoint/call/create`
    - Auth via API key header: `Authorization: Bearer {key}`
    - Config: `odoo_api_mode = 'xmlrpc' | 'json2'`

---

## Data context (from the connected Odoo database)

- **10.5M sale order lines** across 53K products, spanning Feb 2021 to Jun 2022
- **4 warehouses**: WH (main), D02 (Ibn Battuta Mall), D03 (Cityland Mall), SD2 (Jeddah)
- **0 existing orderpoints** — foreqcast will create all new rules
- **0 supplier info** — all products use default_lead_time_days
- **0 stock quants** — no on-hand inventory (historical dataset)
- **858K stock moves** — all in 'done' state (no open moves)
- Product types: food & beverage distribution (chicken, juice, snacks, etc.)

## File structure

```
foreqcast/
├── CLAUDE.md                           # Codebase overview for coding agents
├── IMPLEMENTATION_PLAN.md              # This file — step-by-step instructions
├── INVENTORY_CONFIG.md                 # Detailed inventory config documentation
├── pyproject.toml                      # Package: pyarrow, numpy, hatchling
├── src/foreqcast/
│   ├── __init__.py                     # __version__ = "0.1.0"
│   ├── aggregator.py                   # Demand time series aggregation
│   ├── calculator.py                   # Min/max qty calculator with overrides
│   ├── cli.py                          # CLI entry point
│   ├── config.py                       # SQLite config: 21 settings, overrides, audit
│   ├── forecaster.py                   # Linear regression (numpy polyfit)
│   ├── inventory.py                    # Inventory position: net qty, coverage, flags
│   ├── excel_export.py                 # Odoo-importable .xlsx with Review + Import sheets
│   ├── pipeline.py                     # End-to-end orchestration
│   ├── pusher.py                       # Odoo XML-RPC orderpoint create/update
│   ├── reader.py                       # Parqcast parquet file reader
│   ├── schemas.py                      # 3 output schemas (FORECAST, RULES, INVENTORY)
│   └── writer.py                       # Parquet output writer
├── odoo_addon/
│   ├── __manifest__.py                 # Odoo 19 addon: depends stock
│   ├── __init__.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── foreqcast_settings.py       # res.config.settings: ~20 fields
│   │   └── foreqcast_run.py            # foreqcast.run: run history model
│   ├── views/
│   │   ├── foreqcast_settings_views.xml  # Settings UI: 5 blocks
│   │   └── foreqcast_run_views.xml       # Run list + form views
│   └── data/
│       └── ir_config_parameter.xml     # Default config values
└── tests/
    ├── test_forecaster.py              # 6 tests: constant/growing/declining/insufficient/weekly
    └── test_calculator.py              # 6 tests: formula/threshold/override/exclusion/invariant
```

## Odoo 19 API reference

### stock.warehouse.orderpoint fields
```
id, product_id, warehouse_id, location_id, company_id,
product_min_qty, product_max_qty, trigger ('auto'/'manual'),
route_id, supplier_id, bom_id, replenishment_uom_id,
qty_to_order_computed, qty_to_order_manual,
active, snoozed_until, deadline_date
```

### XML-RPC pattern (current, deprecated)
```python
import xmlrpc.client
common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
uid = common.authenticate(db, user, password, {})
models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
models.execute_kw(db, uid, password, 'stock.warehouse.orderpoint', 'create', [vals])
```

### JSON-2 API pattern (Odoo 19+, preferred)
```
POST {url}/api/stock.warehouse.orderpoint/call/create
Header: Authorization: Bearer {api_key}
Body: {"params": {"vals": {"product_id": 1, "warehouse_id": 1, ...}}}
```

XML-RPC deprecated, scheduled for removal in Odoo 22 (fall 2028).

### Inventory position — how Odoo uses min/max
Odoo's replenishment scheduler checks: `if forecasted_qty < product_min_qty → replenish`. Where `forecasted_qty = on_hand + incoming - outgoing`. The scheduler itself handles the inventory math — foreqcast's min/max are **policy levels** (the safety net), not order quantities. The inventory analysis in foreqcast is for **visibility and tuning**, not for replacing Odoo's triggering logic.

Sources:
- [Odoo 19 External RPC API](https://www.odoo.com/documentation/19.0/developer/reference/external_rpc_api.html)
- [Odoo 19 External JSON-2 API](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html)
- [Odoo 19 Reordering Rules](https://www.odoo.com/documentation/19.0/applications/inventory_and_mrp/inventory/warehouses_storage/replenishment/reordering_rules.html)
- [res.config.settings pattern](https://odoo-development.readthedocs.io/en/latest/dev/py/res.config.settings.html)


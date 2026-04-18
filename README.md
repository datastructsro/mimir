# Foreqcast

Linear regression demand forecaster for the parqcast ecosystem. Reads [parqcast](https://github.com/datastructsro/parqcast) parquet exports, projects demand, computes min/max replenishment rules, and writes them back to Odoo `stock.warehouse.orderpoint` records.

Currently supported: Odoo 18, 19.

**Author:** [DataStruct s.r.o.](https://datastruct.tech) — info@datastruct.tech

## Setup

Requires Python 3.12.3 (Odoo.sh compatibility).

```bash
uv sync                      # install workspace + dev deps
uv run pytest tests/ -x -q   # run tests
uv run ruff check .          # lint
```

## Odoo addon

The addon at `odoo_addon/` reads parqcast's `ir.attachment` exports, runs the pipeline, and stores forecast + rules as attachments on a `foreqcast.run` record. Optionally pushes rules to `stock.warehouse.orderpoint` via XML-RPC.

## CLI

```bash
uv run foreqcast /path/to/parqcast/exports /path/to/output --verbose
```

## Test data

`scripts/generate_test_data.py` produces a deterministic set of sample parquet inputs under `test_data/` (gitignored).

## License

LGPL-3.0-or-later

# Mimir 📈

[![Odoo Version](https://img.shields.io/badge/Odoo-18%20%7C%2019-blue.svg)](https://www.odoo.com)
[![Python](https://img.shields.io/badge/Python-3.12.3-blue.svg)](https://www.python.org/downloads/release/python-3123/)
[![License](https://img.shields.io/badge/License-LGPL--3.0-green.svg)](https://www.gnu.org/licenses/lgpl-3.0.html)

**Mimir** is a warehouse-scoped replenishment review client for the [Parqcast](https://github.com/datastructsro/parqcast) ecosystem. It imports precomputed min/max rules from `mimir-server`, stages them for approval in Odoo, and keeps forecast output only as a confidence-building Excel artifact.

## 🚀 Overview

Mimir reads warehouse-scoped Parquet artifacts published by `mimir-server`, imports precomputed `minmax.parquet` rules for one warehouse at a time, and stages them back to Odoo `stock.warehouse.orderpoint` records for review or approval.

Currently supported Odoo versions: **18, 19**

## ✨ Features

- **Warehouse-Scoped Imports:** Focuses each run on a single warehouse to keep review and approval manageable.
- **Precomputed Rule Consumption:** Imports upstream `minmax.parquet` rules instead of recalculating them locally.
- **Seamless Odoo Integration:** Stages imported rules directly on `stock.warehouse.orderpoint` review records.
- **Multilingual Support:** Ships with UI translations for 19 languages out of the box.

## 🛠️ Setup & Installation

**Prerequisites:** Python 3.12.3 (Required for Odoo.sh compatibility).

The project uses `uv` for dependency management.

```bash
# Install workspace + development dependencies
uv sync

# Run the test suite
uv run pytest tests/ -x -q

# Lint the codebase
uv run ruff check .
```

## 📖 Usage

### Odoo Addon

The addon (located in the `odoo_addon/` directory) integrates directly into Odoo:
1. It preflights the configured `mimir-server` for the selected warehouse.
2. It downloads warehouse-scoped min/max rules and forecast evidence from that server.
3. It enriches those artifacts with live Odoo metadata for the selected warehouse and products.
4. It stores the rule outputs and forecast evidence workbook as attachments on a `mimir.run` record.
5. Optionally, it auto-approves those staged rules into `stock.warehouse.orderpoint`.

The import/forecast core is bundled directly inside the addon under `odoo_addon/mimir_core/`.

### External Server Contract

Mimir treats `mimir-server` as the only source of truth for imported replenishment artifacts. It does not read local Parquet bundles anymore; the only local files it creates are temporary working files and the final output artifacts.

- Configure the server root, for example `https://mimir.datastruct.tech`
- Authenticate with the `X-API-Key` header
- Preflight checks call `/health` and `/warehouses`
- Rules are downloaded from `/warehouses/{warehouse}/rules/latest`
- Forecast evidence is downloaded from `/warehouses/{warehouse}/forecast/latest`
- Empirical lead times are optional and, when available, are downloaded from `/lead-times/latest`

Warehouse-scoped artifacts may identify a warehouse by either:

- Odoo warehouse ID via `_odoo_warehouse_id`
- Warehouse code via `facility_id`

Mimir resolves both forms against the selected Odoo warehouse before importing.

## 🧪 Testing & Development

### Test Data Generation
A deterministic set of sample Parquet inputs can be generated for testing. The generated files are stored under `test_data/` (which is git-ignored).

```bash
uv run python scripts/generate_test_data.py
```

### Locales

The addon includes UI translations for **19 languages**. All `msgstr` entries are fully populated (see `odoo_addon/i18n/`). Translation PRs are always welcome!

| | | |
|---|---|---|
| العربية (ar) | Čeština (cs) | Dansk (da) |
| Deutsch (de) | Ελληνικά (el) | Español (es) |
| Eesti (et) | Suomi (fi) | Hrvatski (hr) |
| Magyar (hu) | Kurdî (ku) | Norsk Bokmål (nb) |
| Nederlands (nl) | Polski (pl) | Português (pt) |
| Română (ro) | Slovenčina (sk) | Slovenščina (sl) |
| Svenska (sv) | | |

## 🏢 About DataStruct

Built and maintained by **[DataStruct s.r.o.](https://datastruct.tech)** — an **[Odoo Official Partner](https://www.odoo.com/partners)** based in the Czech Republic. We specialize in demand forecasting and ERP implementations for manufacturing, retail, and logistics in the Czech–German–Polish region.

- **See it in production:** [Demand-forecast case study](https://datastruct.tech/post/demand-forecasts-shift-planning) · [Machine-builder materials: 67% → 94%](https://datastruct.tech/post/czech-machine-builder-material-delays)
- **Book a 30-min technical diagnostic:** [Schedule here](https://cal.com/oleg-popov-sjwko9/30min)
- **Questions / Partnership:** info@datastruct.tech

**Companion Projects:**
- [parqcast](https://github.com/datastructsro/parqcast) (The primary exporter this tool reads from)
- [parqcast-lambda](https://github.com/datastructsro/parqcast-lambda)
- [parqcast-server](https://github.com/datastructsro/parqcast-server)

## 📄 License

This project is licensed under the **LGPL-3.0-or-later** license.

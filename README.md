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

## Locales

The addon ships with UI translations in 19 languages. All `msgstr` entries
are populated (see `odoo_addon/i18n/`):

| | | |
|---|---|---|
| العربية (ar) | Čeština (cs) | Dansk (da) |
| Deutsch (de) | Ελληνικά (el) | Español (es) |
| Eesti (et) | Suomi (fi) | Hrvatski (hr) |
| Magyar (hu) | Kurdî (ku) | Norsk Bokmål (nb) |
| Nederlands (nl) | Polski (pl) | Português (pt) |
| Română (ro) | Slovenčina (sk) | Slovenščina (sl) |
| Svenska (sv) | | |

Translation PRs welcome.

## About

Built and maintained by **[DataStruct s.r.o.](https://datastruct.tech)** — an **[Odoo Official Partner](https://www.odoo.com/partners)** based in the Czech Republic, specialising in demand forecasting and ERP implementation for manufacturing, retail, and logistics in the Czech–German–Polish triangle.

- **See it in production:** [demand-forecast case study](https://datastruct.tech/post/demand-forecasts-shift-planning) · [machine-builder materials: 67% → 94%](https://datastruct.tech/post/czech-machine-builder-material-delays)
- **Book a 30-min technical diagnostic:** [cal.com/oleg-popov-sjwko9/30min](https://cal.com/oleg-popov-sjwko9/30min)
- **Questions / partnership:** info@datastruct.tech
- **Companion:** [parqcast](https://github.com/datastructsro/parqcast) (the exporter this reads from) · [parqcast-lambda](https://github.com/datastructsro/parqcast-lambda) · [parqcast-server](https://github.com/datastructsro/parqcast-server)

## License

LGPL-3.0-or-later

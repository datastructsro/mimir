# AI Agents Context Guide 🤖

Welcome, AI Agent! This document outlines the technical context and coding standards for the **Mimir** project to help you assist effectively.

## 📌 Project Overview
Mimir is a linear-regression demand-forecasting tool for the **Parqcast** ecosystem. It bridges the gap between historical Parquet exports and Odoo `stock.warehouse.orderpoint` replenishment records.

- **Stack:** Python 3.12.3, Odoo 18/19 compatibility, `uv` & `hatchling` for dependency/build management.
- **Key Domain:** Logistics, inventory replenishment, demand forecasting.
- **Maintainer:** DataStruct s.r.o.

## 🏗️ Architecture & Pipeline Specs
The bundled import/forecast core lives in `odoo_addon/mimir_core/`. The main orchestrator is `pipeline.py`, which works with the following modules:
1. **Server Client:** Talks to `mimir-server` health, warehouse, rules, forecast, and lead-time endpoints (`server_client.py`).
2. **Importer:** Downloads remote artifacts and filters them for the selected warehouse (`importer.py`).
3. **Runtime Context:** Resolves the minimal Odoo-side warehouse, product, and orderpoint context needed for normalization (`runtime_context.py`).
4. **Calculator:** Applies lead times, review periods, service levels, and inventory adjustments to determine staged rules (`calculator.py`).
5. **Inventory Logic:** Evaluates stock position, incoming/outgoing supply, and understock/overstock classification (`inventory.py`).
6. **Writer / Excel Export:** Outputs `forecast_evidence.parquet`, `replenishment_rules.parquet`, `decisions.parquet`, and review workbooks (`writer.py`, `excel_export.py`).
7. **Odoo Entry Point:** Runs the import pipeline from Odoo settings and stores results on `mimir.run` records (`odoo_addon/models/mimir_settings.py`).

An SQLite database (`mimir_config.db`) is maintained locally for an audit trail of pipeline runs.

## 🛠 Coding Standards
1. **Linting & Formatting:** The project strictly follows `ruff` rules. Ensure your code passes `uv run ruff check .`. **Agents must ALWAYS run `uv run ruff check --fix .` and `uv run ruff format .` prior to making any commits to ensure the CI pipeline does not fail.**
2. **Type Hinting:** Pyright is configured with `typeCheckingMode = "basic"`. Strict Python type hints are mandatory.
3. **Tests:** All logic must be covered by `pytest`. Place new tests in the `tests/` directory and ensure they pass with `uv run pytest tests/ -x -q`.
4. **Odoo Standards:** Follow the OCA (Odoo Community Association) guidelines for modifications within the `odoo_addon/` directory.

## 📝 Commit Guidelines
- Use conventional commits (e.g., `feat:`, `fix:`, `docs:`, `chore:`).
- Keep PRs scoped to a single logical change.

## 🛑 What NOT to do
- Avoid hardcoding translations; use Odoo's standard `_()` translation functions.
- Do not modify Pyright or Ruff configurations to bypass type checking or linting constraints.

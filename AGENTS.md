# AI Agents Context Guide 🤖

Welcome, AI Agent! This document outlines the technical context and coding standards for the **Foreqcast** project to help you assist effectively.

## 📌 Project Overview
Foreqcast is a linear-regression demand-forecasting tool for the **Parqcast** ecosystem. It bridges the gap between historical Parquet exports and Odoo `stock.warehouse.orderpoint` replenishment records.

- **Stack:** Python 3.12.3, Odoo 18/19 compatibility, `uv` & `hatchling` for dependency/build management.
- **Key Domain:** Logistics, inventory replenishment, demand forecasting.
- **Maintainer:** DataStruct s.r.o.

## 🏗️ Architecture & Pipeline Specs
The core application lives in `src/foreqcast/`. The main orchestrator is `pipeline.py`, which executes the following distinct phases:
1. **Reader:** Loads parqcast Parquet exports using `pyarrow` (`reader.py`).
2. **Inventory Loader:** Fetches current inventory positions, evaluating modes (e.g., handling reservations and incoming/outgoing stock logic via `inventory.py`).
3. **Aggregator:** Buckets demand time series (`aggregator.py`).
4. **Forecaster:** Runs linear regression (`fit_demand`) using NumPy and computes quantile reorder points (`forecaster.py`).
5. **Calculator:** Applies lead times, review periods, and service levels to determine min/max rules (`calculator.py`).
6. **Writer:** Outputs `forecasts.parquet`, `replenishment_rules.parquet`, and optionally `inventory_analysis.parquet` (`writer.py`).
7. **Pusher:** Pushes finalized rules directly to Odoo via XML-RPC (`pusher.py`).

An SQLite database (`foreqcast_config.db`) is maintained locally for an audit trail of pipeline runs.

## 🛠 Coding Standards
1. **Linting & Formatting:** The project strictly follows `ruff` rules. Ensure your code passes `uv run ruff check .`.
2. **Type Hinting:** Pyright is configured with `typeCheckingMode = "basic"`. Strict Python type hints are mandatory.
3. **Tests:** All logic must be covered by `pytest`. Place new tests in the `tests/` directory and ensure they pass with `uv run pytest tests/ -x -q`.
4. **Odoo Standards:** Follow the OCA (Odoo Community Association) guidelines for modifications within the `odoo_addon/` directory.

## 📝 Commit Guidelines
- Use conventional commits (e.g., `feat:`, `fix:`, `docs:`, `chore:`).
- Keep PRs scoped to a single logical change.

## 🛑 What NOT to do
- Do not introduce breaking changes to the CLI arguments without updating the `README.md`.
- Avoid hardcoding translations; use Odoo's standard `_()` translation functions.
- Do not modify Pyright or Ruff configurations to bypass type checking or linting constraints.

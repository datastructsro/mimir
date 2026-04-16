"""Push replenishment rules to Odoo via XML-RPC.

Reads replenishment_rules.parquet and creates/updates
stock.warehouse.orderpoint records in the Odoo database.

Uses XML-RPC (Odoo 19 also supports JSON-2 API at /api/).
"""

from __future__ import annotations

import logging
import time
import xmlrpc.client
from functools import wraps
from pathlib import Path

import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

SEARCH_BATCH_SIZE = 500
CREATE_BATCH_SIZE = 100


def _retry(max_retries=3, base_delay=1.0, max_delay=30.0):
    """Retry decorator with exponential backoff for transient network errors."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except (ConnectionError, xmlrpc.client.ProtocolError, OSError) as e:
                    if attempt == max_retries:
                        raise
                    logger.warning("Retry %d/%d for %s: %s", attempt + 1, max_retries, func.__name__, e)
                    time.sleep(delay)
                    delay = min(delay * 2, max_delay)

        return wrapper

    return decorator


class OdooPusher:
    """Push orderpoint rules to Odoo via XML-RPC."""

    def __init__(self, url: str, db: str, user: str, password: str):
        self.url = url.rstrip("/")
        self.db = db
        self.user = user
        self.password = password
        self.uid = None
        self.models = None

    def authenticate(self):
        """Authenticate with Odoo and get uid."""
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        self.uid = common.authenticate(self.db, self.user, self.password, {})
        if not self.uid:
            raise RuntimeError(f"Authentication failed for {self.user}@{self.db}")
        self.models = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object")
        logger.info("Authenticated as uid=%d on %s", self.uid, self.db)

    @_retry()
    def _execute(self, model: str, method: str, *args, **kwargs):
        """Call execute_kw on the Odoo models proxy."""
        return self.models.execute_kw(
            self.db, self.uid, self.password,
            model, method, *args, **kwargs,
        )

    def push_rules(self, rules_parquet: str | Path) -> dict:
        """Read rules parquet and push to Odoo.

        Batches search_read at start and creates in groups of ~100.

        Returns summary: {created: N, updated: N, skipped: N, errors: N}
        """
        if not self.uid:
            self.authenticate()

        table = pq.read_table(str(rules_parquet))
        actions = table.column("action").to_pylist()
        product_ids = table.column("_odoo_product_id").to_pylist()
        warehouse_ids = table.column("_odoo_warehouse_id").to_pylist()
        location_ids = table.column("_odoo_location_id").to_pylist()
        min_qtys = table.column("product_min_qty").to_pylist()
        max_qtys = table.column("product_max_qty").to_pylist()
        triggers = table.column("trigger").to_pylist()

        stats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}

        # Batch-load all existing orderpoints at start
        all_pids = list({pid for pid, act in zip(product_ids, actions) if act != "skip"})
        existing_ops = self._batch_load_orderpoints(all_pids)
        logger.info("Found %d existing orderpoints", len(existing_ops))

        # Sort rows into creates and updates
        creates = []
        updates = []
        for i in range(table.num_rows):
            if actions[i] == "skip":
                stats["skipped"] += 1
                continue

            pid, wid = product_ids[i], warehouse_ids[i]
            lid = location_ids[i]
            min_qty, max_qty = min_qtys[i], max_qtys[i]
            trigger = triggers[i] or "auto"
            op_id = existing_ops.get((pid, wid))

            if op_id is not None:
                updates.append((op_id, {"product_min_qty": min_qty, "product_max_qty": max_qty}))
            else:
                vals = {
                    "product_id": pid,
                    "warehouse_id": wid,
                    "product_min_qty": min_qty,
                    "product_max_qty": max_qty,
                    "trigger": trigger,
                }
                if lid:
                    vals["location_id"] = lid
                creates.append(vals)

        # Batch creates
        for batch_start in range(0, len(creates), CREATE_BATCH_SIZE):
            batch = creates[batch_start:batch_start + CREATE_BATCH_SIZE]
            try:
                self._execute("stock.warehouse.orderpoint", "create", [batch])
                stats["created"] += len(batch)
            except Exception as e:
                logger.error("Batch create failed at offset %d: %s", batch_start, e)
                stats["errors"] += len(batch)

        # Updates (Odoo write takes single ID + vals)
        for op_id, vals in updates:
            try:
                self._execute("stock.warehouse.orderpoint", "write", [[op_id], vals])
                stats["updated"] += 1
            except Exception as e:
                logger.error("Update failed for orderpoint %d: %s", op_id, e)
                stats["errors"] += 1

        logger.info(
            "Push complete: %d created, %d updated, %d skipped, %d errors",
            stats["created"], stats["updated"], stats["skipped"], stats["errors"],
        )
        return stats

    def _batch_load_orderpoints(self, product_ids: list[int]) -> dict[tuple[int, int], int]:
        """Batch search_read existing orderpoints. Returns {(pid, wid): op_id}."""
        existing = {}
        for batch_start in range(0, len(product_ids), SEARCH_BATCH_SIZE):
            batch_pids = product_ids[batch_start:batch_start + SEARCH_BATCH_SIZE]
            try:
                records = self._execute(
                    "stock.warehouse.orderpoint", "search_read",
                    [[["product_id", "in", batch_pids]]],
                    {"fields": ["product_id", "warehouse_id"], "limit": False},
                )
                for rec in records:
                    key = (rec["product_id"][0], rec["warehouse_id"][0])
                    existing[key] = rec["id"]
            except Exception as e:
                logger.error("Batch search_read failed at offset %d: %s", batch_start, e)
        return existing

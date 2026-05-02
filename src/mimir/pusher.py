"""Push replenishment rules to Odoo via XML-RPC.

Reads replenishment_rules.parquet and creates/updates
stock.warehouse.orderpoint records in the Odoo database.

Uses XML-RPC (Odoo 19 also supports JSON-2 API at /api/).
"""

from __future__ import annotations

import logging
import time
import xmlrpc.client
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Any, TypedDict, cast

import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

SEARCH_BATCH_SIZE = 500
CREATE_BATCH_SIZE = 100


class PushStats(TypedDict):
    created: int
    updated: int
    skipped: int
    errors: int


@dataclass(frozen=True)
class OdooSession:
    """Authenticated XML-RPC session against an Odoo instance.

    Unlike OdooPusher, which can exist in either unauthenticated or
    authenticated state, OdooSession is only ever constructed after a
    successful authenticate() call — so uid and models are never None
    at the type level. Pass it into any method that needs to make RPC
    calls; pyright enforces that untyped-state access is impossible.
    """

    url: str
    db: str
    password: str
    uid: int
    models: xmlrpc.client.ServerProxy


def _retry(max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 30.0):
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

    def __init__(self, url: str, db: str, user: str, password: str) -> None:
        self.url = url.rstrip("/")
        self.db = db
        self.user = user
        self.password = password
        self._session: OdooSession | None = None

    def connect(self) -> OdooSession:
        """Authenticate if not already, then return the active session."""
        if self._session is not None:
            return self._session
        common = xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/common")
        uid = common.authenticate(self.db, self.user, self.password, {})
        if not uid or not isinstance(uid, int):
            raise RuntimeError(f"Authentication failed for {self.user}@{self.db}")
        self._session = OdooSession(
            url=self.url,
            db=self.db,
            password=self.password,
            uid=uid,
            models=xmlrpc.client.ServerProxy(f"{self.url}/xmlrpc/2/object"),
        )
        logger.info("Authenticated as uid=%d on %s", uid, self.db)
        return self._session

    @_retry()
    def _execute(self, session: OdooSession, model: str, method: str, *args, **kwargs) -> Any:
        """Call execute_kw on the session's models proxy."""
        return session.models.execute_kw(
            session.db, session.uid, session.password,
            model, method, *args, **kwargs,
        )

    def push_rules(self, rules_parquet: str | Path) -> PushStats:
        """Read rules parquet and push to Odoo.

        Batches search_read at start and creates in groups of ~100.
        """
        session = self.connect()

        table = pq.read_table(str(rules_parquet))
        actions = table.column("action").to_pylist()
        product_ids = table.column("_odoo_product_id").to_pylist()
        warehouse_ids = table.column("_odoo_warehouse_id").to_pylist()
        location_ids = table.column("_odoo_location_id").to_pylist()
        min_qtys = table.column("product_min_qty").to_pylist()
        max_qtys = table.column("product_max_qty").to_pylist()
        triggers = table.column("trigger").to_pylist()

        stats: PushStats = {"created": 0, "updated": 0, "skipped": 0, "errors": 0}

        # Batch-load existing orderpoints — ignore null pids in the rules file
        # (the parquet schema allows them, though it's unusual in practice).
        all_pids: list[int] = list({
            pid for pid, act in zip(product_ids, actions)
            if act != "skip" and pid is not None
        })
        existing_ops = self._batch_load_orderpoints(session, all_pids)
        logger.info("Found %d existing orderpoints", len(existing_ops))

        # Sort rows into creates and updates
        creates: list[dict[str, Any]] = []
        updates: list[tuple[int, dict[str, Any]]] = []
        for i in range(table.num_rows):
            if actions[i] == "skip":
                stats["skipped"] += 1
                continue

            pid, wid = product_ids[i], warehouse_ids[i]
            if pid is None or wid is None:
                stats["skipped"] += 1
                continue
            lid = location_ids[i]
            min_qty, max_qty = min_qtys[i], max_qtys[i]
            trigger = triggers[i] or "auto"
            op_id = existing_ops.get((pid, wid))

            if op_id is not None:
                updates.append((op_id, {"product_min_qty": min_qty, "product_max_qty": max_qty}))
            else:
                vals: dict[str, Any] = {
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
                self._execute(session, "stock.warehouse.orderpoint", "create", [batch])
                stats["created"] += len(batch)
            except Exception as e:
                logger.error("Batch create failed at offset %d: %s", batch_start, e)
                stats["errors"] += len(batch)

        # Updates (Odoo write takes single ID + vals)
        for op_id, vals in updates:
            try:
                self._execute(session, "stock.warehouse.orderpoint", "write", [[op_id], vals])
                stats["updated"] += 1
            except Exception as e:
                logger.error("Update failed for orderpoint %d: %s", op_id, e)
                stats["errors"] += 1

        logger.info(
            "Push complete: %d created, %d updated, %d skipped, %d errors",
            stats["created"], stats["updated"], stats["skipped"], stats["errors"],
        )
        return stats

    def _batch_load_orderpoints(
        self, session: OdooSession, product_ids: list[int]
    ) -> dict[tuple[int, int], int]:
        """Batch search_read existing orderpoints. Returns {(pid, wid): op_id}."""
        existing: dict[tuple[int, int], int] = {}
        for batch_start in range(0, len(product_ids), SEARCH_BATCH_SIZE):
            batch_pids = product_ids[batch_start:batch_start + SEARCH_BATCH_SIZE]
            try:
                # execute_kw returns a list of dicts for search_read; the xmlrpc
                # stub types it as the union of possible XML-RPC return primitives.
                records = cast(list[dict[str, Any]], self._execute(
                    session,
                    "stock.warehouse.orderpoint", "search_read",
                    [[["product_id", "in", batch_pids]]],
                    {"fields": ["product_id", "warehouse_id"], "limit": False},
                ))
                for rec in records:
                    key: tuple[int, int] = (rec["product_id"][0], rec["warehouse_id"][0])
                    existing[key] = rec["id"]
            except Exception as e:
                logger.error("Batch search_read failed at offset %d: %s", batch_start, e)
        return existing

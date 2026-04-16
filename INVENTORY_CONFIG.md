# Inventory Position Configuration

How foreqcast accounts for reserved and unreserved inventory when computing replenishment rules.

## The problem

A pure forecast-driven min/max ignores what's currently in the warehouse. This leads to:
- **Overstocking**: product has 90 days of stock but still gets a replenishment rule with min=7 days
- **Understocking**: product has 2 days of stock but safety factor is the same as everything else
- **Blind spots**: no way to see which products are at risk vs sitting idle

## Inventory modes

| Mode | What it does | When to use |
|------|-------------|-------------|
| `ignore` | Don't read inventory data. Min/max from forecast only. | Starting out, or when Odoo's scheduler handles all triggering |
| `analyze` | Read inventory, compute positions, add analysis columns to output. Min/max unchanged. | Visibility into current stock health alongside forecast |
| `adjust` | Read inventory AND modify replenishment behavior (skip overstock, boost understock safety). | Active inventory-aware planning |

```
# In foreqcast_config.db:
UPDATE settings SET value = 'analyze' WHERE key = 'inventory_mode';
```

## Position calculation

```
net_position = on_hand
             - reserved_qty          (if respect_reservations = true)
             + incoming_supply       (if include_incoming_supply = true)
             - outgoing_demand       (if include_outgoing_demand = true)
```

### `respect_reservations` (default: true)

Controls whether reserved stock counts as "available" for planning purposes.

| Value | Calculation | Philosophy |
|-------|-------------|------------|
| `true` | available = on_hand - reserved | **Conservative.** Stock allocated to existing orders is "gone" — only free stock matters. Prevents over-promising. |
| `false` | available = on_hand | **Optimistic.** The planner can reallocate any stock. Better for global optimization, but may conflict with warehouse operations. |

**When to use `false`:** If your planning horizon is long enough that current reservations will be fulfilled before new demand arrives. Also useful when Odoo reservations are stale or unreliable.

### `include_incoming_supply` (default: true)

Whether to count uncommitted incoming goods as part of the position.

| Source | Parquet file | What's counted |
|--------|-------------|---------------|
| `purchase_orders` | `purchase_order_line.parquet` | qty_ordered - qty_received on confirmed POs |
| `incoming_moves` | `stock_move.parquet` | Open moves (confirmed/assigned/waiting) with destination = internal location |
| `manufacturing_orders` | `stock_move.parquet` | MO output moves (destination = internal, origin = production) |

```
# Default: count POs and in-transit moves
UPDATE settings SET value = 'purchase_orders,incoming_moves' WHERE key = 'incoming_sources';

# Also count manufacturing output
UPDATE settings SET value = 'purchase_orders,incoming_moves,manufacturing_orders' WHERE key = 'incoming_sources';
```

**Caution:** Including incoming supply reduces the apparent urgency. If a PO is late or will be cancelled, the position is overstated.

### `include_outgoing_demand` (default: false)

Whether to subtract committed outgoing goods from the position.

**Default is `false`** to avoid double-counting: the forecast already represents expected demand. Subtracting current open SOs on top of the forecast would overstate the need.

Enable this only if:
- Your forecast is purely trend-based (doesn't include current open orders)
- You want a "right now" snapshot of net position, not a forecast-adjusted one

| Source | Parquet file | What's counted |
|--------|-------------|---------------|
| `sale_orders` | `sale_order_line.parquet` | qty_ordered - qty_delivered on confirmed SOs |
| `outgoing_moves` | `stock_move.parquet` | Open moves from internal locations |
| `manufacturing_consumption` | `stock_move.parquet` | MO component demand moves |

## Analysis thresholds

### `overstock_threshold_days` (default: 90)

Products where `coverage_days > 90` are flagged as `overstock`.

Coverage days = net_position / forecasted_daily_demand

In `adjust` mode with `overstock_skip = true`: overstocked products get `action = 'skip'` with `skip_reason = 'overstocked'`. No orderpoint is created/updated.

### `understock_threshold_days` (default: 3)

Products where `coverage_days < 3` are flagged as `understock`.

In `adjust` mode: the safety_factor is multiplied by `understock_urgency_multiplier` (default 1.5). So if base safety = 1.5 and product is understocked, effective safety = 1.5 x 1.5 = 2.25.

Products between `understock_threshold_days` and `understock_threshold_days * 2` are flagged as `at_risk`.

## Inventory classification

Each product x warehouse gets an `inventory_flag`:

| Flag | Meaning | Condition |
|------|---------|-----------|
| `ok` | Healthy stock level | coverage between understock*2 and overstock thresholds |
| `overstock` | Excess inventory | coverage > overstock_threshold_days |
| `understock` | Dangerously low | coverage < understock_threshold_days |
| `at_risk` | Low but not critical | coverage between understock and understock*2 |
| `no_stock` | Zero position, has demand | on_hand=0, incoming=0, demand>0 |
| `no_demand` | Has stock, no demand | on_hand>0, daily_demand=0 (potential dead stock) |
| `no_data` | No inventory data loaded | inventory_mode=ignore, or no quant data for this product |

## Output files

When `inventory_mode` is `analyze` or `adjust`:

### Added columns in `replenishment_rules.parquet`:
- `on_hand_qty`, `reserved_qty`, `incoming_supply_qty`, `outgoing_demand_qty`
- `net_available_qty` — computed position based on config flags
- `coverage_days` — days of demand covered (null if no demand)
- `inventory_flag` — classification string

### New file: `inventory_analysis.parquet`
Full breakdown per product x warehouse with all supply/demand components separated (PO vs moves vs MO).

## Examples

### Example 1: Conservative distribution warehouse
```
inventory_mode = analyze
respect_reservations = true
include_incoming_supply = true
include_outgoing_demand = false
incoming_sources = purchase_orders
overstock_threshold_days = 60
understock_threshold_days = 5
```
Counts POs as incoming but respects reservations. Good for distribution where PO delivery is reliable.

### Example 2: Manufacturing with unreliable reservations
```
inventory_mode = adjust
respect_reservations = false
include_incoming_supply = true
include_outgoing_demand = false
incoming_sources = purchase_orders,incoming_moves,manufacturing_orders
overstock_threshold_days = 120
understock_threshold_days = 3
understock_urgency_multiplier = 2.0
```
Ignores reservations (lets planner reallocate), counts MO output as incoming. Aggressive understock boost.

### Example 3: Retail with fast-moving goods
```
inventory_mode = adjust
respect_reservations = true
include_incoming_supply = false
include_outgoing_demand = false
overstock_threshold_days = 30
understock_threshold_days = 1
overstock_skip = true
```
Only looks at physical on-hand minus reserved. Short thresholds for fast-moving retail.

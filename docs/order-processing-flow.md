# Order Processing Flow Analysis

## Overview

Orders in ACR-HAi flow through an **event-driven pipeline** spanning two subsystems:

- **WES** (Warehouse Execution System) -- owns orders, pick tasks, stations, and inventory
- **ESS** (Equipment Scheduling System) -- owns robots, equipment tasks, paths, and totes

The two subsystems communicate exclusively through **domain events** published on an
in-process async event bus. The full lifecycle of a single order involves **10-12 events**,
**4 WES/ESS boundary crossings**, and coordination between **two robot types**.

---

## End-to-End Flow Diagram

```
WMS                    WES                              ESS
 │                      │                                │
 │  POST /wms/orders    │                                │
 ├─────────────────────►│                                │
 │                      │ OrderCreated                   │
 │                      │──(ws broadcast)──►  Frontend   │
 │                      │                                │
 │  POST /orders/:id/   │                                │
 │       allocate       │                                │
 ├─────────────────────►│                                │
 │                      │ NEW ──► ALLOCATING              │
 │                      │   AllocationEngine.allocate()   │
 │                      │   score stations (4 criteria)   │
 │                      │ ALLOCATING ──► ALLOCATED        │
 │                      │                                │
 │                      │ OrderAllocated                  │
 │                      │───────────────────────────────►│
 │                      │   create PickTask               │
 │                      │   find source Tote by SKU       │
 │                      │   publish RetrieveSourceTote    │
 │                      │                                │
 │                      │            ┌───────────────────┤
 │                      │            │ execute_retrieve() │
 │                      │            │ assign A42TD+K50H  │
 │                      │            │ plan A* path       │
 │                      │            │ store path→Redis   │
 │                      │            └───────────────────┤
 │                      │                                │
 │                      │              SIMULATION TICKS   │
 │                      │              A42TD moves to     │
 │                      │              rack location      │
 │                      │                                │
 │                      │◄── SourceAtCantilever ─────────┤
 │                      │   PickTask: SOURCE_REQUESTED    │
 │                      │          ──► SOURCE_AT_CANTILEVER
 │                      │   plan K50H path to station     │
 │                      │                                │
 │                      │              K50H moves to      │
 │                      │              station            │
 │                      │                                │
 │                      │◄── SourceAtStation ────────────┤
 │                      │   PickTask: ──► SOURCE_AT_STATION
 │                      │   EquipmentTask: ──► DELIVERED  │
 │                      │                                │
 │                      │   OPERATOR SCANNING             │
 │  POST /stations/:id/ │   (first scan auto-transitions  │
 │       scan           │    to PICKING state)            │
 ├─────────────────────►│   qty_picked++                  │
 │  (repeat per item)   │   when qty_picked==qty_to_pick: │
 │                      │   ──► RETURN_REQUESTED          │
 │                      │                                │
 │                      │   PickTaskStateChanged           │
 │                      │   publish ReturnSourceTote       │
 │                      │───────────────────────────────►│
 │                      │            │ execute_return()    │
 │                      │            │ assign K50H+A42TD   │
 │                      │            │ plan A* path        │
 │                      │            └────────────────────┤
 │                      │                                │
 │                      │◄── ReturnAtCantilever ─────────┤
 │                      │   PickTask: ──► RETURN_AT_CANTILEVER
 │                      │   plan A42TD path to rack       │
 │                      │                                │
 │                      │◄── SourceBackInRack ───────────┤
 │                      │   PickTask: ──► COMPLETED       │
 │                      │   EquipmentTask: ──► COMPLETED  │
 │                      │   release both robots           │
 │                      │                                │
 │                      │   check: all PickTasks done?    │
 │                      │   YES ──► Order COMPLETED       │
 │                      │   OrderCompleted                │
 │                      │──(ws broadcast)──► Frontend     │
 │                      │                                │
```

---

## Phase 1: Order Ingestion

### Entry Points

| Endpoint | Source | Purpose |
|---|---|---|
| `POST /wms/orders` | External WMS | Create order from upstream system |
| `POST /orders/{id}/allocate` | Dashboard UI | Trigger station allocation |

### WMS Order Creation (`wms_adapter/router.py`)

1. Validate payload via `WMSOrderCreate` Pydantic schema:
   - `external_id` (required, max 100 chars)
   - `sku` (required, max 50 chars)
   - `quantity` (required, > 0)
   - `priority` (0-10, default 0)
   - `zone_id`, `pbt_at` (optional)
2. Call `OrderService.create_order()` -- persists Order with status `NEW`
3. Emit `OrderCreated` event
4. Event handler broadcasts `"order.updated"` via WebSocket

---

## Phase 2: Station Allocation

### Trigger

`POST /orders/{order_id}/allocate` (WES router)

### Two-Step State Transition

The allocation uses a double transition to prevent concurrent re-entry:

```
Step 1:  NEW ──(allocate)──► ALLOCATING     (locks the order)
Step 2:  Run AllocationEngine.allocate()    (may take time)
Step 3:  ALLOCATING ──(station_assigned)──► ALLOCATED
```

### AllocationEngine Scoring

Each online station in the order's zone is scored with 4 weighted criteria:

| Criterion | Weight | Formula | What It Rewards |
|---|---|---|---|
| Queue capacity | 0.3 | `1.0 - active_tasks / max_queue_size` | Stations with spare capacity |
| SKU batching | 0.3 | `min(1.0, same_sku_count / 3.0)` | Grouping same-SKU orders together |
| PBT urgency | 0.2 | `1.0 - remaining_seconds / 14400` | Orders closer to pick-before-time |
| Robot availability | 0.2 | `min(1.0, idle_robots_in_zone / 4.0)` | Zones with available robots |

**Final score** = weighted sum. Highest-scoring station wins.

### Post-Allocation

The `OrderAllocated` event triggers `order_handlers._handle_order_allocated()`:

1. Create a `PickTask` (state: `SOURCE_REQUESTED`)
2. Query ESS for a `Tote` matching the order's SKU with quantity > 0
3. Link PickTask to the source tote
4. Publish `RetrieveSourceTote` event -- **crosses into ESS**

---

## Phase 3: Tote Retrieval (ESS)

### Equipment Task Creation

`equipment_handlers._handle_retrieve_source_tote()`:

1. Create `EquipmentTask` (type: `RETRIEVE`, state: `PENDING`)
2. `FleetManager.find_nearest_idle()` assigns:
   - **A42TD** robot for rack-to-cantilever leg
   - **K50H** robot for cantilever-to-station leg
3. Plan A* path for A42TD from current position to rack location
4. Store path in Redis for simulation consumption
5. Advance task state: `PENDING` → `A42TD_MOVING`

### Robot Assignment (`FleetManager`)

- Uses **Manhattan distance** to find nearest idle robot of the required type
- Marks robot as `ASSIGNED`, sets `current_task_id`
- If no idle robot exists, raises an error

### Path Planning (`PathPlanner`)

- A* algorithm on the warehouse grid
- Impassable cells: `WALL`, `RACK`
- Congestion-aware: adds per-cell cost from `TrafficController.get_congestion_map()`
- 4-directional movement (N/S/E/W)

---

## Phase 4: Robot Movement (Simulation)

### Tick Loop

`PhysicsEngine` drives the simulation at configurable tick intervals. Each tick calls
`RobotSimulator.update(dt)`:

**For each robot with a path in Redis:**

1. Read next waypoint from cached path
2. Attempt `TrafficController.reserve_cell(target_row, target_col, robot_id)`
3. **If reserved**: move robot, release old cell, update Redis, broadcast position via WS
4. **If blocked**: increment wait counter, set robot to `WAITING` state
5. **After 3 consecutive waits**: replan path with congestion costs
6. **After all robots processed**: run `detect_deadlock()` cycle detection

### Arrival Events

When a robot's path is exhausted, the simulator checks the cell type and emits:

| Cell Type | RETRIEVE task | RETURN task |
|---|---|---|
| `CANTILEVER` | `SourceAtCantilever` | `ReturnAtCantilever` |
| `STATION` | `SourceAtStation` | -- |
| `RACK` | -- | `SourceBackInRack` |

---

## Phase 5: Cantilever Handoff

### SourceAtCantilever (ESS → WES)

`arrival_handlers._handle_source_at_cantilever()`:

1. Transition PickTask: `SOURCE_REQUESTED` → `SOURCE_AT_CANTILEVER`
2. Advance EquipmentTask: `A42TD_MOVING` → `AT_CANTILEVER`
3. Plan A* path for **K50H** from cantilever to station
4. Advance EquipmentTask: `AT_CANTILEVER` → `K50H_MOVING`

This is the **robot handoff point**: A42TD delivers the tote to the cantilever, K50H
picks it up and carries it to the operator's station.

### SourceAtStation (ESS → WES)

`arrival_handlers._handle_source_at_station()`:

1. Transition PickTask: `SOURCE_AT_CANTILEVER` → `SOURCE_AT_STATION`
2. Advance EquipmentTask: `K50H_MOVING` → `DELIVERED`
3. The tote is now physically at the station -- operator can begin picking

---

## Phase 6: Operator Scanning

### Scan Endpoint

`POST /stations/{station_id}/scan` with body `{ "pick_task_id": "..." }`

### `PickTaskService.scan_item()` logic:

```
1. If task state == SOURCE_AT_STATION:
      auto-transition to PICKING (event: "scan_started")

2. Validate state == PICKING (else raise ValueError)

3. qty_picked += 1

4. If qty_picked >= qty_to_pick:
      auto-transition to RETURN_REQUESTED (event: "pick_complete")

5. Commit and return updated task
```

### Supporting Operations

| Endpoint | Purpose |
|---|---|
| `POST /stations/:id/bind-tote` | Bind a destination tote to receive picked items |
| `POST /stations/:id/tote-full` | Clear target tote when full, forcing a new bind |

---

## Phase 7: Tote Return

### Trigger

When PickTask reaches `RETURN_REQUESTED`, `pick_task_handlers._handle_pick_task_state_changed()`
publishes `ReturnSourceTote` -- **crosses back into ESS**.

### Return Equipment Task

`equipment_handlers._handle_return_source_tote()`:

1. Create `EquipmentTask` (type: `RETURN`, state: `PENDING`)
2. Assign K50H (station → cantilever) and A42TD (cantilever → rack)
3. Plan A* path for K50H
4. Advance: `PENDING` → `K50H_MOVING`

### Return Arrival Chain

| Event | Handler Action |
|---|---|
| `ReturnAtCantilever` | Transition PickTask → `RETURN_AT_CANTILEVER`, plan A42TD path to rack, advance to `A42TD_MOVING` |
| `SourceBackInRack` | Transition PickTask → `COMPLETED`, advance EquipmentTask → `COMPLETED`, release both robots |

---

## Phase 8: Order Completion

### Completion Check

In `_handle_source_back_in_rack()`, after completing the PickTask:

```python
# Query for any incomplete pick tasks on this order
incomplete = select(PickTask).where(
    PickTask.order_id == order_id,
    PickTask.state != PickTaskState.COMPLETED,
)

if not incomplete:
    OrderService.complete_order(order_id)
    # Order: IN_PROGRESS → COMPLETED
```

Order completion is **reactive** -- it happens only when the last PickTask's tote is
physically returned to the rack. This ensures inventory accuracy.

### Post-Completion

- `OrderCompleted` event broadcasts `"order.updated"` via WebSocket
- `WMSOutboundClient.report_order_completed()` would notify the upstream WMS (currently
  a logging stub)

---

## State Machine Summary

### Order States

```
NEW ──(allocate)──► ALLOCATING ──(station_assigned)──► ALLOCATED
                                                          │
                                                    (pick_started)
                                                          │
                                                          ▼
                                                     IN_PROGRESS ──(all_picked)──► COMPLETED

Any non-terminal state ──(cancel)──► CANCELLED
  side effect: release_inventory
```

### PickTask States

```
SOURCE_REQUESTED ──► SOURCE_AT_CANTILEVER ──► SOURCE_AT_STATION
                                                      │
                                                (scan_started)
                                                      │
                                                      ▼
                                                   PICKING
                                                      │
                                                (pick_complete)
                                                      │
                                                      ▼
                                              RETURN_REQUESTED ──► RETURN_AT_CANTILEVER ──► COMPLETED
```

### EquipmentTask States

```
PENDING ──(a42td_dispatched)──► A42TD_MOVING ──(at_cantilever)──► AT_CANTILEVER
    ──(k50h_dispatched)──► K50H_MOVING ──(delivered)──► DELIVERED ──(completed)──► COMPLETED
```

---

## WES / ESS Boundary Crossings

| # | Direction | Event | What Crosses |
|---|---|---|---|
| 1 | WES → ESS | `RetrieveSourceTote` | Pick task needs tote brought to station |
| 2 | ESS → WES | `SourceAtCantilever` | A42TD arrived, K50H handoff needed |
| 3 | ESS → WES | `SourceAtStation` | Tote delivered, operator can pick |
| 4 | WES → ESS | `ReturnSourceTote` | Picking done, return tote to rack |
| 5 | ESS → WES | `ReturnAtCantilever` | K50H returned tote, A42TD takes over |
| 6 | ESS → WES | `SourceBackInRack` | Tote stored, pick task complete |

---

## Two-Robot Coordination

The system uses two robot types with complementary roles:

| Robot | Type | Role in RETRIEVE | Role in RETURN |
|---|---|---|---|
| **A42TD** | Large carrier | Rack → Cantilever | Cantilever → Rack |
| **K50H** | Small picker | Cantilever → Station | Station → Cantilever |

The **cantilever** is the physical handoff point between the two robot types. The
EquipmentTask tracks both robot assignments (`a42td_robot_id`, `k50h_robot_id`) and
releases both to `IDLE` when the task reaches `COMPLETED`.

---

## Error Handling and Edge Cases

### Currently Handled

- **No online stations**: `AllocationEngine.allocate()` raises `RuntimeError`
- **Order not found**: 404 from WMS adapter and WES router
- **Invalid state transition**: `ValueError` from state machines
- **Invalid scan state**: `ValueError` if task not in `PICKING` state
- **Schema validation**: Pydantic rejects malformed WMS payloads (422)
- **Handler exceptions**: `@safe_handler` logs and re-raises

### Gaps / Risks

- **No source tote found**: `_handle_order_allocated` may fail silently if no tote
  matches the SKU -- no explicit error path
- **No idle robot available**: `find_nearest_idle` returns `None` -- callers must handle
- **Path planning failure**: If A* finds no path (all routes blocked), `find_path`
  returns empty list -- stored as empty path in Redis
- **Concurrent allocation**: Two orders allocated simultaneously could pick the same
  station, exceeding `max_queue_size` -- no locking
- **Event bus failures**: Events published after DB commit -- if handler fails, DB state
  is inconsistent with event expectations
- **Tote not returned**: If a robot gets permanently stuck, the PickTask and Order never
  complete -- no timeout or watchdog

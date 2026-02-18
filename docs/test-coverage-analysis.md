# Test Coverage Analysis

## Executive Summary

The ACR-HAi codebase has a foundational test suite covering key backend components, but
significant gaps exist across both the backend and frontend. The backend has ~1,400 lines
of tests spanning unit, integration, and e2e levels, while the **frontend has zero tests**
despite having Vitest configured. Below is a detailed analysis with prioritized
recommendations.

---

## Current State

### Backend Tests (6 test files, ~1,400 lines)

| Test File | Level | What It Covers |
|---|---|---|
| `tests/ess/test_task_executor.py` | Unit | TaskExecutor retrieve/return/advance |
| `tests/ess/test_path_planner.py` | Unit | A* pathfinding on various grids |
| `tests/wes/test_pick_task_sm.py` | Unit | PickTask state machine transitions |
| `tests/wes/test_allocation_engine.py` | Unit | Station allocation scoring |
| `tests/integration/test_event_flow.py` | Integration | EventBus pub/sub lifecycle |
| `tests/e2e_test.py` | E2E | Full order-to-pick flow with SQLite + fakeredis |

### Frontend Tests

**None.** Vitest is configured (`npm test` runs `vitest`) but no `.test.ts` or `.spec.tsx`
files exist.

---

## Coverage Gaps

### 1. Completely Untested Backend Modules (High Priority)

These modules contain significant business logic and have **no test coverage at all**.

#### TrafficController (`src/ess/application/traffic_controller.py`)

- Cell reservation / release logic
- Congestion map calculation (normalized reservation counts)
- **Deadlock detection** via cycle detection on a wait-for graph
- This is safety-critical for the simulation -- a bug here causes robots to collide or
  deadlock silently

#### Order State Machine (`src/wes/domain/state_machines/order_sm.py`)

- The PickTask state machine is tested, but the Order state machine is not
- Covers: NEW -> ALLOCATING -> ALLOCATED -> IN_PROGRESS -> COMPLETED, plus cancellation
  from any non-terminal state
- Side effects like `run_allocation_engine`, `emit_order_cancelled`, `release_inventory`

#### OrderService (`src/wes/application/order_service.py`)

- Order creation, allocation, cancellation, and completion orchestration
- Event collection and publication
- Inventory release on cancellation

#### PickTaskService (`src/wes/application/pick_task_service.py`)

- PickTask creation, state transitions, and item scanning (`scan_item`)
- Quantity tracking (`qty_picked` increment with auto-completion)
- Event collection

#### FleetManager (`src/ess/application/fleet_manager.py`)

- `find_nearest_idle()` -- Manhattan distance nearest-robot search
- Robot assignment / release lifecycle
- Edge cases: no idle robots, multiple equidistant robots, zone filtering

#### InventoryService (`src/wes/application/inventory_service.py`)

- Stock allocation / release
- Available quantity calculation (`total_qty - allocated_qty`)
- Under-stock edge cases

#### RobotSimulator (`src/ess/simulation/robot_simulator.py`)

- Per-tick path following with traffic reservation
- WAITING state when cell is occupied
- Congestion-aware rerouting after 3 consecutive waits
- Deadlock resolution
- Heading calculation (0=N, 90=E, 180=S, 270=W)
- Arrival event emission at significant cells (cantilever, station, rack)

#### PhysicsEngine (`src/ess/simulation/physics_engine.py`)

- Tick loop start/stop/pause/resume
- Speed multiplier clamping (0.5x--10x)
- Single-step manual advance
- Updatable registration

#### All Event Handlers (`src/handlers/`)

- `order_handlers.py` -- OrderAllocated handler creates PickTask and finds totes
- `equipment_handlers.py` -- Retrieve/Return handlers run TaskExecutor + path planning
- `arrival_handlers.py` -- Multi-step state transitions on robot arrivals
- `pick_task_handlers.py` -- RETURN_REQUESTED triggers ReturnSourceTote event
- These handlers are the glue between WES and ESS; bugs here break the entire flow

#### WebSocket Manager (`src/shared/websocket_manager.py`)

- Connection pooling and disconnect handling
- Throttled broadcast (100ms)
- Backpressure detection (65KB buffer limit)

#### Redis Cache (`src/ess/infrastructure/redis_cache.py`)

- Robot position/status caching in Redis hashes
- Path serialization/deserialization (JSON lists)
- SCAN-based key discovery

#### Monitoring Services (`src/monitoring/`)

- `AlarmService` -- alarm raising, acknowledgment, 500-alarm cap
- `MetricsService` -- rolling-window KPI calculations (picks/hour, utilization)

#### WMS Adapter (`src/wms_adapter/`)

- Schema validation (string lengths, numeric ranges)
- Router endpoints for order creation/cancellation/status
- Pick progress calculation

#### Database Seed (`src/seed.py`)

- Grid construction from warehouse.yaml
- Idempotency (skips if zones exist)
- Robot/tote/inventory creation

#### Repositories (`src/wes/infrastructure/repositories.py`, `src/ess/infrastructure/repositories.py`)

- CRUD operations with filtering
- Query correctness for status/zone/state filters

---

### 2. Gaps in Existing Tests (Medium Priority)

Even the tested modules have notable gaps:

#### TaskExecutor (`test_task_executor.py`)

- No tests for concurrent task execution
- No tests for partial failures (first robot assigned, second fails)
- No tests for state persistence after transitions

#### PathPlanner (`test_path_planner.py`)

- No performance/scalability tests on large grids
- No tests for dynamic congestion updates during pathfinding
- Congestion cost thresholds could be tested more granularly

#### AllocationEngine (`test_allocation_engine.py`)

- Only tests single-order allocation, never batch
- No tie-breaking tests when scores are equal
- No tests for station capacity constraints (`max_queue_size`)
- No cross-zone allocation edge cases

#### EventBus (`test_event_flow.py`)

- No tests for event ordering guarantees
- No tests for slow handlers blocking the queue
- No tests for bus shutdown/cleanup lifecycle
- No concurrent publish stress testing

#### E2E Test (`e2e_test.py`)

- No concurrent order handling
- No error recovery scenarios
- No WebSocket connection testing (noted as limitation of httpx)
- No low-inventory or out-of-stock scenarios

---

### 3. Frontend -- Zero Coverage (High Priority)

The frontend has substantial logic that warrants testing:

#### Critical (complex algorithms)

- **`WarehouseMap.tsx`** -- Canvas rendering with animation interpolation
  (`easeOutCubic`), pan/zoom coordinate transforms, robot hit-testing, heatmap gradient
  calculation. Extract the pure math functions and test them.

#### High Priority (state management and real-time data)

- **`useWarehouseStore.ts`** -- Robot animation orchestration (tracking position deltas,
  computing animation start times), state update correctness for each message type
- **`WebSocketProvider.tsx`** -- Connection lifecycle, exponential backoff timing
  (2s base, 30s cap), reconnection after disconnect
- **`messageRouter.ts`** -- Routing for all 9 message types, error handling for
  malformed messages, unknown message types

#### Medium Priority (UI logic)

- **`StationOperatorPage.tsx`** -- Derived `activeTask` filtering, `pickComplete`
  computation, put wall slot building
- **`ScanInterface.tsx`** -- Barcode input handling, flash feedback with timeout
  cleanup, progress bar calculation
- **`MetricsPage.tsx`** -- Polling interval setup/teardown, 60-snapshot history ring,
  sparkline height normalization
- **`RobotFleetPage.tsx`** -- Multi-filter logic (zone + status + type), memoization
- **`api/hooks.ts`** -- Query invalidation on mutations, refetch interval configuration

---

## Prioritized Recommendations

### Tier 1 -- Immediate (core business logic correctness)

| # | Area | What to Test | Why |
|---|---|---|---|
| 1 | TrafficController | `reserve_cell`, `release_cell`, `detect_deadlock`, `get_congestion_map` | Safety-critical: prevents robot collisions and deadlocks |
| 2 | Order State Machine | All transitions + side effects, cancellation from every state | Parity with the already-tested PickTask SM |
| 3 | Event Handlers | Each handler in isolation with mocked DB + event bus | These are the system's integration seams; bugs cascade |
| 4 | OrderService | create/allocate/cancel/complete flows, event collection | Core business orchestration |
| 5 | PickTaskService | create, transition, scan_item with qty tracking | Directly affects operator workflow |

### Tier 2 -- Important (simulation and data integrity)

| # | Area | What to Test | Why |
|---|---|---|---|
| 6 | FleetManager | `find_nearest_idle` with various robot distributions | Incorrect assignment wastes robot cycles |
| 7 | RobotSimulator | Path following, wait/reroute logic, arrival events | Core of the visual simulation |
| 8 | PhysicsEngine | Start/stop/pause/resume, speed clamping, tick counting | Simulation control correctness |
| 9 | InventoryService | Allocate/release, under-stock handling | Prevents over-allocation |
| 10 | Redis Cache | Position/path round-trip, SCAN key discovery | Data loss causes ghost robots on the map |

### Tier 3 -- Valuable (infrastructure and frontend)

| # | Area | What to Test | Why |
|---|---|---|---|
| 11 | Frontend: messageRouter | All 9 message types, malformed payloads | First line of defense for WebSocket data |
| 12 | Frontend: useWarehouseStore | Robot animation state, update correctness | Bugs cause visual glitches |
| 13 | Frontend: WarehouseMap math | Easing, coordinate transforms, hit testing | Extract pure functions and unit test |
| 14 | WebSocket Manager | Throttle, backpressure, disconnect cleanup | Prevents memory leaks and dropped updates |
| 15 | Monitoring Services | Alarm cap, KPI rolling-window math | Ensures operational visibility |
| 16 | Seed / Repositories | Idempotent seeding, query filter correctness | Data integrity on startup |
| 17 | WMS Adapter | Schema validation, pick progress calculation | External system boundary |

### Tier 4 -- Hardening (robustness)

| # | Area | What to Test | Why |
|---|---|---|---|
| 18 | Concurrency | Parallel order creation, simultaneous robot assignments | Race conditions in production |
| 19 | Error Recovery | DB failures mid-transaction, Redis timeouts | Resilience under failure |
| 20 | AllocationEngine depth | Batch allocation, tie-breaking, queue saturation | Edge cases in scoring |
| 21 | CI/CD pipeline | Add GitHub Actions to run pytest + vitest on every PR | Prevents regressions |

---

## Quantitative Summary

| Category | Source Files | Tested | Untested | Coverage |
|---|---|---|---|---|
| Backend - WES Domain | 5 | 2 (PickTask SM, events) | 3 (Order SM, models, enums) | ~40% |
| Backend - WES Application | 5 | 1 (AllocationEngine) | 4 (OrderService, PickTaskService, InventoryService, StationService) | ~20% |
| Backend - WES Infrastructure | 2 | 0 | 2 (repositories, router) | 0% |
| Backend - ESS Domain | 3 | 0 | 3 (models, enums, events) | 0% |
| Backend - ESS Application | 5 | 2 (PathPlanner, TaskExecutor) | 3 (FleetManager, TrafficController, ZoneManager) | ~40% |
| Backend - ESS Infrastructure | 3 | 0 | 3 (repositories, redis_cache, router) | 0% |
| Backend - ESS Simulation | 3 | 0 | 3 (PhysicsEngine, RobotSimulator, presets) | 0% |
| Backend - Shared | 6 | 1 (EventBus) | 5 (DB, Redis, WS manager, snapshot, simulation_state) | ~17% |
| Backend - Handlers | 5 | 0 | 5 | 0% |
| Backend - Monitoring | 3 | 0 | 3 | 0% |
| Backend - WMS Adapter | 3 | 0 | 3 | 0% |
| Backend - Other | 4 | 0 | 4 (main, config, seed, deps) | 0% |
| **Backend Total** | **47** | **6** | **41** | **~13%** |
| Frontend | ~25 | 0 | ~25 | **0%** |

---

## Suggested Quick Wins

These are tests that can be written quickly due to the code being pure functions or
having minimal dependencies:

1. **Order state machine** -- Copy the pattern from `test_pick_task_sm.py` (pure
   function, no mocks needed)
2. **TrafficController** -- In-memory data structure, no DB or async required for basic
   reserve/release/congestion tests
3. **AlarmService** -- In-memory, simple raise/ack/cap tests
4. **MetricsService** -- In-memory, test rolling-window calculations
5. **Frontend messageRouter** -- Pure function, test each message type dispatches
   correctly
6. **InventoryService** -- Simple arithmetic on `total_qty - allocated_qty`

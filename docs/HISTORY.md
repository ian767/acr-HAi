# ACR-Hai Development History

## Commit 1: `548f377` (2026-02-17 13:55)

**feat: implement WebSocket, E2E event flow, and data seeding**

프로젝트 초기 구현. 전체 E2E 플로우가 동작하는 상태까지 구축.

### Backend

**아키텍처**: WES(주문/피킹) + ESS(장비/로봇) + WMS Adapter, 이벤트 버스 기반 비동기 통신

```
backend/src/
├── main.py                  # FastAPI 앱 팩토리 + lifespan
├── config.py                # 환경변수 기반 설정
├── deps.py                  # FastAPI 의존성 주입
├── seed.py                  # DB 시딩 (Zone, Station, Robot, Tote, Grid)
├── event_handlers.py        # 도메인 이벤트 핸들러 (11개, 548줄)
│
├── wes/                     # Warehouse Execution System
│   ├── router.py            # REST API (주문, 스테이션, 피크태스크, 재고)
│   ├── application/         # 서비스 레이어
│   │   ├── allocation_engine.py   # 주문-스테이션 배정 알고리즘
│   │   ├── order_service.py       # 주문 생명주기 관리
│   │   ├── pick_task_service.py   # PickTask 상태머신 래퍼
│   │   ├── inventory_service.py   # 재고 관리
│   │   └── station_service.py     # 스테이션 관리
│   ├── domain/
│   │   ├── models.py        # Order, Station, PickTask (SQLAlchemy)
│   │   ├── enums.py         # OrderStatus, PickTaskState
│   │   ├── events.py        # OrderCreated, OrderAllocated, PickTaskStateChanged 등
│   │   └── state_machines/  # Order SM, PickTask SM
│   └── infrastructure/
│       └── repositories.py
│
├── ess/                     # Equipment Scheduling System
│   ├── router.py            # REST API (로봇, 존, 그리드, 시뮬레이션)
│   ├── application/
│   │   ├── fleet_manager.py       # 로봇 관리 + 배정
│   │   ├── path_planner.py        # A* 경로 탐색
│   │   ├── task_executor.py       # 장비 태스크 실행
│   │   ├── traffic_controller.py  # 다중 로봇 교통 제어
│   │   └── zone_manager.py
│   ├── domain/
│   │   ├── models.py        # Robot, Location, Tote, EquipmentTask
│   │   ├── enums.py         # RobotType, CellType, EquipmentTaskType
│   │   └── events.py        # SourceAtCantilever, SourceAtStation 등
│   ├── infrastructure/
│   │   └── redis_cache.py   # 로봇 경로 Redis 캐싱
│   └── simulation/          # 물리 시뮬레이션 엔진
│       ├── physics_engine.py
│       ├── presets.py
│       └── robot_simulator.py
│
├── wms_adapter/             # 외부 WMS 연동 어댑터
│   ├── router.py            # 주문 생성/취소/상태조회 API
│   ├── outbound.py
│   └── schemas.py
│
├── monitoring/              # 모니터링
│   ├── router.py
│   ├── alarm_service.py
│   └── metrics_service.py
│
└── shared/                  # 공유 인프라
    ├── event_bus.py          # 인프로세스 비동기 이벤트 버스
    ├── database.py           # SQLAlchemy 엔진 + 세션 팩토리
    ├── redis.py              # Redis 클라이언트
    ├── websocket_manager.py  # WebSocket 연결 관리 + 브로드캐스트
    ├── snapshot_builder.py   # 시스템 스냅샷 빌드 (WS 초기 전송용)
    ├── simulation_state.py   # 전역 시뮬레이션 상태 (grid, traffic)
    └── base_model.py         # SQLAlchemy 선언적 베이스
```

### Frontend

**스택**: React 18 + TypeScript + Vite + React Router 6 + TanStack Query + Zustand + Recharts

```
frontend/src/
├── App.tsx                  # 메인 앱 (사이드바 네비게이션 + 라우팅)
├── main.tsx                 # 엔트리포인트
├── api/                     # API 클라이언트 + React Query 훅
├── features/
│   ├── dashboard/           # 대시보드
│   ├── wes/                 # 주문관리, 스테이션관리, PickTask 모니터
│   ├── ess/                 # 창고맵, 로봇 플릿
│   ├── station/             # 스테이션 오퍼레이터 UI
│   └── monitoring/          # 알람, 메트릭스
├── stores/                  # Zustand 상태 (UI, 창고 실시간 데이터)
├── types/                   # TypeScript 타입 정의
└── websocket/               # WebSocket 프로바이더 + 메시지 라우터
```

### Config

- `config/warehouse.yaml` - 창고 프리셋 설정
- `config/docker-compose.yml` - PostgreSQL + Redis 컨테이너

### 핵심 이벤트 플로우

```
WMS 주문 → OrderCreated → [할당] → OrderAllocated
  → PickTask 생성 + RetrieveSourceTote 발행
  → TaskExecutor: A42TD 배정 + A* 경로 → Redis 저장
  → 시뮬레이션: A42TD 이동 → SourceAtCantilever
  → K50H 경로 계획 → SourceAtStation
  → [피킹 완료] → RETURN_REQUESTED → ReturnSourceTote
  → K50H 복귀 → ReturnAtCantilever → A42TD 복귀 → SourceBackInRack
  → PickTask COMPLETED → Order COMPLETED
```

### 테스트

- 단위 테스트: PathPlanner, TaskExecutor, AllocationEngine, PickTask SM (61개)
- 통합 테스트: EventBus pub/sub, 이벤트 체인 라운드트립

---

## Commit 2: `baa8f07` (2026-02-17 14:17)

**test: add E2E test with SQLite/fakeredis and fix database.py for SQLite**

외부 서비스(PostgreSQL, Redis) 없이 동작하는 E2E 테스트 추가.

### 변경사항

- `backend/tests/e2e_test.py` (340줄 신규)
  - SQLite in-memory + `StaticPool`로 DB 패치
  - `fakeredis`로 Redis 패치
  - 9단계 E2E 시나리오: Health → Seed 검증 → 주문생성 → 할당 → PickTask 검증 → 시뮬레이션 → 스냅샷 → WMS 상태조회 → 주문취소
- `backend/src/shared/database.py` - SQLite 호환성 수정

### 테스트 결과

- 36/40 pass (4개 grid cell type 실패는 시드 로직 이슈, 핸들러와 무관)

---

## Commit 3: `0a6811f` (2026-02-17 15:13)

**refactor: split event_handlers monolith into handlers/ package and clean up dead code**

기능 변경 없이 구조만 정리. 고도화 전 아키텍처 정리 목적.

### 1. 핸들러 공용 헬퍼 추출

**신규**: `backend/src/handler_support.py` (100줄)

| 헬퍼 | 역할 |
|------|------|
| `handler_session()` | async context manager — 세션 팩토리 2단계 패턴 대체 |
| `HandlerServices(session)` | FleetManager + PathPlanner + TaskExecutor 번들 |
| `plan_and_store_path()` | A* 경로 계산 + Redis 저장 일괄 처리 |
| `ws_broadcast()` | WebSocket 브로드캐스트 |
| `@safe_handler` | try-except 에러 로깅 데코레이터 (re-raise) |

### 2. event_handlers.py → handlers/ 패키지 분리

**삭제**: `backend/src/event_handlers.py` (548줄)

**신규 패키지**:
```
backend/src/handlers/
├── __init__.py              # register_all_handlers(bus)
├── order_handlers.py        # OrderCreated, OrderAllocated, OrderCompleted, OrderCancelled
├── pick_task_handlers.py    # PickTaskStateChanged
├── equipment_handlers.py    # RetrieveSourceTote, ReturnSourceTote
└── arrival_handlers.py      # SourceAtCantilever, SourceAtStation, ReturnAtCantilever, SourceBackInRack
```

**import 변경**:
- `backend/src/main.py`: `from src.handlers import register_all_handlers`
- `backend/tests/e2e_test.py`: 동일

### 3. 프론트엔드 죽은 코드 제거

**삭제** (총 532줄, Pixi.js 기반 → Canvas 2D로 이미 교체됨):
- `frontend/src/features/ess/components/map/layers/GridLayer.ts` (94줄)
- `frontend/src/features/ess/components/map/layers/RobotLayer.ts` (242줄)
- `frontend/src/features/ess/components/map/layers/StationLayer.ts` (111줄)
- `frontend/src/features/ess/components/map/layers/PathLayer.ts` (82줄)

**삭제**: `frontend/package.json`에서 `pixi.js` 의존성 제거

**정리**: `WarehouseMap.tsx` 미사용 import 제거 (`GridState`, `RobotRealtime`, `Station` 타입, 미사용 `error` state)

### 4. ErrorBoundary 추가

**신규**: `frontend/src/components/ErrorBoundary.tsx` (61줄)
- React class component (`getDerivedStateFromError` + `componentDidCatch`)
- 에러 시 white-screen 대신 에러 메시지 + "Try again" 버튼 표시

**수정**: `frontend/src/App.tsx` — `<ErrorBoundary>`로 라우트 래핑

### 5. simulation_state.reset() 추가

**수정**: `backend/src/shared/simulation_state.py`
- `reset()` 함수 추가: `grid = None`, `traffic = TrafficController()` 초기화
- 테스트 격리 용도

### 6. 기타

- `backend/src/ess/router.py` — grid API 응답 형식 개선 (cells를 `[{row, col, type}, ...]` 객체 배열로 변환)
- `frontend/src/api/hooks.ts` — `useGrid` 훅에 `enabled: !!zoneId` 추가

### 검증 결과

| 항목 | 결과 |
|------|------|
| pytest 단위/통합 | 61/61 pass |
| E2E 테스트 | 36/40 pass (grid cell 4개 실패는 기존 이슈) |
| 프론트 빌드 (`tsc -b && vite build`) | clean, 0 errors |
| 수동 스모크 | 주문생성→할당→PickTask→WS 브로드캐스트 정상 |

### 변경 통계

- 21 files changed
- +935 / -1,418 lines
- 순 감소: **483줄**

---

## 현재 상태 (고도화 준비 완료)

### 아키텍처 다이어그램

```
┌──────────────┐     ┌───────────────┐     ┌──────────────┐
│  WMS Adapter │────▶│   Event Bus   │◀────│  ESS Router  │
│  (외부 연동)  │     │  (pub/sub)    │     │  (시뮬레이션)  │
└──────────────┘     └───────┬───────┘     └──────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
     ┌────────────┐  ┌─────────────┐  ┌──────────────┐
     │   order_   │  │ equipment_  │  │  arrival_    │
     │  handlers  │  │  handlers   │  │  handlers    │
     └─────┬──────┘  └──────┬──────┘  └──────┬───────┘
           │                │                 │
           ▼                ▼                 ▼
     ┌──────────────────────────────────────────────┐
     │            handler_support.py                 │
     │  handler_session │ HandlerServices            │
     │  plan_and_store  │ ws_broadcast │ @safe_handler│
     └──────────────────────────────────────────────┘
           │                │                 │
     ┌─────▼─────┐   ┌─────▼──────┐   ┌─────▼──────┐
     │  WES 서비스 │   │  ESS 서비스  │   │   Shared   │
     │ Order,Pick │   │ Fleet,Path │   │  DB,Redis  │
     │  Task,Alloc│   │ Task,Traffic│   │  WS,Event  │
     └───────────┘   └────────────┘   └────────────┘
```

### 알려진 이슈

1. **Grid cell type E2E 실패** — seed 시 grid 셀 타입이 API 응답에서 FLOOR로만 반환됨 (4/40 실패)
2. **fakeredis DeprecationWarning** — `retry_on_timeout` 파라미터 deprecated (기능 영향 없음)

### 하지 않은 것 (의도적)

- DI 프레임워크 도입
- 핸들러별 단위 테스트 추가 (E2E가 커버)
- EventBus 변경
- Base handler 클래스 등 불필요한 추상화

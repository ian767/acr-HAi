좋아. 아래는 바로 다른 프로젝트에 복붙해서 설계 문서/코드 ENUM/검증 로직 초안으로 쓸 수 있는 형태로 정리했어.
(상태 정의표 + 전이 조건표 + API contract 초안)

⸻

1) 상태 정의표 (ENUM)

1.1 OrderStatus

Status	의미	누가 변경
NEW	주문 생성 직후, 아직 배정 없음	WMS/WES
ALLOCATED	station + pickTask + (선택적으로) 로봇 예약 완료	WES
IN_PROGRESS	실제 실행 시작(로봇 이동 시작 or pickTask 시작 이벤트 수신)	WES (ESS 이벤트 기반)
COMPLETED	스테이션에서 처리 완료(검증 통과 후)	WES (Complete API)
CANCELLED (옵션)	사용자/운영 취소	WMS/WES
FAILED (옵션)	시스템 실패로 종료	WES


⸻

1.2 PickTaskState

State	의미	트리거
CREATED	pickTask 생성됨	WES
RESERVED	로봇/리소스 예약 완료 (reservation 생성)	WES
SOURCE_REQUESTED	ESS에 source retrieval 요청(Flow 생성)	WES→ESS
SOURCE_AT_CANTILEVER	tote가 cantilever에 준비됨	ESS 이벤트
SOURCE_PICKED	K50H가 tote pickup 완료	ESS 이벤트
SOURCE_AT_STATION	K50H가 station 접근/진입 완료(스캔 가능)	ESS 이벤트
COMPLETED	station 완료 처리 완료	WES Complete
STALLED (옵션)	진행 없음(알람/복구 대상)	watchdog


⸻

1.3 RobotStatus

Status	의미
idle	할당 대기
moving	이동 중
waiting_for_station	station queue lane/holding에서 대기
dwelling (옵션)	lift/lower/scan/pick 같은 작업 대기
blocked	이동 불가 누적 상태


⸻

1.4 Robot Reservation (필수)

reservation = {
  orderId: string,
  pickTaskId: string,
  stationId: string,
  sinceTs: number
}
reserved: boolean


⸻

1.5 Station Queue (필수)

station = {
  id: string,
  approachCell: {r:number,c:number},
  queueCells: Array<{r:number,c:number}>, // Q1..Qn
  holdingCell: {r:number,c:number},
  currentRobotId: string | null
}


⸻

1.6 Tote Possession (필수)

K50H 기준 최소:

holdPickTaskId: string | null     // 현재 들고있는 tote의 pickTask
holdAtStation: boolean            // station 도착+처리 가능 상태


⸻

2) 전이 조건 표 (State Machine Rules)

2.1 Order 전이

From → To	조건(필수)	이벤트/주체
NEW → ALLOCATED	pickTask 생성 + stationId 배정	WES allocator
ALLOCATED → IN_PROGRESS	로봇이 실제로 움직이기 시작 (move_started) 또는 pickTask가 SOURCE_REQUESTED로 진입	ESS event 기반
IN_PROGRESS → COMPLETED	Complete Validation 통과(아래 Rule CV-1)	WES complete
* → CANCELLED (옵션)	취소 요청	WMS/WES
IN_PROGRESS → FAILED (옵션)	복구 불가	WES


⸻

2.2 PickTask 전이

From → To	조건(필수)	이벤트/주체
CREATED → RESERVED	reservation 생성 (robot/station binding)	WES
RESERVED → SOURCE_REQUESTED	ESS flow 생성 요청 전송 성공	WES
SOURCE_REQUESTED → SOURCE_AT_CANTILEVER	A42TD 완료 이벤트 수신	ESS
SOURCE_AT_CANTILEVER → SOURCE_PICKED	K50H pickup 이벤트 수신	ESS
SOURCE_PICKED → SOURCE_AT_STATION	station 접근/진입 이벤트 수신	ESS
SOURCE_AT_STATION → COMPLETED	complete 검증 통과	WES


⸻

3) 필수 검증 규칙 (가져가면 “버그 재발 방지”됨)

CV-1: Complete Validation (로봇 없이 완료 금지)

POST /wes/stations/{stationId}/complete 호출 시 아래를 모두 만족해야 함:

필수 조건
	1.	station.currentRobotId != null 또는 대체 조건(아래 fallback)
	2.	robot.reservation.stationId == stationId
	3.	robot.holdAtStation == true
	4.	robot.holdPickTaskId == pickTaskId (또는 해당 order의 pickTask)

fallback (currentRobotId가 없을 때)
	•	robots 중 holdAtStation=true이고 reservation.stationId==stationId인 로봇을 찾고,
	•	발견 시 그 로봇을 readyRobot로 인정

만족 못 하면:
	•	HTTP 400
	•	{ code: "NO_ROBOT_AT_STATION", error: "No robot at station" }

⸻

QF-1: Station Queue FIFO “진입=holding, 전진=승급”
	•	신규 로봇은 항상 holdingCell로만 진입
	•	queue lane 내부에서는:
	•	앞 슬롯이 비면 한 칸 전진(Q3→Q2→Q1→A)
	•	approach → station cell 진입은:
	•	station.currentRobotId == null일 때만

로봇이 Q2 같은 중간 셀로 “점프 타겟팅”되면 물리적으로 막히고 FIFO가 깨짐.

⸻

4) API Contract 초안 (REST + WS)

4.1 WMS / Orders

Create Order

POST /api/wms/orders

{
  "sku": "SKU-C",
  "qty": 1,
  "stationId": "STATION-1",
  "fromLocation": {"r":10, "c":20}
}

Response

{
  "orderId": "ORD-123",
  "status": "NEW"
}


⸻

4.2 WES / Allocation

Allocate (manual trigger or internal)

POST /api/wes/orders/{orderId}/allocate

Response

{
  "orderId": "ORD-123",
  "status": "ALLOCATED",
  "pickTaskId": "PT-123",
  "stationId": "STATION-1",
  "allocatedRobotId": "R-1"
}

실제론 allocator가 내부적으로 수행하고, UI는 결과를 조회만 하는 형태가 더 좋음.

⸻

4.3 ESS / Commands

Request source tote to station (WES→ESS)

POST /api/ess/flows/request

{
  "pickTaskId": "PT-123",
  "orderId": "ORD-123",
  "stationId": "STATION-1",
  "fromLocation": {"r":10,"c":20},
  "sourceToteId": "TOTE-011"
}

Response

{ "ok": true }


⸻

4.4 Station Complete

Complete station order (operator action)

POST /api/wes/stations/{stationId}/complete

{
  "orderId": "ORD-123",
  "pickTaskId": "PT-123"
}

Success

{
  "ok": true,
  "orderId": "ORD-123",
  "newStatus": "COMPLETED"
}

Fail (no robot)

{
  "error": "No robot at station",
  "code": "NO_ROBOT_AT_STATION"
}


⸻

5) 이벤트/스트리밍(WS) 최소 스펙

5.1 Event Types (필수 6개)
	•	order.status_changed
	•	pickTask.state_changed
	•	robot.move_started
	•	robot.move_denied (reason 포함)
	•	robot.target_reached
	•	station.ready (K50H ready at station, 스캔 가능)

예시 payload

pickTask.state_changed

{
  "type": "pickTask.state_changed",
  "ts": 1739,
  "pickTaskId": "PT-1",
  "orderId": "ORD-3",
  "stationId": "STATION-1",
  "from": "SOURCE_REQUESTED",
  "to": "SOURCE_AT_CANTILEVER"
}

robot.move_denied

{
  "type": "robot.move_denied",
  "ts": 1740,
  "robotId": "R-1",
  "reason": "queue_lane_forbidden",
  "stationId": "STATION-2"
}


⸻

6) 구현 체크리스트 (이거만 맞추면 동작 정합성 확보됨)

✅ Order status는 ALLOCATED와 IN_PROGRESS를 반드시 구분한다
✅ Complete API는 “robot at station + tote hold” 검증 후에만 성공한다
✅ Station FIFO는 holding 진입 / 승급 전진 방식으로 구현한다
✅ Reservation은 station hopping을 막는 “single source of truth”다
✅ Snapshot은 counts + preview만, 전체 리스트는 cap 둔다



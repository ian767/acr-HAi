import { useWarehouseStore } from "../stores/useWarehouseStore";
import { Sound } from "../utils/sounds";
import type { Station } from "../types/station";
import type { PickTask } from "../types/pickTask";
import type { Order } from "../types/order";
import type { Alarm } from "../types/alarm";
import type { WSMessage, KPIPayload, SnapshotPayload, RobotUpdatedPayload, AllocationStatsPayload, ToteOriginHeatmapPayload } from "../types/websocket";

type MessageHandler = (payload: unknown) => void;

const handlers: Record<string, MessageHandler> = {
  snapshot: (payload) => {
    useWarehouseStore.getState().setSnapshot(payload as SnapshotPayload);
  },

  "robot.updated": (payload) => {
    const data = payload as RobotUpdatedPayload;
    useWarehouseStore.getState().updateRobots(data.robots);
  },

  "station.updated": (payload) => {
    // Merge partial station updates (e.g. queue_state from simulator)
    // with existing station data to avoid losing fields.
    const data = payload as Partial<Station> & { id: string };
    const existing = useWarehouseStore.getState().stations.find((s) => s.id === data.id);
    if (existing) {
      useWarehouseStore.getState().updateStation({ ...existing, ...data });
    } else {
      useWarehouseStore.getState().updateStation(data as Station);
    }
  },

  "task.updated": (payload) => {
    useWarehouseStore.getState().updatePickTask(payload as PickTask);
  },

  "pick_task.updated": (payload) => {
    const data = payload as { pick_task_id?: string; new_state?: string } & Partial<PickTask>;
    if (data.pick_task_id && data.new_state) {
      // Status-only update from event handlers.
      useWarehouseStore.getState().updatePickTaskStatus(data.pick_task_id, data.new_state);
    } else if (data.id) {
      // Full PickTask object.
      useWarehouseStore.getState().updatePickTask(data as PickTask);
    }
  },

  "kpi.updated": (payload) => {
    useWarehouseStore.getState().updateKPI(payload as KPIPayload);
  },

  "alarm.raised": (payload) => {
    useWarehouseStore.getState().addAlarm(payload as Alarm);
  },

  "alarm.cleared": (payload) => {
    const { id } = payload as { id: string };
    useWarehouseStore.getState().clearAlarm(id);
  },

  "order.updated": (payload) => {
    const data = payload as { order_id?: string; status?: string } & Partial<Order>;
    if (data.order_id && data.status) {
      // Partial update from event handlers.
      useWarehouseStore.getState().upsertOrderPartial(data as {
        order_id: string;
        status: string;
        external_id?: string;
        sku?: string;
        quantity?: number;
        station_id?: string;
      });
    } else if (data.id) {
      // Full Order object.
      useWarehouseStore.getState().updateOrder(data as Order);
    }
  },

  "heatmap.updated": (payload) => {
    const { cells } = payload as { cells: Record<string, number> };
    useWarehouseStore.getState().updateHeatmap(cells);
  },

  "allocation_skew.updated": (payload) => {
    useWarehouseStore.getState().updateAllocationStats(
      payload as AllocationStatsPayload,
    );
  },

  "tote_origin_heatmap.updated": (payload) => {
    useWarehouseStore.getState().updateToteOriginHeatmap(
      payload as ToteOriginHeatmapPayload,
    );
  },

  // ----- New logic.md required events -----

  "order.status_changed": (payload) => {
    const data = payload as { orderId: string; status: string };
    useWarehouseStore.getState().upsertOrderPartial({
      order_id: data.orderId,
      status: data.status,
    });
  },

  "pickTask.state_changed": (payload) => {
    const data = payload as {
      pickTaskId: string;
      orderId?: string;
      stationId?: string;
      from: string;
      to: string;
    };
    useWarehouseStore.getState().updatePickTaskStatus(data.pickTaskId, data.to);
  },

  "robot.move_started": (_payload) => {
    // Visual feedback handled by robot.updated; log for debug.
  },

  "robot.move_denied": (_payload) => {
    // Could display alert; currently no-op.
  },

  "robot.target_reached": (_payload) => {
    // Visual feedback handled by robot.updated; log for debug.
  },

  "station.ready": (payload) => {
    const data = payload as { stationId: string; pickTaskId?: string };
    // Update station status to ACTIVE when ready for scanning
    const stations = useWarehouseStore.getState().stations;
    const station = stations.find((s) => s.id === data.stationId);
    if (station) {
      useWarehouseStore.getState().updateStation({
        ...station,
        status: "ACTIVE",
      });
    }
    // Play robot arrival sound
    Sound.robotArrived();
  },
};

export function routeMessage(raw: string): void {
  try {
    const msg = JSON.parse(raw) as WSMessage;
    const handler = handlers[msg.type];
    if (handler) {
      handler(msg.payload);
    } else {
      console.warn("Unknown WS message type:", msg.type);
    }
  } catch {
    console.error("Failed to parse WS message:", raw);
  }
}

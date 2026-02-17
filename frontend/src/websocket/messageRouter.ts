import { useWarehouseStore } from "../stores/useWarehouseStore";
import type { Station } from "../types/station";
import type { PickTask } from "../types/pickTask";
import type { Order } from "../types/order";
import type { Alarm } from "../types/alarm";
import type { WSMessage, WSMessageType, KPIPayload, SnapshotPayload, RobotUpdatedPayload } from "../types/websocket";

type MessageHandler = (payload: unknown) => void;

const handlers: Record<WSMessageType, MessageHandler> = {
  snapshot: (payload) => {
    useWarehouseStore.getState().setSnapshot(payload as SnapshotPayload);
  },

  "robot.updated": (payload) => {
    const data = payload as RobotUpdatedPayload;
    useWarehouseStore.getState().updateRobots(data.robots);
  },

  "station.updated": (payload) => {
    useWarehouseStore.getState().updateStation(payload as Station);
  },

  "task.updated": (payload) => {
    useWarehouseStore.getState().updatePickTask(payload as PickTask);
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
    useWarehouseStore.getState().updateOrder(payload as Order);
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

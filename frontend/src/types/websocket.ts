import type { RobotRealtime } from "./robot";
import type { Station } from "./station";
import type { PickTask } from "./pickTask";
import type { Order } from "./order";
import type { Alarm } from "./alarm";

export type WSMessageType =
  | "snapshot"
  | "robot.updated"
  | "station.updated"
  | "task.updated"
  | "kpi.updated"
  | "alarm.raised"
  | "alarm.cleared"
  | "order.updated";

export interface WSMessage<T = unknown> {
  type: WSMessageType;
  payload: T;
  timestamp: number;
}

export interface SnapshotPayload {
  robots: Record<string, RobotRealtime>;
  stations: Station[];
  pick_tasks: PickTask[];
  orders: Order[];
  alarms: Alarm[];
}

export interface RobotUpdatedPayload {
  robots: Record<string, RobotRealtime>;
}

export interface KPIPayload {
  orders_completed: number;
  orders_in_progress: number;
  picks_per_hour: number;
  robot_utilization: number;
  avg_pick_time_s: number;
}

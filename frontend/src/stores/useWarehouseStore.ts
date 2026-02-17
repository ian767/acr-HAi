import { create } from "zustand";
import type { RobotRealtime } from "../types/robot";
import type { Station } from "../types/station";
import type { PickTask } from "../types/pickTask";
import type { Order } from "../types/order";
import type { Alarm } from "../types/alarm";
import type { KPIPayload } from "../types/websocket";

interface WarehouseState {
  // Real-time state from WebSocket
  robots: Record<string, RobotRealtime>;
  stations: Station[];
  pickTasks: PickTask[];
  orders: Order[];
  alarms: Alarm[];
  kpi: KPIPayload | null;

  // Connection status
  connected: boolean;

  // Actions
  setSnapshot: (data: {
    robots: Record<string, RobotRealtime>;
    stations: Station[];
    pick_tasks: PickTask[];
    orders: Order[];
    alarms: Alarm[];
  }) => void;
  updateRobots: (robots: Record<string, RobotRealtime>) => void;
  updateStation: (station: Station) => void;
  updatePickTask: (task: PickTask) => void;
  updateOrder: (order: Order) => void;
  updateKPI: (kpi: KPIPayload) => void;
  addAlarm: (alarm: Alarm) => void;
  clearAlarm: (alarmId: string) => void;
  setConnected: (connected: boolean) => void;
}

export const useWarehouseStore = create<WarehouseState>((set) => ({
  robots: {},
  stations: [],
  pickTasks: [],
  orders: [],
  alarms: [],
  kpi: null,
  connected: false,

  setSnapshot: (data) =>
    set({
      robots: data.robots,
      stations: data.stations,
      pickTasks: data.pick_tasks,
      orders: data.orders,
      alarms: data.alarms,
    }),

  updateRobots: (robots) =>
    set((state) => ({
      robots: { ...state.robots, ...robots },
    })),

  updateStation: (station) =>
    set((state) => ({
      stations: state.stations.map((s) => (s.id === station.id ? station : s)),
    })),

  updatePickTask: (task) =>
    set((state) => ({
      pickTasks: state.pickTasks.map((t) => (t.id === task.id ? task : t)),
    })),

  updateOrder: (order) =>
    set((state) => ({
      orders: state.orders.map((o) => (o.id === order.id ? order : o)),
    })),

  updateKPI: (kpi) => set({ kpi }),

  addAlarm: (alarm) =>
    set((state) => ({
      alarms: [alarm, ...state.alarms],
    })),

  clearAlarm: (alarmId) =>
    set((state) => ({
      alarms: state.alarms.map((a) =>
        a.id === alarmId ? { ...a, acknowledged: true, acknowledged_at: new Date().toISOString() } : a,
      ),
    })),

  setConnected: (connected) => set({ connected }),
}));

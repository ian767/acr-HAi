import { create } from "zustand";
import type { RobotRealtime } from "../types/robot";
import type { Station } from "../types/station";
import type { PickTask } from "../types/pickTask";
import type { Order } from "../types/order";
import type { Alarm } from "../types/alarm";
import type { KPIPayload } from "../types/websocket";

export interface RobotAnimation {
  fromRow: number;
  fromCol: number;
  toRow: number;
  toCol: number;
  startTime: number;
}

interface WarehouseState {
  // Real-time state from WebSocket
  robots: Record<string, RobotRealtime>;
  stations: Station[];
  pickTasks: PickTask[];
  orders: Order[];
  alarms: Alarm[];
  kpi: KPIPayload | null;

  // Animation state for smooth robot movement
  robotAnimations: Record<string, RobotAnimation>;

  // Heatmap data: "row,col" -> congestion value (0..1)
  heatmap: Record<string, number>;

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
  updatePickTaskStatus: (taskId: string, newState: string) => void;
  updateOrder: (order: Order) => void;
  upsertOrderPartial: (data: {
    order_id: string;
    status: string;
    external_id?: string;
    sku?: string;
    quantity?: number;
    station_id?: string;
  }) => void;
  updateKPI: (kpi: KPIPayload) => void;
  addAlarm: (alarm: Alarm) => void;
  clearAlarm: (alarmId: string) => void;
  setConnected: (connected: boolean) => void;
  updateHeatmap: (cells: Record<string, number>) => void;
  resetAll: () => void;
}

export const useWarehouseStore = create<WarehouseState>((set) => ({
  robots: {},
  stations: [],
  pickTasks: [],
  orders: [],
  alarms: [],
  kpi: null,
  robotAnimations: {},
  heatmap: {},
  connected: false,

  setSnapshot: (data) =>
    set({
      robots: data.robots,
      stations: data.stations,
      pickTasks: data.pick_tasks,
      orders: data.orders,
      alarms: data.alarms,
    }),

  updateRobots: (incoming) =>
    set((state) => {
      const now = performance.now();
      const newAnimations = { ...state.robotAnimations };
      const mergedRobots = { ...state.robots };

      for (const [id, robot] of Object.entries(incoming)) {
        const prev = state.robots[id];
        if (prev && (prev.row !== robot.row || prev.col !== robot.col)) {
          newAnimations[id] = {
            fromRow: prev.row,
            fromCol: prev.col,
            toRow: robot.row,
            toCol: robot.col,
            startTime: now,
          };
        }
        // Merge: preserve name/type from existing data if not in the update.
        mergedRobots[id] = prev
          ? { ...prev, ...robot }
          : robot;
      }

      return {
        robots: mergedRobots,
        robotAnimations: newAnimations,
      };
    }),

  updateStation: (station) =>
    set((state) => ({
      stations: state.stations.map((s) => (s.id === station.id ? station : s)),
    })),

  updatePickTask: (task) =>
    set((state) => {
      const idx = state.pickTasks.findIndex((t) => t.id === task.id);
      if (idx >= 0) {
        const copy = [...state.pickTasks];
        copy[idx] = task;
        return { pickTasks: copy };
      }
      return { pickTasks: [...state.pickTasks, task] };
    }),

  updatePickTaskStatus: (taskId, newState) =>
    set((state) => {
      const idx = state.pickTasks.findIndex((t) => t.id === taskId);
      if (idx >= 0) {
        const copy = [...state.pickTasks];
        copy[idx] = { ...copy[idx]!, state: newState as PickTask["state"] };
        return { pickTasks: copy };
      }
      // Task not in store yet — ignore partial update.
      return state;
    }),

  updateOrder: (order) =>
    set((state) => {
      const idx = state.orders.findIndex((o) => o.id === order.id);
      if (idx >= 0) {
        const copy = [...state.orders];
        copy[idx] = order;
        return { orders: copy };
      }
      return { orders: [...state.orders, order] };
    }),

  upsertOrderPartial: (data) =>
    set((state) => {
      const idx = state.orders.findIndex((o) => o.id === data.order_id);
      if (idx >= 0) {
        const copy = [...state.orders];
        copy[idx] = {
          ...copy[idx]!,
          status: data.status as Order["status"],
          ...(data.station_id != null && { station_id: data.station_id }),
        };
        return { orders: copy };
      }
      // New order — create a minimal entry (OrderCreated provides enough data).
      if (data.external_id) {
        const entry: Order = {
          id: data.order_id,
          external_id: data.external_id,
          sku: data.sku ?? "",
          quantity: data.quantity ?? 0,
          priority: 0,
          pbt_at: null,
          status: data.status as Order["status"],
          station_id: data.station_id ?? null,
          zone_id: null,
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
        };
        return { orders: [...state.orders, entry] };
      }
      // Status-only update for unknown order — ignore.
      return state;
    }),

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

  updateHeatmap: (cells) => set({ heatmap: cells }),

  resetAll: () =>
    set({
      robots: {},
      stations: [],
      pickTasks: [],
      orders: [],
      alarms: [],
      kpi: null,
      robotAnimations: {},
      heatmap: {},
    }),
}));

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
  updateOrder: (order: Order) => void;
  updateKPI: (kpi: KPIPayload) => void;
  addAlarm: (alarm: Alarm) => void;
  clearAlarm: (alarmId: string) => void;
  setConnected: (connected: boolean) => void;
  updateHeatmap: (cells: Record<string, number>) => void;
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
      }

      return {
        robots: { ...state.robots, ...incoming },
        robotAnimations: newAnimations,
      };
    }),

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

  updateHeatmap: (cells) => set({ heatmap: cells }),
}));

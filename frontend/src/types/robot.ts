export type RobotType = "K50H" | "A42TD";

export type RobotStatus =
  | "IDLE"
  | "ASSIGNED"
  | "MOVING"
  | "WAITING"
  | "WAITING_FOR_STATION"
  | "DWELLING"
  | "BLOCKED"
  | "CHARGING";

export interface RobotReservation {
  order_id: string | null;
  pick_task_id: string | null;
  station_id: string | null;
}

export interface Robot {
  id: string;
  name: string;
  type: RobotType;
  zone_id: string;
  status: RobotStatus;
  grid_row: number;
  grid_col: number;
  heading: number;
  current_task_id: string | null;
  speed: number;
  reserved?: boolean;
  reservation?: RobotReservation;
  hold_pick_task_id?: string | null;
  hold_at_station?: boolean;
  territory_col_min?: number | null;
  territory_col_max?: number | null;
  territory_row_min?: number | null;
  territory_row_max?: number | null;
}

export interface RobotRealtime {
  id: string;
  name?: string;
  type?: RobotType;
  row: number;
  col: number;
  heading: number;
  status: RobotStatus;
  path?: [number, number][];
  reserved?: boolean;
  hold_pick_task_id?: string | null;
  hold_at_station?: boolean;
  task_type?: string | null; // "RETRIEVE" | "RETURN" | null
  wait_ticks?: number;
  territory_col_min?: number | null;
  territory_col_max?: number | null;
  territory_row_min?: number | null;
  territory_row_max?: number | null;
  // Blocked cell diagnostics
  blocked_cell?: [number, number] | null;
  blocked_reason?: string | null;
  blocked_by?: string | null;
  blocked_age?: number | null;
  // Debug: target cell (cached from queue advance)
  target_row?: number;
  target_col?: number;
  target_station?: string;
}

export type RobotType = "K50H" | "A42TD";

export type RobotStatus =
  | "IDLE"
  | "ASSIGNED"
  | "MOVING"
  | "WAITING"
  | "DOCKING"
  | "BLOCKED"
  | "CHARGING";

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
}

export interface RobotRealtime {
  id: string;
  row: number;
  col: number;
  heading: number;
  status: RobotStatus;
  path?: [number, number][];
}

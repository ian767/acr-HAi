export type StationStatus = "IDLE" | "ACTIVE" | "PAUSED";

export interface QueueCell {
  position: number;
  row: number;
  col: number;
}

export interface QueueState {
  station: string | null;
  approach: string | null;
  queue: (string | null)[];
  holding: string | null;
  _version_tick?: number;
  _mutation_reason?: string;
}

export interface Station {
  id: string;
  name: string;
  zone_id: string;
  grid_row: number;
  grid_col: number;
  is_online: boolean;
  status: StationStatus;
  max_queue_size: number;
  approach_cell_row?: number | null;
  approach_cell_col?: number | null;
  holding_cell_row?: number | null;
  holding_cell_col?: number | null;
  queue_cells?: QueueCell[];
  current_robot_id?: string | null;
  queue_state?: QueueState | null;
  queue_state_version_tick?: number | null;
  last_queue_mutation_reason?: string | null;
}

export interface PutWallSlot {
  id: string;
  station_id: string;
  slot_label: string;
  target_tote_id: string | null;
  is_locked: boolean;
}

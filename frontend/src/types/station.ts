export type StationStatus = "IDLE" | "ACTIVE" | "PAUSED";

export interface Station {
  id: string;
  name: string;
  zone_id: string;
  grid_row: number;
  grid_col: number;
  is_online: boolean;
  status: StationStatus;
  max_queue_size: number;
}

export interface PutWallSlot {
  id: string;
  station_id: string;
  slot_label: string;
  target_tote_id: string | null;
  is_locked: boolean;
}

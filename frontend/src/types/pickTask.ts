export type PickTaskState =
  | "CREATED"
  | "RESERVED"
  | "SOURCE_REQUESTED"
  | "SOURCE_AT_CANTILEVER"
  | "SOURCE_PICKED"
  | "SOURCE_AT_STATION"
  | "PICKING"
  | "RETURN_REQUESTED"
  | "RETURN_AT_CANTILEVER"
  | "COMPLETED"
  | "STALLED";

export interface PickTask {
  id: string;
  order_id: string;
  station_id: string;
  sku: string;
  qty_to_pick: number;
  qty_picked: number;
  source_tote_id: string | null;
  target_tote_id: string | null;
  target_tote_barcode: string | null;
  put_wall_slot_id: string | null;
  state: PickTaskState;
  assigned_robot_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface PutWallSlot {
  id: string;
  station_id: string;
  slot_label: string;
  target_tote_id: string | null;
  target_tote_barcode: string | null;
  is_locked: boolean;
}

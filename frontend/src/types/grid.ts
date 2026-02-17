export type CellType =
  | "FLOOR"
  | "RACK"
  | "CANTILEVER"
  | "STATION"
  | "AISLE"
  | "WALL"
  | "CHARGING";

export interface GridCell {
  row: number;
  col: number;
  type: CellType;
  label?: string;
}

export interface GridState {
  zone_id: string;
  rows: number;
  cols: number;
  cells: GridCell[];
}

export interface Zone {
  id: string;
  name: string;
  grid_rows: number;
  grid_cols: number;
}

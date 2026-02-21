export type OrderStatus =
  | "NEW"
  | "ALLOCATED"
  | "IN_PROGRESS"
  | "COMPLETED"
  | "CANCELLED"
  | "FAILED";

export interface Order {
  id: string;
  external_id: string;
  sku: string;
  quantity: number;
  priority: number;
  pbt_at: string | null;
  status: OrderStatus;
  station_id: string | null;
  zone_id: string | null;
  created_at: string;
  updated_at: string;
}

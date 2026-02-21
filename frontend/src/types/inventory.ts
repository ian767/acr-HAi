export interface ToteDetail {
  id: string;
  barcode: string;
  sku: string | null;
  sku_name: string | null;
  band: string | null;
  quantity: number;
  status: string;
  location_label: string | null;
}

export interface InventorySummary {
  id: string;
  sku: string;
  sku_name: string | null;
  band: string;
  zone_id: string | null;
  total_qty: number;
  allocated_qty: number;
}

import { api } from "./client";
import type { Order } from "../types/order";
import type { Station } from "../types/station";
import type { PickTask, PutWallSlot } from "../types/pickTask";
import type { ToteDetail, InventorySummary } from "../types/inventory";

export const wesApi = {
  createOrder: (body: { sku: string; quantity: number; priority?: number }) =>
    api.post<Order>(`/wes/orders`, body),

  getAvailableSkus: () =>
    api.get<Array<{ sku: string; available_qty: number }>>(`/wes/inventory/skus`),

  listOrders: (params?: { status?: string; limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.status) qs.set("status", params.status);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const query = qs.toString();
    return api.get<Order[]>(`/wes/orders${query ? `?${query}` : ""}`);
  },

  getOrder: (id: string) => api.get<Order>(`/wes/orders/${id}`),

  allocateOrder: (id: string) => api.post<Order>(`/wes/orders/${id}/allocate`),

  listStations: (zoneId?: string) => {
    const qs = zoneId ? `?zone_id=${zoneId}` : "";
    return api.get<Station[]>(`/wes/stations${qs}`);
  },

  toggleStationOnline: (id: string, online: boolean) =>
    api.put<Station>(`/wes/stations/${id}/online`, { online }),

  listPickTasks: (params?: { station_id?: string; state?: string }) => {
    const qs = new URLSearchParams();
    if (params?.station_id) qs.set("station_id", params.station_id);
    if (params?.state) qs.set("state", params.state);
    const query = qs.toString();
    return api.get<PickTask[]>(`/wes/pick-tasks${query ? `?${query}` : ""}`);
  },

  getPickTask: (id: string) => api.get<PickTask>(`/wes/pick-tasks/${id}`),

  dispatchRetrieve: (pickTaskId: string) =>
    api.post<PickTask>(`/wes/pick-tasks/${pickTaskId}/dispatch`),

  scanItem: (stationId: string, pickTaskId: string) =>
    api.post<PickTask>(`/wes/stations/${stationId}/scan`, { pick_task_id: pickTaskId }),

  bindTote: (stationId: string, pickTaskId: string, targetToteBarcode: string) =>
    api.post<PickTask>(`/wes/stations/${stationId}/bind-tote`, {
      pick_task_id: pickTaskId,
      target_tote_barcode: targetToteBarcode,
    }),

  toteFull: (stationId: string, pickTaskId: string) =>
    api.post<PickTask>(`/wes/stations/${stationId}/tote-full`, {
      pick_task_id: pickTaskId,
    }),

  listInventory: (params?: { sku?: string; zone_id?: string }) => {
    const qs = new URLSearchParams();
    if (params?.sku) qs.set("sku", params.sku);
    if (params?.zone_id) qs.set("zone_id", params.zone_id);
    const query = qs.toString();
    return api.get<InventorySummary[]>(`/wes/inventory${query ? `?${query}` : ""}`);
  },

  listTotes: (params?: {
    sku?: string;
    barcode?: string;
    band?: string;
    status?: string;
    limit?: number;
    offset?: number;
  }) => {
    const qs = new URLSearchParams();
    if (params?.sku) qs.set("sku", params.sku);
    if (params?.barcode) qs.set("barcode", params.barcode);
    if (params?.band) qs.set("band", params.band);
    if (params?.status) qs.set("status", params.status);
    if (params?.limit) qs.set("limit", String(params.limit));
    if (params?.offset) qs.set("offset", String(params.offset));
    const query = qs.toString();
    return api.get<ToteDetail[]>(`/wes/totes${query ? `?${query}` : ""}`);
  },

  seedInventory: (preset: string) =>
    api.post<{ status: string; preset: string; sku_count: number; totes_created: number }>(
      `/wes/inventory/seed`,
      { preset },
    ),

  setReleaseMode: (enabled: boolean) =>
    api.put(`/wes/release-mode`, { enabled }),

  getPutwall: (stationId: string) =>
    api.get<PutWallSlot[]>(`/wes/stations/${stationId}/putwall`),

  bindPutwallSlot: (stationId: string, slotId: string, toteBarcode: string) =>
    api.post<PutWallSlot>(`/wes/stations/${stationId}/putwall/bind-slot`, {
      slot_id: slotId,
      tote_barcode: toteBarcode,
    }),
};

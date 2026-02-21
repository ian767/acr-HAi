import { api } from "./client";
import type { Robot } from "../types/robot";
import type { Zone, GridState } from "../types/grid";

export const essApi = {
  listRobots: (params?: { zone_id?: string; status?: string }) => {
    const qs = new URLSearchParams();
    if (params?.zone_id) qs.set("zone_id", params.zone_id);
    if (params?.status) qs.set("status", params.status);
    const query = qs.toString();
    return api.get<Robot[]>(`/ess/robots${query ? `?${query}` : ""}`);
  },

  getRobot: (id: string) => api.get<Robot>(`/ess/robots/${id}`),

  getGrid: (zoneId: string) => api.get<GridState>(`/ess/grid?zone_id=${zoneId}`),

  listZones: () => api.get<Zone[]>(`/ess/zones`),

  getZone: (id: string) => api.get<Zone>(`/ess/zones/${id}`),

  // Simulation controls
  simulationStart: () => api.post(`/ess/simulation/start`),
  simulationPause: () => api.post(`/ess/simulation/pause`),
  simulationResume: () => api.post(`/ess/simulation/resume`),
  simulationSetSpeed: (speed: number) => api.post(`/ess/simulation/speed`, { speed }),
  simulationStep: () => api.post(`/ess/simulation/step`),
  simulationReset: () => api.post(`/ess/simulation/reset`),
  simulationConfig: () => api.get(`/ess/simulation/config`),
  simulationApplyPreset: (name: string) => api.post(`/ess/simulation/presets/apply`, { name }),

  simulationApplyCustomPreset: (config: Record<string, unknown>) =>
    api.post(`/ess/simulation/presets/custom`, config),

  // Grid editor
  gridSave: (data: { name: string; rows: number; cols: number; cells: Array<{ row: number; col: number; type: string }> }) =>
    api.post(`/ess/grid/save`, data),

  gridListLayouts: () => api.get(`/ess/grid/layouts`),

  gridLoadLayout: (name: string) => api.get(`/ess/grid/layouts/${name}`),

  gridUpdateCell: (row: number, col: number, cellType: string) =>
    api.post(`/ess/grid/cell`, { row, col, cell_type: cellType }),

  gridLoadInto: (name: string) => api.post(`/ess/grid/load/${name}`),

  gridResize: (rows: number, cols: number) =>
    api.post(`/ess/grid/resize?rows=${rows}&cols=${cols}`),
};

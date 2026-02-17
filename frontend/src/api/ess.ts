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
};

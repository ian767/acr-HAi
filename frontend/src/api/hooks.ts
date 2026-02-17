import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { wesApi } from "./wes";
import { essApi } from "./ess";

// === WES Hooks ===

export function useOrders(params?: { status?: string }) {
  return useQuery({
    queryKey: ["orders", params],
    queryFn: () => wesApi.listOrders(params),
    refetchInterval: 5000,
  });
}

export function useOrder(id: string) {
  return useQuery({
    queryKey: ["orders", id],
    queryFn: () => wesApi.getOrder(id),
  });
}

export function useAllocateOrder() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => wesApi.allocateOrder(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["orders"] }),
  });
}

export function useStations(zoneId?: string) {
  return useQuery({
    queryKey: ["stations", zoneId],
    queryFn: () => wesApi.listStations(zoneId),
    refetchInterval: 5000,
  });
}

export function useToggleStationOnline() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, online }: { id: string; online: boolean }) =>
      wesApi.toggleStationOnline(id, online),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["stations"] }),
  });
}

export function usePickTasks(params?: { station_id?: string; state?: string }) {
  return useQuery({
    queryKey: ["pick-tasks", params],
    queryFn: () => wesApi.listPickTasks(params),
    refetchInterval: 3000,
  });
}

export function useScanItem() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ stationId, pickTaskId }: { stationId: string; pickTaskId: string }) =>
      wesApi.scanItem(stationId, pickTaskId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pick-tasks"] }),
  });
}

// === ESS Hooks ===

export function useRobots(params?: { zone_id?: string; status?: string }) {
  return useQuery({
    queryKey: ["robots", params],
    queryFn: () => essApi.listRobots(params),
    refetchInterval: 5000,
  });
}

export function useRobot(id: string) {
  return useQuery({
    queryKey: ["robots", id],
    queryFn: () => essApi.getRobot(id),
  });
}

export function useGrid(zoneId: string) {
  return useQuery({
    queryKey: ["grid", zoneId],
    queryFn: () => essApi.getGrid(zoneId),
    staleTime: 60_000,
  });
}

export function useZones() {
  return useQuery({
    queryKey: ["zones"],
    queryFn: () => essApi.listZones(),
    staleTime: 60_000,
  });
}

import { create } from "zustand";

interface UiState {
  // Sidebar
  sidebarOpen: boolean;
  toggleSidebar: () => void;

  // Selected entities
  selectedRobotId: string | null;
  selectedStationId: string | null;
  selectedOrderId: string | null;
  selectRobot: (id: string | null) => void;
  selectStation: (id: string | null) => void;
  selectOrder: (id: string | null) => void;

  // Map controls
  showPaths: boolean;
  showHeatmap: boolean;
  togglePaths: () => void;
  toggleHeatmap: () => void;

  // Simulation
  simulationRunning: boolean;
  simulationSpeed: number;
  setSimulationRunning: (running: boolean) => void;
  setSimulationSpeed: (speed: number) => void;

  // Zone selection
  activeZoneId: string | null;
  setActiveZone: (id: string | null) => void;

  // Map editor
  editorMode: boolean;
  editorTool: string;
  setEditorMode: (on: boolean) => void;
  setEditorTool: (tool: string) => void;
}

export const useUiStore = create<UiState>((set) => ({
  sidebarOpen: true,
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  selectedRobotId: null,
  selectedStationId: null,
  selectedOrderId: null,
  selectRobot: (id) => set({ selectedRobotId: id }),
  selectStation: (id) => set({ selectedStationId: id }),
  selectOrder: (id) => set({ selectedOrderId: id }),

  showPaths: true,
  showHeatmap: false,
  togglePaths: () => set((s) => ({ showPaths: !s.showPaths })),
  toggleHeatmap: () => set((s) => ({ showHeatmap: !s.showHeatmap })),

  simulationRunning: false,
  simulationSpeed: 1.0,
  setSimulationRunning: (running) => set({ simulationRunning: running }),
  setSimulationSpeed: (speed) => set({ simulationSpeed: speed }),

  activeZoneId: null,
  setActiveZone: (id) => set({ activeZoneId: id }),

  editorMode: false,
  editorTool: "RACK",
  setEditorMode: (on) => set({ editorMode: on }),
  setEditorTool: (tool) => set({ editorTool: tool }),
}));

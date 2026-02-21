import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { WarehouseMap } from "./components/map/WarehouseMap";
import { MapLegend } from "./components/map/MapLegend";
import { OrderCreatePanel } from "./components/OrderCreatePanel";
import { StationWorkflow } from "./components/StationWorkflow";
import { StationOperatorView } from "./components/StationOperatorView";
import { PresetConfigurator } from "./components/PresetConfigurator";
import { EditorToolbar } from "./components/map/EditorToolbar";
import { useUiStore } from "@/stores/useUiStore";
import { useWarehouseStore } from "@/stores/useWarehouseStore";
import { useZones } from "@/api/hooks";
import { essApi } from "@/api/ess";
import type { RobotStatus } from "@/types/robot";

// ------------------------------------------------------------------ styles

const PAGE_STYLE: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  height: "100%",
  background: "#0e1015",
  color: "#e2e8f0",
  fontFamily: "Inter, system-ui, sans-serif",
};

const TOOLBAR_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 12,
  padding: "8px 16px",
  background: "#1a1d27",
  borderBottom: "1px solid #2d3148",
  flexShrink: 0,
  flexWrap: "wrap",
};

const BODY_STYLE: React.CSSProperties = {
  display: "flex",
  flex: 1,
  overflow: "hidden",
  position: "relative",
};

const MAP_STYLE: React.CSSProperties = {
  flex: 1,
  position: "relative",
};

const PANEL_STYLE: React.CSSProperties = {
  width: 340,
  background: "#1a1d27",
  borderLeft: "1px solid #2d3148",
  padding: 16,
  overflowY: "auto",
  flexShrink: 0,
};

const BTN_STYLE: React.CSSProperties = {
  padding: "4px 12px",
  border: "1px solid #4a5568",
  borderRadius: 4,
  background: "#2d3148",
  color: "#e2e8f0",
  cursor: "pointer",
  fontSize: 13,
};

const BTN_PRIMARY: React.CSSProperties = {
  ...BTN_STYLE,
  background: "#3b82f6",
  borderColor: "#3b82f6",
};

const SELECT_STYLE: React.CSSProperties = {
  padding: "4px 8px",
  border: "1px solid #4a5568",
  borderRadius: 4,
  background: "#2d3148",
  color: "#e2e8f0",
  fontSize: 13,
};

const LABEL_STYLE: React.CSSProperties = {
  fontSize: 12,
  color: "#94a3b8",
};

const STATUS_COLORS: Record<RobotStatus, string> = {
  IDLE: "#9ca3af",
  ASSIGNED: "#3b82f6",
  MOVING: "#22c55e",
  WAITING: "#eab308",
  WAITING_FOR_STATION: "#a855f7",
  DWELLING: "#8b5cf6",
  BLOCKED: "#ef4444",
  CHARGING: "#14b8a6",
};

// ------------------------------------------------------------------ component

/**
 * WarehouseMapPage is the top-level page for the ESS map visualization.
 * It includes:
 *  - A toolbar with zone selector, simulation controls, and map toggles.
 *  - The full WarehouseMap canvas.
 *  - A side panel showing details for the selected robot.
 */
export function WarehouseMapPage() {
  const queryClient = useQueryClient();
  const { data: zones } = useZones();

  const activeZoneId = useUiStore((s) => s.activeZoneId);
  const setActiveZone = useUiStore((s) => s.setActiveZone);
  const simulationRunning = useUiStore((s) => s.simulationRunning);
  const setSimulationRunning = useUiStore((s) => s.setSimulationRunning);
  const simulationSpeed = useUiStore((s) => s.simulationSpeed);
  const setSimulationSpeed = useUiStore((s) => s.setSimulationSpeed);
  const showPaths = useUiStore((s) => s.showPaths);
  const togglePaths = useUiStore((s) => s.togglePaths);
  const showHeatmap = useUiStore((s) => s.showHeatmap);
  const toggleHeatmap = useUiStore((s) => s.toggleHeatmap);
  const selectedRobotId = useUiStore((s) => s.selectedRobotId);
  const selectRobot = useUiStore((s) => s.selectRobot);
  const editorMode = useUiStore((s) => s.editorMode);
  const setEditorMode = useUiStore((s) => s.setEditorMode);

  const robots = useWarehouseStore((s) => s.robots);
  const resetWarehouse = useWarehouseStore((s) => s.resetAll);
  const selectedRobot = selectedRobotId ? robots[selectedRobotId] : null;

  const [presets, setPresets] = useState<string[]>([]);
  const [applyingPreset, setApplyingPreset] = useState(false);
  const [showConfigurator, setShowConfigurator] = useState(false);
  const [interactiveMode, setInteractiveMode] = useState(false);
  const [operatorStationId, setOperatorStationId] = useState<string | null>(null);

  const stations = useWarehouseStore((s) => s.stations);
  const pickTasks = useWarehouseStore((s) => s.pickTasks);

  // Auto-select the first zone if none is active yet.
  useMemo(() => {
    if (!activeZoneId && zones && zones.length > 0) {
      setActiveZone(zones[0]!.id);
    }
  }, [activeZoneId, zones, setActiveZone]);

  // Fetch available presets on mount.
  useEffect(() => {
    essApi.simulationConfig().then((cfg: any) => {
      if (cfg?.presets) setPresets(cfg.presets);
      if (cfg?.interactive_mode != null) setInteractiveMode(cfg.interactive_mode);
    });
  }, []);

  // Shared post-preset cleanup: invalidate caches, clear stale zone, refresh config.
  // NOTE: Do NOT call resetWarehouse() here — the backend broadcasts a fresh
  // snapshot via WS (before the HTTP response) that populates robots/stations.
  // Calling resetWarehouse() after would wipe that data.
  const refreshAfterPreset = useCallback(async () => {
    setSimulationRunning(false);
    selectRobot(null);

    // Clear stale zone so auto-select picks the new one.
    setActiveZone(null);

    // Invalidate React Query caches so zones and grid refetch immediately.
    await queryClient.invalidateQueries({ queryKey: ["zones"] });
    await queryClient.invalidateQueries({ queryKey: ["grid"] });

    // Refresh config for presets list, wes_driven, interactive_mode.
    const cfg: any = await essApi.simulationConfig();
    if (cfg?.presets) setPresets(cfg.presets);
    if (cfg?.interactive_mode != null) setInteractiveMode(cfg.interactive_mode);
  }, [queryClient, setSimulationRunning, setActiveZone, selectRobot]);

  // -------------------------------------------------------- preset handler

  const handleApplyPreset = useCallback(
    async (e: React.ChangeEvent<HTMLSelectElement>) => {
      const name = e.target.value;
      if (!name) return;
      setApplyingPreset(true);
      try {
        await essApi.simulationApplyPreset(name);
        await refreshAfterPreset();
      } finally {
        setApplyingPreset(false);
        e.target.value = "";
      }
    },
    [refreshAfterPreset],
  );

  // -------------------------------------------------------- simulation controls

  const handleStart = useCallback(async () => {
    await essApi.simulationStart();
    setSimulationRunning(true);
  }, [setSimulationRunning]);

  const handlePause = useCallback(async () => {
    await essApi.simulationPause();
    setSimulationRunning(false);
  }, [setSimulationRunning]);

  const handleResume = useCallback(async () => {
    await essApi.simulationResume();
    setSimulationRunning(true);
  }, [setSimulationRunning]);

  const handleStep = useCallback(async () => {
    await essApi.simulationStep();
  }, []);

  const handleReset = useCallback(async () => {
    await essApi.simulationReset();
    setSimulationRunning(false);
    setActiveZone(null);
    selectRobot(null);
    resetWarehouse();
    setInteractiveMode(false);
  }, [setSimulationRunning, setActiveZone, selectRobot, resetWarehouse]);

  const handleSpeedChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const speed = parseFloat(e.target.value);
      setSimulationSpeed(speed);
      await essApi.simulationSetSpeed(speed);
    },
    [setSimulationSpeed],
  );

  // -------------------------------------------------------------- render

  return (
    <div style={PAGE_STYLE}>
      {/* ---- Toolbar ---- */}
      <div style={TOOLBAR_STYLE}>
        {/* Zone selector */}
        <label style={LABEL_STYLE}>Zone</label>
        <select
          style={SELECT_STYLE}
          value={activeZoneId ?? ""}
          onChange={(e) => setActiveZone(e.target.value || null)}
        >
          <option value="">-- select --</option>
          {zones?.map((z) => (
            <option key={z.id} value={z.id}>
              {z.name}
            </option>
          ))}
        </select>

        <div style={{ width: 1, height: 24, background: "#4a5568" }} />

        {/* Preset selector */}
        <label style={LABEL_STYLE}>Preset</label>
        <select
          style={{ ...SELECT_STYLE, minWidth: 140 }}
          defaultValue=""
          onChange={handleApplyPreset}
          disabled={applyingPreset}
        >
          <option value="">{applyingPreset ? "Applying..." : "-- apply --"}</option>
          {presets.map((p) => (
            <option key={p} value={p}>
              {p}
            </option>
          ))}
        </select>
        <button
          style={BTN_STYLE}
          onClick={() => setShowConfigurator(true)}
        >
          Custom
        </button>

        <div style={{ width: 1, height: 24, background: "#4a5568" }} />

        {/* Simulation controls */}
        {!simulationRunning ? (
          <button style={BTN_PRIMARY} onClick={handleStart}>
            Start
          </button>
        ) : (
          <button style={BTN_STYLE} onClick={handlePause}>
            Pause
          </button>
        )}
        {!simulationRunning && (
          <button style={BTN_STYLE} onClick={handleResume}>
            Resume
          </button>
        )}
        <button style={BTN_STYLE} onClick={handleStep}>
          Step
        </button>
        <button
          style={{ ...BTN_STYLE, borderColor: "#ef4444", color: "#ef4444" }}
          onClick={handleReset}
        >
          Reset
        </button>

        <div style={{ width: 1, height: 24, background: "#4a5568" }} />

        {/* Speed slider */}
        <label style={LABEL_STYLE}>Speed {simulationSpeed.toFixed(1)}x</label>
        <input
          type="range"
          min="0.5"
          max="10"
          step="0.5"
          value={simulationSpeed}
          onChange={handleSpeedChange}
          style={{ width: 100, accentColor: "#3b82f6" }}
        />

        <div style={{ width: 1, height: 24, background: "#4a5568" }} />

        {/* Map toggles */}
        <label style={{ ...LABEL_STYLE, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={showPaths}
            onChange={togglePaths}
            style={{ marginRight: 4 }}
          />
          Paths
        </label>
        <label style={{ ...LABEL_STYLE, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={showHeatmap}
            onChange={toggleHeatmap}
            style={{ marginRight: 4 }}
          />
          Heatmap
        </label>

        <div style={{ flex: 1 }} />

        {!editorMode && (
          <button
            style={BTN_STYLE}
            onClick={() => setEditorMode(true)}
          >
            Edit Map
          </button>
        )}
      </div>

      {/* ---- Editor toolbar ---- */}
      {editorMode && <EditorToolbar />}

      {/* ---- Body ---- */}
      <div style={BODY_STYLE}>
        <div style={MAP_STYLE}>
          <WarehouseMap />
          <MapLegend />
        </div>

        {/* ---- Right panel ---- */}
        {(interactiveMode || selectedRobot) && (
          <div style={PANEL_STYLE}>
            {/* Selected robot details (collapsible top section) */}
            {selectedRobot && (
              <>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    marginBottom: 8,
                  }}
                >
                  <h3 style={{ margin: 0, fontSize: 13, color: "#94a3b8" }}>
                    {selectedRobot.name ?? selectedRobotId!.slice(0, 8)}
                  </h3>
                  <button
                    style={{
                      ...BTN_STYLE,
                      padding: "2px 8px",
                      fontSize: 11,
                    }}
                    onClick={() => selectRobot(null)}
                  >
                    Close
                  </button>
                </div>
                <InfoRow
                  label="Status"
                  value={
                    <span
                      style={{
                        color: STATUS_COLORS[selectedRobot.status],
                        fontWeight: 600,
                      }}
                    >
                      {selectedRobot.status}
                    </span>
                  }
                />
                <InfoRow
                  label="Position"
                  value={`(${selectedRobot.row}, ${selectedRobot.col})`}
                />
                <InfoRow
                  label="Path"
                  value={
                    selectedRobot.path
                      ? `${selectedRobot.path.length} cells`
                      : "none"
                  }
                />
                <div style={{ height: 1, background: "#2d3148", margin: "12px 0" }} />
              </>
            )}

            {/* Interactive mode: Tabbed panel — Orders | Station */}
            {interactiveMode && <InteractivePanel />}
          </div>
        )}
      </div>

      {/* Station navigator overlay */}
      <StationNavigator
        stations={stations}
        pickTasks={pickTasks}
        onOpenStation={(id) => setOperatorStationId(id)}
      />

      {/* Station operator fullscreen view */}
      {operatorStationId && (
        <StationOperatorView
          stationId={operatorStationId}
          stationName={
            stations.find((s) => s.id === operatorStationId)?.name
            ?? `Station ${operatorStationId.slice(0, 8)}`
          }
          onClose={() => setOperatorStationId(null)}
        />
      )}

      {/* Preset configurator modal */}
      {showConfigurator && (
        <PresetConfigurator
          onClose={() => setShowConfigurator(false)}
          onApplied={async () => {
            setShowConfigurator(false);
            await refreshAfterPreset();
          }}
        />
      )}
    </div>
  );
}

// ------------------------------------------------------------------ InteractivePanel

function InteractivePanel() {
  const [tab, setTab] = useState<"orders" | "station">("orders");

  const tabStyle = (active: boolean): React.CSSProperties => ({
    flex: 1,
    padding: "6px 0",
    border: "none",
    borderBottom: active ? "2px solid #3b82f6" : "2px solid transparent",
    background: "transparent",
    color: active ? "#e2e8f0" : "#64748b",
    cursor: "pointer",
    fontSize: 13,
    fontWeight: active ? 600 : 400,
  });

  return (
    <div>
      {/* Tab bar */}
      <div style={{ display: "flex", marginBottom: 12, borderBottom: "1px solid #2d3148" }}>
        <button style={tabStyle(tab === "orders")} onClick={() => setTab("orders")}>
          Orders
        </button>
        <button style={tabStyle(tab === "station")} onClick={() => setTab("station")}>
          Station
        </button>
      </div>

      {tab === "orders" && <OrderCreatePanel />}
      {tab === "station" && <StationWorkflow />}
    </div>
  );
}

// ------------------------------------------------------------------ StationNavigator

function StationNavigator({
  stations,
  pickTasks,
  onOpenStation,
}: {
  stations: import("@/types/station").Station[];
  pickTasks: import("@/types/pickTask").PickTask[];
  onOpenStation: (stationId: string) => void;
}) {
  const [collapsed, setCollapsed] = useState(false);

  if (stations.length === 0) return null;

  // Count active tasks per station
  const taskCounts: Record<string, { active: number; pending: number }> = {};
  for (const task of pickTasks) {
    if (!task.station_id) continue;
    if (!taskCounts[task.station_id]) {
      taskCounts[task.station_id] = { active: 0, pending: 0 };
    }
    const counts = taskCounts[task.station_id]!;
    if (task.state === "SOURCE_AT_STATION" || task.state === "PICKING") {
      counts.active++;
    } else if (task.state === "SOURCE_REQUESTED" || task.state === "SOURCE_AT_CANTILEVER") {
      counts.pending++;
    }
  }

  return (
    <div
      style={{
        position: "absolute",
        bottom: 16,
        right: 16,
        zIndex: 100,
        background: "#1a1d27ee",
        border: "1px solid #2d3148",
        borderRadius: 8,
        minWidth: 180,
        backdropFilter: "blur(8px)",
      }}
    >
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "8px 12px",
          borderBottom: collapsed ? "none" : "1px solid #2d3148",
          cursor: "pointer",
        }}
        onClick={() => setCollapsed(!collapsed)}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: "#94a3b8" }}>
          Stations ({stations.length})
        </span>
        <span style={{ fontSize: 10, color: "#64748b" }}>
          {collapsed ? "\u25B2" : "\u25BC"}
        </span>
      </div>

      {/* Station list */}
      {!collapsed && (
        <div style={{ padding: "4px 8px 8px" }}>
          {stations.map((station) => {
            const counts = taskCounts[station.id];
            const activeCount = counts?.active ?? 0;
            const pendingCount = counts?.pending ?? 0;

            return (
              <button
                key={station.id}
                onClick={() => onOpenStation(station.id)}
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  width: "100%",
                  padding: "6px 8px",
                  marginBottom: 2,
                  border: "1px solid transparent",
                  borderRadius: 6,
                  background: "transparent",
                  color: "#e2e8f0",
                  cursor: "pointer",
                  fontSize: 12,
                  textAlign: "left",
                  transition: "background 0.15s, border-color 0.15s",
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.background = "#2d314880";
                  e.currentTarget.style.borderColor = "#3b82f644";
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.background = "transparent";
                  e.currentTarget.style.borderColor = "transparent";
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  {/* Online indicator dot */}
                  <span
                    style={{
                      display: "inline-block",
                      width: 6,
                      height: 6,
                      borderRadius: 3,
                      background: station.is_online ? "#22c55e" : "#6b7280",
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ fontWeight: 500 }}>{station.name}</span>
                </div>

                {/* Task badges */}
                <div style={{ display: "flex", gap: 4 }}>
                  {activeCount > 0 && (
                    <span
                      style={{
                        padding: "1px 5px",
                        borderRadius: 8,
                        fontSize: 10,
                        fontWeight: 600,
                        background: "#22c55e",
                        color: "#fff",
                      }}
                    >
                      {activeCount}
                    </span>
                  )}
                  {pendingCount > 0 && (
                    <span
                      style={{
                        padding: "1px 5px",
                        borderRadius: 8,
                        fontSize: 10,
                        fontWeight: 600,
                        background: "#eab308",
                        color: "#fff",
                      }}
                    >
                      {pendingCount}
                    </span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ helpers

function InfoRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        padding: "6px 0",
        borderBottom: "1px solid #2d3148",
        fontSize: 13,
      }}
    >
      <span style={{ color: "#94a3b8" }}>{label}</span>
      <span>{value}</span>
    </div>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { WarehouseMap } from "./components/map/WarehouseMap";
import { MapLegend } from "./components/map/MapLegend";
import { OrderCreatePanel } from "./components/OrderCreatePanel";
import { StationWorkflow } from "./components/StationWorkflow";
import { EditorToolbar } from "./components/map/EditorToolbar";
import { AllocationStatsPanel } from "./components/AllocationStatsPanel";
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
  position: "absolute",
  right: 0,
  top: 0,
  bottom: 0,
  width: 340,
  background: "#1a1d27",
  borderLeft: "1px solid #2d3148",
  padding: 16,
  overflowY: "auto",
  zIndex: 20,
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
  const showAllocationStats = useUiStore((s) => s.showAllocationStats);
  const toggleAllocationStats = useUiStore((s) => s.toggleAllocationStats);
  const showToteOriginHeatmap = useUiStore((s) => s.showToteOriginHeatmap);
  const toggleToteOriginHeatmap = useUiStore((s) => s.toggleToteOriginHeatmap);
  const toteHeatmapMode = useUiStore((s) => s.toteHeatmapMode);
  const setToteHeatmapMode = useUiStore((s) => s.setToteHeatmapMode);
  const selectedRobotId = useUiStore((s) => s.selectedRobotId);
  const selectRobot = useUiStore((s) => s.selectRobot);
  const editorMode = useUiStore((s) => s.editorMode);
  const setEditorMode = useUiStore((s) => s.setEditorMode);

  const robots = useWarehouseStore((s) => s.robots);
  const resetWarehouse = useWarehouseStore((s) => s.resetAll);
  const selectedRobot = selectedRobotId ? robots[selectedRobotId] : null;

  const [layouts, setLayouts] = useState<Array<{ name: string; file: string; rows: number; cols: number }>>([]);
  const [selectedLayout, setSelectedLayout] = useState("");
  const [layoutLoading, setLayoutLoading] = useState(false);
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveLayoutName, setSaveLayoutName] = useState("");
  const [savingLayout, setSavingLayout] = useState(false);
  const [showNewDialog, setShowNewDialog] = useState(false);
  const [newRows, setNewRows] = useState(20);
  const [newCols, setNewCols] = useState(30);
  const [interactiveMode, setInteractiveMode] = useState(false);

  const stations = useWarehouseStore((s) => s.stations);
  const pickTasks = useWarehouseStore((s) => s.pickTasks);

  // Auto-select the first zone if none is active yet.
  useMemo(() => {
    if (!activeZoneId && zones && zones.length > 0) {
      setActiveZone(zones[0]!.id);
    }
  }, [activeZoneId, zones, setActiveZone]);

  // Fetch available layouts and config on mount.
  const refreshLayouts = useCallback(async () => {
    try {
      const result: any = await essApi.gridListLayouts();
      if (result?.layouts) setLayouts(result.layouts);
    } catch {
      // silently ignore
    }
  }, []);

  useEffect(() => {
    refreshLayouts();
    essApi.simulationConfig().then((cfg: any) => {
      if (cfg?.interactive_mode != null) setInteractiveMode(cfg.interactive_mode);
    });
  }, [refreshLayouts]);

  // -------------------------------------------------------- layout handlers

  const handleLoadLayout = useCallback(async () => {
    if (!selectedLayout) return;
    setLayoutLoading(true);
    try {
      await essApi.gridLoadInto(selectedLayout);

      // The load now does a full reconstruction (zone, stations, robots, etc.).
      // Reset zone selection so auto-select picks the new zone.
      setActiveZone(null);
      selectRobot(null);
      setSimulationRunning(false);

      await queryClient.invalidateQueries({ queryKey: ["zones"] });
      await queryClient.invalidateQueries({ queryKey: ["grid"] });

      // Refresh interactive_mode from config.
      const cfg: any = await essApi.simulationConfig();
      if (cfg?.interactive_mode != null) setInteractiveMode(cfg.interactive_mode);
    } catch (err) {
      alert("Failed to load layout: " + String(err));
    } finally {
      setLayoutLoading(false);
    }
  }, [selectedLayout, queryClient, setActiveZone, selectRobot, setSimulationRunning]);

  const handleSaveLayout = useCallback(async () => {
    const trimmed = saveLayoutName.trim();
    if (!trimmed) return;
    setSavingLayout(true);
    try {
      const activeZone = useUiStore.getState().activeZoneId;
      const gridState: any = await essApi.getGrid(activeZone || undefined);
      if (gridState) {
        await essApi.gridSave({
          name: trimmed,
          rows: gridState.rows,
          cols: gridState.cols,
          cells: gridState.cells,
        });
        setSaveLayoutName("");
        setShowSaveDialog(false);
        await refreshLayouts();
      }
    } catch (err) {
      alert("Failed to save layout: " + String(err));
    } finally {
      setSavingLayout(false);
    }
  }, [saveLayoutName, refreshLayouts]);

  const handleDeleteLayout = useCallback(async () => {
    if (!selectedLayout) return;
    const layoutInfo = layouts.find((l) => l.file === selectedLayout);
    const displayName = layoutInfo?.name ?? selectedLayout;
    if (!confirm(`Delete layout "${displayName}"?`)) return;
    try {
      await essApi.gridDeleteLayout(selectedLayout);
      setSelectedLayout("");
      await refreshLayouts();
    } catch (err) {
      alert("Failed to delete layout: " + String(err));
    }
  }, [selectedLayout, layouts, refreshLayouts]);

  const handleNewLayout = useCallback(async () => {
    if (newRows < 5 || newCols < 5) {
      alert("Minimum grid size is 5x5");
      return;
    }
    try {
      await essApi.gridResize(newRows, newCols);
      setShowNewDialog(false);
      setActiveZone(null);
      selectRobot(null);
      setSimulationRunning(false);
      resetWarehouse();
      await queryClient.invalidateQueries({ queryKey: ["zones"] });
      await queryClient.invalidateQueries({ queryKey: ["grid"] });
    } catch (err) {
      alert("Failed to create new layout: " + String(err));
    }
  }, [newRows, newCols, queryClient, setActiveZone, selectRobot, setSimulationRunning, resetWarehouse]);

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

        {/* Layout manager */}
        <label style={LABEL_STYLE}>Layout</label>
        <select
          style={{ ...SELECT_STYLE, minWidth: 140 }}
          value={selectedLayout}
          onChange={(e) => setSelectedLayout(e.target.value)}
        >
          <option value="">-- select --</option>
          {layouts.map((l) => (
            <option key={l.file} value={l.file}>
              {l.name} ({l.rows}x{l.cols})
            </option>
          ))}
        </select>
        <button
          style={BTN_STYLE}
          onClick={handleLoadLayout}
          disabled={!selectedLayout || layoutLoading}
        >
          {layoutLoading ? "Loading..." : "Load"}
        </button>
        <button
          style={{ ...BTN_STYLE, borderColor: "#ef4444", color: "#ef4444" }}
          onClick={handleDeleteLayout}
          disabled={!selectedLayout}
        >
          Del
        </button>
        <button
          style={BTN_STYLE}
          onClick={() => setShowSaveDialog(true)}
        >
          Save As...
        </button>
        <button
          style={{ ...BTN_STYLE, borderColor: "#22c55e", color: "#22c55e" }}
          onClick={() => setShowNewDialog(true)}
        >
          New
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
        <label style={{ ...LABEL_STYLE, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={showAllocationStats}
            onChange={toggleAllocationStats}
            style={{ marginRight: 4 }}
          />
          Allocation
        </label>
        <label style={{ ...LABEL_STYLE, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={showToteOriginHeatmap}
            onChange={toggleToteOriginHeatmap}
            style={{ marginRight: 4 }}
          />
          Tote Origins
        </label>
        {showToteOriginHeatmap && (
          <select
            value={toteHeatmapMode}
            onChange={(e) =>
              setToteHeatmapMode(
                e.target.value as "allocated" | "completed",
              )
            }
            style={{
              background: "#2d3148",
              color: "#e2e8f0",
              border: "1px solid #4a5568",
              borderRadius: 4,
              padding: "2px 6px",
              fontSize: 12,
            }}
          >
            <option value="allocated">Allocated</option>
            <option value="completed">Completed</option>
          </select>
        )}
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
          {showAllocationStats && <AllocationStatsPanel />}
        </div>

        {/* ---- Right panel ---- */}
        {(interactiveMode || selectedRobotId) && (
          <div style={PANEL_STYLE}>
            {/* Selected robot details panel */}
            {selectedRobotId && (
              <RobotInfoPanel
                robot={selectedRobot ?? null}
                robotId={selectedRobotId}
                pickTasks={pickTasks}
                stations={stations}
                onClose={() => selectRobot(null)}
              />
            )}

            {/* Queue Debug Panel */}
            <QueueDebugPanel stations={stations} robots={robots} />

            {/* Interactive mode: Tabbed panel — Orders | Station */}
            {interactiveMode && <InteractivePanel />}
          </div>
        )}
      </div>

      {/* New layout dialog */}
      {showNewDialog && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
          }}
          onClick={() => setShowNewDialog(false)}
        >
          <div
            style={{
              background: "#1a1d27",
              border: "1px solid #2d3148",
              borderRadius: 10,
              padding: 24,
              width: 320,
              color: "#e2e8f0",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ margin: "0 0 16px", fontSize: 16, fontWeight: 700 }}>
              New Layout
            </h3>
            <div style={{ display: "flex", gap: 12, marginBottom: 16 }}>
              <label style={{ flex: 1 }}>
                <span style={{ fontSize: 12, color: "#94a3b8", display: "block", marginBottom: 4 }}>
                  Rows
                </span>
                <input
                  type="number"
                  min={5}
                  max={100}
                  value={newRows}
                  onChange={(e) => setNewRows(parseInt(e.target.value) || 5)}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    border: "1px solid #4a5568",
                    borderRadius: 4,
                    background: "#232738",
                    color: "#e2e8f0",
                    fontSize: 14,
                    boxSizing: "border-box",
                  }}
                />
              </label>
              <label style={{ flex: 1 }}>
                <span style={{ fontSize: 12, color: "#94a3b8", display: "block", marginBottom: 4 }}>
                  Cols
                </span>
                <input
                  type="number"
                  min={5}
                  max={100}
                  value={newCols}
                  onChange={(e) => setNewCols(parseInt(e.target.value) || 5)}
                  style={{
                    width: "100%",
                    padding: "8px 12px",
                    border: "1px solid #4a5568",
                    borderRadius: 4,
                    background: "#232738",
                    color: "#e2e8f0",
                    fontSize: 14,
                    boxSizing: "border-box",
                  }}
                />
              </label>
            </div>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button style={BTN_STYLE} onClick={() => setShowNewDialog(false)}>
                Cancel
              </button>
              <button
                style={{ ...BTN_PRIMARY, background: "#22c55e", borderColor: "#22c55e" }}
                onClick={handleNewLayout}
              >
                Create
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Save layout dialog */}
      {showSaveDialog && (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(0,0,0,0.6)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 100,
          }}
          onClick={() => setShowSaveDialog(false)}
        >
          <div
            style={{
              background: "#1a1d27",
              border: "1px solid #2d3148",
              borderRadius: 10,
              padding: 24,
              width: 360,
              color: "#e2e8f0",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 style={{ margin: "0 0 16px", fontSize: 16, fontWeight: 700 }}>
              Save Layout
            </h3>
            <input
              type="text"
              placeholder="Layout name"
              value={saveLayoutName}
              onChange={(e) => setSaveLayoutName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSaveLayout()}
              style={{
                width: "100%",
                padding: "8px 12px",
                border: "1px solid #4a5568",
                borderRadius: 4,
                background: "#232738",
                color: "#e2e8f0",
                fontSize: 14,
                boxSizing: "border-box",
                marginBottom: 16,
              }}
              autoFocus
            />
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
              <button style={BTN_STYLE} onClick={() => setShowSaveDialog(false)}>
                Cancel
              </button>
              <button
                style={BTN_PRIMARY}
                onClick={handleSaveLayout}
                disabled={!saveLayoutName.trim() || savingLayout}
              >
                {savingLayout ? "Saving..." : "Save"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ QueueDebugPanel

function QueueDebugPanel({
  stations,
  robots,
}: {
  stations: import("@/types/station").Station[];
  robots: Record<string, import("@/types/robot").RobotRealtime>;
}) {
  // Helper: resolve robot_id to short name
  const robotName = (rid: string | null) => {
    if (!rid) return "\u2014";
    const r = robots[rid];
    return r?.name ?? rid.slice(0, 8);
  };

  // Helper: detect approach ghost (only for IDLE robots far from approach)
  const isApproachGhost = (station: import("@/types/station").Station) => {
    const qs = station.queue_state;
    if (!qs?.approach) return false;
    const r = robots[qs.approach];
    if (!r) return true; // robot not found = ghost
    const aRow = station.approach_cell_row ?? station.grid_row;
    const aCol = station.approach_cell_col ?? station.grid_col;
    const dist = Math.abs(r.row - aRow) + Math.abs(r.col - aCol);
    // Only flag as ghost if IDLE and far — WAITING/MOVING robots may be
    // recently promoted and en route to approach cell.
    return dist > 2 && r.status === "IDLE";
  };

  const stationsWithQueue = stations.filter((s) => s.queue_state);
  if (stationsWithQueue.length === 0) return null;

  return (
    <>
      <div
        style={{
          fontSize: 11,
          color: "#f59e0b",
          textTransform: "uppercase",
          letterSpacing: 1,
          marginBottom: 8,
        }}
      >
        Queue Debug
      </div>
      {stationsWithQueue.map((s) => {
        const qs = s.queue_state!;
        const ghost = isApproachGhost(s);
        return (
          <div
            key={s.id}
            style={{
              background: "#141720",
              borderRadius: 6,
              padding: 10,
              marginBottom: 8,
              border: ghost ? "1px solid #ef4444" : "1px solid #334155",
            }}
          >
            <div
              style={{
                fontSize: 12,
                fontWeight: 600,
                color: "#e2e8f0",
                marginBottom: 6,
              }}
            >
              {s.name}
            </div>
            <InfoRow label="Station" value={robotName(qs.station)} />
            <InfoRow
              label="Approach"
              value={
                <span>
                  {robotName(qs.approach)}
                  {ghost && (
                    <span
                      style={{
                        marginLeft: 6,
                        color: "#ef4444",
                        fontWeight: 700,
                        fontSize: 10,
                        padding: "1px 4px",
                        borderRadius: 3,
                        background: "rgba(239,68,68,0.15)",
                      }}
                    >
                      GHOST
                    </span>
                  )}
                </span>
              }
            />
            {qs.queue.map((rid, i) => (
              <InfoRow key={i} label={`Q${i + 1}`} value={robotName(rid)} />
            ))}
            {qs.holding !== undefined && (
              <InfoRow label="Holding" value={robotName(qs.holding)} />
            )}
            {qs._version_tick != null && (
              <InfoRow label="Tick" value={String(qs._version_tick)} />
            )}
            {qs._mutation_reason && (
              <InfoRow
                label="Reason"
                value={
                  <span style={{ fontSize: 10, color: "#94a3b8" }}>
                    {qs._mutation_reason}
                  </span>
                }
              />
            )}
          </div>
        );
      })}
      <div style={{ height: 1, background: "#2d3148", margin: "12px 0" }} />
    </>
  );
}

// ------------------------------------------------------------------ RobotInfoPanel

const HEADING_LABELS: Record<number, string> = {
  0: "N", 90: "E", 180: "S", 270: "W",
};

const TYPE_COLORS: Record<string, string> = {
  K50H: "#22c55e",
  A42TD: "#3b82f6",
};

function RobotInfoPanel({
  robot,
  robotId,
  pickTasks,
  stations,
  onClose,
}: {
  robot: import("@/types/robot").RobotRealtime | null;
  robotId: string;
  pickTasks: import("@/types/pickTask").PickTask[];
  stations: import("@/types/station").Station[];
  onClose: () => void;
}) {
  if (!robot) {
    return (
      <>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
          <h3 style={{ margin: 0, fontSize: 14, color: "#94a3b8" }}>{robotId.slice(0, 8)}</h3>
          <button style={{ ...BTN_STYLE, padding: "2px 8px", fontSize: 11 }} onClick={onClose}>Close</button>
        </div>
        <p style={{ fontSize: 12, color: "#64748b" }}>Loading robot data...</p>
        <div style={{ height: 1, background: "#2d3148", margin: "12px 0" }} />
      </>
    );
  }

  // Find linked pick task
  const linkedTask = robot.hold_pick_task_id
    ? pickTasks.find((t) => t.id === robot.hold_pick_task_id)
    : null;

  // Find reserved station (from linked task or reservation)
  const reservedStation = linkedTask
    ? stations.find((s) => s.id === linkedTask.station_id)
    : null;

  const headingLabel = HEADING_LABELS[robot.heading] ?? `${robot.heading}°`;

  return (
    <>
      {/* Header */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            style={{
              display: "inline-block",
              width: 10,
              height: 10,
              borderRadius: "50%",
              background: TYPE_COLORS[robot.type ?? ""] ?? "#9ca3af",
            }}
          />
          <h3 style={{ margin: 0, fontSize: 14, color: "#e2e8f0" }}>
            {robot.name ?? robotId.slice(0, 8)}
          </h3>
          <span
            style={{
              fontSize: 11,
              padding: "1px 6px",
              borderRadius: 3,
              background: "#2d3148",
              color: "#94a3b8",
            }}
          >
            {robot.type ?? "Unknown"}
          </span>
        </div>
        <button
          style={{ ...BTN_STYLE, padding: "2px 8px", fontSize: 11 }}
          onClick={onClose}
        >
          Close
        </button>
      </div>

      {/* Status & Position */}
      <div
        style={{
          background: "#141720",
          borderRadius: 6,
          padding: 10,
          marginBottom: 10,
        }}
      >
        <InfoRow
          label="Status"
          value={
            <span
              style={{
                color: STATUS_COLORS[robot.status],
                fontWeight: 600,
              }}
            >
              {robot.status}
            </span>
          }
        />
        <InfoRow label="Position" value={`(${robot.row}, ${robot.col})`} />
        <InfoRow label="Heading" value={headingLabel} />
        <InfoRow
          label="Path"
          value={
            robot.path ? `${robot.path.length} cells remaining` : "No path"
          }
        />
      </div>

      {/* Diagnostics */}
      {(() => {
        let diagLabel = "OK";
        let diagColor = "#22c55e";
        const hasPath = robot.path && robot.path.length > 0;
        const wt = robot.wait_ticks ?? 0;
        if (robot.status === "WAITING" && !hasPath) {
          diagLabel = "Stuck: no path";
          diagColor = "#ef4444";
        } else if (robot.status === "WAITING" && wt > 0) {
          const reason = robot.blocked_reason ?? "UNKNOWN";
          const by = robot.blocked_by ?? "?";
          const age = robot.blocked_age ?? 0;
          diagLabel = `Blocked by ${by} (${reason}, ${age}t)`;
          diagColor = "#ef4444";
        } else if (robot.status === "WAITING_FOR_STATION") {
          diagLabel = "At station: awaiting scan";
          diagColor = "#a855f7";
        } else if (robot.status === "IDLE" && !robot.task_type) {
          diagLabel = "Idle: awaiting dispatch";
          diagColor = "#f59e0b";
        } else if (robot.status === "IDLE") {
          diagLabel = "Idle: has task";
          diagColor = "#f59e0b";
        }
        return (
          <div
            style={{
              background: "#141720",
              borderRadius: 6,
              padding: 10,
              marginBottom: 10,
            }}
          >
            <div
              style={{
                fontSize: 11,
                color: "#64748b",
                textTransform: "uppercase",
                letterSpacing: 1,
                marginBottom: 6,
              }}
            >
              Diagnostics
            </div>
            <InfoRow
              label="State"
              value={
                <span style={{ color: diagColor, fontWeight: 600 }}>
                  {diagLabel}
                </span>
              }
            />
            {wt > 0 && <InfoRow label="Wait Ticks" value={String(wt)} />}
          </div>
        );
      })()}

      {/* Target */}
      {robot.target_row != null && (
        <div
          style={{
            background: "#141720",
            borderRadius: 6,
            padding: 10,
            marginBottom: 10,
          }}
        >
          <div
            style={{
              fontSize: 11,
              color: "#64748b",
              textTransform: "uppercase",
              letterSpacing: 1,
              marginBottom: 6,
            }}
          >
            Target
          </div>
          <InfoRow
            label="Target Cell"
            value={`(${robot.target_row}, ${robot.target_col})`}
          />
          {robot.target_station && (
            <InfoRow label="Station" value={robot.target_station} />
          )}
        </div>
      )}

      {/* Task Info */}
      <div
        style={{
          background: "#141720",
          borderRadius: 6,
          padding: 10,
          marginBottom: 10,
        }}
      >
        <div
          style={{
            fontSize: 11,
            color: "#64748b",
            textTransform: "uppercase",
            letterSpacing: 1,
            marginBottom: 6,
          }}
        >
          Task
        </div>
        <InfoRow
          label="Task Type"
          value={
            robot.task_type ? (
              <span
                style={{
                  color: robot.task_type === "RETRIEVE" ? "#f59e0b" : "#8b5cf6",
                  fontWeight: 600,
                }}
              >
                {robot.task_type === "RETRIEVE" ? "RETRIEVE" : "RETURN"}
              </span>
            ) : (
              <span style={{ color: "#64748b" }}>None</span>
            )
          }
        />
        <InfoRow
          label="Carrying Tote"
          value={
            robot.hold_pick_task_id ? (
              <span style={{ color: "#f59e0b", fontWeight: 600 }}>Yes</span>
            ) : (
              <span style={{ color: "#64748b" }}>No</span>
            )
          }
        />
        <InfoRow
          label="At Station"
          value={
            robot.hold_at_station ? (
              <span style={{ color: "#22c55e" }}>Yes</span>
            ) : (
              <span style={{ color: "#64748b" }}>No</span>
            )
          }
        />
      </div>

      {/* Linked Pick Task */}
      {linkedTask && (
        <div
          style={{
            background: "#141720",
            borderRadius: 6,
            padding: 10,
            marginBottom: 10,
          }}
        >
          <div
            style={{
              fontSize: 11,
              color: "#64748b",
              textTransform: "uppercase",
              letterSpacing: 1,
              marginBottom: 6,
            }}
          >
            Pick Task
          </div>
          <InfoRow label="SKU" value={linkedTask.sku} />
          <InfoRow
            label="State"
            value={
              <span style={{ color: "#3b82f6", fontWeight: 600 }}>
                {linkedTask.state}
              </span>
            }
          />
          <InfoRow
            label="Qty"
            value={`${linkedTask.qty_picked} / ${linkedTask.qty_to_pick}`}
          />
          {linkedTask.source_tote_id && (
            <InfoRow
              label="Source Tote"
              value={linkedTask.source_tote_id.slice(0, 8)}
            />
          )}
          {linkedTask.target_tote_barcode && (
            <InfoRow label="Target Tote" value={linkedTask.target_tote_barcode} />
          )}
        </div>
      )}

      {/* Linked Station */}
      {reservedStation && (
        <div
          style={{
            background: "#141720",
            borderRadius: 6,
            padding: 10,
            marginBottom: 10,
          }}
        >
          <div
            style={{
              fontSize: 11,
              color: "#64748b",
              textTransform: "uppercase",
              letterSpacing: 1,
              marginBottom: 6,
            }}
          >
            Destination Station
          </div>
          <InfoRow label="Station" value={reservedStation.name} />
          <InfoRow
            label="Position"
            value={`(${reservedStation.grid_row}, ${reservedStation.grid_col})`}
          />
        </div>
      )}

      <div style={{ height: 1, background: "#2d3148", margin: "12px 0" }} />
    </>
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

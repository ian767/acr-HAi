import { useCallback, useMemo } from "react";
import { WarehouseMap } from "./components/map/WarehouseMap";
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
  width: 300,
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
  DOCKING: "#a855f7",
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

  const robots = useWarehouseStore((s) => s.robots);
  const selectedRobot = selectedRobotId ? robots[selectedRobotId] : null;

  // Auto-select the first zone if none is active yet.
  useMemo(() => {
    if (!activeZoneId && zones && zones.length > 0) {
      setActiveZone(zones[0]!.id);
    }
  }, [activeZoneId, zones, setActiveZone]);

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
  }, [setSimulationRunning]);

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
      </div>

      {/* ---- Body ---- */}
      <div style={BODY_STYLE}>
        <div style={MAP_STYLE}>
          <WarehouseMap />
        </div>

        {/* ---- Robot info panel ---- */}
        {selectedRobot && (
          <div style={PANEL_STYLE}>
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 16,
              }}
            >
              <h3 style={{ margin: 0, fontSize: 16 }}>Robot Details</h3>
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

            <InfoRow label="ID" value={selectedRobotId!} />
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
            <InfoRow label="Heading" value={`${selectedRobot.heading}\u00b0`} />
            <InfoRow
              label="Path length"
              value={
                selectedRobot.path
                  ? `${selectedRobot.path.length} cells`
                  : "none"
              }
            />
          </div>
        )}
      </div>
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

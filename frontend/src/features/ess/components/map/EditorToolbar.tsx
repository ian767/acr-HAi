import { useState, useCallback, useEffect } from "react";
import { useUiStore } from "@/stores/useUiStore";
import { useWarehouseStore } from "@/stores/useWarehouseStore";
import { wesApi } from "@/api/wes";
import { essApi } from "@/api/ess";
import type { Station } from "@/types/station";

// ------------------------------------------------------------------ constants

const CELL_TYPES = [
  { type: "FLOOR", label: "Erase", color: "#1a1d27" },
  { type: "WALL", label: "Wall", color: "#111111" },
  { type: "RACK", label: "Rack", color: "#4a5568" },
  { type: "STATION", label: "Station", color: "#3b82f6" },
  { type: "AISLE", label: "Aisle", color: "#2d3148" },
  { type: "CHARGING", label: "Charging", color: "#22c55e" },
  { type: "IDLE_POINT", label: "Idle Pt", color: "#f59e0b" },
];

// ------------------------------------------------------------------ styles

const BAR: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "6px 12px",
  background: "#232738",
  borderBottom: "1px solid #2d3148",
  flexShrink: 0,
  flexWrap: "wrap",
  fontSize: 12,
};

const TOOL_BTN: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
  padding: "3px 8px",
  border: "1px solid #4a5568",
  borderRadius: 4,
  background: "#2d3148",
  color: "#e2e8f0",
  cursor: "pointer",
  fontSize: 11,
};

const TOOL_ACTIVE: React.CSSProperties = {
  ...TOOL_BTN,
  borderColor: "#3b82f6",
  background: "#3b82f622",
};

const BTN: React.CSSProperties = {
  padding: "3px 10px",
  border: "1px solid #4a5568",
  borderRadius: 4,
  background: "#2d3148",
  color: "#e2e8f0",
  cursor: "pointer",
  fontSize: 11,
};

// ------------------------------------------------------------------ component

export function EditorToolbar() {
  const editorTool = useUiStore((s) => s.editorTool);
  const setEditorTool = useUiStore((s) => s.setEditorTool);
  const setEditorMode = useUiStore((s) => s.setEditorMode);
  const stations = useWarehouseStore((s) => s.stations);

  // Queue editor state
  const [queueEditStation, setQueueEditStation] = useState<Station | null>(null);
  const [queueCells, setQueueCells] = useState<Array<{ role: string; row: number; col: number }>>([]);
  const [savingQueue, setSavingQueue] = useState(false);

  // When station is selected for queue editing, load its current queue config
  const handleSelectQueueStation = useCallback((stationId: string) => {
    const station = stations.find((s) => s.id === stationId);
    if (!station) return;
    setQueueEditStation(station);

    // Load existing queue config
    const existing: Array<{ role: string; row: number; col: number }> = [];
    if (station.approach_cell_row != null && station.approach_cell_col != null) {
      existing.push({ role: "Approach", row: station.approach_cell_row, col: station.approach_cell_col });
    }
    if (station.queue_cells) {
      for (const qc of [...station.queue_cells].sort((a, b) => a.position - b.position)) {
        existing.push({ role: `Q${qc.position + 1}`, row: qc.row, col: qc.col });
      }
    }
    setQueueCells(existing);
  }, [stations]);

  const handleQueueCellClick = useCallback((row: number, col: number) => {
    if (!queueEditStation) return;
    // Don't allow clicking the station cell itself
    if (row === queueEditStation.grid_row && col === queueEditStation.grid_col) return;
    // Don't allow duplicate cells
    if (queueCells.some((c) => c.row === row && c.col === col)) return;

    setQueueCells((prev) => {
      const count = prev.length;
      // First cell = Approach, rest = Q1, Q2, Q3, ...
      let role: string;
      if (count === 0) role = "Approach";
      else role = `Q${count}`;
      return [...prev, { role, row, col }];
    });
  }, [queueEditStation, queueCells]);

  const handleRemoveQueueCell = useCallback((index: number) => {
    setQueueCells((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleSaveQueue = useCallback(async () => {
    if (!queueEditStation || queueCells.length === 0) return;
    setSavingQueue(true);
    try {
      // First cell = approach, remaining = queue (no holding)
      let approach_cell = null;
      const holding_cell = null;
      let queue_cells_data: Array<{ position: number; row: number; col: number }> = [];

      if (queueCells.length >= 1) {
        approach_cell = { position: 0, row: queueCells[0]!.row, col: queueCells[0]!.col };
        queue_cells_data = queueCells.slice(1).map((c, i) => ({
          position: i,
          row: c.row,
          col: c.col,
        }));
      }

      await wesApi.updateStationQueueConfig(queueEditStation.id, {
        approach_cell,
        holding_cell,
        queue_cells: queue_cells_data,
      });
      alert(`Queue saved for ${queueEditStation.name}`);
    } catch (err) {
      alert("Failed to save queue config: " + String(err));
    } finally {
      setSavingQueue(false);
    }
  }, [queueEditStation, queueCells]);

  const handleClearQueue = useCallback(async () => {
    if (!queueEditStation) return;
    setSavingQueue(true);
    try {
      await wesApi.updateStationQueueConfig(queueEditStation.id, {
        approach_cell: null,
        holding_cell: null,
        queue_cells: [],
      });
      setQueueCells([]);
      alert(`Queue cleared for ${queueEditStation.name}`);
    } catch (err) {
      alert("Failed to clear queue config: " + String(err));
    } finally {
      setSavingQueue(false);
    }
  }, [queueEditStation]);

  // ---- Territory editor state (grid rectangle: 2 clicks = corners) ----
  const robots = useWarehouseStore((s) => s.robots);
  const a42tdRobots = Object.values(robots).filter((r) => r.type === "A42TD");
  const [territoryRobotId, setTerritoryRobotId] = useState<string>("");
  const [territoryColMin, setTerritoryColMin] = useState<number | null>(null);
  const [territoryColMax, setTerritoryColMax] = useState<number | null>(null);
  const [territoryRowMin, setTerritoryRowMin] = useState<number | null>(null);
  const [territoryRowMax, setTerritoryRowMax] = useState<number | null>(null);
  const [territoryClickCount, setTerritoryClickCount] = useState(0);
  const [savingTerritory, setSavingTerritory] = useState(false);

  const handleSelectTerritoryRobot = useCallback((robotId: string) => {
    setTerritoryRobotId(robotId);
    const r = robots[robotId];
    if (r) {
      setTerritoryColMin(r.territory_col_min ?? null);
      setTerritoryColMax(r.territory_col_max ?? null);
      setTerritoryRowMin(r.territory_row_min ?? null);
      setTerritoryRowMax(r.territory_row_max ?? null);
      setTerritoryClickCount(r.territory_col_min != null ? 2 : 0);
    } else {
      setTerritoryColMin(null);
      setTerritoryColMax(null);
      setTerritoryRowMin(null);
      setTerritoryRowMax(null);
      setTerritoryClickCount(0);
    }
  }, [robots]);

  const handleTerritoryClick = useCallback((row: number, col: number) => {
    if (!territoryRobotId) return;
    if (territoryClickCount === 0 || territoryClickCount >= 2) {
      // First corner (or reset)
      setTerritoryColMin(col);
      setTerritoryColMax(col);
      setTerritoryRowMin(row);
      setTerritoryRowMax(row);
      setTerritoryClickCount(1);
    } else {
      // Second corner — define rectangle
      setTerritoryColMin(Math.min(territoryColMin ?? col, col));
      setTerritoryColMax(Math.max(territoryColMin ?? col, col));
      setTerritoryRowMin(Math.min(territoryRowMin ?? row, row));
      setTerritoryRowMax(Math.max(territoryRowMin ?? row, row));
      setTerritoryClickCount(2);
    }
  }, [territoryRobotId, territoryClickCount, territoryColMin, territoryRowMin]);

  const handleSaveTerritory = useCallback(async () => {
    if (!territoryRobotId) return;
    setSavingTerritory(true);
    try {
      await essApi.updateRobotTerritory(territoryRobotId, {
        col_min: territoryColMin,
        col_max: territoryColMax,
        row_min: territoryRowMin,
        row_max: territoryRowMax,
      });
    } catch (err) {
      alert("Failed to save territory: " + String(err));
    } finally {
      setSavingTerritory(false);
    }
  }, [territoryRobotId, territoryColMin, territoryColMax, territoryRowMin, territoryRowMax]);

  const handleClearTerritory = useCallback(async () => {
    if (!territoryRobotId) return;
    setSavingTerritory(true);
    try {
      await essApi.updateRobotTerritory(territoryRobotId, {
        col_min: null, col_max: null, row_min: null, row_max: null,
      });
      setTerritoryColMin(null);
      setTerritoryColMax(null);
      setTerritoryRowMin(null);
      setTerritoryRowMax(null);
      setTerritoryClickCount(0);
    } catch (err) {
      alert("Failed to clear territory: " + String(err));
    } finally {
      setSavingTerritory(false);
    }
  }, [territoryRobotId]);

  // Expose queue cell click handler and pending cells to the map via UiStore
  useEffect(() => {
    const state = useUiStore.getState() as any;
    if (editorTool === "QUEUE" && queueEditStation) {
      state._queueCellClick = handleQueueCellClick;
      state._pendingQueueCells = queueCells;
      state._queueEditStationId = queueEditStation.id;
    } else {
      state._queueCellClick = null;
      state._pendingQueueCells = null;
      state._queueEditStationId = null;
    }

    return () => {
      const s = useUiStore.getState() as any;
      s._queueCellClick = null;
      s._pendingQueueCells = null;
      s._queueEditStationId = null;
    };
  }, [editorTool, queueEditStation, handleQueueCellClick, queueCells]);

  // Expose territory click handler to the map
  useEffect(() => {
    const state = useUiStore.getState() as any;
    if (editorTool === "TERRITORY" && territoryRobotId) {
      state._territoryCellClick = handleTerritoryClick;
      state._territoryColMin = territoryColMin;
      state._territoryColMax = territoryColMax;
      state._territoryRowMin = territoryRowMin;
      state._territoryRowMax = territoryRowMax;
      state._territoryRobotId = territoryRobotId;
    } else {
      state._territoryCellClick = null;
      state._territoryColMin = null;
      state._territoryColMax = null;
      state._territoryRowMin = null;
      state._territoryRowMax = null;
      state._territoryRobotId = null;
    }
    return () => {
      const s = useUiStore.getState() as any;
      s._territoryCellClick = null;
      s._territoryColMin = null;
      s._territoryColMax = null;
      s._territoryRowMin = null;
      s._territoryRowMax = null;
      s._territoryRobotId = null;
    };
  }, [editorTool, territoryRobotId, handleTerritoryClick, territoryColMin, territoryColMax, territoryRowMin, territoryRowMax]);

  return (
    <div style={BAR}>
      <span style={{ fontWeight: 600, color: "#94a3b8", marginRight: 4 }}>
        EDITOR
      </span>

      {/* Cell type palette */}
      {CELL_TYPES.map(({ type, label, color }) => (
        <button
          key={type}
          style={editorTool === type ? TOOL_ACTIVE : TOOL_BTN}
          onClick={() => setEditorTool(type)}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: 2,
              background: color,
              border: "1px solid #64748b",
              flexShrink: 0,
            }}
          />
          {label}
        </button>
      ))}

      <div style={{ width: 1, height: 20, background: "#4a5568" }} />

      {/* Queue editor tool */}
      <button
        style={editorTool === "QUEUE" ? TOOL_ACTIVE : TOOL_BTN}
        onClick={() => setEditorTool("QUEUE")}
      >
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: 2,
            background: "#eab308",
            border: "1px solid #64748b",
            flexShrink: 0,
          }}
        />
        Queue
      </button>

      {/* Queue editor panel */}
      {editorTool === "QUEUE" && (
        <>
          <div style={{ width: 1, height: 20, background: "#4a5568" }} />
          <select
            style={{
              padding: "3px 6px",
              border: "1px solid #4a5568",
              borderRadius: 4,
              background: "#2d3148",
              color: "#e2e8f0",
              fontSize: 11,
            }}
            value={queueEditStation?.id ?? ""}
            onChange={(e) => handleSelectQueueStation(e.target.value)}
          >
            <option value="">Select Station</option>
            {stations.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}
              </option>
            ))}
          </select>

          {queueEditStation && (
            <>
              <span style={{ color: "#94a3b8", fontSize: 10 }}>
                Click cells: Approach{" > "}Q1..Qn{" > "}Holding
              </span>
              {queueCells.map((c, i) => (
                <span
                  key={i}
                  style={{
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 2,
                    padding: "1px 5px",
                    background: i === 0 ? "#eab30833" : "#06b6d433",
                    border: `1px solid ${i === 0 ? "#eab308" : "#06b6d4"}`,
                    borderRadius: 3,
                    color: "#e2e8f0",
                    fontSize: 10,
                    cursor: "pointer",
                  }}
                  title="Click to remove"
                  onClick={() => handleRemoveQueueCell(i)}
                >
                  {i === 0 ? "A" : `Q${i}`}
                  ({c.row},{c.col})
                </span>
              ))}
              <button
                style={{ ...BTN, borderColor: "#22c55e", color: "#22c55e" }}
                onClick={handleSaveQueue}
                disabled={savingQueue || queueCells.length === 0}
              >
                {savingQueue ? "..." : "Save Q"}
              </button>
              <button
                style={{ ...BTN, borderColor: "#ef4444", color: "#ef4444", fontSize: 10 }}
                onClick={handleClearQueue}
                disabled={savingQueue}
              >
                Clear Q
              </button>
            </>
          )}
        </>
      )}

      <div style={{ width: 1, height: 20, background: "#4a5568" }} />

      {/* Robot placement tools */}
      <button
        style={editorTool === "ROBOT_K50H" ? TOOL_ACTIVE : TOOL_BTN}
        onClick={() => setEditorTool("ROBOT_K50H")}
      >
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: "50%",
            background: "#22c55e",
            border: "1px solid #64748b",
            flexShrink: 0,
          }}
        />
        K50H
      </button>
      <button
        style={editorTool === "ROBOT_A42TD" ? TOOL_ACTIVE : TOOL_BTN}
        onClick={() => setEditorTool("ROBOT_A42TD")}
      >
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: "50%",
            background: "#3b82f6",
            border: "1px solid #64748b",
            flexShrink: 0,
          }}
        />
        A42TD
      </button>
      {(editorTool === "ROBOT_K50H" || editorTool === "ROBOT_A42TD") && (
        <span style={{ color: "#94a3b8", fontSize: 10 }}>
          Click to place / click robot to remove
        </span>
      )}

      <div style={{ width: 1, height: 20, background: "#4a5568" }} />

      {/* Territory editor tool */}
      <button
        style={editorTool === "TERRITORY" ? TOOL_ACTIVE : TOOL_BTN}
        onClick={() => setEditorTool("TERRITORY")}
      >
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: 2,
            background: "#a855f7",
            border: "1px solid #64748b",
            flexShrink: 0,
          }}
        />
        Territory
      </button>

      {editorTool === "TERRITORY" && (
        <>
          <select
            style={{
              padding: "3px 6px",
              border: "1px solid #4a5568",
              borderRadius: 4,
              background: "#2d3148",
              color: "#e2e8f0",
              fontSize: 11,
            }}
            value={territoryRobotId}
            onChange={(e) => handleSelectTerritoryRobot(e.target.value)}
          >
            <option value="">Select A42TD</option>
            {a42tdRobots.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
              </option>
            ))}
          </select>

          {territoryRobotId && (
            <>
              <span style={{ color: "#94a3b8", fontSize: 10 }}>
                {territoryClickCount === 0 ? "Click corner 1" : territoryClickCount === 1 ? "Click corner 2" : "Rectangle set"}
              </span>
              {territoryColMin !== null && (
                <span
                  style={{
                    padding: "1px 5px",
                    background: "#a855f733",
                    border: "1px solid #a855f7",
                    borderRadius: 3,
                    color: "#e2e8f0",
                    fontSize: 10,
                  }}
                >
                  ({territoryRowMin},{territoryColMin})..({territoryRowMax},{territoryColMax})
                </span>
              )}
              <button
                style={{ ...BTN, borderColor: "#22c55e", color: "#22c55e" }}
                onClick={handleSaveTerritory}
                disabled={savingTerritory || territoryColMin === null}
              >
                {savingTerritory ? "..." : "Save"}
              </button>
              <button
                style={{ ...BTN, borderColor: "#ef4444", color: "#ef4444", fontSize: 10 }}
                onClick={handleClearTerritory}
                disabled={savingTerritory}
              >
                Clear
              </button>
            </>
          )}
        </>
      )}

      <div style={{ width: 1, height: 20, background: "#4a5568" }} />

      <button
        style={{ ...BTN, borderColor: "#ef4444", color: "#ef4444" }}
        onClick={() => setEditorMode(false)}
      >
        Exit Editor
      </button>
    </div>
  );
}

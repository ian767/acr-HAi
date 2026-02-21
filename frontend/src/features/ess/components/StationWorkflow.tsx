import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { wesApi } from "@/api/wes";
import { StationOperatorView } from "./StationOperatorView";
import type { Station } from "@/types/station";
import type { PickTask } from "@/types/pickTask";

// ------------------------------------------------------------------ styles

const INPUT_STYLE: React.CSSProperties = {
  width: "100%",
  padding: "6px 10px",
  border: "1px solid #4a5568",
  borderRadius: 4,
  background: "#232738",
  color: "#e2e8f0",
  fontSize: 13,
  boxSizing: "border-box" as const,
};

const BTN_SCAN: React.CSSProperties = {
  width: "100%",
  padding: "8px 0",
  border: "none",
  borderRadius: 6,
  background: "#22c55e",
  color: "#fff",
  cursor: "pointer",
  fontSize: 13,
  fontWeight: 600,
};

const BTN_ENTRY: React.CSSProperties = {
  width: "100%",
  padding: "10px 0",
  border: "2px solid #3b82f6",
  borderRadius: 8,
  background: "#3b82f620",
  color: "#3b82f6",
  cursor: "pointer",
  fontSize: 14,
  fontWeight: 700,
  marginBottom: 12,
  transition: "background 0.2s, border-color 0.2s",
};

const BADGE_COLORS: Record<string, string> = {
  SOURCE_REQUESTED: "#eab308",
  SOURCE_AT_CANTILEVER: "#f97316",
  SOURCE_AT_STATION: "#22c55e",
  PICKING: "#3b82f6",
  RETURN_REQUESTED: "#a855f7",
  RETURN_AT_CANTILEVER: "#f97316",
  COMPLETED: "#6b7280",
};

// ------------------------------------------------------------------ component

export function StationWorkflow() {
  const queryClient = useQueryClient();
  const [stations, setStations] = useState<Station[]>([]);
  const [selectedStationId, setSelectedStationId] = useState<string | null>(null);
  const [pickTasks, setPickTasks] = useState<PickTask[]>([]);
  const [scanningTaskId, setScanningTaskId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [showOperatorView, setShowOperatorView] = useState(false);

  // Load stations once
  useEffect(() => {
    wesApi.listStations().then((s) => {
      setStations(s);
      if (s.length > 0 && !selectedStationId) {
        setSelectedStationId(s[0]!.id);
      }
    }).catch(() => {});
  }, []);

  // Poll pick tasks for selected station
  useEffect(() => {
    if (!selectedStationId) return;
    const load = () =>
      wesApi
        .listPickTasks({ station_id: selectedStationId })
        .then(setPickTasks)
        .catch(() => {});
    load();
    const id = setInterval(load, 1500);
    return () => clearInterval(id);
  }, [selectedStationId]);

  const handleScan = useCallback(
    async (task: PickTask) => {
      if (!selectedStationId) return;
      setError("");
      setScanningTaskId(task.id);
      try {
        await wesApi.scanItem(selectedStationId, task.id);
        // Invalidate orders cache to reflect progress on Orders page
        queryClient.invalidateQueries({ queryKey: ["orders"] });
      } catch (err: any) {
        setError(err.message || "Scan failed");
      } finally {
        setScanningTaskId(null);
      }
    },
    [selectedStationId, queryClient],
  );

  const scannableTasks = pickTasks.filter(
    (t) => t.state === "SOURCE_AT_STATION" || t.state === "PICKING",
  );
  const pendingTasks = pickTasks.filter(
    (t) => t.state === "SOURCE_REQUESTED" || t.state === "SOURCE_AT_CANTILEVER",
  );

  const selectedStation = stations.find((s) => s.id === selectedStationId);

  // Full-screen operator view
  if (showOperatorView && selectedStationId) {
    return (
      <StationOperatorView
        stationId={selectedStationId}
        stationName={selectedStation?.name ?? `Station ${selectedStationId.slice(0, 8)}`}
        onClose={() => setShowOperatorView(false)}
      />
    );
  }

  return (
    <div>
      <h4 style={{ margin: "0 0 8px", fontSize: 14, color: "#e2e8f0" }}>
        Station Operator
      </h4>

      {/* Station selector */}
      <div style={{ marginBottom: 10 }}>
        <label style={{ fontSize: 11, color: "#94a3b8", display: "block", marginBottom: 3 }}>
          Station
        </label>
        <select
          style={INPUT_STYLE}
          value={selectedStationId ?? ""}
          onChange={(e) => setSelectedStationId(e.target.value || null)}
        >
          <option value="">-- select station --</option>
          {stations.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      </div>

      {/* Entry Button */}
      {selectedStationId && (
        <button
          style={BTN_ENTRY}
          onClick={() => setShowOperatorView(true)}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = "#3b82f640";
            e.currentTarget.style.borderColor = "#60a5fa";
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = "#3b82f620";
            e.currentTarget.style.borderColor = "#3b82f6";
          }}
        >
          Enter Station
        </button>
      )}

      {/* Scannable tasks (SOURCE_AT_STATION / PICKING) */}
      {scannableTasks.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          {scannableTasks.map((task) => {
            const pct = task.qty_to_pick > 0
              ? Math.round((task.qty_picked / task.qty_to_pick) * 100)
              : 0;
            const isScanning = scanningTaskId === task.id;

            return (
              <div
                key={task.id}
                style={{
                  border: "1px solid #22c55e44",
                  borderRadius: 6,
                  padding: 10,
                  marginBottom: 8,
                  background: "#22c55e08",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12 }}>
                  <span style={{ fontWeight: 600, color: "#e2e8f0" }}>{task.sku}</span>
                  <span style={{ color: "#94a3b8" }}>
                    {task.qty_picked} / {task.qty_to_pick}
                  </span>
                </div>

                {/* Progress */}
                <div style={{ height: 4, background: "#2d3148", borderRadius: 2, margin: "6px 0", overflow: "hidden" }}>
                  <div
                    style={{
                      height: "100%",
                      width: `${pct}%`,
                      background: "#22c55e",
                      borderRadius: 2,
                      transition: "width 0.3s",
                    }}
                  />
                </div>

                {task.qty_picked < task.qty_to_pick && (
                  <button
                    style={{ ...BTN_SCAN, opacity: isScanning ? 0.6 : 1 }}
                    onClick={() => handleScan(task)}
                    disabled={isScanning}
                  >
                    {isScanning ? "Scanning..." : `Scan 1x ${task.sku}`}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Pending tasks (robot en route) */}
      {pendingTasks.length > 0 && (
        <div style={{ marginBottom: 8 }}>
          {pendingTasks.map((task) => (
            <div
              key={task.id}
              style={{
                border: "1px solid #2d3148",
                borderRadius: 6,
                padding: 8,
                marginBottom: 6,
                background: "#1e2235",
                fontSize: 12,
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between" }}>
                <span style={{ color: "#94a3b8" }}>{task.sku} x {task.qty_to_pick}</span>
                <span
                  style={{
                    padding: "1px 6px",
                    borderRadius: 3,
                    fontSize: 10,
                    background: BADGE_COLORS[task.state] ?? "#666",
                    color: "#fff",
                  }}
                >
                  {task.state === "SOURCE_REQUESTED" ? "Robot en route" : "At cantilever"}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {scannableTasks.length === 0 && pendingTasks.length === 0 && (
        <p style={{ fontSize: 12, color: "#64748b", margin: 0 }}>
          No tasks at this station.
        </p>
      )}

      {error && (
        <div style={{ fontSize: 12, color: "#ef4444", marginTop: 6 }}>{error}</div>
      )}
    </div>
  );
}

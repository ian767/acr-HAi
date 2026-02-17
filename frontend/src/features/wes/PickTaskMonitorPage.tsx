import { useState } from "react";
import { usePickTasks } from "../../api/hooks";
import type { PickTaskState } from "../../types/pickTask";

const STATE_FLOW: PickTaskState[] = [
  "SOURCE_REQUESTED",
  "SOURCE_AT_CANTILEVER",
  "SOURCE_AT_STATION",
  "PICKING",
  "RETURN_REQUESTED",
  "RETURN_AT_CANTILEVER",
  "COMPLETED",
];

const STATE_COLORS: Record<string, string> = {
  SOURCE_REQUESTED: "#3b82f6",
  SOURCE_AT_CANTILEVER: "#8b5cf6",
  SOURCE_AT_STATION: "#a855f7",
  PICKING: "#f97316",
  RETURN_REQUESTED: "#eab308",
  RETURN_AT_CANTILEVER: "#14b8a6",
  COMPLETED: "#22c55e",
};

export default function PickTaskMonitorPage() {
  const [stateFilter, setStateFilter] = useState<string>("ALL");
  const { data: tasks, isLoading } = usePickTasks(
    stateFilter === "ALL" ? undefined : { state: stateFilter },
  );

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Pick Task Monitor</h1>

      {/* State machine flow visualization */}
      <div
        style={{
          display: "flex",
          gap: 4,
          alignItems: "center",
          marginBottom: 20,
          padding: 12,
          background: "var(--bg-card)",
          borderRadius: "var(--radius)",
          overflowX: "auto",
        }}
      >
        {STATE_FLOW.map((state, i) => (
          <div key={state} style={{ display: "flex", alignItems: "center" }}>
            <button
              onClick={() => setStateFilter(stateFilter === state ? "ALL" : state)}
              style={{
                padding: "6px 10px",
                borderRadius: 6,
                border: stateFilter === state ? "2px solid #fff" : "1px solid var(--border)",
                background: STATE_COLORS[state],
                color: "#fff",
                cursor: "pointer",
                fontSize: 11,
                whiteSpace: "nowrap",
                fontWeight: stateFilter === state ? 700 : 400,
              }}
            >
              {state.replace(/_/g, " ")}
              {tasks && (
                <span style={{ marginLeft: 4, opacity: 0.8 }}>
                  ({tasks.filter((t) => t.state === state).length})
                </span>
              )}
            </button>
            {i < STATE_FLOW.length - 1 && (
              <span style={{ margin: "0 2px", color: "var(--text-secondary)" }}>→</span>
            )}
          </div>
        ))}
      </div>

      {isLoading && <p>Loading...</p>}

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
            <th style={{ padding: 8 }}>ID</th>
            <th style={{ padding: 8 }}>SKU</th>
            <th style={{ padding: 8 }}>Progress</th>
            <th style={{ padding: 8 }}>State</th>
            <th style={{ padding: 8 }}>Robot</th>
            <th style={{ padding: 8 }}>Updated</th>
          </tr>
        </thead>
        <tbody>
          {tasks?.map((task) => (
            <tr key={task.id} style={{ borderBottom: "1px solid var(--border)" }}>
              <td style={{ padding: 8, fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {task.id.slice(0, 8)}
              </td>
              <td style={{ padding: 8 }}>{task.sku}</td>
              <td style={{ padding: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <div
                    style={{
                      width: 80,
                      height: 6,
                      background: "var(--bg-secondary)",
                      borderRadius: 3,
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        width: `${task.qty_to_pick > 0 ? (task.qty_picked / task.qty_to_pick) * 100 : 0}%`,
                        height: "100%",
                        background: "var(--accent-green)",
                        borderRadius: 3,
                      }}
                    />
                  </div>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {task.qty_picked}/{task.qty_to_pick}
                  </span>
                </div>
              </td>
              <td style={{ padding: 8 }}>
                <span
                  style={{
                    padding: "2px 8px",
                    borderRadius: 10,
                    fontSize: 11,
                    fontWeight: 600,
                    background: STATE_COLORS[task.state] ?? "#666",
                    color: "#fff",
                  }}
                >
                  {task.state.replace(/_/g, " ")}
                </span>
              </td>
              <td style={{ padding: 8, fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {task.assigned_robot_id ? task.assigned_robot_id.slice(0, 8) : "-"}
              </td>
              <td style={{ padding: 8, fontSize: 12, color: "var(--text-secondary)" }}>
                {new Date(task.updated_at).toLocaleTimeString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {tasks && tasks.length === 0 && (
        <p style={{ textAlign: "center", padding: 32, color: "var(--text-secondary)" }}>
          No pick tasks found
        </p>
      )}
    </div>
  );
}

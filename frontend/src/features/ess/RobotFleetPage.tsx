import { useState, useMemo, useCallback } from "react";
import { useRobots, useZones } from "@/api/hooks";
import { useUiStore } from "@/stores/useUiStore";
import type { Robot, RobotStatus, RobotType } from "@/types/robot";

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

const TABLE_WRAPPER_STYLE: React.CSSProperties = {
  flex: 1,
  overflowY: "auto",
  padding: 16,
};

const TABLE_STYLE: React.CSSProperties = {
  width: "100%",
  borderCollapse: "collapse",
  fontSize: 13,
};

const TH_STYLE: React.CSSProperties = {
  textAlign: "left",
  padding: "8px 12px",
  borderBottom: "2px solid #2d3148",
  color: "#94a3b8",
  fontWeight: 600,
  fontSize: 12,
  textTransform: "uppercase",
  letterSpacing: 0.5,
  position: "sticky",
  top: 0,
  background: "#0e1015",
};

const TD_STYLE: React.CSSProperties = {
  padding: "8px 12px",
  borderBottom: "1px solid #1e2130",
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

const STATUS_BG: Record<RobotStatus, string> = {
  IDLE: "rgba(156,163,175,0.15)",
  ASSIGNED: "rgba(59,130,246,0.15)",
  MOVING: "rgba(34,197,94,0.15)",
  WAITING: "rgba(234,179,8,0.15)",
  DOCKING: "rgba(168,85,247,0.15)",
  BLOCKED: "rgba(239,68,68,0.15)",
  CHARGING: "rgba(20,184,166,0.15)",
};

const ALL_STATUSES: RobotStatus[] = [
  "IDLE",
  "ASSIGNED",
  "MOVING",
  "WAITING",
  "DOCKING",
  "BLOCKED",
  "CHARGING",
];

const ALL_TYPES: RobotType[] = ["K50H", "A42TD"];

// ------------------------------------------------------------------ component

/**
 * RobotFleetPage shows a filterable table of all robots.
 * Clicking a row navigates to the warehouse map with the robot selected.
 */
export function RobotFleetPage() {
  const { data: robots, isLoading } = useRobots();
  const { data: zones } = useZones();
  const selectRobot = useUiStore((s) => s.selectRobot);
  const setActiveZone = useUiStore((s) => s.setActiveZone);

  // Filters
  const [filterZone, setFilterZone] = useState<string>("");
  const [filterStatus, setFilterStatus] = useState<string>("");
  const [filterType, setFilterType] = useState<string>("");

  const filtered = useMemo(() => {
    if (!robots) return [];
    return robots.filter((r: Robot) => {
      if (filterZone && r.zone_id !== filterZone) return false;
      if (filterStatus && r.status !== filterStatus) return false;
      if (filterType && r.type !== filterType) return false;
      return true;
    });
  }, [robots, filterZone, filterStatus, filterType]);

  const handleRowClick = useCallback(
    (robot: Robot) => {
      selectRobot(robot.id);
      setActiveZone(robot.zone_id);
      // Navigation to the map page would be handled by the app router.
      // If using react-router, the caller can wrap this in a navigate call.
      // For now we just set the store state so the map page picks it up.
    },
    [selectRobot, setActiveZone],
  );

  // Build a zone name lookup.
  const zoneNames = useMemo(() => {
    const map = new Map<string, string>();
    zones?.forEach((z) => map.set(z.id, z.name));
    return map;
  }, [zones]);

  // -------------------------------------------------------------- render

  return (
    <div style={PAGE_STYLE}>
      {/* ---- Filters ---- */}
      <div style={TOOLBAR_STYLE}>
        <span style={{ fontWeight: 600, fontSize: 15 }}>Robot Fleet</span>
        <div style={{ flex: 1 }} />

        <label style={LABEL_STYLE}>Zone</label>
        <select
          style={SELECT_STYLE}
          value={filterZone}
          onChange={(e) => setFilterZone(e.target.value)}
        >
          <option value="">All zones</option>
          {zones?.map((z) => (
            <option key={z.id} value={z.id}>
              {z.name}
            </option>
          ))}
        </select>

        <label style={LABEL_STYLE}>Status</label>
        <select
          style={SELECT_STYLE}
          value={filterStatus}
          onChange={(e) => setFilterStatus(e.target.value)}
        >
          <option value="">All statuses</option>
          {ALL_STATUSES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>

        <label style={LABEL_STYLE}>Type</label>
        <select
          style={SELECT_STYLE}
          value={filterType}
          onChange={(e) => setFilterType(e.target.value)}
        >
          <option value="">All types</option>
          {ALL_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>

        <span style={{ ...LABEL_STYLE, marginLeft: 8 }}>
          {filtered.length} robot{filtered.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* ---- Table ---- */}
      <div style={TABLE_WRAPPER_STYLE}>
        {isLoading ? (
          <div style={{ textAlign: "center", padding: 40, color: "#94a3b8" }}>
            Loading robots...
          </div>
        ) : filtered.length === 0 ? (
          <div style={{ textAlign: "center", padding: 40, color: "#94a3b8" }}>
            No robots match the current filters.
          </div>
        ) : (
          <table style={TABLE_STYLE}>
            <thead>
              <tr>
                <th style={TH_STYLE}>Name</th>
                <th style={TH_STYLE}>Type</th>
                <th style={TH_STYLE}>Zone</th>
                <th style={TH_STYLE}>Status</th>
                <th style={TH_STYLE}>Position</th>
                <th style={TH_STYLE}>Task</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((robot: Robot) => (
                <tr
                  key={robot.id}
                  onClick={() => handleRowClick(robot)}
                  style={{ cursor: "pointer" }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLElement).style.background =
                      "#1a1d27";
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLElement).style.background =
                      "transparent";
                  }}
                >
                  <td style={TD_STYLE}>
                    <span style={{ fontWeight: 500 }}>{robot.name}</span>
                  </td>
                  <td style={TD_STYLE}>
                    <TypeBadge type={robot.type} />
                  </td>
                  <td style={TD_STYLE}>
                    {zoneNames.get(robot.zone_id) ?? robot.zone_id}
                  </td>
                  <td style={TD_STYLE}>
                    <StatusBadge status={robot.status} />
                  </td>
                  <td style={TD_STYLE}>
                    ({robot.grid_row}, {robot.grid_col})
                  </td>
                  <td style={TD_STYLE}>
                    <span style={{ color: "#94a3b8", fontSize: 12 }}>
                      {robot.current_task_id ?? "--"}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ badges

function StatusBadge({ status }: { status: RobotStatus }) {
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 9999,
        fontSize: 11,
        fontWeight: 600,
        color: STATUS_COLORS[status],
        background: STATUS_BG[status],
      }}
    >
      {status}
    </span>
  );
}

function TypeBadge({ type }: { type: RobotType }) {
  const color = type === "K50H" ? "#22c55e" : "#3b82f6";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: 4,
        fontSize: 11,
        fontWeight: 600,
        color,
        background: `${color}22`,
        border: `1px solid ${color}44`,
      }}
    >
      {type}
    </span>
  );
}

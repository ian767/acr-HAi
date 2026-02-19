import { useState } from "react";

// ------------------------------------------------------------------ constants

const CELL_ITEMS: { label: string; color: string }[] = [
  { label: "Floor", color: "#1a1d27" },
  { label: "Rack", color: "#4a5568" },
  { label: "Station", color: "#3b82f6" },
  { label: "Aisle", color: "#2d3148" },
  { label: "Wall", color: "#111111" },
  { label: "Charging", color: "#22c55e" },
];

const ROBOT_ITEMS: { label: string; color: string }[] = [
  { label: "A42TD", color: "#3b82f6" },
  { label: "K50H", color: "#22c55e" },
];

const STATUS_ITEMS: { label: string; color: string; pulse?: boolean }[] = [
  { label: "Idle", color: "#9ca3af" },
  { label: "Moving", color: "#22c55e" },
  { label: "Assigned", color: "#3b82f6" },
  { label: "Waiting", color: "#eab308", pulse: true },
  { label: "Blocked", color: "#ef4444", pulse: true },
  { label: "Docking", color: "#a855f7" },
  { label: "Charging", color: "#14b8a6" },
];

// ------------------------------------------------------------------ styles

const CONTAINER: React.CSSProperties = {
  position: "absolute",
  bottom: 16,
  left: 16,
  zIndex: 20,
  background: "#1a1d27ee",
  border: "1px solid #2d3148",
  borderRadius: 8,
  color: "#e2e8f0",
  fontFamily: "Inter, system-ui, sans-serif",
  fontSize: 11,
  maxWidth: 220,
  backdropFilter: "blur(8px)",
};

const HEADER: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "6px 10px",
  cursor: "pointer",
  userSelect: "none",
};

const BODY: React.CSSProperties = {
  padding: "0 10px 10px",
};

const SECTION_LABEL: React.CSSProperties = {
  fontSize: 10,
  color: "#94a3b8",
  fontWeight: 600,
  textTransform: "uppercase" as const,
  marginBottom: 4,
  marginTop: 8,
};

const ROW: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "2px 0",
};

// ------------------------------------------------------------------ component

export function MapLegend() {
  const [open, setOpen] = useState(false);

  return (
    <div style={CONTAINER}>
      <div style={HEADER} onClick={() => setOpen((v) => !v)}>
        <span style={{ fontWeight: 600, fontSize: 12 }}>Legend</span>
        <span style={{ fontSize: 10, color: "#94a3b8" }}>
          {open ? "\u25B2" : "\u25BC"}
        </span>
      </div>
      {open && (
        <div style={BODY}>
          <div style={SECTION_LABEL}>Cell Types</div>
          {CELL_ITEMS.map((item) => (
            <div key={item.label} style={ROW}>
              <span
                style={{
                  width: 12,
                  height: 12,
                  borderRadius: 2,
                  background: item.color,
                  border: "1px solid #4a5568",
                  flexShrink: 0,
                }}
              />
              <span>{item.label}</span>
            </div>
          ))}

          <div style={SECTION_LABEL}>Robot Types</div>
          {ROBOT_ITEMS.map((item) => (
            <div key={item.label} style={ROW}>
              <span
                style={{
                  width: 10,
                  height: 10,
                  borderRadius: "50%",
                  background: item.color,
                  flexShrink: 0,
                }}
              />
              <span>{item.label}</span>
            </div>
          ))}

          <div style={SECTION_LABEL}>Robot Status</div>
          {STATUS_ITEMS.map((item) => (
            <div key={item.label} style={ROW}>
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: item.color,
                  flexShrink: 0,
                  boxShadow: item.pulse
                    ? `0 0 4px ${item.color}`
                    : undefined,
                }}
              />
              <span>
                {item.label}
                {item.pulse && (
                  <span style={{ color: "#64748b", marginLeft: 4 }}>
                    (pulse)
                  </span>
                )}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

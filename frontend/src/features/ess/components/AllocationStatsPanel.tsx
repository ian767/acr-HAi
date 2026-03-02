import { useWarehouseStore } from "@/stores/useWarehouseStore";

const PANEL: React.CSSProperties = {
  position: "absolute",
  left: 16,
  bottom: 16,
  width: 280,
  background: "rgba(26, 29, 39, 0.95)",
  border: "1px solid #2d3148",
  borderRadius: 8,
  padding: 12,
  zIndex: 30,
  fontSize: 12,
  color: "#e2e8f0",
};

const ROW: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 8,
  marginBottom: 4,
};

const BAR_BG: React.CSSProperties = {
  background: "#2d3148",
  borderRadius: 3,
  height: 14,
  flex: 1,
  overflow: "hidden",
};

export function AllocationStatsPanel() {
  const stats = useWarehouseStore((s) => s.allocationStats);

  if (!stats || stats.stations.length === 0) {
    return (
      <div style={PANEL}>
        <div style={{ fontWeight: 600, marginBottom: 8 }}>
          Allocation Distribution
        </div>
        <div style={{ color: "#94a3b8" }}>No allocations yet</div>
      </div>
    );
  }

  return (
    <div style={PANEL}>
      <div style={{ fontWeight: 600, marginBottom: 8 }}>
        Allocation Distribution ({stats.total} total)
      </div>
      {stats.stations.map((s) => (
        <div key={s.station_id} style={ROW}>
          <span
            style={{
              width: 70,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {s.name ?? s.station_id.slice(0, 8)}
          </span>
          <div style={BAR_BG}>
            <div
              style={{
                width: `${s.pct}%`,
                height: "100%",
                background: "#3b82f6",
                borderRadius: 3,
              }}
            />
          </div>
          <span style={{ width: 55, textAlign: "right", color: "#94a3b8" }}>
            {s.count} ({s.pct}%)
          </span>
        </div>
      ))}
    </div>
  );
}

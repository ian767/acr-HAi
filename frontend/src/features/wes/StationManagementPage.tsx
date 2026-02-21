import { useState } from "react";
import { useStations, useToggleStationOnline } from "../../api/hooks";
import { StationOperatorView } from "../ess/components/StationOperatorView";

const STATUS_COLORS: Record<string, string> = {
  IDLE: "#8b8fa3",
  ACTIVE: "#22c55e",
  PAUSED: "#eab308",
};

export default function StationManagementPage() {
  const { data: stations, isLoading } = useStations();
  const toggle = useToggleStationOnline();
  const [operatorStation, setOperatorStation] = useState<{ id: string; name: string } | null>(null);

  // Full-screen operator view
  if (operatorStation) {
    return (
      <StationOperatorView
        stationId={operatorStation.id}
        stationName={operatorStation.name}
        onClose={() => setOperatorStation(null)}
      />
    );
  }

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Station Management</h1>

      {isLoading && <p>Loading...</p>}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 16 }}>
        {stations?.map((station) => (
          <div
            key={station.id}
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: 16,
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
              <h3 style={{ fontSize: 16, fontWeight: 600 }}>{station.name}</h3>
              <span
                style={{
                  padding: "2px 10px",
                  borderRadius: 12,
                  fontSize: 12,
                  fontWeight: 600,
                  background: STATUS_COLORS[station.status] ?? "#666",
                  color: "#fff",
                }}
              >
                {station.status}
              </span>
            </div>

            <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>
              <div>Position: ({station.grid_row}, {station.grid_col})</div>
              <div>Max Queue: {station.max_queue_size}</div>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>Online:</span>
              <button
                onClick={() => toggle.mutate({ id: station.id, online: !station.is_online })}
                style={{
                  padding: "4px 14px",
                  borderRadius: 4,
                  border: "none",
                  background: station.is_online ? "var(--accent-green)" : "var(--accent-red)",
                  color: "#fff",
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                {station.is_online ? "ON" : "OFF"}
              </button>
            </div>

            {/* Enter Station button */}
            <button
              onClick={() => setOperatorStation({ id: station.id, name: station.name })}
              style={{
                width: "100%",
                marginTop: 12,
                padding: "10px 0",
                border: "2px solid var(--accent-blue)",
                borderRadius: 8,
                background: "rgba(59, 130, 246, 0.1)",
                color: "var(--accent-blue)",
                cursor: "pointer",
                fontSize: 14,
                fontWeight: 700,
              }}
            >
              Enter Station
            </button>
          </div>
        ))}
      </div>

      {stations && stations.length === 0 && (
        <p style={{ textAlign: "center", padding: 32, color: "var(--text-secondary)" }}>
          No stations configured
        </p>
      )}
    </div>
  );
}

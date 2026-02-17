import { useWarehouseStore } from "../../stores/useWarehouseStore";

const KPI_CARDS = [
  { key: "orders_completed", label: "Orders Completed", color: "var(--accent-green)" },
  { key: "orders_in_progress", label: "In Progress", color: "var(--accent-blue)" },
  { key: "picks_per_hour", label: "Picks/Hour", color: "var(--accent-purple)", decimals: 1 },
  { key: "robot_utilization", label: "Robot Util %", color: "var(--accent-yellow)", percent: true },
  { key: "avg_pick_time_s", label: "Avg Pick (s)", color: "var(--accent-red)", decimals: 1 },
] as const;

export default function DashboardPage() {
  const kpi = useWarehouseStore((s) => s.kpi);
  const robots = useWarehouseStore((s) => s.robots);
  const alarms = useWarehouseStore((s) => s.alarms);
  const connected = useWarehouseStore((s) => s.connected);

  const robotCount = Object.keys(robots).length;
  const activeAlarms = alarms.filter((a) => !a.acknowledged).length;

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 24 }}>
        <h1 style={{ fontSize: 24 }}>Dashboard</h1>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <div
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: connected ? "var(--accent-green)" : "var(--accent-red)",
            }}
          />
          <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
            {connected ? "Connected" : "Disconnected"}
          </span>
        </div>
      </div>

      {/* KPI Cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: 16, marginBottom: 24 }}>
        {KPI_CARDS.map((card) => {
          let value: string | number = "-";
          if (kpi) {
            const raw = kpi[card.key as keyof typeof kpi] as number;
            if ("percent" in card && card.percent) {
              value = `${(raw * 100).toFixed(0)}%`;
            } else if ("decimals" in card) {
              value = raw.toFixed(card.decimals);
            } else {
              value = raw;
            }
          }
          return (
            <div
              key={card.key}
              style={{
                background: "var(--bg-card)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                padding: 20,
                borderLeft: `3px solid ${card.color}`,
              }}
            >
              <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
                {card.label}
              </div>
              <div style={{ fontSize: 28, fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                {value}
              </div>
            </div>
          );
        })}
      </div>

      {/* System health row */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {/* Robot summary */}
        <div
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: 20,
          }}
        >
          <h3 style={{ fontSize: 14, marginBottom: 12 }}>Robot Fleet</h3>
          <div style={{ fontSize: 32, fontWeight: 700, fontFamily: "var(--font-mono)" }}>
            {robotCount}
          </div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>robots active</div>
        </div>

        {/* Alarm summary */}
        <div
          style={{
            background: "var(--bg-card)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius)",
            padding: 20,
          }}
        >
          <h3 style={{ fontSize: 14, marginBottom: 12 }}>Active Alarms</h3>
          <div
            style={{
              fontSize: 32,
              fontWeight: 700,
              fontFamily: "var(--font-mono)",
              color: activeAlarms > 0 ? "var(--accent-red)" : "var(--accent-green)",
            }}
          >
            {activeAlarms}
          </div>
          <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>unacknowledged</div>
        </div>
      </div>
    </div>
  );
}

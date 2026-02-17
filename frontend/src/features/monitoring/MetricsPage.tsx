import { useEffect, useState } from "react";
import { api } from "../../api/client";

interface MetricSnapshot {
  timestamp: number;
  orders_completed: number;
  orders_in_progress: number;
  picks_per_hour: number;
  robot_utilization: number;
  avg_pick_time_s: number;
}

export default function MetricsPage() {
  const [history, setHistory] = useState<MetricSnapshot[]>([]);
  const [latest, setLatest] = useState<MetricSnapshot | null>(null);

  useEffect(() => {
    const fetchMetrics = async () => {
      try {
        const data = await api.get<MetricSnapshot>("/metrics");
        setLatest(data);
        setHistory((prev) => [...prev.slice(-59), data]);
      } catch {
        // ignore fetch errors
      }
    };

    fetchMetrics();
    const interval = setInterval(fetchMetrics, 2000);
    return () => clearInterval(interval);
  }, []);

  const metrics = [
    { label: "Orders Completed", value: latest?.orders_completed ?? 0, unit: "" },
    { label: "Orders In Progress", value: latest?.orders_in_progress ?? 0, unit: "" },
    { label: "Picks/Hour", value: latest?.picks_per_hour?.toFixed(1) ?? "0", unit: "/hr" },
    {
      label: "Robot Utilization",
      value: `${((latest?.robot_utilization ?? 0) * 100).toFixed(0)}`,
      unit: "%",
    },
    { label: "Avg Pick Time", value: latest?.avg_pick_time_s?.toFixed(1) ?? "0", unit: "s" },
  ];

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Metrics</h1>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 16, marginBottom: 24 }}>
        {metrics.map((m) => (
          <div
            key={m.label}
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: 20,
            }}
          >
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
              {m.label}
            </div>
            <div style={{ fontSize: 28, fontWeight: 700, fontFamily: "var(--font-mono)" }}>
              {m.value}
              <span style={{ fontSize: 14, fontWeight: 400, color: "var(--text-secondary)" }}>
                {m.unit}
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Simple sparkline visualization */}
      <div
        style={{
          background: "var(--bg-card)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius)",
          padding: 20,
        }}
      >
        <h3 style={{ fontSize: 14, marginBottom: 12 }}>Picks/Hour (last 2 min)</h3>
        <div style={{ display: "flex", alignItems: "end", gap: 2, height: 100 }}>
          {history.map((snap, i) => {
            const maxPPH = Math.max(...history.map((h) => h.picks_per_hour), 1);
            const height = (snap.picks_per_hour / maxPPH) * 100;
            return (
              <div
                key={i}
                style={{
                  flex: 1,
                  height: `${height}%`,
                  background: "var(--accent-blue)",
                  borderRadius: 2,
                  minWidth: 2,
                  opacity: 0.5 + (i / history.length) * 0.5,
                }}
              />
            );
          })}
        </div>
      </div>
    </div>
  );
}

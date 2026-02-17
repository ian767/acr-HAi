import { useWarehouseStore } from "../../stores/useWarehouseStore";
import { api } from "../../api/client";

const SEVERITY_COLORS: Record<string, string> = {
  INFO: "#3b82f6",
  WARNING: "#eab308",
  ERROR: "#f97316",
  CRITICAL: "#ef4444",
};

export default function AlarmListPage() {
  const alarms = useWarehouseStore((s) => s.alarms);
  const clearAlarm = useWarehouseStore((s) => s.clearAlarm);

  const handleAck = async (alarmId: string) => {
    await api.post(`/alarms/${alarmId}/ack`);
    clearAlarm(alarmId);
  };

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Alarms</h1>

      {alarms.length === 0 ? (
        <p style={{ color: "var(--text-secondary)", textAlign: "center", padding: 40 }}>
          No alarms
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {alarms.map((alarm) => (
            <div
              key={alarm.id}
              style={{
                background: "var(--bg-card)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                borderLeft: `3px solid ${SEVERITY_COLORS[alarm.severity] ?? "#666"}`,
                padding: 16,
                opacity: alarm.acknowledged ? 0.5 : 1,
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
                  <span
                    style={{
                      padding: "1px 8px",
                      borderRadius: 10,
                      fontSize: 11,
                      fontWeight: 600,
                      background: SEVERITY_COLORS[alarm.severity] ?? "#666",
                      color: "#fff",
                    }}
                  >
                    {alarm.severity}
                  </span>
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                    {alarm.source}
                  </span>
                </div>
                <div style={{ fontSize: 14 }}>{alarm.message}</div>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>
                  {new Date(alarm.created_at).toLocaleString()}
                </div>
              </div>

              {!alarm.acknowledged && (
                <button
                  onClick={() => handleAck(alarm.id)}
                  style={{
                    padding: "6px 14px",
                    borderRadius: 4,
                    border: "1px solid var(--border)",
                    background: "transparent",
                    color: "var(--text-primary)",
                    cursor: "pointer",
                    fontSize: 12,
                    whiteSpace: "nowrap",
                  }}
                >
                  ACK
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

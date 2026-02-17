import { useParams } from "react-router-dom";
import { useState } from "react";
import { usePickTasks } from "../../api/hooks";
import PutWallGrid from "./components/PutWallGrid";
import ScanInterface from "./components/ScanInterface";
import type { PutWallSlot } from "../../types/station";

export default function StationOperatorPage() {
  const { id: stationId } = useParams<{ id: string }>();
  const { data: tasks } = usePickTasks(stationId ? { station_id: stationId } : undefined);
  const [selectedSlotId, setSelectedSlotId] = useState<string | null>(null);

  if (!stationId) return <p>No station selected</p>;

  const activeTask = tasks?.find(
    (t) => t.state === "PICKING" || t.state === "SOURCE_AT_STATION",
  ) ?? null;

  // Mock put wall slots - in production these come from the API
  const mockSlots: PutWallSlot[] = ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"].map(
    (label, i) => ({
      id: `slot-${i}`,
      station_id: stationId,
      slot_label: label,
      target_tote_id: null,
      is_locked: false,
    }),
  );

  return (
    <div style={{ padding: 24, maxWidth: 1000, margin: "0 auto" }}>
      <h1 style={{ fontSize: 24, marginBottom: 4 }}>Station Operator</h1>
      <p style={{ color: "var(--text-secondary)", marginBottom: 24, fontSize: 14 }}>
        Station: {stationId.slice(0, 8)}
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        {/* Left: Scan interface */}
        <div>
          <ScanInterface stationId={stationId} activeTask={activeTask} />

          {/* Task queue */}
          <div
            style={{
              marginTop: 16,
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: 16,
            }}
          >
            <h3 style={{ fontSize: 14, marginBottom: 8 }}>Task Queue</h3>
            {tasks && tasks.length > 0 ? (
              tasks.map((task) => (
                <div
                  key={task.id}
                  style={{
                    padding: 8,
                    borderBottom: "1px solid var(--border)",
                    fontSize: 13,
                    display: "flex",
                    justifyContent: "space-between",
                  }}
                >
                  <span>{task.sku}</span>
                  <span style={{ color: "var(--text-secondary)" }}>
                    {task.state.replace(/_/g, " ")}
                  </span>
                </div>
              ))
            ) : (
              <p style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                No tasks in queue
              </p>
            )}
          </div>
        </div>

        {/* Right: Put wall */}
        <div>
          <div
            style={{
              background: "var(--bg-card)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius)",
              padding: 16,
            }}
          >
            <h3 style={{ fontSize: 14, marginBottom: 8 }}>Put Wall</h3>
            <PutWallGrid
              slots={mockSlots}
              activeSlotId={selectedSlotId}
              onSlotClick={(slot) => setSelectedSlotId(slot.id)}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

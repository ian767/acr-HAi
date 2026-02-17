import { useParams } from "react-router-dom";
import { useState, useCallback } from "react";
import { usePickTasks } from "../../api/hooks";
import { wesApi } from "../../api/wes";
import PutWallGrid from "./components/PutWallGrid";
import ScanInterface from "./components/ScanInterface";
import type { PutWallSlot } from "../../types/station";

export default function StationOperatorPage() {
  const { id: stationId } = useParams<{ id: string }>();
  const { data: tasks, refetch: refetchTasks } = usePickTasks(
    stationId ? { station_id: stationId } : undefined,
  );
  const [selectedSlotId, setSelectedSlotId] = useState<string | null>(null);
  const [completing, setCompleting] = useState(false);

  if (!stationId) return <p>No station selected</p>;

  const activeTask = tasks?.find(
    (t) => t.state === "PICKING" || t.state === "SOURCE_AT_STATION",
  ) ?? null;

  const pickComplete = activeTask
    && activeTask.qty_picked >= activeTask.qty_to_pick
    && activeTask.qty_to_pick > 0;

  // Build slots from active tasks' put_wall data, falling back to mock
  const slots: PutWallSlot[] = (() => {
    const taskSlots: PutWallSlot[] = [];
    if (tasks) {
      for (const t of tasks) {
        if (t.put_wall_slot_id) {
          taskSlots.push({
            id: t.put_wall_slot_id,
            station_id: stationId,
            slot_label: t.put_wall_slot_id.slice(0, 4).toUpperCase(),
            target_tote_id: t.target_tote_id,
            is_locked: t.state === "COMPLETED",
          });
        }
      }
    }
    if (taskSlots.length > 0) return taskSlots;
    // Fallback: mock slots
    return ["A1", "A2", "A3", "A4", "B1", "B2", "B3", "B4"].map(
      (label, i) => ({
        id: `slot-${i}`,
        station_id: stationId,
        slot_label: label,
        target_tote_id: null,
        is_locked: false,
      }),
    );
  })();

  const handleBind = useCallback(async (slot: PutWallSlot) => {
    if (!activeTask || slot.target_tote_id) return;
    setSelectedSlotId(slot.id);
    try {
      await wesApi.bindTote(stationId, activeTask.id, slot.id);
      refetchTasks();
    } catch (err) {
      console.error("Bind tote failed:", err);
    }
  }, [activeTask, stationId, refetchTasks]);

  const handleCompletePick = useCallback(async () => {
    if (!activeTask || completing) return;
    setCompleting(true);
    try {
      await wesApi.toteFull(stationId, activeTask.id);
      refetchTasks();
    } catch (err) {
      console.error("Complete pick failed:", err);
    } finally {
      setCompleting(false);
    }
  }, [activeTask, stationId, completing, refetchTasks]);

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

          {/* Complete Pick button */}
          {pickComplete && (
            <button
              onClick={handleCompletePick}
              disabled={completing}
              style={{
                width: "100%",
                marginTop: 12,
                padding: "12px 20px",
                borderRadius: 6,
                border: "none",
                background: completing ? "var(--text-secondary)" : "#22c55e",
                color: "#fff",
                cursor: completing ? "not-allowed" : "pointer",
                fontSize: 16,
                fontWeight: 700,
              }}
            >
              {completing ? "Processing..." : "Complete Pick"}
            </button>
          )}

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
              tasks.map((task) => {
                const isActive = activeTask?.id === task.id;
                return (
                  <div
                    key={task.id}
                    style={{
                      padding: 8,
                      borderBottom: "1px solid var(--border)",
                      fontSize: 13,
                      display: "flex",
                      justifyContent: "space-between",
                      background: isActive ? "rgba(59, 130, 246, 0.1)" : undefined,
                      borderLeft: isActive ? "3px solid var(--accent-blue)" : "3px solid transparent",
                    }}
                  >
                    <span style={{ fontWeight: isActive ? 600 : 400 }}>{task.sku}</span>
                    <span style={{ color: "var(--text-secondary)" }}>
                      {task.qty_picked}/{task.qty_to_pick} &middot; {task.state.replace(/_/g, " ")}
                    </span>
                  </div>
                );
              })
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
              slots={slots}
              activeSlotId={selectedSlotId}
              onSlotClick={(slot) => setSelectedSlotId(slot.id)}
              onBind={handleBind}
            />
          </div>
        </div>
      </div>
    </div>
  );
}

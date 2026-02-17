import type { PutWallSlot } from "../../../types/station";

interface Props {
  slots: PutWallSlot[];
  activeSlotId: string | null;
  onSlotClick: (slot: PutWallSlot) => void;
  onBind?: (slot: PutWallSlot) => void;
}

export default function PutWallGrid({ slots, activeSlotId, onSlotClick, onBind }: Props) {
  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: "repeat(4, 1fr)",
        gap: 8,
        padding: 16,
      }}
    >
      {slots.map((slot) => (
        <button
          key={slot.id}
          onClick={() => {
            onSlotClick(slot);
            if (!slot.target_tote_id && !slot.is_locked && onBind) {
              onBind(slot);
            }
          }}
          style={{
            padding: 16,
            borderRadius: "var(--radius)",
            border: slot.id === activeSlotId
              ? "2px solid var(--accent-blue)"
              : "1px solid var(--border)",
            background: slot.is_locked
              ? "var(--accent-red)"
              : slot.target_tote_id
                ? "var(--accent-green)"
                : "var(--bg-card)",
            color: "var(--text-primary)",
            cursor: slot.is_locked ? "not-allowed" : "pointer",
            textAlign: "center",
            opacity: slot.is_locked ? 0.6 : 1,
          }}
        >
          <div style={{ fontSize: 18, fontWeight: 700 }}>{slot.slot_label}</div>
          <div style={{ fontSize: 11, marginTop: 4, color: "var(--text-secondary)" }}>
            {slot.is_locked
              ? "LOCKED"
              : slot.target_tote_id
                ? "BOUND"
                : "EMPTY"}
          </div>
        </button>
      ))}
    </div>
  );
}

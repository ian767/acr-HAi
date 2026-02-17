import { useState, useEffect, useRef } from "react";
import { useScanItem } from "../../../api/hooks";
import type { PickTask } from "../../../types/pickTask";

interface Props {
  stationId: string;
  activeTask: PickTask | null;
}

export default function ScanInterface({ stationId, activeTask }: Props) {
  const [barcode, setBarcode] = useState("");
  const [flash, setFlash] = useState<"success" | "error" | null>(null);
  const scan = useScanItem();
  const flashTimeout = useRef<ReturnType<typeof setTimeout>>();

  useEffect(() => {
    return () => {
      if (flashTimeout.current) clearTimeout(flashTimeout.current);
    };
  }, []);

  const triggerFlash = (type: "success" | "error") => {
    setFlash(type);
    if (flashTimeout.current) clearTimeout(flashTimeout.current);
    flashTimeout.current = setTimeout(() => setFlash(null), 600);
  };

  const handleScan = () => {
    if (!activeTask || !barcode.trim()) return;
    scan.mutate(
      { stationId, pickTaskId: activeTask.id },
      {
        onSuccess: () => {
          setBarcode("");
          triggerFlash("success");
        },
        onError: () => {
          triggerFlash("error");
        },
      },
    );
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleScan();
    }
  };

  const borderColor = flash === "error"
    ? "#ef4444"
    : flash === "success"
      ? "#22c55e"
      : "var(--border)";

  const bgColor = flash === "success"
    ? "rgba(34, 197, 94, 0.08)"
    : flash === "error"
      ? "rgba(239, 68, 68, 0.08)"
      : "var(--bg-card)";

  return (
    <div
      style={{
        background: bgColor,
        border: `2px solid ${borderColor}`,
        borderRadius: "var(--radius)",
        padding: 20,
        transition: "background 0.3s, border-color 0.3s",
      }}
    >
      <h3 style={{ fontSize: 16, marginBottom: 12 }}>Scan Item</h3>

      {activeTask ? (
        <>
          <div style={{ marginBottom: 12, fontSize: 13, color: "var(--text-secondary)" }}>
            <div>SKU: <strong style={{ color: "var(--text-primary)" }}>{activeTask.sku}</strong></div>
            <div>
              Progress: <strong style={{ color: "var(--text-primary)" }}>
                {activeTask.qty_picked} / {activeTask.qty_to_pick}
              </strong>
            </div>
            <div>
              State: <strong style={{ color: "var(--text-primary)" }}>{activeTask.state}</strong>
            </div>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <input
              type="text"
              value={barcode}
              onChange={(e) => setBarcode(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Scan barcode..."
              autoFocus
              style={{
                flex: 1,
                padding: "10px 14px",
                borderRadius: 6,
                border: `1px solid ${flash === "error" ? "#ef4444" : "var(--border)"}`,
                background: "var(--bg-secondary)",
                color: "var(--text-primary)",
                fontSize: 16,
                fontFamily: "var(--font-mono)",
                transition: "border-color 0.3s",
              }}
            />
            <button
              onClick={handleScan}
              disabled={scan.isPending || activeTask.state !== "PICKING"}
              style={{
                padding: "10px 20px",
                borderRadius: 6,
                border: "none",
                background:
                  activeTask.state === "PICKING"
                    ? "var(--accent-green)"
                    : "var(--text-secondary)",
                color: "#fff",
                cursor: activeTask.state === "PICKING" ? "pointer" : "not-allowed",
                fontSize: 14,
                fontWeight: 600,
              }}
            >
              SCAN
            </button>
          </div>

          {/* Progress bar */}
          <div
            style={{
              width: "100%",
              height: 8,
              background: "var(--bg-secondary)",
              borderRadius: 4,
              marginTop: 12,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${activeTask.qty_to_pick > 0 ? (activeTask.qty_picked / activeTask.qty_to_pick) * 100 : 0}%`,
                height: "100%",
                background: "var(--accent-green)",
                borderRadius: 4,
                transition: "width 0.3s ease",
              }}
            />
          </div>
        </>
      ) : (
        <p style={{ color: "var(--text-secondary)", textAlign: "center", padding: 20 }}>
          No active pick task. Waiting for tote arrival...
        </p>
      )}
    </div>
  );
}

import { useState, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useUiStore } from "@/stores/useUiStore";
import { essApi } from "@/api/ess";

// ------------------------------------------------------------------ constants

const CELL_TYPES = [
  { type: "FLOOR", label: "Erase", color: "#1a1d27" },
  { type: "WALL", label: "Wall", color: "#111111" },
  { type: "RACK", label: "Rack", color: "#4a5568" },
  { type: "STATION", label: "Station", color: "#3b82f6" },
  { type: "AISLE", label: "Aisle", color: "#2d3148" },
  { type: "CHARGING", label: "Charging", color: "#22c55e" },
];

// ------------------------------------------------------------------ styles

const BAR: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 6,
  padding: "6px 12px",
  background: "#232738",
  borderBottom: "1px solid #2d3148",
  flexShrink: 0,
  flexWrap: "wrap",
  fontSize: 12,
};

const TOOL_BTN: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: 4,
  padding: "3px 8px",
  border: "1px solid #4a5568",
  borderRadius: 4,
  background: "#2d3148",
  color: "#e2e8f0",
  cursor: "pointer",
  fontSize: 11,
};

const TOOL_ACTIVE: React.CSSProperties = {
  ...TOOL_BTN,
  borderColor: "#3b82f6",
  background: "#3b82f622",
};

const BTN: React.CSSProperties = {
  padding: "3px 10px",
  border: "1px solid #4a5568",
  borderRadius: 4,
  background: "#2d3148",
  color: "#e2e8f0",
  cursor: "pointer",
  fontSize: 11,
};

// ------------------------------------------------------------------ component

export function EditorToolbar() {
  const editorTool = useUiStore((s) => s.editorTool);
  const setEditorTool = useUiStore((s) => s.setEditorTool);
  const setEditorMode = useUiStore((s) => s.setEditorMode);
  const queryClient = useQueryClient();

  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(false);
  const [layoutName, setLayoutName] = useState("");

  const handleSave = useCallback(async () => {
    if (!layoutName.trim()) return;
    setSaving(true);
    try {
      // Fetch current grid state to get cells.
      const gridState: any = await essApi.getGrid("");
      if (gridState) {
        await essApi.gridSave({
          name: layoutName.trim(),
          rows: gridState.rows,
          cols: gridState.cols,
          cells: gridState.cells,
        });
        setLayoutName("");
      }
    } finally {
      setSaving(false);
    }
  }, [layoutName]);

  const handleLoad = useCallback(async () => {
    const result: any = await essApi.gridListLayouts();
    if (!result?.layouts?.length) {
      alert("No saved layouts found.");
      return;
    }
    const name = prompt(
      "Enter layout name to load:\n" +
        result.layouts.map((l: any) => `  ${l.file} (${l.rows}x${l.cols})`).join("\n"),
    );
    if (!name) return;

    setLoading(true);
    try {
      await essApi.gridLoadInto(name);
      // Invalidate grid cache so canvas re-renders with new data
      await queryClient.invalidateQueries({ queryKey: ["grid"] });
    } catch (err) {
      alert("Failed to load layout: " + String(err));
    } finally {
      setLoading(false);
    }
  }, [queryClient]);

  return (
    <div style={BAR}>
      <span style={{ fontWeight: 600, color: "#94a3b8", marginRight: 4 }}>
        EDITOR
      </span>

      {/* Cell type palette */}
      {CELL_TYPES.map(({ type, label, color }) => (
        <button
          key={type}
          style={editorTool === type ? TOOL_ACTIVE : TOOL_BTN}
          onClick={() => setEditorTool(type)}
        >
          <span
            style={{
              width: 10,
              height: 10,
              borderRadius: 2,
              background: color,
              border: "1px solid #64748b",
              flexShrink: 0,
            }}
          />
          {label}
        </button>
      ))}

      <div style={{ width: 1, height: 20, background: "#4a5568" }} />

      {/* Save/Load */}
      <input
        type="text"
        placeholder="Layout name"
        value={layoutName}
        onChange={(e) => setLayoutName(e.target.value)}
        style={{
          padding: "3px 8px",
          border: "1px solid #4a5568",
          borderRadius: 4,
          background: "#2d3148",
          color: "#e2e8f0",
          fontSize: 11,
          width: 100,
        }}
      />
      <button style={BTN} onClick={handleSave} disabled={saving}>
        {saving ? "..." : "Save"}
      </button>
      <button style={BTN} onClick={handleLoad} disabled={loading}>
        {loading ? "..." : "Load"}
      </button>

      <div style={{ width: 1, height: 20, background: "#4a5568" }} />

      <button
        style={{ ...BTN, borderColor: "#ef4444", color: "#ef4444" }}
        onClick={() => setEditorMode(false)}
      >
        Exit Editor
      </button>
    </div>
  );
}

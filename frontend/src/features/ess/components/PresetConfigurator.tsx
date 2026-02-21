import { useState, useCallback } from "react";
import { essApi } from "@/api/ess";

// ------------------------------------------------------------------ styles

const OVERLAY: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  background: "rgba(0,0,0,0.6)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  zIndex: 100,
};

const DIALOG: React.CSSProperties = {
  background: "#1a1d27",
  border: "1px solid #2d3148",
  borderRadius: 10,
  padding: 24,
  width: 480,
  maxHeight: "80vh",
  overflowY: "auto",
  color: "#e2e8f0",
  fontFamily: "Inter, system-ui, sans-serif",
};

const TITLE: React.CSSProperties = {
  margin: "0 0 16px",
  fontSize: 18,
  fontWeight: 700,
};

const FIELD: React.CSSProperties = {
  marginBottom: 12,
};

const LABEL: React.CSSProperties = {
  display: "block",
  fontSize: 12,
  color: "#94a3b8",
  marginBottom: 4,
};

const INPUT: React.CSSProperties = {
  width: "100%",
  padding: "6px 10px",
  border: "1px solid #4a5568",
  borderRadius: 4,
  background: "#232738",
  color: "#e2e8f0",
  fontSize: 13,
  boxSizing: "border-box" as const,
};

const ROW: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 12,
};

const BTN: React.CSSProperties = {
  padding: "8px 16px",
  border: "1px solid #4a5568",
  borderRadius: 6,
  background: "#2d3148",
  color: "#e2e8f0",
  cursor: "pointer",
  fontSize: 13,
  fontWeight: 600,
};

const BTN_PRIMARY: React.CSSProperties = {
  ...BTN,
  background: "#3b82f6",
  borderColor: "#3b82f6",
};

const SECTION: React.CSSProperties = {
  fontSize: 13,
  fontWeight: 600,
  color: "#94a3b8",
  marginTop: 16,
  marginBottom: 8,
  paddingBottom: 4,
  borderBottom: "1px solid #2d3148",
};

// ------------------------------------------------------------------ component

interface Props {
  onClose: () => void;
  onApplied: () => void;
}

export function PresetConfigurator({ onClose, onApplied }: Props) {
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState("");

  // Grid
  const [zoneRows, setZoneRows] = useState(20);
  const [zoneCols, setZoneCols] = useState(30);

  // Robots
  const [a42tdCount, setA42tdCount] = useState(3);
  const [k50hCount, setK50hCount] = useState(2);

  // Racks
  const [rackRowStart, setRackRowStart] = useState(2);
  const [rackRowEnd, setRackRowEnd] = useState(8);
  const [rackColStart, setRackColStart] = useState(2);
  const [rackColEnd, setRackColEnd] = useState(12);

  // Cantilever
  const [cantileverRow, setCantileverRow] = useState(9);

  // Stations
  const [stationCount, setStationCount] = useState(2);

  // Inventory
  const [totes, setTotes] = useState(20);
  const [skuCount, setSkuCount] = useState(10);

  // Speed
  const [speed, setSpeed] = useState(1.0);

  const handleApply = useCallback(async () => {
    setApplying(true);
    setError("");
    try {
      await essApi.simulationApplyCustomPreset({
        zone_rows: zoneRows,
        zone_cols: zoneCols,
        a42td_count: a42tdCount,
        k50h_count: k50hCount,
        rack_row_start: rackRowStart,
        rack_row_end: rackRowEnd,
        rack_col_start: rackColStart,
        rack_col_end: rackColEnd,
        cantilever_row: cantileverRow,
        station_count: stationCount,
        wes_driven: true,
        interactive_mode: true,
        orders_per_minute: 0,
        station_processing_ticks: 0,
        totes,
        sku_count: skuCount,
        speed,
      });
      onApplied();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to apply preset");
    } finally {
      setApplying(false);
    }
  }, [
    zoneRows, zoneCols, a42tdCount, k50hCount,
    rackRowStart, rackRowEnd, rackColStart, rackColEnd,
    cantileverRow, stationCount, totes, skuCount, speed, onApplied,
  ]);

  return (
    <div style={OVERLAY} onClick={onClose}>
      <div style={DIALOG} onClick={(e) => e.stopPropagation()}>
        <h2 style={TITLE}>Custom Preset Configurator</h2>

        {/* Grid size */}
        <div style={SECTION}>Grid Size</div>
        <div style={ROW}>
          <div style={FIELD}>
            <label style={LABEL}>Rows</label>
            <input
              style={INPUT}
              type="number"
              min={10}
              max={120}
              value={zoneRows}
              onChange={(e) => setZoneRows(+e.target.value)}
            />
          </div>
          <div style={FIELD}>
            <label style={LABEL}>Columns</label>
            <input
              style={INPUT}
              type="number"
              min={10}
              max={120}
              value={zoneCols}
              onChange={(e) => setZoneCols(+e.target.value)}
            />
          </div>
        </div>

        {/* Robots */}
        <div style={SECTION}>Robots</div>
        <div style={ROW}>
          <div style={FIELD}>
            <label style={LABEL}>A42TD (rack robots)</label>
            <input
              style={INPUT}
              type="number"
              min={0}
              max={60}
              value={a42tdCount}
              onChange={(e) => setA42tdCount(+e.target.value)}
            />
          </div>
          <div style={FIELD}>
            <label style={LABEL}>K50H (shuttle robots)</label>
            <input
              style={INPUT}
              type="number"
              min={0}
              max={60}
              value={k50hCount}
              onChange={(e) => setK50hCount(+e.target.value)}
            />
          </div>
        </div>

        {/* Rack area */}
        <div style={SECTION}>Rack Area</div>
        <div style={ROW}>
          <div style={FIELD}>
            <label style={LABEL}>Row start</label>
            <input
              style={INPUT}
              type="number"
              min={1}
              value={rackRowStart}
              onChange={(e) => setRackRowStart(+e.target.value)}
            />
          </div>
          <div style={FIELD}>
            <label style={LABEL}>Row end</label>
            <input
              style={INPUT}
              type="number"
              min={2}
              value={rackRowEnd}
              onChange={(e) => setRackRowEnd(+e.target.value)}
            />
          </div>
        </div>
        <div style={ROW}>
          <div style={FIELD}>
            <label style={LABEL}>Col start</label>
            <input
              style={INPUT}
              type="number"
              min={1}
              value={rackColStart}
              onChange={(e) => setRackColStart(+e.target.value)}
            />
          </div>
          <div style={FIELD}>
            <label style={LABEL}>Col end</label>
            <input
              style={INPUT}
              type="number"
              min={2}
              value={rackColEnd}
              onChange={(e) => setRackColEnd(+e.target.value)}
            />
          </div>
        </div>

        {/* Cantilever */}
        <div style={FIELD}>
          <label style={LABEL}>Cantilever row</label>
          <input
            style={INPUT}
            type="number"
            min={1}
            value={cantileverRow}
            onChange={(e) => setCantileverRow(+e.target.value)}
          />
        </div>

        {/* Stations */}
        <div style={SECTION}>Stations</div>
        <div style={FIELD}>
          <label style={LABEL}>Station count</label>
          <input
            style={INPUT}
            type="number"
            min={0}
            max={10}
            value={stationCount}
            onChange={(e) => setStationCount(+e.target.value)}
          />
        </div>

        {/* Inventory */}
        <div style={SECTION}>Inventory</div>
        <div style={ROW}>
          <div style={FIELD}>
            <label style={LABEL}>Tote count</label>
            <input
              style={INPUT}
              type="number"
              min={1}
              max={500}
              value={totes}
              onChange={(e) => setTotes(+e.target.value)}
            />
          </div>
          <div style={FIELD}>
            <label style={LABEL}>SKU variety</label>
            <input
              style={INPUT}
              type="number"
              min={1}
              max={100}
              value={skuCount}
              onChange={(e) => setSkuCount(+e.target.value)}
            />
          </div>
        </div>

        {/* Speed */}
        <div style={FIELD}>
          <label style={LABEL}>Simulation speed: {speed.toFixed(1)}x</label>
          <input
            type="range"
            min={0.5}
            max={10}
            step={0.5}
            value={speed}
            onChange={(e) => setSpeed(+e.target.value)}
            style={{ width: "100%", accentColor: "#3b82f6" }}
          />
        </div>

        {/* Error */}
        {error && (
          <div style={{ padding: "8px 12px", background: "#7f1d1d", border: "1px solid #991b1b", borderRadius: 6, fontSize: 12, color: "#fecaca", marginTop: 12 }}>
            {error}
          </div>
        )}

        {/* Actions */}
        <div
          style={{
            display: "flex",
            justifyContent: "flex-end",
            gap: 8,
            marginTop: 20,
          }}
        >
          <button style={BTN} onClick={onClose}>
            Cancel
          </button>
          <button
            style={BTN_PRIMARY}
            onClick={handleApply}
            disabled={applying}
          >
            {applying ? "Applying..." : "Apply Preset"}
          </button>
        </div>
      </div>
    </div>
  );
}

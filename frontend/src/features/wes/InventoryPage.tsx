import { useState, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useTotes, useInventory } from "../../api/hooks";
import { wesApi } from "../../api/wes";

const BAND_FILTERS = ["ALL", "A", "B", "C", "D", "E", "F"] as const;
const STATUS_FILTERS = ["ALL", "STORED", "IN_TRANSIT", "AT_STATION", "RETURNING"] as const;

const STATUS_COLORS: Record<string, string> = {
  STORED: "#22c55e",
  IN_TRANSIT: "#3b82f6",
  AT_STATION: "#eab308",
  RETURNING: "#a855f7",
};

const BAND_COLORS: Record<string, string> = {
  A: "#ef4444",
  B: "#f97316",
  C: "#eab308",
  D: "#22c55e",
  E: "#3b82f6",
  F: "#8b5cf6",
};

type ViewMode = "summary" | "totes";

export default function InventoryPage() {
  const queryClient = useQueryClient();
  const [view, setView] = useState<ViewMode>("summary");
  const [search, setSearch] = useState("");
  const [bandFilter, setBandFilter] = useState("ALL");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [seeding, setSeeding] = useState(false);

  const { data: inventory } = useInventory();
  const { data: totes, isLoading } = useTotes({
    barcode: search || undefined,
    band: bandFilter === "ALL" ? undefined : bandFilter,
    status: statusFilter === "ALL" ? undefined : statusFilter,
  });

  const handleSeed = useCallback(
    async (preset: string) => {
      setSeeding(true);
      try {
        await wesApi.seedInventory(preset);
        queryClient.invalidateQueries({ queryKey: ["totes"] });
        queryClient.invalidateQueries({ queryKey: ["inventory"] });
        queryClient.invalidateQueries({ queryKey: ["orders"] });
      } finally {
        setSeeding(false);
      }
    },
    [queryClient],
  );

  const isEmpty = !isLoading && (!totes || totes.length === 0) && !search && bandFilter === "ALL" && statusFilter === "ALL";

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Inventory</h1>

      {/* View toggle */}
      <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
        <button
          onClick={() => setView("summary")}
          style={{
            padding: "5px 16px",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: view === "summary" ? "var(--accent-blue)" : "var(--bg-card)",
            color: "var(--text-primary)",
            cursor: "pointer",
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          SKU Summary
        </button>
        <button
          onClick={() => setView("totes")}
          style={{
            padding: "5px 16px",
            borderRadius: 6,
            border: "1px solid var(--border)",
            background: view === "totes" ? "var(--accent-blue)" : "var(--bg-card)",
            color: "var(--text-primary)",
            cursor: "pointer",
            fontSize: 13,
            fontWeight: 600,
          }}
        >
          Totes (LPN)
        </button>
      </div>

      {/* Seed buttons when empty */}
      {isEmpty && (
        <div
          style={{
            padding: 32,
            textAlign: "center",
            background: "var(--bg-card)",
            borderRadius: 8,
            border: "1px solid var(--border)",
            marginBottom: 16,
          }}
        >
          <p style={{ color: "var(--text-secondary)", marginBottom: 16, fontSize: 14 }}>
            No inventory found. Seed some totes to get started.
          </p>
          <div style={{ display: "flex", gap: 10, justifyContent: "center" }}>
            {(["small", "medium", "large"] as const).map((preset) => (
              <button
                key={preset}
                onClick={() => handleSeed(preset)}
                disabled={seeding}
                style={{
                  padding: "8px 20px",
                  borderRadius: 6,
                  border: "none",
                  background: "var(--accent-blue)",
                  color: "#fff",
                  cursor: seeding ? "not-allowed" : "pointer",
                  fontSize: 13,
                  fontWeight: 600,
                  opacity: seeding ? 0.6 : 1,
                  textTransform: "capitalize",
                }}
              >
                {seeding ? "..." : `Seed ${preset}`}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* =============== SKU Summary View =============== */}
      {view === "summary" && inventory && inventory.length > 0 && (
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
              <th style={{ padding: 8 }}>SKU</th>
              <th style={{ padding: 8 }}>Name</th>
              <th style={{ padding: 8 }}>Band</th>
              <th style={{ padding: 8, textAlign: "right" }}>Total</th>
              <th style={{ padding: 8, textAlign: "right" }}>Allocated</th>
              <th style={{ padding: 8, textAlign: "right" }}>Available</th>
              <th style={{ padding: 8 }}>Utilization</th>
            </tr>
          </thead>
          <tbody>
            {inventory.map((inv) => {
              const available = inv.total_qty - inv.allocated_qty;
              const pct = inv.total_qty > 0 ? (inv.allocated_qty / inv.total_qty) * 100 : 0;
              return (
                <tr key={inv.id} style={{ borderBottom: "1px solid var(--border)" }}>
                  <td style={{ padding: 8, fontFamily: "var(--font-mono)", fontSize: 13 }}>
                    {inv.sku}
                  </td>
                  <td style={{ padding: 8, fontSize: 13 }}>{inv.sku_name ?? "-"}</td>
                  <td style={{ padding: 8 }}>
                    {inv.band ? (
                      <span
                        style={{
                          display: "inline-block",
                          width: 22,
                          height: 22,
                          lineHeight: "22px",
                          textAlign: "center",
                          borderRadius: 4,
                          fontSize: 12,
                          fontWeight: 700,
                          background: BAND_COLORS[inv.band] ?? "#666",
                          color: "#fff",
                        }}
                      >
                        {inv.band}
                      </span>
                    ) : "-"}
                  </td>
                  <td style={{ padding: 8, textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {inv.total_qty}
                  </td>
                  <td style={{ padding: 8, textAlign: "right", fontFamily: "var(--font-mono)", color: inv.allocated_qty > 0 ? "#f97316" : "var(--text-secondary)" }}>
                    {inv.allocated_qty}
                  </td>
                  <td style={{ padding: 8, textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600, color: available <= 0 ? "#ef4444" : "#22c55e" }}>
                    {available}
                  </td>
                  <td style={{ padding: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <div style={{ width: 60, height: 6, background: "var(--bg-secondary)", borderRadius: 3, overflow: "hidden" }}>
                        <div
                          style={{
                            width: `${pct}%`,
                            height: "100%",
                            background: pct > 80 ? "#ef4444" : pct > 50 ? "#f97316" : "#22c55e",
                            borderRadius: 3,
                          }}
                        />
                      </div>
                      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{Math.round(pct)}%</span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {view === "summary" && (!inventory || inventory.length === 0) && !isEmpty && (
        <p style={{ textAlign: "center", padding: 32, color: "var(--text-secondary)" }}>
          No inventory data
        </p>
      )}

      {/* =============== Totes View =============== */}
      {view === "totes" && (
        <>
          {/* Search bar */}
          <div style={{ marginBottom: 12 }}>
            <input
              type="text"
              placeholder="Search by barcode..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{
                width: 300,
                padding: "6px 10px",
                borderRadius: 6,
                border: "1px solid var(--border)",
                background: "var(--bg-page)",
                color: "var(--text-primary)",
                fontSize: 13,
              }}
            />
          </div>

          {/* Band filter */}
          <div style={{ display: "flex", gap: 6, marginBottom: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: "28px", marginRight: 4 }}>
              Band:
            </span>
            {BAND_FILTERS.map((b) => (
              <button
                key={b}
                onClick={() => setBandFilter(b)}
                style={{
                  padding: "4px 12px",
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: bandFilter === b ? (b === "ALL" ? "var(--accent-blue)" : BAND_COLORS[b] ?? "var(--accent-blue)") : "var(--bg-card)",
                  color: bandFilter === b ? "#fff" : "var(--text-primary)",
                  cursor: "pointer",
                  fontSize: 12,
                  fontWeight: 600,
                }}
              >
                {b}
              </button>
            ))}
          </div>

          {/* Status filter */}
          <div style={{ display: "flex", gap: 6, marginBottom: 16 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: "28px", marginRight: 4 }}>
              Status:
            </span>
            {STATUS_FILTERS.map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                style={{
                  padding: "4px 12px",
                  borderRadius: 6,
                  border: "1px solid var(--border)",
                  background: statusFilter === s ? (STATUS_COLORS[s] ?? "var(--accent-blue)") : "var(--bg-card)",
                  color: statusFilter === s ? "#fff" : "var(--text-primary)",
                  cursor: "pointer",
                  fontSize: 12,
                }}
              >
                {s}
              </button>
            ))}
          </div>

          {isLoading && <p>Loading...</p>}

          {totes && totes.length > 0 && (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
                  <th style={{ padding: 8 }}>LPN</th>
                  <th style={{ padding: 8 }}>SKU ID</th>
                  <th style={{ padding: 8 }}>Band</th>
                  <th style={{ padding: 8 }}>SKU Name</th>
                  <th style={{ padding: 8 }}>Qty</th>
                  <th style={{ padding: 8 }}>Location</th>
                  <th style={{ padding: 8 }}>Status</th>
                </tr>
              </thead>
              <tbody>
                {totes.map((tote) => (
                  <tr key={tote.id} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: 8, fontFamily: "var(--font-mono)", fontSize: 13 }}>
                      {tote.barcode}
                    </td>
                    <td style={{ padding: 8 }}>{tote.sku ?? "-"}</td>
                    <td style={{ padding: 8 }}>
                      {tote.band ? (
                        <span
                          style={{
                            display: "inline-block",
                            width: 22,
                            height: 22,
                            lineHeight: "22px",
                            textAlign: "center",
                            borderRadius: 4,
                            fontSize: 12,
                            fontWeight: 700,
                            background: BAND_COLORS[tote.band] ?? "#666",
                            color: "#fff",
                          }}
                        >
                          {tote.band}
                        </span>
                      ) : (
                        "-"
                      )}
                    </td>
                    <td style={{ padding: 8, fontSize: 13 }}>{tote.sku_name ?? "-"}</td>
                    <td style={{ padding: 8 }}>{tote.quantity}</td>
                    <td style={{ padding: 8, fontFamily: "var(--font-mono)", fontSize: 12 }}>
                      {tote.location_label ?? "Virtual"}
                    </td>
                    <td style={{ padding: 8 }}>
                      <span
                        style={{
                          padding: "2px 10px",
                          borderRadius: 12,
                          fontSize: 12,
                          fontWeight: 600,
                          background: STATUS_COLORS[tote.status] ?? "#666",
                          color: "#fff",
                        }}
                      >
                        {tote.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          {!isEmpty && totes && totes.length === 0 && (
            <p style={{ textAlign: "center", padding: 32, color: "var(--text-secondary)" }}>
              No totes match the current filters
            </p>
          )}
        </>
      )}
    </div>
  );
}

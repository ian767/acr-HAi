import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useOrders, useAllocateOrder } from "../../api/hooks";
import { wesApi } from "../../api/wes";

const STATUS_COLORS: Record<string, string> = {
  NEW: "#3b82f6",
  FAILED: "#ef4444",
  ALLOCATED: "#a855f7",
  IN_PROGRESS: "#f97316",
  COMPLETED: "#22c55e",
  CANCELLED: "#ef4444",
};

const ORDER_PRESETS = [
  { label: "Single Order", description: "Random SKU, qty 1-3", count: 1, priorityRange: [1, 5] as const },
  { label: "Small Batch", description: "5 random orders", count: 5, priorityRange: [1, 5] as const },
  { label: "Rush Orders", description: "3 high-priority orders", count: 3, priorityRange: [8, 10] as const },
  { label: "Bulk Load", description: "10 mixed orders", count: 10, priorityRange: [1, 10] as const },
  { label: "Same-SKU Batch", description: "5 orders, same SKU", count: 5, priorityRange: [1, 5] as const, sameSku: true },
] as const;

const FALLBACK_SKUS = ["SKU-001", "SKU-002", "SKU-003", "SKU-004", "SKU-005"];

function randInt(min: number, max: number) {
  return Math.floor(Math.random() * (max - min + 1)) + min;
}

export default function OrderManagementPage() {
  const queryClient = useQueryClient();
  const { data: orders } = useOrders();
  const allocate = useAllocateOrder();

  const [skus, setSkus] = useState<Array<{ sku: string; available_qty: number }>>([]);
  const [sku, setSku] = useState("");
  const [qty, setQty] = useState(1);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const [presetBusy, setPresetBusy] = useState(false);

  useEffect(() => {
    wesApi.getAvailableSkus().then(setSkus).catch(() => {});
  }, []);

  const handleCreate = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!sku) return;
      setCreateError("");
      setCreating(true);
      try {
        await wesApi.createOrder({ sku, quantity: qty });
        setQty(1);
        queryClient.invalidateQueries({ queryKey: ["orders"] });
      } catch (err: unknown) {
        setCreateError(err instanceof Error ? err.message : "Failed");
      } finally {
        setCreating(false);
      }
    },
    [sku, qty, queryClient],
  );

  const recentOrders = orders?.slice(0, 10) ?? [];

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Create Orders</h1>

      {/* Warning when no inventory loaded */}
      {skus.length === 0 && (
        <div
          style={{
            padding: "10px 14px",
            marginBottom: 12,
            background: "#78350f",
            border: "1px solid #92400e",
            borderRadius: 8,
            fontSize: 13,
            color: "#fef3c7",
          }}
        >
          No inventory loaded — orders won't allocate until a preset or inventory seed is applied.
        </div>
      )}

      {/* Quick create form */}
      <form
        onSubmit={handleCreate}
        style={{
          display: "flex",
          gap: 8,
          alignItems: "flex-end",
          marginBottom: 20,
          padding: 14,
          background: "var(--bg-card)",
          borderRadius: 8,
          border: "1px solid var(--border)",
        }}
      >
        <div style={{ flex: 2 }}>
          <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "block", marginBottom: 3 }}>
            SKU
          </label>
          {skus.length > 0 ? (
            <select
              value={sku}
              onChange={(e) => setSku(e.target.value)}
              required
              style={{
                width: "100%",
                padding: "6px 8px",
                borderRadius: 4,
                border: "1px solid var(--border)",
                background: "var(--bg-page)",
                color: "var(--text-primary)",
                fontSize: 13,
              }}
            >
              <option value="">-- Select SKU --</option>
              {skus.map((s) => (
                <option key={s.sku} value={s.sku}>
                  {s.sku} ({s.available_qty} avail)
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={sku}
              onChange={(e) => setSku(e.target.value)}
              placeholder="e.g. SKU-001"
              required
              style={{
                width: "100%",
                padding: "6px 8px",
                borderRadius: 4,
                border: "1px solid var(--border)",
                background: "var(--bg-page)",
                color: "var(--text-primary)",
                fontSize: 13,
              }}
            />
          )}
        </div>
        <div style={{ flex: 0, width: 70 }}>
          <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "block", marginBottom: 3 }}>
            Qty
          </label>
          <input
            type="number"
            min={1}
            max={20}
            value={qty}
            onChange={(e) => setQty(Math.max(1, parseInt(e.target.value) || 1))}
            style={{
              width: "100%",
              padding: "6px 8px",
              borderRadius: 4,
              border: "1px solid var(--border)",
              background: "var(--bg-page)",
              color: "var(--text-primary)",
              fontSize: 13,
            }}
          />
        </div>
        <button
          type="submit"
          disabled={creating}
          style={{
            padding: "6px 18px",
            borderRadius: 6,
            border: "none",
            background: "var(--accent-blue)",
            color: "#fff",
            cursor: "pointer",
            fontSize: 13,
            fontWeight: 600,
            opacity: creating ? 0.6 : 1,
          }}
        >
          {creating ? "..." : "Create Order"}
        </button>
      </form>

      {/* Error display */}
      {createError && (
        <div
          style={{
            padding: "10px 14px",
            marginBottom: 12,
            background: "#7f1d1d",
            border: "1px solid #991b1b",
            borderRadius: 8,
            fontSize: 13,
            color: "#fecaca",
          }}
        >
          {createError}
        </div>
      )}

      {/* Quick Create presets */}
      <div
        style={{
          display: "flex",
          gap: 8,
          alignItems: "center",
          marginBottom: 24,
          padding: "10px 14px",
          background: "var(--bg-card)",
          borderRadius: 8,
          border: "1px solid var(--border)",
          flexWrap: "wrap",
        }}
      >
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginRight: 4 }}>
          Quick Create:
        </span>
        {ORDER_PRESETS.map((preset) => (
          <button
            key={preset.label}
            title={preset.description}
            disabled={presetBusy}
            onClick={async () => {
              setPresetBusy(true);
              setCreateError("");
              try {
                const skuList: string[] = skus.length > 0 ? skus.map((s) => s.sku) : [...FALLBACK_SKUS];
                const pick = () => skuList[randInt(0, skuList.length - 1)]!;
                const fixedSku = pick();
                const promises = Array.from({ length: preset.count }, () => {
                  const orderSku = "sameSku" in preset && preset.sameSku ? fixedSku : pick();
                  return wesApi.createOrder({
                    sku: orderSku,
                    quantity: randInt(1, 3),
                    priority: randInt(preset.priorityRange[0], preset.priorityRange[1]),
                  });
                });
                await Promise.allSettled(promises);
                queryClient.invalidateQueries({ queryKey: ["orders"] });
              } catch (err: unknown) {
                setCreateError(err instanceof Error ? err.message : "Preset failed");
              } finally {
                setPresetBusy(false);
              }
            }}
            style={{
              padding: "5px 14px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: "var(--bg-page)",
              color: "var(--text-primary)",
              cursor: presetBusy ? "not-allowed" : "pointer",
              fontSize: 12,
              opacity: presetBusy ? 0.6 : 1,
            }}
          >
            {preset.label}
          </button>
        ))}
      </div>

      {/* Recent Orders */}
      <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 12 }}>Recent Orders</h2>

      {recentOrders.length === 0 ? (
        <p style={{ textAlign: "center", padding: 32, color: "var(--text-secondary)" }}>
          No orders yet — create one above
        </p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {recentOrders.map((order) => (
            <div
              key={order.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "8px 14px",
                background: "var(--bg-card)",
                borderRadius: 8,
                border: "1px solid var(--border)",
                fontSize: 13,
              }}
            >
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, minWidth: 100 }}>
                {order.external_id}
              </span>
              <span style={{ minWidth: 70 }}>{order.sku}</span>
              <span style={{ color: "var(--text-secondary)", minWidth: 30 }}>x{order.quantity}</span>
              <span
                style={{
                  padding: "2px 10px",
                  borderRadius: 12,
                  fontSize: 11,
                  fontWeight: 600,
                  background: STATUS_COLORS[order.status] ?? "#666",
                  color: "#fff",
                }}
              >
                {order.status}
              </span>
              <span style={{ flex: 1 }} />
              {order.status === "NEW" && (
                <button
                  onClick={() => allocate.mutate(order.id)}
                  disabled={allocate.isPending}
                  style={{
                    padding: "3px 12px",
                    borderRadius: 4,
                    border: "none",
                    background: "var(--accent-blue)",
                    color: "#fff",
                    cursor: "pointer",
                    fontSize: 11,
                  }}
                >
                  Allocate
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

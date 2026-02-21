import { useCallback, useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { wesApi } from "@/api/wes";
import type { Order } from "@/types/order";
import type { PickTask } from "@/types/pickTask";

// ------------------------------------------------------------------ styles

const SECTION: React.CSSProperties = {
  marginBottom: 16,
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

const BTN_CREATE: React.CSSProperties = {
  width: "100%",
  padding: "8px 0",
  border: "none",
  borderRadius: 6,
  background: "#3b82f6",
  color: "#fff",
  cursor: "pointer",
  fontSize: 13,
  fontWeight: 600,
};

const STATUS_COLORS: Record<string, string> = {
  NEW: "#3b82f6",
  FAILED: "#ef4444",
  ALLOCATED: "#a855f7",
  IN_PROGRESS: "#f97316",
  COMPLETED: "#22c55e",
  CANCELLED: "#ef4444",
};

const TASK_STATE_LABEL: Record<string, string> = {
  SOURCE_REQUESTED: "Fetching tote",
  SOURCE_AT_CANTILEVER: "At cantilever",
  SOURCE_AT_STATION: "Ready to scan",
  PICKING: "Scanning",
  RETURN_REQUESTED: "Returning",
  RETURN_AT_CANTILEVER: "Return at cantilever",
  COMPLETED: "Done",
};

// ------------------------------------------------------------------ component

export function OrderCreatePanel() {
  const queryClient = useQueryClient();
  const [skus, setSkus] = useState<Array<{ sku: string; available_qty: number }>>([]);
  const [sku, setSku] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // Live order + pick task tracking
  const [orders, setOrders] = useState<Order[]>([]);
  const [pickTasks, setPickTasks] = useState<PickTask[]>([]);

  // Load SKUs
  useEffect(() => {
    const load = () => wesApi.getAvailableSkus().then(setSkus).catch(() => {});
    load();
    const id = setInterval(load, 8000);
    return () => clearInterval(id);
  }, []);

  // Poll orders + pick tasks
  useEffect(() => {
    const load = () => {
      wesApi.listOrders({ limit: 20 }).then(setOrders).catch(() => {});
      wesApi.listPickTasks().then(setPickTasks).catch(() => {});
    };
    load();
    const id = setInterval(load, 2000);
    return () => clearInterval(id);
  }, []);

  const handleCreate = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!sku) return;
      setError("");
      setBusy(true);
      try {
        await wesApi.createOrder({ sku, quantity });
        setQuantity(1);
        // Immediately refresh orders list
        wesApi.listOrders({ limit: 20 }).then(setOrders).catch(() => {});
        // Also invalidate the Orders page cache so it stays in sync
        queryClient.invalidateQueries({ queryKey: ["orders"] });
      } catch (err: any) {
        setError(err.message || "Failed to create order");
      } finally {
        setBusy(false);
      }
    },
    [sku, quantity, queryClient],
  );

  // Find pick task for a given order
  const taskForOrder = (orderId: string) =>
    pickTasks.find((t) => t.order_id === orderId);

  const activeOrders = orders.filter((o) => o.status !== "COMPLETED" && o.status !== "CANCELLED");
  const completedOrders = orders.filter((o) => o.status === "COMPLETED");

  return (
    <div>
      {/* ---- Create Order Form ---- */}
      <div style={SECTION}>
        <h4 style={{ margin: "0 0 10px", fontSize: 14, color: "#e2e8f0" }}>
          Create Order
        </h4>
        <form onSubmit={handleCreate}>
          <div style={{ marginBottom: 8 }}>
            <label style={{ fontSize: 11, color: "#94a3b8", display: "block", marginBottom: 3 }}>
              SKU
            </label>
            <select style={INPUT} value={sku} onChange={(e) => setSku(e.target.value)} required>
              <option value="">-- Select SKU --</option>
              {skus.map((s) => (
                <option key={s.sku} value={s.sku}>
                  {s.sku} ({s.available_qty} available)
                </option>
              ))}
            </select>
          </div>
          <div style={{ marginBottom: 10 }}>
            <label style={{ fontSize: 11, color: "#94a3b8", display: "block", marginBottom: 3 }}>
              Quantity
            </label>
            <input
              type="number"
              min={1}
              max={20}
              value={quantity}
              onChange={(e) => setQuantity(Math.max(1, parseInt(e.target.value) || 1))}
              style={INPUT}
            />
          </div>
          <button type="submit" style={{ ...BTN_CREATE, opacity: busy ? 0.6 : 1 }} disabled={busy}>
            {busy ? "Creating..." : "Create Order"}
          </button>
        </form>
        {error && (
          <div style={{ fontSize: 12, color: "#ef4444", marginTop: 6 }}>{error}</div>
        )}
      </div>

      <div style={{ height: 1, background: "#2d3148", margin: "12px 0" }} />

      {/* ---- Active Orders ---- */}
      <div style={SECTION}>
        <h4 style={{ margin: "0 0 8px", fontSize: 14, color: "#e2e8f0" }}>
          Active Orders
          {activeOrders.length > 0 && (
            <span style={{ fontWeight: 400, fontSize: 12, color: "#94a3b8", marginLeft: 6 }}>
              ({activeOrders.length})
            </span>
          )}
        </h4>

        {activeOrders.length === 0 && (
          <p style={{ fontSize: 12, color: "#64748b", margin: 0 }}>
            No active orders. Create one above.
          </p>
        )}

        {activeOrders.map((order) => {
          const task = taskForOrder(order.id);
          return (
            <OrderCard key={order.id} order={order} pickTask={task ?? null} />
          );
        })}
      </div>

      {/* ---- Completed ---- */}
      {completedOrders.length > 0 && (
        <div style={{ fontSize: 11, color: "#64748b" }}>
          {completedOrders.length} order(s) completed
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ OrderCard

function OrderCard({ order, pickTask }: { order: Order; pickTask: PickTask | null }) {
  const taskState = pickTask?.state;
  const isAtStation = taskState === "SOURCE_AT_STATION" || taskState === "PICKING";
  const pct = pickTask && pickTask.qty_to_pick > 0
    ? Math.round((pickTask.qty_picked / pickTask.qty_to_pick) * 100)
    : 0;

  return (
    <div
      style={{
        border: `1px solid ${isAtStation ? "#22c55e44" : "#2d3148"}`,
        borderRadius: 6,
        padding: 10,
        marginBottom: 8,
        background: isAtStation ? "#22c55e08" : "#1e2235",
      }}
    >
      {/* Header: external_id + status */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "#e2e8f0" }}>
          {order.external_id}
        </span>
        <span
          style={{
            padding: "1px 8px",
            borderRadius: 10,
            fontSize: 10,
            fontWeight: 600,
            background: STATUS_COLORS[order.status] ?? "#666",
            color: "#fff",
          }}
        >
          {order.status}
        </span>
      </div>

      {/* SKU + Qty */}
      <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>
        {order.sku} x {order.quantity}
      </div>

      {/* Pick task state */}
      {pickTask && (
        <div style={{ marginTop: 6 }}>
          <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 3 }}>
            {TASK_STATE_LABEL[pickTask.state] ?? pickTask.state}
            {(pickTask.state === "PICKING" || pickTask.state === "SOURCE_AT_STATION") && (
              <span style={{ float: "right", color: "#e2e8f0" }}>
                {pickTask.qty_picked}/{pickTask.qty_to_pick}
              </span>
            )}
          </div>
          {/* Progress bar */}
          <div style={{ height: 3, background: "#2d3148", borderRadius: 2, overflow: "hidden" }}>
            <div
              style={{
                height: "100%",
                width: `${pct}%`,
                background: isAtStation ? "#22c55e" : "#3b82f6",
                borderRadius: 2,
                transition: "width 0.3s",
              }}
            />
          </div>
        </div>
      )}
    </div>
  );
}

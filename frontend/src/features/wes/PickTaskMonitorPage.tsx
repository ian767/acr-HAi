import { useState, useMemo } from "react";
import { useOrders, usePickTasks } from "../../api/hooks";
import type { PickTaskState } from "../../types/pickTask";
import type { Order } from "../../types/order";
import type { PickTask } from "../../types/pickTask";

const STATE_FLOW: PickTaskState[] = [
  "SOURCE_REQUESTED",
  "SOURCE_AT_CANTILEVER",
  "SOURCE_AT_STATION",
  "PICKING",
  "RETURN_REQUESTED",
  "RETURN_AT_CANTILEVER",
  "COMPLETED",
];

const STATE_COLORS: Record<string, string> = {
  SOURCE_REQUESTED: "#3b82f6",
  SOURCE_AT_CANTILEVER: "#8b5cf6",
  SOURCE_AT_STATION: "#a855f7",
  PICKING: "#f97316",
  RETURN_REQUESTED: "#eab308",
  RETURN_AT_CANTILEVER: "#14b8a6",
  COMPLETED: "#22c55e",
};

const ORDER_STATUS_COLORS: Record<string, string> = {
  NEW: "#3b82f6",
  FAILED: "#ef4444",
  ALLOCATED: "#a855f7",
  IN_PROGRESS: "#f97316",
  COMPLETED: "#22c55e",
  CANCELLED: "#ef4444",
};

type OrderFilter = "ALL" | "ALLOCATED" | "IN_PROGRESS" | "COMPLETED";
const ORDER_FILTERS: OrderFilter[] = ["ALL", "ALLOCATED", "IN_PROGRESS", "COMPLETED"];

interface JoinedRow {
  order: Order;
  pickTask: PickTask | null;
}

export default function PickTaskMonitorPage() {
  const [stateFilter, setStateFilter] = useState<string>("ALL");
  const [orderFilter, setOrderFilter] = useState<OrderFilter>("ALL");

  const { data: allTasks } = usePickTasks();
  const { data: allOrders } = useOrders();

  // Client-side join: order + pick tasks
  const rows = useMemo(() => {
    if (!allOrders) return [];
    const orderMap = new Map<string, Order>();
    for (const o of allOrders) orderMap.set(o.id, o);

    const tasksByOrder = new Map<string, PickTask[]>();
    if (allTasks) {
      for (const t of allTasks) {
        const arr = tasksByOrder.get(t.order_id) ?? [];
        arr.push(t);
        tasksByOrder.set(t.order_id, arr);
      }
    }

    const joined: JoinedRow[] = [];
    for (const order of allOrders) {
      // Order status filter
      if (orderFilter !== "ALL" && order.status !== orderFilter) continue;

      const tasks = tasksByOrder.get(order.id);
      if (tasks && tasks.length > 0) {
        for (const t of tasks) {
          // Pick state filter
          if (stateFilter !== "ALL" && t.state !== stateFilter) continue;
          joined.push({ order, pickTask: t });
        }
      } else {
        // Order with no pick tasks (e.g. NEW/ALLOCATED but not yet dispatched)
        if (stateFilter === "ALL") {
          joined.push({ order, pickTask: null });
        }
      }
    }
    return joined;
  }, [allOrders, allTasks, orderFilter, stateFilter]);

  // Count pick tasks per state (unfiltered)
  const stateCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    if (allTasks) {
      for (const t of allTasks) {
        counts[t.state] = (counts[t.state] ?? 0) + 1;
      }
    }
    return counts;
  }, [allTasks]);

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Order Fulfillment</h1>

      {/* State machine flow visualization */}
      <div
        style={{
          display: "flex",
          gap: 4,
          alignItems: "center",
          marginBottom: 16,
          padding: 12,
          background: "var(--bg-card)",
          borderRadius: "var(--radius)",
          overflowX: "auto",
        }}
      >
        <button
          onClick={() => setStateFilter("ALL")}
          style={{
            padding: "6px 10px",
            borderRadius: 6,
            border: stateFilter === "ALL" ? "2px solid #fff" : "1px solid var(--border)",
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            cursor: "pointer",
            fontSize: 11,
            fontWeight: stateFilter === "ALL" ? 700 : 400,
            marginRight: 4,
          }}
        >
          ALL ({allTasks?.length ?? 0})
        </button>
        {STATE_FLOW.map((state, i) => (
          <div key={state} style={{ display: "flex", alignItems: "center" }}>
            <button
              onClick={() => setStateFilter(stateFilter === state ? "ALL" : state)}
              style={{
                padding: "6px 10px",
                borderRadius: 6,
                border: stateFilter === state ? "2px solid #fff" : "1px solid var(--border)",
                background: STATE_COLORS[state],
                color: "#fff",
                cursor: "pointer",
                fontSize: 11,
                whiteSpace: "nowrap",
                fontWeight: stateFilter === state ? 700 : 400,
              }}
            >
              {state.replace(/_/g, " ")}
              <span style={{ marginLeft: 4, opacity: 0.8 }}>
                ({stateCounts[state] ?? 0})
              </span>
            </button>
            {i < STATE_FLOW.length - 1 && (
              <span style={{ margin: "0 2px", color: "var(--text-secondary)" }}>&rarr;</span>
            )}
          </div>
        ))}
      </div>

      {/* Order status filter */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {ORDER_FILTERS.map((f) => (
          <button
            key={f}
            onClick={() => setOrderFilter(f)}
            style={{
              padding: "5px 14px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: orderFilter === f ? "var(--accent-blue)" : "var(--bg-card)",
              color: "var(--text-primary)",
              cursor: "pointer",
              fontSize: 12,
            }}
          >
            {f}
          </button>
        ))}
      </div>

      {/* Unified table */}
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
            <th style={{ padding: 8 }}>Order</th>
            <th style={{ padding: 8 }}>SKU</th>
            <th style={{ padding: 8 }}>Qty</th>
            <th style={{ padding: 8 }}>Order Status</th>
            <th style={{ padding: 8 }}>Pick State</th>
            <th style={{ padding: 8 }}>Progress</th>
            <th style={{ padding: 8 }}>Updated</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={row.pickTask?.id ?? `${row.order.id}-${idx}`} style={{ borderBottom: "1px solid var(--border)" }}>
              <td style={{ padding: 8, fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {row.order.external_id}
              </td>
              <td style={{ padding: 8 }}>{row.pickTask?.sku ?? row.order.sku}</td>
              <td style={{ padding: 8 }}>
                {row.pickTask ? `${row.pickTask.qty_picked}/${row.pickTask.qty_to_pick}` : row.order.quantity}
              </td>
              <td style={{ padding: 8 }}>
                <span
                  style={{
                    padding: "2px 8px",
                    borderRadius: 10,
                    fontSize: 11,
                    fontWeight: 600,
                    background: ORDER_STATUS_COLORS[row.order.status] ?? "#666",
                    color: "#fff",
                  }}
                >
                  {row.order.status}
                </span>
              </td>
              <td style={{ padding: 8 }}>
                {row.pickTask ? (
                  <span
                    style={{
                      padding: "2px 8px",
                      borderRadius: 10,
                      fontSize: 11,
                      fontWeight: 600,
                      background: STATE_COLORS[row.pickTask.state] ?? "#666",
                      color: "#fff",
                    }}
                  >
                    {row.pickTask.state.replace(/_/g, " ")}
                  </span>
                ) : (
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>-</span>
                )}
              </td>
              <td style={{ padding: 8 }}>
                {row.pickTask ? (
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <div
                      style={{
                        width: 60,
                        height: 6,
                        background: "var(--bg-secondary)",
                        borderRadius: 3,
                        overflow: "hidden",
                      }}
                    >
                      <div
                        style={{
                          width: `${row.pickTask.qty_to_pick > 0 ? (row.pickTask.qty_picked / row.pickTask.qty_to_pick) * 100 : 0}%`,
                          height: "100%",
                          background: "var(--accent-green)",
                          borderRadius: 3,
                        }}
                      />
                    </div>
                  </div>
                ) : (
                  <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>-</span>
                )}
              </td>
              <td style={{ padding: 8, fontSize: 12, color: "var(--text-secondary)" }}>
                {row.pickTask
                  ? new Date(row.pickTask.updated_at).toLocaleTimeString()
                  : row.order.updated_at
                    ? new Date(row.order.updated_at).toLocaleTimeString()
                    : "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {rows.length === 0 && (
        <p style={{ textAlign: "center", padding: 32, color: "var(--text-secondary)" }}>
          No fulfillment data — create orders and apply a simulation preset to begin
        </p>
      )}
    </div>
  );
}

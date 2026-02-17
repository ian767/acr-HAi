import { useState } from "react";
import { useOrders, useAllocateOrder } from "../../api/hooks";
import type { OrderStatus } from "../../types/order";

const STATUS_COLORS: Record<string, string> = {
  NEW: "#3b82f6",
  ALLOCATING: "#eab308",
  ALLOCATED: "#a855f7",
  IN_PROGRESS: "#f97316",
  COMPLETED: "#22c55e",
  CANCELLED: "#ef4444",
};

const STATUSES: (OrderStatus | "ALL")[] = [
  "ALL",
  "NEW",
  "ALLOCATING",
  "ALLOCATED",
  "IN_PROGRESS",
  "COMPLETED",
  "CANCELLED",
];

export default function OrderManagementPage() {
  const [filter, setFilter] = useState<string>("ALL");
  const { data: orders, isLoading } = useOrders(
    filter === "ALL" ? undefined : { status: filter },
  );
  const allocate = useAllocateOrder();

  return (
    <div style={{ padding: 24 }}>
      <h1 style={{ fontSize: 24, marginBottom: 16 }}>Order Management</h1>

      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {STATUSES.map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            style={{
              padding: "6px 14px",
              borderRadius: 6,
              border: "1px solid var(--border)",
              background: filter === s ? "var(--accent-blue)" : "var(--bg-card)",
              color: "var(--text-primary)",
              cursor: "pointer",
              fontSize: 13,
            }}
          >
            {s}
          </button>
        ))}
      </div>

      {isLoading && <p>Loading...</p>}

      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr style={{ borderBottom: "1px solid var(--border)", textAlign: "left" }}>
            <th style={{ padding: 8 }}>External ID</th>
            <th style={{ padding: 8 }}>SKU</th>
            <th style={{ padding: 8 }}>Qty</th>
            <th style={{ padding: 8 }}>Priority</th>
            <th style={{ padding: 8 }}>Status</th>
            <th style={{ padding: 8 }}>Station</th>
            <th style={{ padding: 8 }}>Actions</th>
          </tr>
        </thead>
        <tbody>
          {orders?.map((order) => (
            <tr
              key={order.id}
              style={{ borderBottom: "1px solid var(--border)" }}
            >
              <td style={{ padding: 8, fontFamily: "var(--font-mono)", fontSize: 13 }}>
                {order.external_id}
              </td>
              <td style={{ padding: 8 }}>{order.sku}</td>
              <td style={{ padding: 8 }}>{order.quantity}</td>
              <td style={{ padding: 8 }}>{order.priority}</td>
              <td style={{ padding: 8 }}>
                <span
                  style={{
                    padding: "2px 10px",
                    borderRadius: 12,
                    fontSize: 12,
                    fontWeight: 600,
                    background: STATUS_COLORS[order.status] ?? "#666",
                    color: "#fff",
                  }}
                >
                  {order.status}
                </span>
              </td>
              <td style={{ padding: 8, fontFamily: "var(--font-mono)", fontSize: 12 }}>
                {order.station_id ? order.station_id.slice(0, 8) : "-"}
              </td>
              <td style={{ padding: 8 }}>
                {order.status === "NEW" && (
                  <button
                    onClick={() => allocate.mutate(order.id)}
                    disabled={allocate.isPending}
                    style={{
                      padding: "4px 12px",
                      borderRadius: 4,
                      border: "none",
                      background: "var(--accent-blue)",
                      color: "#fff",
                      cursor: "pointer",
                      fontSize: 12,
                    }}
                  >
                    Allocate
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {orders && orders.length === 0 && (
        <p style={{ textAlign: "center", padding: 32, color: "var(--text-secondary)" }}>
          No orders found
        </p>
      )}
    </div>
  );
}

import { useMemo } from "react";
import { useWarehouseStore } from "@/stores/useWarehouseStore";
import type { Order } from "@/types/order";
import type { PickTask } from "@/types/pickTask";

// ------------------------------------------------------------------ styles

const FEED_STYLE: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 8,
  fontSize: 12,
};

const CARD_STYLE: React.CSSProperties = {
  background: "#232738",
  borderRadius: 6,
  padding: "8px 10px",
  borderLeft: "3px solid #3b82f6",
};

const BADGE_BASE: React.CSSProperties = {
  display: "inline-block",
  padding: "1px 6px",
  borderRadius: 3,
  fontSize: 10,
  fontWeight: 600,
  textTransform: "uppercase" as const,
};

const STATUS_COLORS: Record<string, string> = {
  NEW: "#6b7280",
  FAILED: "#ef4444",
  ALLOCATED: "#3b82f6",
  IN_PROGRESS: "#f59e0b",
  COMPLETED: "#22c55e",
  CANCELLED: "#ef4444",

  SOURCE_REQUESTED: "#8b5cf6",
  SOURCE_AT_CANTILEVER: "#f59e0b",
  SOURCE_AT_STATION: "#3b82f6",
  PICKING: "#14b8a6",
  RETURN_REQUESTED: "#f97316",
  RETURN_AT_CANTILEVER: "#f59e0b",
};

const KPI_STYLE: React.CSSProperties = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 8,
  marginBottom: 12,
};

const KPI_CARD: React.CSSProperties = {
  background: "#232738",
  borderRadius: 6,
  padding: "8px 10px",
  textAlign: "center" as const,
};

const KPI_VALUE: React.CSSProperties = {
  fontSize: 18,
  fontWeight: 700,
  color: "#e2e8f0",
};

const KPI_LABEL: React.CSSProperties = {
  fontSize: 10,
  color: "#94a3b8",
  marginTop: 2,
};

// ------------------------------------------------------------------ component

export function ActivityFeed() {
  const orders = useWarehouseStore((s) => s.orders);
  const pickTasks = useWarehouseStore((s) => s.pickTasks);

  const stats = useMemo(() => {
    const activeOrders = orders.filter(
      (o) => o.status !== "COMPLETED" && o.status !== "CANCELLED",
    ).length;
    const completedOrders = orders.filter(
      (o) => o.status === "COMPLETED",
    ).length;
    const activeTasks = pickTasks.filter(
      (t) => t.state !== "COMPLETED",
    ).length;
    const completedTasks = pickTasks.filter(
      (t) => t.state === "COMPLETED",
    ).length;

    return { activeOrders, completedOrders, activeTasks, completedTasks };
  }, [orders, pickTasks]);

  // Show most recent first (max 20).
  const recentOrders = useMemo(
    () => [...orders].reverse().slice(0, 20),
    [orders],
  );

  const recentTasks = useMemo(
    () =>
      [...pickTasks]
        .filter((t) => t.state !== "COMPLETED")
        .reverse()
        .slice(0, 10),
    [pickTasks],
  );

  return (
    <div style={FEED_STYLE}>
      {/* KPI summary */}
      <div style={KPI_STYLE}>
        <div style={KPI_CARD}>
          <div style={KPI_VALUE}>{stats.activeOrders}</div>
          <div style={KPI_LABEL}>Active Orders</div>
        </div>
        <div style={KPI_CARD}>
          <div style={KPI_VALUE}>{stats.completedOrders}</div>
          <div style={KPI_LABEL}>Completed</div>
        </div>
        <div style={KPI_CARD}>
          <div style={KPI_VALUE}>{stats.activeTasks}</div>
          <div style={KPI_LABEL}>Active Tasks</div>
        </div>
        <div style={KPI_CARD}>
          <div style={KPI_VALUE}>{stats.completedTasks}</div>
          <div style={KPI_LABEL}>Done Tasks</div>
        </div>
      </div>

      {/* Active pick tasks */}
      {recentTasks.length > 0 && (
        <>
          <div style={{ color: "#94a3b8", fontWeight: 600, fontSize: 11 }}>
            Active Pick Tasks
          </div>
          {recentTasks.map((task) => (
            <TaskCard key={task.id} task={task} />
          ))}
        </>
      )}

      {/* Recent orders */}
      {recentOrders.length > 0 && (
        <>
          <div
            style={{
              color: "#94a3b8",
              fontWeight: 600,
              fontSize: 11,
              marginTop: 4,
            }}
          >
            Recent Orders
          </div>
          {recentOrders.map((order) => (
            <OrderCard key={order.id} order={order} />
          ))}
        </>
      )}

      {orders.length === 0 && pickTasks.length === 0 && (
        <div style={{ color: "#64748b", textAlign: "center", padding: 16 }}>
          No activity yet. Start a WES-driven simulation to see orders flow.
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------ sub-cards

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? "#6b7280";
  return (
    <span
      style={{
        ...BADGE_BASE,
        background: `${color}22`,
        color,
        border: `1px solid ${color}44`,
      }}
    >
      {(status ?? "UNKNOWN").replace(/_/g, " ")}
    </span>
  );
}

function OrderCard({ order }: { order: Order }) {
  return (
    <div
      style={{
        ...CARD_STYLE,
        borderLeftColor:
          STATUS_COLORS[order.status] ?? "#6b7280",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 4,
        }}
      >
        <span style={{ fontWeight: 600, color: "#e2e8f0" }}>
          {order.external_id}
        </span>
        <StatusBadge status={order.status} />
      </div>
      <div style={{ color: "#94a3b8" }}>
        {order.sku} &times; {order.quantity}
      </div>
    </div>
  );
}

function TaskCard({ task }: { task: PickTask }) {
  return (
    <div
      style={{
        ...CARD_STYLE,
        borderLeftColor:
          STATUS_COLORS[task.state] ?? "#6b7280",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 4,
        }}
      >
        <span style={{ fontWeight: 600, color: "#e2e8f0" }}>
          {task.sku}
        </span>
        <StatusBadge status={task.state} />
      </div>
      <div style={{ color: "#94a3b8" }}>
        Pick: {task.qty_picked}/{task.qty_to_pick}
      </div>
    </div>
  );
}

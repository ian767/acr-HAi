import { Routes, Route, Navigate, Link, useLocation } from "react-router-dom";
import { lazy, Suspense } from "react";
import { WebSocketProvider } from "./websocket/WebSocketProvider";

const DashboardPage = lazy(() => import("./features/dashboard/DashboardPage"));
const OrderManagementPage = lazy(() => import("./features/wes/OrderManagementPage"));
const StationManagementPage = lazy(() => import("./features/wes/StationManagementPage"));
const PickTaskMonitorPage = lazy(() => import("./features/wes/PickTaskMonitorPage"));
const WarehouseMapPage = lazy(() =>
  import("./features/ess/WarehouseMapPage").then((m) => ({ default: m.WarehouseMapPage })),
);
const RobotFleetPage = lazy(() =>
  import("./features/ess/RobotFleetPage").then((m) => ({ default: m.RobotFleetPage })),
);
const StationOperatorPage = lazy(() => import("./features/station/StationOperatorPage"));
const AlarmListPage = lazy(() => import("./features/monitoring/AlarmListPage"));
const MetricsPage = lazy(() => import("./features/monitoring/MetricsPage"));

const NAV_ITEMS = [
  { path: "/dashboard", label: "Dashboard" },
  { path: "/wes/orders", label: "Orders" },
  { path: "/wes/stations", label: "Stations" },
  { path: "/wes/pick-tasks", label: "Pick Tasks" },
  { path: "/ess/map", label: "Map" },
  { path: "/ess/robots", label: "Robots" },
  { path: "/monitoring/alarms", label: "Alarms" },
  { path: "/monitoring/metrics", label: "Metrics" },
];

function Sidebar() {
  const location = useLocation();
  return (
    <nav
      style={{
        width: 200,
        background: "var(--bg-secondary)",
        borderRight: "1px solid var(--border)",
        padding: "16px 0",
        display: "flex",
        flexDirection: "column",
        gap: 2,
        flexShrink: 0,
      }}
    >
      <div
        style={{
          padding: "8px 16px 16px",
          fontSize: 18,
          fontWeight: 700,
          letterSpacing: -0.5,
        }}
      >
        ACR-Hai
      </div>
      {NAV_ITEMS.map((item) => (
        <Link
          key={item.path}
          to={item.path}
          style={{
            padding: "8px 16px",
            fontSize: 13,
            textDecoration: "none",
            color: location.pathname.startsWith(item.path)
              ? "var(--text-primary)"
              : "var(--text-secondary)",
            background: location.pathname.startsWith(item.path)
              ? "var(--bg-card)"
              : "transparent",
            borderLeft: location.pathname.startsWith(item.path)
              ? "2px solid var(--accent-blue)"
              : "2px solid transparent",
          }}
        >
          {item.label}
        </Link>
      ))}
    </nav>
  );
}

function Loading() {
  return (
    <div style={{ padding: 40, textAlign: "center", color: "var(--text-secondary)" }}>
      Loading...
    </div>
  );
}

export default function App() {
  return (
    <WebSocketProvider>
      <div style={{ display: "flex", minHeight: "100vh" }}>
        <Sidebar />
        <main style={{ flex: 1, overflow: "auto" }}>
          <Suspense fallback={<Loading />}>
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<DashboardPage />} />
              <Route path="/wes/orders" element={<OrderManagementPage />} />
              <Route path="/wes/stations" element={<StationManagementPage />} />
              <Route path="/wes/pick-tasks" element={<PickTaskMonitorPage />} />
              <Route path="/ess/map" element={<WarehouseMapPage />} />
              <Route path="/ess/robots" element={<RobotFleetPage />} />
              <Route path="/station/:id" element={<StationOperatorPage />} />
              <Route path="/monitoring/alarms" element={<AlarmListPage />} />
              <Route path="/monitoring/metrics" element={<MetricsPage />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </WebSocketProvider>
  );
}

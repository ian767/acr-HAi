import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { wesApi } from "@/api/wes";
import type { PickTask, PutWallSlot } from "@/types/pickTask";
import type { Order } from "@/types/order";
import { Sound } from "@/utils/sounds";

// ------------------------------------------------------------------ types

interface Props {
  stationId: string;
  stationName: string;
  onClose: () => void;
}

// Simulated product catalog keyed by SKU prefix
const SKU_CATALOG: Record<string, { name: string; image: string; expiry: string | null }> = {};

function getProductInfo(sku: string): { name: string; image: string; expiry: string | null } {
  if (SKU_CATALOG[sku]) return SKU_CATALOG[sku]!;
  // Generate deterministic placeholder from SKU
  const hash = sku.split("").reduce((a, c) => a + c.charCodeAt(0), 0);
  const colors = ["#3b82f6", "#22c55e", "#eab308", "#a855f7", "#f97316", "#ef4444"];
  const color = colors[hash % colors.length]!;
  return {
    name: sku.replace(/-/g, " ").replace(/SKU\s*/i, "Product "),
    image: color, // Used as background color for placeholder
    expiry: hash % 3 === 0 ? "2026-06-15" : null,
  };
}

// ------------------------------------------------------------------ styles

const OVERLAY: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 9999,
  background: "#0c0e14",
  color: "#e2e8f0",
  fontFamily: "Inter, system-ui, sans-serif",
  display: "flex",
  flexDirection: "column",
};

const HEADER: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  padding: "10px 20px",
  background: "#141722",
  borderBottom: "1px solid #2d3148",
  flexShrink: 0,
};

const GRID: React.CSSProperties = {
  flex: 1,
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gridTemplateRows: "1fr 1fr",
  gap: 1,
  background: "#2d3148",
  overflow: "hidden",
};

const QUADRANT: React.CSSProperties = {
  background: "#0e1015",
  padding: 20,
  overflow: "auto",
};

const BADGE: React.CSSProperties = {
  padding: "2px 8px",
  borderRadius: 10,
  fontSize: 10,
  fontWeight: 600,
  color: "#fff",
};

const INPUT_STYLE: React.CSSProperties = {
  width: "100%",
  padding: "12px 16px",
  border: "2px solid #4a5568",
  borderRadius: 8,
  background: "#1a1d27",
  color: "#e2e8f0",
  fontSize: 18,
  fontFamily: "monospace",
  boxSizing: "border-box" as const,
  outline: "none",
};

const BTN: React.CSSProperties = {
  padding: "8px 16px",
  border: "1px solid #4a5568",
  borderRadius: 6,
  background: "#2d3148",
  color: "#e2e8f0",
  cursor: "pointer",
  fontSize: 13,
  fontWeight: 500,
};

const TASK_STATE_COLORS: Record<string, string> = {
  SOURCE_REQUESTED: "#eab308",
  SOURCE_AT_CANTILEVER: "#f97316",
  SOURCE_AT_STATION: "#22c55e",
  PICKING: "#3b82f6",
  RETURN_REQUESTED: "#a855f7",
  RETURN_AT_CANTILEVER: "#f97316",
  COMPLETED: "#6b7280",
};

const TASK_STATE_LABEL: Record<string, string> = {
  SOURCE_REQUESTED: "Fetching tote",
  SOURCE_AT_CANTILEVER: "At cantilever",
  SOURCE_AT_STATION: "Ready to scan",
  PICKING: "Scanning",
  RETURN_REQUESTED: "Returning tote",
  RETURN_AT_CANTILEVER: "Return at cantilever",
  COMPLETED: "Completed",
};

// ------------------------------------------------------------------ component

export function StationOperatorView({ stationId, stationName, onClose }: Props) {
  const queryClient = useQueryClient();
  const barcodeRef = useRef<HTMLInputElement>(null);

  const [pickTasks, setPickTasks] = useState<PickTask[]>([]);
  const [orders, setOrders] = useState<Order[]>([]);
  const [putwallSlots, setPutwallSlots] = useState<PutWallSlot[]>([]);
  const [barcode, setBarcode] = useState("");
  const [scanQty, setScanQty] = useState(1);
  const [scanning, setScanning] = useState(false);
  const [scanFlash, setScanFlash] = useState<"success" | "error" | null>(null);
  const [scanMsg, setScanMsg] = useState("");
  const [toteBarcode, setToteBarcode] = useState("");
  const [bindingTote, setBindingTote] = useState(false);
  const [pendingToteBarcode, setPendingToteBarcode] = useState<string | null>(null);
  const [exceptionMode, setExceptionMode] = useState(false);
  const [exceptionReason, setExceptionReason] = useState("");
  const flashTimeout = useRef<ReturnType<typeof setTimeout>>();

  // Poll pick tasks + orders + putwall
  useEffect(() => {
    const load = () => {
      wesApi.listPickTasks({ station_id: stationId }).then(setPickTasks).catch(() => {});
      wesApi.listOrders({ limit: 50 }).then(setOrders).catch(() => {});
      wesApi.getPutwall(stationId).then(setPutwallSlots).catch(() => {});
    };
    load();
    const id = setInterval(load, 1500);
    return () => clearInterval(id);
  }, [stationId]);

  // Auto-focus barcode input
  useEffect(() => {
    barcodeRef.current?.focus();
  }, [pickTasks]);

  // Cleanup flash timeout
  useEffect(() => {
    return () => {
      if (flashTimeout.current) clearTimeout(flashTimeout.current);
    };
  }, []);

  // Active task: SOURCE_AT_STATION or PICKING
  const activeTask = pickTasks.find(
    (t) => t.state === "SOURCE_AT_STATION" || t.state === "PICKING",
  ) ?? null;

  const pendingTasks = pickTasks.filter(
    (t) => t.state === "SOURCE_REQUESTED" || t.state === "SOURCE_AT_CANTILEVER",
  );
  const completedTasks = pickTasks.filter((t) => t.state === "COMPLETED");
  const returningTasks = pickTasks.filter(
    (t) => t.state === "RETURN_REQUESTED" || t.state === "RETURN_AT_CANTILEVER",
  );

  // Order for active task
  const activeOrder = activeTask
    ? orders.find((o) => o.id === activeTask.order_id) ?? null
    : null;

  // Product info
  const product = activeTask ? getProductInfo(activeTask.sku) : null;

  // Build put-wall slots (6 slots) from server putwall data (computed early for handlers)
  const putWallSlots = buildPutWallSlots(pickTasks, putwallSlots);
  const firstEmptySlotId = putWallSlots.find(
    (s) => !s.toteBarcode && !s.bound && !s.ready && s.slotId,
  )?.slotId ?? null;

  // Flash effect
  const triggerFlash = useCallback((type: "success" | "error", msg: string) => {
    setScanFlash(type);
    setScanMsg(msg);
    if (flashTimeout.current) clearTimeout(flashTimeout.current);
    flashTimeout.current = setTimeout(() => {
      setScanFlash(null);
      setScanMsg("");
    }, 1500);
  }, []);

  // Scan handler
  const handleScan = useCallback(async () => {
    if (!activeTask || scanning) return;

    // Validate barcode matches expected SKU
    if (barcode.trim().toUpperCase() !== activeTask.sku.toUpperCase()) {
      triggerFlash("error", `Expected ${activeTask.sku}`);
      return;
    }

    setScanning(true);
    try {
      // Scan N times (scanQty)
      for (let i = 0; i < scanQty; i++) {
        await wesApi.scanItem(stationId, activeTask.id);
      }
      setBarcode("");
      setScanQty(1);
      Sound.scanSuccess();
      triggerFlash("success", `Scanned ${scanQty}x ${activeTask.sku}`);
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      // Immediately refresh tasks
      wesApi.listPickTasks({ station_id: stationId }).then(setPickTasks).catch(() => {});
    } catch (err: any) {
      Sound.scanError();
      triggerFlash("error", err.message || "Scan failed");
    } finally {
      setScanning(false);
      barcodeRef.current?.focus();
    }
  }, [activeTask, barcode, scanQty, stationId, scanning, triggerFlash, queryClient]);

  // Handle barcode key down (Enter to scan)
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Enter") {
        e.preventDefault();
        handleScan();
      }
    },
    [handleScan],
  );

  // Dispatch retrieve (manual tote pull)
  const handleDispatch = useCallback(async (pickTaskId: string) => {
    try {
      await wesApi.dispatchRetrieve(pickTaskId);
      triggerFlash("success", "Tote pull dispatched");
      wesApi.listPickTasks({ station_id: stationId }).then(setPickTasks).catch(() => {});
    } catch (err: any) {
      triggerFlash("error", err.message || "Dispatch failed");
    }
  }, [stationId, triggerFlash]);

  // Step 1: Scan target tote barcode → sets pendingToteBarcode (does NOT auto-bind).
  const handleScanTote = useCallback(() => {
    if (!toteBarcode.trim()) return;
    setPendingToteBarcode(toteBarcode.trim());
    setToteBarcode("");
    Sound.toteBound();
    triggerFlash("success", "Tote scanned — select a put-wall cell");
  }, [toteBarcode, triggerFlash]);

  // Step 2: Operator clicks a putwall cell → binds pendingToteBarcode to that slot.
  const handleCellBind = useCallback(async (slotId: string) => {
    if (!pendingToteBarcode || bindingTote) return;
    setBindingTote(true);
    try {
      await wesApi.bindPutwallSlot(stationId, slotId, pendingToteBarcode);
      // If active task needs a target tote, also bind to the task
      if (activeTask && !activeTask.target_tote_id) {
        await wesApi.bindTote(stationId, activeTask.id, pendingToteBarcode);
      }
      setPendingToteBarcode(null);
      Sound.toteBound();
      triggerFlash("success", "Tote bound to cell");
      wesApi.getPutwall(stationId).then(setPutwallSlots).catch(() => {});
      wesApi.listPickTasks({ station_id: stationId }).then(setPickTasks).catch(() => {});
    } catch (err: any) {
      triggerFlash("error", err.message || "Bind failed");
    } finally {
      setBindingTote(false);
    }
  }, [stationId, pendingToteBarcode, bindingTote, triggerFlash, activeTask]);

  // Tote-full handler: operator clicks a bound putwall cell to mark it full.
  const TOTE_CAPACITY = 20;
  const handleToteFull = useCallback(async (taskId: string) => {
    try {
      await wesApi.toteFull(stationId, taskId);
      Sound.scanSuccess();
      triggerFlash("success", "Tote marked full — returning");
      wesApi.getPutwall(stationId).then(setPutwallSlots).catch(() => {});
      wesApi.listPickTasks({ station_id: stationId }).then(setPickTasks).catch(() => {});
      queryClient.invalidateQueries({ queryKey: ["orders"] });
    } catch (err: any) {
      triggerFlash("error", err.message || "Tote-full failed");
    }
  }, [stationId, triggerFlash, queryClient]);

  // Show tote binding when: empty putwall slots exist OR active task needs target tote
  const needsToteBinding = !!(firstEmptySlotId) ||
    !!(activeTask && !activeTask.target_tote_id &&
      (activeTask.state === "SOURCE_AT_STATION" || activeTask.state === "PICKING"));

  // Exception handler
  const handleException = useCallback(async () => {
    if (!activeTask) return;
    // For now: skip remaining picks and force return
    try {
      // Mark as exception via tote-full (triggers return)
      await wesApi.toteFull(stationId, activeTask.id);
      setExceptionMode(false);
      setExceptionReason("");
      triggerFlash("success", "Exception reported - tote returning");
      queryClient.invalidateQueries({ queryKey: ["orders"] });
      wesApi.listPickTasks({ station_id: stationId }).then(setPickTasks).catch(() => {});
    } catch (err: any) {
      triggerFlash("error", err.message || "Exception failed");
    }
  }, [activeTask, stationId, triggerFlash, queryClient]);

  // -------------------------------------------------------------- render

  return (
    <div style={OVERLAY}>
      {/* Header */}
      <div style={HEADER}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 16, fontWeight: 700 }}>{stationName}</span>
          <span style={{ fontSize: 12, color: "#94a3b8" }}>Station Operator</span>
          {activeTask && (
            <span style={{ ...BADGE, background: "#22c55e" }}>ACTIVE</span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span style={{ fontSize: 12, color: "#94a3b8" }}>
            Tasks: {pickTasks.length} | Completed: {completedTasks.length}
          </span>
          <button
            style={{ ...BTN, borderColor: "#ef4444", color: "#ef4444" }}
            onClick={onClose}
          >
            Exit Station
          </button>
        </div>
      </div>

      {/* 4-Quadrant Grid */}
      <div style={GRID}>
        {/* ---- Top Left: Product Info ---- */}
        <div style={QUADRANT}>
          <TopLeftProductInfo task={activeTask} product={product} order={activeOrder} />
        </div>

        {/* ---- Top Right: Task Queue & Station Info ---- */}
        <div style={QUADRANT}>
          <TopRightTaskQueue
            activeTask={activeTask}
            pendingTasks={pendingTasks}
            returningTasks={returningTasks}
            completedTasks={completedTasks}
            orders={orders}
            pickTasks={pickTasks}
            onDispatch={handleDispatch}
          />
        </div>

        {/* ---- Bottom Left: Put-Wall ---- */}
        <div style={QUADRANT}>
          <BottomLeftPutWall
            slots={putWallSlots}
            activeTaskId={activeTask?.id ?? null}
            pendingToteBarcode={pendingToteBarcode}
            onCellBind={handleCellBind}
            onToteFull={handleToteFull}
            toteCapacity={TOTE_CAPACITY}
          />
        </div>

        {/* ---- Bottom Right: Scan Interface ---- */}
        <div style={QUADRANT}>
          <BottomRightScan
            activeTask={activeTask}
            barcode={barcode}
            setBarcode={setBarcode}
            scanQty={scanQty}
            setScanQty={setScanQty}
            scanning={scanning}
            scanFlash={scanFlash}
            scanMsg={scanMsg}
            onScan={handleScan}
            onKeyDown={handleKeyDown}
            barcodeRef={barcodeRef}
            exceptionMode={exceptionMode}
            setExceptionMode={setExceptionMode}
            exceptionReason={exceptionReason}
            setExceptionReason={setExceptionReason}
            onException={handleException}
            needsToteBinding={needsToteBinding}
            toteBarcode={toteBarcode}
            setToteBarcode={setToteBarcode}
            bindingTote={bindingTote}
            onScanTote={handleScanTote}
            pendingToteBarcode={pendingToteBarcode}
          />
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Top Left: Product Info

function TopLeftProductInfo({
  task,
  product,
  order,
}: {
  task: PickTask | null;
  product: { name: string; image: string; expiry: string | null } | null;
  order: Order | null;
}) {
  if (!task || !product) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#64748b" }}>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 48, marginBottom: 12, opacity: 0.3 }}>&#128230;</div>
          <div style={{ fontSize: 14 }}>Waiting for tote arrival...</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>A robot is fetching the source tote</div>
        </div>
      </div>
    );
  }

  const pct = task.qty_to_pick > 0
    ? Math.round((task.qty_picked / task.qty_to_pick) * 100)
    : 0;

  return (
    <div>
      <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>
        Product Information
      </div>

      <div style={{ display: "flex", gap: 16, marginBottom: 16 }}>
        {/* Product Image Placeholder */}
        <div
          style={{
            width: 120,
            height: 120,
            borderRadius: 8,
            background: product.image,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 40,
            color: "#fff",
            fontWeight: 700,
            flexShrink: 0,
            opacity: 0.85,
          }}
        >
          {task.sku.slice(0, 2)}
        </div>

        {/* Product Details */}
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: 18, fontWeight: 700, marginBottom: 4 }}>{product.name}</div>
          <div style={{ fontSize: 13, color: "#94a3b8", marginBottom: 8 }}>
            Internal SKU: <span style={{ color: "#e2e8f0", fontFamily: "monospace" }}>{task.sku}</span>
          </div>

          {order && (
            <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 4 }}>
              Order: <span style={{ color: "#e2e8f0" }}>{order.external_id}</span>
            </div>
          )}

          {product.expiry && (
            <div style={{ fontSize: 12, marginTop: 4 }}>
              <span style={{ color: "#eab308" }}>Expiry: {product.expiry}</span>
            </div>
          )}
        </div>
      </div>

      {/* Quantity Info */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 16 }}>
        <div style={{
          background: "#1a1d27",
          borderRadius: 8,
          padding: 12,
          border: "1px solid #2d3148",
        }}>
          <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>ACR Zone Qty (Source)</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: "#3b82f6" }}>{task.qty_to_pick}</div>
          <div style={{ fontSize: 11, color: "#64748b" }}>Items to pick from tote</div>
        </div>
        <div style={{
          background: "#1a1d27",
          borderRadius: 8,
          padding: 12,
          border: "1px solid #2d3148",
        }}>
          <div style={{ fontSize: 11, color: "#94a3b8", marginBottom: 4 }}>Target Tote Qty</div>
          <div style={{ fontSize: 24, fontWeight: 700, color: "#22c55e" }}>
            {task.qty_picked}
            <span style={{ fontSize: 14, fontWeight: 400, color: "#64748b" }}> / {task.qty_to_pick}</span>
          </div>
          <div style={{ fontSize: 11, color: "#64748b" }}>Items placed in target</div>
        </div>
      </div>

      {/* Progress Bar */}
      <div>
        <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginBottom: 4 }}>
          <span style={{ color: "#94a3b8" }}>Pick Progress</span>
          <span style={{ color: "#e2e8f0", fontWeight: 600 }}>{pct}%</span>
        </div>
        <div style={{ height: 8, background: "#1a1d27", borderRadius: 4, overflow: "hidden" }}>
          <div
            style={{
              height: "100%",
              width: `${pct}%`,
              background: pct >= 100 ? "#22c55e" : "#3b82f6",
              borderRadius: 4,
              transition: "width 0.3s",
            }}
          />
        </div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Top Right: Task Queue

function TopRightTaskQueue({
  activeTask,
  pendingTasks,
  returningTasks,
  completedTasks,
  orders,
  pickTasks,
  onDispatch,
}: {
  activeTask: PickTask | null;
  pendingTasks: PickTask[];
  returningTasks: PickTask[];
  completedTasks: PickTask[];
  orders: Order[];
  onDispatch: (pickTaskId: string) => void;
  pickTasks: PickTask[];
}) {
  const orderForTask = (taskOrderId: string) =>
    orders.find((o) => o.id === taskOrderId);

  return (
    <div>
      <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>
        Station Overview
      </div>

      {/* Stats row */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {[
          { label: "Active", count: activeTask ? 1 : 0, color: "#22c55e" },
          { label: "Pending", count: pendingTasks.length, color: "#eab308" },
          { label: "Returning", count: returningTasks.length, color: "#a855f7" },
          { label: "Done", count: completedTasks.length, color: "#6b7280" },
        ].map((s) => (
          <div
            key={s.label}
            style={{
              flex: 1,
              background: "#1a1d27",
              borderRadius: 6,
              padding: "8px 10px",
              border: "1px solid #2d3148",
              textAlign: "center",
            }}
          >
            <div style={{ fontSize: 20, fontWeight: 700, color: s.color }}>{s.count}</div>
            <div style={{ fontSize: 10, color: "#94a3b8" }}>{s.label}</div>
          </div>
        ))}
      </div>

      {/* Task List */}
      <div style={{ fontSize: 12, color: "#94a3b8", marginBottom: 6, fontWeight: 600 }}>
        Task Queue
      </div>

      <div style={{ maxHeight: "calc(100% - 120px)", overflowY: "auto" }}>
        {pickTasks.length === 0 && (
          <div style={{ color: "#64748b", fontSize: 12, padding: 16, textAlign: "center" }}>
            No tasks assigned to this station
          </div>
        )}

        {pickTasks.map((task) => {
          const order = orderForTask(task.order_id);
          const isActive = activeTask?.id === task.id;
          const pct = task.qty_to_pick > 0
            ? Math.round((task.qty_picked / task.qty_to_pick) * 100)
            : 0;

          return (
            <div
              key={task.id}
              style={{
                padding: 10,
                marginBottom: 4,
                borderRadius: 6,
                background: isActive ? "#22c55e12" : "#1a1d27",
                border: `1px solid ${isActive ? "#22c55e44" : "#2d3148"}`,
                borderLeft: isActive ? "3px solid #22c55e" : "3px solid transparent",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ fontWeight: 600, fontSize: 13, color: "#e2e8f0" }}>{task.sku}</span>
                  {isActive && (
                    <span style={{ ...BADGE, background: "#22c55e", fontSize: 9 }}>ACTIVE</span>
                  )}
                </div>
                <span
                  style={{
                    ...BADGE,
                    background: TASK_STATE_COLORS[task.state] ?? "#666",
                  }}
                >
                  {TASK_STATE_LABEL[task.state] ?? task.state}
                </span>
              </div>

              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "#94a3b8" }}>
                <span>{order?.external_id ?? task.order_id.slice(0, 8)}</span>
                <span>{task.qty_picked}/{task.qty_to_pick} ({pct}%)</span>
              </div>

              {/* Pull Tote button for SOURCE_REQUESTED tasks */}
              {task.state === "SOURCE_REQUESTED" && (
                <button
                  onClick={(e) => { e.stopPropagation(); onDispatch(task.id); }}
                  style={{
                    marginTop: 6,
                    width: "100%",
                    padding: "5px 0",
                    background: "#eab308",
                    color: "#000",
                    border: "none",
                    borderRadius: 4,
                    fontSize: 11,
                    fontWeight: 700,
                    cursor: "pointer",
                  }}
                >
                  Pull Tote
                </button>
              )}

              {/* Mini progress bar */}
              {task.state !== "COMPLETED" && task.state !== "SOURCE_REQUESTED" && (
                <div style={{ height: 2, background: "#2d3148", borderRadius: 1, marginTop: 4, overflow: "hidden" }}>
                  <div
                    style={{
                      height: "100%",
                      width: `${pct}%`,
                      background: TASK_STATE_COLORS[task.state] ?? "#3b82f6",
                      transition: "width 0.3s",
                    }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Bottom Left: Put-Wall

interface PutWallSlotData {
  index: number;
  label: string;
  bound: boolean;
  assigned: boolean;
  toteId: string | null;
  toteBarcode: string | null;
  taskId: string | null;
  sku: string | null;
  qtyTotal: number;
  qtyPicked: number;
  state: string | null;
  slotId: string | null;
  ready: boolean; // pre-bound tote, no task assigned yet
}

function buildPutWallSlots(
  tasks: PickTask[],
  serverSlots: PutWallSlot[],
): PutWallSlotData[] {
  const labels = ["A1", "A2", "A3", "B1", "B2", "B3"];

  // Build map of server slots by label
  const serverMap = new Map<string, PutWallSlot>();
  serverSlots.forEach((s) => serverMap.set(s.slot_label, s));

  // Build map of tasks by put_wall_slot_id
  const taskBySlot = new Map<string, PickTask>();
  const activeTasks = tasks.filter(
    (t) =>
      t.state !== "COMPLETED" &&
      t.state !== "RETURN_AT_CANTILEVER",
  );
  activeTasks.forEach((t) => {
    if (t.put_wall_slot_id) {
      taskBySlot.set(t.put_wall_slot_id, t);
    }
  });

  const slots: PutWallSlotData[] = labels.map((label, i) => {
    const sv = serverMap.get(label);
    // Only show tasks that the backend has explicitly linked to this slot.
    // No client-side fallback — slot state is determined by server data only.
    const task: PickTask | undefined = sv ? taskBySlot.get(sv.id) : undefined;

    const hasTote = !!(sv?.target_tote_id);
    const hasTask = !!task;

    return {
      index: i,
      label,
      bound: hasTask ? !!task.target_tote_id : hasTote,
      assigned: hasTask,
      toteId: task?.target_tote_id ?? sv?.target_tote_id ?? null,
      toteBarcode: task?.target_tote_barcode ?? sv?.target_tote_barcode ?? null,
      taskId: task?.id ?? null,
      sku: task?.sku ?? null,
      qtyTotal: task?.qty_to_pick ?? 0,
      qtyPicked: task?.qty_picked ?? 0,
      state: task?.state ?? null,
      slotId: sv?.id ?? null,
      ready: hasTote && !hasTask && !sv?.is_locked, // pre-bound, no task yet
    } as PutWallSlotData;
  });

  return slots;
}

function BottomLeftPutWall({
  slots,
  activeTaskId,
  pendingToteBarcode,
  onCellBind,
  onToteFull,
  toteCapacity,
}: {
  slots: PutWallSlotData[];
  activeTaskId: string | null;
  pendingToteBarcode: string | null;
  onCellBind: (slotId: string) => void;
  onToteFull: (taskId: string) => void;
  toteCapacity: number;
}) {
  return (
    <div>
      <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>
        Put-Wall (Target Totes)
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "repeat(3, 1fr)",
          gridTemplateRows: "repeat(2, 1fr)",
          gap: 8,
        }}
      >
        {slots.map((slot) => {
          const isActive = slot.taskId === activeTaskId && activeTaskId !== null;
          const pct = slot.qtyTotal > 0
            ? Math.round((slot.qtyPicked / slot.qtyTotal) * 100)
            : 0;
          const isDone = slot.bound && slot.qtyPicked >= slot.qtyTotal && slot.qtyTotal > 0;
          const isFull = slot.assigned && slot.qtyPicked >= toteCapacity;

          // Cell is clickable for binding when pendingToteBarcode is set and cell is empty
          const canBind = !!pendingToteBarcode && !slot.toteBarcode && !slot.bound && !slot.ready && !!slot.slotId;
          // Cell is clickable for tote-full when it has a task and is bound (including DONE cells)
          const canMarkFull = slot.assigned && slot.bound && !!slot.taskId;

          const isClickable = canBind || canMarkFull;

          return (
            <div
              key={slot.label}
              onClick={() => {
                if (canBind && slot.slotId) {
                  onCellBind(slot.slotId);
                } else if (canMarkFull && slot.taskId) {
                  onToteFull(slot.taskId);
                }
              }}
              style={{
                background: canBind
                  ? "#eab30815"
                  : isActive
                    ? "#22c55e12"
                    : slot.bound || slot.ready
                      ? "#1a1d27"
                      : "#12141c",
                border: `2px solid ${
                  canBind
                    ? "#eab308"
                    : isFull
                      ? "#ef4444"
                      : isActive
                        ? "#22c55e"
                        : isDone
                          ? "#22c55e66"
                          : slot.ready
                            ? "#22c55e44"
                            : slot.bound
                              ? "#3b82f644"
                              : "#2d3148"
                }`,
                borderRadius: 10,
                padding: 12,
                minHeight: 100,
                display: "flex",
                flexDirection: "column",
                justifyContent: "space-between",
                transition: "border-color 0.3s, background 0.3s",
                cursor: isClickable ? "pointer" : "default",
                opacity: pendingToteBarcode && !canBind && !slot.bound && !slot.ready ? 0.4 : 1,
              }}
            >
              {/* Slot header */}
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontSize: 16, fontWeight: 700, color: (slot.assigned || slot.ready || canBind) ? "#e2e8f0" : "#4a5568" }}>
                  {slot.label}
                </span>
                {isFull ? (
                  <span style={{ ...BADGE, background: "#ef4444" }}>FULL ({slot.qtyPicked}/{toteCapacity})</span>
                ) : isDone ? (
                  <span style={{ ...BADGE, background: "#22c55e" }}>DONE</span>
                ) : slot.bound && slot.assigned ? (
                  <span style={{ ...BADGE, background: isActive ? "#3b82f6" : "#4a5568" }}>BOUND</span>
                ) : slot.assigned ? (
                  <span style={{ ...BADGE, background: "#eab308" }}>AWAITING</span>
                ) : slot.ready ? (
                  <span style={{ ...BADGE, background: "#22c55e" }}>READY</span>
                ) : canBind ? (
                  <span style={{ ...BADGE, background: "#eab308" }}>SELECT</span>
                ) : (
                  <span style={{ fontSize: 10, color: "#4a5568" }}>EMPTY</span>
                )}
              </div>

              {/* Tote barcode (shown when pre-bound or task-bound) */}
              {slot.toteBarcode && (
                <div style={{ fontSize: 10, color: "#60a5fa", fontFamily: "monospace", marginTop: 4 }}>
                  {slot.toteBarcode}
                </div>
              )}

              {slot.assigned ? (
                <>
                  {/* SKU */}
                  <div style={{ fontSize: 12, color: "#94a3b8", marginTop: 4 }}>
                    {slot.sku}
                  </div>

                  {/* Qty progress */}
                  <div style={{ marginTop: 8 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 3 }}>
                      <span style={{ color: "#94a3b8" }}>Progress</span>
                      <span style={{ color: "#e2e8f0", fontWeight: 600 }}>
                        {slot.qtyPicked} / {slot.qtyTotal}
                      </span>
                    </div>
                    <div style={{ height: 4, background: "#2d3148", borderRadius: 2, overflow: "hidden" }}>
                      <div
                        style={{
                          height: "100%",
                          width: `${pct}%`,
                          background: isFull ? "#ef4444" : isDone ? "#22c55e" : "#3b82f6",
                          borderRadius: 2,
                          transition: "width 0.3s",
                        }}
                      />
                    </div>
                    {canMarkFull && (
                      <div style={{ fontSize: 10, color: "#ef4444", marginTop: 4, textAlign: "center" }}>
                        Click to mark tote full
                      </div>
                    )}
                  </div>
                </>
              ) : slot.ready ? (
                <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <span style={{ fontSize: 11, color: "#22c55e", opacity: 0.7 }}>Tote ready</span>
                </div>
              ) : canBind ? (
                <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <span style={{ fontSize: 12, color: "#eab308", fontWeight: 600 }}>
                    Click to bind tote here
                  </span>
                </div>
              ) : (
                <div
                  style={{
                    flex: 1,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <span style={{ fontSize: 11, opacity: 0.3, color: "#4a5568" }}>
                    Scan tote in scan area
                  </span>
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Put-wall summary */}
      <div style={{ marginTop: 12, fontSize: 11, color: "#64748b", display: "flex", gap: 16 }}>
        <span>Bound: {slots.filter((s) => s.bound).length}/6</span>
        <span>Completed: {slots.filter((s) => s.bound && s.qtyPicked >= s.qtyTotal && s.qtyTotal > 0).length}</span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ Bottom Right: Scan Interface

function BottomRightScan({
  activeTask,
  barcode,
  setBarcode,
  scanQty,
  setScanQty,
  scanning,
  scanFlash,
  scanMsg,
  onScan,
  onKeyDown,
  barcodeRef,
  exceptionMode,
  setExceptionMode,
  exceptionReason,
  setExceptionReason,
  onException,
  needsToteBinding,
  toteBarcode,
  setToteBarcode,
  bindingTote,
  onScanTote,
  pendingToteBarcode,
}: {
  activeTask: PickTask | null;
  barcode: string;
  setBarcode: (v: string) => void;
  scanQty: number;
  setScanQty: (v: number) => void;
  scanning: boolean;
  scanFlash: "success" | "error" | null;
  scanMsg: string;
  onScan: () => void;
  onKeyDown: (e: React.KeyboardEvent) => void;
  barcodeRef: React.RefObject<HTMLInputElement>;
  exceptionMode: boolean;
  setExceptionMode: (v: boolean) => void;
  exceptionReason: string;
  setExceptionReason: (v: string) => void;
  onException: () => void;
  needsToteBinding: boolean;
  toteBarcode: string;
  setToteBarcode: (v: string) => void;
  bindingTote: boolean;
  onScanTote: () => void;
  pendingToteBarcode: string | null;
}) {
  const remaining = activeTask
    ? activeTask.qty_to_pick - activeTask.qty_picked
    : 0;
  const maxQty = Math.max(1, remaining);

  // Phase 1: Scan tote barcode
  // Phase 2: Pending tote → "select a cell" message
  // Phase 3: SKU scan

  // Phase 2: Tote scanned, waiting for cell selection
  if (pendingToteBarcode) {
    return (
      <div>
        <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>
          Step 2 &mdash; Select Put-Wall Cell
        </div>

        <div
          style={{
            background: "#eab30815",
            border: "2px solid #eab30844",
            borderRadius: 8,
            padding: 20,
            marginBottom: 16,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600, color: "#eab308", marginBottom: 8 }}>
            Tote scanned: <span style={{ fontFamily: "monospace" }}>{pendingToteBarcode}</span>
          </div>
          <div style={{ fontSize: 13, color: "#94a3b8" }}>
            Click an empty put-wall cell to bind this tote.
          </div>
        </div>

        {bindingTote && (
          <div style={{ fontSize: 12, color: "#eab308", textAlign: "center" }}>
            Binding...
          </div>
        )}

        {scanMsg && (
          <div style={{ fontSize: 12, color: scanFlash === "error" ? "#ef4444" : "#22c55e", textAlign: "center" }}>
            {scanMsg}
          </div>
        )}
      </div>
    );
  }

  // Phase 1: Tote binding step (scan tote barcode)
  if (needsToteBinding && !(activeTask && activeTask.target_tote_id)) {
    return (
      <div>
        <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>
          Step 1 &mdash; Scan Target Tote
        </div>

        <div
          style={{
            background: "#eab30815",
            border: "2px solid #eab30844",
            borderRadius: 8,
            padding: 16,
            marginBottom: 16,
            textAlign: "center",
          }}
        >
          <div style={{ fontSize: 14, fontWeight: 600, color: "#eab308", marginBottom: 6 }}>
            Scan target tote barcode
          </div>
          <div style={{ fontSize: 12, color: "#94a3b8" }}>
            Then select a put-wall cell to bind it.
          </div>
        </div>

        <div style={{ marginBottom: 12 }}>
          <label style={{ fontSize: 11, color: "#94a3b8", display: "block", marginBottom: 4 }}>
            Target Tote Barcode
          </label>
          <input
            type="text"
            value={toteBarcode}
            onChange={(e) => setToteBarcode(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); onScanTote(); } }}
            placeholder="Scan target tote barcode..."
            autoFocus
            style={{
              ...INPUT_STYLE,
              borderColor: scanFlash === "error" ? "#ef4444" : "#eab308",
            }}
          />
        </div>

        <button
          onClick={onScanTote}
          disabled={!toteBarcode.trim()}
          style={{
            width: "100%",
            padding: "14px 0",
            border: "none",
            borderRadius: 8,
            background: !toteBarcode.trim() ? "#4a5568" : "#eab308",
            color: "#000",
            cursor: !toteBarcode.trim() ? "not-allowed" : "pointer",
            fontSize: 16,
            fontWeight: 700,
            marginBottom: 8,
          }}
        >
          Scan Tote
        </button>

        {scanMsg && (
          <div style={{ fontSize: 12, color: scanFlash === "error" ? "#ef4444" : "#22c55e", textAlign: "center" }}>
            {scanMsg}
          </div>
        )}
      </div>
    );
  }

  // No active task with target tote → waiting for source tote
  if (!activeTask || !activeTask.target_tote_id) {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100%", color: "#64748b" }}>
        <div style={{ textAlign: "center" }}>
          <div style={{ fontSize: 48, marginBottom: 12, opacity: 0.3 }}>&#128269;</div>
          <div style={{ fontSize: 14 }}>Waiting for source tote</div>
          <div style={{ fontSize: 12, marginTop: 4 }}>A robot is fetching the source tote to the station</div>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div style={{ fontSize: 11, color: "#64748b", marginBottom: 8, textTransform: "uppercase", letterSpacing: 1 }}>
        Step 3 &mdash; Scan SKU
      </div>

      {/* Expected SKU indicator */}
      <div
        style={{
          background: "#1a1d27",
          border: "1px solid #2d3148",
          borderRadius: 8,
          padding: 10,
          marginBottom: 12,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <div>
          <div style={{ fontSize: 11, color: "#94a3b8" }}>Expected SKU</div>
          <div style={{ fontSize: 16, fontWeight: 700, fontFamily: "monospace", color: "#3b82f6" }}>
            {activeTask.sku}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={{ fontSize: 11, color: "#94a3b8" }}>Remaining</div>
          <div style={{ fontSize: 20, fontWeight: 700, color: remaining > 0 ? "#e2e8f0" : "#22c55e" }}>
            {remaining}
          </div>
        </div>
      </div>

      {/* Barcode input */}
      <div style={{ marginBottom: 12 }}>
        <label style={{ fontSize: 11, color: "#94a3b8", display: "block", marginBottom: 4 }}>
          Product Barcode
        </label>
        <input
          ref={barcodeRef}
          type="text"
          value={barcode}
          onChange={(e) => setBarcode(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Scan or type barcode..."
          autoFocus
          style={{
            ...INPUT_STYLE,
            borderColor: scanFlash === "error"
              ? "#ef4444"
              : scanFlash === "success"
                ? "#22c55e"
                : "#4a5568",
            transition: "border-color 0.3s",
          }}
        />
      </div>

      {/* Quantity Navigator */}
      <div style={{ marginBottom: 12 }}>
        <label style={{ fontSize: 11, color: "#94a3b8", display: "block", marginBottom: 4 }}>
          Quantity
        </label>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <button
            style={{
              ...BTN,
              width: 44,
              height: 44,
              fontSize: 20,
              fontWeight: 700,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 0,
              opacity: scanQty <= 1 ? 0.4 : 1,
            }}
            onClick={() => setScanQty(Math.max(1, scanQty - 1))}
            disabled={scanQty <= 1}
          >
            -
          </button>
          <div
            style={{
              flex: 1,
              textAlign: "center",
              fontSize: 28,
              fontWeight: 700,
              color: "#e2e8f0",
              background: "#1a1d27",
              border: "1px solid #2d3148",
              borderRadius: 8,
              padding: "6px 0",
            }}
          >
            {scanQty}
          </div>
          <button
            style={{
              ...BTN,
              width: 44,
              height: 44,
              fontSize: 20,
              fontWeight: 700,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              padding: 0,
              opacity: scanQty >= maxQty ? 0.4 : 1,
            }}
            onClick={() => setScanQty(Math.min(maxQty, scanQty + 1))}
            disabled={scanQty >= maxQty}
          >
            +
          </button>
        </div>
      </div>

      {/* Scan Button */}
      <button
        onClick={onScan}
        disabled={scanning || !barcode.trim()}
        style={{
          width: "100%",
          padding: "14px 0",
          border: "none",
          borderRadius: 8,
          background: scanning
            ? "#4a5568"
            : scanFlash === "success"
              ? "#22c55e"
              : scanFlash === "error"
                ? "#ef4444"
                : "#3b82f6",
          color: "#fff",
          cursor: scanning ? "not-allowed" : "pointer",
          fontSize: 16,
          fontWeight: 700,
          marginBottom: 8,
          transition: "background 0.3s",
        }}
      >
        {scanning ? "Scanning..." : scanFlash === "success" ? "Scanned!" : `Confirm Scan (${scanQty}x)`}
      </button>

      {/* Flash message */}
      {scanMsg && (
        <div
          style={{
            fontSize: 12,
            color: scanFlash === "error" ? "#ef4444" : "#22c55e",
            textAlign: "center",
            marginBottom: 8,
          }}
        >
          {scanMsg}
        </div>
      )}

      {/* Exception Section */}
      <div style={{ borderTop: "1px solid #2d3148", paddingTop: 10, marginTop: 4 }}>
        {!exceptionMode ? (
          <button
            style={{
              ...BTN,
              width: "100%",
              borderColor: "#ef444466",
              color: "#ef4444",
              background: "transparent",
              fontSize: 12,
            }}
            onClick={() => setExceptionMode(true)}
          >
            Report Picking Exception
          </button>
        ) : (
          <div>
            <div style={{ fontSize: 12, color: "#ef4444", fontWeight: 600, marginBottom: 6 }}>
              Picking Exception
            </div>
            <select
              style={{
                ...INPUT_STYLE,
                fontSize: 13,
                padding: "8px 10px",
                marginBottom: 8,
              }}
              value={exceptionReason}
              onChange={(e) => setExceptionReason(e.target.value)}
            >
              <option value="">-- Select reason --</option>
              <option value="short_pick">Short Pick (not enough stock)</option>
              <option value="damaged">Damaged Item</option>
              <option value="wrong_item">Wrong Item in Tote</option>
              <option value="other">Other</option>
            </select>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                style={{
                  ...BTN,
                  flex: 1,
                  background: "#ef4444",
                  borderColor: "#ef4444",
                  color: "#fff",
                  opacity: !exceptionReason ? 0.5 : 1,
                }}
                onClick={onException}
                disabled={!exceptionReason}
              >
                Confirm Exception
              </button>
              <button
                style={{ ...BTN, flex: 1 }}
                onClick={() => {
                  setExceptionMode(false);
                  setExceptionReason("");
                }}
              >
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

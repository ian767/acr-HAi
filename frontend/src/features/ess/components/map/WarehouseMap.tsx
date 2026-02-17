import { useEffect, useRef, useCallback } from "react";
import { useWarehouseStore } from "@/stores/useWarehouseStore";
import { useUiStore } from "@/stores/useUiStore";
import { useGrid } from "@/api/hooks";
// ------------------------------------------------------------------ constants

const CELL_SIZE = 20;

const CELL_COLORS: Record<string, string> = {
  FLOOR: "#1a1d27",
  RACK: "#4a5568",
  CANTILEVER: "#eab308",
  STATION: "#3b82f6",
  AISLE: "#2d3148",
  WALL: "#111111",
  CHARGING: "#22c55e",
};

const ROBOT_COLORS: Record<string, string> = {
  K50H: "#22c55e",
  A42TD: "#3b82f6",
};

export { CELL_SIZE };

// ------------------------------------------------------------------ component

export function WarehouseMap() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const rafRef = useRef<number>(0);
  // Pan/zoom state
  const panRef = useRef({ x: 0, y: 0 });
  const zoomRef = useRef(1);
  const draggingRef = useRef(false);
  const lastMouseRef = useRef({ x: 0, y: 0 });

  const activeZoneId = useUiStore((s) => s.activeZoneId);
  const { data: gridState, isLoading, error: gridError } = useGrid(activeZoneId ?? "");

  // ------------------------------------------------------------ drawing

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { robots, stations } = useWarehouseStore.getState();
    const pan = panRef.current;
    const zoom = zoomRef.current;

    // Clear
    ctx.fillStyle = "#0e1015";
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    ctx.save();
    ctx.translate(pan.x, pan.y);
    ctx.scale(zoom, zoom);

    // Draw grid
    if (gridState) {
      const { rows, cols, cells } = gridState;

      // Build cell lookup
      const cellMap = new Map<string, string>();
      for (const cell of cells) {
        cellMap.set(`${cell.row},${cell.col}`, cell.type);
      }

      for (let r = 0; r < rows; r++) {
        for (let c = 0; c < cols; c++) {
          const cellType = cellMap.get(`${r},${c}`) ?? "FLOOR";
          ctx.fillStyle = CELL_COLORS[cellType] ?? CELL_COLORS.FLOOR ?? "#1a1d27";
          ctx.fillRect(c * CELL_SIZE, r * CELL_SIZE, CELL_SIZE - 1, CELL_SIZE - 1);
        }
      }
    }

    // Draw stations
    for (const station of stations) {
      const x = station.grid_col * CELL_SIZE + CELL_SIZE / 2;
      const y = station.grid_row * CELL_SIZE + CELL_SIZE / 2;
      ctx.fillStyle = station.is_online ? "#3b82f6" : "#6b7280";
      ctx.fillRect(x - 7, y - 7, 14, 14);
      ctx.strokeStyle = "rgba(255,255,255,0.4)";
      ctx.lineWidth = 1;
      ctx.strokeRect(x - 7, y - 7, 14, 14);
      // Label
      ctx.fillStyle = "#ffffff";
      ctx.font = "9px monospace";
      ctx.textAlign = "center";
      ctx.fillText(station.name, x, y + 16);
    }

    // Draw robots
    for (const [id, robot] of Object.entries(robots)) {
      const x = robot.col * CELL_SIZE + CELL_SIZE / 2;
      const y = robot.row * CELL_SIZE + CELL_SIZE / 2;
      const color = ROBOT_COLORS[inferType(id)] ?? "#9ca3af";

      ctx.beginPath();
      ctx.arc(x, y, 7, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();

      // Heading indicator
      const headingRad = (robot.heading * Math.PI) / 180;
      ctx.beginPath();
      ctx.moveTo(x + Math.sin(headingRad) * 9, y - Math.cos(headingRad) * 9);
      ctx.lineTo(
        x + Math.sin(headingRad + 2.5) * 5,
        y - Math.cos(headingRad + 2.5) * 5,
      );
      ctx.lineTo(
        x + Math.sin(headingRad - 2.5) * 5,
        y - Math.cos(headingRad - 2.5) * 5,
      );
      ctx.closePath();
      ctx.fillStyle = "rgba(255,255,255,0.7)";
      ctx.fill();

      // Label
      ctx.fillStyle = "#ffffff";
      ctx.font = "8px monospace";
      ctx.textAlign = "center";
      ctx.fillText(id.slice(0, 6), x, y + 14);
    }

    ctx.restore();

    rafRef.current = requestAnimationFrame(draw);
  }, [gridState]);

  // ------------------------------------------------------------ resize

  const resize = useCallback(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container) return;
    const rect = container.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    const ctx = canvas.getContext("2d");
    if (ctx) ctx.scale(dpr, dpr);
  }, []);

  // ------------------------------------------------------------ pan / zoom

  const handleWheel = useCallback((e: WheelEvent) => {
    e.preventDefault();
    const direction = e.deltaY < 0 ? 1 : -1;
    const oldZoom = zoomRef.current;
    const newZoom = Math.min(5, Math.max(0.3, oldZoom + direction * 0.1 * oldZoom));

    const rect = containerRef.current?.getBoundingClientRect();
    if (rect) {
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;
      const scale = newZoom / oldZoom;
      panRef.current.x = mouseX - (mouseX - panRef.current.x) * scale;
      panRef.current.y = mouseY - (mouseY - panRef.current.y) * scale;
    }
    zoomRef.current = newZoom;
  }, []);

  const handlePointerDown = useCallback((e: PointerEvent) => {
    if (e.button === 0 || e.button === 1) {
      draggingRef.current = true;
      lastMouseRef.current = { x: e.clientX, y: e.clientY };
    }
  }, []);

  const handlePointerMove = useCallback((e: PointerEvent) => {
    if (!draggingRef.current) return;
    panRef.current.x += e.clientX - lastMouseRef.current.x;
    panRef.current.y += e.clientY - lastMouseRef.current.y;
    lastMouseRef.current = { x: e.clientX, y: e.clientY };
  }, []);

  const handlePointerUp = useCallback(() => {
    draggingRef.current = false;
  }, []);

  // ------------------------------------------------------------ lifecycle

  useEffect(() => {
    resize();
    rafRef.current = requestAnimationFrame(draw);

    const el = containerRef.current;
    if (el) {
      el.addEventListener("wheel", handleWheel, { passive: false });
      el.addEventListener("pointerdown", handlePointerDown);
    }
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("resize", resize);

    return () => {
      cancelAnimationFrame(rafRef.current);
      if (el) {
        el.removeEventListener("wheel", handleWheel);
        el.removeEventListener("pointerdown", handlePointerDown);
      }
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("resize", resize);
    };
  }, [draw, resize, handleWheel, handlePointerDown, handlePointerMove, handlePointerUp]);

  // Re-resize when grid loads (ensures canvas dimensions are correct)
  useEffect(() => {
    if (gridState) resize();
  }, [gridState, resize]);

  // -------------------------------------------------------------- render

  if (gridError) {
    return (
      <div style={{ padding: 40, color: "#ef4444" }}>
        Grid load error: {String(gridError)}
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      style={{
        width: "100%",
        height: "100%",
        overflow: "hidden",
        position: "relative",
        cursor: draggingRef.current ? "grabbing" : "grab",
      }}
    >
      <canvas ref={canvasRef} style={{ display: "block" }} />
      {isLoading && (
        <div
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            color: "#8b8fa3",
            fontSize: 14,
          }}
        >
          Loading grid...
        </div>
      )}
      {!isLoading && !gridState && !activeZoneId && (
        <div
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            transform: "translate(-50%, -50%)",
            color: "#8b8fa3",
            fontSize: 14,
          }}
        >
          Select a zone to view the map
        </div>
      )}
    </div>
  );
}

function inferType(id: string): string {
  const lower = id.toLowerCase();
  if (lower.includes("k50")) return "K50H";
  if (lower.includes("a42")) return "A42TD";
  return "K50H";
}

import { useEffect, useRef, useCallback } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useWarehouseStore } from "@/stores/useWarehouseStore";
import type { RobotAnimation } from "@/stores/useWarehouseStore";
import { useUiStore } from "@/stores/useUiStore";
import { useGrid } from "@/api/hooks";
import { essApi } from "@/api/ess";
import type { RobotRealtime } from "@/types/robot";

// ------------------------------------------------------------------ constants

const CELL_SIZE = 20;
const ANIMATION_DURATION_MS = 140;

const CELL_COLORS: Record<string, string> = {
  FLOOR: "#1a1d27",
  RACK: "#4a5568",
  STATION: "#3b82f6",
  AISLE: "#2d3148",
  WALL: "#111111",
  CHARGING: "#22c55e",
  IDLE_POINT: "#f59e0b",
};

const ROBOT_COLORS: Record<string, string> = {
  K50H: "#22c55e",
  A42TD: "#3b82f6",
};

export { CELL_SIZE };

// ------------------------------------------------------------------ helpers

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

function inferType(robot: RobotRealtime): string {
  if (robot.type) return robot.type;
  if (robot.name) {
    const lower = robot.name.toLowerCase();
    if (lower.includes("k50")) return "K50H";
    if (lower.includes("a42")) return "A42TD";
  }
  return "K50H";
}

function interpolateRobot(
  robot: RobotRealtime,
  anim: RobotAnimation | undefined,
  now: number,
): { x: number; y: number } {
  if (!anim) {
    return {
      x: robot.col * CELL_SIZE + CELL_SIZE / 2,
      y: robot.row * CELL_SIZE + CELL_SIZE / 2,
    };
  }
  const elapsed = now - anim.startTime;
  const t = Math.min(1, elapsed / ANIMATION_DURATION_MS);
  const eased = easeOutCubic(t);
  const row = anim.fromRow + (anim.toRow - anim.fromRow) * eased;
  const col = anim.fromCol + (anim.toCol - anim.fromCol) * eased;
  return {
    x: col * CELL_SIZE + CELL_SIZE / 2,
    y: row * CELL_SIZE + CELL_SIZE / 2,
  };
}

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
  const dragStartRef = useRef({ x: 0, y: 0 });

  // Editor drag-painting state
  const paintingRef = useRef(false);
  const lastPaintedRef = useRef<string | null>(null);

  const queryClient = useQueryClient();
  const activeZoneId = useUiStore((s) => s.activeZoneId);
  const editorMode = useUiStore((s) => s.editorMode);
  const { data: gridState, isLoading, error: gridError } = useGrid(activeZoneId ?? "");

  // ------------------------------------------------------------ helpers

  /** Convert screen (client) coordinates to grid row/col. */
  const screenToGrid = useCallback(
    (clientX: number, clientY: number): { row: number; col: number } | null => {
      const rect = containerRef.current?.getBoundingClientRect();
      if (!rect) return null;
      const screenX = clientX - rect.left;
      const screenY = clientY - rect.top;
      const zoom = zoomRef.current;
      const pan = panRef.current;
      const worldX = (screenX - pan.x) / zoom;
      const worldY = (screenY - pan.y) / zoom;
      const col = Math.floor(worldX / CELL_SIZE);
      const row = Math.floor(worldY / CELL_SIZE);
      return { row, col };
    },
    [],
  );

  /** Paint a single cell: optimistic cache update + fire-and-forget API call. */
  const paintCell = useCallback(
    (row: number, col: number, cellType: string) => {
      if (!activeZoneId) return;

      // Optimistic cache update
      queryClient.setQueryData(
        ["grid", activeZoneId],
        (old: any) => {
          if (!old) return old;
          // Remove existing cell at this position, then add new one (if not FLOOR)
          const filtered = old.cells.filter(
            (c: any) => !(c.row === row && c.col === col),
          );
          if (cellType !== "FLOOR") {
            filtered.push({ row, col, type: cellType });
          }
          return { ...old, cells: filtered };
        },
      );

      // Fire-and-forget API call
      essApi.gridUpdateCell(row, col, cellType);
    },
    [activeZoneId, queryClient],
  );

  // ------------------------------------------------------------ drawing

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const { robots, stations, robotAnimations, heatmap } = useWarehouseStore.getState();
    const { showPaths, showHeatmap, selectedRobotId } = useUiStore.getState();
    const pan = panRef.current;
    const zoom = zoomRef.current;
    const now = performance.now();

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

      // ---- Editor grid lines ----
      const { editorMode: isEditing } = useUiStore.getState();
      if (isEditing) {
        ctx.strokeStyle = "rgba(255,255,255,0.08)";
        ctx.lineWidth = 0.5;
        for (let r = 0; r <= rows; r++) {
          ctx.beginPath();
          ctx.moveTo(0, r * CELL_SIZE);
          ctx.lineTo(cols * CELL_SIZE, r * CELL_SIZE);
          ctx.stroke();
        }
        for (let c = 0; c <= cols; c++) {
          ctx.beginPath();
          ctx.moveTo(c * CELL_SIZE, 0);
          ctx.lineTo(c * CELL_SIZE, rows * CELL_SIZE);
          ctx.stroke();
        }
      }

      // ---- Heatmap overlay ----
      if (showHeatmap) {
        for (const [key, value] of Object.entries(heatmap)) {
          const parts = key.split(",");
          const r = parseInt(parts[0] ?? "", 10);
          const c = parseInt(parts[1] ?? "", 10);
          if (isNaN(r) || isNaN(c)) continue;
          // Interpolate from yellow (low) to red (high)
          const red = 255;
          const green = Math.round(255 * (1 - value));
          const alpha = 0.15 + value * 0.45;
          ctx.fillStyle = `rgba(${red}, ${green}, 0, ${alpha})`;
          ctx.fillRect(c * CELL_SIZE, r * CELL_SIZE, CELL_SIZE - 1, CELL_SIZE - 1);
        }
      }

      // ---- Tote origin heatmap overlay ----
      const { showToteOriginHeatmap, toteHeatmapMode } = useUiStore.getState();
      const toteOriginHeatmap = useWarehouseStore.getState().toteOriginHeatmap;
      if (showToteOriginHeatmap && toteOriginHeatmap) {
        const cells =
          toteHeatmapMode === "allocated"
            ? toteOriginHeatmap.allocated
            : toteOriginHeatmap.completed;
        if (cells) {
          // Top-N rendering for performance
          const entries = Object.entries(cells);
          entries.sort((a, b) => b[1] - a[1]);
          const topN = entries.slice(0, 300);
          const maxVal = Math.max(1, topN[0]?.[1] ?? 1);
          for (const [key, count] of topN) {
            const parts = key.split(",");
            const r = parseInt(parts[0] ?? "", 10);
            const c = parseInt(parts[1] ?? "", 10);
            if (isNaN(r) || isNaN(c)) continue;
            const norm = count / maxVal;
            const alpha = 0.15 + norm * 0.55;
            ctx.fillStyle =
              toteHeatmapMode === "allocated"
                ? `rgba(139, 92, 246, ${alpha})`
                : `rgba(34, 197, 94, ${alpha})`;
            ctx.fillRect(
              c * CELL_SIZE,
              r * CELL_SIZE,
              CELL_SIZE - 1,
              CELL_SIZE - 1,
            );
            if (norm > 0.3) {
              ctx.fillStyle = "rgba(255,255,255,0.8)";
              ctx.font = "bold 7px monospace";
              ctx.textAlign = "center";
              ctx.fillText(
                String(count),
                c * CELL_SIZE + CELL_SIZE / 2,
                r * CELL_SIZE + CELL_SIZE / 2 + 3,
              );
            }
          }
        }
      }
    }

    // Draw station queue cells (approach, queue, holding) BEFORE station body
    for (const station of stations) {
      // Approach cell — yellow diamond
      if (station.approach_cell_row != null && station.approach_cell_col != null) {
        const ax = station.approach_cell_col * CELL_SIZE + CELL_SIZE / 2;
        const ay = station.approach_cell_row * CELL_SIZE + CELL_SIZE / 2;
        ctx.fillStyle = "rgba(234, 179, 8, 0.35)";
        ctx.fillRect(
          station.approach_cell_col * CELL_SIZE,
          station.approach_cell_row * CELL_SIZE,
          CELL_SIZE - 1,
          CELL_SIZE - 1,
        );
        ctx.strokeStyle = "#eab308";
        ctx.lineWidth = 1;
        ctx.strokeRect(
          station.approach_cell_col * CELL_SIZE + 1,
          station.approach_cell_row * CELL_SIZE + 1,
          CELL_SIZE - 3,
          CELL_SIZE - 3,
        );
        ctx.fillStyle = "#eab308";
        ctx.font = "bold 7px monospace";
        ctx.textAlign = "center";
        ctx.fillText("A", ax, ay + 3);
      }

      // Queue cells — cyan numbered
      if (station.queue_cells && station.queue_cells.length > 0) {
        for (const qc of station.queue_cells) {
          const qx = qc.col * CELL_SIZE + CELL_SIZE / 2;
          const qy = qc.row * CELL_SIZE + CELL_SIZE / 2;
          ctx.fillStyle = "rgba(6, 182, 212, 0.3)";
          ctx.fillRect(qc.col * CELL_SIZE, qc.row * CELL_SIZE, CELL_SIZE - 1, CELL_SIZE - 1);
          ctx.strokeStyle = "#06b6d4";
          ctx.lineWidth = 1;
          ctx.strokeRect(qc.col * CELL_SIZE + 1, qc.row * CELL_SIZE + 1, CELL_SIZE - 3, CELL_SIZE - 3);
          ctx.fillStyle = "#06b6d4";
          ctx.font = "bold 7px monospace";
          ctx.textAlign = "center";
          ctx.fillText(`Q${qc.position + 1}`, qx, qy + 3);
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

    // ---- Draw pending queue cells from editor (unsaved) ----
    const pendingQueueCells = (useUiStore.getState() as any)?._pendingQueueCells;
    if (pendingQueueCells && Array.isArray(pendingQueueCells)) {
      for (let i = 0; i < pendingQueueCells.length; i++) {
        const cell = pendingQueueCells[i];
        const cx = cell.col * CELL_SIZE + CELL_SIZE / 2;
        const cy = cell.row * CELL_SIZE + CELL_SIZE / 2;
        const isApproach = i === 0;
        const isHolding = i === pendingQueueCells.length - 1 && pendingQueueCells.length > 1;
        const color = isApproach ? "#eab308" : isHolding ? "#f97316" : "#06b6d4";

        // Pulsing border
        const pulse = (Math.sin(now / 400 + i) + 1) / 2;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.5 + pulse;
        ctx.strokeRect(
          cell.col * CELL_SIZE + 1,
          cell.row * CELL_SIZE + 1,
          CELL_SIZE - 3,
          CELL_SIZE - 3,
        );
        ctx.fillStyle = color;
        ctx.font = "bold 8px monospace";
        ctx.textAlign = "center";
        const label = isApproach ? "A" : isHolding ? "H" : `Q${i}`;
        ctx.fillText(label, cx, cy + 3);
      }
    }

    // ---- Draw territory overlays (saved + pending edit) ----
    if (gridState) {
      // Draw saved territories from robot data (always visible)
      for (const [, robot] of Object.entries(robots)) {
        if (
          inferType(robot) === "A42TD" &&
          robot.territory_col_min != null &&
          robot.territory_col_max != null
        ) {
          const cMin = robot.territory_col_min;
          const cMax = robot.territory_col_max;
          const rMin = robot.territory_row_min ?? 0;
          const rMax = robot.territory_row_max ?? gridState.rows - 1;
          ctx.fillStyle = "rgba(59, 130, 246, 0.08)";
          ctx.fillRect(
            cMin * CELL_SIZE,
            rMin * CELL_SIZE,
            (cMax - cMin + 1) * CELL_SIZE,
            (rMax - rMin + 1) * CELL_SIZE,
          );
          ctx.strokeStyle = "rgba(59, 130, 246, 0.25)";
          ctx.lineWidth = 1;
          ctx.setLineDash([4, 4]);
          ctx.strokeRect(
            cMin * CELL_SIZE,
            rMin * CELL_SIZE,
            (cMax - cMin + 1) * CELL_SIZE,
            (rMax - rMin + 1) * CELL_SIZE,
          );
          ctx.setLineDash([]);
        }
      }

      // Draw pending territory edit overlay (brighter, pulsing rectangle)
      const uiState = useUiStore.getState() as any;
      const editColMin = uiState?._territoryColMin;
      const editColMax = uiState?._territoryColMax;
      const editRowMin = uiState?._territoryRowMin;
      const editRowMax = uiState?._territoryRowMax;
      const editRobotId = uiState?._territoryRobotId;
      if (editColMin != null && editColMax != null && editRobotId) {
        const cMin = Math.min(editColMin, editColMax);
        const cMax = Math.max(editColMin, editColMax);
        const rMin = editRowMin != null && editRowMax != null ? Math.min(editRowMin, editRowMax) : 0;
        const rMax = editRowMin != null && editRowMax != null ? Math.max(editRowMin, editRowMax) : gridState.rows - 1;
        const pulse = (Math.sin(now / 400) + 1) / 2;
        ctx.fillStyle = `rgba(168, 85, 247, ${0.1 + pulse * 0.1})`;
        ctx.fillRect(
          cMin * CELL_SIZE,
          rMin * CELL_SIZE,
          (cMax - cMin + 1) * CELL_SIZE,
          (rMax - rMin + 1) * CELL_SIZE,
        );
        ctx.strokeStyle = `rgba(168, 85, 247, ${0.4 + pulse * 0.3})`;
        ctx.lineWidth = 2;
        ctx.strokeRect(
          cMin * CELL_SIZE,
          rMin * CELL_SIZE,
          (cMax - cMin + 1) * CELL_SIZE,
          (rMax - rMin + 1) * CELL_SIZE,
        );
      }
    }

    // ---- Draw paths (before robots so they appear behind) ----
    if (showPaths) {
      for (const [id, robot] of Object.entries(robots)) {
        if (!robot.path || robot.path.length === 0) continue;
        const isSelected = id === selectedRobotId;
        const pos = interpolateRobot(robot, robotAnimations[id], now);

        ctx.beginPath();
        ctx.moveTo(pos.x, pos.y);
        for (const [pr, pc] of robot.path) {
          ctx.lineTo(pc * CELL_SIZE + CELL_SIZE / 2, pr * CELL_SIZE + CELL_SIZE / 2);
        }
        ctx.setLineDash([3, 3]);
        ctx.lineWidth = isSelected ? 2 : 1;
        ctx.strokeStyle = isSelected ? "rgba(255,255,255,0.8)" : "rgba(255,255,255,0.25)";
        ctx.stroke();
        ctx.setLineDash([]);

        // Destination marker
        const dest = robot.path[robot.path.length - 1];
        if (!dest) continue;
        const dx = dest[1] * CELL_SIZE + CELL_SIZE / 2;
        const dy = dest[0] * CELL_SIZE + CELL_SIZE / 2;
        ctx.beginPath();
        ctx.arc(dx, dy, 3, 0, Math.PI * 2);
        ctx.fillStyle = isSelected ? "rgba(255,255,255,0.9)" : "rgba(255,255,255,0.35)";
        ctx.fill();
      }
    }

    // ---- Draw robots ----
    for (const [id, robot] of Object.entries(robots)) {
      const anim = robotAnimations[id];
      const pos = interpolateRobot(robot, anim, now);
      const { x, y } = pos;
      const color = ROBOT_COLORS[inferType(robot)] ?? "#9ca3af";
      const isSelected = id === selectedRobotId;

      // WAITING / BLOCKED pulse ring
      if (robot.status === "WAITING" || robot.status === "BLOCKED") {
        const pulsePhase = (Math.sin(now / 300) + 1) / 2; // 0..1
        const pulseRadius = 9 + pulsePhase * 4;
        const pulseColor = robot.status === "BLOCKED"
          ? `rgba(239, 68, 68, ${0.3 + pulsePhase * 0.4})`
          : `rgba(234, 179, 8, ${0.3 + pulsePhase * 0.4})`;
        ctx.beginPath();
        ctx.arc(x, y, pulseRadius, 0, Math.PI * 2);
        ctx.strokeStyle = pulseColor;
        ctx.lineWidth = 2;
        ctx.stroke();
      }

      // Robot body
      ctx.beginPath();
      ctx.arc(x, y, 7, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();

      // Tote indicator — small orange box on robot when carrying a tote
      if (robot.hold_pick_task_id) {
        ctx.fillStyle = "#f59e0b";
        ctx.fillRect(x - 4, y - 4, 8, 8);
        ctx.strokeStyle = "rgba(0,0,0,0.5)";
        ctx.lineWidth = 0.5;
        ctx.strokeRect(x - 4, y - 4, 8, 8);
      }

      // Selection ring
      if (isSelected) {
        ctx.beginPath();
        ctx.arc(x, y, 10, 0, Math.PI * 2);
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2;
        ctx.stroke();
      }

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
      ctx.fillText(robot.name ?? id.slice(0, 6), x, y + 14);

      // Task-type mission label (e.g. →R, ←S)
      if (robot.task_type && robot.status !== "IDLE" && robot.status !== "WAITING_FOR_STATION") {
        const rType = inferType(robot);
        let label = "";
        if (rType === "A42TD") {
          label = robot.task_type === "RETRIEVE" ? "\u2192R" : "\u2190R";
        } else {
          label = robot.task_type === "RETRIEVE" ? "\u2192S" : "\u2190S";
        }
        if (label) {
          ctx.font = "bold 7px monospace";
          ctx.fillStyle = rType === "A42TD" ? "#93c5fd" : "#86efac";
          ctx.textAlign = "left";
          ctx.fillText(label, x + 9, y - 5);
        }
      }
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
    const { editorMode: isEditing, editorTool } = useUiStore.getState();

    if (isEditing && e.button === 0) {
      // Queue tool: single click adds cell to queue config
      if (editorTool === "QUEUE") {
        const cell = screenToGrid(e.clientX, e.clientY);
        if (cell && cell.row >= 0 && cell.col >= 0) {
          const queueClick = (useUiStore.getState() as any)._queueCellClick;
          if (typeof queueClick === "function") {
            queueClick(cell.row, cell.col);
          }
        }
        return;
      }

      // Territory tool: single click sets column range
      if (editorTool === "TERRITORY") {
        const cell = screenToGrid(e.clientX, e.clientY);
        if (cell && cell.row >= 0 && cell.col >= 0) {
          const territoryCellClick = (useUiStore.getState() as any)._territoryCellClick;
          if (typeof territoryCellClick === "function") {
            territoryCellClick(cell.row, cell.col);
          }
        }
        return;
      }

      // Robot tool: single click places or removes a robot
      if (editorTool.startsWith("ROBOT_")) {
        const cell = screenToGrid(e.clientX, e.clientY);
        if (cell && cell.row >= 0 && cell.col >= 0) {
          const robots = useWarehouseStore.getState().robots;
          let deleted = false;
          for (const [id, robot] of Object.entries(robots)) {
            if (robot.row === cell.row && robot.col === cell.col) {
              essApi.deleteRobot(id).catch((err) =>
                alert("Failed to delete robot: " + String(err)),
              );
              deleted = true;
              break;
            }
          }
          if (!deleted) {
            const robotType = editorTool === "ROBOT_K50H" ? "K50H" : "A42TD";
            essApi.createRobot({ type: robotType, row: cell.row, col: cell.col }).catch(
              (err) => alert("Failed to create robot: " + String(err)),
            );
          }
        }
        return;
      }

      // Editor mode: left click starts painting
      paintingRef.current = true;
      lastPaintedRef.current = null;

      const cell = screenToGrid(e.clientX, e.clientY);
      if (cell && cell.row >= 0 && cell.col >= 0) {
        const key = `${cell.row},${cell.col}`;
        paintCell(cell.row, cell.col, editorTool);
        lastPaintedRef.current = key;
      }
      return;
    }

    // Middle click always pans (in editor or normal mode)
    // Left click pans only in normal mode
    if (e.button === 0 || e.button === 1) {
      draggingRef.current = true;
      lastMouseRef.current = { x: e.clientX, y: e.clientY };
      dragStartRef.current = { x: e.clientX, y: e.clientY };
    }
  }, [screenToGrid, paintCell]);

  const handlePointerMove = useCallback((e: PointerEvent) => {
    // Editor drag painting
    if (paintingRef.current) {
      const { editorTool } = useUiStore.getState();
      const cell = screenToGrid(e.clientX, e.clientY);
      if (cell && cell.row >= 0 && cell.col >= 0) {
        const key = `${cell.row},${cell.col}`;
        if (key !== lastPaintedRef.current) {
          paintCell(cell.row, cell.col, editorTool);
          lastPaintedRef.current = key;
        }
      }
      return;
    }

    // Normal panning
    if (!draggingRef.current) return;
    panRef.current.x += e.clientX - lastMouseRef.current.x;
    panRef.current.y += e.clientY - lastMouseRef.current.y;
    lastMouseRef.current = { x: e.clientX, y: e.clientY };
  }, [screenToGrid, paintCell]);

  const handlePointerUp = useCallback((e: PointerEvent) => {
    // End editor painting
    if (paintingRef.current) {
      paintingRef.current = false;
      lastPaintedRef.current = null;
      return;
    }

    if (!draggingRef.current) {
      draggingRef.current = false;
      return;
    }
    draggingRef.current = false;

    // Click detection: if drag distance < 3px, treat as click
    const dx = e.clientX - dragStartRef.current.x;
    const dy = e.clientY - dragStartRef.current.y;
    if (Math.sqrt(dx * dx + dy * dy) >= 3) return;

    // Hit-test robots (10px radius)
    const rect = containerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const screenX = e.clientX - rect.left;
    const screenY = e.clientY - rect.top;
    const zoom = zoomRef.current;
    const pan = panRef.current;
    const wx = (screenX - pan.x) / zoom;
    const wy = (screenY - pan.y) / zoom;

    const { robots } = useWarehouseStore.getState();
    let closestId: string | null = null;
    let closestDist = 20 / zoom; // 20px in screen space (full cell)

    for (const [id, robot] of Object.entries(robots)) {
      const rx = robot.col * CELL_SIZE + CELL_SIZE / 2;
      const ry = robot.row * CELL_SIZE + CELL_SIZE / 2;
      const dist = Math.sqrt((wx - rx) ** 2 + (wy - ry) ** 2);
      if (dist < closestDist) {
        closestDist = dist;
        closestId = id;
      }
    }

    useUiStore.getState().selectRobot(closestId);
  }, [screenToGrid]);

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
        cursor: editorMode ? "crosshair" : draggingRef.current ? "grabbing" : "grab",
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

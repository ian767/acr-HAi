import { useEffect, useRef, useCallback } from "react";
import { Application, Container } from "pixi.js";
import { useWarehouseStore } from "@/stores/useWarehouseStore";
import { useUiStore } from "@/stores/useUiStore";
import { useGrid } from "@/api/hooks";
import { GridLayer } from "./layers/GridLayer";
import { RobotLayer } from "./layers/RobotLayer";
import { StationLayer } from "./layers/StationLayer";
import { PathLayer } from "./layers/PathLayer";

// ------------------------------------------------------------------ constants

const MIN_ZOOM = 0.3;
const MAX_ZOOM = 5;
const ZOOM_STEP = 0.1;

// ------------------------------------------------------------------ component

/**
 * WarehouseMap hosts a PixiJS 8 Application and manages all map layers.
 *
 * It subscribes directly to Zustand stores (outside React re-renders) for
 * high-frequency robot updates and drives the animation loop via
 * `requestAnimationFrame`.
 */
export function WarehouseMap() {
  const containerRef = useRef<HTMLDivElement>(null);
  const appRef = useRef<Application | null>(null);
  const worldRef = useRef<Container | null>(null);
  const layersRef = useRef<{
    grid: GridLayer | null;
    robots: RobotLayer | null;
    stations: StationLayer | null;
    paths: PathLayer | null;
  }>({ grid: null, robots: null, stations: null, paths: null });
  const rafRef = useRef<number>(0);

  // Pan/zoom state stored outside React to avoid re-renders.
  const panRef = useRef({ x: 0, y: 0 });
  const zoomRef = useRef(1);
  const draggingRef = useRef(false);
  const lastMouseRef = useRef({ x: 0, y: 0 });

  const activeZoneId = useUiStore((s) => s.activeZoneId);
  const { data: gridState } = useGrid(activeZoneId ?? "");

  // ------------------------------------------------------------ initialise

  const initApp = useCallback(async () => {
    if (!containerRef.current) return;

    const app = new Application();
    await app.init({
      resizeTo: containerRef.current,
      background: 0x0e1015,
      antialias: true,
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
    });

    containerRef.current.appendChild(app.canvas as HTMLCanvasElement);
    appRef.current = app;

    // World container for pan/zoom.
    const world = new Container();
    world.label = "world";
    app.stage.addChild(world);
    worldRef.current = world;
  }, []);

  // ------------------------------------------------------------ layers

  const buildLayers = useCallback(() => {
    const app = appRef.current;
    const world = worldRef.current;
    if (!app || !world || !gridState) return;

    // Tear down previous layers.
    const prev = layersRef.current;
    prev.grid?.dispose();
    prev.robots?.dispose();
    prev.stations?.dispose();
    prev.paths?.dispose();
    world.removeChildren();

    // Rebuild from scratch for the current grid/zone.
    const grid = new GridLayer(app, gridState);
    const paths = new PathLayer(app);
    const stations = new StationLayer(app);
    const robots = new RobotLayer(app);

    world.addChild(grid.container);
    world.addChild(paths.container);
    world.addChild(stations.container);
    world.addChild(robots.container);

    layersRef.current = { grid, robots, stations, paths };

    // Initial update with current store state.
    const warehouseState = useWarehouseStore.getState();
    const uiState = useUiStore.getState();

    stations.update(warehouseState.stations);
    robots.update(warehouseState.robots, uiState.selectedRobotId, (id) =>
      useUiStore.getState().selectRobot(id),
    );
    paths.update(warehouseState.robots, uiState.showPaths);
  }, [gridState]);

  // ------------------------------------------------------------ animation

  const startLoop = useCallback(() => {
    const tick = () => {
      const { robots: robotLayer, paths: pathLayer, stations: stationLayer } =
        layersRef.current;

      // Pull latest state from stores (no React re-render).
      const { robots, stations } = useWarehouseStore.getState();
      const { selectedRobotId, showPaths } = useUiStore.getState();

      if (robotLayer) {
        robotLayer.update(robots, selectedRobotId, (id) =>
          useUiStore.getState().selectRobot(id),
        );
        robotLayer.interpolate(0.15);
      }

      if (stationLayer) {
        stationLayer.update(stations);
      }

      if (pathLayer) {
        pathLayer.update(robots, showPaths);
      }

      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
  }, []);

  // ------------------------------------------------------------ pan / zoom

  const applyTransform = useCallback(() => {
    const world = worldRef.current;
    if (!world) return;
    world.scale.set(zoomRef.current);
    world.x = panRef.current.x;
    world.y = panRef.current.y;
  }, []);

  const handleWheel = useCallback(
    (e: WheelEvent) => {
      e.preventDefault();
      const direction = e.deltaY < 0 ? 1 : -1;
      const oldZoom = zoomRef.current;
      const newZoom = Math.min(
        MAX_ZOOM,
        Math.max(MIN_ZOOM, oldZoom + direction * ZOOM_STEP * oldZoom),
      );

      // Zoom toward the cursor position.
      const rect = containerRef.current?.getBoundingClientRect();
      if (rect) {
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        const scale = newZoom / oldZoom;
        panRef.current.x = mouseX - (mouseX - panRef.current.x) * scale;
        panRef.current.y = mouseY - (mouseY - panRef.current.y) * scale;
      }

      zoomRef.current = newZoom;
      applyTransform();
    },
    [applyTransform],
  );

  const handlePointerDown = useCallback((e: PointerEvent) => {
    // Only pan on middle-click or left-click on the background.
    if (e.button === 1 || e.button === 0) {
      draggingRef.current = true;
      lastMouseRef.current = { x: e.clientX, y: e.clientY };
    }
  }, []);

  const handlePointerMove = useCallback(
    (e: PointerEvent) => {
      if (!draggingRef.current) return;
      const dx = e.clientX - lastMouseRef.current.x;
      const dy = e.clientY - lastMouseRef.current.y;
      panRef.current.x += dx;
      panRef.current.y += dy;
      lastMouseRef.current = { x: e.clientX, y: e.clientY };
      applyTransform();
    },
    [applyTransform],
  );

  const handlePointerUp = useCallback(() => {
    draggingRef.current = false;
  }, []);

  // ------------------------------------------------------------ resize

  const handleResize = useCallback(() => {
    const app = appRef.current;
    if (app) {
      app.resize();
    }
  }, []);

  // ------------------------------------------------------------ lifecycle

  useEffect(() => {
    let destroyed = false;

    const setup = async () => {
      await initApp();
      if (destroyed) return;
      startLoop();
    };

    void setup();

    return () => {
      destroyed = true;
      cancelAnimationFrame(rafRef.current);

      const { grid, robots, stations, paths } = layersRef.current;
      grid?.dispose();
      robots?.dispose();
      stations?.dispose();
      paths?.dispose();

      if (appRef.current) {
        appRef.current.destroy(true, { children: true });
        appRef.current = null;
      }
    };
  }, [initApp, startLoop]);

  // Rebuild layers when grid data changes.
  useEffect(() => {
    if (gridState && appRef.current) {
      buildLayers();
    }
  }, [gridState, buildLayers]);

  // Bind DOM event listeners for pan/zoom.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    el.addEventListener("wheel", handleWheel, { passive: false });
    el.addEventListener("pointerdown", handlePointerDown);
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp);
    window.addEventListener("resize", handleResize);

    return () => {
      el.removeEventListener("wheel", handleWheel);
      el.removeEventListener("pointerdown", handlePointerDown);
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
      window.removeEventListener("resize", handleResize);
    };
  }, [handleWheel, handlePointerDown, handlePointerMove, handlePointerUp, handleResize]);

  // -------------------------------------------------------------- render

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
    />
  );
}

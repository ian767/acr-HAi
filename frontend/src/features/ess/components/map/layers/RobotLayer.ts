import {
  Application,
  Container,
  Graphics,
  Text,
  TextStyle,
  RenderTexture,
  Sprite,
} from "pixi.js";
import type { RobotRealtime, RobotType } from "@/types/robot";
import { CELL_SIZE } from "./GridLayer";

const ROBOT_RADIUS = 7;
const SELECTED_RING_RADIUS = 10;

const ROBOT_TYPE_COLORS: Record<RobotType, number> = {
  K50H: 0x22c55e,
  A42TD: 0x3b82f6,
};

const DEFAULT_ROBOT_COLOR = 0x9ca3af;
const SELECTED_OUTLINE_COLOR = 0xfacc15;

/**
 * Per-robot pooled sprite entry.  Keeps the visual objects and the
 * interpolation state for smooth movement between real-time updates.
 */
interface RobotSprite {
  container: Container;
  body: Sprite;
  ring: Graphics;
  label: Text;
  // Interpolation state
  displayX: number;
  displayY: number;
  targetX: number;
  targetY: number;
}

const LABEL_STYLE = new TextStyle({
  fontFamily: "monospace",
  fontSize: 10,
  fill: 0xffffff,
  align: "center",
});

/**
 * RobotLayer manages a sprite pool of robot visuals.  On each `update()`
 * call it reconciles the pool against the incoming robot dictionary, reusing
 * existing sprites and hiding any that are no longer needed.
 *
 * It also performs linear interpolation each frame so robots glide smoothly
 * between the discrete grid positions delivered by the WebSocket.
 */
export class RobotLayer {
  public readonly container: Container;

  private app: Application;
  private pool: Map<string, RobotSprite> = new Map();
  private bodyTextures: Map<string, RenderTexture> = new Map();

  constructor(app: Application) {
    this.app = app;
    this.container = new Container();
    this.container.label = "RobotLayer";
    this.container.eventMode = "static";
  }

  /**
   * Reconcile the visual pool against the latest robot state.
   * `onSelect` is invoked when the user clicks a robot body.
   */
  update(
    robots: Record<string, RobotRealtime>,
    selectedId: string | null,
    onSelect?: (id: string) => void,
  ): void {
    const activeIds = new Set<string>();

    for (const [id, robot] of Object.entries(robots)) {
      activeIds.add(id);

      const targetX = robot.col * CELL_SIZE + CELL_SIZE / 2;
      const targetY = robot.row * CELL_SIZE + CELL_SIZE / 2;

      let entry = this.pool.get(id);

      if (!entry) {
        entry = this.createRobotSprite(id, robot, onSelect);
        this.pool.set(id, entry);
        this.container.addChild(entry.container);
      }

      // Update target position for interpolation.
      entry.targetX = targetX;
      entry.targetY = targetY;

      // Update visuals that may change each tick.
      entry.container.visible = true;

      // Selected ring visibility.
      entry.ring.visible = id === selectedId;

      // Heading rotation (apply to the body sprite).
      entry.body.rotation = (robot.heading * Math.PI) / 180;
    }

    // Hide sprites that are no longer in the data set.
    for (const [id, entry] of this.pool) {
      if (!activeIds.has(id)) {
        entry.container.visible = false;
      }
    }
  }

  /**
   * Call every animation frame to advance the position interpolation.
   * `alpha` is the lerp factor (0-1, typically ~0.15 for 60 fps smoothing).
   */
  interpolate(alpha: number = 0.15): void {
    for (const entry of this.pool.values()) {
      if (!entry.container.visible) continue;

      entry.displayX += (entry.targetX - entry.displayX) * alpha;
      entry.displayY += (entry.targetY - entry.displayY) * alpha;

      entry.container.x = entry.displayX;
      entry.container.y = entry.displayY;
    }
  }

  /** Clean up all GPU resources. */
  dispose(): void {
    for (const entry of this.pool.values()) {
      entry.container.destroy({ children: true });
    }
    this.pool.clear();

    for (const tex of this.bodyTextures.values()) {
      tex.destroy(true);
    }
    this.bodyTextures.clear();
  }

  // ------------------------------------------------------------------ private

  private getBodyTexture(robotType: string): RenderTexture {
    if (this.bodyTextures.has(robotType)) {
      return this.bodyTextures.get(robotType)!;
    }

    const color =
      ROBOT_TYPE_COLORS[robotType as RobotType] ?? DEFAULT_ROBOT_COLOR;

    const gfx = new Graphics();

    // Filled circle for the robot body.
    gfx.circle(ROBOT_RADIUS, ROBOT_RADIUS, ROBOT_RADIUS);
    gfx.fill({ color });

    // Small heading indicator triangle.
    gfx.moveTo(ROBOT_RADIUS, 0);
    gfx.lineTo(ROBOT_RADIUS + 3, ROBOT_RADIUS - 2);
    gfx.lineTo(ROBOT_RADIUS - 3, ROBOT_RADIUS - 2);
    gfx.closePath();
    gfx.fill({ color: 0xffffff, alpha: 0.7 });

    const size = ROBOT_RADIUS * 2 + 2;
    const texture = RenderTexture.create({ width: size, height: size });
    this.app.renderer.render({ container: gfx, target: texture });
    gfx.destroy();

    this.bodyTextures.set(robotType, texture);
    return texture;
  }

  private createRobotSprite(
    id: string,
    robot: RobotRealtime,
    onSelect?: (id: string) => void,
  ): RobotSprite {
    const wrapper = new Container();
    wrapper.label = `robot-${id}`;

    // Selection ring (drawn fresh each time visibility changes).
    const ring = new Graphics();
    ring.circle(0, 0, SELECTED_RING_RADIUS);
    ring.stroke({ color: SELECTED_OUTLINE_COLOR, width: 2 });
    ring.visible = false;
    wrapper.addChild(ring);

    // Determine type from the id prefix heuristic or fallback.
    const robotType = this.inferType(id);
    const texture = this.getBodyTexture(robotType);
    const body = new Sprite(texture);
    body.anchor.set(0.5);
    body.eventMode = "static";
    body.cursor = "pointer";
    if (onSelect) {
      body.on("pointerdown", (e) => {
        e.stopPropagation();
        onSelect(id);
      });
    }
    wrapper.addChild(body);

    // Name label beneath the robot.
    const label = new Text({
      text: id.slice(0, 8),
      style: LABEL_STYLE,
    });
    label.anchor.set(0.5, 0);
    label.y = ROBOT_RADIUS + 2;
    wrapper.addChild(label);

    // Initial position snapped to the grid target (no lerp on first frame).
    const startX = robot.col * CELL_SIZE + CELL_SIZE / 2;
    const startY = robot.row * CELL_SIZE + CELL_SIZE / 2;

    return {
      container: wrapper,
      body,
      ring,
      label,
      displayX: startX,
      displayY: startY,
      targetX: startX,
      targetY: startY,
    };
  }

  /**
   * Infer the robot type from its id.  In production the server would include
   * the type on the real-time payload; here we fall back to a heuristic.
   */
  private inferType(id: string): string {
    const lower = id.toLowerCase();
    if (lower.includes("k50")) return "K50H";
    if (lower.includes("a42")) return "A42TD";
    return "K50H";
  }
}

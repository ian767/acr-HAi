import { Application, Container, Graphics } from "pixi.js";
import type { RobotRealtime } from "@/types/robot";
import { CELL_SIZE } from "./GridLayer";

const PATH_COLOR = 0xfacc15;
const PATH_WIDTH = 2;

/**
 * PathLayer draws the planned path for every robot that has a `path` array.
 * Each segment fades with an alpha gradient so the leading edge of the path
 * is bright and the tail fades away, giving a "trail" look.
 */
export class PathLayer {
  public readonly container: Container;

  private gfx: Graphics;

  constructor(_app: Application) {
    this.container = new Container();
    this.container.label = "PathLayer";

    this.gfx = new Graphics();
    this.container.addChild(this.gfx);
  }

  /**
   * Redraw all robot paths.
   * @param robots  Current real-time robot state.
   * @param visible Whether path visualisation is enabled.
   */
  update(robots: Record<string, RobotRealtime>, visible: boolean): void {
    this.gfx.clear();

    if (!visible) {
      this.container.visible = false;
      return;
    }

    this.container.visible = true;

    for (const robot of Object.values(robots)) {
      const path = robot.path;
      if (!path || path.length < 2) continue;

      this.drawPath(path, robot.row, robot.col);
    }
  }

  /** Release resources. */
  dispose(): void {
    this.gfx.destroy();
  }

  // ------------------------------------------------------------------ private

  private drawPath(
    path: [number, number][],
    currentRow: number,
    currentCol: number,
  ): void {
    // Prepend the robot's current position so the line starts from the robot.
    const points: [number, number][] = [[currentRow, currentCol], ...path];
    const segmentCount = points.length - 1;

    for (let i = 0; i < segmentCount; i++) {
      const [r1, c1] = points[i]!;
      const [r2, c2] = points[i + 1]!;

      // Alpha gradient: brighter near the robot, fading toward the destination.
      const alpha = 1.0 - (i / segmentCount) * 0.8;

      const x1 = c1 * CELL_SIZE + CELL_SIZE / 2;
      const y1 = r1 * CELL_SIZE + CELL_SIZE / 2;
      const x2 = c2 * CELL_SIZE + CELL_SIZE / 2;
      const y2 = r2 * CELL_SIZE + CELL_SIZE / 2;

      this.gfx.moveTo(x1, y1);
      this.gfx.lineTo(x2, y2);
      this.gfx.stroke({ color: PATH_COLOR, width: PATH_WIDTH, alpha });
    }
  }
}

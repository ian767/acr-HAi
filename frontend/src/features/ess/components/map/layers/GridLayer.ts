import { Application, Container, Graphics, RenderTexture, Sprite } from "pixi.js";
import type { GridState, CellType } from "@/types/grid";

const CELL_SIZE = 20;

const CELL_COLORS: Record<CellType, number> = {
  FLOOR: 0x1a1d27,
  RACK: 0x4a5568,
  CANTILEVER: 0xeab308,
  STATION: 0x3b82f6,
  AISLE: 0x2d3148,
  WALL: 0x111111,
  CHARGING: 0x22c55e,
};

/**
 * GridLayer renders the static warehouse grid as a single pre-baked texture
 * for optimal draw-call performance.  Call `rebuild()` when the grid data
 * changes (e.g. zone switch).  The layer is added directly to the provided
 * parent container so the caller controls z-ordering.
 */
export class GridLayer {
  public readonly container: Container;

  private sprite: Sprite | null = null;
  private texture: RenderTexture | null = null;
  private app: Application;

  constructor(app: Application, gridState: GridState) {
    this.app = app;
    this.container = new Container();
    this.container.label = "GridLayer";
    this.build(gridState);
  }

  /** Tear down the old texture and rebuild from new grid data. */
  rebuild(gridState: GridState): void {
    this.dispose();
    this.build(gridState);
  }

  /** Free GPU resources. */
  dispose(): void {
    if (this.sprite) {
      this.container.removeChild(this.sprite);
      this.sprite.destroy();
      this.sprite = null;
    }
    if (this.texture) {
      this.texture.destroy(true);
      this.texture = null;
    }
  }

  // ------------------------------------------------------------------ private

  private build(gridState: GridState): void {
    const { rows, cols, cells } = gridState;

    const width = cols * CELL_SIZE;
    const height = rows * CELL_SIZE;

    // Build a lookup map for fast cell type resolution.
    // Default to FLOOR for cells not explicitly listed.
    const cellMap = new Map<string, CellType>();
    for (const cell of cells) {
      cellMap.set(`${cell.row},${cell.col}`, cell.type);
    }

    // Draw every cell into a temporary Graphics object.
    const gfx = new Graphics();

    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const cellType = cellMap.get(`${r},${c}`) ?? "FLOOR";
        const color = CELL_COLORS[cellType];
        gfx.rect(c * CELL_SIZE, r * CELL_SIZE, CELL_SIZE - 1, CELL_SIZE - 1);
        gfx.fill({ color });
      }
    }

    // Bake the grid into a RenderTexture so the entire grid is a single quad.
    this.texture = RenderTexture.create({ width, height });
    this.app.renderer.render({ container: gfx, target: this.texture });

    this.sprite = new Sprite(this.texture);
    this.container.addChild(this.sprite);

    // The temporary Graphics can be destroyed immediately.
    gfx.destroy();
  }
}

export { CELL_SIZE };

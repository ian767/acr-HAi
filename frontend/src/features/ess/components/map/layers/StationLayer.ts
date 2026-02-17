import { Application, Container, Graphics, Text, TextStyle } from "pixi.js";
import type { Station } from "@/types/station";
import { CELL_SIZE } from "./GridLayer";

const MARKER_SIZE = 14;
const STATION_COLOR = 0x3b82f6;
const STATION_OFFLINE_COLOR = 0x6b7280;

const LABEL_STYLE = new TextStyle({
  fontFamily: "monospace",
  fontSize: 9,
  fill: 0xffffff,
  align: "center",
});

interface StationMarker {
  container: Container;
  marker: Graphics;
  label: Text;
}

/**
 * StationLayer renders labelled markers at each station's grid position.
 * Markers are pooled and reused across updates.
 */
export class StationLayer {
  public readonly container: Container;

  private markers: Map<string, StationMarker> = new Map();

  constructor(_app: Application) {
    this.container = new Container();
    this.container.label = "StationLayer";
  }

  /** Reconcile station markers with the current station list. */
  update(stations: Station[]): void {
    const activeIds = new Set<string>();

    for (const station of stations) {
      activeIds.add(station.id);

      const x = station.grid_col * CELL_SIZE + CELL_SIZE / 2;
      const y = station.grid_row * CELL_SIZE + CELL_SIZE / 2;

      let entry = this.markers.get(station.id);

      if (!entry) {
        entry = this.createMarker(station);
        this.markers.set(station.id, entry);
        this.container.addChild(entry.container);
      }

      // Update position.
      entry.container.x = x;
      entry.container.y = y;
      entry.container.visible = true;

      // Update colour based on online status.
      const color = station.is_online ? STATION_COLOR : STATION_OFFLINE_COLOR;
      entry.marker.clear();
      entry.marker.roundRect(
        -MARKER_SIZE / 2,
        -MARKER_SIZE / 2,
        MARKER_SIZE,
        MARKER_SIZE,
        3,
      );
      entry.marker.fill({ color });
      entry.marker.stroke({ color: 0xffffff, width: 1, alpha: 0.4 });

      // Update label text in case the name changed.
      entry.label.text = station.name;
    }

    // Hide markers no longer present.
    for (const [id, entry] of this.markers) {
      if (!activeIds.has(id)) {
        entry.container.visible = false;
      }
    }
  }

  /** Free all resources. */
  dispose(): void {
    for (const entry of this.markers.values()) {
      entry.container.destroy({ children: true });
    }
    this.markers.clear();
  }

  // ------------------------------------------------------------------ private

  private createMarker(station: Station): StationMarker {
    const wrapper = new Container();
    wrapper.label = `station-${station.id}`;

    const marker = new Graphics();
    wrapper.addChild(marker);

    const label = new Text({
      text: station.name,
      style: LABEL_STYLE,
    });
    label.anchor.set(0.5, 0);
    label.y = MARKER_SIZE / 2 + 2;
    wrapper.addChild(label);

    return { container: wrapper, marker, label };
  }
}

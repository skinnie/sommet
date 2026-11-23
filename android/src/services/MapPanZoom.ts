// Pan and pinch for the small tile maps inside pages (TrackPreview), André 2026-11-21: "press
// and drag". A view is a centre (as a 0..1 fraction of the world, so it does not depend on
// the zoom) and a fractional zoom; tiles are drawn at floor(zoom), stretched by 2^(zoom - z).
import { TILE_SIZE, TileMosaic, worldPixel, worldToLatLon } from './MapTile';

export interface MapView { u: number; v: number; zf: number }

export const MIN_ZOOM = 2;
export const MAX_ZOOM = 18;

const clampZoom = (zf: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zf));
const wrap01 = (u: number) => u - Math.floor(u);
const clamp01 = (v: number) => Math.min(1, Math.max(0, v));

/** The view a fitted mosaic shows, so the first touch starts exactly where the picture was. */
export function viewFromMosaic(m: TileMosaic): MapView {
  const world = TILE_SIZE * 2 ** m.z;
  return { u: m.centerWorld.x / world, v: m.centerWorld.y / world, zf: m.z + Math.log2(m.scale) };
}

export interface ViewLayout {
  z: number;
  tiles: { x: number; y: number; key: string; left: number; top: number; size: number }[];
  project: (lat: number, lon: number) => { x: number; y: number };
  latLonAt: (x: number, y: number) => { lat: number; lon: number };
}

/** Tiles and projection for a view in a box of w x h display pixels. */
export function layoutView(view: MapView, w: number, h: number): ViewLayout {
  const z = Math.max(0, Math.min(MAX_ZOOM, Math.floor(view.zf + 1e-9)));
  const s = 2 ** (view.zf - z);
  const world = TILE_SIZE * 2 ** z;
  const originX = view.u * world - w / 2 / s;
  const originY = view.v * world - h / 2 / s;
  const max = 2 ** z - 1;
  const tiles: ViewLayout['tiles'] = [];
  const x0 = Math.floor(originX / TILE_SIZE), x1 = Math.floor((originX + w / s) / TILE_SIZE);
  const y0 = Math.max(0, Math.floor(originY / TILE_SIZE)), y1 = Math.min(max, Math.floor((originY + h / s) / TILE_SIZE));
  for (let ty = y0; ty <= y1; ty++) {
    for (let tx = x0; tx <= x1; tx++) {
      tiles.push({ x: ((tx % (max + 1)) + max + 1) % (max + 1), y: ty, key: `${z}-${tx}-${ty}`,
                   left: (tx * TILE_SIZE - originX) * s, top: (ty * TILE_SIZE - originY) * s, size: TILE_SIZE * s });
    }
  }
  return {
    z, tiles,
    project: (lat, lon) => {
      const p = worldPixel(lat, lon, z);
      return { x: (p.x - originX) * s, y: (p.y - originY) * s };
    },
    latLonAt: (x, y) => worldToLatLon(originX + x / s, originY + y / s, z),
  };
}

/** The same view centred on one point, zoomed in to at least `minZoom` (a POI picked in a list). */
export function centredOn(view: MapView, lat: number, lon: number, minZoom: number): MapView {
  const p = worldPixel(lat, lon, 0);
  return { u: wrap01(p.x / TILE_SIZE), v: clamp01(p.y / TILE_SIZE), zf: clampZoom(Math.max(view.zf, minZoom)) };
}

/** Moves the map with the finger: a drag of (dx, dy) display pixels. */
export function panView(view: MapView, dx: number, dy: number): MapView {
  const world = TILE_SIZE * 2 ** view.zf;
  return { ...view, u: wrap01(view.u - dx / world), v: clamp01(view.v - dy / world) };
}

/** Pinch: the zoom the view had when the two fingers landed, times their spread ratio. */
export function zoomView(view: MapView, startZoom: number, ratio: number): MapView {
  return { ...view, zf: clampZoom(startZoom + Math.log2(Math.max(ratio, 1e-9))) };
}

import { MapProvider } from './MapProviderService';

// Real, 2026-08-09 ("I would prefer an immediate map view on this side") - a single static
// XYZ tile image per route/POI/activity thumbnail (rendered via plain RN <Image>s, not a
// WebView). TrackPreview.tsx's own header comment explains the real constraint this works
// around: N simultaneous live WebView+Leaflet instances in a list is the same
// hardware-confirmed crash risk desktop's own ActivitiesPage.qml documents for N live map
// widgets. Desktop's RoutesPage.qml/PoisPage.qml sidestep that by using MapView.qml, a
// cheap in-process tile renderer with no browser engine involved - this is the RN
// equivalent of "cheap enough not to need virtualization": a handful of small cached image
// requests per thumbnail, the same real cost as any photo grid, not a browser instance.

const TILE_SIZE = 256;

// Real, 2026-08-10 ("change color of the routes to something visible, elegant and
// thicker... it is grey, not very visible") - TrackPreview/MapScreen briefly drew tracks in
// theme.primary, copying desktop's own real pattern (Theme.primary + a white halo). That
// works on desktop because desktop's dark-mode primary is a teal (#57C9B3); Android's own
// v3 primary was deliberately changed to a muted slate grey earlier this session (a
// separate, real, explicit request) - reusing it for a map overlay was the mistake, not
// the pattern: grey-on-beige-street-tile is genuinely low contrast, confirmed live. Map
// tiles have their own fixed, mostly-light palette regardless of the app's own light/dark
// mode, so this is a fixed color, not theme-driven - a teal with good contrast against
// typical OSM/IGN colors (beige/tan roads, green parks, white background, blue water),
// calmer than the red it replaced.
//
// 2026-08-15 (André, design-parity audit: desktop is the baseline): this is desktop's
// map-overlay color, Theme.qml's `mapAccent` = its _lightPrimary #167E6A - the exact same
// fixed, theme-independent teal desktop draws every track/POI marker in (MapView.qml
// strokeStyle, PoisPage.qml). Was #00897B, a near-but-not-equal Material teal that made the
// two apps' maps visibly disagree; matched to desktop's value so a route drawn here is the
// same green as the same route on desktop.
export const TRACK_COLOR = '#167E6A';

// OpenStreetMap's tile policy blocks requests that don't name their app: React Native's <Image>
// sends okhttp's own User-Agent, and every list thumbnail on the OpenStreetMap layer came back
// as the "403 Access blocked" tile (André, 2026-11-19). Send this with every tile request, as
// the move page's WebView, TileCache and the weather lookup already do.
export const TILE_HEADERS = { 'User-Agent': 'Sommet/2.0' };

export function tileUrl(provider: MapProvider, z: number, x: number, y: number): string {
  switch (provider) {
    case 'osm':
      return `https://tile.openstreetmap.org/${z}/${x}/${y}.png`;
    case 'cyclosm':
    default:
      return `https://a.tile-cyclosm.openstreetmap.fr/cyclosm/${z}/${x}/${y}.png`;
  }
}

// Real Web Mercator world-pixel coordinates at zoom z (continuous, not snapped to a tile) -
// the same formula every slippy map uses (MapView.qml's own header comment calls it "the
// same formula every slippy map uses"; IGN's WMTS TILEMATRIXSET=PM is the same
// Pseudo-Mercator XYZ scheme, see MapHtml.ts).
function worldPixel(lat: number, lon: number, z: number): { x: number; y: number } {
  const latRad = (lat * Math.PI) / 180;
  const world = TILE_SIZE * 2 ** z;
  return {
    x: world * ((lon + 180) / 360),
    y: world * ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2),
  };
}

export interface TileMosaic {
  z: number;
  contentWidth: number;
  contentHeight: number;
  tiles: { x: number; y: number; left: number; top: number; size: number }[];
  // Real lat/lon -> display-pixel coordinates within [0,contentWidth] x [0,contentHeight].
  project: (lat: number, lon: number) => { x: number; y: number };
}

interface FitOpts {
  // Height to fit/center within, instead of computing one from the bounding box. Omit for
  // "fill targetWidth exactly, whatever height that needs" (routes); pass it for "fit
  // inside this exact box, centered, letterboxed if needed" (activities/POIs - a uniform
  // grid where every cell must be the same size).
  targetHeight?: number;
  padding?: number;
  maxZoom?: number;
  maxTiles?: number;
  // Real bug, found live ("routes on the map seem very big" - Garmin) - only applies when
  // targetHeight is omitted: the auto-computed height has no natural upper bound, so a
  // route with a real elongated (much taller than wide) bounding box demanded an equally
  // huge card. See fitBoundsMosaic's own use of this below.
  maxAutoHeight?: number;
}

function buildMosaic(
  centerWorld: { x: number; y: number },
  bboxW: number, bboxH: number,
  z: number, targetWidth: number, targetHeight: number | undefined, padding: number,
): TileMosaic {
  const scale = targetHeight === undefined
    ? targetWidth / (bboxW + padding * 2)
    : Math.min(targetWidth / (bboxW + padding * 2), targetHeight / (bboxH + padding * 2));
  const contentWidth = targetWidth;
  const contentHeight = targetHeight ?? (bboxH + padding * 2) * scale;

  // Center the bbox's own center in the content box - not top-left+padding - so any extra
  // slack (the non-binding axis, when targetHeight forces a smaller scale than the bbox
  // alone would need) is distributed evenly on both sides instead of piling up on one edge.
  const originX = centerWorld.x - contentWidth / 2 / scale;
  const originY = centerWorld.y - contentHeight / 2 / scale;
  const toDisplay = (worldX: number, worldY: number) => ({
    x: (worldX - originX) * scale,
    y: (worldY - originY) * scale,
  });

  const maxTileIndex = 2 ** z - 1;
  const firstTileX = Math.max(0, Math.floor(originX / TILE_SIZE));
  const firstTileY = Math.max(0, Math.floor(originY / TILE_SIZE));
  const lastTileX = Math.min(maxTileIndex, Math.floor((originX + contentWidth / scale) / TILE_SIZE));
  const lastTileY = Math.min(maxTileIndex, Math.floor((originY + contentHeight / scale) / TILE_SIZE));

  const tiles: TileMosaic['tiles'] = [];
  for (let ty = firstTileY; ty <= lastTileY; ty++) {
    for (let tx = firstTileX; tx <= lastTileX; tx++) {
      const tl = toDisplay(tx * TILE_SIZE, ty * TILE_SIZE);
      tiles.push({ x: tx, y: ty, left: tl.x, top: tl.y, size: TILE_SIZE * scale });
    }
  }

  return {
    z, contentWidth, contentHeight, tiles,
    project: (lat, lon) => {
      const p = worldPixel(lat, lon, z);
      return toDisplay(p.x, p.y);
    },
  };
}

// Real, 2026-08-10 ("apply the same fix as we did on the desktop, centering the gpx for
// POIs and Routes on the maps") - desktop's real fix (MapView.qml's own header comment,
// point 3): "currentZoom auto-fits to the track's real bounding box... instead of the
// caller's own averaged trackCenter() + a fixed guessed zoomLevel". This is that fit,
// ported: the real bounding box of every point, centered, at the deepest zoom whose native
// tiles still fit the given box - stitching however many tiles that needs (desktop's own
// MapView.qml is a continuous in-process renderer with no tile-count cost; this is the
// tile-mosaic equivalent). `opts.targetHeight` omitted (routes - see TrackPreview.tsx's own
// header comment on "allow the cards to have different height to fit the gpx completely" -
// the real output height here, not letterboxed/cropped) vs provided (activities/POIs - a
// fixed grid cell, letterboxed and centered instead).
export function fitBoundsMosaic(
  points: { lat: number; lon: number }[],
  targetWidth: number,
  opts: FitOpts = {},
): TileMosaic {
  const padding = opts.padding ?? 20;
  const maxZoom = opts.maxZoom ?? 18;
  const maxTiles = opts.maxTiles ?? 12;
  const maxAutoHeight = opts.maxAutoHeight ?? 420;
  let targetHeight = opts.targetHeight;

  const lats = points.map(p => p.lat);
  const lons = points.map(p => p.lon);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);

  const availableWidth = Math.max(targetWidth - padding * 2, 30);
  const availableHeight = targetHeight !== undefined ? Math.max(targetHeight - padding * 2, 30) : Infinity;

  let z = maxZoom;
  for (; z >= 0; z--) {
    const p0 = worldPixel(minLat, minLon, z);
    const p1 = worldPixel(maxLat, maxLon, z);
    const w = p1.x - p0.x;
    const h = p0.y - p1.y; // maxLat -> smaller y (north is up)
    const tilesAcross = Math.ceil((w + padding * 2) / TILE_SIZE) + 1;
    const tilesDown = Math.ceil((h + padding * 2) / TILE_SIZE) + 1;
    if (w <= availableWidth && h <= availableHeight && tilesAcross * tilesDown <= maxTiles) break;
  }
  if (z < 0) z = 0;

  const p0 = worldPixel(minLat, minLon, z);
  const p1 = worldPixel(maxLat, maxLon, z);
  const bboxW = Math.max(p1.x - p0.x, 1);
  const bboxH = Math.max(p0.y - p1.y, 1);
  const center = worldPixel((minLat + maxLat) / 2, (minLon + maxLon) / 2, z);

  // The clamp itself: if nothing constrained the height (routes - see maxAutoHeight's own
  // comment above) and the real bbox aspect ratio would need more than maxAutoHeight at the
  // width-driven scale, fall back to the same letterboxed/centered fit buildMosaic already
  // does for a real targetHeight (activities/POIs) - just with maxAutoHeight as that height,
  // instead of letting the card grow without bound.
  if (targetHeight === undefined) {
    const scaleW = targetWidth / (bboxW + padding * 2);
    const naturalHeight = (bboxH + padding * 2) * scaleW;
    if (naturalHeight > maxAutoHeight) targetHeight = maxAutoHeight;
  }

  return buildMosaic(center, bboxW, bboxH, z, targetWidth, targetHeight, padding);
}

// Same real centering fix, for a single point (a POI, or an activity/route with only one
// real GPS fix) - degenerates fitBoundsMosaic's bounding box to zero-size at a comfortably
// close zoom instead of falling through its own zoom search (which would otherwise pick
// the deepest zoom available for a point, more detail than a marker preview needs).
export function centeredPointMosaic(
  lat: number, lon: number, targetWidth: number, targetHeight: number, zoom = 15,
): TileMosaic {
  const center = worldPixel(lat, lon, zoom);
  // Real bug, found live (2026-08-10): a single point is a genuinely zero-size bounding
  // box, and buildMosaic's scale formula is targetDim/(bboxDim + padding*2) - with both
  // bboxW/bboxH AND padding at 0 here, that's targetDim/0 = Infinity, which propagates into
  // every tile's left/top/width/height as Infinity/NaN and RN just silently renders
  // nothing. Passing the target box's own dimensions as the "bbox" makes the scale formula
  // resolve to exactly 1 (native tile resolution, no upscaling) - bboxW/H otherwise has no
  // other effect on buildMosaic (centering is purely center+contentWidth/Height driven).
  return buildMosaic(center, targetWidth, targetHeight, zoom, targetWidth, targetHeight, 0);
}

export { TILE_SIZE };

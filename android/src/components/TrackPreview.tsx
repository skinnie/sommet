import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { View, Image, Text, StyleSheet, LayoutChangeEvent, PanResponder, GestureResponderEvent } from 'react-native';
import Svg, { Circle, Polyline } from 'react-native-svg';
import { useFocusEffect } from '@react-navigation/native';
import { useV3Theme } from '../theme/v3';
import { getMapProvider, MapProvider } from '../services/MapProviderService';
import { fitBoundsMosaic, centeredPointMosaic, tileUrl, TileMosaic, TRACK_COLOR, TILE_HEADERS } from '../services/MapTile';
import { t } from '../i18n';
import { MapView as PanZoomView, layoutView, panView, viewFromMosaic, zoomView } from '../services/MapPanZoom';
import { useScrollLock } from './ScrollLock';

// Touch (André, 2026-11-21: "press and drag and for poi, double tap"). A quick swipe on the map
// still scrolls the page; a finger held still for HOLD_MS grabs the map (a green frame shows
// it), the page stops scrolling and the drag moves the map. Two fingers pinch-zoom. A
// double-tap calls onDoubleTap with the point (the POI form puts the POI there).
const HOLD_MS = 300;
const SLOP = 10;
const DOUBLE_TAP_MS = 300;

// v3.0 UI port, revised 2026-08-10 ("apply the same fix as we did on the desktop, centering
// the gpx for POIs and Routes on the maps. for routes allow the cards to have different
// height to allow gpx to completely fit and show") - desktop's real fix (MapView.qml's own
// header comment) was fitting AND centering on the track's real bounding box, at whatever
// aspect ratio it actually has, instead of a fixed guessed zoom around an averaged center.
// The previous version here (bestSingleTile) picked whichever single fixed-size grid tile
// happened to contain every point - not centered, and cropped to a square regardless of the
// route's real shape. This uses MapTile.ts's own fitBoundsMosaic/centeredPointMosaic
// instead, stitching however many tiles the real bounding box needs.
//
// Two-phase render: the real display width isn't known until after the first layout pass
// (this box is always width:'100%' of whatever row/card it's in), so the first render is
// just a plain placeholder that measures itself; the real mosaic (which needs that width to
// pick a zoom/scale) renders on the next pass.
export function TrackPreview({
  points, height = 100, markerOnly = false, variableHeight = false, multiMarker = false,
  interactive = true, onDoubleTap, fallbackCenter,
}: {
  // No point yet (the POI form before a place is chosen): show the map here, without a pin.
  fallbackCenter?: { lat: number; lon: number };
  // false: a fixed picture (activity cards, where a long-press already deletes the move).
  interactive?: boolean;
  onDoubleTap?: (lat: number, lon: number) => void;
  points: { lat: number; lon: number }[];
  height?: number;
  markerOnly?: boolean;
  // Real, 2026-08-10 ("for routes allow the cards to have different height to allow gpx to
  // completely fit and show") - default false: every other caller (POIs, activities) is a
  // uniform grid/list where a consistent cell size matters more than showing the shape at
  // 100% - RouteScreen's own on-watch list is the one real opt-in.
  variableHeight?: boolean;
  // Real, 2026-08-10 ("desktop version has more functions... like the map with locations")
  // - Kailash's visited-places list is a set of independent points, not a connected track;
  // the existing hasMultiPoint path (below) always draws a polyline through every point in
  // array order, which would draw nonsense lines between unrelated cities. This mode fits
  // the same bounding box but draws each point as its own marker instead, no polyline.
  multiMarker?: boolean;
}) {
  const theme = useV3Theme();
  const [provider, setProvider] = useState<MapProvider>('cyclosm');
  // Real bug, found live (2026-08-10) - a plain useEffect([]) only fetches the persisted
  // provider once, on mount. React Navigation's native-stack keeps RouteScreen/PoiScreen
  // mounted in the background for back-navigation, so a TrackPreview that mounted before a
  // Settings change kept showing the OLD provider forever after - it never re-rendered to
  // notice. useFocusEffect re-checks every time the screen holding this component becomes
  // visible again, same pattern RouteScreen.tsx's own loadOnWatch() already uses.
  useFocusEffect(useCallback(() => { getMapProvider().then(setProvider); }, []));
  const [boxWidth, setBoxWidth] = useState<number | null>(null);

  // ---- pan and pinch (null view = the fitted picture, until the first grab) ----
  const scrollLock = useScrollLock();
  const [view, setView] = useState<PanZoomView | null>(null);
  const [grabbed, setGrabbed] = useState(false);
  const viewRef = useRef<PanZoomView | null>(null);
  const fittedRef = useRef<PanZoomView | null>(null);
  const layoutRef = useRef<ReturnType<typeof layoutView> | null>(null);
  const g = useRef({ armed: false, locked: false, timer: null as ReturnType<typeof setTimeout> | null,
                     x0: 0, y0: 0, t0: 0, moved: false, lastX: 0, lastY: 0,
                     pinchD0: 0, pinchZ0: 0, lastTapT: 0, lastTapX: 0, lastTapY: 0 }).current;
  const pointsKey = points.map(p => `${p.lat},${p.lon}`).join(';');
  useEffect(() => { viewRef.current = null; setView(null); }, [pointsKey]);   // new data: fit again
  const setV = (v: PanZoomView) => { viewRef.current = v; setView(v); };
  const release = () => {
    if (g.timer) { clearTimeout(g.timer); g.timer = null; }
    g.armed = false; g.pinchD0 = 0;
    if (g.locked) { g.locked = false; scrollLock.unlock(); }
    setGrabbed(false);
  };
  const grab = () => {
    g.armed = true;
    if (!g.locked) { g.locked = true; scrollLock.lock(); }
    if (!viewRef.current && fittedRef.current) setV(fittedRef.current);
    setGrabbed(true);
  };
  useEffect(() => () => { if (g.timer) clearTimeout(g.timer); if (g.locked) scrollLock.unlock(); }, []);   // eslint-disable-line react-hooks/exhaustive-deps
  const spread = (e: GestureResponderEvent) => {
    const ts = e.nativeEvent.touches;
    return ts.length < 2 ? 0 : Math.hypot(ts[0].pageX - ts[1].pageX, ts[0].pageY - ts[1].pageY);
  };
  const responder = useMemo(() => PanResponder.create({
    onStartShouldSetPanResponder: () => false,
    onMoveShouldSetPanResponder: () => g.armed,
    onMoveShouldSetPanResponderCapture: () => g.armed,
    onPanResponderTerminationRequest: () => false,
    onPanResponderGrant: (_e, gs) => { g.lastX = gs.moveX; g.lastY = gs.moveY; },
    onPanResponderMove: (e, gs) => {
      const v = viewRef.current; if (!v) return;
      const d = spread(e);
      if (d > 0) {
        if (!g.pinchD0) { g.pinchD0 = d; g.pinchZ0 = v.zf; }
        setV(zoomView(v, g.pinchZ0, d / g.pinchD0));
      } else {
        g.pinchD0 = 0;
        setV(panView(v, gs.moveX - g.lastX, gs.moveY - g.lastY));
      }
      g.lastX = gs.moveX; g.lastY = gs.moveY;
    },
    onPanResponderRelease: () => release(),
    onPanResponderTerminate: () => release(),
  }), []);   // eslint-disable-line react-hooks/exhaustive-deps
  const touchHandlers = interactive ? {
    onTouchStart: (e: GestureResponderEvent) => {
      const ts = e.nativeEvent.touches;
      if (ts.length >= 2) { grab(); return; }
      g.x0 = e.nativeEvent.pageX; g.y0 = e.nativeEvent.pageY; g.t0 = Date.now(); g.moved = false;
      if (g.timer) clearTimeout(g.timer);
      g.timer = setTimeout(() => { g.timer = null; if (!g.moved) grab(); }, HOLD_MS);
    },
    onTouchMove: (e: GestureResponderEvent) => {
      if (Math.hypot(e.nativeEvent.pageX - g.x0, e.nativeEvent.pageY - g.y0) > SLOP) {
        g.moved = true;
        if (!g.armed && g.timer) { clearTimeout(g.timer); g.timer = null; }   // a swipe: the page scrolls
      }
    },
    onTouchEnd: (e: GestureResponderEvent) => {
      if (e.nativeEvent.touches.length > 0) return;
      const wasArmed = g.armed;
      const quickTap = !g.moved && Date.now() - g.t0 < HOLD_MS;
      release();
      if (wasArmed || !quickTap || !onDoubleTap) return;
      const now = Date.now(), x = e.nativeEvent.locationX, y = e.nativeEvent.locationY;
      if (now - g.lastTapT < DOUBLE_TAP_MS && Math.hypot(x - g.lastTapX, y - g.lastTapY) < 40) {
        g.lastTapT = 0;
        const at = layoutRef.current ? layoutRef.current.latLonAt(x, y) : null;
        if (at) onDoubleTap(at.lat, at.lon);
      } else { g.lastTapT = now; g.lastTapX = x; g.lastTapY = y; }
    },
    onTouchCancel: () => release(),
  } : {};

  // Real bug, found live (2026-08-10) - some real synced activities carry a garbage GPS fix
  // (an out-of-range or non-finite lat/lon - the watch's own GPS chip emitting a value
  // before it has a real lock). Math.tan()/Math.log() on an out-of-range latitude produces
  // Infinity/NaN, which silently poisons every tile position downstream with no crash and
  // no visible error - RN just renders nothing. Same "skip the bad one, don't throw" spirit
  // PoiService.ts's own SBEM entry parsing already uses for malformed watch data.
  const validPoints = points.filter(p =>
    Number.isFinite(p.lat) && p.lat >= -90 && p.lat <= 90 &&
    Number.isFinite(p.lon) && p.lon >= -180 && p.lon <= 180
  );

  const hasMultiMarker = multiMarker && validPoints.length >= 1;
  const hasMultiPoint = !markerOnly && !multiMarker && validPoints.length >= 2;
  const hasSinglePoint = !multiMarker && (markerOnly || validPoints.length === 1) && validPoints.length >= 1;

  // Real, 2026-08-10 ("for data without gps data, please do a nice mappyish image saying no
  // data, soft color to not hurt the rest") - was a flat, empty box; a real placeholder now,
  // the same map-shaped visual language (rounded card, a faint terrain-line glyph) instead
  // of just nothing.
  const centerOnly = !hasMultiPoint && !hasSinglePoint && !hasMultiMarker && !!fallbackCenter
    && Number.isFinite(fallbackCenter.lat) && Number.isFinite(fallbackCenter.lon);
  if (!hasMultiPoint && !hasSinglePoint && !hasMultiMarker && !centerOnly) {
    return (
      <View style={[styles.box, styles.noDataBox, { height, backgroundColor: theme.background }]}>
        <Svg width={30} height={30} viewBox="0 0 24 24">
          <Circle cx={17} cy={7} r={2.6} stroke={theme.mutedText} strokeWidth={1.3} fill="none" opacity={0.45} />
          <Polyline
            points="3,19 8,11 12,14 20,4" fill="none" stroke={theme.mutedText} strokeWidth={1.3}
            strokeLinecap="round" strokeLinejoin="round" opacity={0.3}
          />
        </Svg>
        <Text style={[styles.noDataText, { color: theme.mutedText }]}>{t.trackPreviewNoData}</Text>
      </View>
    );
  }

  // Re-measured on every layout, not only the first: turning the tablet changes the width, and a
  // mosaic fitted to the old width left the track off-centre and cut at the edge (2026-11-12).
  const onLayout = (e: LayoutChangeEvent) => {
    const w = Math.round(e.nativeEvent.layout.width);
    if (w > 0 && w !== boxWidth) setBoxWidth(w);
  };

  if (boxWidth === null) {
    return <View style={[styles.box, { height, backgroundColor: theme.background }]} onLayout={onLayout} />;
  }

  const mosaic: TileMosaic = centerOnly
    ? centeredPointMosaic(fallbackCenter!.lat, fallbackCenter!.lon, boxWidth, height, 13)
    : hasSinglePoint
    ? centeredPointMosaic(validPoints[0].lat, validPoints[0].lon, boxWidth, height, 15)
    : hasMultiMarker && validPoints.length === 1
    ? centeredPointMosaic(validPoints[0].lat, validPoints[0].lon, boxWidth, height, 6)
    : fitBoundsMosaic(validPoints, boxWidth, variableHeight ? {} : { targetHeight: height });

  fittedRef.current = viewFromMosaic(mosaic);
  // Once grabbed, the owner's view; before that, the fitted picture (the same view, to the pixel).
  const lay = layoutView(view ?? fittedRef.current, mosaic.contentWidth, mosaic.contentHeight);
  layoutRef.current = lay;
  const drawn = view ? { z: lay.z, tiles: lay.tiles, project: lay.project } : mosaic;
  const projected = validPoints.map(p => drawn.project(p.lat, p.lon));

  return (
    <View style={[styles.box, { height: mosaic.contentHeight, backgroundColor: theme.background }]} onLayout={onLayout}
          {...touchHandlers} {...(interactive ? responder.panHandlers : {})}>
      <View style={StyleSheet.absoluteFill} pointerEvents="none">
      {drawn.tiles.map(tile => (
        <Image
          key={'key' in tile ? (tile as { key: string }).key : `${drawn.z}-${tile.x}-${tile.y}`}
          source={{ uri: tileUrl(provider, drawn.z, tile.x, tile.y), headers: TILE_HEADERS }}
          style={{ position: 'absolute', left: tile.left, top: tile.top, width: tile.size, height: tile.size }}
        />
      ))}
      <Svg width={mosaic.contentWidth} height={mosaic.contentHeight} style={StyleSheet.absoluteFill}>
        {centerOnly ? null : hasMultiMarker ? (
          projected.map((p, i) => (
            <React.Fragment key={i}>
              <Circle cx={p.x} cy={p.y} r={12} fill={TRACK_COLOR} opacity={0.18} />
              <Circle cx={p.x} cy={p.y} r={7} fill="#ffffff" stroke={TRACK_COLOR} strokeWidth={2} />
              <Circle cx={p.x} cy={p.y} r={3} fill={TRACK_COLOR} />
            </React.Fragment>
          ))
        ) : hasSinglePoint ? (
          // Real, 2026-08-10 ("POI: make the marker of a color better visible", then "it is
          // grey, not very visible" once theme.primary turned out to be Android's own muted
          // slate grey) - a white disc (visible against any tile color) with a TRACK_COLOR
          // ring and center dot, TRACK_COLOR being the same fixed, map-specific teal used
          // for the route line below (see MapTile.ts's own header comment on why this is
          // fixed, not theme.primary).
          <>
            <Circle cx={projected[0].x} cy={projected[0].y} r={16} fill={TRACK_COLOR} opacity={0.18} />
            <Circle cx={projected[0].x} cy={projected[0].y} r={10} fill="#ffffff" stroke={TRACK_COLOR} strokeWidth={2.5} />
            <Circle cx={projected[0].x} cy={projected[0].y} r={4} fill={TRACK_COLOR} />
          </>
        ) : (
          <>
            {/* Real, 2026-08-10 ("make the route even more thicker...it is better than
                before but not optimal") - third pass on thickness (4 -> 6), halo scaled up
                to match (6.5 -> 9) so it still reads as a clean border around the main
                line, not an uneven smear. TRACK_COLOR is a fixed, theme-independent teal -
                see MapTile.ts's own header comment. */}
            <Polyline
              points={projected.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')}
              fill="none" stroke="#ffffff" strokeWidth={9} strokeLinecap="round" strokeLinejoin="round"
            />
            <Polyline
              points={projected.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ')}
              fill="none" stroke={TRACK_COLOR} strokeWidth={6} strokeLinecap="round" strokeLinejoin="round"
            />
          </>
        )}
      </Svg>
      {grabbed && <View style={[StyleSheet.absoluteFill, styles.grabbed]} />}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  box: { width: '100%', borderRadius: 12, overflow: 'hidden' },
  noDataBox: { alignItems: 'center', justifyContent: 'center', gap: 6 },
  noDataText: { fontSize: 11, fontWeight: '600' },
  grabbed: { borderWidth: 2, borderColor: TRACK_COLOR, borderRadius: 12 },
});

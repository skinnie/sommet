import React, { useEffect, useState } from 'react';
import { readGpxFile } from '../services/GpxService';
import { parseTrackPoints } from '../services/GpxParser';
import { TrackPreview } from './TrackPreview';
import { SportPicture } from './SportPicture';

// v3.0 UI port (2026-08-09, "re do activities... to match entirely desktop") - desktop's
// ActivitiesPage.qml shows a live embedded map per card, real request "activities... just a
// grid, per Apple Photos". Ported here as the same lightweight SVG track shape
// TrackPreview.tsx already uses for Routes/POIs, not a live map - a plain local GPX file
// read + parse is cheap (no watch/network round trip), unlike desktop's own live-map-per-card
// pattern, which its own ActivitiesPage.qml header comment documents as a real,
// hardware-confirmed crash risk (too many simultaneous live map instances).
//
// Module-level cache (path -> points), not component state - the same GPX file gets
// re-parsed on every scroll-in/out otherwise (FlatList unmounts/remounts offscreen rows),
// and this data never changes for an already-synced activity.
const cache = new Map<string, { lat: number; lon: number }[]>();

// A move whose file holds no positions (pool swim, indoor ride) shows its sport instead
// (move-page/03, SportPicture.tsx).
export function ActivityThumbnail({ gpxPath, height = 90, sport, seed }: {
  gpxPath: string; height?: number; sport?: string | null; seed?: string;
}) {
  const [points, setPoints] = useState<{ lat: number; lon: number }[] | null>(cache.get(gpxPath) ?? null);

  useEffect(() => {
    if (cache.has(gpxPath)) { setPoints(cache.get(gpxPath)!); return; }
    let cancelled = false;
    readGpxFile(gpxPath)
      .then(xml => {
        const pts = parseTrackPoints(xml).map(p => ({ lat: p.latitude, lon: p.longitude }));
        cache.set(gpxPath, pts);
        if (!cancelled) setPoints(pts);
      })
      .catch(() => { if (!cancelled) setPoints([]); });
    return () => { cancelled = true; };
  }, [gpxPath]);

  if (points === null) return null;
  if (points.length === 0) return <SportPicture sport={sport} seed={seed ?? gpxPath} height={height} />;
  return <TrackPreview points={points} height={height} />;
}

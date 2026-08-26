import React from 'react';
import Svg, { Path, Circle, Line, Rect } from 'react-native-svg';

// Outlined, 24dp grid, 2px stroke — same set drawn for the black-and-white
// UI mockup. Paths are 1:1 ports of that mockup's <symbol> defs.

export type IconName =
  | 'sync' | 'satellite' | 'route' | 'poi' | 'backup' | 'settings'
  | 'list' | 'link' | 'map' | 'chart' | 'activity' | 'key' | 'person'
  | 'delete' | 'check' | 'info' | 'battery' | 'warning' | 'mountain'
  | 'watch' | 'etrex' | 'sun' | 'moon' | 'auto' | 'chevronLeft' | 'chevronRight'
  | 'cycling' | 'running' | 'walking'
  | 'play' | 'pause' | 'skipBack' | 'skipForward'
  | 'download' | 'upload' | 'calendar' | 'bluetooth'
  // Nav-rail icons for the screens ported from desktop (2026-08-26). Each mirrors the Material
  // Symbol the desktop's Icons.qml picks for the same page, so the two rails read alike:
  // coach = forum, weight = monitor_weight, health = monitor_heart, ember = local_fire_department.
  // Before this they all fell back to 'chart' and the rail showed four identical bar charts.
  | 'coach' | 'weight' | 'health' | 'ember';

interface Props {
  name: IconName;
  size?: number;
  color?: string;
}

export default function Icon({ name, size = 20, color = '#000' }: Props) {
  const s = { stroke: color, strokeWidth: 1.9, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, fill: 'none' };

  switch (name) {
    case 'sync':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M4 12a8 8 0 0 1 8-8c2.5 0 4.7 1.2 6 3" {...s} />
          <Path d="M20 3v5h-5" {...s} />
          <Path d="M20 12a8 8 0 0 1-8 8c-2.5 0-4.7-1.2-6-3" {...s} />
          <Path d="M4 21v-5h5" {...s} />
        </Svg>
      );
    case 'satellite':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={6} cy={18} r={2} stroke={color} strokeWidth={1.9} fill="none" />
          <Path d="M9 15l6-6" {...s} />
          <Path d="M13 7l4-4" {...s} />
          <Path d="M15 5a6 6 0 0 1 4 4" {...s} />
          <Path d="M17.5 2.5a9 9 0 0 1 4 4" {...s} />
        </Svg>
      );
    case 'route':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={5} cy={19} r={2} stroke={color} strokeWidth={1.9} fill="none" />
          <Circle cx={19} cy={5} r={2} stroke={color} strokeWidth={1.9} fill="none" />
          <Path d="M6.5 17.5C9 13 6 10 9 7c2-2 5 1 8-2" {...s} />
        </Svg>
      );
    case 'poi':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M12 21s7-7.2 7-12a7 7 0 1 0-14 0c0 4.8 7 12 7 12z" {...s} />
          <Circle cx={12} cy={9} r={2.4} stroke={color} strokeWidth={1.9} fill="none" />
        </Svg>
      );
    case 'backup':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M7 18a4 4 0 0 1-.6-7.96A5.5 5.5 0 0 1 17 8.5a4.5 4.5 0 0 1 .5 8.98" {...s} />
          <Path d="M12 21v-8" {...s} />
          <Path d="M9 15l3-3 3 3" {...s} />
        </Svg>
      );
    case 'settings':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={12} cy={12} r={3.2} stroke={color} strokeWidth={1.9} fill="none" />
          <Line x1={12} y1={2} x2={12} y2={5} {...s} />
          <Line x1={12} y1={19} x2={12} y2={22} {...s} />
          <Line x1={2} y1={12} x2={5} y2={12} {...s} />
          <Line x1={19} y1={12} x2={22} y2={12} {...s} />
          <Line x1={4.9} y1={4.9} x2={7} y2={7} {...s} />
          <Line x1={17} y1={17} x2={19.1} y2={19.1} {...s} />
          <Line x1={4.9} y1={19.1} x2={7} y2={17} {...s} />
          <Line x1={17} y1={7} x2={19.1} y2={4.9} {...s} />
        </Svg>
      );
    case 'list':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Line x1={8} y1={6} x2={20} y2={6} {...s} />
          <Circle cx={4} cy={6} r={1.3} fill={color} stroke="none" />
          <Line x1={8} y1={12} x2={20} y2={12} {...s} />
          <Circle cx={4} cy={12} r={1.3} fill={color} stroke="none" />
          <Line x1={8} y1={18} x2={20} y2={18} {...s} />
          <Circle cx={4} cy={18} r={1.3} fill={color} stroke="none" />
        </Svg>
      );
    case 'link':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M8 12l-2.5 2.5a3.5 3.5 0 0 0 5 5L13 17" {...s} />
          <Path d="M16 12l2.5-2.5a3.5 3.5 0 0 0-5-5L11 7" {...s} />
          <Path d="M9.5 14.5l5-5" {...s} />
        </Svg>
      );
    // Multi-watch switcher (2026-08-16): transport marker for a paired watch. Feather's
    // bluetooth rune, one polyline.
    case 'bluetooth':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M6.5 6.5 L17.5 17.5 L12 23 L12 1 L17.5 6.5 L6.5 17.5" {...s} />
        </Svg>
      );
    case 'coach':
      // forum: a speech bubble with a second one behind it — the Coach page is a chat.
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M8 3h11a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2h-1v3l-3.5-3H8a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z" {...s} />
          <Path d="M6 17H5a2 2 0 0 1-2-2V8" {...s} />
        </Svg>
      );
    case 'weight':
      // monitor_weight: a square scale with a dial arc and needle. The arc sits low and spans
      // most of the width on purpose - a smaller, higher arc with a longer needle read as a
      // question mark in a box at 22dp on the nav rail (seen on the tablet, 2026-08-26).
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Rect x={3} y={3} width={18} height={18} rx={3} {...s} />
          <Path d="M7 14a5 5 0 0 1 10 0" {...s} />
          <Line x1={12} y1={14} x2={9.5} y2={11} {...s} />
        </Svg>
      );
    case 'health':
      // monitor_heart: a heart with an ECG trace across it.
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M20.4 8.6a4.6 4.6 0 0 0-8.4-2.6 4.6 4.6 0 0 0-8.4 2.6c0 .5.1 1 .2 1.4h3.7l1.4-2.4 2 5 1.6-3.1 1 1.1h5.9c.1-.5.2-1 .2-1.5z" {...s} />
          <Path d="M4.6 12.2c1.3 3 4.8 5.6 7.4 7.4 2.6-1.8 6.1-4.4 7.4-7.4" {...s} />
        </Svg>
      );
    case 'ember':
      // local_fire_department: the flame Ember (the fasting PWA) is named for.
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M12 2c.6 3-1.2 4.4-2.7 5.9C7.5 9.6 6 11.3 6 14a6 6 0 0 0 12 0c0-2.6-1.3-4.4-2.7-6.1-.7 1-1.5 1.6-2.3 1.8.5-2.6-.2-5.6-1-7.7z" {...s} />
          <Path d="M12 21a3 3 0 0 1-3-3c0-1.6 1.4-2.4 3-4.5 1.6 2.1 3 2.9 3 4.5a3 3 0 0 1-3 3z" {...s} />
        </Svg>
      );
    case 'map':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M9 4L4 6v14l5-2 6 2 5-2V4l-5 2-6-2z" {...s} />
          <Line x1={9} y1={4} x2={9} y2={18} {...s} />
          <Line x1={15} y1={6} x2={15} y2={20} {...s} />
        </Svg>
      );
    case 'chart':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Line x1={5} y1={20} x2={5} y2={12} {...s} />
          <Line x1={12} y1={20} x2={12} y2={7} {...s} />
          <Line x1={19} y1={20} x2={19} y2={15} {...s} />
          <Line x1={3} y1={20} x2={21} y2={20} {...s} />
        </Svg>
      );
    case 'activity':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M3 12h4l2-7 4 14 2-7h6" {...s} />
        </Svg>
      );
    // Calendar screen link (2026-08-13, desktop CalendarPage.qml parity). Same 24dp/1.9
    // outline language as the rest of this set: a month grid with a torn-off header band.
    case 'calendar':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Rect x={3.5} y={5} width={17} height={16} rx={2.5} stroke={color} strokeWidth={1.9} fill="none" />
          <Line x1={3.5} y1={9.5} x2={20.5} y2={9.5} {...s} />
          <Line x1={8} y1={3} x2={8} y2={6.5} {...s} />
          <Line x1={16} y1={3} x2={16} y2={6.5} {...s} />
          <Circle cx={8} cy={14} r={1.1} fill={color} stroke="none" />
          <Circle cx={12} cy={14} r={1.1} fill={color} stroke="none" />
          <Circle cx={16} cy={14} r={1.1} fill={color} stroke="none" />
          <Circle cx={8} cy={17.5} r={1.1} fill={color} stroke="none" />
          <Circle cx={12} cy={17.5} r={1.1} fill={color} stroke="none" />
        </Svg>
      );
    // Real, 2026-08-10 ("change the orange icons to something more aligned with our
    // material design and colors") - LogListScreen's activity-type filter chips/labels
    // used raw emoji (🚴🏃🥾) for icons - full-color glyphs baked into the system emoji
    // font, impossible to tint, and visibly clashing with this app's own monochrome
    // outline icon language everywhere else. Same 24dp/1.9-stroke style as the rest of
    // this set, tintable like every other icon here.
    case 'cycling':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={6} cy={17} r={3.2} stroke={color} strokeWidth={1.9} fill="none" />
          <Circle cx={18} cy={17} r={3.2} stroke={color} strokeWidth={1.9} fill="none" />
          <Path d="M6 17l4-8h4l4 8M10 9l3 5h5M13 14l-3.5 3" {...s} />
          <Circle cx={15.5} cy={5.5} r={1.5} fill={color} stroke="none" />
        </Svg>
      );
    case 'running':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={14.5} cy={4.5} r={1.6} fill={color} stroke="none" />
          <Path d="M12.5 7l-2.5 2.5 3 1.5-1 4-4.5 4.5M13 8l3 2-1 3.5 4 1.5" {...s} />
        </Svg>
      );
    case 'walking':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={13} cy={4.5} r={1.6} fill={color} stroke="none" />
          <Path d="M12 7v5l-3 2v6M12 12l3 1.5v6.5" {...s} />
        </Svg>
      );
    // Real, 2026-08-10 ("I still see the orange buttons on activities") - MapScreen's
    // replay bar (rewind/play-pause/fast-forward) used raw emoji (⏪▶️⏸⏩), same root cause
    // as the activity-type icons: Android's own emoji font bakes a colored (orange/amber)
    // shape into these specific glyphs, uncontrollable via style/color. Plain filled
    // triangles/bars instead - fully tintable like every other icon here.
    case 'play':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M6 4l14 8-14 8V4z" fill={color} stroke="none" />
        </Svg>
      );
    case 'pause':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Rect x={5} y={4} width={5} height={16} rx={1.2} fill={color} stroke="none" />
          <Rect x={14} y={4} width={5} height={16} rx={1.2} fill={color} stroke="none" />
        </Svg>
      );
    // Real, 2026-08-11 (André, A1: "Still the ugly orange buttons inside activity").
    // MapScreen's two floating buttons still drew raw arrows (⬇ ⬆) as text - the same root
    // cause as the replay bar and the activity chips before them: Android's emoji font bakes
    // a colored glyph in and no style can tint it. Outline arrows in the same 24dp/1.9
    // stroke as the rest of this set.
    case 'download':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M12 3v12M7 11l5 5 5-5M4 20h16" {...s} />
        </Svg>
      );
    case 'upload':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M12 21V9M7 13l5-5 5 5M4 4h16" {...s} />
        </Svg>
      );
    case 'skipBack':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M12 4l-9 8 9 8V4z" fill={color} stroke="none" />
          <Path d="M21 4l-9 8 9 8V4z" fill={color} stroke="none" />
        </Svg>
      );
    case 'skipForward':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M12 4l9 8-9 8V4z" fill={color} stroke="none" />
          <Path d="M3 4l9 8-9 8V4z" fill={color} stroke="none" />
        </Svg>
      );
    case 'key':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={7} cy={15} r={4} stroke={color} strokeWidth={1.9} fill="none" />
          <Path d="M10 12l9-9" {...s} />
          <Path d="M15 6l3 3" {...s} />
          <Path d="M18 3l3 3" {...s} />
        </Svg>
      );
    case 'person':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={12} cy={8} r={4} stroke={color} strokeWidth={1.9} fill="none" />
          <Path d="M4 20c0-4 4-6 8-6s8 2 8 6" {...s} />
        </Svg>
      );
    case 'delete':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M4 7h16" {...s} />
          <Path d="M9 7V4h6v3" {...s} />
          <Path d="M6 7l1 13h10l1-13" {...s} />
          <Line x1={10} y1={11} x2={10} y2={17} {...s} />
          <Line x1={14} y1={11} x2={14} y2={17} {...s} />
        </Svg>
      );
    case 'check':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={12} cy={12} r={9} stroke={color} strokeWidth={1.9} fill="none" />
          <Path d="M8 12.5l2.5 2.5 5.5-6" {...s} />
        </Svg>
      );
    case 'info':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={12} cy={12} r={9} stroke={color} strokeWidth={1.9} fill="none" />
          <Line x1={12} y1={11} x2={12} y2={16} {...s} />
          <Circle cx={12} cy={7.6} r={0.9} fill={color} stroke="none" />
        </Svg>
      );
    case 'battery':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Rect x={2.5} y={8} width={16} height={8} rx={2} stroke={color} strokeWidth={1.9} fill="none" />
          <Line x1={21} y1={11} x2={21} y2={13} {...s} />
          <Rect x={4.5} y={10} width={10} height={4} rx={0.5} fill={color} stroke="none" />
        </Svg>
      );
    case 'warning':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M12 3.5L21.5 20h-19L12 3.5z" {...s} />
          <Line x1={12} y1={10} x2={12} y2={14.5} {...s} />
          <Circle cx={12} cy={17.3} r={0.9} fill={color} stroke="none" />
        </Svg>
      );
    case 'mountain':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M2 18.5L6.5 9.5L9.5 13L13.5 5.5L17.5 12L20 9L22 18.5Z" {...s} />
        </Svg>
      );
    case 'watch':
      // Suunto Ambit3 Peak silhouette — round bezel, top/bottom strap
      // lugs, four side buttons, and a small mountain on the dial to tie
      // it back to the app mark (see Ambit3_Peak_Icon.png reference).
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Rect x={9.3} y={0.8} width={5.4} height={5} rx={1.6} stroke={color} strokeWidth={1.9} fill="none" />
          <Rect x={9.3} y={18.2} width={5.4} height={5} rx={1.6} stroke={color} strokeWidth={1.9} fill="none" />
          <Circle cx={12} cy={12} r={6.6} stroke={color} strokeWidth={1.9} fill="none" />
          <Circle cx={17.6} cy={8.6} r={1} fill={color} stroke="none" />
          <Circle cx={17.6} cy={15.4} r={1} fill={color} stroke="none" />
          <Circle cx={6.4} cy={8.6} r={1} fill={color} stroke="none" />
          <Circle cx={6.4} cy={15.4} r={1} fill={color} stroke="none" />
          <Path d="M8 15.2l2-4 1.6 2 2.4-5 2 4.7" stroke={color} strokeWidth={1.4} strokeLinecap="round" strokeLinejoin="round" fill="none" />
        </Svg>
      );
    case 'etrex':
      // Garmin eTrex 10 silhouette — tall rounded handheld body, screen,
      // top-right round button, side D-pad/menu nubs (see etrex10.jpg).
      // Real, 2026-08-10 ("adjust the etrex screen position a bit down to not touch the
      // circle that represents the joystick, give it a little spacing to match the
      // original device") - the screen's top edge (was y=6.6) sat only 0.3 units below the
      // button circle's own bottom edge (y=3.9+2.4=6.3) - practically touching at real icon
      // sizes. Moved down to y=8 for real, visible separation.
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Rect x={6} y={1} width={12} height={22} rx={4} stroke={color} strokeWidth={1.9} fill="none" />
          <Rect x={8} y={8} width={8} height={8.6} rx={1} stroke={color} strokeWidth={1.9} fill="none" />
          <Circle cx={14.6} cy={5.1} r={1.2} stroke={color} strokeWidth={1.5} fill="none" />
          <Line x1={6} y1={10.5} x2={4.6} y2={10.5} {...s} />
          <Line x1={18} y1={14.5} x2={19.4} y2={14.5} {...s} />
        </Svg>
      );
    case 'sun':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={12} cy={12} r={4.2} stroke={color} strokeWidth={1.9} fill="none" />
          <Line x1={12} y1={1.5} x2={12} y2={4} {...s} />
          <Line x1={12} y1={20} x2={12} y2={22.5} {...s} />
          <Line x1={1.5} y1={12} x2={4} y2={12} {...s} />
          <Line x1={20} y1={12} x2={22.5} y2={12} {...s} />
          <Line x1={4.5} y1={4.5} x2={6.2} y2={6.2} {...s} />
          <Line x1={17.8} y1={17.8} x2={19.5} y2={19.5} {...s} />
          <Line x1={4.5} y1={19.5} x2={6.2} y2={17.8} {...s} />
          <Line x1={17.8} y1={6.2} x2={19.5} y2={4.5} {...s} />
        </Svg>
      );
    case 'moon':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a6.8 6.8 0 0 0 10.5 10.5z" {...s} />
        </Svg>
      );
    case 'auto':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Circle cx={12} cy={12} r={9} stroke={color} strokeWidth={1.9} fill="none" />
          <Path d="M12 3a9 9 0 0 1 0 18z" fill={color} stroke="none" />
        </Svg>
      );
    case 'chevronLeft':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M15 4.5L7.5 12l7.5 7.5" {...s} />
        </Svg>
      );
    case 'chevronRight':
      return (
        <Svg width={size} height={size} viewBox="0 0 24 24">
          <Path d="M9 4.5L16.5 12L9 19.5" {...s} />
        </Svg>
      );
    default:
      return null;
  }
}

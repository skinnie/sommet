import { ACTIVITY_VIEW_LOGIC_SRC, ACTIVITY_CHART_DRAW_SRC } from '../config/activityChartSources';

// The activity screen's chart page for the Android app (André, 2026-09-27; plan ACT-16). A plain
// <canvas> page in a WebView that runs the SAME drawing code as the desktop's QML Canvas
// (shared/activity_chart_draw.js, inlined below as source text) and the same maths
// (shared/activity_view_logic.js). RN sends data with ChartPage.setData(...) / setState(...);
// the page posts back {type:'hover'|'zoom'|'focus', ...}.
// Touch: one finger across the chart scrubs the values, two fingers pinch to zoom, double-tap
// shows everything, drag the strip underneath (shown while zoomed) to move along. A mouse wheel also zooms (tablets).

export function buildActivityChartHtml(): string {
  return `<!DOCTYPE html>
<html><head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
  html, body { background: transparent; overflow: hidden; font-family: sans-serif; }
  canvas { display: block; touch-action: none; }
  #wrap { position: relative; }
  #cursor { position: absolute; left: 0; top: 0; }
</style>
</head><body>
<div id="wrap"><canvas id="plot"></canvas><canvas id="cursor"></canvas></div>
<canvas id="strip"></canvas>
<script>
var AVL = (function () { var module = { exports: {} }; ${ACTIVITY_VIEW_LOGIC_SRC}
  ; return module.exports; })();
var ACD = (function () { var module = { exports: {} }; ${ACTIVITY_CHART_DRAW_SRC}
  ; return module.exports; })();

(function () {
  var S = { mode: 'overlay', st: null, channels: [], focusId: '', isDist: true, zoom: null, hover: -1,
            colourMode: '', cfg: null, zones: null, sport: '', target: null, colors: {}, zone: {}, labels: {},
            swim: { yMode: 'len', colourMode: 'stroke', showHr: false }, plotH: 300 };
  var geo = null, sel = null, xv = [], ribbon = null;
  var plot = document.getElementById('plot'), cur = document.getElementById('cursor'), strip = document.getElementById('strip');
  function post(m) { if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(JSON.stringify(m)); }
  function focusCh() { for (var i = 0; i < S.channels.length; i++) if (S.channels[i].id === S.focusId) return S.channels[i]; return S.channels[0] || null; }
  function sizeCanvas(c, w, h) {
    var d = window.devicePixelRatio || 1;
    c.width = Math.round(w * d); c.height = Math.round(h * d); c.style.width = w + 'px'; c.style.height = h + 'px';
    var ctx = c.getContext('2d'); ctx.setTransform(d, 0, 0, d, 0, 0); return ctx;
  }
  function computeXv() {
    var s = S.st && S.st.streams;
    if (!s || !s.t) { xv = []; return; }
    if (S.isDist && s.dist) xv = s.dist.map(function (v) { return v === null ? NaN : v; });
    else { var t0 = s.t[0]; xv = s.t.map(function (v) { return v - t0; }); }
  }
  function computeRibbon() {
    ribbon = null;
    if (!S.colourMode || !S.st || !S.cfg) return;
    var cb = AVL.colourBands(S.cfg, S.colourMode, S.st, S.zones || {}, S.sport);
    if (!cb) return;
    ribbon = { key: S.cfg.colour_modes[S.colourMode].key, bands: cb.bands, label: cb.label,
               colourOf: function (b) { return AVL.bandColour(cb, b); } };
  }
  function opts() {
    var n = xv.length, i0 = S.zoom ? Math.max(0, S.zoom[0]) : 0, i1 = S.zoom ? Math.min(n - 1, S.zoom[1]) : n - 1;
    return { L: AVL, st: S.st, xv: xv, i0: i0, i1: i1, channels: S.channels, focus: focusCh(), ribbon: ribbon,
             target: S.target, isDist: S.isDist, colors: S.colors, zone: S.zone, labels: S.labels };
  }
  function W() { return document.body.clientWidth || window.innerWidth; }
  function drawAll() {
    var w = W();
    if (S.mode === 'swim') {
      cur.style.display = 'none'; strip.style.display = 'none';
      var ctx = sizeCanvas(plot, w, 250);
      geo = S.st ? ACD.drawSwim(ctx, w, 250, { L: AVL, st: S.st, yMode: S.swim.yMode, colourMode: S.swim.colourMode,
                                               showHr: S.swim.showHr, hover: S.hover, colors: S.colors, zone: S.zone, labels: S.labels }) : null;
      post({ type: 'height', h: 256 });
      return;
    }
    // The overview strip only shows while zoomed (André, 2026-09-29): unzoomed it repeats the chart.
    var showStrip = !!S.zoom && !!S.st && xv.length > 1;
    cur.style.display = 'block'; strip.style.display = showStrip ? 'block' : 'none';
    var pctx = sizeCanvas(plot, w, S.plotH);
    geo = S.st && xv.length > 1 ? ACD.drawPlot(pctx, w, S.plotH, opts()) : null;
    drawCursor();
    if (showStrip) {
      var sctx = sizeCanvas(strip, w, 60);
      strip.style.marginTop = '6px';
      ACD.drawStrip(sctx, w, 60, opts());
    }
    post({ type: 'height', h: S.plotH + (showStrip ? 66 : 4) });
  }
  function drawCursor() {
    var w = W(), ctx = sizeCanvas(cur, w, S.plotH);
    ACD.drawCursor(ctx, w, S.plotH, opts(), geo, S.hover, sel);
  }

  // ---- pointer / touch
  var touches = {}, pinch = null, lastTap = 0, scrubbing = false;
  function localX(e, el) { var r = el.getBoundingClientRect(); return e.clientX - r.left; }
  function setHover(k) { if (k !== S.hover) { S.hover = k; if (S.mode === 'swim') drawAll(); else drawCursor(); post({ type: 'hover', idx: k }); } }
  function zoomTo(a, b) { var z = ACD.zoomRange(xv.length, a, b); S.zoom = z; drawAll(); post({ type: 'zoom', range: z }); }
  cur.addEventListener('pointerdown', function (e) {
    if (S.mode === 'swim' || !geo) return;
    touches[e.pointerId] = { x: localX(e, cur), y: e.clientY - cur.getBoundingClientRect().top };
    var ids = Object.keys(touches);
    if (ids.length === 2) {
      var a = touches[ids[0]], b = touches[ids[1]], n = xv.length - 1;
      var za = S.zoom ? S.zoom[0] : 0, zb = S.zoom ? S.zoom[1] : n;
      pinch = { d0: Math.abs(a.x - b.x) || 1, za: za, zb: zb, c: ACD.idxAt(geo, xv, (a.x + b.x) / 2) };
      scrubbing = false;
      return;
    }
    var p = touches[e.pointerId];
    var hits = geo ? geo.legendHits : [];
    for (var i = 0; i < hits.length; i++) {
      var h = hits[i];
      if (p.x >= h.x && p.x <= h.x + h.w && p.y >= h.y && p.y <= h.y + h.h) { S.focusId = h.id; drawAll(); post({ type: 'focus', id: h.id }); return; }
    }
    var now = Date.now();
    if (now - lastTap < 300) { lastTap = 0; S.zoom = null; drawAll(); post({ type: 'zoom', range: null }); return; }
    lastTap = now;
    scrubbing = true;
    setHover(ACD.idxAt(geo, xv, p.x));
  });
  cur.addEventListener('pointermove', function (e) {
    if (touches[e.pointerId]) touches[e.pointerId].x = localX(e, cur);
    var ids = Object.keys(touches);
    if (pinch && ids.length === 2) {
      var a = touches[ids[0]], b = touches[ids[1]];
      var f = pinch.d0 / (Math.abs(a.x - b.x) || 1), c = pinch.c;
      zoomTo(c - (c - pinch.za) * f, c + (pinch.zb - c) * f);
      return;
    }
    if (scrubbing || e.pointerType === 'mouse') setHover(ACD.idxAt(geo, xv, localX(e, cur)));
  });
  function up(e) { delete touches[e.pointerId]; if (Object.keys(touches).length < 2) pinch = null; if (!Object.keys(touches).length) scrubbing = false; }
  cur.addEventListener('pointerup', up); cur.addEventListener('pointercancel', up);
  cur.addEventListener('pointerleave', function (e) { if (e.pointerType === 'mouse') setHover(-1); });
  cur.addEventListener('wheel', function (e) {
    e.preventDefault();
    var n = xv.length - 1, a = S.zoom ? S.zoom[0] : 0, b = S.zoom ? S.zoom[1] : n;
    if (Math.abs(e.deltaX) > Math.abs(e.deltaY) || e.shiftKey) {
      if (!S.zoom) return; var d = (e.deltaX || e.deltaY) / 100 * (b - a) * 0.15; zoomTo(a + d, b + d); return;
    }
    var c = ACD.idxAt(geo, xv, localX(e, cur)), f = Math.exp(Math.max(-3, Math.min(3, e.deltaY / 100)) * 0.25);
    zoomTo(c - (c - a) * f, c + (b - c) * f);
  }, { passive: false });
  strip.addEventListener('pointerdown', stripMove);
  strip.addEventListener('pointermove', function (e) { if (e.buttons || e.pointerType !== 'mouse') stripMove(e); });
  function stripMove(e) {
    var k = ACD.stripIdxAt(W(), xv, localX(e, strip)), w = S.zoom ? S.zoom[1] - S.zoom[0] : Math.round((xv.length - 1) / 4);
    zoomTo(k - w / 2, k + w / 2);
  }
  // swim bars: tap/hover a bar
  plot.addEventListener('pointerdown', function (e) { if (S.mode === 'swim' && geo) swimHover(e); });
  plot.addEventListener('pointermove', function (e) { if (S.mode === 'swim' && geo) swimHover(e); });
  function swimHover(e) { var i = Math.floor((localX(e, plot) - geo.L) / geo.bw); setHover(i >= 0 && i < geo.n ? i : -1); }

  window.ChartPage = {
    setData: function (d) {
      for (var k in d) S[k] = d[k];
      computeXv(); computeRibbon(); drawAll();
    },
    setState: function (d) {
      var needX = d.isDist !== undefined && d.isDist !== S.isDist, needRibbon = d.colourMode !== undefined && d.colourMode !== S.colourMode;
      for (var k in d) S[k] = d[k];
      if (needX) computeXv();
      if (needRibbon) computeRibbon();
      if (Object.keys(d).length === 1 && d.hover !== undefined && S.mode !== 'swim') drawCursor(); else drawAll();
    }
  };
  window.addEventListener('resize', drawAll);
  post({ type: 'ready' });
})();
</script>
</body></html>`;
}

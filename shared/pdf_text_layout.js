// Rebuild a PDF page's text as lines, like `pdftotext -layout` (what the desktop's roadbook import
// reads): pdf.js gives positioned text pieces; pieces on the same baseline become one line, left to
// right, and a wide horizontal gap (a table column) becomes 2+ spaces so tools/roadbook_import.py's
// "name, then a column gap" rule still finds the control name.
//
// One source for two places: the Android WebView page (embedded as text by
// tools/gen_pdfjs_inline.js - Hermes can't give a function's source back) and the node test
// (tools/test_roadbook_pdf.js). Plain ES2015, no dependencies.

/** pages: one array per page of pdf.js getTextContent() items ({str, transform, width, height}). */
function pdfTextLayout(pages) {
  var out = [];
  for (var p = 0; p < pages.length; p++) out.push(layoutPage(pages[p]));
  return out.join('\n\f\n');
}

function layoutPage(items) {
  var pieces = [];
  var chars = 0, width = 0;
  for (var k = 0; k < items.length; k++) {
    var it = items[k];
    // Whitespace-only pieces are skipped: pdf.js bridges a column gap with one " " piece as wide as
    // the gap, which would hide it; the gap between the real words is measured instead.
    if (!it || typeof it.str !== 'string' || !/\S/.test(it.str)) continue;
    var t = it.transform || [1, 0, 0, 1, 0, 0];
    var h = Math.sqrt(t[2] * t[2] + t[3] * t[3]) || it.height || 10;
    pieces.push({ x: t[4], y: t[5], w: it.width || 0, h: h, s: it.str });
    if (it.width > 0) { chars += it.str.length; width += it.width; }
  }
  if (!pieces.length) return '';
  var cw = chars ? width / chars : 5;              // average character width on this page

  // Top to bottom, then left to right; a piece joins a line when its baseline is within half a
  // text height of the line's (cells of one table row can sit a little higher or lower).
  pieces.sort(function (a, b) { return (b.y - a.y) || (a.x - b.x); });
  var lines = [];
  for (var i = 0; i < pieces.length; i++) {
    var pc = pieces[i], line = null;
    for (var j = lines.length - 1; j >= 0 && j >= lines.length - 4; j--) {
      if (Math.abs(lines[j].y - pc.y) <= 0.5 * Math.min(lines[j].h, pc.h)) { line = lines[j]; break; }
    }
    if (line) line.pieces.push(pc);
    else lines.push({ y: pc.y, h: pc.h, pieces: [pc] });
  }

  var text = [];
  for (var L = 0; L < lines.length; L++) {
    var ps = lines[L].pieces.sort(function (a, b) { return a.x - b.x; });
    var s = '', end = null;
    for (var q = 0; q < ps.length; q++) {
      var piece = ps[q];
      if (end !== null) {
        var gap = piece.x - end;
        if (gap > 1.5 * cw) s = s.replace(/ +$/, '') + new Array(Math.max(2, Math.round(gap / cw)) + 1).join(' ');
        else if (gap > 0.2 * cw && !/\s$/.test(s) && !/^\s/.test(piece.s)) s += ' ';
      }
      s += piece.s;
      end = piece.x + piece.w;
    }
    text.push(s.replace(/\s+$/, ''));
  }
  return text.join('\n');
}

if (typeof module !== 'undefined' && module.exports) module.exports = { pdfTextLayout: pdfTextLayout };

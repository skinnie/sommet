// Twin of tools/roadbook_import.py: a brevet roadbook's control table -> controls with km, opening and
// closing times. The text path only - the desktop reads a PDF with pdftotext, Android with pdf.js
// (components/PdfTextReader.tsx) into the same kind of text. Parity-tested.
import { pyRound } from './pyCompat';

const CTRL_RE = /^\s*(?:C|CP|CONTROL|CONTRÔLE|CTRL)\s*(\d+)\s*[-–—:]\s*(.+)$/iu;
const TIME_RE = /\b(\d{1,2}[:hH]\d{2})\b/g;
const KM_RE = /\b(\d{1,4}(?:[.,]\d+)?)\b/g;

export interface RoadbookControl { label: string; name: string; km: number; open: string | null; close: string }

function normTime(t: string): string {
  const [hh, mm] = t.replace(/h/g, ':').replace(/H/g, ':').split(':');
  return `${String(parseInt(hh, 10)).padStart(2, '0')}:${mm}`;
}

function parseControlLine(num: number, rest: string): RoadbookControl | null {
  const times = Array.from(rest.matchAll(TIME_RE), m => m[1]);
  if (!times.length) return null;
  let name = rest.trim().split(/\s{2,}/)[0].trim();
  name = name.replace(/\s+\d.*$/, '').trim() || rest.trim().split(/\s+/)[0];
  const close = normTime(times[times.length - 1]);
  const open = times.length >= 2 ? normTime(times[times.length - 2]) : null;
  const firstTimePos = rest.indexOf(times.length >= 2 ? times[times.length - 2] : times[times.length - 1]);
  const head = firstTimePos > 0 ? rest.slice(0, firstTimePos) : rest;
  const kms = Array.from(head.matchAll(KM_RE), m => m[1]);
  const decimals = kms.filter(k => k.includes(',') || k.includes('.'));
  const tok = decimals.length ? decimals[decimals.length - 1] : (kms.length ? kms[kms.length - 1] : null);
  if (tok === null) return null;
  return { label: `C${num}`, name, km: pyRound(parseFloat(tok.replace(',', '.')), 1), open, close };
}

export function parseRoadbook(text: string): RoadbookControl[] {
  const out: RoadbookControl[] = [];
  const seen = new Set<string>();
  // PDFs made through PostScript often turn "-" into the minus sign U+2212 ("C1 − NAME").
  text = text.replace(/\u2212/g, '-');
  // Python str.splitlines() line boundaries.
  const lines = text.split(/\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/);
  for (let i = 0; i < lines.length; i++) {
    const m = CTRL_RE.exec(lines[i]);
    if (!m) continue;
    const num = parseInt(m[1], 10), rest = m[2];
    let c = parseControlLine(num, rest);
    // A long place name wrapped in its table cell, the km and times on the next line
    // ("C3 - CHÂLONS-" / "EN-CHAMPAGNE   Mairie   143,2   264,7   13:21   2:39"): join the two.
    if (c === null && !/\b\d{1,2}[:hH]\d{2}\b/.test(rest) && i + 1 < lines.length && !CTRL_RE.test(lines[i + 1])) {
      const head = rest.trimEnd();
      c = parseControlLine(num, head + (head.endsWith('-') ? '' : ' ') + lines[i + 1].trim());
    }
    const key = c ? `${c.label}|${c.km}` : '';
    if (c && !seen.has(key)) { seen.add(key); out.push(c); }
  }
  return out.map((c, i) => [c, i] as [RoadbookControl, number])
    .sort((a, b) => (a[0].km - b[0].km) || (a[1] - b[1])).map(x => x[0]);
}

/** roadbook_import.py main() for {text}: {ok, controls, count} | {ok:false, error}. */
export function importRoadbook(body: { text?: string; pdf?: string }): any {
  const text = body.text;
  if (!text && body.pdf) return { ok: false, error: "could not read PDF (need 'pdftotext'); paste the table text instead" };
  if (!text) return { ok: false, error: 'no roadbook text or readable PDF provided' };
  const controls = parseRoadbook(text);
  if (!controls.length) return { ok: false, error: "no control rows found (expected lines like 'C1 - PLACE ... 112,5 ... 12:30')" };
  return { ok: true, controls, count: controls.length };
}

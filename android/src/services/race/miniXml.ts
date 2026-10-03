// A small XML reader for what the race engine reads with Python's ElementTree (PitStopper GPX):
// elements with attributes, text (entities decoded: &amp; &lt; &gt; &quot; &apos; &#N; &#xH;), CDATA,
// comments / processing instructions / doctype skipped. `text` is ElementTree's .text: the character
// data before the first child. Throws on malformed input (ElementTree raises ParseError).

export interface XmlEl { tag: string; attrs: Record<string, string>; children: XmlEl[]; text: string }

function decode(s: string): string {
  return s.replace(/&(#x[0-9a-fA-F]+|#[0-9]+|amp|lt|gt|quot|apos);/g, (_, e: string) => {
    if (e[0] === '#') return String.fromCodePoint(e[1] === 'x' || e[1] === 'X' ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
    return ({ amp: '&', lt: '<', gt: '>', quot: '"', apos: "'" } as Record<string, string>)[e];
  });
}

export function parseXml(src: string): XmlEl {
  let i = 0;
  const n = src.length;
  const root: XmlEl = { tag: '#document', attrs: {}, children: [], text: '' };
  const stack: XmlEl[] = [root];
  const sawChild = new WeakSet<XmlEl>();
  const addText = (t: string) => {
    const cur = stack[stack.length - 1];
    if (!sawChild.has(cur)) cur.text += t;
  };
  while (i < n) {
    const lt = src.indexOf('<', i);
    if (lt < 0) { addText(decode(src.slice(i))); break; }
    if (lt > i) addText(decode(src.slice(i, lt)));
    if (src.startsWith('<!--', lt)) { const e = src.indexOf('-->', lt + 4); if (e < 0) throw new Error('unclosed comment'); i = e + 3; continue; }
    if (src.startsWith('<![CDATA[', lt)) { const e = src.indexOf(']]>', lt + 9); if (e < 0) throw new Error('unclosed CDATA'); addText(src.slice(lt + 9, e)); i = e + 3; continue; }
    if (src.startsWith('<?', lt)) { const e = src.indexOf('?>', lt + 2); if (e < 0) throw new Error('unclosed PI'); i = e + 2; continue; }
    if (src.startsWith('<!', lt)) { const e = src.indexOf('>', lt + 2); if (e < 0) throw new Error('unclosed decl'); i = e + 1; continue; }
    const gt = (() => {                                   // the '>' closing this tag, outside quotes
      let q: string | null = null;
      for (let k = lt + 1; k < n; k++) {
        const c = src[k];
        if (q) { if (c === q) q = null; } else if (c === '"' || c === "'") q = c; else if (c === '>') return k;
      }
      return -1;
    })();
    if (gt < 0) throw new Error('unclosed tag');
    const body = src.slice(lt + 1, gt);
    i = gt + 1;
    if (body[0] === '/') {
      const name = body.slice(1).trim();
      const cur = stack.pop();
      if (!cur || cur.tag !== name) throw new Error(`mismatched tag: ${name}`);
      continue;
    }
    const selfClose = body.endsWith('/');
    const inner = selfClose ? body.slice(0, -1) : body;
    const m = /^([^\s/>]+)/.exec(inner);
    if (!m) throw new Error('bad tag');
    const el: XmlEl = { tag: m[1], attrs: {}, children: [], text: '' };
    const ar = /([^\s=]+)\s*=\s*("([^"]*)"|'([^']*)')/g;
    let a: RegExpExecArray | null;
    const rest = inner.slice(m[1].length);
    while ((a = ar.exec(rest))) el.attrs[a[1]] = decode(a[3] ?? a[4] ?? '');
    const parent = stack[stack.length - 1];
    parent.children.push(el);
    sawChild.add(parent);
    if (!selfClose) stack.push(el);
  }
  if (stack.length !== 1) throw new Error('unclosed element');
  if (!root.children.length) throw new Error('no element found');
  return root.children[0];
}

/** ElementTree root.iter(): the element and all descendants, document order. */
export function* iterAll(el: XmlEl): Generator<XmlEl> {
  yield el;
  for (const c of el.children) yield* iterAll(c);
}

export const localName = (tag: string) => { const k = tag.lastIndexOf('}'); const c = tag.lastIndexOf(':'); return tag.slice(Math.max(k, c) + 1); };

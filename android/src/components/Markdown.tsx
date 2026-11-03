import React from 'react';
import { View, Text, Linking } from 'react-native';

// A small Markdown renderer for the Coach's bubbles (André, 2026-11-02, desktop picks #4): the
// desktop shows Claude's answers with Qt's Text.MarkdownText, so bold, italics, lists and links
// show properly instead of raw ** and dashes. React Native has no Markdown text, and this needs
// only what a short chat answer uses, so it is written here rather than pulled in as a package:
//
//   paragraphs (a blank line between them; single line breaks join, as in CommonMark),
//   **bold** / __bold__, *italic* / _italic_, `code`, [links](https://…),
//   "- " / "* " / "+ " bullet lists, "1. " / "1) " numbered lists, and # headings (shown bold).
//
// Anything else stays as plain text. Every <Text> here, nested ones included, sets its own
// fontSize: on André's BlissOS tablet an inline <Text> without one renders invisible.

export interface MdSpan {
  text: string;
  bold?: boolean;
  italic?: boolean;
  code?: boolean;
  link?: string;
}

export type MdBlock =
  | { kind: 'paragraph'; spans: MdSpan[] }
  | { kind: 'heading'; spans: MdSpan[] }
  | { kind: 'bullet'; items: MdSpan[][] }
  | { kind: 'ordered'; start: number; items: MdSpan[][] };

interface Style { bold?: boolean; italic?: boolean; link?: string }

function pushText(out: MdSpan[], text: string, st: Style) {
  if (!text) return;
  const last = out[out.length - 1];
  if (last && !last.code && !!last.bold === !!st.bold && !!last.italic === !!st.italic && last.link === st.link) {
    last.text += text;
    return;
  }
  const span: MdSpan = { text };
  if (st.bold) span.bold = true;
  if (st.italic) span.italic = true;
  if (st.link) span.link = st.link;
  out.push(span);
}

const isWordChar = (c: string | undefined) => !!c && /[\p{L}\p{N}]/u.test(c);
const isSpace = (c: string | undefined) => c === undefined || /\s/.test(c);

// Finds the closing delimiter for an emphasis opened at `from` (just past the opener).
function findClose(s: string, from: number, delim: string): number {
  let i = from;
  while (i < s.length) {
    if (s[i] === '\\') { i += 2; continue; }
    if (s[i] === '`') {
      const end = s.indexOf('`', i + 1);
      if (end > 0) { i = end + 1; continue; }
    }
    // For a single * or _, skip a ** / __ (that belongs to bold inside the italic).
    if (delim.length === 1 && s[i] === delim && s[i + 1] === delim) { i += 2; continue; }
    if (s.startsWith(delim, i) && !isSpace(s[i - 1]) && i > from) {
      // An underscore closes only at a word edge (snake_case words stay as they are).
      if (delim[0] === '_' && isWordChar(s[i + delim.length])) { i += 1; continue; }
      return i;
    }
    i += 1;
  }
  return -1;
}

export function parseInline(s: string, st: Style = {}, out: MdSpan[] = []): MdSpan[] {
  let i = 0;
  let buf = '';
  const flush = () => { pushText(out, buf, st); buf = ''; };
  while (i < s.length) {
    const c = s[i];
    // Backslash escapes a Markdown character.
    if (c === '\\' && i + 1 < s.length && /[\\`*_[\]()#+\-.!]/.test(s[i + 1])) {
      buf += s[i + 1];
      i += 2;
      continue;
    }
    if (c === '`') {
      const end = s.indexOf('`', i + 1);
      if (end > i + 1) {
        flush();
        out.push({ text: s.slice(i + 1, end), code: true });
        i = end + 1;
        continue;
      }
    }
    if (c === '[' && !st.link) {
      const close = s.indexOf('](', i + 1);
      const end = close > 0 ? s.indexOf(')', close + 2) : -1;
      if (close > i && end > close) {
        const url = s.slice(close + 2, end).trim();
        if (/^(https?:\/\/|mailto:)/i.test(url)) {
          flush();
          parseInline(s.slice(i + 1, close), { ...st, link: url }, out);
          i = end + 1;
          continue;
        }
      }
    }
    if (c === '*' || c === '_') {
      const double = s[i + 1] === c;
      const delim = double ? c + c : c;
      const after = s[i + delim.length];
      // An opener must touch the text after it, and an underscore must start a word.
      const canOpen = !isSpace(after) && after !== c && !(c === '_' && isWordChar(s[i - 1]));
      if (canOpen) {
        const end = findClose(s, i + delim.length, delim);
        if (end > 0) {
          flush();
          const inner = s.slice(i + delim.length, end);
          parseInline(inner, double ? { ...st, bold: true } : { ...st, italic: true }, out);
          i = end + delim.length;
          continue;
        }
      }
    }
    buf += c;
    i += 1;
  }
  flush();
  return out;
}

const BULLET = /^\s{0,3}[-*+]\s+(.*)$/;
const ORDERED = /^\s{0,3}(\d{1,9})[.)]\s+(.*)$/;
const HEADING = /^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;

export function parseMarkdown(src: string): MdBlock[] {
  const lines = src.replace(/\r\n?/g, '\n').split('\n');
  const blocks: MdBlock[] = [];
  let para: string[] = [];
  let list: { kind: 'bullet' | 'ordered'; start: number; items: string[] } | null = null;

  const endPara = () => {
    if (para.length) blocks.push({ kind: 'paragraph', spans: parseInline(para.join(' ')) });
    para = [];
  };
  const endList = () => {
    if (!list) return;
    const items = list.items.map(it => parseInline(it));
    blocks.push(list.kind === 'bullet' ? { kind: 'bullet', items } : { kind: 'ordered', start: list.start, items });
    list = null;
  };

  for (const raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (line.trim() === '') {
      endPara();
      // A blank line inside a list keeps the list going if the next line is another item.
      continue;
    }
    if (RULE.test(line)) {
      // A "---" line: a thematic break. A chat bubble draws none, it only ends the block.
      endPara(); endList();
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      endPara(); endList();
      blocks.push({ kind: 'heading', spans: parseInline(h[1]) });
      continue;
    }
    const b = BULLET.exec(line);
    const o = b ? null : ORDERED.exec(line);
    if (b || o) {
      endPara();
      const kind = b ? 'bullet' : 'ordered';
      if (!list || list.kind !== kind) {
        endList();
        list = { kind, start: o ? parseInt(o[1], 10) : 1, items: [] };
      }
      list.items.push(b ? b[1] : o![2]);
      continue;
    }
    if (list && /^\s+/.test(raw)) {
      // An indented line continues the list item above it.
      list.items[list.items.length - 1] += ' ' + line.trim();
      continue;
    }
    endList();
    para.push(line.trim());
  }
  endPara();
  endList();
  return blocks;
}

function openLink(url: string) {
  Linking.openURL(url).catch(() => { /* no app for this link: nothing to do */ });
}

export default function Markdown({
  text, color, linkColor, fontSize, codeBackground,
}: {
  text: string; color: string; linkColor: string; fontSize: number; codeBackground?: string;
}) {
  const blocks = parseMarkdown(text);
  const lineHeight = Math.round(fontSize * 1.42);
  const spans = (list: MdSpan[], baseBold = false) => list.map((sp, i) => (
    <Text
      key={i}
      onPress={sp.link ? () => openLink(sp.link!) : undefined}
      accessibilityRole={sp.link ? 'link' : undefined}
      style={{
        fontSize,
        color: sp.link ? linkColor : color,
        fontWeight: sp.bold || baseBold ? '700' : '400',
        fontStyle: sp.italic ? 'italic' : 'normal',
        textDecorationLine: sp.link ? 'underline' : 'none',
        fontFamily: sp.code ? 'monospace' : undefined,
        backgroundColor: sp.code ? codeBackground : undefined,
      }}
    >
      {sp.text}
    </Text>
  ));

  return (
    <View style={{ gap: 6 }}>
      {blocks.map((b, bi) => {
        if (b.kind === 'paragraph' || b.kind === 'heading') {
          return (
            <Text key={bi} style={{ fontSize, color, lineHeight }}>
              {spans(b.spans, b.kind === 'heading')}
            </Text>
          );
        }
        return (
          <View key={bi} style={{ gap: 3 }}>
            {b.items.map((item, ii) => (
              <View key={ii} style={{ flexDirection: 'row' }}>
                <Text style={{ fontSize, color, lineHeight, minWidth: b.kind === 'bullet' ? 14 : 20 }}>
                  {b.kind === 'bullet' ? '•' : `${b.start + ii}.`}
                </Text>
                <Text style={{ flex: 1, fontSize, color, lineHeight }}>{spans(item)}</Text>
              </View>
            ))}
          </View>
        );
      })}
    </View>
  );
}

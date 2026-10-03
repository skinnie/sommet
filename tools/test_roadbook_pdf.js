#!/usr/bin/env node
// Roadbook PDF: does Android (pdf.js + shared/pdf_text_layout.js, what the app's WebView runs) find
// the same controls as the desktop (`pdftotext -layout` + tools/roadbook_import.py)?
//
// Run: node tools/test_roadbook_pdf.js file.pdf [...]
// Both texts go through the same Python parser, so a difference is a text-layout difference (the
// parser's TypeScript twin is checked against the Python by tools/test_race_parity.js).
'use strict';
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { pathToFileURL } = require('url');
const { pdfjsPackage } = require('./gen_pdfjs_inline');
const { pdfTextLayout } = require('../shared/pdf_text_layout');

const ROOT = path.resolve(__dirname, '..');

// The same getDocument options as the app (android/src/components/PdfTextReader.tsx).
const DOC_OPTS = { isEvalSupported: false, disableFontFace: true, useSystemFonts: false, stopAtErrors: false };

async function pdfjsText(pdfjsLib, file) {
  const task = pdfjsLib.getDocument({ data: new Uint8Array(fs.readFileSync(file)), ...DOC_OPTS });
  const doc = await task.promise;
  const pages = [];
  for (let p = 1; p <= doc.numPages; p++) pages.push((await (await doc.getPage(p)).getTextContent()).items);
  await task.destroy();
  return pdfTextLayout(pages);
}

function parse(text) {
  const py = 'import json,sys; sys.path.insert(0, "tools"); from roadbook_import import parse_roadbook; print(json.dumps(parse_roadbook(sys.stdin.read())))';
  return JSON.parse(execFileSync('python3', ['-c', py], { cwd: ROOT, input: text }).toString());
}

async function main() {
  const files = process.argv.slice(2);
  if (!files.length) { console.error('usage: node tools/test_roadbook_pdf.js file.pdf [...]'); process.exit(2); }
  const pkg = pdfjsPackage();
  globalThis.pdfjsWorker = await import(pathToFileURL(path.join(pkg, 'legacy', 'build', 'pdf.worker.min.mjs')).href);
  const pdfjsLib = await import(pathToFileURL(path.join(pkg, 'legacy', 'build', 'pdf.min.mjs')).href);
  let failed = 0;
  for (const f of files) {
    const desk = parse(execFileSync('pdftotext', ['-layout', f, '-']).toString());
    const text = await pdfjsText(pdfjsLib, f);
    const andr = parse(text);
    // km and times must match; a name can differ where the two layouts split a column differently.
    const key = cs => JSON.stringify(cs.map(c => [c.label, c.km, c.open, c.close]));
    const same = key(desk) === key(andr);
    const names = JSON.stringify(desk.map(c => c.name)) === JSON.stringify(andr.map(c => c.name));
    if (!same) failed++;
    console.log(`${same ? 'PASS' : 'FAIL'} ${path.basename(f)}: desktop ${desk.length} controls, android ${andr.length}` +
                (same && !names ? ' (a name differs)' : ''));
    if (!same || !names || process.env.V) {
      console.log('  desktop:', JSON.stringify(desk));
      console.log('  android:', JSON.stringify(andr));
      console.log(text.split('\n').filter(l => /^\s*C\d/.test(l)).map(l => '  | ' + l).join('\n'));
    }
  }
  console.log(`\n${files.length - failed} passed, ${failed} failed`);
  process.exit(failed ? 1 : 0);
}

main().catch(e => { console.error(e); process.exit(1); });

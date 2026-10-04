#!/usr/bin/env node
/*
 * One version for every platform: the HIGHER of android/package.json "version" and the desktop's
 * project(AmbitApp VERSION ...) in desktop/CMakeLists.txt.
 *
 * It used to take package.json alone, which silently set the desktop BACK whenever the desktop
 * had been bumped on its own (2026-10-03: 0.2.45 -> 0.2.42, on a plain `npm install`, since this
 * also runs as postinstall). André, 2026-10-04: "put to sync the higher one". So whichever side
 * was bumped last wins, package.json is brought up to it too, and nothing ever goes down.
 *
 * This stamps that one version into every place each platform reads its version from, so they
 * can never drift again (they had: desktop 0.2.16, package 0.2.25, in-app 0.2.1, gradle 0.2.14,
 * iOS 1.0). Run it after changing package.json:
 *
 *     npm run sync-version          # from android/
 *
 * It also runs automatically on `npm version <x>` (the "version" script hook below), so
 * `npm version patch` bumps package.json AND stamps all platforms AND stages the files.
 *
 * Files written (idempotent — only rewrites when the value actually changes):
 *   android/src/config/version.ts        APP_VERSION            (the in-app "About" string, JS)
 *   android/android/app/build.gradle     versionName + versionCode (code = major*10000+minor*100+patch)
 *   android/ios/.../project.pbxproj       MARKETING_VERSION + CURRENT_PROJECT_VERSION
 *   desktop/CMakeLists.txt                project(AmbitApp VERSION ...)
 *   android/package.json (+ lockfile)     "version", when the desktop was ahead
 *
 * Android build.gradle and desktop CMake could self-read package.json at build time, but a single
 * explicit stamp keeps all five values greppable and identical in the tree (and works for the iOS
 * pbxproj, which can't cleanly self-read). Marketing version is the semver string; the Android
 * versionCode / iOS build number are a monotonic integer derived from it.
 */
'use strict';
const fs = require('fs');
const path = require('path');

const ANDROID = path.resolve(__dirname, '..');          // android/
const ROOT = path.resolve(ANDROID, '..');               // repo root
const pkg = JSON.parse(fs.readFileSync(path.join(ANDROID, 'package.json'), 'utf8'));
const PKG_V = String(pkg.version).trim();
const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/;
if (!SEMVER.test(PKG_V)) { console.error(`sync-version: package.json version "${PKG_V}" is not X.Y.Z`); process.exit(1); }
// The desktop's own version, when the file is there (it is not in an Android-only checkout).
let DESKTOP_V = '';
try {
  const cm = /project\(AmbitApp VERSION (\d+\.\d+\.\d+)/.exec(
    fs.readFileSync(path.join(ROOT, 'desktop', 'CMakeLists.txt'), 'utf8'));
  if (cm) DESKTOP_V = cm[1];
} catch { /* no desktop tree */ }
const num = v => { const x = SEMVER.exec(v); return (+x[1]) * 1e6 + (+x[2]) * 1e3 + (+x[3]); };
const V = DESKTOP_V && num(DESKTOP_V) > num(PKG_V) ? DESKTOP_V : PKG_V;
const m = SEMVER.exec(V);
const CODE = (+m[1]) * 10000 + (+m[2]) * 100 + (+m[3]);  // 0.2.25 -> 225, monotonic across releases

let changed = 0;
function patch(rel, subs) {
  const file = path.join(ROOT, rel);
  let src;
  try { src = fs.readFileSync(file, 'utf8'); }
  catch { console.warn(`sync-version: skip (missing) ${rel}`); return; }
  let out = src;
  for (const [re, repl] of subs) out = out.replace(re, repl);
  if (out !== src) { fs.writeFileSync(file, out); changed++; console.log(`  updated ${rel}`); }
}

// package.json itself (and its lockfile), when the desktop was the higher one.
patch('android/package.json', [
  [/"version":\s*"[^"]*"/, `"version": "${V}"`],           // first match = the package's own
]);
patch('android/package-lock.json', [
  // the two leading "version" fields are the package's own (top level, then packages[""])
  [/^(\{\s*"name":\s*"[^"]*",\s*"version":\s*)"[^"]*"/, `$1"${V}"`],
  [/("packages":\s*\{\s*"":\s*\{\s*"name":\s*"[^"]*",\s*"version":\s*)"[^"]*"/, `$1"${V}"`],
]);
patch('android/src/config/version.ts', [
  [/APP_VERSION\s*=\s*'[^']*'/, `APP_VERSION = '${V}'`],
]);
patch('android/android/app/build.gradle', [
  [/versionName\s+"[^"]*"/, `versionName "${V}"`],
  [/versionCode\s+\d+/, `versionCode ${CODE}`],
]);
patch('android/ios/Sommet.xcodeproj/project.pbxproj', [
  [/MARKETING_VERSION = [^;]+;/g, `MARKETING_VERSION = ${V};`],
  [/CURRENT_PROJECT_VERSION = [^;]+;/g, `CURRENT_PROJECT_VERSION = ${CODE};`],
]);
patch('desktop/CMakeLists.txt', [
  [/project\(AmbitApp VERSION [0-9.]+/, `project(AmbitApp VERSION ${V}`],
]);

console.log(`sync-version: ${V} (build ${CODE}) — ${changed} file(s) updated`);

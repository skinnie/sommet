import * as Keychain from 'react-native-keychain';
import { bytesToBase64 } from './Base64';

// ─── Coros Training Hub (teamapi.coros.com) ───────────────────────────────────
//
// The Dura keeps its recordings in internal flash and never exposes them over USB
// mass storage (only maps/logs/firmware live on the two FAT volumes — see the
// device probe in this feature's notes). So, unlike the Ambit/Garmin readers that
// pull files straight off the mounted watch, Coros activities come from the cloud:
// the same private JSON API that powers the COROS Training Hub web dashboard.
//
// It's unofficial and unsupported by COROS — it can change without notice — so this
// stays small and defensive. Endpoints/codes verified against the community
// reverse-engineering (xballoy/coros-api, NYT87/coros-connect):
//   POST /account/login          { account, accountType:2, pwd:md5(password) } -> { accessToken }
//   GET  /activity/query?size&pageNumber&modeList   -> { dataList:[{labelId,sportType,name,date}], totalPage }
//   POST /activity/detail/download?labelId&sportType&fileType -> { fileUrl }   (fetch that for the bytes)
// Authenticated calls carry the token in a lowercase `accesstoken` header.

// Global accounts use teamapi.coros.com; China-region accounts use teamcnapi.coros.com.
// André's account is global (Portugal); expose the base as a constant so a CN user
// only has to flip this one line.
const COROS_HOST = 'https://teamapi.coros.com';

// activity/detail/download fileType codes (NOT the extension string).
const FILE_TYPE = { csv: '0', gpx: '1', kml: '2', tcx: '3', fit: '4' } as const;
export type CorosFileType = keyof typeof FILE_TYPE;

const KC_TOKEN = 'opensportsync_coros_token';

// ─── Types ────────────────────────────────────────────────────────────────────

interface CorosSession {
  accessToken: string;
  userId?: string;
  regionId?: number;
}

export interface CorosActivity {
  labelId: string;
  sportType: number;
  name?: string;
  /** Coros' own day stamp, usually YYYYMMDD as a number. */
  date?: number;
}

// ─── Token storage (Keychain, like Strava) ────────────────────────────────────

async function saveSession(s: CorosSession): Promise<void> {
  await Keychain.setGenericPassword('coros', JSON.stringify(s), { service: KC_TOKEN });
}

async function loadSession(): Promise<CorosSession | null> {
  const creds = await Keychain.getGenericPassword({ service: KC_TOKEN });
  return creds ? JSON.parse(creds.password) : null;
}

export async function isAuthenticated(): Promise<boolean> {
  return (await loadSession()) !== null;
}

export async function logout(): Promise<void> {
  await Keychain.resetGenericPassword({ service: KC_TOKEN });
}

// ─── MD5 (Coros hashes the password before login) ─────────────────────────────
//
// React Native has no crypto.subtle MD5 and the app pulls in no hash lib, so keep a
// small self-contained implementation here — matching this project's per-file helper
// convention (KailashHistoryReader/PoiService each keep their own small codecs rather
// than a shared module). Standard RFC-1321 MD5, hex digest of the UTF-8 password.
function md5Hex(input: string): string {
  const utf8 = unescape(encodeURIComponent(input));
  const bytes = new Uint8Array(utf8.length);
  for (let i = 0; i < utf8.length; i++) bytes[i] = utf8.charCodeAt(i);
  return md5Bytes(bytes);
}

function md5Bytes(msg: Uint8Array): string {
  const rotl = (x: number, c: number) => (x << c) | (x >>> (32 - c));
  const add = (a: number, b: number) => (a + b) | 0;

  const S = [7,12,17,22, 7,12,17,22, 7,12,17,22, 7,12,17,22,
             5, 9,14,20, 5, 9,14,20, 5, 9,14,20, 5, 9,14,20,
             4,11,16,23, 4,11,16,23, 4,11,16,23, 4,11,16,23,
             6,10,15,21, 6,10,15,21, 6,10,15,21, 6,10,15,21];
  const K: number[] = [];
  for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296) | 0;

  const origLen = msg.length;
  const bitLen = origLen * 8;
  // pad to 56 mod 64, then 64-bit little-endian length
  const withPad = ((origLen + 8) >> 6) * 64 + 64;
  const buf = new Uint8Array(withPad);
  buf.set(msg);
  buf[origLen] = 0x80;
  for (let i = 0; i < 8; i++) buf[withPad - 8 + i] = (bitLen / Math.pow(2, 8 * i)) & 0xff;

  let a0 = 0x67452301, b0 = 0xefcdab89, c0 = 0x98badcfe, d0 = 0x10325476;
  const M = new Int32Array(16);
  for (let off = 0; off < withPad; off += 64) {
    for (let i = 0; i < 16; i++) {
      const j = off + i * 4;
      M[i] = buf[j] | (buf[j + 1] << 8) | (buf[j + 2] << 16) | (buf[j + 3] << 24);
    }
    let A = a0, B = b0, C = c0, D = d0;
    for (let i = 0; i < 64; i++) {
      let F: number, g: number;
      if (i < 16)      { F = (B & C) | (~B & D);        g = i; }
      else if (i < 32) { F = (D & B) | (~D & C);        g = (5 * i + 1) & 15; }
      else if (i < 48) { F = B ^ C ^ D;                 g = (3 * i + 5) & 15; }
      else             { F = C ^ (B | ~D);              g = (7 * i) & 15; }
      F = add(add(add(F, A), K[i]), M[g]);
      A = D; D = C; C = B;
      B = add(B, rotl(F, S[i]));
    }
    a0 = add(a0, A); b0 = add(b0, B); c0 = add(c0, C); d0 = add(d0, D);
  }

  const hex = (n: number) => {
    let out = '';
    for (let i = 0; i < 4; i++) out += ((n >>> (8 * i)) & 0xff).toString(16).padStart(2, '0');
    return out;
  };
  return hex(a0) + hex(b0) + hex(c0) + hex(d0);
}

// ─── API calls ────────────────────────────────────────────────────────────────

async function authHeaders(): Promise<Record<string, string>> {
  const s = await loadSession();
  if (!s) throw new Error('Not logged in to Coros');
  return { accesstoken: s.accessToken };
}

/** Log in with email + password. Stores the token in the Keychain on success. */
export async function login(email: string, password: string): Promise<void> {
  const res = await fetch(`${COROS_HOST}/account/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ account: email, accountType: 2, pwd: md5Hex(password) }),
  });
  const j: any = await res.json().catch(() => ({}));
  if (j?.result !== '0000' || !j?.data?.accessToken) {
    throw new Error(j?.message || `Coros login failed (result=${j?.result ?? res.status})`);
  }
  await saveSession({ accessToken: j.data.accessToken, userId: j.data.userId, regionId: j.data.regionId });
}

/** All activities, newest first, paging through /activity/query (size 200/page). */
export async function listActivities(): Promise<CorosActivity[]> {
  const headers = await authHeaders();
  const out: CorosActivity[] = [];
  let page = 1;
  let totalPage = 1;
  do {
    const u = `${COROS_HOST}/activity/query?size=200&pageNumber=${page}&modeList=`;
    const res = await fetch(u, { headers });
    const j: any = await res.json().catch(() => ({}));
    if (j?.result !== '0000') throw new Error(j?.message || `Coros query failed (result=${j?.result})`);
    for (const a of j.data?.dataList ?? []) {
      out.push({ labelId: String(a.labelId), sportType: Number(a.sportType), name: a.name, date: a.date });
    }
    totalPage = Number(j.data?.totalPage ?? 1);
    page++;
  } while (page <= totalPage);
  return out;
}

/** Resolve the temporary download URL for one activity in the given format. */
async function downloadUrl(a: CorosActivity, fileType: CorosFileType): Promise<string> {
  const headers = await authHeaders();
  const u = `${COROS_HOST}/activity/detail/download`
    + `?labelId=${encodeURIComponent(a.labelId)}`
    + `&sportType=${a.sportType}`
    + `&fileType=${FILE_TYPE[fileType]}`;
  const res = await fetch(u, { method: 'POST', headers });
  const j: any = await res.json().catch(() => ({}));
  if (j?.result !== '0000' || !j?.data?.fileUrl) {
    throw new Error(j?.message || `Coros download URL failed (result=${j?.result})`);
  }
  return j.data.fileUrl;
}

/** Fetch an activity as text (GPX/TCX). */
export async function downloadText(a: CorosActivity, fileType: 'gpx' | 'tcx'): Promise<string> {
  const url = await downloadUrl(a, fileType);
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Coros file fetch failed (${res.status})`);
  return res.text();
}

/** Fetch an activity's FIT as base64 (ready for writeFitFile). */
export async function downloadFitBase64(a: CorosActivity): Promise<string> {
  const url = await downloadUrl(a, 'fit');
  const res = await fetch(url);
  if (!res.ok) throw new Error(`Coros FIT fetch failed (${res.status})`);
  return bytesToBase64(new Uint8Array(await res.arrayBuffer()));
}

import * as Keychain from 'react-native-keychain';

// ─── Intervals.icu — auth par clé API personnelle (pas d'OAuth) ───────────────
// Voir Settings → Developer Settings sur intervals.icu pour la clé et l'ID athlète.
// Auth : HTTP Basic, username="API_KEY", password=<clé>.

const KEYCHAIN_SERVICE = 'opensportsync_intervals_icu';
const API_BASE         = 'https://intervals.icu/api/v1';

export interface IntervalsIcuCredentials {
  athleteId: string;
  apiKey: string;
}

// Stocké dans le Keychain comme (username=athleteId, password=apiKey) —
// un seul secret générique suffit pour les deux valeurs.
export async function getIntervalsIcuCredentials(): Promise<IntervalsIcuCredentials | null> {
  const creds = await Keychain.getGenericPassword({ service: KEYCHAIN_SERVICE });
  if (!creds) return null;
  return { athleteId: creds.username, apiKey: creds.password };
}

export async function saveIntervalsIcuCredentials(athleteId: string, apiKey: string): Promise<void> {
  await Keychain.setGenericPassword(athleteId.trim(), apiKey.trim(), { service: KEYCHAIN_SERVICE });
}

export async function removeIntervalsIcuCredentials(): Promise<void> {
  await Keychain.resetGenericPassword({ service: KEYCHAIN_SERVICE });
}

export interface IntervalsIcuUploadResult {
  activityId: string;
  viewerUrl: string;
}

export async function uploadFitToIntervalsIcu(
  fitPath: string,
  athleteId: string,
  apiKey: string,
  name?: string,
): Promise<IntervalsIcuUploadResult> {
  const fileName = fitPath.split('/').pop() ?? 'activity.fit';
  const formData = new FormData();
  formData.append('file', {
    uri: `file://${fitPath}`,
    name: fileName,
    type: 'application/fit',
  } as any);
  // Set the activity TITLE from the watch's real activity name (e.g. "Running", or a custom
  // mode name like "Breathing") - this is the intervals.icu upload's own `name` field, separate
  // from the FIT. The FIT itself can only categorise the sport as a fixed enum, so a custom name
  // has no home inside the file; passing it here is what makes the activity show its real name
  // instead of the filename. Empty/whitespace names are skipped (intervals then titles it itself).
  if (name && name.trim()) formData.append('name', name.trim());

  // Basic Auth : username="API_KEY", password=<clé API perso>
  const authHeader = 'Basic ' + btoa(`API_KEY:${apiKey}`);

  const response = await fetch(
    `${API_BASE}/athlete/${encodeURIComponent(athleteId)}/activities`,
    {
      method: 'POST',
      headers: { Authorization: authHeader },
      body: formData,
    }
  );

  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`Intervals.icu: ${response.status} ${response.statusText} — ${text}`);
  }

  const json = await response.json();
  // La réponse peut être un objet unique ou un tableau (import multi-activités) selon le fichier FIT
  const first = Array.isArray(json) ? json[0] : json;
  const activityId = String(first?.id ?? first?.activity_id ?? '');
  if (!activityId) {
    throw new Error('Intervals.icu: unexpected response\n' + JSON.stringify(json));
  }

  return {
    activityId,
    viewerUrl: `https://intervals.icu/activities/${activityId}`,
  };
}

// Permanently delete ONE activity from intervals.icu (2026-08-26, desktop parity — André chose
// "also delete from the source" over a local-only hide). Irreversible on their side, so callers
// must confirm with the user first.
//
// `activityId` is intervals' own id. This app namespaces imported rows as `icu:<id>` locally, so
// strip that prefix before calling. A 404 means it is already gone, which counts as success.
export async function deleteIntervalsIcuActivity(activityId: string): Promise<boolean> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return false;
  const id = activityId.replace(/^icu:/, '');
  const resp = await fetch(`${API_BASE}/activity/${encodeURIComponent(id)}`, {
    method: 'DELETE',
    headers: {
      Authorization: 'Basic ' + btoa(`API_KEY:${creds.apiKey}`),
      'User-Agent': 'Sommet/1.0',
    },
  });
  return resp.ok || resp.status === 404;
}

// ─── Athlete training thresholds (FTP / LTHR / Max HR / weight) ────────────────
// For the Bryton profile reconciliation (BrytonProfile.ts): intervals.icu is the source of truth
// for these numbers. Mirrors the desktop tools/intervals_athlete.py. Read from the Ride
// sportSettings group + profile; write FTP/LTHR/MaxHR back to that group (merge) and weight to
// today's wellness. Gender/birthday/height are athlete-profile fields not writable this way.
export interface AthleteThresholds {
  ftp?: number; lthr?: number; maxHr?: number;
  weight?: number; height?: number; gender?: number; // read-only extras
  age?: number; // from icu_date_of_birth (read-only; the Magene C406 stores an age)
  rideGroupId?: number;
}

// Whole years from a "YYYY-MM-DD" birth date (tools/intervals_athlete.py does the same).
function ageFrom(dob?: string): number | undefined {
  if (!dob) return undefined;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(dob));
  if (!m) return undefined;
  const now = new Date();
  const y = +m[1], mo = +m[2], d = +m[3];
  return now.getFullYear() - y - ((now.getMonth() + 1 < mo || (now.getMonth() + 1 === mo && now.getDate() < d)) ? 1 : 0);
}

function authHeader(apiKey: string): string { return 'Basic ' + btoa(`API_KEY:${apiKey}`); }

export async function getAthleteThresholds(): Promise<AthleteThresholds | null> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return null;
  const resp = await fetch(`${API_BASE}/athlete/${encodeURIComponent(creds.athleteId)}`, {
    headers: { Authorization: authHeader(creds.apiKey), 'User-Agent': 'Sommet/1.0' },
  });
  if (!resp.ok) return null;
  let prof: any = await resp.json();
  if (Array.isArray(prof)) prof = prof[0];
  const groups: any[] = prof.sportSettings || [];
  const ride = groups.find(g => (g.types || []).includes('Ride')) || groups[0] || {};
  const weight = prof.icu_weight ?? prof.weight;
  return {
    ftp: ride.ftp ?? undefined,
    lthr: ride.lthr ?? undefined,
    maxHr: ride.max_hr ?? undefined,
    weight: weight ? Math.round(weight * 10) / 10 : undefined,
    height: prof.height ? Math.round(prof.height * 100) : undefined,
    gender: prof.sex === 'M' ? 1 : prof.sex === 'F' ? 0 : undefined,
    age: ageFrom(prof.icu_date_of_birth ?? prof.date_of_birth),
    rideGroupId: ride.id,
  };
}

export async function putAthleteThresholds(
  changes: { ftp?: number; lthr?: number; maxHr?: number; weight?: number },
): Promise<boolean> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return false;
  const headers = {
    Authorization: authHeader(creds.apiKey), 'User-Agent': 'Sommet/1.0',
    'Content-Type': 'application/json',
  };
  const ss: any = {};
  if (changes.ftp != null) ss.ftp = Math.round(changes.ftp);
  if (changes.lthr != null) ss.lthr = Math.round(changes.lthr);
  if (changes.maxHr != null) ss.max_hr = Math.round(changes.maxHr);
  if (Object.keys(ss).length) {
    const cur = await getAthleteThresholds();
    if (!cur?.rideGroupId) return false;
    const r = await fetch(
      `${API_BASE}/athlete/${encodeURIComponent(creds.athleteId)}/sport-settings/${cur.rideGroupId}`,
      { method: 'PUT', headers, body: JSON.stringify(ss) },
    );
    if (!r.ok) return false;
  }
  if (changes.weight != null) {
    const today = new Date().toISOString().slice(0, 10);
    const r = await fetch(
      `${API_BASE}/athlete/${encodeURIComponent(creds.athleteId)}/wellness/${today}`,
      { method: 'PUT', headers, body: JSON.stringify({ weight: changes.weight }) },
    );
    if (!r.ok) return false;
  }
  return true;
}

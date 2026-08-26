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
): Promise<IntervalsIcuUploadResult> {
  const fileName = fitPath.split('/').pop() ?? 'activity.fit';
  const formData = new FormData();
  formData.append('file', {
    uri: `file://${fitPath}`,
    name: fileName,
    type: 'application/fit',
  } as any);

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

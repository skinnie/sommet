import * as Keychain from 'react-native-keychain';
import { STRAVA_CLIENT_ID, STRAVA_CLIENT_SECRET } from '../config/secrets';

// ─── Configuration OAuth2 Strava ──────────────────────────────────────────────

const STRAVA_REDIRECT_URI = 'opensportsync://oauth/strava';
const STRAVA_AUTH_URL     = 'https://www.strava.com/oauth/authorize';
const STRAVA_TOKEN_URL    = 'https://www.strava.com/oauth/token';
const STRAVA_API_BASE     = 'https://www.strava.com/api/v3';
const STRAVA_SCOPES       = 'activity:write,read';

const KC_TOKEN = 'opensportsync_strava_token';

// ─── Types ────────────────────────────────────────────────────────────────────

interface TokenData {
  access_token:  string;
  refresh_token: string;
  expires_at:    number;  // timestamp ms
}

export interface StravaUploadResult {
  stravaUrl: string;
}

// ─── Token storage ────────────────────────────────────────────────────────────

async function saveToken(data: TokenData): Promise<void> {
  await Keychain.setGenericPassword('strava', JSON.stringify(data), { service: KC_TOKEN });
}

async function loadToken(): Promise<TokenData | null> {
  const creds = await Keychain.getGenericPassword({ service: KC_TOKEN });
  return creds ? JSON.parse(creds.password) : null;
}

export async function isAuthenticated(): Promise<boolean> {
  return (await loadToken()) !== null;
}

export async function logout(): Promise<void> {
  await Keychain.resetGenericPassword({ service: KC_TOKEN });
}

// ─── URL d'autorisation OAuth2 ────────────────────────────────────────────────

/**
 * Génère l'URL d'autorisation Strava.
 * Ouvrir avec Linking.openURL — retour via deep link opensportsync://oauth/strava?code=...
 */
export function getAuthorizationUrl(): string {
  const params = new URLSearchParams({
    client_id:     STRAVA_CLIENT_ID,
    redirect_uri:  STRAVA_REDIRECT_URI,
    response_type: 'code',
    approval_prompt: 'auto',
    scope:         STRAVA_SCOPES,
  });
  return `${STRAVA_AUTH_URL}?${params.toString()}`;
}

// ─── Échange du code contre un token ──────────────────────────────────────────

/**
 * Appelé par App.tsx quand le deep link opensportsync://oauth/strava?code=... est reçu.
 */
export async function handleOAuthCallback(code: string): Promise<void> {
  const response = await fetch(STRAVA_TOKEN_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      client_id:     STRAVA_CLIENT_ID,
      client_secret: STRAVA_CLIENT_SECRET,
      code,
      grant_type:    'authorization_code',
    }).toString(),
  });

  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Strava token exchange failed: HTTP ${response.status} — ${body}`);
  }

  const json = await response.json();
  await saveToken({
    access_token:  json.access_token,
    refresh_token: json.refresh_token,
    expires_at:    json.expires_at * 1000,  // Strava retourne des secondes
  });
}

// ─── Refresh automatique ──────────────────────────────────────────────────────

async function getValidToken(): Promise<string> {
  let token = await loadToken();
  if (!token) throw new Error('Not authenticated with Strava');

  if (Date.now() > token.expires_at - 60_000) {
    const response = await fetch(STRAVA_TOKEN_URL, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id:     STRAVA_CLIENT_ID,
        client_secret: STRAVA_CLIENT_SECRET,
        grant_type:    'refresh_token',
        refresh_token: token.refresh_token,
      }).toString(),
    });

    if (!response.ok) {
      await logout();
      throw new Error('Strava session expired, please reconnect');
    }

    const json = await response.json();
    token = {
      access_token:  json.access_token,
      refresh_token: json.refresh_token ?? token.refresh_token,
      expires_at:    json.expires_at * 1000,
    };
    await saveToken(token);
  }

  return token.access_token;
}

// ─── Upload FIT vers Strava ───────────────────────────────────────────────────

/**
 * Uploads a FIT file to Strava and waits for processing to finish. Strava reads the sport from
 * the FIT itself (we no longer guess a Strava type from the activity name). Returns the activity
 * URL on Strava.
 */
export async function uploadFitToStrava(
  fitPath: string,
  activityName?: string,
): Promise<StravaUploadResult> {
  const accessToken   = await getValidToken();
  const fileUri       = fitPath.startsWith('file://') ? fitPath : `file://${fitPath}`;
  const fileName      = fitPath.split('/').pop() ?? 'activity.fit';

  // Upload the FIT, not the GPX. Strava reads the sport (and sub_sport - so an indoor ride comes
  // in as a Virtual Ride) straight from the FIT, which fixes two things at once: the old GPX path
  // carried no sport (Strava guessed "Workout"), and an indoor move has no GPS track for a GPX to
  // even hold - the FIT is the only thing that represents it. Trainer flag set for indoor/virtual
  // moves so Strava marks them as such.
  const indoor = /indoor|virtual|trainer|treadmill|home\s*trainer/i.test(activityName || '');

  const formData = new FormData();
  formData.append('file', {
    uri:  fileUri,
    type: 'application/vnd.ant.fit',
    name: fileName,
  } as any);
  formData.append('data_type', 'fit');
  formData.append('name',      activityName || fileName.replace('.fit', ''));
  if (indoor) formData.append('trainer', '1');

  const uploadRes = await fetch(`${STRAVA_API_BASE}/uploads`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${accessToken}` },
    body: formData,
  });

  if (uploadRes.status === 429) {
    throw new Error('Strava rate limit reached (200 req/15 min). Try again in 15 min.');
  }
  if (!uploadRes.ok) {
    const body = await uploadRes.text();
    throw new Error(`Strava upload failed: HTTP ${uploadRes.status} — ${body}`);
  }

  const upload = await uploadRes.json();
  if (upload.error) {
    throw new Error(`Strava: ${upload.error}`);
  }

  const uploadId = upload.id_str ?? String(upload.id);

  // 2. Polling jusqu'à traitement (max 30 × 2s = 60s)
  for (let i = 0; i < 30; i++) {
    await new Promise(r => setTimeout(r, 2000));

    const statusRes = await fetch(`${STRAVA_API_BASE}/uploads/${uploadId}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    if (statusRes.status === 429) {
      throw new Error('Strava rate limit reached (200 req/15 min). Try again in 15 min.');
    }
    if (!statusRes.ok) continue;

    const status = await statusRes.json();

    if (status.error) {
      throw new Error(`Strava processing error: ${status.error}`);
    }
    if (status.activity_id) {
      return { stravaUrl: `https://www.strava.com/activities/${status.activity_id}` };
    }
    // status: "Your activity is being processed." → continuer
  }

  throw new Error('Strava timeout: processing not finished after 60 seconds');
}

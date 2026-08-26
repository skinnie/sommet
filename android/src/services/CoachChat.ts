import * as Keychain from 'react-native-keychain';
import { Readiness } from './CoachService';

// Coach chat — the Android port of desktop coachservice.cpp's chat half (André, 2026-08-26:
// "coach, the key is given by the user. systm has an mcp also... I never said it was easy").
//
// Two backends, exactly like the desktop:
//   - "canned"  : pre-written replies keyed off the message. Free, offline, no key.
//   - "claude"  : a real conversation with the Anthropic API, using the user's OWN key.
// The key is supplied by the user (console.anthropic.com), never bundled, and is NOT a
// claude.ai subscription - that distinction is the one users trip over, so the Settings UI
// says so in plain words.
//
// The key lives in the Keychain, not AsyncStorage: it is a credential, and this app already
// keeps the intervals.icu credentials there (ApiIntervalsIcu.ts) rather than in plain storage.

const KEYCHAIN_SERVICE = 'sommet.coach.anthropic';
const ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages';
// Same model the desktop asks for, so both apps answer with the same voice.
const MODEL = 'claude-sonnet-5';

export type ChatBackend = 'canned' | 'claude';
export interface ChatMessage {
  role: 'me' | 'coach';
  text: string;
}

// ---- key storage -------------------------------------------------------------------------

export async function getAnthropicKey(): Promise<string | null> {
  try {
    const creds = await Keychain.getGenericPassword({ service: KEYCHAIN_SERVICE });
    return creds ? creds.password : null;
  } catch {
    return null;
  }
}

export async function setAnthropicKey(key: string): Promise<void> {
  // username is unused by this credential; Keychain requires one, so a fixed label is stored.
  await Keychain.setGenericPassword('anthropic', key.trim(), { service: KEYCHAIN_SERVICE });
}

export async function clearAnthropicKey(): Promise<void> {
  await Keychain.resetGenericPassword({ service: KEYCHAIN_SERVICE });
}

export async function hasAnthropicKey(): Promise<boolean> {
  return !!(await getAnthropicKey());
}

// ---- system prompt -----------------------------------------------------------------------

// Same instructions as the desktop's, including the "no jargon" rule - a coach that answers in
// TSS/CTL/TSB is useless to read on a phone mid-ride.
function systemPrompt(r: Readiness | null): string {
  const state = r
    ? `Today's readiness: ${r.light} (${r.sentence}). `
      + `Fitness ${Math.round(r.fitness)}, Fatigue ${Math.round(r.fatigue)}, `
      + `Freshness ${Math.round(r.freshness)}.`
    : `Today's readiness: not available (intervals.icu not connected or no training load yet).`;

  return (
    "You are Sommet's training coach for a Suunto Ambit3 watch user. Be concise and warm, "
    + 'like a knowledgeable training partner, not a corporate assistant. Never use jargon '
    + '(no "TSS"/"CTL"/"TSB" in your reply - translate to plain language).\n\n'
    + state + '\n\n'
    + "Rider profile: not yet collected by this app (no onboarding screen built) - don't "
    + 'assume specifics about their sport or goals beyond what they tell you in the chat.'
  );
}

// ---- Claude ------------------------------------------------------------------------------

export async function replyClaude(
  history: ChatMessage[],
  readiness: Readiness | null,
): Promise<string> {
  const key = await getAnthropicKey();
  if (!key) throw new Error('No Anthropic API key set.');

  // The whole conversation is sent, not just the latest line, so follow-ups ("and if it rains?")
  // actually have context. The desktop currently sends only the last message - this is the one
  // deliberate improvement over it rather than a difference for its own sake.
  const messages = history.map(m => ({
    role: m.role === 'me' ? 'user' : 'assistant',
    content: m.text,
  }));

  const resp = await fetch(ANTHROPIC_URL, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      'x-api-key': key,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model: MODEL,
      max_tokens: 500,
      system: systemPrompt(readiness),
      messages,
    }),
  });

  if (!resp.ok) {
    // Surface the status: 401 means a bad or expired key, 429 means rate-limited, and the user
    // can act on each differently. A silent failure would look like the coach ignoring them.
    const detail = resp.status === 401
      ? 'the key was rejected - check it at console.anthropic.com'
      : resp.status === 429 ? 'rate limited, try again shortly'
      : `HTTP ${resp.status}`;
    throw new Error(`Claude API error: ${detail}`);
  }

  const json = await resp.json();
  // Anthropic returns content as an array of typed blocks; join the text ones.
  const text = Array.isArray(json?.content)
    ? json.content.filter((b: any) => b?.type === 'text').map((b: any) => b.text).join('').trim()
    : '';
  return text || '(no reply)';
}

// ---- canned --------------------------------------------------------------------------------

// Mirrors the desktop's replyCanned(): keyed off what the message mentions, and honest about
// what is not wired up rather than pretending.
export function replyCanned(userText: string, readiness: Readiness | null): string {
  const t = userText.toLowerCase();

  if (t.includes('short')) {
    return "Sure — keep it short today. Something around 30 minutes, easy enough that you could "
      + 'hold a conversation the whole way.';
  }
  if (t.includes('outdoor') || t.includes('outside')) {
    return "I don't have live weather wired into this reply yet — check the forecast on Home, "
      + 'then pick a route from Routes and go.';
  }
  if (t.includes('watch') || t.includes('send')) {
    return "Sending a session to your watch isn't wired up from this screen yet. For now, build "
      + 'it in the Workout Calendar and sync from there.';
  }
  if (readiness) {
    return readiness.sentence;
  }
  return 'Connect intervals.icu in Settings and I can tell you how today looks.';
}

// One entry point the screen calls; picks the backend the same way the desktop does.
export async function sendCoachMessage(
  history: ChatMessage[],
  readiness: Readiness | null,
  backend: ChatBackend,
): Promise<string> {
  if (backend === 'claude' && (await hasAnthropicKey())) {
    return replyClaude(history, readiness);
  }
  const last = history.length > 0 ? history[history.length - 1].text : '';
  return replyCanned(last, readiness);
}

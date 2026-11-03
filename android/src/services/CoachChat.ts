import * as Keychain from 'react-native-keychain';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Readiness, WorkoutPick } from './CoachService';

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

// A card under a coach bubble (André, 2026-11-02, desktop picks #18): a workout (the same
// fields as the canned picks, so the same card draws both), one number in a tile, or a tip.
export interface CoachCard {
  type: 'workout' | 'stat' | 'note';
  // workout
  name?: string;
  durationSec?: number;
  intensity?: string;
  intensityFactor?: number;
  load?: number;
  // stat
  label?: string;
  value?: string;
  unit?: string;
  caption?: string;
  // note
  text?: string;
}

export interface ChatMessage {
  role: 'me' | 'coach';
  text: string;
  cards?: CoachCard[];
  // A note from the app itself (an error, pre-written help): shown in the chat, never sent to
  // Claude as part of the conversation (desktop's `local` bubbles).
  local?: boolean;
}

// What a reply adds to the chat: the coach bubble without its role.
export type CoachReply = Omit<ChatMessage, 'role'>;

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

// ---- which replies: pre-written or Claude -----------------------------------------------

// Settings -> Coach -> "Chat with a real AI coach" (desktop parity, 2026-10-22: CoachService's
// chatBackend). Saved on its own, apart from the key. Before this switch existed the replies
// simply followed whether a key was stored, so with nothing saved yet that is still the answer -
// an install that already chats with Claude keeps doing so.
const CHAT_BACKEND_KEY = 'ambitapp:coach:chatBackend';

export async function getChatBackend(): Promise<ChatBackend> {
  try {
    const v = await AsyncStorage.getItem(CHAT_BACKEND_KEY);
    if (v === 'claude' || v === 'canned') return v;
  } catch { /* fall through */ }
  return (await hasAnthropicKey()) ? 'claude' : 'canned';
}

export async function setChatBackend(v: ChatBackend): Promise<void> {
  await AsyncStorage.setItem(CHAT_BACKEND_KEY, v);
}

// ---- system prompt -----------------------------------------------------------------------

// Same instructions as the desktop's (coachservice.cpp replyClaude), including the "no jargon"
// rule - a coach that answers in TSS/CTL/TSB is useless to read on a phone mid-ride - and
// today's catalogue picks, so a workout card can only ever be one of them.
export function systemPrompt(r: Readiness | null, picks: WorkoutPick[] = []): string {
  const state = r
    ? `Today's readiness: ${r.light} (${r.sentence}). `
      + `Fitness ${Math.round(r.fitness)}, Fatigue ${Math.round(r.fatigue)}, `
      + `Freshness ${Math.round(r.freshness)}.`
    : `Today's readiness: not available (intervals.icu not connected or no training load yet).`;
  const pickLines = picks.map(p =>
    `- ${p.name} (${Math.trunc(Math.trunc(p.durationSec || 0) / 60)} min, intensity ${p.intensity}, `
    + `intensity factor ${(p.intensityFactor || 0).toFixed(2)}, TSS ${(p.load || 0).toFixed(0)})`);

  return (
    "You are Sommet's training coach for a Suunto Ambit3 watch user. Be concise and warm, "
    + 'like a knowledgeable training partner, not a corporate assistant. Never use jargon '
    + '(no "TSS"/"CTL"/"TSB" in your reply - translate to plain language).\n\n'
    + state + '\n'
    + "Today's catalogue picks for this readiness:\n"
    + (pickLines.length ? pickLines.join('\n') : "(none match today's duration/intensity)") + '\n\n'
    + "Rider profile: not yet collected by this app (no onboarding screen built) - don't "
    + 'assume specifics about their sport or goals beyond what they tell you in the chat.'
    // How the answer is laid out on the Coach screen (desktop picks #18).
    + '\n\nYour answer is shown in a chat bubble with cards under it. Put your reply in "text": '
    + 'one to four short sentences; Markdown is fine (bold, a short list). Add "cards" only '
    + 'when they help, usually none to two:\n'
    + '- "workout": a session from today\'s catalogue picks above, copying its name, minutes, '
    + 'intensity, intensity factor ("if") and TSS ("tss") exactly. Never invent a workout card.\n'
    + '- "stat": one number worth showing, e.g. label "Freshness", value "-12", unit "", '
    + 'caption "tired, an easy day helps". Plain words in label and caption.\n'
    + '- "note": a one-line tip.\n'
    + 'Don\'t repeat in "text" what a card already shows.'
  );
}

// The one shape Claude answers in (desktop picks #18, kCoachReplySchema in coachservice.cpp):
// a short Markdown text, plus cards the Coach screen knows how to draw. Structured outputs
// (output_config.format) guarantee the JSON. Kept identical to the desktop's.
export const COACH_REPLY_SCHEMA = {
  type: 'object',
  properties: {
    text: { type: 'string' },
    cards: {
      type: 'array',
      items: {
        anyOf: [
          {
            type: 'object',
            properties: {
              type: { type: 'string', const: 'workout' },
              name: { type: 'string' },
              minutes: { type: 'integer' },
              intensity: { type: 'string' },
              if: { type: 'number' },
              tss: { type: 'number' },
            },
            required: ['type', 'name', 'minutes', 'intensity', 'if', 'tss'],
            additionalProperties: false,
          },
          {
            type: 'object',
            properties: {
              type: { type: 'string', const: 'stat' },
              label: { type: 'string' },
              value: { type: 'string' },
              unit: { type: 'string' },
              caption: { type: 'string' },
            },
            required: ['type', 'label', 'value', 'unit', 'caption'],
            additionalProperties: false,
          },
          {
            type: 'object',
            properties: {
              type: { type: 'string', const: 'note' },
              text: { type: 'string' },
            },
            required: ['type', 'text'],
            additionalProperties: false,
          },
        ],
      },
    },
  },
  required: ['text', 'cards'],
  additionalProperties: false,
};

const MAX_TURNS = 12;
const MAX_CHARS = 12000;

// The recent conversation for Claude (desktop picks #16, chatHistoryForClaude): the Messages API
// wants turns that alternate and start with the user, so same-role bubbles are merged, a coach
// bubble before the first question is left out, and local notes (errors, pre-written help) are
// skipped. Cards become one line each. Kept to the last 12 turns / ~12,000 characters; the newest
// turn always goes, even when it's long on its own.
export function chatHistoryForClaude(history: ChatMessage[]): { role: 'user' | 'assistant'; content: string }[] {
  const turns: { role: 'user' | 'assistant'; text: string }[] = [];
  for (const m of history) {
    if (m.local) continue;
    const role = m.role === 'me' ? 'user' : 'assistant';
    let text = (m.text || '').trim();
    const cardLines = (m.cards || []).map(c => {
      if (c.type === 'stat') return `[${`${c.label ?? ''}: ${c.value ?? ''} ${c.unit ?? ''}`.trim()}]`;
      if (c.type === 'note') return c.text ?? '';
      return `[Workout card: ${c.name ?? ''}, ${Math.trunc(Math.trunc(c.durationSec || 0) / 60)} min]`;
    });
    if (cardLines.length) text += (text ? '\n' : '') + cardLines.join('\n');
    if (!text) continue;
    const last = turns[turns.length - 1];
    if (last && last.role === role) last.text += '\n\n' + text;
    else turns.push({ role, text });
  }

  const count = turns.length;
  let start = count;
  let chars = 0;
  while (start > 0 && count - start < MAX_TURNS) {
    const len = turns[start - 1].text.length;
    if (chars + len > MAX_CHARS && start < count) break;
    chars += len;
    start -= 1;
  }
  while (start < count && turns[start].role !== 'user') start += 1;
  return turns.slice(start).map(t => ({ role: t.role, content: t.text }));
}

const str = (v: unknown): string => (typeof v === 'string' ? v : '');
const num = (v: unknown): number => (typeof v === 'number' && isFinite(v) ? v : 0);

// Claude's cards -> the cards the Coach screen draws (desktop cardsFromClaude). At most four;
// a card type the screen doesn't know shows its text, or is skipped.
export function cardsFromClaude(cards: unknown): CoachCard[] {
  const out: CoachCard[] = [];
  if (!Array.isArray(cards)) return out;
  for (const v of cards) {
    if (out.length >= 4) break;
    const c = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>;
    const type = str(c.type);
    if (type === 'workout') {
      out.push({
        type: 'workout',
        name: str(c.name),
        durationSec: Math.round(num(c.minutes) * 60),
        intensity: str(c.intensity),
        intensityFactor: num(c.if),
        load: num(c.tss),
      });
    } else if (type === 'stat') {
      out.push({ type: 'stat', label: str(c.label), value: str(c.value), unit: str(c.unit), caption: str(c.caption) });
    } else {
      const text = str(c.text).trim();
      if (text) out.push({ type: 'note', text });
    }
  }
  return out;
}

// Wording kept identical to the desktop's local bubbles.
export const MSG_KEY_REFUSED = "(Claude didn't accept your API key. Paste a new one in Settings → Coach.)";
export const MSG_REFUSAL = "(Claude didn't answer that one — try asking another way.)";
export const MSG_EMPTY = '(empty reply from Claude)';
export const MSG_INCOMPLETE = "(Claude's answer came back incomplete — try again.)";
export const msgUnreachable = (detail: string) =>
  `(couldn't reach Claude: ${detail} — falling back, try again in a moment)`;

// A Messages API response -> the coach bubble (desktop replyClaude's finished handler). The
// answer is in the text blocks; a thinking block can come first, so all text blocks are joined.
export function replyFromResponse(json: any): CoachReply {
  const stopReason = str(json?.stop_reason);
  const raw = Array.isArray(json?.content)
    ? json.content.filter((b: any) => b?.type === 'text').map((b: any) => str(b.text)).join('')
    : '';
  if (stopReason === 'refusal') return { text: MSG_REFUSAL, local: true };
  let parsed: unknown = null;
  try { parsed = JSON.parse(raw); } catch { parsed = null; }
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const answer = parsed as Record<string, unknown>;
    const text = str(answer.text).trim();
    const cards = cardsFromClaude(answer.cards);
    if (!text && cards.length === 0) return { text: MSG_EMPTY, local: true };
    return { text, cards };
  }
  // JSON cut off (e.g. stop_reason "max_tokens"): don't show half an object.
  if (raw.trim().startsWith('{')) return { text: MSG_INCOMPLETE, local: true };
  if (raw.trim()) return { text: raw.trim() };   // plain text, as before
  return { text: MSG_EMPTY, local: true };
}

// A failed HTTP answer -> one short reason for the "couldn't reach Claude" bubble.
function errorDetail(status: number, json: any): string {
  if (status === 429) return 'too many requests';
  if (status === 529) return 'Claude is busy';
  const own = str(json?.error?.message).trim();
  return own || `HTTP ${status}`;
}

// ---- Claude ------------------------------------------------------------------------------

export async function replyClaude(
  history: ChatMessage[],
  readiness: Readiness | null,
  picks: WorkoutPick[] = [],
): Promise<CoachReply> {
  const key = await getAnthropicKey();
  if (!key) throw new Error('No Anthropic API key set.');

  let messages = chatHistoryForClaude(history);
  if (messages.length === 0 || messages[messages.length - 1].role !== 'user') {
    const lastMine = [...history].reverse().find(m => m.role === 'me');
    messages = [{ role: 'user', content: lastMine?.text ?? '' }];
  }

  let resp: Response;
  try {
    resp = await fetch(ANTHROPIC_URL, {
      method: 'POST',
      headers: {
        'content-type': 'application/json',
        'x-api-key': key,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: MODEL,
        // Room for the model's own thinking as well as the answer: a cap that's too low cuts the
        // JSON off mid-way (desktop picks #18).
        max_tokens: 16000,
        system: systemPrompt(readiness, picks),
        messages,
        output_config: { format: { type: 'json_schema', schema: COACH_REPLY_SCHEMA } },
      }),
    });
  } catch (e: any) {
    return { text: msgUnreachable(String(e?.message ?? e)), local: true };
  }

  let json: any = null;
  try { json = await resp.json(); } catch { json = null; }
  if (!resp.ok) {
    // A refused key won't work "in a moment": say where to fix it.
    if (resp.status === 401) return { text: MSG_KEY_REFUSED, local: true };
    return { text: msgUnreachable(errorDetail(resp.status, json)), local: true };
  }
  return replyFromResponse(json);
}

// ---- canned --------------------------------------------------------------------------------

// Mirrors the desktop's replyCanned(): keyed off what the message mentions, and honest about
// what is not wired up rather than pretending.
// The "not wired up" note and the connect-intervals help are local (desktop parity): never sent
// to Claude as part of the conversation.
export function replyCanned(userText: string, readiness: Readiness | null): CoachReply {
  const t = userText.toLowerCase();

  if (t.includes('short')) {
    return { text: "Sure — keep it short today. Something around 30 minutes, easy enough that you could "
      + 'hold a conversation the whole way.' };
  }
  if (t.includes('outdoor') || t.includes('outside')) {
    return { text: "I don't have live weather wired into this reply yet — check the forecast on Home, "
      + 'then pick a route from Routes and go.' };
  }
  if (t.includes('watch') || t.includes('send')) {
    return { text: "Sending a session to your watch isn't wired up from this screen yet. For now, build "
      + 'it in the Workout Calendar and sync from there.', local: true };
  }
  if (readiness) {
    return { text: readiness.sentence };
  }
  return { text: 'Connect intervals.icu in Settings and I can tell you how today looks.', local: true };
}

// One entry point the screen calls; picks the backend the same way the desktop does.
export async function sendCoachMessage(
  history: ChatMessage[],
  readiness: Readiness | null,
  backend: ChatBackend,
  picks: WorkoutPick[] = [],
): Promise<CoachReply> {
  if (backend === 'claude' && (await hasAnthropicKey())) {
    return replyClaude(history, readiness, picks);
  }
  const last = history.length > 0 ? history[history.length - 1].text : '';
  return replyCanned(last, readiness);
}

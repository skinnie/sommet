jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn() }));
jest.mock('react-native-keychain', () => ({
  getGenericPassword: jest.fn(async () => ({ username: 'anthropic', password: 'test-key-not-real' })),
  setGenericPassword: jest.fn(),
  resetGenericPassword: jest.fn(),
}));

import {
  chatHistoryForClaude, cardsFromClaude, replyFromResponse, replyClaude, replyCanned, systemPrompt,
  ChatMessage, MSG_KEY_REFUSED, MSG_REFUSAL, MSG_EMPTY, MSG_INCOMPLETE, COACH_REPLY_SCHEMA,
} from '../CoachChat';
import { WorkoutPick } from '../CoachService';

const picks: WorkoutPick[] = [
  { name: 'Recovery Spin', durationSec: 1800, load: 18, intensityFactor: 0.55, intensity: 'recovery' },
];

describe('chatHistoryForClaude (desktop picks #16)', () => {
  it('starts with the user, merges same-role turns and skips local notes', () => {
    const h: ChatMessage[] = [
      { role: 'coach', text: 'Good morning.' },               // before the first question: left out
      { role: 'me', text: 'How do I look?' },
      { role: 'coach', text: 'Fresh.' },
      { role: 'coach', text: '(couldn\'t reach Claude: x)', local: true },
      { role: 'coach', text: 'Go easy.' },
      { role: 'me', text: 'And tomorrow?' },
    ];
    expect(chatHistoryForClaude(h)).toEqual([
      { role: 'user', content: 'How do I look?' },
      { role: 'assistant', content: 'Fresh.\n\nGo easy.' },
      { role: 'user', content: 'And tomorrow?' },
    ]);
  });

  it('turns cards into one line each, like the desktop', () => {
    const h: ChatMessage[] = [
      { role: 'me', text: 'Today?' },
      { role: 'coach', text: 'Easy day.', cards: [
        { type: 'workout', name: 'Recovery Spin', durationSec: 1830 },
        { type: 'stat', label: 'Freshness', value: '-12', unit: '' },
        { type: 'note', text: 'Drink before you go.' },
      ] },
      { role: 'me', text: 'Ok' },
    ];
    expect(chatHistoryForClaude(h)[1].content)
      .toBe('Easy day.\n[Workout card: Recovery Spin, 30 min]\n[Freshness: -12]\nDrink before you go.');
  });

  it('keeps the last 12 turns, and starts on a user turn', () => {
    const h: ChatMessage[] = [];
    for (let i = 0; i < 10; i++) {
      h.push({ role: 'me', text: `q${i}` });
      h.push({ role: 'coach', text: `a${i}` });
    }
    h.push({ role: 'me', text: 'last' });
    const out = chatHistoryForClaude(h);
    expect(out.length).toBeLessThanOrEqual(12);
    expect(out[0].role).toBe('user');
    expect(out[out.length - 1]).toEqual({ role: 'user', content: 'last' });
    expect(out[0].content).toBe('q5');
  });

  it('keeps to ~12,000 characters, but always sends the newest turn', () => {
    const big = 'x'.repeat(7000);
    const h: ChatMessage[] = [
      { role: 'me', text: big },
      { role: 'coach', text: big },
      { role: 'me', text: big },
    ];
    expect(chatHistoryForClaude(h)).toEqual([{ role: 'user', content: big }]);
    const huge = 'y'.repeat(20000);
    expect(chatHistoryForClaude([{ role: 'me', text: huge }])).toEqual([{ role: 'user', content: huge }]);
  });
});

describe('cardsFromClaude (desktop picks #18)', () => {
  it('maps workouts to the pick fields and keeps at most four cards', () => {
    const cards = cardsFromClaude([
      { type: 'workout', name: 'Spin', minutes: 30, intensity: 'recovery', if: 0.55, tss: 18 },
      { type: 'stat', label: 'Freshness', value: '-12', unit: '', caption: 'tired' },
      { type: 'note', text: '  Sleep early.  ' },
      { type: 'chart', text: 'shown as a note' },
      { type: 'chart' },                                    // no text: skipped
      { type: 'note', text: 'fifth card: dropped' },
    ]);
    expect(cards).toEqual([
      { type: 'workout', name: 'Spin', durationSec: 1800, intensity: 'recovery', intensityFactor: 0.55, load: 18 },
      { type: 'stat', label: 'Freshness', value: '-12', unit: '', caption: 'tired' },
      { type: 'note', text: 'Sleep early.' },
      { type: 'note', text: 'shown as a note' },
    ]);
    expect(cardsFromClaude('nope')).toEqual([]);
  });
});

describe('replyFromResponse', () => {
  const answer = (text: string, stop = 'end_turn') => ({
    stop_reason: stop,
    content: [{ type: 'thinking', thinking: '...' }, { type: 'text', text }],
  });
  it('reads the JSON answer from the text blocks, after a thinking block', () => {
    const r = replyFromResponse(answer(JSON.stringify({ text: '**Easy** day.', cards: [{ type: 'note', text: 'Hydrate.' }] })));
    expect(r).toEqual({ text: '**Easy** day.', cards: [{ type: 'note', text: 'Hydrate.' }] });
  });
  it('says so for a refusal, a cut-off answer and an empty one', () => {
    expect(replyFromResponse(answer('', 'refusal'))).toEqual({ text: MSG_REFUSAL, local: true });
    expect(replyFromResponse(answer('{"text": "Eas', 'max_tokens'))).toEqual({ text: MSG_INCOMPLETE, local: true });
    expect(replyFromResponse(answer(JSON.stringify({ text: ' ', cards: [] })))).toEqual({ text: MSG_EMPTY, local: true });
    expect(replyFromResponse({ content: [] })).toEqual({ text: MSG_EMPTY, local: true });
  });
  it('shows a plain-text answer as it is', () => {
    expect(replyFromResponse(answer('Just ride easy.'))).toEqual({ text: 'Just ride easy.' });
  });
});

describe('systemPrompt', () => {
  it('lists today\'s picks like the desktop', () => {
    const p = systemPrompt(null, picks);
    expect(p).toContain("Today's catalogue picks for this readiness:\n- Recovery Spin (30 min, intensity recovery, intensity factor 0.55, TSS 18)");
    expect(p).toContain('Never invent a workout card.');
    expect(systemPrompt(null, [])).toContain("(none match today's duration/intensity)");
  });
});

describe('replyCanned', () => {
  it('marks the "not wired up" note as local', () => {
    expect(replyCanned('send it to my watch', null).local).toBe(true);
    expect(replyCanned('something shorter', null).local).toBeUndefined();
  });
});

describe('replyClaude', () => {
  const realFetch = globalThis.fetch;
  afterEach(() => { globalThis.fetch = realFetch; });

  it('asks for the structured answer with the recent conversation', async () => {
    const fetchMock = jest.fn(async (_url: string, _init: any) => ({
      ok: true, status: 200,
      json: async () => ({ stop_reason: 'end_turn', content: [{ type: 'text', text: '{"text":"Hi.","cards":[]}' }] }),
    }));
    globalThis.fetch = fetchMock as any;
    const r = await replyClaude([{ role: 'me', text: 'Hello' }], null, picks);
    expect(r).toEqual({ text: 'Hi.', cards: [] });
    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.max_tokens).toBe(16000);
    expect(body.output_config).toEqual({ format: { type: 'json_schema', schema: COACH_REPLY_SCHEMA } });
    expect(body.messages).toEqual([{ role: 'user', content: 'Hello' }]);
    expect(body.system).toContain('Recovery Spin');
  });

  it('says where to paste a new key on a 401', async () => {
    globalThis.fetch = jest.fn(async () => ({ ok: false, status: 401, json: async () => ({}) })) as any;
    expect(await replyClaude([{ role: 'me', text: 'Hello' }], null)).toEqual({ text: MSG_KEY_REFUSED, local: true });
  });

  it('says it couldn\'t reach Claude for other failures', async () => {
    globalThis.fetch = jest.fn(async () => ({ ok: false, status: 429, json: async () => ({}) })) as any;
    const r = await replyClaude([{ role: 'me', text: 'Hello' }], null);
    expect(r.local).toBe(true);
    expect(r.text).toBe("(couldn't reach Claude: too many requests — falling back, try again in a moment)");
    globalThis.fetch = jest.fn(async () => { throw new Error('Network request failed'); }) as any;
    expect((await replyClaude([{ role: 'me', text: 'Hello' }], null)).text)
      .toBe("(couldn't reach Claude: Network request failed — falling back, try again in a moment)");
  });
});

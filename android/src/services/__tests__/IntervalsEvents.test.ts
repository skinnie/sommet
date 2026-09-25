// Parity with tools/intervals_events.py (fixture generated from it).
jest.mock('../ApiIntervalsIcu', () => ({ getIntervalsIcuCredentials: jest.fn(async () => null) }));
import fx from './intervals_events.fixture.json';
import { eventBody } from '../IntervalsEvents';

it('event bodies match the desktop tool', () => {
  expect(fx.entries.map((e: any) => eventBody(e))).toEqual(fx.bodies);
});

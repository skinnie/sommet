jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn() }));
import { compass, summarizeHourly } from '../ActivityWeather';

// The real Open-Meteo hours (Lille, 2026-09-23) the desktop's weather line was checked against:
// a 37 km ride 10:15:30-11:28:58 UTC showed "Overcast · 18–21 °C · 8 km/h S (gusts 19)".
const HOURLY = {
  time: ['2026-09-23T09:00', '2026-09-23T10:00', '2026-09-23T11:00', '2026-09-23T12:00'],
  temperature_2m: [17.2, 18.3, 20.5, 22.3],
  apparent_temperature: [16, 17, 19, 21],
  precipitation: [0, 0, 0, 0],
  wind_speed_10m: [9.7, 9.0, 6.8, 6.1],
  wind_direction_10m: [165, 204, 194, 205],
  wind_gusts_10m: [19.8, 19.4, 16.9, 18.0],
  weather_code: [1, 3, 3, 0],
};
const START = Date.parse('2026-09-23T10:15:30Z');
const END = START + 4408 * 1000;

describe('summarizeHourly', () => {
  it('uses exactly the hours the move spans - same numbers the desktop showed', () => {
    const w = summarizeHourly(HOURLY, START, END, [])!;
    expect(w.tempMin).toBe(18.3);
    expect(w.tempMax).toBe(20.5);
    expect(Math.round(w.windKmh)).toBe(8);
    expect(w.windCompass).toBe('S');
    expect(Math.round(w.gustKmh)).toBe(19);
    expect(w.code).toBe(3);
    expect(w.headShare).toBeUndefined();   // no track -> no wind shares
  });

  it('splits a track into head / cross / tail wind', () => {
    // Wind from the south (~200°): riding due south is headwind, due north tailwind.
    const south = [{ lat: 50.60, lon: 3.08 }, { lat: 50.59, lon: 3.08 }, { lat: 50.58, lon: 3.08 }];
    const north = [...south].reverse();
    const out = summarizeHourly(HOURLY, START, END, [...south, ...north.slice(1)])!;
    expect(out.headShare).toBeCloseTo(0.5, 1);
    expect(out.tailShare).toBeCloseTo(0.5, 1);
  });

  it('is null when no hour overlaps', () => {
    expect(summarizeHourly(HOURLY, Date.parse('2026-09-24T10:00:00Z'), Date.parse('2026-09-24T11:00:00Z'), [])).toBeNull();
  });
});

describe('compass', () => {
  it('names the 8 points', () => {
    expect([0, 45, 90, 135, 180, 225, 270, 315, 359].map(compass)).toEqual(['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW', 'N']);
  });
});

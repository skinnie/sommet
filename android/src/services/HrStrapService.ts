import { NativeModules } from 'react-native';
import { hrvSummary, type HrvSummary } from './hrv';

// Morning-HRV from a standard BLE heart-rate strap (André's COOSPO HW9, or any strap that reports
// R-R). This is the mobile twin of the desktop's tools/hrv_strap.py: the native module does the
// BLE (scan 0x180D → connect → notify 0x2A37 → collect R-R for N seconds), and the HRV math is the
// shared TS port in ./hrv.ts — so iOS and Android compute the same RMSSD as the desktop.
//
// Modeled on SmartSensorService.ts: a thin wrapper over an optional native module. The native
// `HrStrap` module ships in a mobile build; until it's present, isHrStrapAvailable() is false and
// the screen degrades gracefully instead of throwing.

const Native = (NativeModules as any).HrStrap as
  | {
      // Scan for a strap advertising the Heart Rate service (optionally matching a name
      // substring), connect, collect R-R for `seconds`, disconnect, and resolve with the raw
      // intervals in milliseconds plus the strap identity. Rejects if none found / not worn.
      measure(seconds: number, nameFilter: string | null): Promise<HrStrapRaw>;
    }
  | undefined;

export interface HrStrapRaw {
  mac: string;
  name?: string;
  rrMs: number[]; // raw R-R intervals in ms (unfiltered; hrvSummary cleans them)
}

export interface HrStrapReading extends HrvSummary {
  ok: boolean;
  mac: string;
  name?: string;
  rrMs: number[];
}

export function isHrStrapAvailable(): boolean {
  return !!Native;
}

/**
 * Take a morning-HRV spot reading: collect ~`seconds` of R-R from the strap, then compute HRV
 * with the shared math. `nameFilter` defaults to "HW9" (COOSPO) but any RR-capable strap works.
 */
export async function measureHrv(
  seconds = 120,
  nameFilter: string | null = 'HW9',
): Promise<HrStrapReading> {
  if (!Native) throw new Error('native-missing');
  const raw = await Native.measure(seconds, nameFilter);
  const summary = hrvSummary(raw.rrMs || []);
  return {
    ok: (raw.rrMs?.length ?? 0) >= 2 && summary.rmssdMs != null,
    mac: raw.mac,
    name: raw.name,
    rrMs: raw.rrMs || [],
    ...summary,
  };
}

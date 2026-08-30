import { DeviceProvider } from './devices/DeviceProvider';
import { ambitDeviceProvider } from './devices/AmbitDeviceProvider';
import { writeGpxFile } from './GpxService';
import { extractGpxMetadata } from './GpxParser';
import { isActivitySynced, isActivityDeleted, markActivitySynced, getAllSyncedIds, getDeletedIds } from '../database/db';
import { isMarkSyncedEnabled } from './MarkSynced';
import { attributeMoveToGear } from './GearAutoAssign';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface SyncState {
  phase: 'idle' | 'connecting' | 'fetching' | 'writing' | 'done' | 'error';
  current: number;
  total: number;
  newCount: number;   // logs effectivement écrits (nouveaux)
  error?: string;
  deviceName?: string; // nom du modèle détecté, ex. "Suunto Ambit3 Peak" (connu après connect())
}

type SyncListener = (state: SyncState) => void;

// ─── SyncService ──────────────────────────────────────────────────────────────

/**
 * Orchestre une synchronisation complète :
 *   connect → getLogs → (pour chaque log nouveau) writeGpx → markSynced → disconnect
 *
 * @param onState  Callback appelé à chaque changement d'état
 * @param provider DeviceProvider à utiliser (par défaut : Suunto Ambit)
 * @returns        Nombre de nouveaux logs écrits
 */
export async function runSync(
  onState: SyncListener,
  provider: DeviceProvider = ambitDeviceProvider,
  opts: { forceRefresh?: boolean } = {},
): Promise<number> {
  // forceRefresh (André, 2026-08-30): re-read and OVERWRITE the GPX of activities already on the
  // phone — so a decode fix (e.g. the trekking "no GPS data" periodic-sample fix) reaches moves
  // that were synced before it. The watch is an immutable logbook that re-sends everything, so we
  // just skip the deleted blacklist instead of every already-synced id, and overwrite on write.
  // Deleted activities are still never resurrected (isActivityDeleted guard below).
  const refresh = !!opts.forceRefresh || !!provider.refreshExisting;
  const emit = (partial: Partial<SyncState> & { phase: SyncState['phase'] }) =>
    onState({ current: 0, total: 0, newCount: 0, ...partial });

  // ── 1. Connexion ────────────────────────────────────────────────────────────
  emit({ phase: 'connecting' });
  let deviceName: string | undefined;
  try {
    const info = await provider.connect();
    deviceName = info.name;
  } catch (e: any) {
    emit({ phase: 'error', error: e?.message ?? 'Connection failed' });
    return 0;
  }

  // ── 2. Récupération des logs ─────────────────────────────────────────────────
  emit({ phase: 'fetching', current: 0, total: 0, deviceName });

  // Charger les IDs déjà connus → passés au skip_callback natif pour éviter
  // de relire le payload complet des logs déjà synchronisés
  // On a forced refresh, tell the watch to re-send the kept activities (only the deleted
  // blacklist is "known"); otherwise skip everything already synced.
  const knownIds = opts.forceRefresh ? await getDeletedIds() : await getAllSyncedIds();

  let current = 0;
  let total = 0;
  const unsubscribe = provider.onSyncProgress(event => {
    current = event.current;
    total = event.total;
    onState({ phase: 'fetching', current, total, newCount: 0, deviceName });
  });

  let gpxLogs: string[];
  try {
    gpxLogs = await provider.getLogs(knownIds);
  } catch (e: any) {
    unsubscribe();
    emit({ phase: 'error', error: e?.message ?? 'Failed to read logs' });
    await provider.disconnect().catch(() => {});
    return 0;
  }
  unsubscribe();

  // ── 2b. Mark-synced write-back (experimental Settings toggle, OFF by default) ──
  // The moves in gpxLogs are exactly the ones read this session (the watch skipped the
  // already-known ones), so their indices match the native cache 0..gpxLogs.length-1. Tell
  // the watch they're synced so the Suunto app / SuuntoLink don't duplicate them. Best-effort
  // and non-fatal: a failure here must never lose the moves the user just read. Only the
  // Ambit providers implement markSyncedLogs, and it no-ops on unsupported watches.
  if (provider.markSyncedLogs && (await isMarkSyncedEnabled())) {
    try {
      await provider.markSyncedLogs(gpxLogs.length);
    } catch (e: any) {
      console.log('[mark-synced] write-back failed, moves still returned:', e?.message ?? e);
    }
  }

  // ── 3. Écriture des nouveaux logs ────────────────────────────────────────────
  emit({ phase: 'writing', current: 0, total: gpxLogs.length, newCount: 0, deviceName });
  let newCount = 0;

  for (let i = 0; i < gpxLogs.length; i++) {
    const gpxXml = gpxLogs[i];

    const meta = extractGpxMetadata(gpxXml);
    const id = meta.date
      ? meta.date.replace(/[^0-9T]/g, '').substring(0, 15) // "20240615T093000"
      : `log_${Date.now()}_${i}`;

    onState({ phase: 'writing', current: i + 1, total: gpxLogs.length, newCount, deviceName });

    // A live re-read device (Kailash) refreshes existing activities so a re-sync adopts a
    // better decode; an immutable logbook (Ambit3) skips ones already synced. Either way a
    // user-DELETED activity is never resurrected.
    if (await isActivityDeleted(id)) continue;
    if (!refresh && await isActivitySynced(id)) continue;

    const gpxPath = await writeGpxFile(id, gpxXml, refresh);
    if (!gpxPath) continue;

    await markActivitySynced({
      id,
      synced_at: Date.now(),
      gpx_path: gpxPath,
      date: meta.date,
      duration_s: meta.durationS,
      distance_m: meta.distanceM,
      d_plus: meta.dPlus,
      activity_type: meta.activityType,
    });
    // Attribute this move to its default gear in the local usage ledger (best-effort) so the app
    // tallies gear mileage itself — the step toward not needing intervals.icu (GearTotals).
    await attributeMoveToGear(id, meta.activityType, meta.distanceM, meta.durationS, meta.date);
    newCount++;
  }

  // ── 4. Déconnexion ───────────────────────────────────────────────────────────
  await provider.disconnect().catch(() => {});
  emit({ phase: 'done', current: gpxLogs.length, total: gpxLogs.length, newCount, deviceName });
  return newCount;
}

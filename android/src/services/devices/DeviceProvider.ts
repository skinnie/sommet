// ─── DeviceProvider ───────────────────────────────────────────────────────────
// Interface générique pour toute montre GPS connectée via USB OTG.
// Permet à SyncService et aux exports d'être indépendants du hardware.

import { SyncProgressEvent } from '../../native/AmbitUsbModule';

export interface DeviceInfo {
  name: string;
  vendorId: number;
  productId: number;
}

export interface DeviceProvider {
  /** Nom lisible du provider, ex. "Suunto Ambit" */
  readonly deviceName: string;

  /** Détecte la montre, demande la permission USB et initialise la connexion. */
  connect(): Promise<DeviceInfo>;

  /** Ferme la connexion proprement. */
  disconnect(): Promise<void>;

  /** Récupère tous les logs sous forme de strings GPX.
   *  knownIds : IDs déjà en DB (format YYYYMMDDTHHMMSS) — les logs correspondants sont skippés côté montre. */
  getLogs(knownIds?: string[]): Promise<string[]>;

  /** Native FIT (base64) of each move from the most recent getLogs(), aligned index-for-index
   *  with the GPX array it returned ("" for a move with no FIT). Optional — a provider that can't
   *  build FIT natively omits it and the caller falls back to a GPX→FIT conversion. Must be called
   *  right after getLogs() (reads the same native cache; does not re-read the watch). */
  getLogFits?(): Promise<string[]>;

  /** S'abonne aux événements de progression pendant getLogs(). Retourne une fonction de désinscription. */
  onSyncProgress(callback: (event: SyncProgressEvent) => void): () => void;

  /** Envoie le fichier SGEE (éphémérides GPS) à la montre. Optionnel selon le device. */
  updateSgee?(path: string): Promise<boolean>;

  /** Experimental "mark synced" write-back (opt-in, OFF by default). Marks the `count` moves
   *  just read this session synced on the watch so the Suunto app / SuuntoLink don't
   *  duplicate them. Optional — only watches with a known synced entry id implement it, and
   *  it no-ops on unsupported devices. Returns how many moves were marked. */
  markSyncedLogs?(count: number): Promise<number>;

  /** The watch's own daily step counts (Ambit3: its last 30 days), stored for the Health
   *  screen. Optional, best-effort, never throws; returns how many days were read. */
  syncDailySteps?(): Promise<number>;

  /** Sets the watch clock to the phone's local time, as the Suunto app does on every sync (so a
   *  daylight-saving change reaches the watch). Optional; resolves true once the watch took it. */
  setClock?(): Promise<boolean>;

  /** The connected watch's serial number: the name a move is saved under, the same one the
   *  desktop uses, so one move has one identity on every device. Optional; '' when unknown. */
  serial?(): Promise<string>;

  /** When true, a sync REFRESHES already-synced activities (overwrites their GPX + row)
   *  instead of skipping them. For a live re-read device like the Kailash, getLogs() rebuilds
   *  every activity from a fresh read of the TrackLog + DeviceHistory each time, so re-syncing
   *  should pick up a better decode (e.g. the session's real distance) rather than keep a stale
   *  one. Immutable per-move logbooks (Ambit3) leave this false. A user-DELETED activity is
   *  still never resurrected. */
  readonly refreshExisting?: boolean;
}

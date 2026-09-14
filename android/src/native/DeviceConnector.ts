/**
 * DeviceConnector — interface générique pour tout appareil GPS connecté.
 *
 * L'architecture est intentionnellement hardware-agnostic : chaque marque/modèle
 * fournit sa propre implémentation de cette interface. L'application ne connaît
 * que cette interface ; les couches données (GPX, SQLite, cloud…) sont
 * totalement indépendantes du hardware.
 *
 * Implémentations existantes :
 *   - AmbitUsbModule  → Suunto Ambit 1/2/3 via USB OTG + libambit (NDK)
 *
 * Implémentations futures possibles :
 *   - GarminBleModule  → Garmin via Bluetooth LE
 *   - PolarUsbModule   → Polar via USB
 *   - GpxFileImport    → import manuel de fichiers GPX (aucun hardware requis)
 */

/** Informations génériques sur l'appareil connecté. */
export interface DeviceInfo {
  name: string;       // ex: "Suunto Ambit 1 — Finch"
  vendorId?: number;  // USB VID (optionnel pour BLE)
  productId?: number; // USB PID (optionnel pour BLE)
}

/** Événement de progression émis pendant la synchronisation. */
export interface SyncProgressEvent {
  current: number;
  total: number;
}

/**
 * Interface que toute implémentation hardware doit respecter.
 */
export interface DeviceConnector {
  /** Détecte l'appareil, demande les permissions et ouvre la connexion. */
  connect(): Promise<DeviceInfo>;

  /** Ferme proprement la connexion. */
  disconnect(): Promise<void>;

  /**
   * Récupère les logs GPS sous forme de strings GPX.
   * @param knownIds  IDs déjà synchronisés à ignorer (format YYYYMMDDTHHMMSS)
   */
  getLogs(knownIds?: string[]): Promise<string[]>;

  /**
   * Native FIT (base64) of each move from the most recent getLogs(), aligned index-for-index
   * with the GPX array it returned ("" for a move that produced no FIT). Optional: a connector
   * that can't build FIT natively omits it, and the caller falls back to a GPX→FIT conversion.
   * Must be called right after getLogs() (reads the same native cache; does not re-read).
   */
  getLogFits?(): Promise<string[]>;

  /**
   * Met à jour les éphémérides GPS sur l'appareil (si supporté).
   * @param path  Chemin absolu du fichier SGEE
   * @returns     true si réussi, false si non supporté
   */
  updateSgee?(path: string): Promise<boolean>;

  /**
   * S'abonne aux événements de progression.
   * @returns  Fonction de désinscription
   */
  onSyncProgress(callback: (event: SyncProgressEvent) => void): () => void;
}

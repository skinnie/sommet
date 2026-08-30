// In-session cache of the raw 12 KB CustomModes (sport modes) region. Reading it over BLE is
// ~12 chunk round-trips (~5 s), and the screen read it on open, so an edit re-reading the SAME
// region before its write is wasted time. The screen's read seeds this cache; every writer reuses
// it as its "before" and updates it after a successful write; nothing on the watch changes this
// region except our own writes within a session, so the cache is always current. Cleared on
// disconnect / when the Sport Modes screen leaves, so the next open reads fresh. (André,
// 2026-08-30: "sport modes… something to do for a better experience".)

let cache: Uint8Array | null = null;

export function getCustomModesCache(): Uint8Array | null {
  return cache;
}

export function setCustomModesCache(bytes: Uint8Array | null): void {
  cache = bytes ? new Uint8Array(bytes) : null; // copy, so a later in-place edit can't corrupt it
}

export function clearCustomModesCache(): void {
  cache = null;
}

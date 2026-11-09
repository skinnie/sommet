import { NativeModules, NativeEventEmitter } from 'react-native';

const { AmbitBleModule: NativeAmbitBle } = NativeModules;
const emitter = new NativeEventEmitter(NativeAmbitBle);

if (!NativeAmbitBle) {
  throw new Error(
    'AmbitBleModule native module not found. ' +
    'Check that AmbitBlePackage is registered in MainApplication.kt ' +
    'and that the NDK build succeeded.'
  );
}

/**
 * EXPERIMENTAL — BLE connect for Sommet, v0.3.0. Ambit3/Traverse series,
 * plus the Kailash (added 2026-08-09 — driven internally as an Ambit3 Peak,
 * see AmbitBleModule.kt's COMPATIBLE_NAME_PREFIXES and KAILASH-BLE-FINDINGS.md
 * Finding 8; the Kailash has no Routes/POI feature at all though, see
 * KAILASH-SCOPING-NOTE.md — only Sync/Backup make sense for it here).
 * Real-hardware-tested as of 2026-08-09: pairing itself now works
 * (see HANDOFF.md Milestone 7's dated entries that day for the two real bugs
 * found and fixed — an unhandled PAIRING_VARIANT_PASSKEY, and a
 * BLUETOOTH_PRIVILEGED permission wall on setPairingConfirmation()).
 *
 * Deliberately thin: once connected, every other watch operation
 * (writeRoute, readRegion, addPoi, ...) is imported from AmbitUsbModule.ts
 * as usual and works unchanged — they operate on the same native g_device
 * regardless of which transport connected it. Only connection setup is
 * BLE-specific.
 */

/** Scans for an advertising Ambit3/Traverse/Kailash and connects. The watch's
 * "Sync now" action must have just been triggered — its advertising window
 * is short, so call this right after, not any earlier (see RouteScreen.tsx's
 * waitForSyncNowTap). Resolves once the watch's device info has been read
 * back and confirmed (see protocol_ble.c) — from here on, writeRoute()/
 * readRegion()/etc. from AmbitUsbModule.ts work
 * the same as over cable. */
/** Resolves with the connected watch's Bluetooth address (used by the switcher to highlight
 * which paired watch is active). */
export function scanAndConnect(): Promise<string> {
  return NativeAmbitBle.scanAndConnect();
}

/** A Suunto watch already bonded (paired) to this phone over Bluetooth. */
export interface BondedWatch {
  address: string; // Bluetooth MAC — the stable id used by scanAndConnectTo()
  name: string;    // the bonded device name, e.g. "Ambit3 Peak" / "Suunto Kailash"
}

/** Every paired Suunto watch, so the switcher can list BLE watches beside cabled USB ones.
 * Guarded so an older native build (without listBondedWatches) degrades to "no paired
 * watches" instead of throwing — a JS/native version skew must never crash the connect flow. */
export function listBondedWatches(): Promise<BondedWatch[]> {
  if (typeof NativeAmbitBle.listBondedWatches !== 'function') return Promise.resolve([]);
  return NativeAmbitBle.listBondedWatches();
}

/** Like scanAndConnect(), but pins the connection to the one bonded watch at `address`
 * (from listBondedWatches()) — for when more than one paired watch might be in range. Same
 * "trigger Sync now on the watch first" requirement as scanAndConnect(). Falls back to the
 * unpinned scan if the native method is missing (version skew). */
export function scanAndConnectTo(address: string): Promise<string> {
  if (typeof NativeAmbitBle.scanAndConnectTo !== 'function') return NativeAmbitBle.scanAndConnect();
  return NativeAmbitBle.scanAndConnectTo(address);
}

export function disconnectBle(): Promise<void> {
  return NativeAmbitBle.disconnectBle();
}

/** Keeps a background connect armed to this bonded watch (until disconnectBle()), as the
 * Suunto app does: whenever the watch calls - "Sync now", or its own schedule - it links by
 * itself and onBleReconnected fires once the session is up. Resolves false when it can't
 * (no permission, Bluetooth off, watch not bonded). */
export function armAutoReconnect(address: string): Promise<boolean> {
  if (typeof NativeAmbitBle.armAutoReconnect !== 'function') return Promise.resolve(false);
  return NativeAmbitBle.armAutoReconnect(address);
}

/** The watch connected by itself through armAutoReconnect and its session is up. */
export function onBleReconnected(callback: (event: { address: string }) => void): () => void {
  const sub = emitter.addListener('AmbitBleReconnected', callback);
  return () => sub.remove();
}

/** Fires when a live, already-handshaken BLE link drops on its own (the watch's own
 * behavior, not something JS asked for — see AmbitBleModule.kt's post-handshake
 * onConnectionStateChange branch). Real gap fixed 2026-08-21: without this, HomeScreen.tsx
 * had no way to learn a connection it thought was still up had actually gone, and kept
 * showing "Connected" indefinitely. Callers should reset their own "connected" state and
 * let the user reconnect (scanAndConnect/scanAndConnectTo) rather than polling. */
export function onBleDisconnected(
  callback: (event: { status: number; reason?: 'watch' | 'adapter_off' }) => void,
): () => void {
  const sub = emitter.addListener('AmbitBleDisconnected', callback);
  return () => sub.remove();
}

/** The watch asked to sync: "Sync now" on the watch while the Bluetooth link is already up.
 * Before 2026-10-24 Sommet never answered that request, so pressing Sync now on a linked
 * watch did nothing. */
export function onBleSyncRequested(callback: () => void): () => void {
  const sub = emitter.addListener('AmbitBleSyncRequested', callback);
  return () => sub.remove();
}

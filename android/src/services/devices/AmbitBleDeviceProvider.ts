// ─── AmbitBleDeviceProvider ───────────────────────────────────────────────────
// DeviceProvider for a Suunto Ambit3/Traverse/Kailash already connected over BLE.
//
// The native watch operations (getLogs, updateSgee, onSyncProgress) all act on
// the same shared native g_device regardless of transport, so they are reused
// verbatim from AmbitUsbModule. The ONLY difference from the USB provider is
// connect()/disconnect(): over BLE the connection is established separately by
// AmbitBleModule.scanAndConnect() (GATT server + handshake, see
// AmbitBleModule.kt / protocol_ble.c) BEFORE sync runs, so connect() here must
// NOT call AmbitUsbModule.connect() — that opens a USB device and fails with
// "no device, check cable" when there's only a BLE link. It's a no-op that
// returns the info already learned during the BLE handshake, and disconnect()
// leaves the BLE link up (the Home screen owns its lifecycle).

import * as AmbitUsbModule from '../../native/AmbitUsbModule';
import { DeviceProvider, DeviceInfo } from './DeviceProvider';
import { SyncProgressEvent } from '../../native/AmbitUsbModule';
import { markReadLogsSynced } from '../MarkSynced';

export class AmbitBleDeviceProvider implements DeviceProvider {
  readonly deviceName = 'Suunto Ambit (BLE)';

  // Already connected over BLE before sync starts — nothing to open here.
  // getDeviceInfo() reads from the shared native device populated by the
  // BLE handshake, so the sync UI still gets a real device name.
  async connect(): Promise<DeviceInfo> {
    try {
      const info = await AmbitUsbModule.getDeviceInfo();
      return { name: info.name || 'Suunto Ambit', vendorId: 0x1493, productId: 0 };
    } catch {
      return { name: 'Suunto Ambit', vendorId: 0x1493, productId: 0 };
    }
  }

  // Leave the BLE link up — HomeScreen manages connect/disconnect for BLE.
  async disconnect(): Promise<void> { /* no-op */ }

  getLogs(knownIds: string[] = []): Promise<string[]> {
    return AmbitUsbModule.getLogs(knownIds);
  }

  getLogFits(): Promise<string[]> {
    return AmbitUsbModule.getLogFits();
  }

  onSyncProgress(callback: (event: SyncProgressEvent) => void): () => void {
    return AmbitUsbModule.onSyncProgress(callback);
  }

  updateSgee(path: string): Promise<boolean> {
    return AmbitUsbModule.updateSgee(path);
  }

  // EXPERIMENTAL/unverified over BLE: the real Suunto app never wrote this flag on BLE (it
  // uses EventBoard events). Rides the same shared-g_device native path as USB by analogy;
  // markReadLogsSynced tags the logs so it's clear this is the unproven transport.
  markSyncedLogs(count: number): Promise<number> {
    return markReadLogsSynced(count, ' (BLE, unverified)');
  }
}

export const ambitBleDeviceProvider = new AmbitBleDeviceProvider();

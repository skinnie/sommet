//
//  AmbitBleModule.swift — iOS/iPadOS BLE connect for AmbitApp.
//
//  The iOS twin of android/.../ble/AmbitBleModule.kt. It exposes the SAME
//  React Native method surface (scanAndConnect / scanAndConnectTo /
//  listBondedWatches / disconnectBle + the "AmbitBleDisconnected" event), so
//  src/native/AmbitBleModule.ts and AmbitBleDeviceProvider.ts work unchanged.
//
//  KEY ARCHITECTURAL DIFFERENCE from Android (proven from the iOS PacketLogger
//  captures, 2026-08): the official Suunto/7R iOS app connects to the watch as
//  a BLE *central* (CBCentralManager) — the watch hosts the NSP service as a
//  peripheral. Android instead hosts the service itself and lets the watch
//  connect in. iOS's central role is the well-supported path and avoids the
//  custom-service overflow-advertising caveat. The NSP layer is unaffected: the
//  watch still drives the conversation (pushes 0x1201/0x0002, phone answers
//  0x0000), so the watch-driven handshake in protocol_ble.c applies unchanged.
//
//  Only connection setup is here. Once connected, every watch operation
//  (getLogs, writeRoute, readRegion, …) runs through the shared native g_device
//  via the AmbitCore bridge, exactly as over USB/JNI on Android.
//

import Foundation
import CoreBluetooth
import React

// NSP GATT profile (same 128-bit UUIDs the Android module documents). Roles as
// seen by an iOS central: we WRITE to c6339440 (client→server) and SUBSCRIBE to
// notifications on d0fd6b80 (server→client). Confirmed by the capture: writes on
// the watch's write char, notifications on d0fd6b80.
private let kNspServiceUUID = CBUUID(string: "98ae7120-e62e-11e3-badd-0002a5d5c51b")
private let kNspWriteCharUUID = CBUUID(string: "c6339440-e62e-11e3-a5b3-0002a5d5c51b")
private let kNspNotifyCharUUID = CBUUID(string: "d0fd6b80-e62e-11e3-a2e9-0002a5d5c51b")

private let kSuuntoVID: UInt16 = 0x1493
private let kCompatibleNamePrefixes = ["Ambit3", "Traverse", "Suunto NSP", "Suunto Kailash", "Kailash"]
private let kConnectTimeoutSec: TimeInterval = 25.0

// VID is always Suunto's; PID only picks the driver_support row (mirrors
// AmbitBleModule.kt guessProductId). The handshake learns the real model after.
private func guessProductId(_ name: String?) -> UInt16 {
  guard let n = name else { return 0x001c }
  if n.hasPrefix("Traverse Alpha") { return 0x002d }
  if n.hasPrefix("Traverse") { return 0x002b }
  return 0x001c // Ambit3 Sport / default Ambit3 driver
}

// C write thunk: the framing layer calls this (a @convention(c) function, so it
// carries no Swift context) with each outgoing GATT chunk. `ud` is the module
// instance passed as opaque userdata to libambit_new_from_ble_ios.
private func ambitBleWriteThunk(_ ud: UnsafeMutableRawPointer?,
                                _ data: UnsafePointer<UInt8>?,
                                _ len: Int) {
  guard let ud = ud, let data = data, len > 0 else { return }
  let module = Unmanaged<AmbitBleModule>.fromOpaque(ud).takeUnretainedValue()
  module.writeChunk(Data(bytes: data, count: len))
}

@objc(AmbitBleModule)
final class AmbitBleModule: RCTEventEmitter {

  // CoreBluetooth callbacks are delivered on this dedicated background queue.
  // CRITICAL: it must NOT be the main queue and must differ from the queue the
  // blocking handshake runs on — the handshake (libambit_new_from_ble_ios)
  // blocks waiting for notification frames that arrive on THIS queue, so the two
  // must be different threads or they deadlock. (Android relies on the same
  // split: binder thread delivers notifications while an executor thread blocks
  // in the handshake.)
  private let cbQueue = DispatchQueue(label: "com.ambitsyncmodern.ble.cb")
  private lazy var central: CBCentralManager = CBCentralManager(delegate: self, queue: cbQueue)

  private var peripheral: CBPeripheral?
  private var writeChar: CBCharacteristic?
  private var notifyChar: CBCharacteristic?
  private var device: OpaquePointer? // ambit_object_t*

  private var connectResolve: RCTPromiseResolveBlock?
  private var connectReject: RCTPromiseRejectBlock?
  private var pinnedAddress: String?     // scanAndConnectTo target (peripheral.identifier UUID string)
  private var handshakeStarted = false
  private var hasListeners = false

  // MARK: RCTEventEmitter

  override static func requiresMainQueueSetup() -> Bool { false }
  override func supportedEvents() -> [String]! { ["AmbitBleDisconnected"] }
  override func startObserving() { hasListeners = true }
  override func stopObserving() { hasListeners = false }

  // MARK: Exported methods (match AmbitBleModule.kt / AmbitBleModule.ts)

  @objc(scanAndConnect:rejecter:)
  func scanAndConnect(_ resolve: @escaping RCTPromiseResolveBlock,
                      rejecter reject: @escaping RCTPromiseRejectBlock) {
    beginScan(address: nil, resolve: resolve, reject: reject)
  }

  @objc(scanAndConnectTo:resolver:rejecter:)
  func scanAndConnectTo(_ address: String,
                        resolver resolve: @escaping RCTPromiseResolveBlock,
                        rejecter reject: @escaping RCTPromiseRejectBlock) {
    beginScan(address: address, resolve: resolve, reject: reject)
  }

  // iOS gives no access to the system's bonded-device list, and CoreBluetooth
  // pairing is implicit, so there is no direct equivalent of Android's
  // getBondedDevices(). Return [] — the JS wrapper already treats an empty list
  // as "no paired watches" and falls back to the unpinned scan.
  @objc(listBondedWatches:rejecter:)
  func listBondedWatches(_ resolve: @escaping RCTPromiseResolveBlock,
                         rejecter reject: @escaping RCTPromiseRejectBlock) {
    resolve([])
  }

  @objc(disconnectBle:rejecter:)
  func disconnectBle(_ resolve: @escaping RCTPromiseResolveBlock,
                     rejecter reject: @escaping RCTPromiseRejectBlock) {
    cbQueue.async {
      self.teardown(emitStatus: nil)
      resolve(true)
    }
  }

  // MARK: Scan / connect flow

  private func beginScan(address: String?,
                         resolve: @escaping RCTPromiseResolveBlock,
                         reject: @escaping RCTPromiseRejectBlock) {
    cbQueue.async {
      if self.connectResolve != nil {
        reject("BUSY", "A BLE connect is already in progress", nil)
        return
      }
      self.connectResolve = resolve
      self.connectReject = reject
      self.pinnedAddress = address
      self.handshakeStarted = false

      // Arm the pre-init RX stash BEFORE the watch can write, so its opening
      // frame is parked and replayed rather than dropped.
      ambit_ios_ble_reset_rx_stash()

      switch self.central.state {
      case .poweredOn:
        self.startScan()
      case .unknown, .resetting:
        // centralManagerDidUpdateState will kick the scan once powered on.
        break
      default:
        self.failConnect("BLE_UNAVAILABLE", "Bluetooth is off or unavailable")
      }

      // Overall timeout guard.
      self.cbQueue.asyncAfter(deadline: .now() + kConnectTimeoutSec) {
        if self.connectResolve != nil && !self.handshakeStarted {
          self.failConnect("TIMEOUT", "Timed out waiting for the watch. Tap ‘Sync now’ on the watch, then retry.")
        }
      }
    }
  }

  private func startScan() {
    // Scan with services: nil and filter in didDiscover — the watch advertises
    // the NSP UUID as a *solicited* service (CBAdvertisementDataSolicitedServiceUUIDsKey),
    // which scanForPeripherals(withServices:) does not match. Name-prefix +
    // solicited/advertised UUID check catches it either way (same nuance the
    // Kotlin module handles for Android's ScanFilter).
    central.scanForPeripherals(withServices: nil,
                               options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
  }

  private func failConnect(_ code: String, _ message: String) {
    let reject = connectReject
    connectResolve = nil
    connectReject = nil
    if central.isScanning { central.stopScan() }
    reject?(code, message, nil)
  }

  // MARK: Outgoing write (called from the C write thunk, possibly off cbQueue)

  func writeChunk(_ data: Data) {
    cbQueue.async {
      guard let p = self.peripheral, let wc = self.writeChar else { return }
      p.writeValue(data, for: wc, type: .withoutResponse)
    }
  }

  // MARK: Teardown

  private func teardown(emitStatus status: Int?) {
    if central.isScanning { central.stopScan() }
    if let p = peripheral {
      if let nc = notifyChar { p.setNotifyValue(false, for: nc) }
      central.cancelPeripheralConnection(p)
    }
    if let d = device {
      ambit_ios_ble_set_active_object(nil)
      libambit_close(d)
      device = nil
    }
    peripheral = nil
    writeChar = nil
    notifyChar = nil
    if let s = status, hasListeners {
      sendEvent(withName: "AmbitBleDisconnected", body: ["status": s])
    }
  }
}

// MARK: - CBCentralManagerDelegate

extension AmbitBleModule: CBCentralManagerDelegate {
  func centralManagerDidUpdateState(_ central: CBCentralManager) {
    if central.state == .poweredOn, connectResolve != nil, peripheral == nil {
      startScan()
    }
  }

  func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral,
                      advertisementData: [String: Any], rssi RSSI: NSNumber) {
    // Pinned to one watch?
    if let pin = pinnedAddress, peripheral.identifier.uuidString != pin { return }

    let name = peripheral.name ?? (advertisementData[CBAdvertisementDataLocalNameKey] as? String)
    let advertisedUUIDs = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []
    let solicitedUUIDs = advertisementData[CBAdvertisementDataSolicitedServiceUUIDsKey] as? [CBUUID] ?? []
    let hasNsp = advertisedUUIDs.contains(kNspServiceUUID) || solicitedUUIDs.contains(kNspServiceUUID)
    let nameMatches = name.map { n in kCompatibleNamePrefixes.contains { n.hasPrefix($0) } } ?? false

    guard hasNsp || nameMatches else { return }

    central.stopScan()
    self.peripheral = peripheral
    peripheral.delegate = self
    central.connect(peripheral, options: nil)
  }

  func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
    peripheral.discoverServices([kNspServiceUUID])
  }

  func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral,
                      error: Error?) {
    failConnect("CONNECT_FAILED", error?.localizedDescription ?? "Failed to connect to the watch")
  }

  func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral,
                      error: Error?) {
    let wasConnecting = connectResolve != nil && !handshakeStarted
    if wasConnecting {
      failConnect("DISCONNECTED", error?.localizedDescription ?? "The watch disconnected during setup")
    } else {
      // A live, already-handshaken link dropped on its own — mirror Android's
      // post-handshake onConnectionStateChange → AmbitBleDisconnected event.
      teardown(emitStatus: (error as NSError?)?.code ?? 0)
    }
  }
}

// MARK: - CBPeripheralDelegate

extension AmbitBleModule: CBPeripheralDelegate {
  func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
    guard error == nil, let svc = peripheral.services?.first(where: { $0.uuid == kNspServiceUUID }) else {
      failConnect("NO_NSP_SERVICE", "Watch does not expose the Suunto NSP service")
      return
    }
    peripheral.discoverCharacteristics([kNspWriteCharUUID, kNspNotifyCharUUID], for: svc)
  }

  func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService,
                  error: Error?) {
    guard error == nil else {
      failConnect("DISCOVER_FAILED", error!.localizedDescription); return
    }
    for c in service.characteristics ?? [] {
      if c.uuid == kNspWriteCharUUID { writeChar = c }
      if c.uuid == kNspNotifyCharUUID { notifyChar = c }
    }
    guard let nc = notifyChar, writeChar != nil else {
      failConnect("NO_NSP_CHARS", "NSP characteristics not found on the watch"); return
    }
    // Subscribing to the notify CCCD is the "transport is live" signal. The
    // handshake starts only once the watch confirms the subscription.
    peripheral.setNotifyValue(true, for: nc)
  }

  func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic,
                  error: Error?) {
    guard error == nil, characteristic.uuid == kNspNotifyCharUUID, characteristic.isNotifying,
          !handshakeStarted else { return }
    handshakeStarted = true

    // Run the BLOCKING handshake off cbQueue (notifications arrive on cbQueue).
    let selfPtr = Unmanaged.passUnretained(self).toOpaque()
    let vid = kSuuntoVID
    let pid = guessProductId(peripheral.name)
    let addr = peripheral.identifier.uuidString
    DispatchQueue.global(qos: .userInitiated).async {
      let obj = libambit_new_from_ble_ios(ambitBleWriteThunk, selfPtr, vid, pid)
      self.cbQueue.async {
        if let obj = obj {
          self.device = obj
          let resolve = self.connectResolve
          self.connectResolve = nil
          self.connectReject = nil
          resolve?(addr)
        } else {
          self.failConnect("HANDSHAKE_FAILED", "The watch did not complete the NSP handshake")
          self.teardown(emitStatus: nil)
        }
      }
    }
  }

  func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic,
                  error: Error?) {
    guard error == nil, characteristic.uuid == kNspNotifyCharUUID,
          let value = characteristic.value, !value.isEmpty else { return }
    // Feed the raw GATT notification bytes into the framing layer (watch → phone).
    value.withUnsafeBytes { raw in
      if let base = raw.bindMemory(to: UInt8.self).baseAddress {
        ambit_ios_ble_on_notify(base, value.count)
      }
    }
  }
}

//
//  HrStrapModule.swift — read R-R from a standard BLE heart-rate strap for morning HRV.
//
//  iOS twin of the Android HrStrapModule.kt and the desktop tools/hrv_strap.py. A SEPARATE
//  standard-GATT peripheral (COOSPO HW9 optical armband, or any strap that reports R-R) — NOT
//  the watch: this is a normal CoreBluetooth *central* (unlike AmbitBleModule, whose watch link
//  is an inverted GATT server). Scan Heart Rate service 0x180D → connect → subscribe Heart Rate
//  Measurement 0x2A37 → collect R-R for `seconds` → resolve { mac, name, rrMs: [Int] }.
//  The HRV math (RMSSD etc.) is the shared TS in src/services/hrv.ts, identical to the desktop.
//
import Foundation
import CoreBluetooth
import React

private let HR_SERVICE = CBUUID(string: "180D")
private let HR_MEAS = CBUUID(string: "2A37")

@objc(HrStrap)
class HrStrapModule: NSObject, CBCentralManagerDelegate, CBPeripheralDelegate {

  private var central: CBCentralManager!
  private let bleQueue = DispatchQueue(label: "com.ambitsyncmodern.hrstrap")
  private var peripheral: CBPeripheral?
  private var resolve: RCTPromiseResolveBlock?
  private var reject: RCTPromiseRejectBlock?
  private var seconds: Int = 120
  private var nameFilter: String?
  private var rrMs: [Int] = []
  private var devName: String?
  private var finished = false
  private var wantScan = false

  override init() {
    super.init()
    central = CBCentralManager(delegate: self, queue: bleQueue)
  }

  @objc static func requiresMainQueueSetup() -> Bool { false }

  // measure(seconds, nameFilter): collect R-R for `seconds`, matching a name substring, or any
  // HR-service peripheral when nameFilter is null/empty.
  @objc(measure:nameFilter:resolver:rejecter:)
  func measure(_ seconds: NSNumber,
               nameFilter: NSString?,
               resolver: @escaping RCTPromiseResolveBlock,
               rejecter: @escaping RCTPromiseRejectBlock) {
    bleQueue.async {
      if self.resolve != nil { rejecter("BUSY", "A measurement is already running", nil); return }
      let s = seconds.intValue
      self.seconds = (s >= 20 && s <= 600) ? s : 120
      let f = nameFilter as String?
      self.nameFilter = (f?.isEmpty == false) ? f : nil
      self.rrMs = []; self.devName = nil; self.peripheral = nil; self.finished = false
      self.resolve = resolver; self.reject = rejecter
      if self.central.state == .poweredOn { self.startScan() } else { self.wantScan = true }
    }
  }

  private func startScan() {
    wantScan = false
    central.scanForPeripherals(withServices: nil,
                               options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])
    // Scan timeout: if nothing matched, fail cleanly.
    bleQueue.asyncAfter(deadline: .now() + 15) { [weak self] in
      guard let self = self, self.peripheral == nil, !self.finished else { return }
      self.central.stopScan()
      self.failOnce("NOT_FOUND", "No heart-rate strap found - turn it on / wear it and retry")
    }
  }

  func centralManagerDidUpdateState(_ c: CBCentralManager) {
    if c.state == .poweredOn && wantScan { startScan() }
    else if c.state != .poweredOn && wantScan { failOnce("BLUETOOTH_OFF", "Bluetooth is off") }
  }

  func centralManager(_ c: CBCentralManager, didDiscover p: CBPeripheral,
                      advertisementData: [String: Any], rssi RSSI: NSNumber) {
    let advName = (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? p.name
    let advUuids = (advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID]) ?? []
    let advertisesHr = advUuids.contains(HR_SERVICE)
    if let filter = nameFilter {
      guard let n = advName, n.range(of: filter, options: .caseInsensitive) != nil else { return }
    } else {
      guard advertisesHr else { return }
    }
    devName = advName
    central.stopScan()
    peripheral = p
    p.delegate = self
    central.connect(p, options: nil)
  }

  func centralManager(_ c: CBCentralManager, didConnect p: CBPeripheral) {
    p.discoverServices([HR_SERVICE])
  }

  func centralManager(_ c: CBCentralManager, didFailToConnect p: CBPeripheral, error: Error?) {
    failOnce("CONNECT_FAILED", error?.localizedDescription ?? "connect failed")
  }

  func centralManager(_ c: CBCentralManager, didDisconnectPeripheral p: CBPeripheral, error: Error?) {
    // Dropped mid-capture: resolve with whatever we have.
    if !finished { finishOnce() }
  }

  func peripheral(_ p: CBPeripheral, didDiscoverServices error: Error?) {
    guard let svc = p.services?.first(where: { $0.uuid == HR_SERVICE }) else {
      failOnce("NO_HR", "Strap has no Heart Rate service"); return
    }
    p.discoverCharacteristics([HR_MEAS], for: svc)
  }

  func peripheral(_ p: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
    guard let ch = service.characteristics?.first(where: { $0.uuid == HR_MEAS }) else {
      failOnce("NO_HR", "Strap has no Heart Rate Measurement characteristic"); return
    }
    p.setNotifyValue(true, for: ch)
    // Collect for the requested window, then resolve.
    bleQueue.asyncAfter(deadline: .now() + .seconds(seconds)) { [weak self] in
      guard let self = self, !self.finished else { return }
      self.finishOnce()
    }
  }

  func peripheral(_ p: CBPeripheral, didUpdateValueFor ch: CBCharacteristic, error: Error?) {
    guard ch.uuid == HR_MEAS, let data = ch.value else { return }
    parseRr([UInt8](data))
  }

  // R-R (ms) from one 0x2A37 notification: flags bit0 HR format, bit3 energy, bit4 RR present;
  // RR are uint16 LE in 1/1024 s.
  private func parseRr(_ v: [UInt8]) {
    if v.isEmpty { return }
    let flags = Int(v[0])
    var i = 1
    i += (flags & 0x01) != 0 ? 2 : 1
    if (flags & 0x08) != 0 { i += 2 }
    if (flags & 0x10) != 0 {
      while i + 1 < v.count {
        let raw = Int(v[i]) | (Int(v[i + 1]) << 8)
        rrMs.append(Int((Double(raw) * 1000.0 / 1024.0).rounded()))
        i += 2
      }
    }
  }

  private func finishOnce() {
    if finished { return }
    finished = true
    if let p = peripheral { central.cancelPeripheralConnection(p) }
    central.stopScan()
    var out: [String: Any] = ["mac": peripheral?.identifier.uuidString ?? "", "rrMs": rrMs]
    if let n = devName { out["name"] = n }
    resolve?(out)
    resolve = nil; reject = nil
  }

  private func failOnce(_ code: String, _ message: String) {
    if finished { return }
    finished = true
    if let p = peripheral { central.cancelPeripheralConnection(p) }
    central.stopScan()
    reject?(code, message, nil)
    resolve = nil; reject = nil
  }
}

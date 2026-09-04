package com.ambitsyncmodern.hrstrap

import android.Manifest
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.util.Log
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.*
import com.facebook.react.modules.core.PermissionAwareActivity
import com.facebook.react.modules.core.PermissionListener
import java.util.UUID

/*
 * HR-strap R-R reader for morning HRV (COOSPO HW9 optical armband, or any strap that reports
 * R-R). SEPARATE standard-GATT peripheral, nothing to do with the watch: scan Heart Rate service
 * 0x180D -> connect -> enable Heart Rate Measurement 0x2A37 notifications via the 0x2902 CCCD ->
 * collect R-R for `seconds` -> resolve { mac, name, rrMs: [...] }. The HRV math (RMSSD etc.) is
 * done in TS (src/services/hrv.ts) so it stays identical to the desktop's tools/hrv.py.
 *
 * Modeled on AmbitSmartSensorModule (same scan/permission/GATT scaffolding); the difference is we
 * collect a timed R-R STREAM (many notifications) rather than one HR sample, and we extract the
 * RR-Interval field, not the HR. Read-only - cannot brick anything.
 */
class HrStrapModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName() = "HrStrap"

    private val mainHandler = Handler(Looper.getMainLooper())
    private var scanCallback: ScanCallback? = null
    private var gatt: BluetoothGatt? = null
    private var pendingPromise: Promise? = null
    private var pendingPermissionPromise: Promise? = null
    private var resolved = false
    private var seconds = 120
    private var nameFilter: String? = null
    private var deviceName: String? = null
    private var deviceAddr: String? = null
    private val rrMs = ArrayList<Int>()

    companion object {
        private const val TAG = "HrStrap"
        private const val SCAN_TIMEOUT_MS = 15_000L
        private const val PERMISSION_REQUEST_CODE = 4246
        private val HR_SERVICE = uuid16("180D")
        private val HR_MEASUREMENT = uuid16("2A37")
        private val CCCD = uuid16("2902")
        private fun uuid16(short: String): UUID =
            UUID.fromString("0000$short-0000-1000-8000-00805f9b34fb")
    }

    // measure(seconds, nameFilter): collect R-R for `seconds`, matching a name substring (or any
    // HR-service peripheral when nameFilter is null/empty).
    @ReactMethod
    fun measure(seconds: Int, nameFilter: String?, promise: Promise) {
        if (pendingPromise != null) { promise.reject("BUSY", "A measurement is already running"); return }
        this.seconds = if (seconds in 20..600) seconds else 120
        this.nameFilter = nameFilter?.takeIf { it.isNotBlank() }
        if (!hasPermissions()) { pendingPermissionPromise = promise; requestPermissions(); return }
        startScan(promise)
    }

    private fun startScan(promise: Promise) {
        val adapter = (reactContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
        if (adapter == null || !adapter.isEnabled) { promise.reject("BLUETOOTH_OFF", "Bluetooth is off or unavailable"); return }
        val scanner = adapter.bluetoothLeScanner
            ?: run { promise.reject("BLE_UNAVAILABLE", "BLE scanning unavailable"); return }

        pendingPromise = promise
        resolved = false
        rrMs.clear(); deviceName = null; deviceAddr = null

        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        val timeout = Runnable {
            try { scanner.stopScan(scanCallback) } catch (_: SecurityException) {}
            rejectOnce("NOT_FOUND", "No heart-rate strap found - turn it on / wear it and retry")
        }
        mainHandler.postDelayed(timeout, SCAN_TIMEOUT_MS)

        try {
            scanner.startScan(null, settings, object : ScanCallback() {
                override fun onScanResult(callbackType: Int, result: ScanResult) {
                    val record = result.scanRecord
                    val advertisesHr = record?.serviceUuids?.any { it.uuid == HR_SERVICE } == true
                    val name = result.device.name ?: record?.deviceName
                    val filter = nameFilter
                    val nameMatches = filter != null && name != null && name.contains(filter, ignoreCase = true)
                    // Match: a name substring if given, else any peripheral advertising the HR service.
                    if (filter != null) { if (!nameMatches) return } else if (!advertisesHr) return
                    Log.d(TAG, "match: name=$name addr=${result.device.address}")
                    deviceName = name; deviceAddr = result.device.address
                    mainHandler.removeCallbacks(timeout)
                    try { scanner.stopScan(this) } catch (_: SecurityException) {}
                    connect(result.device)
                }
                override fun onScanFailed(errorCode: Int) {
                    mainHandler.removeCallbacks(timeout)
                    rejectOnce("SCAN_FAILED", "BLE scan failed, code=$errorCode")
                }
            }.also { scanCallback = it })
        } catch (e: SecurityException) {
            mainHandler.removeCallbacks(timeout)
            rejectOnce("PERMISSION_DENIED", e.message ?: "Bluetooth permission denied")
        }
    }

    private fun connect(device: BluetoothDevice) {
        try { gatt = device.connectGatt(reactContext, false, gattCallback) }
        catch (e: SecurityException) { rejectOnce("PERMISSION_DENIED", e.message ?: "connect denied") }
    }

    private val gattCallback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                try { g.discoverServices() } catch (_: SecurityException) {}
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                // Dropped mid-capture: resolve with whatever R-R we collected.
                if (!resolved) finishSuccess()
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            val hr = g.getService(HR_SERVICE)?.getCharacteristic(HR_MEASUREMENT)
            if (hr == null) { rejectOnce("NO_HR", "Strap has no Heart Rate Measurement characteristic"); return }
            try {
                g.setCharacteristicNotification(hr, true)
                hr.getDescriptor(CCCD)?.let {
                    it.value = BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE
                    g.writeDescriptor(it)
                }
                // Collect for the requested window, then resolve.
                mainHandler.postDelayed({ if (!resolved) finishSuccess() }, seconds * 1000L)
            } catch (_: SecurityException) { rejectOnce("PERMISSION_DENIED", "notify enable denied") }
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, ch: BluetoothGattCharacteristic) {
            if (ch.uuid == HR_MEASUREMENT) parseRr(ch.value)
        }
    }

    // Extract R-R intervals (ms) from one 0x2A37 notification. flags bit0 HR format, bit3 energy,
    // bit4 RR present; RR are uint16 LE in 1/1024 s.
    private fun parseRr(value: ByteArray?) {
        if (value == null || value.isEmpty()) return
        val flags = value[0].toInt()
        var i = 1
        i += if (flags and 0x01 != 0) 2 else 1
        if (flags and 0x08 != 0) i += 2
        if (flags and 0x10 != 0) {
            while (i + 1 < value.size) {
                val raw = (value[i].toInt() and 0xFF) or ((value[i + 1].toInt() and 0xFF) shl 8)
                rrMs.add(Math.round(raw * 1000.0 / 1024.0).toInt())
                i += 2
            }
        }
    }

    private fun finishSuccess() {
        if (resolved) return
        resolved = true
        closeGatt()
        val arr = Arguments.createArray().apply { rrMs.forEach { pushInt(it) } }
        val map = Arguments.createMap().apply {
            putString("mac", deviceAddr ?: "")
            deviceName?.let { putString("name", it) }
            putArray("rrMs", arr)
        }
        pendingPromise?.resolve(map)
        pendingPromise = null
    }

    private fun rejectOnce(code: String, message: String?) {
        if (resolved) return
        resolved = true
        closeGatt()
        pendingPromise?.reject(code, message ?: "Error")
        pendingPromise = null
    }

    private fun closeGatt() {
        try { gatt?.disconnect(); gatt?.close() } catch (_: SecurityException) {}
        gatt = null
    }

    private fun hasPermissions(): Boolean {
        val perms = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        return perms.all { ContextCompat.checkSelfPermission(reactContext, it) == PackageManager.PERMISSION_GRANTED }
    }

    private fun requestPermissions() {
        val activity = reactContext.currentActivity as? PermissionAwareActivity
        if (activity == null) {
            pendingPermissionPromise?.let { pendingPermissionPromise = null; it.reject("NO_ACTIVITY", "No active activity") }
            return
        }
        val perms = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        activity.requestPermissions(perms, PERMISSION_REQUEST_CODE, object : PermissionListener {
            override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, results: IntArray): Boolean {
                if (requestCode != PERMISSION_REQUEST_CODE) return false
                val granted = results.isNotEmpty() && results.all { it == PackageManager.PERMISSION_GRANTED }
                val promise = pendingPermissionPromise
                pendingPermissionPromise = null
                if (granted && promise != null) startScan(promise)
                else promise?.reject("PERMISSION_DENIED", "Bluetooth permission was not granted")
                return true
            }
        })
    }
}

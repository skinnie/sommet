package com.ambitsyncmodern.magene

import android.Manifest
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGatt
import android.bluetooth.BluetoothGattCallback
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
import android.bluetooth.BluetoothStatusCodes
import android.bluetooth.le.ScanCallback
import android.bluetooth.le.ScanFilter
import android.bluetooth.le.ScanResult
import android.bluetooth.le.ScanSettings
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.ParcelUuid
import android.util.Base64
import android.util.Log
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.*
import com.facebook.react.modules.core.DeviceEventManagerModule
import com.facebook.react.modules.core.PermissionAwareActivity
import com.facebook.react.modules.core.PermissionListener
import java.util.UUID

/*
 * Magene C406 (Pro) BLE transport - the Android twin of the bleak calls in the desktop's
 * tools/magene_import.py / magene_device.py / magene_route.py / magene_workout.py.
 *
 * Deliberately THIN: scan, connect (+ Just-Works bond - the C406 stays on its "please pair"
 * screen until a companion bonds, see magene_import._connect), MTU, write CC02/CC03, read a
 * standard characteristic, and CC02/CC03 notifications forwarded to JS as "MageneNotify"
 * events. Every Magene command, the ride-download reassembly, the route/workout encoders and
 * the credit-paced file transfer live in TypeScript (src/services/Magene*.ts), line-for-line
 * ports of the Python tools, so the protocol has one shape on both platforms.
 *
 * One GATT op at a time: the JS side awaits each call (MageneBle.ts serializes them), and a
 * second op while one is pending is rejected BUSY rather than silently dropped by Android.
 */
class MageneBleModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName() = "MageneBle"

    companion object {
        private const val TAG = "MageneBle"
        private const val PERMISSION_REQUEST_CODE = 4247
        private const val OP_TIMEOUT_MS = 15_000L
        private const val CONNECT_TIMEOUT_MS = 30_000L
        private const val BOND_TIMEOUT_MS = 30_000L
        val SERVICE: UUID = UUID.fromString("8ce5cc01-0a4d-11e9-ab14-d663bd873d93")
        val CC02: UUID = UUID.fromString("8ce5cc02-0a4d-11e9-ab14-d663bd873d93")
        val CC03: UUID = UUID.fromString("8ce5cc03-0a4d-11e9-ab14-d663bd873d93")
        val CCCD: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")
    }

    private val mainHandler = Handler(Looper.getMainLooper())
    private var gatt: BluetoothGatt? = null
    private var mtu = 23

    // The single in-flight op (connect steps, write, read) and its timeout.
    private var pending: Promise? = null
    private var pendingTimeout: Runnable? = null
    private var pendingPermission: (() -> Unit)? = null
    private var pendingPermissionPromise: Promise? = null

    // Connect state machine: connected -> (bond) -> discover -> MTU -> CCCD cc02 -> CCCD cc03.
    private var connectPromise: Promise? = null
    private var bondReceiver: BroadcastReceiver? = null

    // ---- permissions --------------------------------------------------------------------------
    private fun perms() = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
        arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
    else arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)

    private fun hasPermissions() = perms().all {
        ContextCompat.checkSelfPermission(reactContext, it) == PackageManager.PERMISSION_GRANTED
    }

    private fun withPermissions(promise: Promise, then: () -> Unit) {
        if (hasPermissions()) { then(); return }
        val activity = reactContext.currentActivity as? PermissionAwareActivity
            ?: run { promise.reject("NO_ACTIVITY", "No active activity"); return }
        pendingPermission = then
        pendingPermissionPromise = promise
        activity.requestPermissions(perms(), PERMISSION_REQUEST_CODE, object : PermissionListener {
            override fun onRequestPermissionsResult(code: Int, p: Array<String>, results: IntArray): Boolean {
                if (code != PERMISSION_REQUEST_CODE) return false
                val ok = results.isNotEmpty() && results.all { it == PackageManager.PERMISSION_GRANTED }
                val cont = pendingPermission; val pr = pendingPermissionPromise
                pendingPermission = null; pendingPermissionPromise = null
                if (ok) cont?.invoke() else pr?.reject("PERMISSION_DENIED", "Bluetooth permission was not granted")
                return true
            }
        })
    }

    private fun adapter() =
        (reactContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter

    // ---- scan -----------------------------------------------------------------------------
    // scan(ms) -> [{address, name, rssi}] for every peripheral advertising the C406 service
    // (same filter as magene_import._scan).
    @ReactMethod
    fun scan(timeoutMs: Int, promise: Promise) = withPermissions(promise) {
        val ad = adapter()
        if (ad == null || !ad.isEnabled) { promise.reject("BLUETOOTH_OFF", "Bluetooth is off"); return@withPermissions }
        val scanner = ad.bluetoothLeScanner
            ?: run { promise.reject("BLE_UNAVAILABLE", "BLE scanning unavailable"); return@withPermissions }
        val found = LinkedHashMap<String, WritableMap>()
        val cb = object : ScanCallback() {
            override fun onScanResult(type: Int, r: ScanResult) {
                val m = Arguments.createMap()
                m.putString("address", r.device.address)
                m.putString("name", r.scanRecord?.deviceName ?: safeName(r.device) ?: "")
                m.putInt("rssi", r.rssi)
                found[r.device.address] = m
            }
            override fun onScanFailed(errorCode: Int) { Log.w(TAG, "scan failed $errorCode") }
        }
        val filter = ScanFilter.Builder().setServiceUuid(ParcelUuid(SERVICE)).build()
        val settings = ScanSettings.Builder().setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY).build()
        try { scanner.startScan(listOf(filter), settings, cb) }
        catch (e: SecurityException) { promise.reject("PERMISSION_DENIED", e.message); return@withPermissions }
        mainHandler.postDelayed({
            try { scanner.stopScan(cb) } catch (_: SecurityException) {}
            val arr = Arguments.createArray()
            found.values.forEach { arr.pushMap(it) }
            promise.resolve(arr)
        }, timeoutMs.coerceIn(2000, 30000).toLong())
    }

    private fun safeName(d: BluetoothDevice): String? = try { d.name } catch (_: SecurityException) { null }

    // ---- connect ----------------------------------------------------------------------------
    // connect(address) -> {mtu, bonded}. Bonds first when the device isn't bonded yet.
    @ReactMethod
    fun connect(address: String, promise: Promise) = withPermissions(promise) {
        if (connectPromise != null || pending != null) { promise.reject("BUSY", "Magene operation in progress"); return@withPermissions }
        val ad = adapter() ?: run { promise.reject("BLUETOOTH_OFF", "Bluetooth is off"); return@withPermissions }
        closeGatt()
        connectPromise = promise
        mtu = 23
        armTimeout(CONNECT_TIMEOUT_MS) { failConnect("TIMEOUT", "The Magene didn't answer - wake it and keep it close") }
        try {
            val dev = ad.getRemoteDevice(address)
            gatt = dev.connectGatt(reactContext, false, callback, BluetoothDevice.TRANSPORT_LE)
        } catch (e: Exception) { failConnect("CONNECT_FAILED", e.message) }
    }

    private fun failConnect(code: String, msg: String?) {
        clearTimeout()
        unregisterBond()
        val p = connectPromise; connectPromise = null
        closeGatt()
        p?.reject(code, msg ?: code)
    }

    private fun afterBond(g: BluetoothGatt) {
        if (connectPromise == null) return
        // The bond wait reused the timer; re-arm the connect timeout for the remaining steps.
        armTimeout(CONNECT_TIMEOUT_MS) { failConnect("TIMEOUT", "The Magene didn't finish connecting") }
        try { if (!g.discoverServices()) failConnect("DISCOVER_FAILED", "Service discovery failed") }
        catch (e: SecurityException) { failConnect("PERMISSION_DENIED", e.message) }
    }

    private fun ensureBond(g: BluetoothGatt) {
        val dev = g.device
        val state = try { dev.bondState } catch (_: SecurityException) { BluetoothDevice.BOND_NONE }
        if (state == BluetoothDevice.BOND_BONDED) { afterBond(g); return }
        // Just-Works bond; carry on either way when it settles (like the desktop's best-effort
        // pair) - an unbonded link still reads rides, the device just keeps its pair screen.
        val rcv = object : BroadcastReceiver() {
            override fun onReceive(c: Context, i: Intent) {
                val d: BluetoothDevice? = if (Build.VERSION.SDK_INT >= 33)
                    i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
                else @Suppress("DEPRECATION") i.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                if (d?.address != dev.address) return
                val s = i.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE, BluetoothDevice.BOND_NONE)
                if (s == BluetoothDevice.BOND_BONDED || s == BluetoothDevice.BOND_NONE) {
                    unregisterBond()
                    mainHandler.postDelayed({ gatt?.let { afterBond(it) } }, 500)
                }
            }
        }
        bondReceiver = rcv
        ContextCompat.registerReceiver(reactContext, rcv,
            IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED), ContextCompat.RECEIVER_EXPORTED)
        armTimeout(BOND_TIMEOUT_MS) { unregisterBond(); gatt?.let { afterBond(it) } }
        try { if (!dev.createBond()) { unregisterBond(); afterBond(g) } }
        catch (e: SecurityException) { unregisterBond(); afterBond(g) }
    }

    private fun unregisterBond() {
        bondReceiver?.let { try { reactContext.unregisterReceiver(it) } catch (_: Exception) {} }
        bondReceiver = null
    }

    private fun enableNotify(g: BluetoothGatt, uuid: UUID): Boolean {
        val ch = g.getService(SERVICE)?.getCharacteristic(uuid) ?: return false
        return try {
            g.setCharacteristicNotification(ch, true)
            val d = ch.getDescriptor(CCCD) ?: return false
            writeDescriptorCompat(g, d, BluetoothGattDescriptor.ENABLE_NOTIFICATION_VALUE)
        } catch (_: SecurityException) { false }
    }

    private val callback = object : BluetoothGattCallback() {
        override fun onConnectionStateChange(g: BluetoothGatt, status: Int, newState: Int) {
            if (newState == BluetoothProfile.STATE_CONNECTED && connectPromise != null) {
                mainHandler.post { ensureBond(g) }
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                if (connectPromise != null) mainHandler.post { failConnect("DISCONNECTED", "The Magene dropped the connection (status $status)") }
                else {
                    mainHandler.post { rejectPending("DISCONNECTED", "The Magene disconnected") }
                    emit("MageneDisconnected", Arguments.createMap())
                }
            }
        }

        override fun onServicesDiscovered(g: BluetoothGatt, status: Int) {
            if (connectPromise == null) return
            if (g.getService(SERVICE) == null) { mainHandler.post { failConnect("NOT_MAGENE", "No Magene service on this device") }; return }
            try { if (!g.requestMtu(247)) mainHandler.post { startNotify(g) } }
            catch (_: SecurityException) { mainHandler.post { startNotify(g) } }
        }

        override fun onMtuChanged(g: BluetoothGatt, newMtu: Int, status: Int) {
            if (status == BluetoothGatt.GATT_SUCCESS) mtu = newMtu
            if (connectPromise != null) mainHandler.post { startNotify(g) }
        }

        override fun onDescriptorWrite(g: BluetoothGatt, d: BluetoothGattDescriptor, status: Int) {
            if (connectPromise == null) return
            mainHandler.post {
                if (d.characteristic.uuid == CC02) {
                    if (!enableNotify(g, CC03)) failConnect("NOTIFY_FAILED", "Couldn't subscribe to CC03")
                } else if (d.characteristic.uuid == CC03) {
                    clearTimeout()
                    val p = connectPromise; connectPromise = null
                    val bonded = try { g.device.bondState == BluetoothDevice.BOND_BONDED } catch (_: SecurityException) { false }
                    p?.resolve(Arguments.createMap().apply { putInt("mtu", mtu); putBoolean("bonded", bonded) })
                }
            }
        }

        override fun onCharacteristicWrite(g: BluetoothGatt, ch: BluetoothGattCharacteristic, status: Int) {
            mainHandler.post {
                if (status == BluetoothGatt.GATT_SUCCESS) resolvePending(null)
                else rejectPending("WRITE_FAILED", "GATT write failed (status $status)")
            }
        }

        override fun onCharacteristicRead(g: BluetoothGatt, ch: BluetoothGattCharacteristic, value: ByteArray, status: Int) {
            readDone(value, status)
        }

        @Deprecated("pre-33 path")
        override fun onCharacteristicRead(g: BluetoothGatt, ch: BluetoothGattCharacteristic, status: Int) {
            @Suppress("DEPRECATION") readDone(ch.value ?: ByteArray(0), status)
        }

        override fun onCharacteristicChanged(g: BluetoothGatt, ch: BluetoothGattCharacteristic, value: ByteArray) {
            notify(ch.uuid, value)
        }

        @Deprecated("pre-33 path")
        override fun onCharacteristicChanged(g: BluetoothGatt, ch: BluetoothGattCharacteristic) {
            @Suppress("DEPRECATION") notify(ch.uuid, ch.value ?: ByteArray(0))
        }
    }

    private fun startNotify(g: BluetoothGatt) {
        if (!enableNotify(g, CC02)) failConnect("NOTIFY_FAILED", "Couldn't subscribe to CC02")
    }

    private fun readDone(value: ByteArray, status: Int) = mainHandler.post {
        if (status == BluetoothGatt.GATT_SUCCESS) resolvePending(Base64.encodeToString(value, Base64.NO_WRAP))
        else rejectPending("READ_FAILED", "GATT read failed (status $status)")
    }

    private fun notify(uuid: UUID, value: ByteArray) {
        val ch = when (uuid) { CC02 -> "cc02"; CC03 -> "cc03"; else -> return }
        emit("MageneNotify", Arguments.createMap().apply {
            putString("ch", ch)
            putString("data", Base64.encodeToString(value, Base64.NO_WRAP))
        })
    }

    private fun emit(event: String, params: WritableMap) {
        reactContext.getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java).emit(event, params)
    }

    // ---- write / read ------------------------------------------------------------------------
    // write("cc02"|"cc03", base64, withResponse) - resolves when Android reports the write done.
    @ReactMethod
    fun write(ch: String, dataB64: String, withResponse: Boolean, promise: Promise) {
        val g = gatt ?: run { promise.reject("NOT_CONNECTED", "Magene not connected"); return }
        val c = g.getService(SERVICE)?.getCharacteristic(if (ch == "cc03") CC03 else CC02)
            ?: run { promise.reject("NO_CHAR", "Characteristic $ch missing"); return }
        if (!beginOp(promise)) return
        val data = Base64.decode(dataB64, Base64.NO_WRAP)
        val type = if (withResponse) BluetoothGattCharacteristic.WRITE_TYPE_DEFAULT
                   else BluetoothGattCharacteristic.WRITE_TYPE_NO_RESPONSE
        val ok = try {
            if (Build.VERSION.SDK_INT >= 33) g.writeCharacteristic(c, data, type) == BluetoothStatusCodes.SUCCESS
            else @Suppress("DEPRECATION") run { c.writeType = type; c.value = data; g.writeCharacteristic(c) }
        } catch (e: SecurityException) { false }
        if (!ok) rejectPending("WRITE_FAILED", "Android refused the write")
    }

    // read(uuid) -> base64 value of a characteristic in any service (battery 2A19, 180A info).
    @ReactMethod
    fun read(uuid: String, promise: Promise) {
        val g = gatt ?: run { promise.reject("NOT_CONNECTED", "Magene not connected"); return }
        val target = UUID.fromString(uuid)
        val c = g.services.firstNotNullOfOrNull { it.getCharacteristic(target) }
            ?: run { promise.reject("NO_CHAR", "Characteristic $uuid missing"); return }
        if (!beginOp(promise)) return
        val ok = try { g.readCharacteristic(c) } catch (_: SecurityException) { false }
        if (!ok) rejectPending("READ_FAILED", "Android refused the read")
    }

    @ReactMethod
    fun getMtu(promise: Promise) = promise.resolve(mtu)

    @ReactMethod
    fun disconnect(promise: Promise) {
        rejectPending("DISCONNECTED", "Disconnected")
        closeGatt()
        promise.resolve(null)
    }

    // Required by NativeEventEmitter on the JS side.
    @ReactMethod fun addListener(eventName: String) {}
    @ReactMethod fun removeListeners(count: Int) {}

    // ---- op bookkeeping ------------------------------------------------------------------------
    private fun beginOp(p: Promise): Boolean {
        if (pending != null || connectPromise != null) { p.reject("BUSY", "Magene operation in progress"); return false }
        pending = p
        armTimeout(OP_TIMEOUT_MS) { rejectPending("TIMEOUT", "The Magene didn't respond") }
        return true
    }

    private fun resolvePending(v: Any?) {
        clearTimeout()
        val p = pending; pending = null
        p?.resolve(v)
    }

    private fun rejectPending(code: String, msg: String) {
        val p = pending ?: return
        clearTimeout()
        pending = null
        p.reject(code, msg)
    }

    private fun armTimeout(ms: Long, onTimeout: () -> Unit) {
        clearTimeout()
        val r = Runnable { pendingTimeout = null; onTimeout() }
        pendingTimeout = r
        mainHandler.postDelayed(r, ms)
    }

    private fun clearTimeout() {
        pendingTimeout?.let { mainHandler.removeCallbacks(it) }
        pendingTimeout = null
    }

    private fun writeDescriptorCompat(g: BluetoothGatt, d: BluetoothGattDescriptor, v: ByteArray): Boolean =
        if (Build.VERSION.SDK_INT >= 33) g.writeDescriptor(d, v) == BluetoothStatusCodes.SUCCESS
        else @Suppress("DEPRECATION") run { d.value = v; g.writeDescriptor(d) }

    private fun closeGatt() {
        try { gatt?.disconnect(); gatt?.close() } catch (_: SecurityException) {}
        gatt = null
    }

    override fun invalidate() {
        unregisterBond()
        closeGatt()
        super.invalidate()
    }
}

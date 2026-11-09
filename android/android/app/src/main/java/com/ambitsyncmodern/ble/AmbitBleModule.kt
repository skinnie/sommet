package com.ambitsyncmodern.ble

import android.Manifest
import android.util.Log
import android.bluetooth.BluetoothDevice
import android.bluetooth.BluetoothGattCharacteristic
import android.bluetooth.BluetoothGattDescriptor
import android.bluetooth.BluetoothGattServer
import android.bluetooth.BluetoothGattServerCallback
import android.bluetooth.BluetoothGattService
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothProfile
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
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.*
import com.facebook.react.modules.core.DeviceEventManagerModule
import com.facebook.react.modules.core.PermissionAwareActivity
import com.facebook.react.modules.core.PermissionListener
import java.util.ArrayDeque
import java.util.UUID
import java.util.concurrent.Executors

/*
 * EXPERIMENTAL — BLE support for Sommet. Ambit3 / Traverse / Kailash.
 *
 * ARCHITECTURE (corrected 2026-08-09 — this is the whole ballgame, see HANDOFF.md
 * Milestone 7 item 8): the Ambit3 BLE protocol is INVERTED from an ordinary
 * watch, ANCS-style. The custom NSP service is NOT hosted on the watch — the
 * watch only ever exposes the two generic GATT services. The PHONE hosts a
 * BluetoothGattServer with the NSP service; the watch advertises the service
 * UUID as a *solicitation* ("connect to me if you host NSP"), then connects in
 * as a GATT CLIENT and reads/writes the phone's characteristics.
 *
 * This was confirmed two independent ways: a live btsnoop HCI capture of the
 * working Suunto app on the real tablet+watch (assets/ble 2026-08-09/), and the
 * decompiled Suunto app (assets/APK/Compress.zip, com.suunto.komposti —
 * BLEService.java setService(), BLECentralImpl.java createGattServer(),
 * BLEBase.java's NSP_TO_CLIENT / NSP_TO_SERVER UUID constants). Every earlier
 * client-model attempt (this project's own prior code, nRF Connect, a Linux
 * bleak session) failed identically — all only ever saw the two generic
 * services on the watch — because the NSP service was never there to find.
 *
 * Server spec (from BLEService.java setService(), byte-for-byte vs the capture):
 *   service  98ae7120  PRIMARY
 *     notify d0fd6b80  PROPERTY_NOTIFY + PERMISSION_READ, with a CCCD (0x2902)
 *                      — phone -> watch data, via notifyCharacteristicChanged()
 *     write  c6339440  PROPERTY_WRITE_NO_RESPONSE + PERMISSION_WRITE
 *                      — watch -> phone data, via onCharacteristicWriteRequest()
 * Note the UUID roles are inverted vs. the intuitive "we write / we get notified"
 * client reading: c6339440 is where the WATCH writes into US; d0fd6b80 is where
 * WE notify the watch. The names in BLEBase.java (NSP_TO_SERVER = c6339440,
 * NSP_TO_CLIENT = d0fd6b80) are from the watch's point of view (watch = client).
 *
 * The NSP frame format (0x7e envelope, 12-byte header, CRC32) and the native
 * protocol code (protocol_ble.c) are UNCHANGED — only the GATT plumbing
 * direction flips. protocol_ble.c still calls bleWriteChunk() to "send to the
 * watch" and this module still calls nativeAmbitBleOnNotify() to feed "received
 * from the watch"; only which GATT operation backs each of those changed.
 *
 * Pairing/bonding (passkey handling, the bond-state receiver below) is a real,
 * separate prerequisite that IS still needed and is handled here — see the
 * receiver's own comments and HANDOFF.md items 5-7 for that whole saga.
 */

private val NSP_SERVICE_UUID: UUID = UUID.fromString("98ae7120-e62e-11e3-badd-0002a5d5c51b")
// c6339440 = NSP_TO_SERVER: the WATCH writes into this, WE (the server) receive it.
private val NSP_WRITE_CHAR_UUID: UUID = UUID.fromString("c6339440-e62e-11e3-a5b3-0002a5d5c51b")
// d0fd6b80 = NSP_TO_CLIENT: WE (the server) notify the watch on this.
private val NSP_NOTIFY_CHAR_UUID: UUID = UUID.fromString("d0fd6b80-e62e-11e3-a2e9-0002a5d5c51b")
private val CCCD_UUID: UUID = UUID.fromString("00002902-0000-1000-8000-00805f9b34fb")

private const val SCAN_TIMEOUT_MS = 15_000L
// Matches Suunto's own BluetoothOperationWaitBonding timeout (jadx diff, 2026-08-21 -
// sommet_ble_stability_suunto_app_diff memory) - a bonding attempt gets this long to
// resolve to BOND_BONDED/BOND_NONE before it's treated as a named failure.
private const val BOND_TIMEOUT_MS = 60_000L
// From the scan match to the watch subscribing to our notify characteristic. The watch can
// link and then never subscribe (seen 2026-10-24: three links, no subscription, and Home
// showed "Connecting" for 3.5 minutes). Bonding has its own BOND_TIMEOUT_MS; this one waits
// it out and then starts again.
private const val SUBSCRIBE_TIMEOUT_MS = 30_000L
/* Belt-and-suspenders alongside the service-UUID scan filter (which already
 * only matches this device family) and the Ambit3/Traverse USB PID table
 * this project already maintains — see AmbitUsbModule.kt's SUUNTO_PID_NAMES.
 * "Suunto NSP" added 2026-08-09 for the Kailash (Hoopoe) — confirmed from a
 * real Android scan and a real capture (kailashpair.pklg) both showing that as
 * its advertised name, not "Kailash"/"Hoopoe". */
private val COMPATIBLE_NAME_PREFIXES = listOf("Ambit3", "Traverse", "Suunto NSP")
private const val BLE_PERMISSION_REQUEST_CODE = 4243

class AmbitBleModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    // ─── Native (implemented in jni_bridge.cpp / protocol_ble.c) ─────────────
    private external fun nativeAmbitBleInit(vid: Int, pid: Int): Boolean
    private external fun nativeAmbitBleOnNotify(chunk: ByteArray)
    // Arms the native pre-init RX stash before the watch can write, so its
    // opening frame isn't dropped in the window before nativeAmbitBleInit
    // publishes g_device. See jni_ble_reset_rx_stash / jni_ble_flush_rx_stash.
    private external fun nativeAmbitBleResetRx()

    private val executor = Executors.newSingleThreadExecutor()
    private val mainHandler = Handler(Looper.getMainLooper())

    // ─── GATT server state (we are the server; the watch is the client) ──────
    private var gattServer: BluetoothGattServer? = null
    private var notifyChar: BluetoothGattCharacteristic? = null  // d0fd6b80, we notify on it
    private var connectedDevice: BluetoothDevice? = null
    private var connectPromise: Promise? = null
    private var pendingPermissionPromise: Promise? = null
    // The watch subscribing to the notify CCCD is the "transport is live" signal
    // (BLEService.java: _isServicing / serviceReady). Guard so the native
    // device-info handshake runs exactly once even if the watch re-subscribes.
    private var nativeInitStarted = false

    // Serializes outgoing notifications — one notifyCharacteristicChanged must
    // complete (onNotificationSent) before the next is issued, same one-in-flight
    // discipline the old client write path used.
    private val chunkQueueLock = Any()
    private val pendingChunks = ArrayDeque<ByteArray>()
    private var chunkInFlight = false

    override fun getName() = "AmbitBleModule"

    // ─── Tablet Bluetooth switched off ───────────────────────────────────────
    // When the phone's own Bluetooth goes off, the GATT server and the link die with it, but
    // no connection-state callback arrives. Home kept showing "Connected · via Bluetooth" with
    // no way to reconnect (seen 2026-10-24 after an off/on). Treat it like the watch closing
    // the link: reset, and tell JS.
    private val adapterStateReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != android.bluetooth.BluetoothAdapter.ACTION_STATE_CHANGED) return
            val state = intent.getIntExtra(android.bluetooth.BluetoothAdapter.EXTRA_STATE, -1)
            if (state == android.bluetooth.BluetoothAdapter.STATE_ON) { rearmBackgroundConnect(); return }
            if (state != android.bluetooth.BluetoothAdapter.STATE_TURNING_OFF &&
                state != android.bluetooth.BluetoothAdapter.STATE_OFF) return
            mainHandler.post { onAdapterOff() }
        }
    }

    // After adapterStateReceiver: Kotlin runs initializers in the order they are written.
    init {
        registerAdapterStateReceiver()
    }

    private fun registerAdapterStateReceiver() {
        try {
            ContextCompat.registerReceiver(
                reactContext, adapterStateReceiver,
                IntentFilter(android.bluetooth.BluetoothAdapter.ACTION_STATE_CHANGED),
                ContextCompat.RECEIVER_EXPORTED)
        } catch (e: Exception) {
            Log.e("AmbitBleModule", "adapter state receiver not registered: ${e.message}")
        }
    }

    private fun onAdapterOff() {
        val wasLive = nativeInitStarted
        val wasConnecting = connectPromise != null
        if (!wasLive && !wasConnecting && gattServer == null) return
        Log.d("AmbitBleModule", "tablet Bluetooth turned off (live=$wasLive connecting=$wasConnecting)")
        clearSubscribeTimeout()
        scanCallback?.let { cb ->
            try {
                (reactContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)
                    ?.adapter?.bluetoothLeScanner?.stopScan(cb)
            } catch (_: Exception) {}
        }
        if (wasConnecting) {
            failConnect("BLUETOOTH_OFF", "Bluetooth was turned off on this device")
        } else {
            closeServer()
        }
        nativeInitStarted = false
        if (wasLive) emitDisconnected(-1, "adapter_off")
    }

    // ─── Standing reconnect, as the Suunto app does ──────────────────────────
    // The watch ends every session itself (HCI reason 0x13) a few seconds to minutes after
    // its sync. The Suunto app keeps a background connect to the bonded watch (btsnoop
    // 2026-08-09: LE Create Connection with the accept-list filter, re-issued after every
    // disconnect, phone = central), so whenever the watch advertises again - "Sync now", or
    // its own schedule - it is linked in ~0.2 s and syncs. Sommet used to connect only after a
    // scan started by the Connect button, so every session needed Connect + Sync now.
    private var autoReconnectAddress: String? = null

    /** Keeps a background connect armed to this bonded watch until disconnectBle(). Resolves
     * false when it can't (no permission, Bluetooth off, not bonded). */
    @ReactMethod
    fun armAutoReconnect(address: String, promise: Promise) {
        val adapter = (reactContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
        val device = try { adapter?.getRemoteDevice(address) } catch (_: Exception) { null }
        val bonded = try { device?.bondState == BluetoothDevice.BOND_BONDED } catch (_: SecurityException) { false }
        if (!hasBlePermissions() || adapter == null || !adapter.isEnabled || device == null || !bonded) {
            promise.resolve(false)
            return
        }
        autoReconnectAddress = address
        mainHandler.post { armBackgroundConnect(device) }
        promise.resolve(true)
    }

    // A link the watch made that starts no session. The watch's first discovery request can
    // reach the stack before Android has registered the link; Android then never answers it
    // and the watch waits out its 30 s ATT timeout, stuck, before hanging up (btsnoop
    // 2026-10-25 00:53:16: request 0.2 ms before the connection event; in the links that
    // worked it came ~30 ms after and was answered at once). A good link starts its session
    // within ~1.3 s, so after EMPTY_LINK_MS we drop it and re-arm: a watch that still wants
    // to sync calls again.
    private val EMPTY_LINK_MS = 6_000L
    private var emptyLinkRunnable: Runnable? = null
    // Set when the watchdog drops a link: the watch is calling back, so the next try is a
    // direct connect (see armBackgroundConnect).
    private var fastRetryNext = false
    @Volatile private var linkUp = false

    private fun armEmptyLinkWatchdog(device: BluetoothDevice) {
        emptyLinkRunnable?.let { mainHandler.removeCallbacks(it) }
        val r = Runnable {
            emptyLinkRunnable = null
            if (nativeInitStarted || connectPromise != null) return@Runnable
            Log.d("AmbitBleModule", "the watch linked but started no session in ${EMPTY_LINK_MS / 1000} s - dropping the link")
            fastRetryNext = true
            try { gattServer?.cancelConnection(device) } catch (_: Exception) {}
        }
        emptyLinkRunnable = r
        mainHandler.postDelayed(r, EMPTY_LINK_MS)
    }

    private fun rearmBackgroundConnect() {
        val address = autoReconnectAddress ?: return
        val fast = fastRetryNext
        fastRetryNext = false
        mainHandler.postDelayed({
            if (autoReconnectAddress != address) return@postDelayed
            val adapter = (reactContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
            val device = try { adapter?.getRemoteDevice(address) } catch (_: Exception) { null } ?: return@postDelayed
            armBackgroundConnect(device, fast)
        }, 1_000L)
    }

    // The background connect listens 30 ms in every 1280 ms (btsnoop 2026-10-25), so a watch
    // calling back after a dropped link took 8-24 s to get through. After a drop we first try
    // a direct connect (30 ms in every 60 ms) for FAST_RETRY_MS, then fall back to the
    // background one. The current Suunto app connects in the same order (direct first,
    // autoConnect as the fallback: RetryWithAutoConnectStrategy, 6.12.7).
    private val FAST_RETRY_MS = 15_000L
    private var fastRetryRunnable: Runnable? = null

    private fun clearFastRetry() {
        fastRetryRunnable?.let { mainHandler.removeCallbacks(it) }
        fastRetryRunnable = null
    }

    private fun armBackgroundConnect(device: BluetoothDevice, fast: Boolean = false) {
        clearFastRetry()
        if (nativeInitStarted || connectPromise != null) return   // a session or a Connect is running
        val adapter = (reactContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
        if (adapter == null || !adapter.isEnabled) return
        nativeAmbitBleResetRx()          // the next session's first bytes wait for its handshake
        if (!fast) {
            Log.d("AmbitBleModule", "standing reconnect armed for ${device.address}")
            openServerAndConnect(device, background = true)
            return
        }
        Log.d("AmbitBleModule", "fast reconnect for ${device.address} (direct for ${FAST_RETRY_MS / 1000} s)")
        openServerAndConnect(device, background = false)
        val r = Runnable {
            fastRetryRunnable = null
            if (nativeInitStarted || connectPromise != null || linkUp) return@Runnable
            if (autoReconnectAddress != device.address) return@Runnable   // Disconnect was pressed
            Log.d("AmbitBleModule", "no call back in ${FAST_RETRY_MS / 1000} s - back to the background connect")
            try { gattServer?.cancelConnection(device) } catch (_: Exception) {}
            armBackgroundConnect(device, fast = false)
        }
        fastRetryRunnable = r
        mainHandler.postDelayed(r, FAST_RETRY_MS)
    }

    private fun emitReconnected(address: String) {
        Log.d("AmbitBleModule", "the watch reconnected by itself - session up")
        val params = Arguments.createMap().apply { putString("address", address) }
        try {
            reactContext
                .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
                .emit("AmbitBleReconnected", params)
        } catch (e: Exception) {
            Log.e("AmbitBleModule", "emitReconnected failed: ${e.message}")
        }
    }

    // ─── Link up, but the watch never subscribes ─────────────────────────────
    private var subscribeTimeoutRunnable: Runnable? = null

    private fun armSubscribeTimeout() {
        clearSubscribeTimeout()
        val r = Runnable {
            subscribeTimeoutRunnable = null
            if (nativeInitStarted || connectPromise == null) return@Runnable
            if (bondingInProgress) { armSubscribeTimeout(); return@Runnable }
            failConnect("NO_SESSION",
                "The watch connected but did not start the session. Choose \"Sync now\" on the watch and try again.")
        }
        subscribeTimeoutRunnable = r
        mainHandler.postDelayed(r, SUBSCRIBE_TIMEOUT_MS)
    }

    private fun clearSubscribeTimeout() {
        subscribeTimeoutRunnable?.let { mainHandler.removeCallbacks(it) }
        subscribeTimeoutRunnable = null
    }

    // Required by NativeEventEmitter on the JS side (without them React Native logs a warning at
    // every start). Events are sent through RCTDeviceEventEmitter, so there's nothing to track.
    @ReactMethod fun addListener(eventName: String) {}
    @ReactMethod fun removeListeners(count: Int) {}

    // ─── scanAndConnect() ──────────────────────────────────────────────────────
    // Scans for the watch advertising (soliciting) the NSP service UUID, then —
    // because the phone is the GATT SERVER, not a client — opens a GATT server
    // hosting the NSP service and lets the watch connect into it. Resolves once
    // the watch has subscribed to our notify characteristic and the native
    // device-info handshake succeeds. From here on, writeRoute()/readRegion()/etc.
    // on AmbitUsbModule work exactly as over USB — same shared native g_device.
    @ReactMethod
    fun scanAndConnect(promise: Promise) {
        scanTargetAddress = null
        beginScan(promise)
    }

    // Multi-watch switcher (2026-08-16). Like scanAndConnect(), but only connects to the one
    // bonded watch at `address` (from listBondedWatches()). With more than one paired watch in
    // range, the plain scan would grab whichever solicited first; this pins it to the picked
    // one. The user still triggers "Sync now"/"Pair Mobile App" on that watch to open its
    // short advertising window (the watch is the GATT client that connects into our server).
    @ReactMethod
    fun scanAndConnectTo(address: String?, promise: Promise) {
        scanTargetAddress = if (address.isNullOrEmpty()) null else address
        beginScan(promise)
    }

    private fun beginScan(promise: Promise) {
        // Arm the native RX stash now — before we scan/connect, and therefore
        // before the watch can write its opening frame. Any bytes that arrive
        // before nativeAmbitBleInit publishes g_device get parked and replayed
        // instead of dropped (the Kailash sends its 0x0002 hello only once).
        nativeInitStarted = false
        nativeAmbitBleResetRx()
        if (!hasBlePermissions()) {
            pendingPermissionPromise = promise
            requestBlePermissions()
            return
        }
        startScan(promise)
    }

    // Every Suunto watch already bonded to this phone (BluetoothAdapter.bondedDevices), so the
    // switcher can list paired BLE watches next to cabled USB ones. HR straps and other Suunto
    // accessories are filtered out — only device names matching the watch family are kept.
    @ReactMethod
    fun listBondedWatches(promise: Promise) {
        val out = Arguments.createArray()
        val adapter = (reactContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
        if (adapter == null) { promise.resolve(out); return }
        try {
            for (dev in adapter.bondedDevices ?: emptySet()) {
                val name = dev.name ?: continue
                if (!isWatchName(name)) continue
                val m = Arguments.createMap()
                m.putString("address", dev.address)
                m.putString("name", name)
                out.pushMap(m)
            }
        } catch (_: SecurityException) {
            // BLUETOOTH_CONNECT not granted yet — return whatever we have (likely empty)
        }
        promise.resolve(out)
    }

    // A bonded device is one of our watches if its name matches the BLE family prefixes or
    // names a known model, but is not an HR strap / smart sensor accessory.
    private fun isWatchName(name: String): Boolean {
        if (name.contains("Sensor", ignoreCase = true) || name.contains("HR", ignoreCase = true)) return false
        if (COMPATIBLE_NAME_PREFIXES.any { name.startsWith(it) }) return true
        return listOf("Ambit", "Traverse", "Kailash").any { name.contains(it, ignoreCase = true) }
    }

    // Multi-watch switcher: when set, startScan() connects only to this device address.
    private var scanTargetAddress: String? = null
    private var scanCallback: ScanCallback? = null

    private fun startScan(promise: Promise) {
        val adapter = (reactContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager)?.adapter
        if (adapter == null || !adapter.isEnabled) {
            promise.reject("BLUETOOTH_OFF", "Bluetooth is off or unavailable")
            return
        }
        val scanner = adapter.bluetoothLeScanner
        if (scanner == null) {
            promise.reject("BLE_UNAVAILABLE", "BLE scanning unavailable on this device")
            return
        }

        connectPromise = promise
        // Scan UNFILTERED (2026-08-09) and match in the callback. The watch
        // advertises the NSP service as a *solicitation* ("connect to me if you
        // host this"), not as a normal advertised service class — and Android's
        // ScanFilter.setServiceUuid() matches only the latter. The Ambit3 happens
        // to advertise it both ways so a service-UUID filter caught it, but the
        // Kailash only solicits, so that filter never matched it (real hardware:
        // scan registered, zero results). Checking the solicitation UUID + name
        // ourselves catches every device in the family regardless of how each one
        // advertises. No filter list -> we see all devices and decide below.
        val settings = ScanSettings.Builder()
            .setScanMode(ScanSettings.SCAN_MODE_LOW_LATENCY)
            .build()

        val timeoutRunnable = Runnable {
            try { scanner.stopScan(scanCallback) } catch (_: SecurityException) {}
            connectPromise?.let {
                connectPromise = null
                it.reject("SCAN_TIMEOUT", "No Ambit3/Traverse/Kailash found in range — trigger \"Sync now\" or " +
                    "\"Pair Mobile App\" on the watch right before scanning, its advertising window is short")
            }
        }
        mainHandler.postDelayed(timeoutRunnable, SCAN_TIMEOUT_MS)

        try {
            scanner.startScan(emptyList(), settings, object : ScanCallback() {
                override fun onScanResult(callbackType: Int, result: ScanResult) {
                    val record = result.scanRecord
                    val nspTarget = ParcelUuid(NSP_SERVICE_UUID)
                    // Match if the advertisement carries the NSP service UUID as a
                    // normal service class OR as a solicitation (the Kailash case),
                    // or by a known name prefix as a fallback.
                    val advertisesNsp =
                        (record?.serviceUuids?.contains(nspTarget) == true) ||
                        (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q &&
                            record?.serviceSolicitationUuids?.contains(nspTarget) == true)
                    val name = result.device.name ?: record?.deviceName
                    val nameMatches = name != null && COMPATIBLE_NAME_PREFIXES.any { name.startsWith(it) }
                    if (!advertisesNsp && !nameMatches) return // not one of ours — ignore (unfiltered scan sees everything)
                    // Multi-watch switcher: if the user picked a specific paired watch, ignore
                    // any other Suunto that happens to solicit at the same time.
                    scanTargetAddress?.let { if (result.device.address != it) return }

                    Log.d("AmbitBleModule", "scan match: name=$name advertisesNsp=$advertisesNsp addr=${result.device.address}")
                    mainHandler.removeCallbacks(timeoutRunnable)
                    try { scanner.stopScan(this) } catch (_: SecurityException) {}
                    connectToDevice(result.device)
                }

                override fun onScanFailed(errorCode: Int) {
                    mainHandler.removeCallbacks(timeoutRunnable)
                    connectPromise?.let {
                        connectPromise = null
                        it.reject("SCAN_FAILED", "BLE scan failed, code=$errorCode")
                    }
                }
            }.also { scanCallback = it })
        } catch (e: SecurityException) {
            mainHandler.removeCallbacks(timeoutRunnable)
            connectPromise = null
            promise.reject("PERMISSION_DENIED", e.message)
        }
    }

    // ─── Connect: open the GATT server and let the watch connect in ───────────
    private fun connectToDevice(device: BluetoothDevice) {
        // The standing reconnect may already have linked the watch and started its session
        // (it resolves the pending Connect itself); a new server now would cut it.
        if (nativeInitStarted) return
        nativeInitStarted = false
        armSubscribeTimeout()
        registerBondAndPairingReceiver(device)
        openServerAndConnect(device)
    }

    // false: direct connect, right after a scan match (the user's Connect). true: a standing
    // background connect to a bonded watch, see armBackgroundConnect().
    private var connectInBackground = false

    private fun openServerAndConnect(device: BluetoothDevice, background: Boolean = false) {
        connectInBackground = background
        // One server for every session. A new one changes the GATT database, so the watch gets a
        // Service Changed indication as it links, and its discovery request racing that
        // indication went unanswered until its 30 s ATT timeout, when it hung up (btsnoop
        // 2026-10-25 01:26 and 01:27; the links that worked had no such race). Reusing it also
        // keeps a second server from being registered next to the first (serverIf 8 and 9).
        val existing = gattServer
        if (existing != null && notifyChar != null) {
            synchronized(chunkQueueLock) { pendingChunks.clear(); chunkInFlight = false }
            connectedDevice = device
            try {
                existing.connect(device, background)
                Log.d("AmbitBleModule", "gattServer.connect(${if (background) "background" else "direct"}) issued for ${device.address} (same server)")
            } catch (e: SecurityException) {
                failConnect("PERMISSION_DENIED", e.message)
            }
            return
        }
        val manager = reactContext.getSystemService(Context.BLUETOOTH_SERVICE) as? BluetoothManager
        if (manager == null) {
            failConnect("BLUETOOTH_OFF", "BluetoothManager unavailable")
            return
        }
        try {
            val server = manager.openGattServer(reactContext, serverCallback)
            if (server == null) {
                failConnect("SERVER_OPEN_FAILED", "openGattServer() returned null (Bluetooth off?)")
                return
            }
            gattServer = server

            // Build the NSP service exactly as the decompiled BLEService.setService():
            // notify char (PROPERTY_NOTIFY, PERMISSION_READ) with a writable CCCD, and
            // write char (PROPERTY_WRITE_NO_RESPONSE, PERMISSION_WRITE).
            val service = BluetoothGattService(NSP_SERVICE_UUID, BluetoothGattService.SERVICE_TYPE_PRIMARY)

            val notify = BluetoothGattCharacteristic(
                NSP_NOTIFY_CHAR_UUID,
                BluetoothGattCharacteristic.PROPERTY_NOTIFY,
                BluetoothGattCharacteristic.PERMISSION_READ
            )
            notify.addDescriptor(
                BluetoothGattDescriptor(CCCD_UUID, BluetoothGattDescriptor.PERMISSION_WRITE)
            )
            service.addCharacteristic(notify)
            notifyChar = notify

            val write = BluetoothGattCharacteristic(
                NSP_WRITE_CHAR_UUID,
                BluetoothGattCharacteristic.PROPERTY_WRITE_NO_RESPONSE,
                BluetoothGattCharacteristic.PERMISSION_WRITE
            )
            service.addCharacteristic(write)

            // addService is async; connect to the watch once it's registered
            // (onServiceAdded) so the watch never discovers an incomplete DB.
            // Remember which device we're servicing (also needed for notify). Set before
            // addService: onServiceAdded runs on a binder thread, can come back first, and
            // connects to this device; when it won the race the connect was never issued
            // (2026-10-25 01:49).
            connectedDevice = device
            Log.d("AmbitBleModule", "opening GATT server, adding NSP service")
            server.addService(service)
        } catch (e: SecurityException) {
            failConnect("PERMISSION_DENIED", e.message)
        }
    }

    private val serverCallback = object : BluetoothGattServerCallback() {
        override fun onServiceAdded(status: Int, service: BluetoothGattService) {
            Log.d("AmbitBleModule", "onServiceAdded status=$status uuid=${service.uuid}")
            // Now initiate the ACL connection from the server side.
            //
            // autoConnect=FALSE (direct connect), corrected 2026-08-09 on real hardware:
            // the decompiled Suunto app uses true, but true does a low-priority BACKGROUND
            // connect that measured ~15s to actually link on a FRESH (unbonded) device —
            // by which time the watch's short "Pair Mobile App" advertising window has
            // closed, so bonding starts then instantly dies as the link drops
            // (bond state 11 -> 10, "disconnected before subscribing"). A direct connect
            // links within the advertising window (~250ms when it worked earlier, once
            // bonded). The Suunto app likely tolerates true because it keeps a persistent
            // bond and isn't racing a fresh-pair window each time.
            val device = connectedDevice ?: return
            try {
                gattServer?.connect(device, connectInBackground)
                Log.d("AmbitBleModule", "gattServer.connect(${if (connectInBackground) "background" else "direct"}) issued for ${device.address}")
            } catch (e: SecurityException) {
                failConnect("PERMISSION_DENIED", e.message)
            }
        }

        override fun onConnectionStateChange(device: BluetoothDevice, status: Int, newState: Int) {
            Log.d("AmbitBleModule", "server onConnectionStateChange status=$status newState=$newState dev=${device.address}")
            if (newState == BluetoothProfile.STATE_CONNECTED) {
                linkUp = true
                connectedDevice = device
                mainHandler.post { clearFastRetry() }
                if (connectPromise == null && !nativeInitStarted) armEmptyLinkWatchdog(device)
                // Do NOT start the native handshake yet — wait for the watch to
                // actually subscribe to our notify CCCD (onDescriptorWriteRequest),
                // which is the real "transport is live" signal. Bonding, if needed,
                // happens around here and is driven by the bond-state receiver.
            } else if (newState == BluetoothProfile.STATE_DISCONNECTED) {
                linkUp = false
                if (!nativeInitStarted && connectPromise == null) {
                    // A link the watch opened by itself that closed before a session started:
                    // nothing to report, wait for the next one.
                    rearmBackgroundConnect()
                } else if (!nativeInitStarted) {
                    if (bondingInProgress) {
                        // Distinct, expected failure - not a generic drop. Suunto's own app
                        // (jadx diff, 2026-08-21 - sommet_ble_stability_suunto_app_diff
                        // memory) has a dedicated BluetoothOperationWaitBonding queue step
                        // that treats a GATT disconnect DURING bonding as its own named
                        // case rather than routing it through the same generic disconnect
                        // path as everything else. Same idea here: clear the bonding-wait
                        // state and report a message that actually says what happened,
                        // instead of the generic "disconnected before subscribing" (which
                        // reads like a normal connect race, not a failed pairing attempt).
                        clearBondTimeout()
                        bondingInProgress = false
                        failConnect("DISCONNECTED_DURING_BONDING",
                            "Watch disconnected while pairing/bonding was in progress (status=$status)")
                    } else {
                        // Disconnected before we ever went live — surface it as the connect failure.
                        failConnect("DISCONNECTED", "Watch disconnected before subscribing (status=$status)")
                    }
                } else {
                    // Post-handshake disconnect - the link was already live and syncing.
                    // Real gap, found live 2026-08-21 (André: "the watch kinda don't
                    // reconnect after a while"): nothing here told JS this happened at all.
                    // HomeScreen.tsx's `bleConnected` state assumed the BLE link "stays up
                    // until explicitly closed" and never checked again - so the UI kept
                    // showing "Connected" indefinitely after a real, silent drop, and the
                    // user had no signal that tapping "Pair" again was even necessary (the
                    // native reconnect itself works fine once re-triggered - confirmed live,
                    // same session - the JS side just never asked it to). Reset local state
                    // and emit an event so JS can reflect reality instead of a stale assumption.
                    Log.d("AmbitBleModule", "post-handshake disconnect (status=$status) - notifying JS")
                    nativeInitStarted = false
                    connectedDevice = null
                    emitDisconnected(status, "watch")
                    rearmBackgroundConnect()
                }
            }
        }

        override fun onCharacteristicWriteRequest(
            device: BluetoothDevice, requestId: Int, characteristic: BluetoothGattCharacteristic,
            preparedWrite: Boolean, responseNeeded: Boolean, offset: Int, value: ByteArray
        ) {
            // Watch -> phone NSP data lands here (writes to c6339440).
            if (characteristic.uuid == NSP_WRITE_CHAR_UUID) {
                nativeAmbitBleOnNotify(value)   // stashed until the handshake is armed
                // A bonded watch reconnecting may skip rewriting the notify CCCD (a client keeps
                // that per bond) and go straight to its opener; that starts the session too.
                if (!nativeInitStarted) {
                    nativeInitStarted = true
                    connectedDevice = device
                    mainHandler.post { clearSubscribeTimeout() }
                    proceedToNativeInit(device)
                }
            }
            if (responseNeeded) {
                try {
                    gattServer?.sendResponse(device, requestId, android.bluetooth.BluetoothGatt.GATT_SUCCESS, offset, null)
                } catch (_: SecurityException) {}
            }
        }

        override fun onDescriptorWriteRequest(
            device: BluetoothDevice, requestId: Int, descriptor: BluetoothGattDescriptor,
            preparedWrite: Boolean, responseNeeded: Boolean, offset: Int, value: ByteArray
        ) {
            // The watch subscribing to our notify characteristic's CCCD is the
            // "transport ready" signal (BLEService.java: value[0]==1 -> serviceReady).
            val isNotifyCccd = descriptor.uuid == CCCD_UUID &&
                descriptor.characteristic?.uuid == NSP_NOTIFY_CHAR_UUID
            val enabling = value.isNotEmpty() && value[0].toInt() != 0
            Log.d("AmbitBleModule", "onDescriptorWriteRequest cccd=$isNotifyCccd enabling=$enabling")

            if (responseNeeded) {
                try {
                    gattServer?.sendResponse(device, requestId, android.bluetooth.BluetoothGatt.GATT_SUCCESS, offset, value)
                } catch (_: SecurityException) {}
            }

            if (isNotifyCccd && enabling && !nativeInitStarted) {
                nativeInitStarted = true
                mainHandler.post { clearSubscribeTimeout() }
                connectedDevice = device
                proceedToNativeInit(device)
            }
        }

        override fun onNotificationSent(device: BluetoothDevice, status: Int) {
            // One outgoing chunk finished — release the in-flight lock and drain the next.
            synchronized(chunkQueueLock) { chunkInFlight = false }
            drainPendingChunks()
        }
    }

    private fun proceedToNativeInit(device: BluetoothDevice) {
        Log.d("AmbitBleModule", "watch subscribed — starting native device-info handshake")
        executor.execute {
            val ok = nativeAmbitBleInit(SUUNTO_VID, guessProductId(device.name))
            mainHandler.post {
                if (connectPromise == null) {
                    // The watch connected by itself (Sync now, or its own schedule) through the
                    // standing background connect: tell JS, which syncs like after Connect.
                    if (ok) emitReconnected(device.address)
                    else {
                        Log.e("AmbitBleModule", "background session: device-info handshake failed")
                        nativeInitStarted = false
                        try { gattServer?.cancelConnection(device) } catch (_: Exception) {}
                    }
                    return@post
                }
                connectPromise?.let {
                    connectPromise = null
                    // Resolve the connected watch's address (multi-watch switcher, 2026-08-16):
                    // lets the picker highlight the right paired watch even after a generic scan.
                    if (ok) it.resolve(device.address)
                    else it.reject("BLE_INIT_FAILED",
                        "Watch connected and subscribed, but device-info readback failed — " +
                        "see logcat tag AmbitProtocolBle. Refusing to proceed without a confirmed firmware generation.")
                }
            }
        }
    }

    // ─── bleWriteChunk(): phone -> watch, via notification ────────────────────
    // Called from native code (protocol_ble.c, via JNI) with one already-framed
    // <=20-byte piece of an outgoing NSP message. In the server model this is a
    // notifyCharacteristicChanged() on our notify characteristic, NOT a client
    // write — see the file header for the architecture. Serialized one-in-flight
    // via onNotificationSent.
    @Suppress("unused") // called from protocol_ble.c via JNI, not from Kotlin
    fun bleWriteChunk(chunk: ByteArray) {
        synchronized(chunkQueueLock) {
            pendingChunks.addLast(chunk)
        }
        drainPendingChunks()
    }

    private fun drainPendingChunks() {
        val server = gattServer ?: return
        val ch = notifyChar ?: return
        val device = connectedDevice ?: return
        val chunk: ByteArray
        synchronized(chunkQueueLock) {
            if (chunkInFlight || pendingChunks.isEmpty()) return
            chunk = pendingChunks.removeFirst()
            chunkInFlight = true
        }
        try {
            val ok =
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    // 4-arg overload (API 33+) takes the value directly and returns a status Int.
                    server.notifyCharacteristicChanged(device, ch, false, chunk) == BluetoothStatusCodesSuccess
                } else {
                    @Suppress("DEPRECATION")
                    ch.value = chunk
                    @Suppress("DEPRECATION")
                    server.notifyCharacteristicChanged(device, ch, false)
                }
            if (!ok) {
                // Couldn't queue it (e.g. not subscribed yet) — release and stop;
                // native side will surface the resulting NSP timeout.
                synchronized(chunkQueueLock) { chunkInFlight = false }
            }
        } catch (e: SecurityException) {
            synchronized(chunkQueueLock) { chunkInFlight = false }
        }
    }

    @ReactMethod
    fun disconnectBle(promise: Promise) {
        autoReconnectAddress = null
        unregisterBondReceiver()
        clearSubscribeTimeout()
        closeServer()
        nativeInitStarted = false
        promise.resolve(true)
    }

    private fun closeServer() {
        try {
            connectedDevice?.let { gattServer?.cancelConnection(it) }
            gattServer?.close()
        } catch (_: Exception) {}
        gattServer = null
        notifyChar = null
        connectedDevice = null
        linkUp = false
        mainHandler.post { clearFastRetry() }
        synchronized(chunkQueueLock) { pendingChunks.clear(); chunkInFlight = false }
    }

    /** The watch asked to sync ("Sync now" on the watch while the link is already up).
     * Called from protocol_ble.c on the thread that received the notice, after it has been
     * answered; JS (HomeScreen) runs the same sync as the app's own Sync button. */
    @Suppress("unused") // called from protocol_ble.c via JNI
    fun bleSyncRequested() {
        Log.d("AmbitBleModule", "the watch asked to sync")
        mainHandler.post {
            try {
                reactContext
                    .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
                    .emit("AmbitBleSyncRequested", null)
            } catch (e: Exception) {
                Log.e("AmbitBleModule", "sync request not passed on: ${e.message}")
            }
        }
    }

    /** Tells JS a live BLE link just dropped, so HomeScreen.tsx can flip `bleConnected` back
     * to false and surface a real reconnect prompt instead of trusting a stale assumption
     * that the link never drops on its own. See the post-handshake disconnect branch above
     * for why this exists. */
    private fun emitDisconnected(status: Int, reason: String) {
        val params = Arguments.createMap().apply {
            putInt("status", status)
            putString("reason", reason)   // "watch" (the watch closed the link) or "adapter_off"
        }
        try {
            reactContext
                .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
                .emit("AmbitBleDisconnected", params)
        } catch (e: Exception) {
            Log.e("AmbitBleModule", "emitDisconnected failed: ${e.message}")
        }
    }

    private fun failConnect(code: String, message: String?) {
        Log.e("AmbitBleModule", "failConnect($code): $message")
        mainHandler.post {
            clearSubscribeTimeout()
            connectPromise?.let { connectPromise = null; it.reject(code, message) }
        }
        // Drop the link but keep the server (see openServerAndConnect).
        try { connectedDevice?.let { gattServer?.cancelConnection(it) } } catch (_: Exception) {}
        connectedDevice = null
        synchronized(chunkQueueLock) { pendingChunks.clear(); chunkInFlight = false }
        rearmBackgroundConnect()   // back to waiting for the watch, if it has been connected before
    }

    // ─── Pairing / bonding (unchanged prerequisite) ──────────────────────────
    // The watch requires an LE bond. The bond is triggered around the GATT-server
    // connection; this receiver handles the passkey/PIN prompts. See HANDOFF.md
    // Milestone 7 items 5-7 for the whole passkey/BLUETOOTH_PRIVILEGED saga.
    private var bondReceiver: BroadcastReceiver? = null

    // Explicit, timed bonding-wait state — see the DISCONNECTED_DURING_BONDING branch above
    // and BOND_TIMEOUT_MS's own comment for why this exists as a named state rather than
    // being inferred from bond-state broadcasts alone.
    private var bondingInProgress = false
    private var bondTimeoutRunnable: Runnable? = null

    private fun clearBondTimeout() {
        bondTimeoutRunnable?.let { mainHandler.removeCallbacks(it) }
        bondTimeoutRunnable = null
    }

    private fun registerBondAndPairingReceiver(device: BluetoothDevice) {
        unregisterBondReceiver()
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                val target: BluetoothDevice? =
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
                        intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE, BluetoothDevice::class.java)
                    else
                        @Suppress("DEPRECATION") intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                if (target?.address != device.address) return

                when (intent.action) {
                    BluetoothDevice.ACTION_BOND_STATE_CHANGED -> {
                        val state = intent.getIntExtra(BluetoothDevice.EXTRA_BOND_STATE, BluetoothDevice.BOND_NONE)
                        Log.d("AmbitBleModule", "bond state -> $state")
                        when (state) {
                            BluetoothDevice.BOND_BONDING -> {
                                bondingInProgress = true
                                clearBondTimeout()
                                // Mirrors Suunto's own BluetoothOperationWaitBonding timeout
                                // (60s) - without this, a bonding attempt that never resolves
                                // (the watch drops the link mid-SMP-exchange without a bond
                                // broadcast ever following, a real possibility per this
                                // project's own le-connection-abort-by-local history) leaves
                                // bondingInProgress permanently true and connectPromise
                                // hanging until the outer scan timeout, rather than failing
                                // with a message that says what actually happened.
                                val timeout = Runnable {
                                    bondTimeoutRunnable = null
                                    if (bondingInProgress) {
                                        bondingInProgress = false
                                        failConnect("BONDING_TIMEOUT",
                                            "Bonding did not complete within ${BOND_TIMEOUT_MS / 1000}s")
                                    }
                                }
                                bondTimeoutRunnable = timeout
                                mainHandler.postDelayed(timeout, BOND_TIMEOUT_MS)
                            }
                            BluetoothDevice.BOND_BONDED, BluetoothDevice.BOND_NONE -> {
                                bondingInProgress = false
                                clearBondTimeout()
                            }
                        }
                    }

                    BluetoothDevice.ACTION_PAIRING_REQUEST -> {
                        val variant = intent.getIntExtra(BluetoothDevice.EXTRA_PAIRING_VARIANT, -1)
                        Log.d("AmbitBleModule", "ACTION_PAIRING_REQUEST variant=$variant")
                        // PIN (0) / PIN_16_DIGITS (7) / PASSKEY (1): the watch shows a code the
                        // user types here. setPin() is the only public submission API.
                        if (variant == BluetoothDevice.PAIRING_VARIANT_PIN || variant == 7 || variant == 1) {
                            abortBroadcast()
                            promptForPin(device)
                        }
                        // PASSKEY_CONFIRMATION (2) / CONSENT (3): NOT handled — confirming those
                        // needs BLUETOOTH_PRIVILEGED (system-only). Leave the broadcast alone so
                        // Android's own (privileged) system dialog confirms it — the fix that
                        // finally got bonding to complete, 2026-08-09, see HANDOFF.md item 7.
                    }
                }
            }
        }
        bondReceiver = receiver
        val filter = IntentFilter(BluetoothDevice.ACTION_BOND_STATE_CHANGED).apply {
            addAction(BluetoothDevice.ACTION_PAIRING_REQUEST)
            priority = IntentFilter.SYSTEM_HIGH_PRIORITY
        }
        // RECEIVER_EXPORTED is required — com.android.bluetooth (system) must be able
        // to deliver these, and a NOT_EXPORTED dynamic receiver blocks it. See
        // HANDOFF.md Milestone 7 for the real logcat that proved this.
        ContextCompat.registerReceiver(reactContext, receiver, filter, ContextCompat.RECEIVER_EXPORTED)
    }

    /** Native PIN/passkey-entry dialog — the watch shows a numeric code, the user
     * types it here; setPin() is the only public submission API for either PIN or
     * Passkey Entry. See ACTION_PAIRING_REQUEST handling for why. */
    private fun promptForPin(device: BluetoothDevice) {
        val activity = reactContext.currentActivity ?: return
        mainHandler.post {
            val input = android.widget.EditText(activity).apply {
                inputType = android.text.InputType.TYPE_CLASS_NUMBER
                hint = "Code"
            }
            android.app.AlertDialog.Builder(activity)
                .setTitle("Pair with watch")
                .setMessage("Enter the code shown on the watch's screen")
                .setView(input)
                .setCancelable(false)
                .setPositiveButton("OK") { _, _ ->
                    val pin = input.text.toString()
                    val pinBytes = pin.toByteArray(Charsets.UTF_8)
                    if (pinBytes.isNotEmpty() && pinBytes.size <= 16) {
                        try { device.setPin(pinBytes) } catch (_: SecurityException) {}
                    }
                }
                .setNegativeButton(android.R.string.cancel, null)
                .show()
        }
    }

    private fun unregisterBondReceiver() {
        bondReceiver?.let {
            try { reactContext.unregisterReceiver(it) } catch (_: IllegalArgumentException) {}
        }
        bondReceiver = null
        bondingInProgress = false
        clearBondTimeout()
    }

    /** VID is always Suunto's; PID only picks the driver_support row, and any
     * Ambit3-family device (Kailash included — driven internally as an Ambit3
     * Peak, KAILASH-BLE-FINDINGS.md Finding 8) shares device_driver_ambit3.c. */
    private fun guessProductId(name: String?): Int = when {
        name?.startsWith("Traverse Alpha") == true -> 0x002d
        name?.startsWith("Traverse") == true -> 0x002b
        else -> 0x001c // Ambit3 Sport
    }

    // ─── Permissions ─────────────────────────────────────────────────────────

    private fun hasBlePermissions(): Boolean {
        val perms = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        return perms.all { ContextCompat.checkSelfPermission(reactContext, it) == PackageManager.PERMISSION_GRANTED }
    }

    private fun requestBlePermissions() {
        val activity = reactContext.currentActivity as? PermissionAwareActivity
        if (activity == null) {
            pendingPermissionPromise?.let { pendingPermissionPromise = null; it.reject("NO_ACTIVITY", "No active activity") }
            return
        }
        val perms = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            arrayOf(Manifest.permission.BLUETOOTH_SCAN, Manifest.permission.BLUETOOTH_CONNECT)
        } else {
            arrayOf(Manifest.permission.ACCESS_FINE_LOCATION)
        }
        activity.requestPermissions(perms, BLE_PERMISSION_REQUEST_CODE, object : PermissionListener {
            override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, results: IntArray): Boolean {
                if (requestCode != BLE_PERMISSION_REQUEST_CODE) return false
                val granted = results.isNotEmpty() && results.all { it == PackageManager.PERMISSION_GRANTED }
                val promise = pendingPermissionPromise
                pendingPermissionPromise = null
                if (granted && promise != null) {
                    startScan(promise)
                } else {
                    promise?.reject("PERMISSION_DENIED", "Bluetooth permission was not granted")
                }
                return true
            }
        })
    }

    companion object {
        private const val SUUNTO_VID = 0x1493
        // BluetoothStatusCodes.SUCCESS is API 33+; the 4-arg notifyCharacteristicChanged
        // (API 33+) returns an Int status where 0 == success, matching this.
        private const val BluetoothStatusCodesSuccess = 0
    }
}

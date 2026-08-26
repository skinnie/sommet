package com.ambitsyncmodern.usb

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.net.Uri
import android.os.Build
import android.util.Base64
import com.facebook.react.bridge.*
import com.facebook.react.modules.core.DeviceEventManagerModule
import org.json.JSONObject
import java.util.concurrent.Executors

private const val PICK_GPX_REQUEST_CODE = 4242
private const val SAVE_FILE_AS_REQUEST_CODE = 4243

private const val SUUNTO_VID = 0x1493

// PIDs Suunto connus → nom de modèle (source : openambit/src/libambit/device_support.c)
// Utilisé comme source de vérité pour l'affichage : plus fiable que UsbDevice.productName,
// qui dépend de la chaîne iProduct renvoyée par le firmware de la montre (souvent générique).
private val SUUNTO_PID_NAMES = mapOf(
    0x0010 to "Suunto Ambit",           // codename Bluebird
    0x0019 to "Suunto Ambit 2",         // codename Duck
    0x001a to "Suunto Ambit 2 S",       // codename Colibri
    0x001b to "Suunto Ambit 3 Peak",    // codename Emu
    0x001c to "Suunto Ambit 3 Sport",   // codename Finch
    0x001d to "Suunto Ambit 2 R",       // codename Greentit
    0x001e to "Suunto Ambit 3 Run",     // codename Ibisbill
    // Real, 2026-08-08: found missing here the same way it was found missing from
    // tools/write_nav.py's own PRODUCT_IDS dict this same session (confirmed via `lsusb`
    // against a real connected Kailash, "ID 1493:002a Suunto Kailash") - without this entry,
    // `it.productId in SUUNTO_PID_NAMES` below silently excludes a real, working Kailash from
    // both device detection and permission requests (both check membership in this same
    // map), the exact same failure mode ("no Ambit3 on the USB bus" despite the watch being
    // genuinely connected) write_nav.py had. Missing again from the parallel theme-redesign
    // copy this file was merged with (2026-08-08) - re-added, not dropped a second time.
    0x002a to "Suunto Kailash",         // codename Hoopoe
    0x002b to "Suunto Traverse",        // codename Jabiru
    0x002c to "Suunto Ambit 3 Vertical",// codename Kaka
    0x002d to "Suunto Traverse Alpha",  // codename Loon
)

private const val ACTION_USB_PERMISSION = "com.ambitsyncmodern.USB_PERMISSION"

class AmbitUsbModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    companion object {
        // jni_bridge.cpp est compilé dans libappmodules.so (chargé automatiquement
        // par SoLoader pour la New Architecture). Les fonctions JNI sont toujours
        // disponibles ; nativeAmbitInit() retourne false tant que libambit n'est pas intégré.
        const val jniLoaded: Boolean = true

        // Référence vers l'instance vivante du module, pour que MainActivity puisse
        // déclencher un événement JS sans dépendre de l'API ReactContext exacte de
        // ReactActivity (qui varie selon la version RN / New Architecture) — le module
        // a déjà un reactContext garanti valide via son constructeur.
        @Volatile private var activeInstance: AmbitUsbModule? = null

        /**
         * Appelée par MainActivity.onNewIntent() quand la montre est branchée alors
         * que l'app tourne déjà (launchMode="singleTask" -> pas de nouveau onCreate).
         * Le cas "lancement à froid" via ce même intent est géré différemment, côté JS
         * (wasLaunchedViaUsbAttach(), interrogé une fois au montage) : émettre un
         * événement aussi tôt risquerait de survenir avant qu'un listener JS ne soit
         * abonné, et un événement RCTDeviceEventEmitter non écouté est perdu, pas mis
         * en file d'attente.
         */
        fun notifyUsbAttached() {
            activeInstance?.emitUsbAttached()
        }
    }

    // ─── Fonctions JNI (implémentées dans jni_bridge.cpp) ────────────────────
    private external fun nativeAmbitInit(fd: Int, epIn: Int, epOut: Int, vid: Int, pid: Int): Boolean
    private external fun nativeAmbitGetDeviceInfo(): String
    private external fun nativeAmbitGetLogCount(knownDates: Array<String>): Int
    private external fun nativeAmbitGetLogAsGpx(index: Int): String?
    private external fun nativeAmbitMarkReadLogsSynced(): Int
    private external fun nativeAmbitSendSgee(data: ByteArray): Boolean
    private external fun nativeAmbitWriteRoute(
        routeName: String,
        ptLat: DoubleArray, ptLon: DoubleArray, ptAlt: IntArray,
        wptLat: DoubleArray, wptLon: DoubleArray, wptName: Array<String>, wptPointIndex: IntArray,
        distanceM: Int, ascentM: Int, descentM: Int, timestampSec: Long
    ): Boolean
    private external fun nativeAmbitAddPoi(name: String, lat: Double, lon: Double, type: Int): Boolean
    private external fun nativeAmbitReadRegion(address: Long, length: Long): String?
    private external fun nativeAmbitReadPoiListRaw(): String?
    private external fun nativeAmbitReadMemoryMapRaw(): String?
    // Firmware flasher (firmware_flash_android.c). THE ONE WRITE THAT CAN BRICK.
    private external fun nativeAmbitFwEnterBsl(): Boolean
    private external fun nativeAmbitFwReboot(): Boolean
    private external fun nativeAmbitFwStream(header: ByteArray, payload: ByteArray, doCommit: Boolean, resume: Boolean): Boolean
    private external fun nativeAmbitReadDeviceHistoryRaw(): String?
    private external fun nativeAmbitReadDeviceLogRaw(): String?
    private external fun nativeAmbitReadSettingsRaw(): String?
    private external fun nativeAmbitReadPersonalSettings(): String?
    private external fun nativeAmbitWritePersonalSetting(offset: Int, width: Int, value: Int): Boolean
    private external fun nativeAmbitWriteSettingsRaw(data: ByteArray): Boolean
    private external fun nativeAmbitSetDateTime(): Boolean
    private external fun nativeAmbitReadCustomModesRaw(): String?
    private external fun nativeAmbitWriteCustomModesRaw(data: ByteArray): Boolean
    private external fun nativeAmbitWriteRegion(address: Long, data: ByteArray, extent: Int): Boolean
    private external fun nativeAmbitDisconnect()

    // ─── État interne ─────────────────────────────────────────────────────────
    private var currentDevice: UsbDevice? = null
    // Multi-watch switcher (2026-08-16): which attached Suunto to talk to when more than one is
    // plugged in, by its stable Android USB path (UsbDevice.deviceName, e.g. /dev/bus/usb/002/010).
    // null = "first found", the pre-switcher behaviour. Set from JS via selectDevice(); connect()
    // prefers it, falling back to the first match if that watch is no longer attached.
    @Volatile private var selectedDeviceName: String? = null
    // Kept so a firmware re-enumeration reopen can close the dead connection before opening the
    // re-enumerated one (normal connect() left this local; the flasher needs to manage it).
    private var currentConnection: android.hardware.usb.UsbDeviceConnection? = null
    private var pendingConnectPromise: Promise? = null
    private var pendingPickGpxPromise: Promise? = null
    private var pendingSaveAsPromise: Promise? = null
    private var pendingSaveAsSourcePath: String? = null
    private val executor = Executors.newSingleThreadExecutor()

    private val activityEventListener = object : BaseActivityEventListener() {
        override fun onActivityResult(activity: Activity, requestCode: Int, resultCode: Int, data: Intent?) {
            when (requestCode) {
                PICK_GPX_REQUEST_CODE -> {
                    val promise = pendingPickGpxPromise ?: return
                    pendingPickGpxPromise = null

                    val uri: Uri? = if (resultCode == Activity.RESULT_OK) data?.data else null
                    if (uri == null) {
                        promise.reject("GPX_PICK_CANCELLED", "No file selected")
                        return
                    }
                    try {
                        val destFile = java.io.File(reactContext.cacheDir, "picked_route_${System.currentTimeMillis()}.gpx")
                        reactContext.contentResolver.openInputStream(uri)?.use { input ->
                            destFile.outputStream().use { output -> input.copyTo(output) }
                        } ?: throw Exception("Could not open the selected file")
                        promise.resolve(destFile.absolutePath)
                    } catch (e: Exception) {
                        promise.reject("GPX_PICK_FAILED", e.message ?: "Unknown error")
                    }
                }
                SAVE_FILE_AS_REQUEST_CODE -> {
                    val promise = pendingSaveAsPromise ?: return
                    val sourcePath = pendingSaveAsSourcePath
                    pendingSaveAsPromise = null
                    pendingSaveAsSourcePath = null

                    val uri: Uri? = if (resultCode == Activity.RESULT_OK) data?.data else null
                    if (uri == null || sourcePath == null) {
                        promise.reject("SAVE_AS_CANCELLED", "No destination chosen")
                        return
                    }
                    try {
                        reactContext.contentResolver.openOutputStream(uri)?.use { output ->
                            java.io.File(sourcePath).inputStream().use { input -> input.copyTo(output) }
                        } ?: throw Exception("Could not open the chosen destination")
                        promise.resolve(uri.toString())
                    } catch (e: Exception) {
                        promise.reject("SAVE_AS_FAILED", e.message ?: "Unknown error")
                    }
                }
            }
        }
    }

    init {
        reactContext.addActivityEventListener(activityEventListener)
        activeInstance = this
    }

    private fun emitUsbAttached() {
        reactContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit("AmbitUsbAttached", null)
    }

    // ─── wasLaunchedViaUsbAttach() ──────────────────────────────────────────────
    // Interrogé une fois par JS au montage : l'app a-t-elle été lancée à froid par
    // le branchement de la montre (intent-filter USB_DEVICE_ATTACHED du manifest) ?
    @ReactMethod
    fun wasLaunchedViaUsbAttach(promise: Promise) {
        val action = reactContext.currentActivity?.intent?.action
        promise.resolve(action == UsbManager.ACTION_USB_DEVICE_ATTACHED)
    }

    // ─── detectAttachedDeviceType() ─────────────────────────────────────────────
    // v2.3 beta: device_filter.xml now matches both Ambit/Traverse (VID 0x1493)
    // and Garmin (VID 0x091e, GarminModule.kt) — USB_DEVICE_ATTACHED alone no
    // longer means "an Ambit was plugged in". Called before routing to either
    // device's flow. Returns "ambit" | "garmin" | "none" — never throws.
    @ReactMethod
    fun detectAttachedDeviceType(promise: Promise) {
        val usbManager = reactContext.getSystemService(Context.USB_SERVICE) as UsbManager
        val devices = usbManager.deviceList.values
        val ambit = devices.find { it.vendorId == SUUNTO_VID && it.productId in SUUNTO_PID_NAMES }
        if (ambit != null) { promise.resolve("ambit"); return }
        // 2334 = 0x091E, Garmin's VID — kept as a local literal rather than importing
        // GarminModule.kt, matching this project's existing pattern of not creating
        // cross-module dependencies between separate device integrations.
        val garmin = devices.find { it.vendorId == 2334 }
        if (garmin != null) { promise.resolve("garmin"); return }
        promise.resolve("none")
    }

    private val usbPermissionReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != ACTION_USB_PERMISSION) return
            try { reactContext.unregisterReceiver(this) } catch (_: Exception) {}
            val device: UsbDevice? = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                intent.getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
            } else {
                @Suppress("DEPRECATION")
                intent.getParcelableExtra(UsbManager.EXTRA_DEVICE)
            }
            val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
            val promise = pendingConnectPromise ?: return
            pendingConnectPromise = null

            if (!granted || device == null) {
                promise.reject("USB_PERMISSION_DENIED", "USB permission denied by user")
                return
            }
            openDeviceAndInit(device, promise)
        }
    }

    override fun getName() = "AmbitUsbModule"

    // ─── connect() ────────────────────────────────────────────────────────────
    // Détecte l'Ambit, demande la permission USB, initialise la connexion JNI.
    @ReactMethod
    fun connect(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable (libambit not integrated)")
            return
        }
        val usbManager = reactContext.getSystemService(Context.USB_SERVICE) as UsbManager
        val matches = usbManager.deviceList.values.filter { device ->
            device.vendorId == SUUNTO_VID && device.productId in SUUNTO_PID_NAMES
        }
        // Prefer the watch the user picked in the switcher; fall back to the first attached one
        // (unchanged single-watch behaviour) if it's the only one or the selection is stale.
        val ambit = matches.find { it.deviceName == selectedDeviceName } ?: matches.firstOrNull()
        if (ambit == null) {
            promise.reject("AMBIT_NOT_FOUND", "No Suunto watch detected. Check the USB OTG cable.")
            return
        }
        currentDevice = ambit

        if (usbManager.hasPermission(ambit)) {
            openDeviceAndInit(ambit, promise)
            return
        }

        // Demander la permission à l'utilisateur
        pendingConnectPromise = promise
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S)
            PendingIntent.FLAG_MUTABLE else 0
        val permissionIntent = PendingIntent.getBroadcast(
            reactContext, 0, Intent(ACTION_USB_PERMISSION), flags
        )
        val filter = IntentFilter(ACTION_USB_PERMISSION)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            reactContext.registerReceiver(usbPermissionReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            reactContext.registerReceiver(usbPermissionReceiver, filter)
        }
        usbManager.requestPermission(ambit, permissionIntent)
    }

    // Multi-watch switcher (2026-08-16). All attached Suunto watches, so the UI can offer a
    // picker when more than one is plugged in (the desktop app already has this). Each entry's
    // `deviceName` is the stable USB path used by selectDevice()/connect().
    @ReactMethod
    fun listDevices(promise: Promise) {
        val usbManager = reactContext.getSystemService(Context.USB_SERVICE) as UsbManager
        val out = Arguments.createArray()
        for (device in usbManager.deviceList.values) {
            if (device.vendorId != SUUNTO_VID || device.productId !in SUUNTO_PID_NAMES) continue
            val m = Arguments.createMap()
            m.putString("deviceName", device.deviceName)
            m.putInt("productId", device.productId)
            m.putString("name", SUUNTO_PID_NAMES[device.productId] ?: device.productName ?: "Suunto")
            out.pushMap(m)
        }
        promise.resolve(out)
    }

    // Choose which attached watch subsequent connect() calls target. Pass null/"" to clear the
    // choice (back to "first found"). Cheap and synchronous - just records the selection.
    @ReactMethod
    fun selectDevice(deviceName: String?, promise: Promise) {
        selectedDeviceName = if (deviceName.isNullOrEmpty()) null else deviceName
        promise.resolve(true)
    }

    private fun openDeviceAndInit(device: UsbDevice, promise: Promise) {
        val usbManager = reactContext.getSystemService(Context.USB_SERVICE) as UsbManager
        val connection = usbManager.openDevice(device)
        if (connection == null) {
            promise.reject("USB_OPEN_FAILED", "Could not open the USB connection")
            return
        }

        // Trouver l'interface HID (interface 0) et ses endpoints
        val iface = device.getInterface(0)
        connection.claimInterface(iface, true)

        var epIn  = -1
        var epOut = -1
        for (i in 0 until iface.endpointCount) {
            val ep = iface.getEndpoint(i)
            val isInterruptOrBulk = ep.type == UsbConstants.USB_ENDPOINT_XFER_INT ||
                                    ep.type == UsbConstants.USB_ENDPOINT_XFER_BULK
            if (!isInterruptOrBulk) continue
            if (ep.direction == UsbConstants.USB_DIR_IN && epIn  == -1) epIn  = ep.address
            if (ep.direction == UsbConstants.USB_DIR_OUT && epOut == -1) epOut = ep.address
        }

        if (epIn == -1) {
            connection.close()
            promise.reject("USB_NO_ENDPOINT", "No USB IN endpoint found on interface 0")
            return
        }

        val fd  = connection.fileDescriptor
        val vid = device.vendorId
        val pid = device.productId

        val ok = nativeAmbitInit(fd, epIn, epOut, vid, pid)
        if (!ok) {
            connection.close()
            promise.reject("AMBIT_INIT_FAILED", "libambit initialization failed (VID=0x${vid.toString(16)} PID=0x${pid.toString(16)})")
            return
        }

        val deviceName = SUUNTO_PID_NAMES[pid] ?: device.productName ?: "Suunto (0x${pid.toString(16)})"
        val info = Arguments.createMap().apply {
            putString("name", deviceName)
            putInt("vendorId", vid)
            putInt("productId", pid)
        }
        promise.resolve(info)
    }

    // ─── getDeviceInfo() ──────────────────────────────────────────────────────
    // Model/serial/firmware/hardware version (from the watch's own device-info
    // reply, cached at connect time) plus a live battery read. Requires connect()
    // to have already succeeded — nativeAmbitGetDeviceInfo() returns "{}" if not.
    @ReactMethod
    fun getDeviceInfo(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        // Over BLE there is no USB `currentDevice`, but the native g_device
        // (populated by the BLE handshake) still holds model/serial/fw/hw. So don't
        // gate on the USB device: read the native info first, and only treat it as
        // "not connected" when there's neither a USB device nor a native model.
        // Gating on currentDevice here is exactly what made getDeviceInfo() reject
        // over BLE, so isKailash(model==='Hoopoe') was never true and the Kailash
        // branch was skipped (2026-08-09).
        val device = currentDevice
        try {
            val json = JSONObject(nativeAmbitGetDeviceInfo())
            val model = json.optString("model", "")
            if (device == null && model.isEmpty()) {
                promise.reject("AMBIT_NOT_CONNECTED", "Call connect() first")
                return
            }
            val name = when {
                device != null -> SUUNTO_PID_NAMES[device.productId] ?: device.productName ?: "Suunto"
                // BLE: derive a friendly name from the watch's own codename.
                model == "Hoopoe" -> "Suunto Kailash"
                model == "Emu"    -> "Suunto Ambit3 Peak"
                model.isNotEmpty() -> "Suunto $model"
                else -> "Suunto"
            }
            val info = Arguments.createMap().apply {
                putString("name", name)
                putString("model", model)
                putString("serial", json.optString("serial", ""))
                putString("fwVersion", json.optString("fwVersion", ""))
                putString("hwVersion", json.optString("hwVersion", ""))
                putInt("battery", json.optInt("battery", -1))
            }
            promise.resolve(info)
        } catch (e: Exception) {
            promise.reject("DEVICE_INFO_PARSE_FAILED", e.message, e)
        }
    }

    // ─── getLogs() ────────────────────────────────────────────────────────────
    // Retourne un tableau de strings GPX (un par log).
    // Exécuté sur un thread dédié car nativeAmbitGetLogCount() peut bloquer
    // plusieurs minutes lors de la lecture des logs depuis la montre.
    @ReactMethod
    fun getLogs(knownIds: ReadableArray, promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        val knownDates = Array(knownIds.size()) { i -> knownIds.getString(i) ?: "" }
        executor.execute {
            val count = nativeAmbitGetLogCount(knownDates)
            if (count < 0) {
                promise.reject("NOT_CONNECTED", "Watch not connected or not initialized")
                return@execute
            }
            val results = Arguments.createArray()
            for (i in 0 until count) {
                val gpx = nativeAmbitGetLogAsGpx(i)
                if (gpx != null) results.pushString(gpx)
                emitProgress(i + 1, count)
            }
            promise.resolve(results)
        }
    }

    // ─── updateSgee() ─────────────────────────────────────────────────────────
    // path : chemin absolu du fichier SGEE téléchargé sur le téléphone
    @ReactMethod
    fun updateSgee(path: String, promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            val file = java.io.File(path)
            if (!file.exists()) {
                promise.reject("SGEE_FILE_NOT_FOUND", "SGEE file not found: $path")
                return@execute
            }
            val data = file.readBytes()
            val ok = nativeAmbitSendSgee(data)
            if (ok) promise.resolve(true)
            else promise.reject("SGEE_SEND_FAILED", "Failed to send SGEE data")
        }
    }

    // ─── pickGpxFile() ────────────────────────────────────────────────────────
    // Ouvre le sélecteur de fichiers Android (Storage Access Framework) et copie
    // le fichier choisi dans le cache de l'app, pour obtenir un chemin local
    // classique (content:// n'est pas garanti lisible par RNFS.readFile).
    @ReactMethod
    fun pickGpxFile(promise: Promise) {
        val activity = reactContext.currentActivity
        if (activity == null) {
            promise.reject("NO_ACTIVITY", "No active activity")
            return
        }
        pendingPickGpxPromise = promise
        val intent = Intent(Intent.ACTION_OPEN_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = "*/*" // pas de type MIME GPX standard fiable sur tous les file managers
        }
        activity.startActivityForResult(intent, PICK_GPX_REQUEST_CODE)
    }

    // ─── saveFileAs() ─────────────────────────────────────────────────────────
    // Opens the system "Save as" picker (Storage Access Framework) so the user
    // can choose exactly where a downloaded file goes, instead of it always
    // landing silently in Downloads. Defaults to the Downloads folder (via
    // EXTRA_INITIAL_URI) so accepting the picker's default is equivalent to
    // today's saveToDownloads() behavior — the user only needs to browse
    // elsewhere if they actually want to. Added for the Ambit firmware Backup
    // screen (v2.3.3) but generic — any local file can be saved this way.
    @ReactMethod
    fun saveFileAs(sourcePath: String, suggestedName: String, mimeType: String, promise: Promise) {
        val activity = reactContext.currentActivity
        if (activity == null) {
            promise.reject("NO_ACTIVITY", "No active activity")
            return
        }
        pendingSaveAsPromise = promise
        pendingSaveAsSourcePath = sourcePath
        val intent = Intent(Intent.ACTION_CREATE_DOCUMENT).apply {
            addCategory(Intent.CATEGORY_OPENABLE)
            type = mimeType
            putExtra(Intent.EXTRA_TITLE, suggestedName)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                // Standard AOSP DocumentsUI URI for the public Downloads folder —
                // just a starting point for the picker, not a guarantee (some
                // OEM file pickers ignore it), but the common case (stock/AOSP-
                // derived pickers, including BlissOS's) honors it.
                putExtra(
                    android.provider.DocumentsContract.EXTRA_INITIAL_URI,
                    Uri.parse("content://com.android.externalstorage.documents/document/primary:Download")
                )
            }
        }
        activity.startActivityForResult(intent, SAVE_FILE_AS_REQUEST_CODE)
    }

    // ─── writeRoute() ─────────────────────────────────────────────────────────
    // Écrit une route (déjà simplifiée côté JS, <= AMBIT3_MAX_ROUTE_POINTS points)
    // sur la montre. Remplace toute la base de données de navigation ; les POIs
    // sont préservés côté natif (lecture 0x0b24 avant, restauration 0x0b25 après).
    // Non durable : voir device_driver_ambit3.c, ambit3_write_route_to_watch().
    @ReactMethod
    fun writeRoute(route: ReadableMap, promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val name = route.getString("name") ?: "Route"
                val pts = route.getArray("points") ?: throw Exception("points manquant")
                val ptLat = DoubleArray(pts.size())
                val ptLon = DoubleArray(pts.size())
                val ptAlt = IntArray(pts.size())
                for (i in 0 until pts.size()) {
                    val p = pts.getMap(i)!!
                    ptLat[i] = p.getDouble("lat")
                    ptLon[i] = p.getDouble("lon")
                    ptAlt[i] = if (p.hasKey("alt") && !p.isNull("alt")) p.getInt("alt") else 30000 // AMBIT3_ALTITUDE_NONE
                }

                val wpts = route.getArray("waypoints") ?: throw Exception("waypoints manquant")
                val wptLat = DoubleArray(wpts.size())
                val wptLon = DoubleArray(wpts.size())
                val wptName = Array(wpts.size()) { "" }
                val wptPointIndex = IntArray(wpts.size())
                for (i in 0 until wpts.size()) {
                    val w = wpts.getMap(i)!!
                    wptLat[i] = w.getDouble("lat")
                    wptLon[i] = w.getDouble("lon")
                    wptName[i] = w.getString("name") ?: ""
                    wptPointIndex[i] = w.getInt("pointIndex")
                }

                val ok = nativeAmbitWriteRoute(
                    name,
                    ptLat, ptLon, ptAlt,
                    wptLat, wptLon, wptName, wptPointIndex,
                    route.getInt("distanceM"), route.getInt("ascentM"), route.getInt("descentM"),
                    route.getDouble("timestampSec").toLong()
                )
                if (ok) promise.resolve(true)
                else promise.reject("ROUTE_WRITE_FAILED", "Route write failed (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("ROUTE_WRITE_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // ─── addPoi() ─────────────────────────────────────────────────────────────
    // Ajoute un POI sur la montre en préservant ceux déjà présents. Contrairement
    // à writeRoute(), ne touche pas aux régions Waypoints/Routes de la flash.
    // `type` is the Ambit POI type byte 0-17 (the icon the watch shows); React Native passes
    // JS numbers as Double, so it arrives as Double and is narrowed to Int for the JNI call.
    @ReactMethod
    fun addPoi(name: String, lat: Double, lon: Double, type: Double, promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        if (name.isBlank()) {
            promise.reject("POI_INVALID_NAME", "POI name cannot be empty")
            return
        }
        executor.execute {
            try {
                val ok = nativeAmbitAddPoi(name.trim(), lat, lon, type.toInt())
                if (ok) promise.resolve(true)
                else promise.reject("POI_ADD_FAILED", "Failed to add POI (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("POI_ADD_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // ─── readRegion() / readPoiListRaw() ───────────────────────────────────────
    // Lecture seule. Renvoie du base64 ; le décodage (structures routes/waypoints,
    // entrées SBEM0102 des POIs) se fait côté TS — voir RouteReader.ts / PoiService.ts.
    @ReactMethod
    fun readRegion(address: Double, length: Double, promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val b64 = nativeAmbitReadRegion(address.toLong(), length.toLong())
                if (b64 != null) promise.resolve(b64)
                else promise.reject("REGION_READ_FAILED", "Failed to read region (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("REGION_READ_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    @ReactMethod
    fun readPoiListRaw(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val b64 = nativeAmbitReadPoiListRaw()
                if (b64 != null) promise.resolve(b64)
                else promise.reject("POI_READ_FAILED", "Failed to read POI list (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("POI_READ_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // Per-device navigation port (2026-08-15): the raw 0x0b21 memory-map reply (region table
    // with each region's own start+size). Parsed in TS (MemoryMap.ts) like write_nav.py's
    // read_memory_map(), so routes/POIs read from the addresses the watch declares - a
    // Traverse's bases differ from the Ambit3 Peak's. Read-only.
    @ReactMethod
    fun readMemoryMapRaw(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val b64 = nativeAmbitReadMemoryMapRaw()
                if (b64 != null) promise.resolve(b64)
                else promise.reject("MEMMAP_READ_FAILED", "Failed to read memory map (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("MEMMAP_READ_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // ─── Firmware flasher ─────────────────────────────────────────────────────
    // THE ONE WRITE THAT CAN BRICK. Faithful port of the desktop's proven firmware_write.py.
    // The live flash is only reachable via firmwareFlash(..., confirm=true) - the UI gates it
    // behind an explicit confirmation for a supervised session. NOT hardware-tested on Android.
    //
    // Entering the bootloader (0x0202) and the final reboot (0x0200) make the watch
    // re-enumerate on USB; only this Kotlin layer (UsbManager) can re-acquire the device, so
    // it orchestrates the native steps across those re-enumerations.

    private fun emitFwPhase(phase: String, message: String, extra: WritableMap? = null) {
        val params = (extra ?: Arguments.createMap()).apply {
            putString("phase", phase)
            putString("message", message)
        }
        reactContext.getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit("AmbitFirmwarePhase", params)
    }

    /** Opens a device and inits libambit on it, closing any previously-tracked connection.
     * Returns true on success. Used both by connect() and the firmware re-enumeration reopen. */
    private fun openAndInitDevice(device: UsbDevice): Boolean {
        val usbManager = reactContext.getSystemService(Context.USB_SERVICE) as UsbManager
        if (!usbManager.hasPermission(device)) return false
        val connection = usbManager.openDevice(device) ?: return false
        val iface = device.getInterface(0)
        connection.claimInterface(iface, true)
        var epIn = -1; var epOut = -1
        for (i in 0 until iface.endpointCount) {
            val ep = iface.getEndpoint(i)
            val ib = ep.type == UsbConstants.USB_ENDPOINT_XFER_INT || ep.type == UsbConstants.USB_ENDPOINT_XFER_BULK
            if (!ib) continue
            if (ep.direction == UsbConstants.USB_DIR_IN && epIn == -1) epIn = ep.address
            if (ep.direction == UsbConstants.USB_DIR_OUT && epOut == -1) epOut = ep.address
        }
        if (epIn == -1) { connection.close(); return false }
        if (!nativeAmbitInit(connection.fileDescriptor, epIn, epOut, device.vendorId, device.productId)) {
            connection.close(); return false
        }
        try { currentConnection?.close() } catch (_: Exception) {}
        currentConnection = connection
        currentDevice = device
        return true
    }

    /** After a 0x0202/0x0200 re-enumeration, wait for the same VID:PID watch to reappear as a
     * DIFFERENT USB instance (the re-enumerated device gets a new /dev path) and reopen it. The
     * device-name check is essential: right after 0x0202 the OLD app-mode instance lingers in
     * deviceList for a moment, and opening it (same name) makes the next command fail - the real
     * bug seen 2026-08-15. So we first wait for the device to DETACH, then for the new instance. */
    private fun reopenReenumerated(productId: Int, timeoutMs: Long): Boolean {
        val usbManager = reactContext.getSystemService(Context.USB_SERVICE) as UsbManager
        val oldName = currentDevice?.deviceName
        if (jniLoaded) nativeAmbitDisconnect()
        try { currentConnection?.close() } catch (_: Exception) {}
        currentConnection = null
        val deadline = System.currentTimeMillis() + timeoutMs
        // Phase 1: wait for the old instance to disappear (re-enumeration actually started).
        while (System.currentTimeMillis() < deadline) {
            val stillThere = usbManager.deviceList.values.any {
                it.vendorId == SUUNTO_VID && it.productId == productId && it.deviceName == oldName
            }
            if (!stillThere) break
            Thread.sleep(300)
        }
        // Phase 2: wait for the re-enumerated instance (a new /dev name) and open it.
        while (System.currentTimeMillis() < deadline) {
            val dev = usbManager.deviceList.values.firstOrNull {
                it.vendorId == SUUNTO_VID && it.productId == productId && it.deviceName != oldName
            }
            if (dev != null && usbManager.hasPermission(dev)) {
                Thread.sleep(400)  // small settle after attach before opening
                if (openAndInitDevice(dev)) return true
            }
            Thread.sleep(400)
        }
        return false
    }

    /** Reads the .sfi at `path` and checks it against the connected watch, WITHOUT sending
     * anything to the watch (the desktop's safe "dry connection"). Resolves a plan object. */
    @ReactMethod
    fun firmwarePreflight(path: String, promise: Promise) {
        if (!jniLoaded) { promise.reject("JNI_NOT_LOADED", "Native library unavailable"); return }
        executor.execute {
            try {
                val bytes = java.io.File(path).readBytes()
                if (bytes.size <= 32 || !(bytes[0]=='S'.code.toByte() && bytes[1]=='F'.code.toByte() &&
                        bytes[2]=='I'.code.toByte() && bytes[3]=='2'.code.toByte())) {
                    promise.reject("FW_BAD_FILE", "Not an SFI2ST firmware container"); return@execute
                }
                val payloadLen = bytes.size - 32
                val chunks = (payloadLen + 511) / 512
                val infoJson = nativeAmbitGetDeviceInfo()
                val out = Arguments.createMap().apply {
                    putString("deviceInfoJson", infoJson)
                    putInt("headerLen", 32)
                    putInt("payloadLen", payloadLen)
                    putInt("chunks", chunks)
                }
                promise.resolve(out)
            } catch (e: Exception) {
                promise.reject("FW_PREFLIGHT_ERROR", e.message ?: "preflight failed")
            }
        }
    }

    /** THE LIVE FLASH. `confirm` MUST be true or it refuses. `commit=false` streams the whole
     * image but stops before the irreversible 0x0e03 (recoverable - watch stays in BSL). The
     * caller (Firmware screen) only passes confirm=true after an explicit user confirmation. */
    @ReactMethod
    fun firmwareFlash(path: String, commit: Boolean, confirm: Boolean, promise: Promise) {
        if (!jniLoaded) { promise.reject("JNI_NOT_LOADED", "Native library unavailable"); return }
        if (!confirm) { promise.reject("FW_NOT_CONFIRMED", "firmwareFlash requires explicit confirm=true"); return }
        val pid = currentDevice?.productId
        if (pid == null) { promise.reject("FW_NOT_CONNECTED", "connect to the watch first"); return }
        executor.execute {
            try {
                val bytes = java.io.File(path).readBytes()
                val header = bytes.copyOfRange(0, 32)
                val payload = bytes.copyOfRange(32, bytes.size)

                // A watch already in BSL (an interrupted earlier transfer) reports model "BSL".
                // Then we skip 0x0202 + the re-enumeration and re-stream from offset 0 (resume),
                // exactly as SuuntoLink does - see the resumefirmwarekailash capture.
                val alreadyBsl = nativeAmbitGetDeviceInfo().contains("\"model\":\"BSL\"")

                if (alreadyBsl) {
                    emitFwPhase("resume_bsl", "Watch already in bootloader — resuming from the start…")
                } else {
                    emitFwPhase("enter_bsl", "Entering bootloader (0x0202)…")
                    if (!nativeAmbitFwEnterBsl()) { promise.reject("FW_BSL_FAILED", "0x0202 enter-BSL failed"); return@execute }
                    emitFwPhase("reopen_bsl", "Waiting for the watch to re-enumerate in BSL…")
                    if (!reopenReenumerated(pid, 40000)) {
                        promise.reject("FW_REOPEN_BSL_FAILED", "watch did not reappear in BSL (re-grant USB permission if prompted)")
                        return@execute
                    }
                }

                emitFwPhase("streaming", if (commit) "Flashing… (do not disconnect)" else "Streaming (stream-only, will not commit)…")
                if (!nativeAmbitFwStream(header, payload, commit, alreadyBsl)) {
                    promise.reject("FW_STREAM_FAILED", "streaming failed - watch is in BSL, restartable")
                    return@execute
                }

                if (!commit) {
                    emitFwPhase("stream_only_done", "Stream complete, NOT committed. Watch is in BSL (recoverable).")
                    promise.resolve(Arguments.createMap().apply { putBoolean("committed", false); putBoolean("inBsl", true) })
                    return@execute
                }

                emitFwPhase("reboot", "Rebooting to the application (0x0200)…")
                nativeAmbitFwReboot()
                val back = reopenReenumerated(pid, 40000)
                val info = if (back) nativeAmbitGetDeviceInfo() else "{}"
                emitFwPhase("done", if (back) "Flash complete." else "Flashed; reboot not yet confirmed.")
                promise.resolve(Arguments.createMap().apply {
                    putBoolean("committed", true); putBoolean("rebooted", back); putString("deviceInfoJson", info)
                })
            } catch (e: Exception) {
                promise.reject("FW_FLASH_ERROR", e.message ?: "flash failed")
            }
        }
    }

    // Real, 2026-08-08: Kailash's own sml.DeviceHistory (visited cities/countries, travel
    // stats, plus a real activity-mode logbook bundled in the same reply) via the same
    // 0x1200 "object by identifier" query write_nav.py's own CMD_LOG_HEADERS already uses -
    // see ambit3_read_object_by_id_raw()'s own comment (device_driver_ambit3.c) for where
    // entry 0x67 was found. Decoding happens in TS (KailashHistoryReader.ts), mirroring the
    // companion research project's tools/kailash_history.py.
    @ReactMethod
    fun readDeviceHistoryRaw(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val b64 = nativeAmbitReadDeviceHistoryRaw()
                if (b64 != null) promise.resolve(b64)
                else promise.reject("HISTORY_READ_FAILED", "Failed to read device history (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("HISTORY_READ_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // Kailash test hook (2026-08-09). Reads sml.DeviceLog (entry 0x53) — the ephemeral
    // per-activity GPS sample store — to confirm KAILASH-BLE-FINDINGS.md Finding 7 live
    // over BLE. Same native path as readDeviceHistoryRaw, different entry id.
    @ReactMethod
    fun readDeviceLogRaw(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val b64 = nativeAmbitReadDeviceLogRaw()
                if (b64 != null) promise.resolve(b64)
                else promise.reject("DEVICELOG_READ_FAILED", "Failed to read device log (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("DEVICELOG_READ_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // Real, 2026-08-08 ("Settings on ambit 3 - if they are already cracked to be changed by
    // cable, we will need to build a UI for it"). Real, read-only 0x1100 query - the same
    // sml.DeviceSettings tree tools/settings_write.py already reads on the desktop side.
    // Decoding happens in TS (AmbitSettingsReader.ts).
    @ReactMethod
    fun readSettingsRaw(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val b64 = nativeAmbitReadSettingsRaw()
                if (b64 != null) promise.resolve(b64)
                else promise.reject("SETTINGS_READ_FAILED", "Failed to read settings (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("SETTINGS_READ_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // Ambit 1 / Ambit 2 family (USB-only): the legacy personal-settings read, returned as
    // JSON (see nativeAmbitReadPersonalSettings / AmbitPersonalSettingsReader.ts). Read-only.
    @ReactMethod
    fun readPersonalSettings(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val json = nativeAmbitReadPersonalSettings()
                if (json != null) promise.resolve(json)
                else promise.reject("PERSONAL_SETTINGS_READ_FAILED", "Failed to read personal settings (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("PERSONAL_SETTINGS_READ_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // Ambit 1/2 (Bluebird) legacy personal-settings WRITE (0x0b01), reverse-engineered from a
    // real SuuntoLink<->Ambit2 USB capture (2026-08-26). Read-modify-write of one field: the
    // native side reads the whole struct (0x0b00), patches `value` at `offset` (`width` 1 or 2,
    // little-endian), and writes it back at the device's own length. See AmbitPersonalSettings-
    // Writer.ts for the field offset table. Guarded to the Bluebird family natively.
    @ReactMethod
    fun writePersonalSetting(offset: Int, width: Int, value: Int, promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val ok = nativeAmbitWritePersonalSetting(offset, width, value)
                if (ok) promise.resolve(true)
                else promise.reject("PERSONAL_SETTINGS_WRITE_FAILED", "Failed to write personal setting (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("PERSONAL_SETTINGS_WRITE_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // Real, hardware-confirmed write, 2026-08-08 (see ambit3_write_settings_raw()'s own
    // comment in device_driver_ambit3.c: André confirmed on a real watch's own screen that
    // this exact mechanism visibly switched the display Light -> Dark). `dataBase64` must
    // be the *entire* settings blob (read first, patch one field, send the whole thing
    // back) - matching how tools/settings_write.py's own write_one() and the real
    // SuuntoLink reference client both work.
    @ReactMethod
    fun writeSettingsRaw(dataBase64: String, promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val data = Base64.decode(dataBase64, Base64.DEFAULT)
                val ok = nativeAmbitWriteSettingsRaw(data)
                if (ok) promise.resolve(true)
                else promise.reject("SETTINGS_WRITE_FAILED", "Failed to write settings (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("SETTINGS_WRITE_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // Real, 2026-08-10 ("I connected the kailash via usb... it didn't sync time... is this
    // function implemented in our app? if not implement it"). Writes the phone's own current
    // local time to the watch - for a cable-connected Ambit3-family watch this is the plain,
    // MovesLink-confirmed 0x0300/0x0302 pair; for a BLE-connected Kailash it's a different,
    // real mechanism found in the 7R app's own BLE captures (single-entry SBEM0102 pushes via
    // command 0x1201 - see device_driver_ambit3.c's kailash_ble_time_sync() for the full
    // evidence). Which path runs is decided inside libambit itself (date_time_set()'s own
    // transport/product_id check), not here.
    // ─── markReadLogsSynced() ──────────────────────────────────────────────────
    // Experimental "mark synced workouts as synced" toggle (OFF by default). Marks every move
    // read this session (the native g_log_dates cache from getLogs) synced on the watch via
    // command 0x1201, so the Suunto app / SuuntoLink don't duplicate them - tradeoff: the move
    // can't be re-retrieved from the watch afterwards. The TS layer (MarkSynced.ts) decides
    // whether the connected watch SUPPORTS this (Ambit3 GEN4 fw only, mirroring the desktop
    // mark_synced.py guard) and only calls it then. Marking all cached moves natively (rather
    // than by a caller index) keeps it tied to exactly what was read. Resolves the number of
    // moves marked. Shared by USB and BLE - both act on the same native g_device.
    @ReactMethod
    fun markReadLogsSynced(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val marked = nativeAmbitMarkReadLogsSynced()
                if (marked >= 0) promise.resolve(marked)
                else promise.reject("MARK_SYNCED_FAILED",
                    "Watch not initialized for mark-synced (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("MARK_SYNCED_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    @ReactMethod
    fun setDateTime(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val ok = nativeAmbitSetDateTime()
                if (ok) promise.resolve(true)
                else promise.reject("TIME_SYNC_FAILED", "Failed to set watch clock (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("TIME_SYNC_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // Real, 2026-08-08. Real, read-only 0x0b17 flash read of the 12288-byte CustomModes
    // region (sport modes) - the same region tools/custom_modes.py already reads.
    // Decoding happens in TS (CustomModesReader.ts).
    @ReactMethod
    fun readCustomModesRaw(promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val b64 = nativeAmbitReadCustomModesRaw()
                if (b64 != null) promise.resolve(b64)
                else promise.reject("CUSTOMMODES_READ_FAILED", "Failed to read CustomModes (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("CUSTOMMODES_READ_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // Real mechanism, NOT yet hardware-confirmed on Android specifically - see
    // ambit3_write_custom_modes_raw()'s own detailed comment in device_driver_ambit3.c.
    // `dataBase64` must be the *entire* 12288-byte CustomModes region (read first, patch
    // only the specific bytes to change, send the whole thing back).
    @ReactMethod
    fun writeCustomModesRaw(dataBase64: String, promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val data = Base64.decode(dataBase64, Base64.DEFAULT)
                val ok = nativeAmbitWriteCustomModesRaw(data)
                if (ok) promise.resolve(true)
                else promise.reject("CUSTOMMODES_WRITE_FAILED", "Failed to write CustomModes (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("CUSTOMMODES_WRITE_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // EXPERIMENTAL (2026-08-14) - generic region write for App-Zone / Training-program. Writes
    // the first `extent` bytes of the base64 image at `address`, finalized with the same
    // used-extent hash + data-tail as writeCustomModesRaw (no commit). The image + extent are
    // built and proven byte-exact in TS (see the per-region builders); this only marshals.
    @ReactMethod
    fun writeRegion(address: Double, dataBase64: String, extent: Double, promise: Promise) {
        if (!jniLoaded) {
            promise.reject("JNI_NOT_LOADED", "Native library unavailable")
            return
        }
        executor.execute {
            try {
                val data = Base64.decode(dataBase64, Base64.DEFAULT)
                val ok = nativeAmbitWriteRegion(address.toLong(), data, extent.toInt())
                if (ok) promise.resolve(true)
                else promise.reject("REGION_WRITE_FAILED", "Failed to write region (see logcat AmbitJNI)")
            } catch (e: Exception) {
                promise.reject("REGION_WRITE_ERROR", e.message ?: "Unknown error")
            }
        }
    }

    // ─── shareFile() ──────────────────────────────────────────────────────────
    // Partage un fichier local vers d'autres apps via le share sheet Android.
    // Utilise FileProvider pour générer une URI content:// (requis Android 7+).
    @ReactMethod
    fun shareFile(filePath: String, mimeType: String, promise: Promise) {
        try {
            val file = java.io.File(filePath)
            if (!file.exists()) {
                promise.reject("FILE_NOT_FOUND", "File not found: $filePath")
                return
            }
            val uri = androidx.core.content.FileProvider.getUriForFile(
                reactApplicationContext,
                "${reactApplicationContext.packageName}.fileprovider",
                file
            )
            val intent = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
                type = mimeType
                putExtra(android.content.Intent.EXTRA_STREAM, uri)
                addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
            }
            val chooser = android.content.Intent.createChooser(intent, "Partager GPX")
            chooser.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
            reactApplicationContext.startActivity(chooser)
            promise.resolve(null)
        } catch (e: Exception) {
            promise.reject("SHARE_ERROR", e.message ?: "Unknown error")
        }
    }

    // ─── saveToDownloads() ────────────────────────────────────────────────────
    // Copie le fichier dans le dossier Téléchargements du téléphone.
    // API 29+ : MediaStore (aucune permission requise).
    // API 28  : copie directe (WRITE_EXTERNAL_STORAGE requis).
    @ReactMethod
    fun saveToDownloads(filePath: String, fileName: String, mimeType: String, promise: Promise) {
        try {
            val file = java.io.File(filePath)
            if (!file.exists()) {
                promise.reject("FILE_NOT_FOUND", "File not found: $filePath")
                return
            }
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.Q) {
                val values = android.content.ContentValues().apply {
                    put(android.provider.MediaStore.Downloads.DISPLAY_NAME, fileName)
                    put(android.provider.MediaStore.Downloads.MIME_TYPE, mimeType)
                    put(android.provider.MediaStore.Downloads.IS_PENDING, 1)
                }
                val resolver = reactApplicationContext.contentResolver
                val uri = resolver.insert(android.provider.MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                    ?: throw Exception("Impossible de créer l'entrée MediaStore")
                resolver.openOutputStream(uri)?.use { os -> file.inputStream().copyTo(os) }
                values.clear()
                values.put(android.provider.MediaStore.Downloads.IS_PENDING, 0)
                resolver.update(uri, values, null, null)
            } else {
                val downloadsDir = android.os.Environment.getExternalStoragePublicDirectory(
                    android.os.Environment.DIRECTORY_DOWNLOADS
                )
                downloadsDir.mkdirs()
                file.copyTo(java.io.File(downloadsDir, fileName), overwrite = true)
            }
            promise.resolve(null)
        } catch (e: Exception) {
            promise.reject("SAVE_ERROR", e.message ?: "Unknown error")
        }
    }

    // ─── disconnect() ─────────────────────────────────────────────────────────
    @ReactMethod
    fun disconnect(promise: Promise) {
        if (jniLoaded) nativeAmbitDisconnect()
        currentDevice = null
        promise.resolve(true)
    }

    // ─── Événements de progression vers React Native ──────────────────────────
    private fun emitProgress(current: Int, total: Int) {
        val params = Arguments.createMap().apply {
            putInt("current", current)
            putInt("total", total)
        }
        reactContext
            .getJSModule(DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit("AmbitSyncProgress", params)
    }

    override fun onCatalystInstanceDestroy() {
        super.onCatalystInstanceDestroy()
        if (activeInstance === this) activeInstance = null
        try { reactContext.unregisterReceiver(usbPermissionReceiver) } catch (_: Exception) {}
        executor.execute { if (jniLoaded) nativeAmbitDisconnect() }
        executor.shutdown()
    }
}

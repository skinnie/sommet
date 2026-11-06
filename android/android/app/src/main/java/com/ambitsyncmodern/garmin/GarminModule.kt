package com.ambitsyncmodern.garmin

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.net.Uri
import android.os.Build
import android.os.storage.StorageManager
import android.os.storage.StorageVolume
import androidx.documentfile.provider.DocumentFile
import com.facebook.react.bridge.*
import me.jahnen.libaums.core.UsbMassStorageDevice
import me.jahnen.libaums.core.fs.UsbFile
import me.jahnen.libaums.core.fs.UsbFileInputStream
import me.jahnen.libaums.core.fs.UsbFileOutputStream
import org.xmlpull.v1.XmlPullParser
import org.xmlpull.v1.XmlPullParserFactory
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors

/*
 * v2.3 beta — Garmin USB Mass Storage support. See GARMIN_USB_IMPORT_SPEC.md for the full
 * research trail (real hardware: André's eTrex 30, 2026-08-07).
 *
 * Garmin devices work completely differently from the Ambit3 (plain files on a FAT
 * filesystem, not an NSP flash protocol), so this is a wholly separate module, not an
 * extension of AmbitUsbModule.kt — matches this project's "one file per format" convention.
 *
 * Uses libaums (me.jahnen.libaums:core, Apache 2.0) for userspace USB Mass Storage access —
 * a real, maintained library, not hand-rolled SCSI/FAT. Confirmed necessary rather than
 * relying on OS auto-mount: stock Android has no built-in document provider for arbitrary
 * USB-OTG mass storage devices (this is exactly the gap libaums exists to fill), unlike the
 * desktop-Linux automount (`udisks2`) used during initial research, which doesn't generalize
 * to Android.
 */

private const val GARMIN_VID = 2334 // 0x091E, confirmed on real hardware — see device_filter.xml
private const val ACTION_GARMIN_USB_PERMISSION = "com.ambitsyncmodern.GARMIN_USB_PERMISSION"

// Edge 500 on the tablet (2026-11-06): Android mounts the Edge's drive itself (vold: "public:8,0
// ... fsLabel=GARMIN", the "Garmin USB drive" notification), and the drive is a FAT16 volume with
// no partition table, which libaums can't open ("Found partition table MasterBootRecordCreator",
// then no volume). Same situation as the Bryton (BrytonUsbModule.kt), same fix: the user grants the
// GARMIN drive once through the Storage Access Framework and we read/write it with DocumentFile.
// Used only when Android has a mounted GARMIN volume AND its GarminDevice.xml says Edge, so the
// eTrex (never auto-mounted, libaums) is untouched.
private const val SAF_PREFS = "garmin_saf"
private const val SAF_KEY_TREE = "treeUri"
private const val REQUEST_GARMIN_TREE = 0xE500
private const val SAF_VOLUME_INDEX = 100

data class GarminVolumeInfo(
    val volumeIndex: Int,
    val hasGarminDeviceXml: Boolean,
    val model: String?,
    val firmwareVersion: String?,
    val partNumber: String?,
    val activityPath: String?, // resolved from GarminDevice.xml's GPSData/OutputFromUnit entry
    // Edge 500 & co. (2026-11-05): FIT rides, from the FIT_TYPE_4 OutputFromUnit entry
    // ("Garmin/Activities"). The eTrex has no FIT_TYPE_4, so this stays null there.
    val fitActivityPath: String?,
    // A mass-storage Garmin Edge (Edge 500/510/800/810): GarminDevice.xml's Description starts
    // with "Edge" - same rule as the desktop's tools/mtp_import.py _garmin_mass_model().
    val isEdge: Boolean,
)

class GarminModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext), ActivityEventListener {

    init { reactContext.addActivityEventListener(this) }

    private val executor = Executors.newSingleThreadExecutor()
    private var pendingConnectPromise: Promise? = null

    // Kept open between calls so listActivityFiles()/readActivityFile() can reuse the same
    // mounted volumes without re-scanning USB every time. Closed explicitly by disconnect().
    private var openDevices: List<UsbMassStorageDevice> = emptyList()
    private var volumeInfos: List<GarminVolumeInfo> = emptyList()

    override fun getName() = "GarminModule"

    // Required by NativeEventEmitter on the JS side (without them React Native logs a warning at
    // every start). Events are sent through RCTDeviceEventEmitter, so there's nothing to track.
    @ReactMethod fun addListener(eventName: String) {}
    @ReactMethod fun removeListeners(count: Int) {}

    private val usbPermissionReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            if (intent.action != ACTION_GARMIN_USB_PERMISSION) return
            try { reactContext.unregisterReceiver(this) } catch (_: Exception) {}
            val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
            val promise = pendingConnectPromise ?: return
            pendingConnectPromise = null
            if (!granted) {
                promise.reject("USB_PERMISSION_DENIED", "USB permission denied by user")
                return
            }
            scanVolumes(promise)
        }
    }

    // ─── connect() ───────────────────────────────────────────────────────────
    // Finds a Garmin mass-storage device, requests permission if needed, mounts
    // every volume it exposes (internal memory + SD card show up as separate
    // volumes — see GARMIN_USB_IMPORT_SPEC.md's multi-volume note), and parses
    // each one's GarminDevice.xml if present.
    @ReactMethod
    fun connect(promise: Promise) {
        if (garminStorageVolume() != null) { connectSaf(promise); return }
        val usbManager = reactContext.getSystemService(Context.USB_SERVICE) as UsbManager
        val garmin: UsbDevice? = usbManager.deviceList.values.find { it.vendorId == GARMIN_VID }
        if (garmin == null) {
            promise.reject("GARMIN_NOT_FOUND", "No Garmin device detected. Check the USB cable and that the device is powered on.")
            return
        }

        if (usbManager.hasPermission(garmin)) {
            scanVolumes(promise)
            return
        }

        pendingConnectPromise = promise
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
        val permissionIntent = PendingIntent.getBroadcast(
            reactContext, 0, Intent(ACTION_GARMIN_USB_PERMISSION), flags
        )
        val filter = IntentFilter(ACTION_GARMIN_USB_PERMISSION)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            reactContext.registerReceiver(usbPermissionReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        } else {
            reactContext.registerReceiver(usbPermissionReceiver, filter)
        }
        usbManager.requestPermission(garmin, permissionIntent)
    }

    // Real-world reference (sportablet.com's Garmin-connection tutorial, via the
    // decompiled apps' own author): a Garmin handheld can take up to ~40 seconds
    // to finish mounting after the USB link comes up — Android itself is busy
    // scanning the new filesystem for media in that window. A single immediate
    // enumeration attempt, as this originally did, fails on a device that just
    // hasn't finished mounting yet, indistinguishable from "not a mass storage
    // device at all". Retry instead of failing fast.
    private val MOUNT_RETRY_INTERVAL_MS = 3000L
    private val MOUNT_RETRY_BUDGET_MS = 45000L

    private fun scanVolumes(promise: Promise) {
        executor.execute {
            closeAllDevices() // in case of a stale previous connect()
            val deadline = System.currentTimeMillis() + MOUNT_RETRY_BUDGET_MS
            var attempt = 0
            while (true) {
                attempt++
                val outcome = tryScanVolumesOnce()
                when (outcome) {
                    is ScanOutcome.Success -> {
                        openDevices = outcome.opened
                        volumeInfos = outcome.infos
                        mainResolve(promise, buildScanResult(outcome.infos))
                        return@execute
                    }
                    is ScanOutcome.NotFoundYet -> {
                        if (System.currentTimeMillis() >= deadline) {
                            mainReject(promise, outcome.code, outcome.message +
                                " (gave up after ${MOUNT_RETRY_BUDGET_MS / 1000}s — Garmin devices can be slow " +
                                "to mount; also check the device is set to USB Mass Storage mode under " +
                                "Setup > Interface, and that the USB connection can supply enough power)")
                            return@execute
                        }
                        emitMountWaiting(attempt, (deadline - System.currentTimeMillis()) / 1000)
                        Thread.sleep(MOUNT_RETRY_INTERVAL_MS)
                    }
                    is ScanOutcome.HardFailure -> {
                        mainReject(promise, outcome.code, outcome.message)
                        return@execute
                    }
                }
            }
        }
    }

    private sealed class ScanOutcome {
        data class Success(val opened: List<UsbMassStorageDevice>, val infos: List<GarminVolumeInfo>) : ScanOutcome()
        data class NotFoundYet(val code: String, val message: String) : ScanOutcome()
        data class HardFailure(val code: String, val message: String) : ScanOutcome()
    }

    private fun tryScanVolumesOnce(): ScanOutcome {
        return try {
            val allDevices = UsbMassStorageDevice.getMassStorageDevices(reactContext)
            val garminDevices = allDevices.filter { it.usbDevice.vendorId == GARMIN_VID }
            if (garminDevices.isEmpty()) {
                return ScanOutcome.NotFoundYet("GARMIN_NOT_FOUND",
                    "No Garmin mass-storage device found yet — still waiting for it to finish mounting")
            }

            val opened = mutableListOf<UsbMassStorageDevice>()
            val infos = mutableListOf<GarminVolumeInfo>()
            var volumeIndex = 0

            for (device in garminDevices) {
                try {
                    device.init()
                } catch (e: Exception) {
                    continue // this LUN/partition failed to init — skip it, don't fail the whole connect
                }
                opened.add(device)
                for (partition in device.partitions) {
                    val root = partition.fileSystem.rootDirectory
                    val garminDir = findChildCaseInsensitive(root, "Garmin")
                    val descriptor = garminDir?.let { findChildCaseInsensitive(it, "GarminDevice.xml") }
                    val parsed = descriptor?.let { parseGarminDeviceXml(readUsbFileFully(it)) }
                    infos.add(
                        GarminVolumeInfo(
                            volumeIndex = volumeIndex,
                            hasGarminDeviceXml = descriptor != null,
                            model = parsed?.model,
                            firmwareVersion = parsed?.firmwareVersion,
                            partNumber = parsed?.partNumber,
                            activityPath = parsed?.activityPath,
                            fitActivityPath = parsed?.fitActivityPath,
                            isEdge = parsed?.model?.trim()?.startsWith("edge", ignoreCase = true) == true,
                        )
                    )
                    volumeIndex++
                }
            }

            if (infos.isEmpty()) {
                closeDevicesList(opened)
                return ScanOutcome.NotFoundYet("GARMIN_NO_VOLUMES",
                    "Garmin device detected but no readable volumes yet — still waiting for it to finish mounting")
            }

            ScanOutcome.Success(opened, infos)
        } catch (e: Exception) {
            ScanOutcome.HardFailure("GARMIN_SCAN_FAILED", e.message ?: "Unknown error scanning Garmin volumes")
        }
    }

    private fun emitMountWaiting(attempt: Int, secondsLeft: Long) {
        val params = Arguments.createMap().apply {
            putInt("attempt", attempt)
            putInt("secondsLeft", secondsLeft.toInt())
        }
        reactContext
            .getJSModule(com.facebook.react.modules.core.DeviceEventManagerModule.RCTDeviceEventEmitter::class.java)
            .emit("GarminMountWaiting", params)
    }

    private fun buildScanResult(infos: List<GarminVolumeInfo>): WritableMap {
        val result = Arguments.createMap()
        val volumesArray = Arguments.createArray()
        for (info in infos) {
            val m = Arguments.createMap()
            m.putInt("volumeIndex", info.volumeIndex)
            m.putBoolean("hasGarminDeviceXml", info.hasGarminDeviceXml)
            m.putString("model", info.model)
            m.putString("firmwareVersion", info.firmwareVersion)
            m.putString("partNumber", info.partNumber)
            m.putString("activityPath", info.activityPath)
            m.putString("fitActivityPath", info.fitActivityPath)
            m.putBoolean("isEdge", info.isEdge)
            volumesArray.pushMap(m)
        }
        result.putArray("volumes", volumesArray)
        val hasSdCard = infos.any { !it.hasGarminDeviceXml } && infos.any { it.hasGarminDeviceXml }
        result.putBoolean("hasSdCard", hasSdCard)
        return result
    }

    // ─── SAF path (Edge drives Android mounts itself) ───────────────────────
    private var safRoot: DocumentFile? = null
    // The grant can be the drive itself or its Garmin folder (both are natural picks in the
    // picker, 2026-11-06); with the folder, paths "Garmin/..." are resolved from the grant.
    private var safRootIsGarminDir = false

    private fun isGarminDir(root: DocumentFile): Boolean = safChild(root, "GarminDevice.xml") != null
    private var pendingSafPromise: Promise? = null

    /** The mounted GARMIN drive Android owns, or null. */
    private fun garminStorageVolume(): StorageVolume? {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) return null
        return try {
            val sm = reactContext.getSystemService(Context.STORAGE_SERVICE) as StorageManager
            sm.storageVolumes.firstOrNull {
                !it.isPrimary && it.state == android.os.Environment.MEDIA_MOUNTED
                    && it.getDescription(reactContext)?.contains("GARMIN", true) == true
            }
        } catch (_: Exception) { null }
    }

    private fun savedSafRoot(): DocumentFile? {
        val s = reactContext.getSharedPreferences(SAF_PREFS, Context.MODE_PRIVATE).getString(SAF_KEY_TREE, null)
            ?: return null
        val uri = Uri.parse(s)
        val held = reactContext.contentResolver.persistedUriPermissions.any {
            it.uri == uri && it.isReadPermission && it.isWritePermission
        }
        return if (held) DocumentFile.fromTreeUri(reactContext, uri) else null
    }

    private fun safChild(dir: DocumentFile, name: String): DocumentFile? =
        dir.listFiles().firstOrNull { it.name?.equals(name, ignoreCase = true) == true }

    private fun safResolve(path: String): DocumentFile? {
        var cur: DocumentFile = safRoot ?: return null
        var segs = path.split("/").filter { it.isNotEmpty() }
        if (safRootIsGarminDir) {
            if (segs.isEmpty() || !segs[0].equals("Garmin", ignoreCase = true)) return null
            segs = segs.drop(1)
        }
        for (seg in segs) {
            if (seg == "." || seg == "..") return null
            cur = safChild(cur, seg) ?: return null
        }
        return cur
    }

    private fun safReadBytes(f: DocumentFile): ByteArray =
        reactContext.contentResolver.openInputStream(f.uri)?.use { ins ->
            val out = ByteArrayOutputStream(); val buf = ByteArray(8192)
            while (true) { val n = ins.read(buf); if (n < 0) break; out.write(buf, 0, n) }
            out.toByteArray()
        } ?: throw IllegalStateException("could not open ${f.name}")

    /** The Edge's volume info from a granted drive root, or null if it isn't an Edge. */
    private fun safEdgeInfo(root: DocumentFile): GarminVolumeInfo? {
        val xml = safChild(root, "GarminDevice.xml")
            ?: safChild(root, "Garmin")?.let { safChild(it, "GarminDevice.xml") } ?: return null
        val parsed = parseGarminDeviceXml(safReadBytes(xml)) ?: return null
        if (parsed.model?.trim()?.startsWith("edge", ignoreCase = true) != true) return null
        return GarminVolumeInfo(SAF_VOLUME_INDEX, true, parsed.model, parsed.firmwareVersion, parsed.partNumber,
            parsed.activityPath, parsed.fitActivityPath, true)
    }

    private fun useSaf(root: DocumentFile, info: GarminVolumeInfo, promise: Promise) {
        safRoot = root
        safRootIsGarminDir = isGarminDir(root)
        volumeInfos = listOf(info)
        mainResolve(promise, buildScanResult(volumeInfos))
    }

    private fun connectSaf(promise: Promise) {
        executor.execute {
            closeAllDevices()
            try {
                val root = savedSafRoot()
                val info = root?.let { safEdgeInfo(it) }
                if (root != null && info != null) { useSaf(root, info, promise); return@execute }
            } catch (_: Exception) {}
            UiThreadUtil.runOnUiThread { launchSafPicker(promise) }
        }
    }

    private fun launchSafPicker(promise: Promise) {
        val activity = reactContext.currentActivity
        if (activity == null) { promise.reject("NO_ACTIVITY", "App not in foreground"); return }
        if (pendingSafPromise != null) { promise.reject("BUSY", "A connect is already in progress"); return }
        var intent = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            garminStorageVolume()?.let { v -> try { intent = v.createOpenDocumentTreeIntent() } catch (_: Exception) {} }
        }
        pendingSafPromise = promise
        try {
            activity.startActivityForResult(intent, REQUEST_GARMIN_TREE)
        } catch (e: Exception) {
            pendingSafPromise = null
            promise.reject("PICK_FAILED", e.message ?: "Could not open the folder picker")
        }
    }

    override fun onActivityResult(activity: Activity, requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != REQUEST_GARMIN_TREE) return
        val promise = pendingSafPromise ?: return
        pendingSafPromise = null
        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null) { promise.reject("CANCELLED", "No drive selected"); return }
        executor.execute {
            try {
                val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
                reactContext.contentResolver.takePersistableUriPermission(uri, flags)
                reactContext.getSharedPreferences(SAF_PREFS, Context.MODE_PRIVATE).edit()
                    .putString(SAF_KEY_TREE, uri.toString()).apply()
                val root = DocumentFile.fromTreeUri(reactContext, uri)
                val info = root?.let { safEdgeInfo(it) }
                if (root == null || info == null) {
                    mainReject(promise, "NOT_EDGE", "That isn't the Edge (no GarminDevice.xml for an Edge there). Pick the GARMIN drive or its Garmin folder.")
                    return@execute
                }
                useSaf(root, info, promise)
            } catch (e: Exception) {
                mainReject(promise, "GRANT_FAILED", e.message ?: "Could not use the selected drive")
            }
        }
    }

    override fun onNewIntent(intent: Intent) {}

    private fun safList(path: String, promise: Promise) {
        val dir = safResolve(path)
        if (dir == null || !dir.isDirectory) { mainReject(promise, "GARMIN_PATH_NOT_FOUND", "$path not found on this volume"); return }
        val names = Arguments.createArray()
        for (f in dir.listFiles()) if (!f.isDirectory) f.name?.let { names.pushString(it) }
        mainResolve(promise, names)
    }

    private fun safRead(path: String, promise: Promise) {
        val f = safResolve(path)
        if (f == null || f.isDirectory) { mainReject(promise, "GARMIN_FILE_NOT_FOUND", "$path not found"); return }
        mainResolve(promise, android.util.Base64.encodeToString(safReadBytes(f), android.util.Base64.NO_WRAP))
    }

    /** Same rule as the libaums writeFile(): one file straight inside the Edge's Garmin/NewFiles. */
    private fun safWrite(name: String, bytes: ByteArray, path: String, promise: Promise) {
        val dir = safResolve("Garmin/NewFiles")
        if (dir == null || !dir.isDirectory) { mainReject(promise, "GARMIN_PATH_NOT_FOUND", "Garmin/NewFiles missing - is this an Edge 500 drive?"); return }
        safChild(dir, name)?.let { try { it.delete() } catch (_: Exception) {} }
        val target = dir.createFile("application/octet-stream", name)
            ?: throw IllegalStateException("could not create $name")
        if (target.name != name) {           // the provider renamed it ("x (1).fit") - don't leave a stray file
            try { target.delete() } catch (_: Exception) {}
            mainReject(promise, "GARMIN_WRITE_FAILED", "the drive renamed $name to ${target.name}"); return
        }
        reactContext.contentResolver.openOutputStream(target.uri, "wt")?.use { it.write(bytes) }
            ?: throw IllegalStateException("could not open $name for writing")
        if (!safReadBytes(target).contentEquals(bytes)) { mainReject(promise, "GARMIN_VERIFY_FAILED", "verify after write failed for $path"); return }
        mainResolve(promise, true)
    }

    // ─── listActivityFiles() ─────────────────────────────────────────────────
    // Lists files at the given volume's resolved activityPath (from connect()'s
    // result) — the GPSData DataType's OutputFromUnit location, e.g.
    // "Garmin/GPX/Current" on André's eTrex 30. Filenames only; read the content
    // with readActivityFile().
    @ReactMethod
    fun listActivityFiles(volumeIndex: Int, promise: Promise) {
        executor.execute {
            try {
                val dir = resolveVolumeActivityDir(volumeIndex)
                if (dir == null) {
                    mainReject(promise, "GARMIN_PATH_NOT_FOUND", "Activity path not found for this volume")
                    return@execute
                }
                val names = Arguments.createArray()
                for (f in dir.listFiles()) {
                    if (!f.isDirectory) names.pushString(f.name)
                }
                mainResolve(promise, names)
            } catch (e: Exception) {
                mainReject(promise, "GARMIN_LIST_FAILED", e.message ?: "Unknown error listing activity files")
            }
        }
    }

    // ─── readActivityFile() ──────────────────────────────────────────────────
    // Reads one file's full text content (GPX is plain XML) from the resolved
    // activity directory. Parsing the GPX itself happens on the JS side, reusing
    // RouteGpxParser.ts/GpxParser.ts — same as every other GPX source in this app.
    @ReactMethod
    fun readActivityFile(volumeIndex: Int, fileName: String, promise: Promise) {
        executor.execute {
            try {
                val dir = resolveVolumeActivityDir(volumeIndex)
                val file = dir?.let { findChildCaseInsensitive(it, fileName) }
                if (file == null) {
                    mainReject(promise, "GARMIN_FILE_NOT_FOUND", "$fileName not found")
                    return@execute
                }
                val text = String(readUsbFileFully(file), Charsets.UTF_8)
                mainResolve(promise, text)
            } catch (e: Exception) {
                mainReject(promise, "GARMIN_READ_FAILED", e.message ?: "Unknown error reading $fileName")
            }
        }
    }

    // ─── listGpxDirFiles() / readGpxDirFile() ────────────────────────────────
    // Unlike listActivityFiles()/readActivityFile() (scoped to the resolved
    // activityPath, e.g. "Garmin/GPX/Current"), these look directly inside
    // "Garmin/GPX" itself — where saved routes/tracks and BaseCamp-authored
    // "Waypoints*.gpx" POI files live (confirmed on real hardware, see
    // GARMIN_USB_IMPORT_SPEC.md). Not recursive, so Current/'s activity files
    // are naturally excluded. Works on any volume, not just the one carrying
    // GarminDevice.xml — the SD card can hold its own Garmin/GPX folder too.
    @ReactMethod
    fun listGpxDirFiles(volumeIndex: Int, promise: Promise) {
        executor.execute {
            try {
                val dir = resolveVolumeGpxDir(volumeIndex)
                if (dir == null) {
                    mainReject(promise, "GARMIN_PATH_NOT_FOUND", "No Garmin/GPX folder on this volume")
                    return@execute
                }
                val names = Arguments.createArray()
                for (f in dir.listFiles()) {
                    if (!f.isDirectory && f.name.lowercase().endsWith(".gpx")) names.pushString(f.name)
                }
                mainResolve(promise, names)
            } catch (e: Exception) {
                mainReject(promise, "GARMIN_LIST_FAILED", e.message ?: "Unknown error listing Garmin/GPX")
            }
        }
    }

    @ReactMethod
    fun readGpxDirFile(volumeIndex: Int, fileName: String, promise: Promise) {
        executor.execute {
            try {
                val dir = resolveVolumeGpxDir(volumeIndex)
                val file = dir?.let { findChildCaseInsensitive(it, fileName) }
                if (file == null) {
                    mainReject(promise, "GARMIN_FILE_NOT_FOUND", "$fileName not found")
                    return@execute
                }
                val text = String(readUsbFileFully(file), Charsets.UTF_8)
                mainResolve(promise, text)
            } catch (e: Exception) {
                mainReject(promise, "GARMIN_READ_FAILED", e.message ?: "Unknown error reading $fileName")
            }
        }
    }

    // ─── writeGpxToSdCard() ──────────────────────────────────────────────────
    // Per GARMIN_USB_IMPORT_SPEC.md, confirmed with André 2026-08-07: NEVER write
    // to internal memory, no exceptions, for routes or POI alike. Only proceeds
    // if `volumeIndex` refers to a volume WITHOUT GarminDevice.xml (the SD-card
    // heuristic above) — refuses otherwise, does not silently redirect to
    // internal memory. fileName should already include the .gpx extension.
    @ReactMethod
    fun writeGpxToSdCard(volumeIndex: Int, fileName: String, gpxContent: String, promise: Promise) {
        executor.execute {
            try {
                val info = volumeInfos.find { it.volumeIndex == volumeIndex }
                if (info == null) {
                    mainReject(promise, "GARMIN_VOLUME_NOT_FOUND", "Unknown volume")
                    return@execute
                }
                if (info.hasGarminDeviceXml) {
                    mainReject(promise, "GARMIN_REFUSED_INTERNAL_WRITE",
                        "Refusing to write to internal memory — SD card only, by design. See GARMIN_USB_IMPORT_SPEC.md.")
                    return@execute
                }
                val (device, partitionOffset) = deviceAndPartitionForVolumeIndex(volumeIndex)
                if (device == null) {
                    mainReject(promise, "GARMIN_VOLUME_NOT_FOUND", "Volume no longer available")
                    return@execute
                }
                val partition = device.partitions[partitionOffset]
                val root = partition.fileSystem.rootDirectory
                val garminDir = findChildCaseInsensitive(root, "Garmin")
                    ?: root.createDirectory("Garmin")
                val gpxDir = findChildCaseInsensitive(garminDir, "GPX")
                    ?: garminDir.createDirectory("GPX")
                val existing = findChildCaseInsensitive(gpxDir, fileName)
                val target = existing ?: gpxDir.createFile(fileName)
                val os = UsbFileOutputStream(target)
                os.write(gpxContent.toByteArray(Charsets.UTF_8))
                os.close()
                mainResolve(promise, true)
            } catch (e: Exception) {
                mainReject(promise, "GARMIN_WRITE_FAILED", e.message ?: "Unknown error writing $fileName")
            }
        }
    }

    // ─── listDir() / readFile() / writeFile() — Edge 500 & co. (2026-11-05) ────────
    // Generic file access relative to a volume's root, for the mass-storage Edges: rides in
    // Garmin/Activities, the profile in Garmin/Settings + Garmin/Sports, and everything Sommet
    // sends (profile copies, TCX courses, FIT workouts) dropped into Garmin/NewFiles, which the
    // Edge reads when it leaves USB mode (desktop tools/edge500_*.py, checked on a real Edge 500).
    // Bytes travel as base64; the FIT/TCX logic is in the TS services (Edge500*.ts).
    //
    // SAFETY: writeFile() only ever writes ONE file straight inside Garmin/NewFiles of a volume
    // whose GarminDevice.xml says it is an Edge. It never writes over the Edge's own files, and the
    // eTrex rule (never write to internal memory, writeGpxToSdCard()) stays untouched.

    /** File names (not folders) directly inside `path`. */
    @ReactMethod
    fun listDir(volumeIndex: Int, path: String, promise: Promise) {
        executor.execute {
            try {
                if (volumeIndex == SAF_VOLUME_INDEX) { safList(path, promise); return@execute }
                val dir = resolvePath(volumeIndex, path)
                if (dir == null || !dir.isDirectory) {
                    mainReject(promise, "GARMIN_PATH_NOT_FOUND", "$path not found on this volume")
                    return@execute
                }
                val names = Arguments.createArray()
                for (f in dir.listFiles()) if (!f.isDirectory) names.pushString(f.name)
                mainResolve(promise, names)
            } catch (e: Exception) {
                mainReject(promise, "GARMIN_LIST_FAILED", e.message ?: "Unknown error listing $path")
            }
        }
    }

    /** One file's bytes, base64. */
    @ReactMethod
    fun readFile(volumeIndex: Int, path: String, promise: Promise) {
        executor.execute {
            try {
                if (volumeIndex == SAF_VOLUME_INDEX) { safRead(path, promise); return@execute }
                val file = resolvePath(volumeIndex, path)
                if (file == null || file.isDirectory) {
                    mainReject(promise, "GARMIN_FILE_NOT_FOUND", "$path not found")
                    return@execute
                }
                mainResolve(promise, android.util.Base64.encodeToString(readUsbFileFully(file), android.util.Base64.NO_WRAP))
            } catch (e: Exception) {
                mainReject(promise, "GARMIN_READ_FAILED", e.message ?: "Unknown error reading $path")
            }
        }
    }

    /** Writes `base64` to Garmin/NewFiles/<name> on an Edge volume, then reads it back to check. */
    @ReactMethod
    fun writeFile(volumeIndex: Int, path: String, base64: String, promise: Promise) {
        executor.execute {
            try {
                val info = volumeInfos.find { it.volumeIndex == volumeIndex }
                if (info == null) {
                    mainReject(promise, "GARMIN_VOLUME_NOT_FOUND", "Unknown volume")
                    return@execute
                }
                val segments = path.split("/").filter { it.isNotEmpty() }
                if (!info.isEdge || segments.size != 3 || !segments[0].equals("Garmin", ignoreCase = true)
                    || !segments[1].equals("NewFiles", ignoreCase = true)
                    || segments[2] == "." || segments[2] == ".." || segments[2].contains('\\')) {
                    mainReject(promise, "GARMIN_REFUSED_WRITE",
                        "Refusing to write $path - only a file in an Edge's Garmin/NewFiles is allowed")
                    return@execute
                }
                val bytes = android.util.Base64.decode(base64, android.util.Base64.DEFAULT)
                if (volumeIndex == SAF_VOLUME_INDEX) { safWrite(segments[2], bytes, path, promise); return@execute }
                val dir = resolvePath(volumeIndex, "Garmin/NewFiles")
                if (dir == null || !dir.isDirectory) {
                    mainReject(promise, "GARMIN_PATH_NOT_FOUND", "Garmin/NewFiles missing - is this an Edge 500 drive?")
                    return@execute
                }
                // Re-create rather than overwrite, so a shorter file can't keep a stale tail.
                findChildCaseInsensitive(dir, segments[2])?.let { try { it.delete() } catch (_: Exception) {} }
                val target = dir.createFile(segments[2])
                val os = UsbFileOutputStream(target)
                os.write(bytes)
                os.close()
                val back = findChildCaseInsensitive(dir, segments[2])?.let { readUsbFileFully(it) }
                if (back == null || !back.contentEquals(bytes)) {
                    mainReject(promise, "GARMIN_VERIFY_FAILED", "verify after write failed for $path")
                    return@execute
                }
                mainResolve(promise, true)
            } catch (e: Exception) {
                mainReject(promise, "GARMIN_WRITE_FAILED", e.message ?: "Unknown error writing $path")
            }
        }
    }

    @ReactMethod
    fun disconnect(promise: Promise) {
        executor.execute {
            closeAllDevices()
            mainResolve(promise, true)
        }
    }

    // ─── internal helpers ────────────────────────────────────────────────────

    private fun resolveVolumeActivityDir(volumeIndex: Int): UsbFile? {
        val info = volumeInfos.find { it.volumeIndex == volumeIndex } ?: return null
        val path = info.activityPath ?: return null
        val (device, partitionOffset) = deviceAndPartitionForVolumeIndex(volumeIndex)
        if (device == null) return null
        val root = device.partitions[partitionOffset].fileSystem.rootDirectory
        var current: UsbFile = root
        for (segment in path.split("/").filter { it.isNotEmpty() }) {
            current = findChildCaseInsensitive(current, segment) ?: return null
        }
        return current
    }

    /** A file or folder by its path from the volume root (case-insensitive), or null. */
    private fun resolvePath(volumeIndex: Int, path: String): UsbFile? {
        val (device, partitionOffset) = deviceAndPartitionForVolumeIndex(volumeIndex)
        if (device == null) return null
        var current: UsbFile = device.partitions[partitionOffset].fileSystem.rootDirectory
        for (segment in path.split("/").filter { it.isNotEmpty() }) {
            if (segment == "." || segment == "..") return null
            current = findChildCaseInsensitive(current, segment) ?: return null
        }
        return current
    }

    private fun resolveVolumeGpxDir(volumeIndex: Int): UsbFile? {
        val (device, partitionOffset) = deviceAndPartitionForVolumeIndex(volumeIndex)
        if (device == null) return null
        val root = device.partitions[partitionOffset].fileSystem.rootDirectory
        val garminDir = findChildCaseInsensitive(root, "Garmin") ?: return null
        return findChildCaseInsensitive(garminDir, "GPX")
    }

    // volumeIndex was assigned sequentially across (device, partition) pairs in
    // scanVolumes() — walk the same structure to map it back.
    private fun deviceAndPartitionForVolumeIndex(volumeIndex: Int): Pair<UsbMassStorageDevice?, Int> {
        var i = 0
        for (device in openDevices) {
            for (p in device.partitions.indices) {
                if (i == volumeIndex) return Pair(device, p)
                i++
            }
        }
        return Pair(null, -1)
    }

    private fun closeAllDevices() {
        closeDevicesList(openDevices)
        openDevices = emptyList()
        volumeInfos = emptyList()
        safRoot = null
    }

    private fun closeDevicesList(devices: List<UsbMassStorageDevice>) {
        for (d in devices) {
            try { d.close() } catch (_: Exception) {}
        }
    }

    private fun mainResolve(promise: Promise, value: Any) {
        UiThreadUtil.runOnUiThread {
            when (value) {
                is WritableMap -> promise.resolve(value)
                is WritableArray -> promise.resolve(value)
                is String -> promise.resolve(value)
                is Boolean -> promise.resolve(value)
                else -> promise.resolve(null)
            }
        }
    }

    private fun mainReject(promise: Promise, code: String, message: String) {
        UiThreadUtil.runOnUiThread { promise.reject(code, message) }
    }
}

private fun findChildCaseInsensitive(dir: UsbFile, name: String): UsbFile? =
    dir.listFiles().find { it.name.equals(name, ignoreCase = true) }

private fun readUsbFileFully(file: UsbFile): ByteArray {
    val ins = UsbFileInputStream(file)
    val out = ByteArrayOutputStream()
    val buffer = ByteArray(8192)
    while (true) {
        val n = ins.read(buffer)
        if (n < 0) break
        out.write(buffer, 0, n)
    }
    ins.close()
    return out.toByteArray()
}

private data class ParsedGarminDevice(
    val model: String?,
    val firmwareVersion: String?,
    val partNumber: String?,
    val activityPath: String?,
    val fitActivityPath: String?,
)

/**
 * Parses the fields confirmed against real hardware (see GARMIN_USB_IMPORT_SPEC.md):
 * Model/Description, Model/SoftwareVersion (formatted with an implied decimal point
 * two digits from the right, e.g. 501 -> "5.01"), Model/PartNumber, and the GPSData
 * DataType's OutputFromUnit File/Location/Path (the recorded-activity folder — NOT
 * the InputToUnit one, which is for writing waypoints/routes onto the device).
 */
private fun parseGarminDeviceXml(bytes: ByteArray): ParsedGarminDevice? {
    return try {
        val factory = XmlPullParserFactory.newInstance()
        factory.isNamespaceAware = false
        val parser = factory.newPullParser()
        parser.setInput(bytes.inputStream(), "UTF-8")

        var model: String? = null
        var softwareVersion: String? = null
        var partNumber: String? = null
        var activityPath: String? = null
        var fitActivityPath: String? = null

        var inModel = false
        var inGpsDataType = false
        var currentDataTypeName: String? = null
        var inFile = false
        var currentTransferDirection: String? = null
        var currentPath: String? = null

        var eventType = parser.eventType
        while (eventType != XmlPullParser.END_DOCUMENT) {
            when (eventType) {
                XmlPullParser.START_TAG -> {
                    when (parser.name) {
                        "Model" -> inModel = true
                        "PartNumber" -> if (inModel) partNumber = parser.nextText()
                        "SoftwareVersion" -> if (inModel) softwareVersion = parser.nextText()
                        "Description" -> if (inModel) model = parser.nextText()
                        "DataType" -> currentDataTypeName = null
                        "Name" -> if (currentDataTypeName == null) {
                            currentDataTypeName = parser.nextText()
                            inGpsDataType = currentDataTypeName == "GPSData"
                        }
                        "File" -> { inFile = true; currentTransferDirection = null; currentPath = null }
                        "Path" -> if (inFile) currentPath = parser.nextText()
                        "TransferDirection" -> if (inFile) currentTransferDirection = parser.nextText()
                    }
                }
                XmlPullParser.END_TAG -> {
                    when (parser.name) {
                        "Model" -> inModel = false
                        "File" -> {
                            if (inGpsDataType && currentTransferDirection == "OutputFromUnit" && currentPath != null) {
                                activityPath = currentPath
                            }
                            // Edge 500 & co.: recorded rides are FIT_TYPE_4 ("Garmin/Activities").
                            if (currentDataTypeName == "FIT_TYPE_4" && currentTransferDirection == "OutputFromUnit"
                                && currentPath != null && fitActivityPath == null) {
                                fitActivityPath = currentPath
                            }
                            inFile = false
                        }
                        "DataType" -> inGpsDataType = false
                    }
                }
            }
            eventType = parser.next()
        }

        ParsedGarminDevice(
            model = model,
            firmwareVersion = softwareVersion?.let { formatSoftwareVersion(it) },
            partNumber = partNumber,
            activityPath = activityPath,
            fitActivityPath = fitActivityPath,
        )
    } catch (e: Exception) {
        null
    }
}

/** "501" -> "5.01", implied decimal point two digits from the right — Garmin's
 * convention, confirmed against André's real eTrex 30 (SoftwareVersion 501 matches
 * the device's own "5.01" firmware version). Not yet confirmed universal across all
 * Garmin device generations — see GARMIN_USB_IMPORT_SPEC.md. */
private fun formatSoftwareVersion(raw: String): String {
    val digits = raw.trim()
    if (digits.length <= 2) return "0.${digits.padStart(2, '0')}"
    val whole = digits.substring(0, digits.length - 2)
    val frac = digits.substring(digits.length - 2)
    return "$whole.$frac"
}

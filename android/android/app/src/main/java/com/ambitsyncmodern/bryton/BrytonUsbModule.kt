package com.ambitsyncmodern.bryton

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.usb.UsbConstants
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.Build
import com.facebook.react.bridge.*
import me.jahnen.libaums.core.UsbMassStorageDevice
import me.jahnen.libaums.core.fs.UsbFile
import me.jahnen.libaums.core.fs.UsbFileInputStream
import me.jahnen.libaums.core.fs.UsbFileOutputStream
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors

/*
 * Bryton Aero 60 over USB Mass Storage (André, 2026-09-24). The Aero 60 exposes a plain FAT
 * volume with its files at known paths (System/Profile.bin, workouts under System/Plan/Cycling) - the
 * same transport GarminModule already handles with libaums (me.jahnen.libaums:core). This is a
 * generic path-based read/write/list over that volume, identified by a filesystem MARKER
 * (System/Profile.bin) rather than a USB vendor id, since the Bryton's VID isn't fixed. The TS
 * side (BrytonFit.ts / BrytonProfile.ts) does all the encode/decode; this only moves bytes.
 *
 * Mirrors GarminModule's permission + libaums flow. Kept deliberately separate ("one file per
 * format"). Read/write happen off the UI thread; results resolve back on it.
 */
private const val ACTION_BRYTON_USB_PERMISSION = "com.ambitsyncmodern.BRYTON_USB_PERMISSION"
private const val MARKER_PATH = "System/Profile.bin"

class BrytonUsbModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    private val executor = Executors.newSingleThreadExecutor()
    private var openDevice: UsbMassStorageDevice? = null
    private var partitionIndex: Int = 0

    override fun getName() = "BrytonUsb"

    private val usbPermissionReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            try { reactContext.unregisterReceiver(this) } catch (_: Exception) {}
            val granted = intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)
            val p = pendingConnect ?: return
            pendingConnect = null
            if (granted) scan(p) else mainReject(p, "BRYTON_PERMISSION_DENIED", "USB permission denied")
        }
    }
    private var pendingConnect: Promise? = null

    // A USB device that advertises a Mass Storage interface (class 8).
    private fun massStorageCandidate(usbManager: UsbManager): UsbDevice? =
        usbManager.deviceList.values.firstOrNull { dev ->
            (0 until dev.interfaceCount).any { dev.getInterface(it).interfaceClass == UsbConstants.USB_CLASS_MASS_STORAGE }
        }

    @ReactMethod
    fun connect(promise: Promise) {
        val usbManager = reactContext.getSystemService(Context.USB_SERVICE) as UsbManager
        val dev = massStorageCandidate(usbManager)
        if (dev == null) {
            promise.reject("BRYTON_NOT_FOUND", "No USB drive detected. Plug in the Bryton and confirm the cable carries data.")
            return
        }
        if (usbManager.hasPermission(dev)) { scan(promise); return }
        pendingConnect = promise
        val flags = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0
        val permissionIntent = PendingIntent.getBroadcast(reactContext, 0, Intent(ACTION_BRYTON_USB_PERMISSION), flags)
        val filter = IntentFilter(ACTION_BRYTON_USB_PERMISSION)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU)
            reactContext.registerReceiver(usbPermissionReceiver, filter, Context.RECEIVER_NOT_EXPORTED)
        else reactContext.registerReceiver(usbPermissionReceiver, filter)
        usbManager.requestPermission(dev, permissionIntent)
    }

    // Open every mass-storage volume and keep the first whose root carries the Bryton marker.
    private fun scan(promise: Promise) {
        executor.execute {
            close()
            try {
                val devices = UsbMassStorageDevice.getMassStorageDevices(reactContext)
                for (device in devices) {
                    try { device.init() } catch (_: Exception) { continue }
                    for (pIdx in device.partitions.indices) {
                        val root = device.partitions[pIdx].fileSystem.rootDirectory
                        if (traverse(root, MARKER_PATH) != null) {
                            openDevice = device; partitionIndex = pIdx
                            val name = try { device.partitions[pIdx].volumeLabel } catch (_: Exception) { "BRYTON" }
                            val out = Arguments.createMap().apply {
                                putBoolean("found", true)
                                putString("name", if (name.isNullOrBlank()) "Bryton Aero 60" else name)
                            }
                            mainResolve(promise, out)
                            return@execute
                        }
                    }
                    try { device.close() } catch (_: Exception) {}
                }
                mainReject(promise, "BRYTON_NOT_FOUND", "A USB drive is connected but it doesn't look like a Bryton (no System/Profile.bin).")
            } catch (e: Exception) {
                mainReject(promise, "BRYTON_SCAN_FAILED", e.message ?: "Could not read the USB drive")
            }
        }
    }

    @ReactMethod
    fun readFile(path: String, promise: Promise) {
        executor.execute {
            try {
                val f = root()?.let { traverse(it, path) }
                if (f == null || f.isDirectory) { mainReject(promise, "BRYTON_FILE_NOT_FOUND", "$path not found"); return@execute }
                val b64 = android.util.Base64.encodeToString(readFully(f), android.util.Base64.NO_WRAP)
                mainResolve(promise, b64)
            } catch (e: Exception) { mainReject(promise, "BRYTON_READ_FAILED", e.message ?: "read failed") }
        }
    }

    @ReactMethod
    fun listDir(path: String, promise: Promise) {
        executor.execute {
            try {
                val dir = root()?.let { traverse(it, path) }
                if (dir == null || !dir.isDirectory) { mainReject(promise, "BRYTON_DIR_NOT_FOUND", "$path not found"); return@execute }
                val names = Arguments.createArray()
                for (f in dir.listFiles()) if (!f.isDirectory) names.pushString(f.name)
                mainResolve(promise, names)
            } catch (e: Exception) { mainReject(promise, "BRYTON_LIST_FAILED", e.message ?: "list failed") }
        }
    }

    @ReactMethod
    fun writeFile(path: String, base64: String, promise: Promise) {
        executor.execute {
            try {
                val r = root() ?: run { mainReject(promise, "BRYTON_NOT_CONNECTED", "Connect the Bryton first"); return@execute }
                val segments = path.split("/").filter { it.isNotEmpty() }
                var dir: UsbFile = r
                for (i in 0 until segments.size - 1) {
                    dir = childInsensitive(dir, segments[i]) ?: dir.createDirectory(segments[i])
                }
                val fileName = segments.last()
                // Delete any existing file first so the new content can't leave a stale tail
                // (Profile.bin is fixed-length and .fit is small, so exactness matters).
                childInsensitive(dir, fileName)?.let { try { it.delete() } catch (_: Exception) {} }
                val target = dir.createFile(fileName)
                val bytes = android.util.Base64.decode(base64, android.util.Base64.DEFAULT)
                UsbFileOutputStream(target).use { it.write(bytes) }
                mainResolve(promise, true)
            } catch (e: Exception) { mainReject(promise, "BRYTON_WRITE_FAILED", e.message ?: "write failed") }
        }
    }

    @ReactMethod
    fun disconnect(promise: Promise) {
        executor.execute { close(); mainResolve(promise, true) }
    }

    // ─── helpers ───────────────────────────────────────────────────────────
    private fun root(): UsbFile? =
        openDevice?.let { it.partitions[partitionIndex].fileSystem.rootDirectory }

    private fun traverse(from: UsbFile, path: String): UsbFile? {
        var cur: UsbFile = from
        for (seg in path.split("/").filter { it.isNotEmpty() }) {
            cur = childInsensitive(cur, seg) ?: return null
        }
        return cur
    }

    private fun childInsensitive(dir: UsbFile, name: String): UsbFile? =
        dir.listFiles().find { it.name.equals(name, ignoreCase = true) }

    private fun readFully(file: UsbFile): ByteArray {
        val ins = UsbFileInputStream(file)
        val out = ByteArrayOutputStream()
        val buf = ByteArray(8192)
        while (true) { val n = ins.read(buf); if (n < 0) break; out.write(buf, 0, n) }
        ins.close()
        return out.toByteArray()
    }

    private fun close() {
        try { openDevice?.close() } catch (_: Exception) {}
        openDevice = null; partitionIndex = 0
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

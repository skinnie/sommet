package com.ambitsyncmodern.bryton

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.storage.StorageManager
import androidx.documentfile.provider.DocumentFile
import com.facebook.react.bridge.*
import java.io.ByteArrayOutputStream

/*
 * Bryton Aero 60 over the Storage Access Framework (SAF), André, 2026-09-24.
 *
 * First cut used libaums (like GarminModule) - but on-device testing showed Android AUTO-MOUNTS the
 * Bryton's FAT volume (vold: "public:8,0 mounted ... fsLabel=BRYTON"), so the kernel owns the raw
 * device and libaums can't claim it (the eTrex doesn't auto-mount, which is why it works there).
 * The same auto-mount, though, exposes the volume through SAF: the user grants the BRYTON drive once
 * (a persisted tree permission) and we read/write its files via DocumentFile. The TS side
 * (BrytonUsb.ts) is unchanged - same connect/readFile/writeFile/listDir/disconnect surface.
 *
 * The picker is pre-targeted at the removable volume (StorageVolume.createOpenDocumentTreeIntent on
 * API 29+), so "Use this folder" is usually one tap. The grant is remembered in SharedPreferences,
 * so later sessions connect without re-prompting.
 */
private const val PREFS = "bryton_saf"
private const val KEY_TREE = "treeUri"
private const val REQUEST_TREE = 0xB27

class BrytonUsbModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext), ActivityEventListener {

    init { reactContext.addActivityEventListener(this) }
    override fun getName() = "BrytonUsb"

    private var pendingConnect: Promise? = null

    private fun savedTree(): Uri? {
        val s = reactContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_TREE, null)
        return s?.let { Uri.parse(it) }
    }
    private fun saveTree(uri: Uri) {
        reactContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit().putString(KEY_TREE, uri.toString()).apply()
    }

    private fun rootDoc(): DocumentFile? {
        val uri = savedTree() ?: return null
        // is the persisted permission still held?
        val held = reactContext.contentResolver.persistedUriPermissions.any {
            it.uri == uri && it.isReadPermission && it.isWritePermission
        }
        if (!held) return null
        return DocumentFile.fromTreeUri(reactContext, uri)
    }

    @ReactMethod
    fun connect(promise: Promise) {
        val root = rootDoc()
        if (root != null && traverse(root, "System/Profile.bin") != null) {
            promise.resolve(result(root)); return
        }
        // need the user to grant the drive
        val activity = reactContext.currentActivity
        if (activity == null) { promise.reject("NO_ACTIVITY", "App not in foreground"); return }
        if (pendingConnect != null) { promise.reject("BUSY", "A connect is already in progress"); return }
        pendingConnect = promise
        val intent = openTreeIntent()
        try {
            activity.startActivityForResult(intent, REQUEST_TREE)
        } catch (e: Exception) {
            pendingConnect = null
            promise.reject("PICK_FAILED", e.message ?: "Could not open the folder picker")
        }
    }

    private fun openTreeIntent(): Intent {
        // Pre-target the removable (Bryton) volume so the picker opens on it.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            val sm = reactContext.getSystemService(Context.STORAGE_SERVICE) as StorageManager
            val vols = sm.storageVolumes.filter { it.isRemovable && !it.isPrimary }
            // Prefer the volume actually labelled BRYTON (the tablet may have several USB drives),
            // else fall back to the first removable one.
            val vol = vols.firstOrNull { it.getDescription(reactContext)?.contains("BRYTON", true) == true }
                ?: vols.firstOrNull()
            if (vol != null) {
                try { return vol.createOpenDocumentTreeIntent() } catch (_: Exception) {}
            }
        }
        return Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
    }

    override fun onActivityResult(activity: Activity, requestCode: Int, resultCode: Int, data: Intent?) {
        if (requestCode != REQUEST_TREE) return
        val promise = pendingConnect ?: return
        pendingConnect = null
        val uri = data?.data
        if (resultCode != Activity.RESULT_OK || uri == null) { promise.reject("CANCELLED", "No folder selected"); return }
        try {
            val flags = Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            reactContext.contentResolver.takePersistableUriPermission(uri, flags)
            saveTree(uri)
            val root = DocumentFile.fromTreeUri(reactContext, uri)
            if (root == null || traverse(root, "System/Profile.bin") == null) {
                promise.reject("NOT_BRYTON", "That folder isn't a Bryton drive (no System/Profile.bin). Pick the BRYTON drive itself.")
                return
            }
            promise.resolve(result(root))
        } catch (e: Exception) {
            promise.reject("GRANT_FAILED", e.message ?: "Could not use the selected folder")
        }
    }

    override fun onNewIntent(intent: Intent) {}

    private fun result(root: DocumentFile): WritableMap =
        Arguments.createMap().apply {
            putBoolean("found", true)
            putString("name", "Bryton Aero 60")   // root.name is the volume UUID, not friendly
        }

    @ReactMethod
    fun readFile(path: String, promise: Promise) {
        Thread {
            try {
                val root = rootDoc() ?: run { promise.reject("NOT_CONNECTED", "Connect the Bryton first"); return@Thread }
                val f = traverse(root, path)
                if (f == null || f.isDirectory) { promise.reject("BRYTON_FILE_NOT_FOUND", "$path not found"); return@Thread }
                val bytes = reactContext.contentResolver.openInputStream(f.uri)?.use { readFully(it) }
                    ?: throw IllegalStateException("could not open $path")
                promise.resolve(android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP))
            } catch (e: Exception) { promise.reject("BRYTON_READ_FAILED", e.message ?: "read failed") }
        }.start()
    }

    @ReactMethod
    fun listDir(path: String, promise: Promise) {
        Thread {
            try {
                val root = rootDoc() ?: run { promise.reject("NOT_CONNECTED", "Connect the Bryton first"); return@Thread }
                val dir = traverse(root, path)
                if (dir == null || !dir.isDirectory) { promise.reject("BRYTON_DIR_NOT_FOUND", "$path not found"); return@Thread }
                val names = Arguments.createArray()
                for (f in dir.listFiles()) if (!f.isDirectory) names.pushString(f.name)
                promise.resolve(names)
            } catch (e: Exception) { promise.reject("BRYTON_LIST_FAILED", e.message ?: "list failed") }
        }.start()
    }

    @ReactMethod
    fun writeFile(path: String, base64: String, promise: Promise) {
        Thread {
            try {
                val root = rootDoc() ?: run { promise.reject("NOT_CONNECTED", "Connect the Bryton first"); return@Thread }
                val segments = path.split("/").filter { it.isNotEmpty() }
                var dir: DocumentFile = root
                for (i in 0 until segments.size - 1) {
                    dir = childInsensitive(dir, segments[i]) ?: dir.createDirectory(segments[i])
                        ?: throw IllegalStateException("could not create ${segments[i]}")
                }
                val fileName = segments.last()
                // Replace any existing file so the new content can't leave a stale tail (Profile.bin
                // is fixed-length, .fit small — exactness matters). SAF "wt" also truncates, but a
                // clean re-create avoids any provider that ignores the truncate mode.
                childInsensitive(dir, fileName)?.let { try { it.delete() } catch (_: Exception) {} }
                val target = dir.createFile("application/octet-stream", fileName)
                    ?: throw IllegalStateException("could not create $fileName")
                val bytes = android.util.Base64.decode(base64, android.util.Base64.DEFAULT)
                reactContext.contentResolver.openOutputStream(target.uri, "wt")?.use { it.write(bytes) }
                    ?: throw IllegalStateException("could not open $fileName for writing")
                promise.resolve(true)
            } catch (e: Exception) { promise.reject("BRYTON_WRITE_FAILED", e.message ?: "write failed") }
        }.start()
    }

    @ReactMethod
    fun disconnect(promise: Promise) { promise.resolve(true) } // SAF grant persists; nothing to close

    // ─── helpers ───────────────────────────────────────────────────────────
    private fun traverse(from: DocumentFile, path: String): DocumentFile? {
        var cur: DocumentFile = from
        for (seg in path.split("/").filter { it.isNotEmpty() }) {
            cur = childInsensitive(cur, seg) ?: return null
        }
        return cur
    }
    private fun childInsensitive(dir: DocumentFile, name: String): DocumentFile? =
        dir.listFiles().firstOrNull { it.name?.equals(name, ignoreCase = true) == true }

    private fun readFully(ins: java.io.InputStream): ByteArray {
        val out = ByteArrayOutputStream()
        val buf = ByteArray(8192)
        while (true) { val n = ins.read(buf); if (n < 0) break; out.write(buf, 0, n) }
        return out.toByteArray()
    }
}

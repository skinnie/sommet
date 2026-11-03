package com.ambitsyncmodern.update

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod
import java.io.File

/*
 * App updates (André, 2026-11-03, 🟣124): AppUpdate.ts downloads the APK of a newer GitHub
 * release into the cache, and this hands it to Android's own installer, which asks the user
 * to confirm. Before that, the file must really be Sommet at the expected version: a wrong
 * or damaged download is refused here, and Android itself refuses an APK signed with another
 * key than the installed app. Allowing Sommet to install apps is the user's choice in
 * Android's settings; openInstallSettings() only opens that screen.
 */
class AppUpdateModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName() = "AppUpdate"

    // False until the user allows "Install unknown apps" for Sommet (Android 8+).
    @ReactMethod
    fun canInstall(promise: Promise) {
        val ok = Build.VERSION.SDK_INT < Build.VERSION_CODES.O ||
            reactContext.packageManager.canRequestPackageInstalls()
        promise.resolve(ok)
    }

    @ReactMethod
    fun openInstallSettings(promise: Promise) {
        try {
            val intent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                    Uri.parse("package:${reactContext.packageName}"))
            } else {
                Intent(Settings.ACTION_SECURITY_SETTINGS)
            }
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            reactContext.startActivity(intent)
            promise.resolve(null)
        } catch (e: Exception) {
            promise.reject("SETTINGS_ERROR", e.message ?: "Could not open the settings")
        }
    }

    // path: the downloaded APK (in cacheDir/updates/). expectedVersion: the release's version,
    // e.g. "0.2.58" - the APK's own versionName must match it.
    @ReactMethod
    fun installApk(path: String, expectedVersion: String, promise: Promise) {
        try {
            val file = File(path)
            val updatesDir = File(reactContext.cacheDir, "updates").canonicalFile
            if (!file.exists() || file.canonicalFile.parentFile != updatesDir) {
                promise.reject("APK_NOT_FOUND", "The download is missing")
                return
            }
            val pm = reactContext.packageManager
            val info = pm.getPackageArchiveInfo(file.path, 0)
            if (info == null) {
                promise.reject("APK_DAMAGED", "The download is not a complete app")
                return
            }
            if (info.packageName != reactContext.packageName || info.versionName != expectedVersion) {
                promise.reject("APK_MISMATCH",
                    "The download is ${info.packageName} ${info.versionName}, not Sommet $expectedVersion")
                return
            }
            val uri = FileProvider.getUriForFile(
                reactContext, "${reactContext.packageName}.fileprovider", file)
            val intent = Intent(Intent.ACTION_VIEW).apply {
                setDataAndType(uri, "application/vnd.android.package-archive")
                addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            reactContext.startActivity(intent)
            promise.resolve(null)
        } catch (e: Exception) {
            promise.reject("INSTALL_ERROR", e.message ?: "Could not open the installer")
        }
    }
}

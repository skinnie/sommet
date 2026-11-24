package com.ambitsyncmodern.location

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.net.wifi.WifiManager
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import androidx.core.content.ContextCompat
import com.facebook.react.bridge.Arguments
import com.facebook.react.bridge.Promise
import com.facebook.react.bridge.ReactApplicationContext
import com.facebook.react.bridge.ReactContextBaseJavaModule
import com.facebook.react.bridge.ReactMethod

// Where this tablet or phone is, for the POIs map to open there (André, 2026-11-23: "can we make
// the map appear on the tablet location?"). A position read recently is used at once; otherwise
// it listens for one fix until the timeout. Nothing here sends it anywhere; the weather uses it
// rounded to about 1 km (WeatherService.ts).
class DeviceLocationModule(private val ctx: ReactApplicationContext) : ReactContextBaseJavaModule(ctx) {
    override fun getName() = "DeviceLocation"

    private val main = Handler(Looper.getMainLooper())

    private fun allowed(): Boolean =
        ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED ||
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED

    private fun toMap(l: Location) = Arguments.createMap().apply {
        putDouble("lat", l.latitude)
        putDouble("lon", l.longitude)
        putDouble("accuracy", l.accuracy.toDouble())
        putDouble("ageSeconds", (System.currentTimeMillis() - l.time) / 1000.0)
    }

    /** The place as {lat, lon, accuracy, ageSeconds}, or null when there is none. */
    @ReactMethod
    fun current(timeoutMs: Double, promise: Promise) {
        val lm = ctx.getSystemService(Context.LOCATION_SERVICE) as? LocationManager
        if (lm == null || !allowed()) { promise.resolve(null); return }
        main.post {
            try {
                val enabled = lm.getProviders(true)
                val last = enabled.mapNotNull { p ->
                    try { lm.getLastKnownLocation(p) } catch (_: Exception) { null }
                }.maxByOrNull { it.time }
                if (last != null && System.currentTimeMillis() - last.time < FRESH_MS) {
                    promise.resolve(toMap(last)); return@post
                }
                val live = listOf(LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER)
                    .filter { it in enabled }
                if (live.isEmpty()) { promise.resolve(last?.let { toMap(it) }); return@post }
                listenOnce(lm, live, last, timeoutMs.toLong(), promise)
            } catch (_: SecurityException) {
                promise.resolve(null)
            }
        }
    }

    /** The Wi-Fi networks heard now, as [{bssid, level (dBm), ssid}], for a position by Wi-Fi
     *  when the owner switched it on (DeviceLocation.ts decides what of it is sent). */
    @ReactMethod
    fun wifiNearby(promise: Promise) {
        val out = Arguments.createArray()
        val wm = ctx.applicationContext.getSystemService(Context.WIFI_SERVICE) as? WifiManager
        if (wm == null || !allowed()) { promise.resolve(out); return }
        try {
            @Suppress("DEPRECATION") wm.startScan()       // results come later; the last scan is used now
            for (r in wm.scanResults) {
                out.pushMap(Arguments.createMap().apply {
                    putString("bssid", r.BSSID ?: "")
                    putInt("level", r.level)
                    @Suppress("DEPRECATION") putString("ssid", r.SSID ?: "")
                })
            }
        } catch (_: Exception) {}
        promise.resolve(out)
    }

    private fun listenOnce(lm: LocationManager, providers: List<String>, last: Location?,
                           timeoutMs: Long, promise: Promise) {
        var done = false
        lateinit var listener: LocationListener
        val finish = { found: Location? ->
            if (!done) {
                done = true
                try { lm.removeUpdates(listener) } catch (_: Exception) {}
                promise.resolve(found?.let { toMap(it) })
            }
        }
        // The three empty ones are abstract before Android 11, and this app runs from Android 9.
        listener = object : LocationListener {
            override fun onLocationChanged(location: Location) { finish(location) }
            @Deprecated("Called only before Android 11")
            override fun onStatusChanged(provider: String?, status: Int, extras: Bundle?) {}
            override fun onProviderEnabled(provider: String) {}
            override fun onProviderDisabled(provider: String) {}
        }
        providers.forEach { lm.requestLocationUpdates(it, 0L, 0f, listener, Looper.getMainLooper()) }
        main.postDelayed({ finish(last) }, timeoutMs)
    }

    private companion object {
        const val FRESH_MS = 10 * 60 * 1000L
    }
}

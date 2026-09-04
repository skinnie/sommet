package com.ambitsyncmodern.polarsleep

import android.util.Log
import com.ambitsyncmodern.BuildConfig
import com.facebook.react.bridge.*
import com.polar.sdk.api.PolarBleApi
import com.polar.sdk.api.PolarBleApiCallback
import com.polar.sdk.api.PolarBleApiDefaultImpl
import com.polar.sdk.api.model.PolarAccelerometerData
import com.polar.sdk.api.model.PolarDeviceInfo
import com.polar.sdk.api.model.PolarOfflineRecordingData
import com.polar.sdk.api.model.PolarOfflineRecordingEntry
import com.polar.sdk.api.model.PolarPpgData
import com.polar.sdk.api.model.PolarSensorSetting
import io.reactivex.rxjava3.disposables.CompositeDisposable
import io.reactivex.rxjava3.disposables.Disposable
import java.text.SimpleDateFormat
import java.util.Locale
import java.util.UUID

/*
 * Polar Verity Sense OVERNIGHT capture, so André can sleep with just the band and connect in the
 * morning. This is the offline-recording twin of HrStrapModule (which does a LIVE morning R-R spot
 * read over standard GATT): here the band RECORDS raw PPG + ACC to its own flash while disconnected,
 * and we fetch it later. All heavy DSP (PPG -> R-R -> HRV) stays in Python (tools/sleep_stage.py via
 * the backend) - this module only drives the band's offline-recording lifecycle and hands the raw
 * samples up as JSON. Read-only w.r.t. the WATCH (a different device entirely); it cannot brick it.
 *
 * JS surface (PolarSleepService.ts):
 *   search()                 -> [{deviceId, name, rssi}]   scan for Polar bands
 *   arm(deviceId)            -> {armed:true, types:[...]}   at bedtime: start PPG+ACC offline rec
 *   status(deviceId)         -> {recording:bool, types:[]}  is it still recording?
 *   fetchLatest(deviceId, delete) -> recording JSON         in the morning: download + assemble
 *   stop(deviceId)           -> {stopped:true}              stop offline recording early
 *
 * Requires the Polar BLE SDK (com.github.polarofficial:polar-ble-sdk) + RxJava3 in app/build.gradle.
 */
class PolarSleepModule(private val reactContext: ReactApplicationContext) :
    ReactContextBaseJavaModule(reactContext) {

    override fun getName() = "PolarSleep"

    private val disposables = CompositeDisposable()
    private var searchDisposable: Disposable? = null

    // The Verity records these two; PPG gives us the pulse (-> R-R -> HRV), ACC gives us motion
    // (the sleep-staging feature + the motion gate that rejects tossing-and-turning beats).
    private val dataTypes = listOf(
        PolarBleApi.PolarDeviceDataType.PPG,
        PolarBleApi.PolarDeviceDataType.ACC,
    )

    private val api: PolarBleApi by lazy {
        PolarBleApiDefaultImpl.defaultImplementation(
            reactContext.applicationContext,
            setOf(
                PolarBleApi.PolarBleSdkFeature.FEATURE_POLAR_OFFLINE_RECORDING,
                PolarBleApi.PolarBleSdkFeature.FEATURE_DEVICE_INFO,
                PolarBleApi.PolarBleSdkFeature.FEATURE_BATTERY_INFO,
            ),
        ).also { it.setApiCallback(callback) }
    }

    // Which deviceIds have finished connecting + are ready for offline-recording calls.
    private val ready = HashSet<String>()
    private val connected = HashSet<String>()

    private val callback = object : PolarBleApiCallback() {
        override fun deviceConnected(info: PolarDeviceInfo) { connected.add(info.deviceId) }
        override fun deviceDisconnected(info: PolarDeviceInfo) {
            connected.remove(info.deviceId); ready.remove(info.deviceId)
        }
        override fun bleSdkFeatureReady(id: String, feature: PolarBleApi.PolarBleSdkFeature) {
            if (feature == PolarBleApi.PolarBleSdkFeature.FEATURE_POLAR_OFFLINE_RECORDING) ready.add(id)
        }
    }

    companion object {
        private const val TAG = "PolarSleep"
        private const val CONNECT_TIMEOUT_MS = 20_000L
        private val ISO = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US)
    }

    // --- scan --------------------------------------------------------------------------------
    @ReactMethod
    fun search(promise: Promise) {
        val found = Arguments.createArray()
        val seen = HashSet<String>()
        searchDisposable?.dispose()
        searchDisposable = api.searchForDevice().subscribe(
            { info ->
                if (seen.add(info.deviceId)) {
                    found.pushMap(Arguments.createMap().apply {
                        putString("deviceId", info.deviceId)
                        putString("name", info.name)
                        putInt("rssi", info.rssi)
                    })
                }
            },
            { e -> promise.reject("SEARCH_FAILED", e.message, e) },
        )
        // Collect for a few seconds, then return whatever we saw.
        reactContext.runOnUiQueueThread {
            android.os.Handler(reactContext.mainLooper).postDelayed({
                searchDisposable?.dispose(); searchDisposable = null
                promise.resolve(found)
            }, 6_000L)
        }
    }

    // --- connect helper: connect (if needed) and wait until offline-recording is ready -------
    private fun withReady(deviceId: String, promise: Promise, block: (String) -> Unit) {
        if (ready.contains(deviceId)) { block(deviceId); return }
        try {
            if (!connected.contains(deviceId)) api.connectToDevice(deviceId)
        } catch (e: Exception) {
            promise.reject("CONNECT_FAILED", e.message, e); return
        }
        // Poll for readiness (bleSdkFeatureReady arrives async) up to the timeout.
        val handler = android.os.Handler(reactContext.mainLooper)
        val started = System.currentTimeMillis()
        val poll = object : Runnable {
            override fun run() {
                when {
                    ready.contains(deviceId) -> block(deviceId)
                    System.currentTimeMillis() - started > CONNECT_TIMEOUT_MS ->
                        promise.reject("NOT_READY", "Band did not become ready in time (is it on/charged/in range?)")
                    else -> handler.postDelayed(this, 300)
                }
            }
        }
        handler.postDelayed(poll, 300)
    }

    // --- arm: start PPG + ACC offline recording at their max sample rate ----------------------
    @ReactMethod
    fun arm(deviceId: String, promise: Promise) {
        withReady(deviceId, promise) { id ->
            val started = Arguments.createArray()
            var pending = dataTypes.size
            var failed = false
            for (type in dataTypes) {
                disposables.add(
                    api.requestOfflineRecordingSettings(id, type)
                        .flatMapCompletable { setting ->
                            api.startOfflineRecording(id, type, setting.maxSettings(), null)
                        }
                        .subscribe(
                            {
                                started.pushString(type.name)
                                if (--pending == 0 && !failed) resolveArmed(promise, started)
                            },
                            { e ->
                                // ALREADY_IN_STATE means it's already recording that type - treat as success.
                                if (e.message?.contains("ALREADY", true) == true) {
                                    started.pushString(type.name)
                                    if (--pending == 0 && !failed) resolveArmed(promise, started)
                                } else if (!failed) {
                                    failed = true
                                    promise.reject("ARM_FAILED", "Could not start ${type.name} recording: ${e.message}", e)
                                }
                            },
                        ),
                )
            }
        }
    }

    private fun resolveArmed(promise: Promise, started: WritableArray) {
        promise.resolve(Arguments.createMap().apply {
            putBoolean("armed", true)
            putArray("types", started)
        })
    }

    // --- status: is it still recording, and which types --------------------------------------
    @ReactMethod
    fun status(deviceId: String, promise: Promise) {
        withReady(deviceId, promise) { id ->
            disposables.add(
                api.getOfflineRecordingStatus(id).subscribe(
                    { types ->
                        val arr = Arguments.createArray()
                        types.forEach { arr.pushString(it.name) }
                        promise.resolve(Arguments.createMap().apply {
                            putBoolean("recording", types.isNotEmpty())
                            putArray("types", arr)
                        })
                    },
                    { e -> promise.reject("STATUS_FAILED", e.message, e) },
                ),
            )
        }
    }

    // --- stop offline recording early --------------------------------------------------------
    @ReactMethod
    fun stop(deviceId: String, promise: Promise) {
        withReady(deviceId, promise) { id ->
            var pending = dataTypes.size
            for (type in dataTypes) {
                disposables.add(
                    api.stopOfflineRecording(id, type).subscribe(
                        { if (--pending == 0) promise.resolve(Arguments.createMap().apply { putBoolean("stopped", true) }) },
                        { e -> if (e.message?.contains("NOT", true) != true) Log.w(TAG, "stop ${type.name}: ${e.message}")
                               if (--pending == 0) promise.resolve(Arguments.createMap().apply { putBoolean("stopped", true) }) },
                    ),
                )
            }
        }
    }

    // --- fetchLatest: download the most recent PPG (+ its ACC), assemble the recording JSON ---
    @ReactMethod
    fun fetchLatest(deviceId: String, deleteAfter: Boolean, promise: Promise) {
        withReady(deviceId, promise) { id ->
            val entries = ArrayList<PolarOfflineRecordingEntry>()
            disposables.add(
                api.listOfflineRecordings(id).subscribe(
                    { entries.add(it) },
                    { e -> promise.reject("LIST_FAILED", e.message, e) },
                    { assembleFromEntries(id, entries, deleteAfter, promise) },
                ),
            )
        }
    }

    private fun assembleFromEntries(
        id: String,
        entries: List<PolarOfflineRecordingEntry>,
        deleteAfter: Boolean,
        promise: Promise,
    ) {
        if (entries.isEmpty()) {
            promise.reject("NO_RECORDING", "No offline recording on the band - was it armed at bedtime?")
            return
        }
        // Newest session = the PPG entry with the latest start date; pair it with the ACC entry
        // closest in time (same session).
        val ppgEntry = entries.filter { it.type == PolarBleApi.PolarDeviceDataType.PPG }
            .maxByOrNull { it.date.time }
        if (ppgEntry == null) {
            promise.reject("NO_PPG", "Recording has no PPG - can't derive HRV")
            return
        }
        val accEntry = entries.filter { it.type == PolarBleApi.PolarDeviceDataType.ACC }
            .minByOrNull { Math.abs(it.date.time - ppgEntry.date.time) }

        disposables.add(
            api.getOfflineRecord(id, ppgEntry, null).subscribe(
                { ppgRec ->
                    val ppg = (ppgRec as? PolarOfflineRecordingData.PpgOfflineRecording)
                    if (ppg == null) { promise.reject("BAD_PPG", "Unexpected PPG record type"); return@subscribe }
                    if (accEntry == null) {
                        finishRecording(id, ppg, null, ppgEntry, null, deleteAfter, promise)
                    } else {
                        disposables.add(
                            api.getOfflineRecord(id, accEntry, null).subscribe(
                                { accRec ->
                                    finishRecording(id, ppg,
                                        accRec as? PolarOfflineRecordingData.AccOfflineRecording,
                                        ppgEntry, accEntry, deleteAfter, promise)
                                },
                                { // ACC failed - still return PPG-only (HRV works without motion gate)
                                    finishRecording(id, ppg, null, ppgEntry, null, deleteAfter, promise)
                                },
                            ),
                        )
                    }
                },
                { e -> promise.reject("FETCH_FAILED", e.message, e) },
            ),
        )
    }

    private fun finishRecording(
        id: String,
        ppg: PolarOfflineRecordingData.PpgOfflineRecording,
        acc: PolarOfflineRecordingData.AccOfflineRecording?,
        ppgEntry: PolarOfflineRecordingEntry,
        accEntry: PolarOfflineRecordingEntry?,
        deleteAfter: Boolean,
        promise: Promise,
    ) {
        val out = Arguments.createMap()
        out.putString("start_time", ISO.format(ppg.startTime.time))
        out.putDouble("ppg_hz", sampleRateOf(ppg.settings))
        // PPG: one row per sample = its channel list. sleep_stage.py picks the best channel.
        val ppgArr = Arguments.createArray()
        for (s in ppg.data.samples) {
            val row = Arguments.createArray()
            for (c in s.channelSamples) row.pushInt(c)
            ppgArr.pushArray(row)
        }
        out.putArray("ppg", ppgArr)
        if (acc != null) {
            out.putDouble("acc_hz", sampleRateOf(acc.settings))
            val accArr = Arguments.createArray()
            for (s in acc.data.samples) {
                val row = Arguments.createArray()
                row.pushInt(s.x); row.pushInt(s.y); row.pushInt(s.z)
                accArr.pushArray(row)
            }
            out.putArray("acc", accArr)
        }
        // Optionally free the band's flash now that we have the samples.
        if (deleteAfter) {
            disposables.add(api.removeOfflineRecord(id, ppgEntry).subscribe({}, { Log.w(TAG, "rm ppg: ${it.message}") }))
            accEntry?.let { disposables.add(api.removeOfflineRecord(id, it).subscribe({}, { Log.w(TAG, "rm acc: ${it.message}") })) }
        }
        promise.resolve(out)
    }

    private fun sampleRateOf(setting: PolarSensorSetting?): Double {
        val hz = setting?.settings?.get(PolarSensorSetting.SettingType.SAMPLE_RATE)?.maxOrNull()
        return (hz ?: 0).toDouble()
    }

    override fun invalidate() {
        super.invalidate()
        try { searchDisposable?.dispose(); disposables.clear(); api.cleanup() } catch (_: Exception) {}
    }

    // Keep an unused reference so BuildConfig/UUID imports don't trip lint if trimmed later.
    @Suppress("unused") private fun debugTag() = "$TAG/${BuildConfig.APPLICATION_ID}/${UUID.randomUUID()}"
}

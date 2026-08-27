#include <jni.h>
#include <android/log.h>
#include <string>
#include <sstream>
#include <iomanip>
#include <cstring>
#include <ctime>
#include <vector>
#include <set>
#include <cmath>
#include <pthread.h>

// ─── libambit ─────────────────────────────────────────────────────────────────
#include "libambit/libambit.h"
#include "libambit/libambit_int.h"
#include "device_driver_ambit3_navigation.h"
// pmem20.h has no extern "C" guard (like protocol.h) - wrap it so its libambit_pmem20_data_write
// (the chunked 0x0b16 raw flash write) links against the C libambit.a. Used by
// nativeAmbitWriteLegacyRegion for the Ambit1/2 sport-mode + nav restore writes.
extern "C" {
#include "libambit/pmem20.h"
}

// libambit_protocol_command lives in protocol.h, which (unlike libambit.h) has no extern "C"
// guard - including it from this C++ TU would mangle the name and fail to link against the C
// libambit.a. Forward-declare it with C linkage instead (used for the legacy 0x0b00/0x0b01
// personal-settings read-modify-write). Buffers are freed with plain free() - the existing
// nativeAmbitReadPoiListRaw comment confirms libambit_protocol_free() is exactly that.
extern "C" int libambit_protocol_command(ambit_object_t *object, uint16_t command,
                                         uint8_t *data, size_t datalen,
                                         uint8_t **reply_data, size_t *replylen,
                                         uint8_t legacy_format);

// libambit_new_from_fd() est déclaré dans libambit_android.c
extern "C" ambit_object_t *libambit_new_from_fd(int fd, int ep_in, int ep_out,
                                                 uint16_t vid, uint16_t pid);

// libambit_new_from_ble() is declared in libambit_android.c. BLE-only —
// see protocol_ble.c and HANDOFF.md Milestone 7 (2026-08-06 entry) for why
// USB and BLE need separate constructors (different wire framing, not just
// a different transport under the same framing).
extern "C" ambit_object_t *libambit_new_from_ble(JavaVM *jvm, jobject module_ref_local,
                                                  JNIEnv *env, uint16_t vid, uint16_t pid);

// ambit_ble_on_notify() is declared in protocol_ble.c — feeds raw GATT
// notification bytes into the BLE frame reassembly/CRC-check logic.
extern "C" void ambit_ble_on_notify(ambit_object_t *object, const uint8_t *data, size_t len);

// ambit3_write_route_to_watch() est déclarée dans device_driver_ambit3.c
extern "C" int ambit3_write_route_to_watch(ambit_object_t *object,
                                            const ambit3_nav_route_t *routes,
                                            size_t route_count);

// ambit3_add_poi_to_watch() est déclarée dans device_driver_ambit3.c
extern "C" int ambit3_add_poi_to_watch(ambit_object_t *object,
                                        const char *name, double lat, double lon, int type);

// ambit3_read_flash_region() / ambit3_read_poi_list_raw() / ambit3_read_object_by_id_raw()
// sont déclarées dans device_driver_ambit3.c
extern "C" int ambit3_read_flash_region(ambit_object_t *object, uint32_t address, uint32_t length, uint8_t *out_buffer);
extern "C" int ambit3_read_poi_list_raw(ambit_object_t *object, uint8_t **out, size_t *out_len);
extern "C" int ambit3_read_memory_map_raw(ambit_object_t *object, uint8_t **out, size_t *out_len);
// Firmware flasher (firmware_flash_android.c). See its header comment for the re-enumeration
// dance Kotlin orchestrates around these.
extern "C" int ambit3_fw_enter_bsl(ambit_object_t *object);
extern "C" int ambit3_fw_reboot(ambit_object_t *object);
extern "C" int ambit3_fw_stream(ambit_object_t *object, const uint8_t *header, size_t header_len,
                                const uint8_t *payload, size_t payload_len, int do_commit, int resume);
extern "C" int ambit3_read_object_by_id_raw(ambit_object_t *object, uint8_t entry_id, uint8_t **out, size_t *out_len);
extern "C" int ambit3_read_settings_raw(ambit_object_t *object, uint8_t **out, size_t *out_len);
extern "C" int ambit3_write_settings_raw(ambit_object_t *object, const uint8_t *data, size_t datalen, uint8_t **out, size_t *out_len);
extern "C" int ambit3_read_custom_modes_raw(ambit_object_t *object, uint8_t *out_buffer);
extern "C" int ambit3_write_custom_modes_raw(ambit_object_t *object, const uint8_t *data, size_t datalen);
extern "C" int ambit3_write_region_raw(ambit_object_t *object, uint32_t base, const uint8_t *data, size_t extent);

#undef  LOG_TAG
#define LOG_TAG "AmbitJNI"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)

// ─── Base64 (pour renvoyer des blobs bruts — régions flash, liste POI — à JS) ──

static std::string base64Encode(const uint8_t *data, size_t len)
{
    static const char table[] =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve(((len + 2) / 3) * 4);
    size_t i = 0;
    while (i + 3 <= len) {
        uint32_t n = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
        out += table[(n >> 18) & 0x3F];
        out += table[(n >> 12) & 0x3F];
        out += table[(n >> 6) & 0x3F];
        out += table[n & 0x3F];
        i += 3;
    }
    size_t rem = len - i;
    if (rem == 1) {
        uint32_t n = data[i] << 16;
        out += table[(n >> 18) & 0x3F];
        out += table[(n >> 12) & 0x3F];
        out += "==";
    } else if (rem == 2) {
        uint32_t n = (data[i] << 16) | (data[i + 1] << 8);
        out += table[(n >> 18) & 0x3F];
        out += table[(n >> 12) & 0x3F];
        out += table[(n >> 6) & 0x3F];
        out += "=";
    }
    return out;
}

static std::string jsonEscape(const char *s)
{
    std::string out;
    if (!s) return out;
    for (const char *p = s; *p; p++) {
        if (*p == '"' || *p == '\\') out += '\\';
        if ((unsigned char)*p < 0x20) continue; // drop control chars, keep it simple
        out += *p;
    }
    return out;
}

// ─── État global ──────────────────────────────────────────────────────────────

static ambit_object_t *g_device = nullptr;

// Cache des logs lus (rempli lors de nativeAmbitGetLogCount, consommé par nativeAmbitGetLogAsGpx)
static std::vector<std::string> g_log_cache;

// Parallel to g_log_cache: each read move's own header date_time, kept so
// nativeAmbitMarkReadLogsSynced can rebuild the minimal ambit_log_entry_t that
// libambit_log_synced() needs (it reads only header.date_time - see
// device_driver_ambit3.c's log_synced()). Same index as g_log_cache/getLogAsGpx.
static std::vector<ambit_date_time_t> g_log_dates;

// Publishes the in-construction BLE object to g_device DURING libambit_new_from_ble,
// so incoming notifications route to it while the server-side handshake (which runs
// inside that call, before it returns) is waiting for the watch's frames. Without
// this, nativeAmbitBleOnNotify's `if (!g_device) return` drops every handshake frame
// because g_device isn't assigned until the call returns. Called from
// libambit_android.c. (2026-08-09, HANDOFF.md Milestone 7 item 9.)
extern "C" void jni_ble_set_active_object(ambit_object_t *obj) {
    g_device = obj;
}

// ─── Pre-init RX stash (2026-08-09) ────────────────────────────────────────────
// The watch's opening frame reaches nativeAmbitBleOnNotify (binder thread) the
// instant it subscribes to our notify CCCD — which is BEFORE nativeAmbitBleInit
// (executor thread) has run far enough to publish g_device. The old
// `if (!g_device) return` guard silently dropped those bytes. The Ambit3 masks
// this by re-sending its 0x1201 opener every ~5s, but the Kailash (Hoopoe) sends
// its 0x0002 hello exactly once, so the dropped hello hung the handshake for the
// full 20s timeout with zero frames seen.
//
// Fix: until the handshake is armed (g_rx_ready), stash incoming bytes in order
// instead of dropping them; the handshake calls jni_ble_flush_rx_stash() once
// g_device + handshake_mode are live to replay them and switch to live feed. The
// stash-vs-live decision, the replay, and the ready flip all happen under
// g_rx_mtx, so a write racing the flip is serialized (stashed-then-replayed or
// fed-live, never lost or reordered). Armed fresh at scanAndConnect() time via
// jni_ble_reset_rx_stash() (before the watch can write), which also covers
// reconnects without an app restart.
static pthread_mutex_t g_rx_mtx = PTHREAD_MUTEX_INITIALIZER;
static std::vector<uint8_t> g_rx_stash;
static bool g_rx_ready = false;   // false: stash incoming bytes; true: feed live

extern "C" void jni_ble_reset_rx_stash(void) {
    pthread_mutex_lock(&g_rx_mtx);
    g_rx_ready = false;
    g_rx_stash.clear();
    pthread_mutex_unlock(&g_rx_mtx);
}

extern "C" void jni_ble_flush_rx_stash(void) {
    pthread_mutex_lock(&g_rx_mtx);
    if (g_device && !g_rx_stash.empty()) {
        LOGI("BLE rx stash: replaying %zu pre-init bytes into the handshake",
             g_rx_stash.size());
        ambit_ble_on_notify(g_device, g_rx_stash.data(), g_rx_stash.size());
    }
    g_rx_stash.clear();
    g_rx_ready = true;
    pthread_mutex_unlock(&g_rx_mtx);
}

// IDs des activités déjà synchronisées — format "YYYYMMDDTHHMMSS"
// Rempli par nativeAmbitGetLogCount avant chaque lecture
static std::set<std::string> g_known_dates;

// Formate la date d'un header en ID comparable à ceux stockés en DB
static std::string formatLogId(const ambit_log_header_t *h)
{
    char buf[20];
    snprintf(buf, sizeof(buf), "%04d%02d%02dT%02d%02d%02d",
             h->date_time.year, h->date_time.month, h->date_time.day,
             h->date_time.hour, h->date_time.minute,
             (int)(h->date_time.msec / 1000));
    return std::string(buf);
}

// ─── Conversion log → GPX ─────────────────────────────────────────────────────
//
// ambit_log_entry_t contient :
//   - header.date_time  (ambit_date_time_t : year/month/day/hour/minute/msec)
//   - header.activity_name, header.duration (ms), header.distance (m), header.ascent (m)
//   - samples : tableau de ambit_log_sample_t
//
// Types de samples avec coordonnées GPS :
//   ambit_log_sample_type_gps_base  → u.gps_base.latitude/longitude/altitude (×10^-7, ×0.01 m)
//   ambit_log_sample_type_gps_small → u.gps_small.latitude/longitude
//   ambit_log_sample_type_gps_tiny  → u.gps_tiny.latitude/longitude
//   ambit_log_sample_type_periodic  → u.periodic.values[] contenant lat/lon séparément

static std::string convertEntryToGpx(const ambit_log_entry_t *entry)
{
    std::ostringstream gpx;

    // Formater la date ISO 8601
    char date_buf[32];
    snprintf(date_buf, sizeof(date_buf), "%04d-%02d-%02dT%02d:%02d:%02dZ",
             entry->header.date_time.year,
             entry->header.date_time.month,
             entry->header.date_time.day,
             entry->header.date_time.hour,
             entry->header.date_time.minute,
             (int)(entry->header.date_time.msec / 1000));

    // activity_name peut être non-null mais pointer vers une chaîne vide (NUL)
    const char *act_raw  = entry->header.activity_name;
    bool        act_ok   = act_raw && act_raw[0] != '\0';
    uint8_t     act_type = entry->header.activity_type;

    __android_log_print(ANDROID_LOG_DEBUG, "AmbitJNI",
        "Activity: name='%s' sport_type=0x%02x(%d)",
        act_raw ? act_raw : "(null)", act_type, act_type);

    gpx << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        << "<gpx version=\"1.1\" creator=\"AmbitSyncModern\""
        << " xmlns=\"http://www.topografix.com/GPX/1/1\""
        << " xmlns:gpxtpx=\"http://www.garmin.com/xmlschemas/TrackPointExtension/v1\">\n"
        << "  <metadata><time>" << date_buf << "</time></metadata>\n"
        << "  <trk><name>" << (act_ok ? act_raw : "") << "</name>\n"
        << "    <extensions>\n"
        << "      <duration>"    << entry->header.duration / 1000 << "</duration>\n"
        << "      <distance>"    << entry->header.distance         << "</distance>\n"
        << "      <ascent>"      << entry->header.ascent           << "</ascent>\n"
        << "      <sport_type>"  << (int)act_type                  << "</sport_type>\n"
        << "    </extensions>\n"
        << "  <trkseg>\n";

    // État GPS courant — mis à jour par gps_base, complété par gps_small/tiny/periodic
    double cur_lat = 0.0, cur_lon = 0.0, cur_ele = 0.0;
    bool   has_pos = false;
    uint32_t cur_time_ms = 0;  // temps depuis le début en ms

    // Calculer le timestamp Unix de début (approximatif, seconds depuis epoch)
    struct tm start_tm = {};
    start_tm.tm_year  = entry->header.date_time.year  - 1900;
    start_tm.tm_mon   = entry->header.date_time.month - 1;
    start_tm.tm_mday  = entry->header.date_time.day;
    start_tm.tm_hour  = entry->header.date_time.hour;
    start_tm.tm_min   = entry->header.date_time.minute;
    start_tm.tm_sec   = (int)(entry->header.date_time.msec / 1000);
    time_t start_epoch = mktime(&start_tm);

    for (uint32_t i = 0; i < entry->samples_count; i++) {
        const ambit_log_sample_t &s = entry->samples[i];
        cur_time_ms = s.time;

        bool emit = false;

        if (s.type == ambit_log_sample_type_gps_base) {
            cur_lat = s.u.gps_base.latitude  / 1e7;
            cur_lon = s.u.gps_base.longitude / 1e7;
            cur_ele = s.u.gps_base.altitude  / 100.0;  // cm → m
            has_pos = true;
            emit    = true;
        }
        else if (s.type == ambit_log_sample_type_gps_small) {
            cur_lat = s.u.gps_small.latitude  / 1e7;
            cur_lon = s.u.gps_small.longitude / 1e7;
            has_pos = true;
            emit    = true;
        }
        else if (s.type == ambit_log_sample_type_gps_tiny) {
            cur_lat = s.u.gps_tiny.latitude  / 1e7;
            cur_lon = s.u.gps_tiny.longitude / 1e7;
            has_pos = true;
            emit    = true;
        }
        else if (s.type == ambit_log_sample_type_periodic && has_pos) {
            // Les samples périodiques peuvent contenir lat/lon séparément
            double lat = cur_lat, lon = cur_lon;
            bool lat_ok = false, lon_ok = false;
            for (uint8_t v = 0; v < s.u.periodic.value_count; v++) {
                const ambit_log_sample_periodic_value_t &pv = s.u.periodic.values[v];
                if (pv.type == ambit_log_sample_periodic_type_latitude)  { lat = pv.u.latitude  / 1e7; lat_ok = true; }
                if (pv.type == ambit_log_sample_periodic_type_longitude) { lon = pv.u.longitude / 1e7; lon_ok = true; }
                // Ne pas écraser cur_ele avec l'altitude barométrique periodique :
                // elle diverge souvent de l'altitude GPS (gps_base) et cause des D+ délirants.
            }
            if (lat_ok && lon_ok) { cur_lat = lat; cur_lon = lon; emit = true; }
        }

        if (emit && has_pos && (cur_lat != 0.0 || cur_lon != 0.0)) {
            time_t point_epoch = start_epoch + (time_t)(cur_time_ms / 1000);
            struct tm *ptm = gmtime(&point_epoch);
            char time_buf[32];
            strftime(time_buf, sizeof(time_buf), "%Y-%m-%dT%H:%M:%SZ", ptm);

            gpx << std::fixed << std::setprecision(7)
                << "    <trkpt lat=\"" << cur_lat << "\" lon=\"" << cur_lon << "\">"
                << "<ele>" << std::setprecision(1) << cur_ele << "</ele>"
                << "<time>" << time_buf << "</time>"
                << "</trkpt>\n";
        }
    }

    gpx << "  </trkseg></trk>\n</gpx>";
    return gpx.str();
}

// ─── Callback libambit_log_read ────────────────────────────────────────────────

static void log_push_callback(void *userdata, ambit_log_entry_t *log_entry)
{
    (void)userdata;
    std::string gpx = convertEntryToGpx(log_entry);
    g_log_cache.push_back(gpx);
    g_log_dates.push_back(log_entry->header.date_time);   // for mark-synced (same index)
    LOGI("log_push_callback: log #%zu ajouté (%zu bytes)",
         g_log_cache.size(), gpx.size());
    // Ne pas libérer ici : device_driver_ambit.c appelle libambit_log_entry_free après push_cb
}

static int log_skip_callback(void *userdata, ambit_log_header_t *log_header)
{
    (void)userdata;
    if (g_known_dates.empty()) return 1;  // rien à skipper
    std::string id = formatLogId(log_header);
    int skip = g_known_dates.count(id) ? 0 : 1;
    if (skip == 0) LOGI("log_skip_callback: skip %s (déjà synchro)", id.c_str());
    return skip;  // 0 = skipper, 1 = lire
}

// ─── JNI ──────────────────────────────────────────────────────────────────────

extern "C" {

/**
 * nativeAmbitInit
 *
 * @param fd     FileDescriptor de la connexion USB Android
 * @param epIn   Adresse endpoint interrupt IN  (ex: 0x81)
 * @param epOut  Adresse endpoint interrupt OUT (ex: 0x01), ou -1
 * @param vid    Vendor ID Suunto (0x1493)
 * @param pid    Product ID Suunto (0x001C Ambit3 Sport, 0x0010 Ambit 1…)
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitInit(
        JNIEnv * /* env */, jobject /* thiz */,
        jint fd, jint epIn, jint epOut, jint vid, jint pid)
{
    LOGI("nativeAmbitInit fd=%d epIn=0x%02x epOut=0x%02x vid=0x%04x pid=0x%04x",
         fd, epIn, epOut, vid, pid);

    // Fermer un éventuel device précédent
    if (g_device) {
        libambit_close(g_device);
        g_device = nullptr;
    }
    g_log_cache.clear();
    g_log_dates.clear();

    g_device = libambit_new_from_fd(fd, epIn, epOut,
                                    (uint16_t)vid, (uint16_t)pid);
    if (!g_device) {
        LOGE("libambit_new_from_fd failed : VID/PID 0x%04x/0x%04x non supporté", vid, pid);
        return JNI_FALSE;
    }

    LOGI("Ambit initialisé avec succès (driver sélectionné)");
    return JNI_TRUE;
}

/**
 * nativeAmbitGetDeviceInfo
 *
 * Returns a JSON string: {"model":"...","serial":"...","fwVersion":"X.Y.Z",
 * "hwVersion":"X.Y.Z","battery":N}.
 *
 * model/serial/fwVersion/hwVersion come from device_info, already populated
 * once at connect time for both USB and BLE (see libambit_android.c) — no
 * extra round-trip to the watch needed here. The third version component is
 * a 16-bit little-endian value (version[2] | version[3]<<8), not a plain
 * byte — confirmed against a hardware-verified reference implementation
 * (device_info.py in the companion desktop project), matching the formatting
 * version_string() in libambit.c already uses.
 *
 * battery is a live read (ambit_command_status / 0x0306, charge = reply
 * byte[1]) since it changes over time and isn't cached in device_info.
 * -1 if the read fails — non-fatal, caller should just hide the battery row.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitGetDeviceInfo(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("Not initialized"); return env->NewStringUTF("{}"); }

    const uint8_t *fw = g_device->device_info.fw_version;
    const uint8_t *hw = g_device->device_info.hw_version;
    char fw_buf[16];
    char hw_buf[16];
    snprintf(fw_buf, sizeof(fw_buf), "%d.%d.%d", fw[0], fw[1], fw[2] | (fw[3] << 8));
    snprintf(hw_buf, sizeof(hw_buf), "%d.%d.%d", hw[0], hw[1], hw[2] | (hw[3] << 8));

    ambit_device_status_t status;
    int battery = -1;
    if (libambit_device_status_get(g_device, &status) == 0) {
        battery = status.charge;
    } else {
        LOGE("Failed to read battery status");
    }

    std::ostringstream json;
    json << "{"
         << "\"model\":\""      << jsonEscape(g_device->device_info.model)  << "\","
         << "\"serial\":\""     << jsonEscape(g_device->device_info.serial) << "\","
         << "\"fwVersion\":\""  << fw_buf << "\","
         << "\"hwVersion\":\""  << hw_buf << "\","
         << "\"battery\":"      << battery
         << "}";

    return env->NewStringUTF(json.str().c_str());
}

/**
 * nativeAmbitGetLogCount
 *
 * Lit TOUS les logs depuis la montre et les met en cache (g_log_cache).
 * Retourne le nombre de logs lus, ou -1 en cas d'erreur.
 * Opération synchrone longue — appelée depuis un thread background Kotlin.
 */
JNIEXPORT jint JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitGetLogCount(
        JNIEnv *env, jobject /* thiz */, jobjectArray knownDates)
{
    if (!g_device) { LOGE("Not initialized"); return -1; }

    // Charger les IDs déjà connus pour le skip_callback
    g_known_dates.clear();
    if (knownDates) {
        jsize n = env->GetArrayLength(knownDates);
        for (jsize i = 0; i < n; i++) {
            auto jstr = (jstring)env->GetObjectArrayElement(knownDates, i);
            const char *s = env->GetStringUTFChars(jstr, nullptr);
            g_known_dates.insert(s);
            env->ReleaseStringUTFChars(jstr, s);
            env->DeleteLocalRef(jstr);
        }
        LOGI("nativeAmbitGetLogCount: %zu IDs connus (skip activé)", g_known_dates.size());
    }

    g_log_cache.clear();
    g_log_dates.clear();
    int ret = libambit_log_read(g_device,
                                log_skip_callback,
                                log_push_callback,
                                nullptr,   // progress_callback
                                nullptr);  // userdata
    if (ret < 0) {
        LOGE("libambit_log_read failed: %d", ret);
        return -1;
    }
    LOGI("nativeAmbitGetLogCount: %zu logs lus", g_log_cache.size());
    return (jint)g_log_cache.size();
}

/**
 * nativeAmbitGetLogAsGpx
 *
 * Retourne le GPX du log à l'index donné depuis le cache g_log_cache.
 * Doit être appelé après nativeAmbitGetLogCount.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitGetLogAsGpx(
        JNIEnv *env, jobject /* thiz */, jint index)
{
    if ((size_t)index >= g_log_cache.size()) {
        LOGE("nativeAmbitGetLogAsGpx: index %d hors limites (cache=%zu)",
             index, g_log_cache.size());
        return nullptr;
    }
    return env->NewStringUTF(g_log_cache[(size_t)index].c_str());
}

/**
 * nativeAmbitMarkReadLogsSynced
 *
 * Tells the watch that every move read this session (all of g_log_dates, filled by
 * nativeAmbitGetLogCount - the moves the watch actually sent, NOT the possibly-shorter GPX
 * array) is synced - the per-move flag SuuntoLink writes over cable so the Suunto app /
 * SuuntoLink don't duplicate it. Opt-in, experimental (Settings), OFF by default; the TS
 * layer decides whether the device SUPPORTS it (only Ambit3 GEN4 fw has a known log_synced
 * entry id, mirroring the desktop mark_synced.py guard) and only calls this for supported
 * watches. Driving the loop off g_log_dates here, rather than a caller-supplied index/count,
 * keeps marking tied to exactly the moves that were read - no dependence on the GPX list
 * length. libambit_log_synced reads only header.date_time, so a minimal stack entry with just
 * that field is enough. Best-effort: one failed move doesn't abort the rest. Returns the
 * number of moves marked, or -1 if not initialized. Nothing is deleted - the watch still
 * reclaims log space by wraparound.
 */
JNIEXPORT jint JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitMarkReadLogsSynced(
        JNIEnv * /* env */, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitMarkReadLogsSynced: not initialized"); return -1; }
    int marked = 0;
    for (size_t i = 0; i < g_log_dates.size(); i++) {
        ambit_log_entry_t entry;
        memset(&entry, 0, sizeof(entry));
        entry.header.date_time = g_log_dates[i];
        int r = libambit_log_synced(g_device, &entry);
        if (r != 0) {
            LOGE("nativeAmbitMarkReadLogsSynced: libambit_log_synced returned %d (move %zu)", r, i);
            continue;
        }
        marked++;
    }
    LOGI("nativeAmbitMarkReadLogsSynced: %d/%zu move(s) marked synced on watch",
         marked, g_log_dates.size());
    return marked;
}

/**
 * nativeAmbitSendSgee
 *
 * Envoie les éphémérides GPS à la montre via libambit_gps_orbit_write.
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitSendSgee(
        JNIEnv *env, jobject /* thiz */, jbyteArray data)
{
    if (!g_device) { LOGE("Not initialized"); return JNI_FALSE; }

    jsize len   = env->GetArrayLength(data);
    jbyte *bytes = env->GetByteArrayElements(data, nullptr);
    LOGI("nativeAmbitSendSgee: %d bytes", (int)len);

    int ret = libambit_gps_orbit_write(g_device, (uint8_t *)bytes, (size_t)len);
    env->ReleaseByteArrayElements(data, bytes, JNI_ABORT);

    if (ret != 0) { LOGE("libambit_gps_orbit_write failed: %d", ret); return JNI_FALSE; }
    return JNI_TRUE;
}

/**
 * nativeAmbitWriteRoute
 *
 * Writes a single route (already simplified to <= AMBIT3_MAX_ROUTE_POINTS
 * points on the JS side — see src/services/RouteSimplify.ts) to the watch's
 * navigation database, via ambit3_write_route_to_watch() in
 * device_driver_ambit3.c. Route points and waypoints are passed as parallel
 * primitive arrays since that's what crosses the JNI boundary cheaply;
 * everything else (GPX parsing, simplification, the caller-supplied
 * distance/ascent/descent/timestamp) already happened on the JS side.
 *
 * @return true on success. On false, check logcat tag "AmbitJNI" for which
 * step failed — the native side logs a specific reason for every failure
 * path (see device_driver_ambit3.c's ambit3_write_route_to_watch).
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitWriteRoute(
        JNIEnv *env, jobject /* thiz */,
        jstring routeName,
        jdoubleArray ptLat, jdoubleArray ptLon, jintArray ptAlt,
        jdoubleArray wptLat, jdoubleArray wptLon, jobjectArray wptName, jintArray wptPointIndex,
        jint distanceM, jint ascentM, jint descentM, jlong timestampSec)
{
    if (!g_device) { LOGE("nativeAmbitWriteRoute: Not connected"); return JNI_FALSE; }

    jsize point_count = env->GetArrayLength(ptLat);
    jsize waypoint_count = wptLat ? env->GetArrayLength(wptLat) : 0;

    if (point_count < 2) {
        LOGE("nativeAmbitWriteRoute: route has fewer than 2 points (%d)", point_count);
        return JNI_FALSE;
    }
    if (waypoint_count == 0) {
        // Not just a style nit: a route with zero on-device waypoints reads back
        // self-consistent (both CRCs match) but never appears in the watch's own
        // Navigation menu, confirmed on real hardware. The caller (RouteSimplify /
        // NavigationService) must always synthesize a Start/End pair when the
        // source GPX has no type="Waypoint" entries -- this is the last line of
        // defense, not the intended fix point.
        LOGE("nativeAmbitWriteRoute: route has no waypoints, refusing -- it would "
             "write successfully but never show up on the watch");
        return JNI_FALSE;
    }

    const char *name_utf8 = env->GetStringUTFChars(routeName, nullptr);

    jdouble *lat = env->GetDoubleArrayElements(ptLat, nullptr);
    jdouble *lon = env->GetDoubleArrayElements(ptLon, nullptr);
    jint    *alt = env->GetIntArrayElements(ptAlt, nullptr);

    std::vector<ambit3_nav_point_t> points(point_count);
    for (jsize i = 0; i < point_count; i++) {
        points[i].latitude  = (int32_t)llround(lat[i] * 1e7);
        points[i].longitude = (int32_t)llround(lon[i] * 1e7);
        points[i].altitude  = alt[i];
    }

    jdouble *wlat = env->GetDoubleArrayElements(wptLat, nullptr);
    jdouble *wlon = env->GetDoubleArrayElements(wptLon, nullptr);
    jint    *widx = env->GetIntArrayElements(wptPointIndex, nullptr);

    std::vector<ambit3_nav_waypoint_t> waypoints(waypoint_count);
    std::vector<std::string> wpt_names_utf8(waypoint_count); // keep alive until the call below
    for (jsize i = 0; i < waypoint_count; i++) {
        auto jname = (jstring)env->GetObjectArrayElement(wptName, i);
        const char *n = env->GetStringUTFChars(jname, nullptr);
        wpt_names_utf8[i] = n;
        env->ReleaseStringUTFChars(jname, n);
        env->DeleteLocalRef(jname);

        waypoints[i].latitude    = (int32_t)llround(wlat[i] * 1e7);
        waypoints[i].longitude   = (int32_t)llround(wlon[i] * 1e7);
        waypoints[i].point_index = (uint16_t)widx[i];
        strncpy(waypoints[i].name, wpt_names_utf8[i].c_str(), sizeof(waypoints[i].name) - 1);
        waypoints[i].name[sizeof(waypoints[i].name) - 1] = '\0';
    }

    // The route index's timestamp is seconds since an empirically-found, non-standard
    // epoch (1953-11-25T17:31:44 UTC) -- not Unix time. HANDOFF.md: "the exact epoch
    // ... is not a known round date", pinned to the second by matching a real capture,
    // never explained further. `timestampSec` below is a real Unix timestamp (also
    // used as-is for the calendar-component fields further down via gmtime_r); this
    // offset converts it to what the route index itself expects.
    static const int64_t AMBIT3_ROUTE_EPOCH_OFFSET_SEC = 508055296; // unix_epoch - route_epoch, precomputed

    ambit3_nav_route_t route = {};
    strncpy(route.name, name_utf8, sizeof(route.name) - 1);
    route.points = points.data();
    route.point_count = (uint16_t)point_count;
    route.distance = (uint32_t)distanceM;
    route.ascent = (uint16_t)ascentM;
    route.descent = (uint16_t)descentM;
    route.timestamp = (uint32_t)((int64_t)timestampSec + AMBIT3_ROUTE_EPOCH_OFFSET_SEC);
    route.waypoints = waypoints.data();
    route.waypoint_count = (uint16_t)waypoint_count;

    // month/day/hour/minute/second: the waypoint descriptor's own last-modification
    // stamp, no year (see tools/README.md). Derived from timestampSec, UTC.
    time_t t = (time_t)timestampSec;
    struct tm tmv;
    gmtime_r(&t, &tmv);
    route.month  = (uint8_t)(tmv.tm_mon + 1);
    route.day    = (uint8_t)tmv.tm_mday;
    route.hour   = (uint8_t)tmv.tm_hour;
    route.minute = (uint8_t)tmv.tm_min;
    route.second = (uint8_t)tmv.tm_sec;

    LOGI("nativeAmbitWriteRoute: writing '%s', %d points, %d waypoints", name_utf8, point_count, waypoint_count);
    int ret = ambit3_write_route_to_watch(g_device, &route, 1);

    env->ReleaseDoubleArrayElements(ptLat, lat, JNI_ABORT);
    env->ReleaseDoubleArrayElements(ptLon, lon, JNI_ABORT);
    env->ReleaseIntArrayElements(ptAlt, alt, JNI_ABORT);
    env->ReleaseDoubleArrayElements(wptLat, wlat, JNI_ABORT);
    env->ReleaseDoubleArrayElements(wptLon, wlon, JNI_ABORT);
    env->ReleaseIntArrayElements(wptPointIndex, widx, JNI_ABORT);
    env->ReleaseStringUTFChars(routeName, name_utf8);

    if (ret != 0) { LOGE("ambit3_write_route_to_watch failed: %d", ret); return JNI_FALSE; }
    return JNI_TRUE;
}

/**
 * nativeAmbitAddPoi
 *
 * Adds one POI to the watch, preserving every POI already there. Much
 * smaller/lower-risk than nativeAmbitWriteRoute: doesn't touch the
 * Waypoints/Routes flash regions at all, only the POI SBEM list.
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitAddPoi(
        JNIEnv *env, jobject /* thiz */,
        jstring name, jdouble lat, jdouble lon, jint type)
{
    if (!g_device) { LOGE("nativeAmbitAddPoi: Not connected"); return JNI_FALSE; }

    const char *name_utf8 = env->GetStringUTFChars(name, nullptr);
    LOGI("nativeAmbitAddPoi: adding '%s' (type %d)", name_utf8, (int)type);
    int ret = ambit3_add_poi_to_watch(g_device, name_utf8, (double)lat, (double)lon, (int)type);
    env->ReleaseStringUTFChars(name, name_utf8);

    if (ret != 0) { LOGE("ambit3_add_poi_to_watch failed: %d", ret); return JNI_FALSE; }
    return JNI_TRUE;
}

/**
 * nativeAmbitReadRegion
 *
 * Reads `length` bytes at `address` and returns them base64-encoded.
 * Decoding (routes/waypoints structures) happens in TS -- see RouteReader.ts.
 * Returns null on failure.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadRegion(
        JNIEnv *env, jobject /* thiz */,
        jlong address, jlong length)
{
    if (!g_device) { LOGE("nativeAmbitReadRegion: Not connected"); return nullptr; }
    // Real, 2026-08-08: Kailash's own TrackLog region is 1,310,713 bytes - the previous
    // 1024*1024 (1,048,576) cap was tight enough to reject it outright before this device
    // type existed to test against. Raised to 2MB, still a real sanity bound (every flash
    // region this project has ever seen, on any watch, is well under that), not removed.
    if (length <= 0 || length > 2 * 1024 * 1024) { LOGE("nativeAmbitReadRegion: implausible length %lld", (long long)length); return nullptr; }

    std::vector<uint8_t> buffer((size_t)length);
    LOGI("nativeAmbitReadRegion: reading 0x%06llx / %lld bytes", (long long)address, (long long)length);
    int ret = ambit3_read_flash_region(g_device, (uint32_t)address, (uint32_t)length, buffer.data());
    if (ret != 0) { LOGE("ambit3_read_flash_region failed: %d", ret); return nullptr; }

    std::string b64 = base64Encode(buffer.data(), buffer.size());
    return env->NewStringUTF(b64.c_str());
}

/**
 * nativeAmbitReadPoiListRaw
 *
 * Returns the watch's raw POI SBEM0102 reply (0x0b24), base64-encoded, or
 * null on failure / if the watch genuinely has none (an empty string is a
 * real, reachable "zero POIs" state, distinct from a read failure).
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadPoiListRaw(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitReadPoiListRaw: Not connected"); return nullptr; }

    uint8_t *raw = nullptr;
    size_t rawlen = 0;
    int ret = ambit3_read_poi_list_raw(g_device, &raw, &rawlen);
    if (ret != 0) {
        LOGE("ambit3_read_poi_list_raw failed: %d", ret);
        return nullptr;
    }
    std::string b64 = (raw && rawlen > 0) ? base64Encode(raw, rawlen) : std::string();
    // raw is malloc'd by libambit_protocol_command(); libambit_protocol_free() (protocol.h,
    // not included here) is verified to be exactly `if (data) free(data);`, so plain free()
    // is equivalent and avoids pulling in protocol.h just for this.
    free(raw);
    return env->NewStringUTF(b64.c_str());
}

/**
 * nativeAmbitReadMemoryMapRaw
 *
 * Per-device navigation port (2026-08-15). Returns the watch's raw 0x0b21 memory-map reply,
 * base64-encoded - the region table (Waypoints/Routes/CustomModes/Apps/GlonassSGEE/...) with
 * each region's own start+size. TS (MemoryMap.ts) parses it exactly like the companion
 * project's tools/write_nav.py read_memory_map(), so routes/POIs read from the addresses the
 * watch declares instead of the hardcoded Ambit3 Peak bases (which are wrong on a Traverse).
 * Null on failure.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadMemoryMapRaw(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitReadMemoryMapRaw: Not connected"); return nullptr; }

    uint8_t *raw = nullptr;
    size_t rawlen = 0;
    int ret = ambit3_read_memory_map_raw(g_device, &raw, &rawlen);
    if (ret != 0) {
        LOGE("ambit3_read_memory_map_raw failed: %d", ret);
        return nullptr;
    }
    LOGI("nativeAmbitReadMemoryMapRaw(0x0b21): %zu raw bytes", rawlen);
    std::string b64 = (raw && rawlen > 0) ? base64Encode(raw, rawlen) : std::string();
    free(raw);  // same equivalence as nativeAmbitReadPoiListRaw's own free(), see its comment
    return env->NewStringUTF(b64.c_str());
}

/* ─── Firmware flasher ────────────────────────────────────────────────────────
 * THE ONE WRITE THAT CAN BRICK. These three are called by Kotlin's firmwareFlash()
 * orchestrator across USB re-enumerations - see firmware_flash_android.c's header. Each acts
 * on whatever g_device is currently open (Kotlin re-inits g_device after each re-enumeration).
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitFwEnterBsl(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitFwEnterBsl: Not connected"); return JNI_FALSE; }
    return ambit3_fw_enter_bsl(g_device) == 0 ? JNI_TRUE : JNI_FALSE;
}

JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitFwReboot(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitFwReboot: Not connected"); return JNI_FALSE; }
    return ambit3_fw_reboot(g_device) == 0 ? JNI_TRUE : JNI_FALSE;
}

/* Streams header + payload to a watch already in BSL. doCommit=false stops before the
 * irreversible 0x0e03 (recoverable); doCommit=true flashes. Blocks for minutes. */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitFwStream(
        JNIEnv *env, jobject /* thiz */, jbyteArray header, jbyteArray payload, jboolean doCommit, jboolean resume)
{
    if (!g_device) { LOGE("nativeAmbitFwStream: Not connected"); return JNI_FALSE; }
    if (!header || !payload) { LOGE("nativeAmbitFwStream: null header/payload"); return JNI_FALSE; }

    jsize hlen = env->GetArrayLength(header);
    jsize plen = env->GetArrayLength(payload);
    jbyte *hbytes = env->GetByteArrayElements(header, nullptr);
    jbyte *pbytes = env->GetByteArrayElements(payload, nullptr);

    LOGI("nativeAmbitFwStream: header=%d payload=%d commit=%d resume=%d", (int)hlen, (int)plen, (int)doCommit, (int)resume);
    int ret = ambit3_fw_stream(g_device, (const uint8_t *)hbytes, (size_t)hlen,
                               (const uint8_t *)pbytes, (size_t)plen, doCommit ? 1 : 0, resume ? 1 : 0);

    env->ReleaseByteArrayElements(header, hbytes, JNI_ABORT);
    env->ReleaseByteArrayElements(payload, pbytes, JNI_ABORT);
    return ret == 0 ? JNI_TRUE : JNI_FALSE;
}

/**
 * nativeAmbitReadDeviceHistoryRaw
 *
 * Real, 2026-08-08 ("if we could import this data which is on the watch and read it to our
 * app would be awesome"). Returns the watch's raw sml.DeviceHistory reply (0x1200, entry
 * 0x67 - see ambit3_read_object_by_id_raw()'s own comment in device_driver_ambit3.c for how
 * this was found), base64-encoded. Decoding (SBEM0102 entries, the two real unit
 * conversions - Duration=raw/10, Location=float32 radians - and which entry IDs mean what)
 * happens in TS, mirroring the companion research project's own tools/kailash_history.py
 * exactly rather than re-deriving any of it here. Null on failure.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadDeviceHistoryRaw(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitReadDeviceHistoryRaw: Not connected"); return nullptr; }

    uint8_t *raw = nullptr;
    size_t rawlen = 0;
    int ret = ambit3_read_object_by_id_raw(g_device, 0x67, &raw, &rawlen);
    if (ret != 0) {
        LOGE("ambit3_read_object_by_id_raw(0x67) failed: %d", ret);
        return nullptr;
    }
    LOGI("nativeAmbitReadDeviceHistoryRaw(0x67): %zu raw bytes", rawlen);
    std::string b64 = (raw && rawlen > 0) ? base64Encode(raw, rawlen) : std::string();
    free(raw);  // same equivalence as nativeAmbitReadPoiListRaw's own free(), see its comment
    return env->NewStringUTF(b64.c_str());
}

/**
 * nativeAmbitReadDeviceLogRaw
 *
 * Kailash test hook (2026-08-09). Reads the watch's raw sml.DeviceLog reply
 * (0x1200, entry 0x53) — the EPHEMERAL per-activity GPS sample store, distinct
 * from the persistent DeviceHistory (0x67) summaries above. Same generic
 * ambit3_read_object_by_id_raw() path, different entry id. Base64-encoded, null
 * on failure. Diagnostic: this exists to confirm KAILASH-BLE-FINDINGS.md
 * Finding 7 live — whether DeviceLog returns real samples over an active BLE
 * session (and only before the 7R app drains it), or comes back empty. The raw
 * length is logged so a capture-free logcat read answers that. No TS decoder
 * yet — a non-empty length is the signal; full decode is the follow-up work.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadDeviceLogRaw(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitReadDeviceLogRaw: Not connected"); return nullptr; }

    uint8_t *raw = nullptr;
    size_t rawlen = 0;
    int ret = ambit3_read_object_by_id_raw(g_device, 0x53, &raw, &rawlen);
    if (ret != 0) {
        LOGE("ambit3_read_object_by_id_raw(0x53) failed: %d", ret);
        return nullptr;
    }
    LOGI("nativeAmbitReadDeviceLogRaw(0x53): %zu raw bytes", rawlen);
    std::string b64 = (raw && rawlen > 0) ? base64Encode(raw, rawlen) : std::string();
    free(raw);
    return env->NewStringUTF(b64.c_str());
}

/**
 * nativeAmbitReadSettingsRaw
 *
 * Real, 2026-08-08 ("Settings on ambit 3 - if they are already cracked to be changed by
 * cable, we will need to build a UI for it"). Returns the watch's raw sml.DeviceSettings
 * reply (0x1100, four zero bytes), base64-encoded. Decoding happens in TS
 * (AmbitSettingsReader.ts), mirroring the companion research project's own
 * tools/settings_write.py. Null on failure.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadSettingsRaw(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitReadSettingsRaw: Not connected"); return nullptr; }

    uint8_t *raw = nullptr;
    size_t rawlen = 0;
    int ret = ambit3_read_settings_raw(g_device, &raw, &rawlen);
    if (ret != 0) {
        LOGE("ambit3_read_settings_raw failed: %d", ret);
        return nullptr;
    }
    std::string b64 = (raw && rawlen > 0) ? base64Encode(raw, rawlen) : std::string();
    free(raw);
    return env->NewStringUTF(b64.c_str());
}

/**
 * nativeAmbitReadPersonalSettings
 *
 * Ambit 1 / Ambit 2 family (USB-only — these have no Bluetooth). Unlike the Ambit3/Kailash
 * SBEM sml.DeviceSettings (0x1100) path above, the older watches answer the legacy
 * personal-settings command, which libambit already parses into ambit_personal_settings_t
 * (device_driver_ambit.c / personal.c, from openambit). We surface the user-facing fields
 * as JSON; TS maps them to the same settings UI (AmbitPersonalSettingsReader.ts).
 *
 * READ-ONLY on purpose: libambit implements no personal-settings *write* (only the unused
 * 0x0b01 command id exists), and this project won't invent an unverified whole-blob write
 * to a 2012 watch — see the "prove it, don't brick it" rule this settings code already
 * follows for Ambit3. Null on failure.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadPersonalSettings(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitReadPersonalSettings: Not connected"); return nullptr; }

    ambit_personal_settings_t *ps = libambit_personal_settings_alloc();
    if (!ps) return nullptr;
    int ret = libambit_personal_settings_get(g_device, ps);
    if (ret != 0) {
        LOGE("libambit_personal_settings_get failed: %d", ret);
        libambit_personal_settings_free(ps);
        return nullptr;
    }
    std::ostringstream json;
    json << "{"
         << "\"date_format\":"            << (int)ps->date_format           << ","
         << "\"tones\":"                  << (int)ps->tones_mode            << ","
         << "\"gps_position_format\":"    << (int)ps->gps_position_format   << ","
         << "\"button_lock_sport_mode\":" << (int)ps->sportmode_button_lock << ","
         << "\"button_lock_time_mode\":"  << (int)ps->timemode_button_lock  << ","
         << "\"units_mode\":"             << (int)ps->units_mode            << ","
         << "\"language\":"               << (int)ps->language              << ","
         << "\"time_format\":"            << (int)ps->time_format           << ","
         << "\"gps_time_keeping\":"       << (int)ps->sync_time_w_gps       << ","
         << "\"backlight_mode\":"         << (int)ps->backlight_mode        << ","
         << "\"backlight_brightness\":"   << (int)ps->backlight_brightness  << ","
         << "\"display_dark\":"           << (int)ps->display_is_negative   << ","
         << "\"alti_baro_mode\":"         << (int)ps->alti_baro_mode        << ","
         << "\"storm_alarm\":"            << (int)ps->storm_alarm           << ","
         // Personal profile + compass declination (Ambit 1/2 read-only; no write exists in
         // the protocol - confirmed: no 0x0b01 in any Movescount capture). weight is kg*0.01,
         // length is height in cm, is_male 1/0, compass_declination in the watch's own units.
         << "\"weight\":"                 << (int)ps->weight                << ","
         << "\"birth_year\":"             << (int)ps->birthyear             << ","
         << "\"gender\":"                 << (int)ps->is_male               << ","
         << "\"height\":"                 << (int)ps->length                << ","
         << "\"max_hr\":"                 << (int)ps->max_hr                << ","
         << "\"rest_hr\":"                << (int)ps->rest_hr               << ","
         << "\"fitness_level\":"          << (int)ps->fitness_level
         << "}";
    libambit_personal_settings_free(ps);
    return env->NewStringUTF(json.str().c_str());
}

/**
 * nativeAmbitWritePersonalSetting
 *
 * Ambit 1/2 (Bluebird) legacy personal-settings WRITE - the 0x0b01 command openambit/libambit
 * only ever declared, reverse-engineered from a real SuuntoLink<->Ambit2 USB capture
 * (2026-08-26, docs/ambit2_protocol_findings.md): the settings struct is 188 bytes on the
 * Ambit2 (132 on the Ambit1), same field offsets. Read-modify-write: read the whole struct
 * (0x0b00), patch one field in place at `offset` (`width` 1 or 2, little-endian value), write
 * the whole thing back (0x0b01) at the device's OWN reply length so the Ambit2's extra tail is
 * preserved rather than truncated. Guarded to the Bluebird family and bounds-checked. Mirrors
 * the desktop tools/vendor/ambit_legacy_cli cmd_settings_write, hardware-verified there
 * (rest_hr round-trip on a real Ambit2). Returns JNI_TRUE on success.
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitWritePersonalSetting(
        JNIEnv * /*env*/, jobject /*thiz*/, jint offset, jint width, jint value)
{
    if (!g_device) { LOGE("nativeAmbitWritePersonalSetting: Not connected"); return JNI_FALSE; }
    uint16_t pid = g_device->device_info.product_id;
    // Bluebird family only: Ambit1 0x0010, Ambit2 0x0019, Ambit2S 0x001A, Ambit2R 0x001D.
    if (pid != 0x0010 && pid != 0x0019 && pid != 0x001A && pid != 0x001D) {
        LOGE("nativeAmbitWritePersonalSetting: not an Ambit1/2 (pid 0x%04x)", pid);
        return JNI_FALSE;
    }
    if (width != 1 && width != 2) { LOGE("nativeAmbitWritePersonalSetting: bad width %d", width); return JNI_FALSE; }

    uint8_t *reply = nullptr;
    size_t replylen = 0;
    if (libambit_protocol_command(g_device, 0x0b00, nullptr, 0, &reply, &replylen, 0) != 0
        || replylen < 132) {
        if (reply) free(reply);
        LOGE("nativeAmbitWritePersonalSetting: 0x0b00 read failed (len %zu)", replylen);
        return JNI_FALSE;
    }
    if ((size_t)(offset + width) > replylen) {
        free(reply);
        LOGE("nativeAmbitWritePersonalSetting: offset %d+%d beyond struct %zu", offset, width, replylen);
        return JNI_FALSE;
    }
    if (width == 2) {
        reply[offset] = (uint8_t)(value & 0xff);
        reply[offset + 1] = (uint8_t)((value >> 8) & 0xff);
    } else {
        reply[offset] = (uint8_t)value;
    }
    uint8_t *wreply = nullptr;
    size_t wlen = 0;
    int rc = libambit_protocol_command(g_device, 0x0b01, reply, replylen, &wreply, &wlen, 0);
    if (wreply) free(wreply);
    free(reply);
    if (rc != 0) {
        LOGE("nativeAmbitWritePersonalSetting: 0x0b01 write rc %d", rc);
        return JNI_FALSE;
    }
    return JNI_TRUE;
}

/*
 * nativeAmbitWriteLegacyRegion
 *
 * Ambit 1/2 (Bluebird) raw flash-region WRITE - the chunked 0x0b16 data_write followed by the
 * 0x0b18 COMMIT tail. Both are required: without the 0x0b18 tail the watch acknowledges the 0x0b16
 * chunks but DISCARDS them (they read back correct in-session but revert after a reconnect - the
 * exact bug this fixed, 2026-08-27). Reverse-engineered from the real SuuntoLink<->Ambit2 capture
 * assets/pcap/ambit2_suuntolink_settings_sportmodes.pcap: 1024-byte 0x0b16 chunks to 0x2000, then
 * 0x0b18 [u32 addr][u32 tailExtra] (no hash). `tailExtra` is region-specific and constant per
 * region (0xffffffff for the sport-mode region - proven content-independent across 160 real writes
 * in the pcap; the region CRC for nav), so the JS caller supplies it. The caller passes the FULL
 * region bytes (read with nativeAmbitReadLegacyRegion, patch in JS, write back) - a read-modify-
 * write only changes what JS asked to. Guarded to the Bluebird family. Returns JNI_TRUE on success.
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitWriteLegacyRegion(
        JNIEnv *env, jobject /*thiz*/, jlong address, jbyteArray data, jlong tailExtra)
{
    if (!g_device) { LOGE("nativeAmbitWriteLegacyRegion: Not connected"); return JNI_FALSE; }
    uint16_t pid = g_device->device_info.product_id;
    if (pid != 0x0010 && pid != 0x0019 && pid != 0x001A && pid != 0x001D) {
        LOGE("nativeAmbitWriteLegacyRegion: not an Ambit1/2 (pid 0x%04x)", pid);
        return JNI_FALSE;
    }
    if (!data) { LOGE("nativeAmbitWriteLegacyRegion: null data"); return JNI_FALSE; }
    jsize len = env->GetArrayLength(data);
    if (len <= 0) { LOGE("nativeAmbitWriteLegacyRegion: empty data"); return JNI_FALSE; }
    jbyte *bytes = env->GetByteArrayElements(data, nullptr);

    // A local pmem20 over the connected device, 0x400 (1024 B) chunking - the Bluebird driver's own
    // driver_param (device_support.c) and what SuuntoLink uses in the capture. Self-contained so we
    // don't reach into the private driver_data struct.
    libambit_pmem20_t pm;
    memset(&pm, 0, sizeof(pm));
    int ir = libambit_pmem20_init(&pm, g_device, 0x400);
    int rc = -1;
    if (ir == 0) {
        rc = libambit_pmem20_data_write(&pm, (uint32_t)address, (const uint8_t *)bytes, (size_t)len);
        libambit_pmem20_deinit(&pm);
    }
    env->ReleaseByteArrayElements(data, bytes, JNI_ABORT); // read-only, don't copy back
    if (ir != 0 || rc != 0) {
        LOGE("nativeAmbitWriteLegacyRegion: init %d / data_write %d at 0x%06llx", ir, rc, (long long)address);
        return JNI_FALSE;
    }

    // 0x0b18 COMMIT tail - [u32 addr][u32 tailExtra], no hash (the sport-mode / nav variant).
    uint32_t addr = (uint32_t)address, ex = (uint32_t)tailExtra;
    uint8_t tail[8] = {
        (uint8_t)(addr & 0xff), (uint8_t)((addr >> 8) & 0xff), (uint8_t)((addr >> 16) & 0xff), (uint8_t)((addr >> 24) & 0xff),
        (uint8_t)(ex & 0xff),   (uint8_t)((ex >> 8) & 0xff),   (uint8_t)((ex >> 16) & 0xff),   (uint8_t)((ex >> 24) & 0xff),
    };
    uint8_t *treply = nullptr; size_t tlen = 0;
    int trc = libambit_protocol_command(g_device, 0x0b18, tail, sizeof(tail), &treply, &tlen, 0);
    if (treply) free(treply);
    if (trc != 0) {
        LOGE("nativeAmbitWriteLegacyRegion: 0x0b18 commit tail rc %d at 0x%06llx", trc, (long long)address);
        return JNI_FALSE;
    }
    LOGI("nativeAmbitWriteLegacyRegion: wrote+committed %d bytes at 0x%06llx (tail 0x%08x)", (int)len, (long long)address, ex);
    return JNI_TRUE;
}

// JSON-escape a fixed-length C string field (watch names are latin1; emit >=0x80 as \u00xx so
// the payload stays valid UTF-8, the same discipline ambit_legacy_cli.c's json_str uses).
static void jni_json_escape(std::ostringstream &o, const char *s, size_t maxlen) {
    for (size_t i = 0; i < maxlen && s[i]; i++) {
        unsigned char c = (unsigned char)s[i];
        if (c == '"' || c == '\\') { o << '\\' << (char)c; }
        else if (c >= 0x20 && c < 0x7f) { o << (char)c; }
        else { char buf[8]; snprintf(buf, sizeof buf, "\\u%04x", c); o << buf; }
    }
}

/**
 * nativeAmbitReadLegacyNav
 *
 * Ambit 1/2 (Bluebird) waypoints + routes. The legacy family answers 0x0b02/0x0b03, which
 * libambit_navigation_read() drives (its driver has no navigation_read method, but the
 * top-level function works - same call the desktop tools/vendor/ambit_legacy_cli `settings`
 * makes). Android's SBEM POI read (0x0b24) is empty on this family, so PoiService/RouteReader
 * route here instead for parity with desktop. lat/lon are emitted as raw int32 (degrees*1e7);
 * TS divides. Null on failure.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadLegacyNav(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitReadLegacyNav: Not connected"); return nullptr; }
    ambit_personal_settings_t *ps = libambit_personal_settings_alloc();
    if (!ps) return nullptr;
    libambit_personal_settings_get(g_device, ps);       // populate base struct (match CLI order)
    int nav_rc = libambit_navigation_read(g_device, ps); // fills ps->waypoints and ps->routes
    std::ostringstream json;
    json << "{\"ok\":" << (nav_rc == 0 ? "true" : "false")
         << ",\"nav_rc\":" << nav_rc << ",\"waypoints\":[";
    for (uint16_t i = 0; i < ps->waypoints.count; i++) {
        ambit_waypoint_t *w = &ps->waypoints.data[i];
        if (i) json << ",";
        json << "{\"name\":\"";
        jni_json_escape(json, w->name, sizeof(w->name));
        json << "\",\"routeName\":\"";
        jni_json_escape(json, w->route_name, sizeof(w->route_name));
        json << "\",\"index\":" << (int)w->index
             << ",\"lat_e7\":" << (long)w->latitude
             << ",\"lon_e7\":" << (long)w->longitude
             << ",\"type\":" << (int)w->type << "}";
    }
    json << "],\"routes\":[";
    for (uint8_t i = 0; i < ps->routes.count; i++) {
        ambit_route_t *r = &ps->routes.data[i];
        if (i) json << ",";
        json << "{\"name\":\"";
        jni_json_escape(json, r->name, sizeof(r->name));
        json << "\",\"points_count\":" << (int)r->points_count
             << ",\"activity_id\":" << (int)r->activity_id
             << ",\"distance\":" << (long)r->distance << "}";
    }
    json << "]}";
    libambit_personal_settings_free(ps);
    return env->NewStringUTF(json.str().c_str());
}

/**
 * nativeAmbitReadLegacyRegion
 *
 * Ambit 1/2 (Bluebird) raw flash read. nativeAmbitReadRegion uses ambit3_read_flash_region,
 * which SIGSEGVs on this family - the Ambit3 flash protocol doesn't apply. This mirrors the
 * desktop tools/vendor/ambit_legacy_cli cmd_region_dump exactly: 0x0b17 in 512-byte chunks
 * ([u32 addr][u32 len] request; reply echoes 8 bytes then the data), stopping GRACEFULLY at
 * the region end (a short/failed chunk = end of region, returns the partial - not an error,
 * same as the desktop). Returns base64 of what was read (empty string if nothing). Used by
 * AmbitLegacySportModes.ts for region 0x2000.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadLegacyRegion(
        JNIEnv *env, jobject /* thiz */, jlong address, jlong length)
{
    if (!g_device) { LOGE("nativeAmbitReadLegacyRegion: Not connected"); return nullptr; }
    if (length <= 0 || length > 2 * 1024 * 1024) {
        LOGE("nativeAmbitReadLegacyRegion: implausible length %lld", (long long)length);
        return nullptr;
    }
    const uint32_t CHUNK = 512;
    uint32_t total = (uint32_t)length, base = (uint32_t)address, got = 0;
    std::vector<uint8_t> out;
    out.reserve(total);
    while (got < total) {
        uint32_t want = total - got;
        if (want > CHUNK) want = CHUNK;
        uint8_t send[8];
        uint32_t addr = base + got;
        send[0] = addr & 0xff;        send[1] = (addr >> 8) & 0xff;
        send[2] = (addr >> 16) & 0xff; send[3] = (addr >> 24) & 0xff;
        send[4] = want & 0xff;        send[5] = (want >> 8) & 0xff;
        send[6] = (want >> 16) & 0xff; send[7] = (want >> 24) & 0xff;
        uint8_t *reply = nullptr;
        size_t replylen = 0;
        if (libambit_protocol_command(g_device, 0x0b17, send, sizeof(send), &reply, &replylen, 0) != 0
            || replylen < (size_t)want + 8) {
            if (reply) free(reply);
            break;  // region ended (partial) - graceful, matches desktop cmd_region_dump
        }
        out.insert(out.end(), reply + 8, reply + 8 + want);
        free(reply);
        got += want;
    }
    LOGI("nativeAmbitReadLegacyRegion: 0x%06x, got %zu of %u bytes", base, out.size(), total);
    std::string b64 = out.empty() ? std::string() : base64Encode(out.data(), out.size());
    return env->NewStringUTF(b64.c_str());
}

/**
 * nativeAmbitWriteSettingsRaw
 *
 * Real, hardware-confirmed 2026-08-08 (see ambit3_write_settings_raw()'s own comment in
 * device_driver_ambit3.c): writes a full sml.DeviceSettings blob back via 0x1101. `data` is
 * a plain jbyteArray (Kotlin decodes the base64 string it got from TS before calling this -
 * no base64 decoding needed on the native side, unlike the read direction). Returns true on
 * a clean 0x1101 send; this does NOT by itself confirm the write took effect - the caller
 * (AmbitSettingsWriter.ts) re-reads via nativeAmbitReadSettingsRaw() afterward and compares,
 * the same "prove it, don't just trust the ACK" rule this project's own live testing found
 * necessary (custom_modes_andre.md).
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitWriteSettingsRaw(
        JNIEnv *env, jobject /* thiz */, jbyteArray data)
{
    if (!g_device) { LOGE("nativeAmbitWriteSettingsRaw: Not connected"); return JNI_FALSE; }
    if (!data) { LOGE("nativeAmbitWriteSettingsRaw: null data"); return JNI_FALSE; }

    jsize len = env->GetArrayLength(data);
    std::vector<uint8_t> buffer((size_t)len);
    env->GetByteArrayRegion(data, 0, len, reinterpret_cast<jbyte *>(buffer.data()));

    uint8_t *reply = nullptr;
    size_t replylen = 0;
    int ret = ambit3_write_settings_raw(g_device, buffer.data(), buffer.size(), &reply, &replylen);
    free(reply);  // expected empty on success (confirmed live) - not returned to Kotlin
    if (ret != 0) {
        LOGE("ambit3_write_settings_raw failed: %d", ret);
        return JNI_FALSE;
    }
    return JNI_TRUE;
}

/**
 * nativeAmbitSetDateTime
 *
 * Real, 2026-08-10 ("I connected the kailash via usb... it didn't sync time... is this
 * function implemented in our app?"). Writes the phone's own current local time to the
 * watch. Dispatch by watch/transport (Kailash BLE vs. everything else) happens inside
 * libambit itself (device_driver_ambit3.c's date_time_set()) - this JNI layer only builds
 * a real localtime() struct tm (tm_gmtoff/tm_zone populated, required for the Kailash path's
 * own %z rendering) and calls the transport-agnostic libambit_date_time_set().
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitSetDateTime(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitSetDateTime: Not connected"); return JNI_FALSE; }

    time_t now = time(nullptr);
    struct tm tmv;
    localtime_r(&now, &tmv);

    int ret = libambit_date_time_set(g_device, &tmv);
    if (ret != 0) {
        LOGE("libambit_date_time_set failed: %d", ret);
        return JNI_FALSE;
    }
    return JNI_TRUE;
}

/**
 * nativeAmbitReadCustomModesRaw
 *
 * Real, 2026-08-08 ("This weekend we will full debug the two watches and built the apps").
 * Returns the watch's raw 12288-byte CustomModes region (sport modes), base64-encoded, via
 * the existing generic ambit3_read_flash_region() path (see
 * ambit3_read_custom_modes_raw()'s own comment in device_driver_ambit3.c). Decoding
 * (the BXml tag tree - exercise modes, displays, sport-mode slots) happens in TS, mirroring
 * the companion research project's own tools/custom_modes.py. Null on failure.
 */
JNIEXPORT jstring JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitReadCustomModesRaw(
        JNIEnv *env, jobject /* thiz */)
{
    if (!g_device) { LOGE("nativeAmbitReadCustomModesRaw: Not connected"); return nullptr; }

    std::vector<uint8_t> buffer(12288);
    int ret = ambit3_read_custom_modes_raw(g_device, buffer.data());
    if (ret != 0) {
        LOGE("ambit3_read_custom_modes_raw failed: %d", ret);
        return nullptr;
    }
    std::string b64 = base64Encode(buffer.data(), buffer.size());
    return env->NewStringUTF(b64.c_str());
}

/**
 * nativeAmbitWriteCustomModesRaw
 *
 * Real mechanism, NOT yet hardware-confirmed on Android specifically - see
 * ambit3_write_custom_modes_raw()'s own detailed comment in device_driver_ambit3.c for
 * exactly what is and isn't proven here (the desktop side of this same mechanism is fully
 * confirmed working; this native port reuses only already-proven building blocks but has
 * not itself been tested against real hardware). `data` must be the *entire* 12288-byte
 * CustomModes region (read first via nativeAmbitReadCustomModesRaw(), patch only the
 * specific bytes to change, send the whole thing back) - matching the discipline every one
 * of this session's Python write tools already follows. Returns true only if the full
 * write+tail+commit sequence completed without a protocol-level failure - this does NOT by
 * itself confirm the watch's live state actually reflects the write; the caller should
 * re-read via nativeAmbitReadCustomModesRaw() and compare, the same "prove it" rule already
 * established for settings writes.
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitWriteCustomModesRaw(
        JNIEnv *env, jobject /* thiz */, jbyteArray data)
{
    if (!g_device) { LOGE("nativeAmbitWriteCustomModesRaw: Not connected"); return JNI_FALSE; }
    if (!data) { LOGE("nativeAmbitWriteCustomModesRaw: null data"); return JNI_FALSE; }

    jsize len = env->GetArrayLength(data);
    if (len != 12288) {
        LOGE("nativeAmbitWriteCustomModesRaw: expected 12288 bytes, got %d", (int)len);
        return JNI_FALSE;
    }
    std::vector<uint8_t> buffer((size_t)len);
    env->GetByteArrayRegion(data, 0, len, reinterpret_cast<jbyte *>(buffer.data()));

    int ret = ambit3_write_custom_modes_raw(g_device, buffer.data(), buffer.size());
    if (ret != 0) {
        LOGE("ambit3_write_custom_modes_raw failed: %d", ret);
        return JNI_FALSE;
    }
    return JNI_TRUE;
}

/**
 * nativeAmbitWriteRegion  (EXPERIMENTAL - App-Zone / Training-program, 2026-08-14)
 *
 * Generic region write: writes the first `extent` bytes of `data` at flash `address`, then
 * finalizes with the SAME used-extent SHA256 data-tail as the CustomModes writer (no commit).
 * `data` is the full region image the caller built; `extent` is how many of its leading bytes
 * are the real used content (the rest, if any, is 0xFF padding the watch never hashes). The
 * per-region format correctness lives in TS (proven byte-exact against captures). Returns
 * false on any protocol-level failure; the caller re-reads to confirm the live state.
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitWriteRegion(
        JNIEnv *env, jobject /* thiz */, jlong address, jbyteArray data, jint extent)
{
    if (!g_device) { LOGE("nativeAmbitWriteRegion: Not connected"); return JNI_FALSE; }
    if (!data) { LOGE("nativeAmbitWriteRegion: null data"); return JNI_FALSE; }

    jsize len = env->GetArrayLength(data);
    if (extent < 4 || extent > len) {
        LOGE("nativeAmbitWriteRegion: bad extent %d for %d-byte image", (int)extent, (int)len);
        return JNI_FALSE;
    }
    if (address < 0 || address > 0x00FFFFFF) {
        LOGE("nativeAmbitWriteRegion: implausible address 0x%llx", (long long)address);
        return JNI_FALSE;
    }
    std::vector<uint8_t> buffer((size_t)len);
    env->GetByteArrayRegion(data, 0, len, reinterpret_cast<jbyte *>(buffer.data()));

    LOGI("nativeAmbitWriteRegion: 0x%06llx extent=%d (image %d)", (long long)address, (int)extent, (int)len);
    int ret = ambit3_write_region_raw(g_device, (uint32_t)address, buffer.data(), (size_t)extent);
    if (ret != 0) {
        LOGE("ambit3_write_region_raw failed: %d", ret);
        return JNI_FALSE;
    }
    return JNI_TRUE;
}

/**
 * nativeAmbitDisconnect
 */
JNIEXPORT void JNICALL
Java_com_ambitsyncmodern_usb_AmbitUsbModule_nativeAmbitDisconnect(
        JNIEnv * /* env */, jobject /* thiz */)
{
    LOGI("nativeAmbitDisconnect");
    g_log_cache.clear();
    g_log_dates.clear();
    if (g_device) {
        libambit_close(g_device);
        g_device = nullptr;
    }
}

/**
 * nativeAmbitBleInit
 *
 * Constructs g_device from an already-connected, already-discovered BLE
 * link (Kotlin's AmbitBleModule has connected, handled the Service Changed
 * indication, re-discovered services, found the custom service's write/
 * notify characteristics by UUID, and enabled notifications before calling
 * this — see AmbitBleModule.kt). Reuses the SAME g_device global as the USB
 * path: every other native export in this file (nativeAmbitWriteRoute,
 * nativeAmbitReadRegion, nativeAmbitDisconnect, etc.) already operates on
 * g_device without caring how it was constructed, so BLE gets all of that
 * for free — only connection setup and the BLE frame codec (protocol_ble.c)
 * are new code.
 *
 * @param vid Vendor ID  (0x1493 for Suunto)
 * @param pid Product ID — Ambit3/Traverse only; enforced by AmbitBleModule's
 *            scan filter before this is ever called, not re-checked here.
 */
JNIEXPORT jboolean JNICALL
Java_com_ambitsyncmodern_ble_AmbitBleModule_nativeAmbitBleInit(
        JNIEnv *env, jobject thiz, jint vid, jint pid)
{
    LOGI("nativeAmbitBleInit vid=0x%04x pid=0x%04x", vid, pid);

    if (g_device) {
        libambit_close(g_device);
        g_device = nullptr;
    }
    g_log_cache.clear();
    g_log_dates.clear();

    JavaVM *jvm = nullptr;
    env->GetJavaVM(&jvm);

    g_device = libambit_new_from_ble(jvm, thiz, env, (uint16_t)vid, (uint16_t)pid);
    if (!g_device) {
        LOGE("libambit_new_from_ble failed — VID/PID 0x%04x/0x%04x, or device info "
             "read didn't come back in a recognized shape (see protocol_ble.c)", vid, pid);
        return JNI_FALSE;
    }

    LOGI("Ambit BLE initialized successfully (driver selected)");
    return JNI_TRUE;
}

/**
 * nativeAmbitBleOnNotify
 *
 * Called by AmbitBleModule.kt's onCharacteristicChanged for every raw GATT
 * notification received on the watch's notify characteristic. Forwards the
 * bytes into protocol_ble.c's frame reassembly — NOT called from the same
 * thread that nativeAmbitWriteRoute/etc. (and therefore
 * libambit_protocol_command_ble's blocking wait) run on.
 */
JNIEXPORT void JNICALL
Java_com_ambitsyncmodern_ble_AmbitBleModule_nativeAmbitBleOnNotify(
        JNIEnv *env, jobject /* thiz */, jbyteArray chunk)
{
    jsize len = env->GetArrayLength(chunk);
    jbyte *bytes = env->GetByteArrayElements(chunk, nullptr);

    pthread_mutex_lock(&g_rx_mtx);
    if (!g_rx_ready) {
        // Handshake not armed yet (g_device may still be null) — park the bytes
        // in order rather than dropping them; jni_ble_flush_rx_stash() replays
        // them once the handshake is live. See the stash comment above.
        g_rx_stash.insert(g_rx_stash.end(),
                          (const uint8_t *)bytes, (const uint8_t *)bytes + len);
        pthread_mutex_unlock(&g_rx_mtx);
    } else {
        pthread_mutex_unlock(&g_rx_mtx);
        if (g_device) // live: g_device null here means a stray post-disconnect notify
            ambit_ble_on_notify(g_device, (const uint8_t *)bytes, (size_t)len);
    }
    env->ReleaseByteArrayElements(chunk, bytes, JNI_ABORT);
}

/**
 * nativeAmbitBleResetRx
 *
 * Called from AmbitBleModule.scanAndConnect() before any scan/connect — i.e.
 * before the watch can write — to arm the pre-init RX stash for a fresh
 * connection (also re-arms it for a reconnect without an app restart). See the
 * stash comment on jni_ble_reset_rx_stash / jni_ble_flush_rx_stash above.
 */
JNIEXPORT void JNICALL
Java_com_ambitsyncmodern_ble_AmbitBleModule_nativeAmbitBleResetRx(
        JNIEnv * /* env */, jobject /* thiz */)
{
    jni_ble_reset_rx_stash();
}

} // extern "C"

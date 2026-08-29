//
//  AmbitUsbModule.mm — iOS data-operations bridge (the ObjC++ twin of Android's
//  jni_bridge.cpp), registered as NativeModules.AmbitUsbModule to match
//  src/native/AmbitUsbModule.ts.
//
//  On iOS there is no USB — the name is kept only because the JS layer imports
//  the module by that name and every watch operation is transport-agnostic: it
//  acts on the shared connected device (ambit_ios_active_device()), which the
//  BLE handshake in AmbitBleModule produced. So the same getLogs/writeRoute/
//  readRegion/settings/sport-mode operations work over BLE with no JS changes,
//  exactly as they do over USB/JNI on Android.
//
//  The portable C++ helpers (base64 via NSData, GPX conversion, the read log
//  cache) are ported from jni_bridge.cpp; the per-operation bodies call the same
//  libambit / ambit3_* C functions. USB-only, firmware, and Android file/SAF and
//  usb-attach methods are stubbed with clear rejections (or benign empties) so
//  the module loads and the BLE sync path is fully functional.
//
#import <React/RCTBridgeModule.h>
#import <React/RCTEventEmitter.h>
#import <Foundation/Foundation.h>

#include <string>
#include <sstream>
#include <iomanip>
#include <vector>
#include <set>
#include <cmath>
#include <ctime>
#import <os/log.h>

// Trace JS->native calls to stderr (visible via devicectl --console) + unified log.
#define CORE_LOG(fmt, ...) do { \
  fprintf(stderr, "[sommet.core] " fmt "\n", ##__VA_ARGS__); \
  os_log(OS_LOG_DEFAULT, "[sommet.core] " fmt, ##__VA_ARGS__); \
} while (0)

extern "C" {
#include "libambit.h"
#include "libambit_int.h"
#include "device_driver_ambit3_navigation.h"

// The BLE-connected device (defined in libambit_ios.c).
ambit_object_t *ambit_ios_active_device(void);

// ambit3_* raw ops (defined in device_driver_ambit3.c) — same forward
// declarations jni_bridge.cpp uses.
int ambit3_write_route_to_watch(ambit_object_t *object, const ambit3_nav_route_t *routes, size_t route_count);
int ambit3_add_poi_to_watch(ambit_object_t *object, const char *name, double lat, double lon, int type);
int ambit3_read_flash_region(ambit_object_t *object, uint32_t address, uint32_t length, uint8_t *out_buffer);
int ambit3_read_poi_list_raw(ambit_object_t *object, uint8_t **out, size_t *out_len);
int ambit3_read_memory_map_raw(ambit_object_t *object, uint8_t **out, size_t *out_len);
int ambit3_read_object_by_id_raw(ambit_object_t *object, uint8_t entry_id, uint8_t **out, size_t *out_len);
int ambit3_read_settings_raw(ambit_object_t *object, uint8_t **out, size_t *out_len);
int ambit3_write_settings_raw(ambit_object_t *object, const uint8_t *data, size_t datalen, uint8_t **out, size_t *out_len);
int ambit3_read_custom_modes_raw(ambit_object_t *object, uint8_t *out_buffer);
int ambit3_write_custom_modes_raw(ambit_object_t *object, const uint8_t *data, size_t datalen);
int ambit3_write_region_raw(ambit_object_t *object, uint32_t base, const uint8_t *data, size_t extent);
}

// ─── Portable helpers (ported from jni_bridge.cpp) ────────────────────────────

static NSString *b64(const uint8_t *data, size_t len) {
    if (!data || len == 0) return @"";
    return [[NSData dataWithBytes:data length:len] base64EncodedStringWithOptions:0];
}

// Over BLE the watch reports its internal CODENAME as the device_info model
// (e.g. "Emu" for an Ambit3 Peak, "Hoopoe" for a Kailash). Map it to the
// marketing name — same table as Android's SUUNTO_PID_NAMES, keyed by codename.
static NSString *friendlyName(NSString *model) {
    static NSDictionary *map = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        map = @{
            @"Bluebird": @"Suunto Ambit",       @"Duck":     @"Suunto Ambit2",
            @"Colibri":  @"Suunto Ambit2 S",    @"Greentit": @"Suunto Ambit2 R",
            @"Emu":      @"Suunto Ambit3 Peak", @"Finch":    @"Suunto Ambit3 Sport",
            @"Ibisbill": @"Suunto Ambit3 Run",  @"Kaka":     @"Suunto Ambit3 Vertical",
            @"Hoopoe":   @"Suunto Kailash",     @"Jabiru":   @"Suunto Traverse",
            @"Loon":     @"Suunto Traverse Alpha",
        };
    });
    NSString *friendly = map[model];
    if (friendly) return friendly;
    return model.length ? model : @"Suunto Ambit";
}

// Read-log cache: filled by a getLogs() download, indexed the same as the
// returned GPX array (see jni_bridge.cpp's g_log_cache / g_log_dates).
static std::vector<std::string> g_log_cache;
static std::vector<ambit_date_time_t> g_log_dates;
static std::set<std::string> g_known_dates;

static std::string formatLogId(const ambit_log_header_t *h) {
    char buf[20];
    snprintf(buf, sizeof(buf), "%04d%02d%02dT%02d%02d%02d",
             h->date_time.year, h->date_time.month, h->date_time.day,
             h->date_time.hour, h->date_time.minute, (int)(h->date_time.msec / 1000));
    return std::string(buf);
}

// GPX conversion — a straight port of jni_bridge.cpp's convertEntryToGpx().
static std::string convertEntryToGpx(const ambit_log_entry_t *entry) {
    std::ostringstream gpx;
    char date_buf[32];
    snprintf(date_buf, sizeof(date_buf), "%04d-%02d-%02dT%02d:%02d:%02dZ",
             entry->header.date_time.year, entry->header.date_time.month,
             entry->header.date_time.day, entry->header.date_time.hour,
             entry->header.date_time.minute, (int)(entry->header.date_time.msec / 1000));
    const char *act_raw = entry->header.activity_name;
    bool act_ok = act_raw && act_raw[0] != '\0';
    uint8_t act_type = entry->header.activity_type;

    gpx << "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        << "<gpx version=\"1.1\" creator=\"Sommet\""
        << " xmlns=\"http://www.topografix.com/GPX/1/1\""
        << " xmlns:gpxtpx=\"http://www.garmin.com/xmlschemas/TrackPointExtension/v1\">\n"
        << "  <metadata><time>" << date_buf << "</time></metadata>\n"
        << "  <trk><name>" << (act_ok ? act_raw : "") << "</name>\n"
        << "    <extensions>\n"
        << "      <duration>" << entry->header.duration / 1000 << "</duration>\n"
        << "      <distance>" << entry->header.distance << "</distance>\n"
        << "      <ascent>" << entry->header.ascent << "</ascent>\n"
        << "      <sport_type>" << (int)act_type << "</sport_type>\n"
        << "    </extensions>\n"
        << "  <trkseg>\n";

    double cur_lat = 0.0, cur_lon = 0.0, cur_ele = 0.0;
    bool has_pos = false;
    uint32_t cur_time_ms = 0;
    struct tm start_tm = {};
    start_tm.tm_year = entry->header.date_time.year - 1900;
    start_tm.tm_mon  = entry->header.date_time.month - 1;
    start_tm.tm_mday = entry->header.date_time.day;
    start_tm.tm_hour = entry->header.date_time.hour;
    start_tm.tm_min  = entry->header.date_time.minute;
    start_tm.tm_sec  = (int)(entry->header.date_time.msec / 1000);
    time_t start_epoch = mktime(&start_tm);  // matches jni_bridge.cpp (parity with Android GPX times)

    for (uint32_t i = 0; i < entry->samples_count; i++) {
        const ambit_log_sample_t &s = entry->samples[i];
        cur_time_ms = s.time;
        bool emit = false;
        if (s.type == ambit_log_sample_type_gps_base) {
            cur_lat = s.u.gps_base.latitude / 1e7;
            cur_lon = s.u.gps_base.longitude / 1e7;
            cur_ele = s.u.gps_base.altitude / 100.0;
            has_pos = true; emit = true;
        } else if (s.type == ambit_log_sample_type_gps_small) {
            cur_lat = s.u.gps_small.latitude / 1e7;
            cur_lon = s.u.gps_small.longitude / 1e7;
            has_pos = true; emit = true;
        } else if (s.type == ambit_log_sample_type_gps_tiny) {
            cur_lat = s.u.gps_tiny.latitude / 1e7;
            cur_lon = s.u.gps_tiny.longitude / 1e7;
            has_pos = true; emit = true;
        } else if (s.type == ambit_log_sample_type_periodic && has_pos) {
            double lat = cur_lat, lon = cur_lon;
            bool lat_ok = false, lon_ok = false;
            for (uint8_t v = 0; v < s.u.periodic.value_count; v++) {
                const ambit_log_sample_periodic_value_t &pv = s.u.periodic.values[v];
                if (pv.type == ambit_log_sample_periodic_type_latitude)  { lat = pv.u.latitude / 1e7; lat_ok = true; }
                if (pv.type == ambit_log_sample_periodic_type_longitude) { lon = pv.u.longitude / 1e7; lon_ok = true; }
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
                << "<time>" << time_buf << "</time></trkpt>\n";
        }
    }
    gpx << "  </trkseg></trk>\n</gpx>";
    return gpx.str();
}

static void log_push_callback(void *ud, ambit_log_entry_t *entry) {
    (void)ud;
    g_log_cache.push_back(convertEntryToGpx(entry));
    g_log_dates.push_back(entry->header.date_time);
}

static int log_skip_callback(void *ud, ambit_log_header_t *header) {
    (void)ud;
    if (g_known_dates.empty()) return 1;
    return g_known_dates.count(formatLogId(header)) ? 0 : 1;
}

// ─── Module ───────────────────────────────────────────────────────────────────

@interface AmbitUsbModule : RCTEventEmitter <RCTBridgeModule>
@end

@implementation AmbitUsbModule {
    BOOL _hasListeners;
}

RCT_EXPORT_MODULE(AmbitUsbModule);

// Watch operations block (BLE replies arrive on the CB queue on a different
// thread); run them on a dedicated serial queue, never main.
- (dispatch_queue_t)methodQueue { return dispatch_queue_create("com.sommet.ambitcore", DISPATCH_QUEUE_SERIAL); }
+ (BOOL)requiresMainQueueSetup { return NO; }
- (NSArray<NSString *> *)supportedEvents { return @[@"AmbitSyncProgress", @"AmbitUsbAttached", @"AmbitFirmwarePhase"]; }
- (void)startObserving { _hasListeners = YES; }
- (void)stopObserving { _hasListeners = NO; }

// ── Device info ────────────────────────────────────────────────────────────
RCT_EXPORT_METHOD(getDeviceInfo:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    CORE_LOG("getDeviceInfo called (dev=%p)", (void*)dev);
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }

    const uint8_t *fw = dev->device_info.fw_version;
    const uint8_t *hw = dev->device_info.hw_version;
    NSString *fwStr = [NSString stringWithFormat:@"%d.%d.%d", fw[0], fw[1], fw[2] | (fw[3] << 8)];
    NSString *hwStr = [NSString stringWithFormat:@"%d.%d.%d", hw[0], hw[1], hw[2] | (hw[3] << 8)];
    NSString *model  = [NSString stringWithUTF8String:dev->device_info.model ?: ""];
    NSString *serial = [NSString stringWithUTF8String:dev->device_info.serial ?: ""];

    int battery = -1;
    ambit_device_status_t status;
    if (libambit_device_status_get(dev, &status) == 0) battery = status.charge;

    resolve(@{ @"name": friendlyName(model),
               @"model": model, @"serial": serial,
               @"fwVersion": fwStr, @"hwVersion": hwStr, @"battery": @(battery) });
}

// ── Activity log sync ──────────────────────────────────────────────────────
RCT_EXPORT_METHOD(getLogs:(NSArray *)knownIds resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    CORE_LOG("getLogs called (knownIds=%lu dev=%p)", (unsigned long)knownIds.count, (void*)dev);
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }

    g_known_dates.clear();
    for (id v in knownIds) if ([v isKindOfClass:NSString.class]) g_known_dates.insert(std::string([v UTF8String]));
    g_log_cache.clear();
    g_log_dates.clear();

    CORE_LOG("getLogs: calling libambit_log_read ...");
    int ret = libambit_log_read(dev, log_skip_callback, log_push_callback, NULL, NULL);
    CORE_LOG("getLogs: libambit_log_read returned %d, cache=%zu", ret, g_log_cache.size());
    if (ret < 0) { reject(@"LOG_READ_FAILED", @"Failed to read activity logs", nil); return; }

    NSMutableArray<NSString *> *results = [NSMutableArray arrayWithCapacity:g_log_cache.size()];
    NSUInteger total = g_log_cache.size();
    for (NSUInteger i = 0; i < total; i++) {
        [results addObject:[NSString stringWithUTF8String:g_log_cache[i].c_str()]];
        if (_hasListeners) [self sendEventWithName:@"AmbitSyncProgress" body:@{@"current": @(i + 1), @"total": @(total)}];
    }
    resolve(results);
}

RCT_EXPORT_METHOD(markReadLogsSynced:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    int marked = 0;
    for (size_t i = 0; i < g_log_dates.size(); i++) {
        ambit_log_entry_t entry; memset(&entry, 0, sizeof(entry));
        entry.header.date_time = g_log_dates[i];
        if (libambit_log_synced(dev, &entry) == 0) marked++;
    }
    resolve(@(marked));
}

RCT_EXPORT_METHOD(updateSgee:(NSString *)path resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    NSData *data = [NSData dataWithContentsOfFile:path];
    if (!data) { reject(@"SGEE_FILE_NOT_FOUND", [@"SGEE file not found: " stringByAppendingString:path], nil); return; }
    int ok = libambit_gps_orbit_write(dev, (uint8_t *)data.bytes, (size_t)data.length);
    if (ok == 0) resolve(@YES); else reject(@"SGEE_SEND_FAILED", @"Failed to send SGEE data", nil);
}

RCT_EXPORT_METHOD(setDateTime:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    time_t now = time(NULL); struct tm tmv; localtime_r(&now, &tmv);
    if (libambit_date_time_set(dev, &tmv) == 0) resolve(@YES);
    else reject(@"TIME_SYNC_FAILED", @"Failed to set watch clock", nil);
}

// ── Navigation: route + POI writes ─────────────────────────────────────────
RCT_EXPORT_METHOD(writeRoute:(NSDictionary *)route resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }

    NSArray *pts = route[@"points"];  NSArray *wpts = route[@"waypoints"];
    if (pts.count < 2) { reject(@"BAD_ROUTE", @"route has fewer than 2 points", nil); return; }
    if (wpts.count == 0) { reject(@"BAD_ROUTE", @"route has no waypoints — it would not show on the watch", nil); return; }

    std::vector<ambit3_nav_point_t> points(pts.count);
    for (NSUInteger i = 0; i < pts.count; i++) {
        NSDictionary *p = pts[i];
        points[i].latitude  = (int32_t)llround([p[@"lat"] doubleValue] * 1e7);
        points[i].longitude = (int32_t)llround([p[@"lon"] doubleValue] * 1e7);
        id alt = p[@"alt"];
        points[i].altitude  = (alt == nil || alt == NSNull.null) ? 0 : (int)[alt intValue];
    }
    std::vector<ambit3_nav_waypoint_t> waypoints(wpts.count);
    for (NSUInteger i = 0; i < wpts.count; i++) {
        NSDictionary *w = wpts[i];
        waypoints[i].latitude    = (int32_t)llround([w[@"lat"] doubleValue] * 1e7);
        waypoints[i].longitude   = (int32_t)llround([w[@"lon"] doubleValue] * 1e7);
        waypoints[i].point_index = (uint16_t)[w[@"pointIndex"] intValue];
        const char *n = [(w[@"name"] ?: @"") UTF8String];
        strncpy(waypoints[i].name, n, sizeof(waypoints[i].name) - 1);
        waypoints[i].name[sizeof(waypoints[i].name) - 1] = '\0';
    }

    // The route index timestamp uses the watch's own non-Unix epoch — same
    // offset jni_bridge.cpp applies (unix_epoch - route_epoch, precomputed).
    static const int64_t AMBIT3_ROUTE_EPOCH_OFFSET_SEC = 508055296;
    long long tsSec = [route[@"timestampSec"] longLongValue];

    ambit3_nav_route_t r = {};
    strncpy(r.name, [(route[@"name"] ?: @"") UTF8String], sizeof(r.name) - 1);
    r.points = points.data();     r.point_count = (uint16_t)pts.count;
    r.distance = (uint32_t)[route[@"distanceM"] intValue];
    r.ascent   = (uint16_t)[route[@"ascentM"] intValue];
    r.descent  = (uint16_t)[route[@"descentM"] intValue];
    r.timestamp = (uint32_t)(tsSec + AMBIT3_ROUTE_EPOCH_OFFSET_SEC);
    r.waypoints = waypoints.data(); r.waypoint_count = (uint16_t)wpts.count;
    time_t t = (time_t)tsSec; struct tm tmv; gmtime_r(&t, &tmv);
    r.month = (uint8_t)(tmv.tm_mon + 1); r.day = (uint8_t)tmv.tm_mday;
    r.hour = (uint8_t)tmv.tm_hour; r.minute = (uint8_t)tmv.tm_min; r.second = (uint8_t)tmv.tm_sec;

    if (ambit3_write_route_to_watch(dev, &r, 1) == 0) resolve(@YES);
    else reject(@"WRITE_ROUTE_FAILED", @"ambit3_write_route_to_watch failed", nil);
}

RCT_EXPORT_METHOD(addPoi:(NSString *)name lat:(double)lat lon:(double)lon type:(NSInteger)type
                  resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    if (ambit3_add_poi_to_watch(dev, [name UTF8String], lat, lon, (int)type) == 0) resolve(@YES);
    else reject(@"ADD_POI_FAILED", @"ambit3_add_poi_to_watch failed", nil);
}

// ── Raw reads (base64) ─────────────────────────────────────────────────────
RCT_EXPORT_METHOD(readRegion:(double)address length:(double)length
                  resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    if (length <= 0 || length > 2 * 1024 * 1024) { reject(@"BAD_LENGTH", @"implausible length", nil); return; }
    std::vector<uint8_t> buf((size_t)length);
    if (ambit3_read_flash_region(dev, (uint32_t)address, (uint32_t)length, buf.data()) != 0) {
        reject(@"READ_REGION_FAILED", @"ambit3_read_flash_region failed", nil); return;
    }
    resolve(b64(buf.data(), buf.size()));
}

// Shared helper for the malloc'd raw reads (POI list, memory map, history, log, settings).
- (void)rawRead:(int(^)(ambit_object_t *dev, uint8_t **out, size_t *len))fn
        resolve:(RCTPromiseResolveBlock)resolve reject:(RCTPromiseRejectBlock)reject code:(NSString *)code {
    ambit_object_t *dev = ambit_ios_active_device();
    CORE_LOG("rawRead[%s] called (dev=%p)", code.UTF8String, (void*)dev);
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    uint8_t *raw = NULL; size_t rawlen = 0;
    int rc = fn(dev, &raw, &rawlen);
    CORE_LOG("rawRead[%s] rc=%d len=%zu", code.UTF8String, rc, rawlen);
    if (rc != 0) { reject(code, @"raw read failed", nil); return; }
    NSString *out = (raw && rawlen > 0) ? b64(raw, rawlen) : @"";
    free(raw);
    resolve(out);
}

RCT_EXPORT_METHOD(readPoiListRaw:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    [self rawRead:^int(ambit_object_t *d, uint8_t **o, size_t *l){ return ambit3_read_poi_list_raw(d, o, l); }
          resolve:resolve reject:reject code:@"POI_READ_FAILED"];
}
RCT_EXPORT_METHOD(readMemoryMapRaw:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    [self rawRead:^int(ambit_object_t *d, uint8_t **o, size_t *l){ return ambit3_read_memory_map_raw(d, o, l); }
          resolve:resolve reject:reject code:@"MEMMAP_READ_FAILED"];
}
RCT_EXPORT_METHOD(readDeviceHistoryRaw:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    [self rawRead:^int(ambit_object_t *d, uint8_t **o, size_t *l){ return ambit3_read_object_by_id_raw(d, 0x67, o, l); }
          resolve:resolve reject:reject code:@"HISTORY_READ_FAILED"];
}
RCT_EXPORT_METHOD(readDeviceLogRaw:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    [self rawRead:^int(ambit_object_t *d, uint8_t **o, size_t *l){ return ambit3_read_object_by_id_raw(d, 0x53, o, l); }
          resolve:resolve reject:reject code:@"DEVLOG_READ_FAILED"];
}
RCT_EXPORT_METHOD(readSettingsRaw:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    [self rawRead:^int(ambit_object_t *d, uint8_t **o, size_t *l){ return ambit3_read_settings_raw(d, o, l); }
          resolve:resolve reject:reject code:@"SETTINGS_READ_FAILED"];
}

RCT_EXPORT_METHOD(readCustomModesRaw:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    std::vector<uint8_t> buf(12288);
    if (ambit3_read_custom_modes_raw(dev, buf.data()) != 0) { reject(@"CUSTOMMODES_READ_FAILED", @"read failed", nil); return; }
    resolve(b64(buf.data(), buf.size()));
}

// ── Raw writes (base64 in) ─────────────────────────────────────────────────
RCT_EXPORT_METHOD(writeSettingsRaw:(NSString *)dataBase64 resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    NSData *data = [[NSData alloc] initWithBase64EncodedString:dataBase64 options:0];
    if (!data) { reject(@"BAD_BASE64", @"invalid base64", nil); return; }
    uint8_t *reply = NULL; size_t replylen = 0;
    int ret = ambit3_write_settings_raw(dev, (const uint8_t *)data.bytes, (size_t)data.length, &reply, &replylen);
    free(reply);
    if (ret == 0) resolve(@YES); else reject(@"SETTINGS_WRITE_FAILED", @"write failed", nil);
}

RCT_EXPORT_METHOD(writeCustomModesRaw:(NSString *)dataBase64 resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    NSData *data = [[NSData alloc] initWithBase64EncodedString:dataBase64 options:0];
    if (!data || data.length != 12288) { reject(@"BAD_CUSTOMMODES", @"expected 12288 bytes", nil); return; }
    if (ambit3_write_custom_modes_raw(dev, (const uint8_t *)data.bytes, (size_t)data.length) == 0) resolve(@YES);
    else reject(@"CUSTOMMODES_WRITE_FAILED", @"write failed", nil);
}

RCT_EXPORT_METHOD(writeRegion:(double)address data:(NSString *)dataBase64 extent:(NSInteger)extent
                  resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"NOT_CONNECTED", @"Watch not connected", nil); return; }
    NSData *data = [[NSData alloc] initWithBase64EncodedString:dataBase64 options:0];
    if (!data) { reject(@"BAD_BASE64", @"invalid base64", nil); return; }
    if (extent < 4 || (NSUInteger)extent > data.length) { reject(@"BAD_EXTENT", @"bad extent", nil); return; }
    if (address < 0 || address > 0x00FFFFFF) { reject(@"BAD_ADDRESS", @"implausible address", nil); return; }
    if (ambit3_write_region_raw(dev, (uint32_t)address, (const uint8_t *)data.bytes, (size_t)extent) == 0) resolve(@YES);
    else reject(@"WRITE_REGION_FAILED", @"write failed", nil);
}

// ── connect / disconnect ───────────────────────────────────────────────────
// iOS has no USB; BLE owns connect/disconnect (AmbitBleModule). These exist so
// the JS module surface is complete. connect() returns whatever the BLE
// handshake already learned; disconnect() is a no-op (HomeScreen owns the link).
RCT_EXPORT_METHOD(connect:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) {
    ambit_object_t *dev = ambit_ios_active_device();
    if (!dev) { reject(@"USB_UNSUPPORTED_IOS", @"No BLE watch connected; USB is not available on iOS", nil); return; }
    NSString *model = [NSString stringWithUTF8String:dev->device_info.model ?: ""];
    resolve(@{ @"name": friendlyName(model), @"vendorId": @(0x1493), @"productId": @(0) });
}
RCT_EXPORT_METHOD(disconnect:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { resolve([NSNull null]); }

// ── Stubs: USB-attach / multi-device (no USB on iOS) ───────────────────────
RCT_EXPORT_METHOD(wasLaunchedViaUsbAttach:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { resolve(@NO); }
RCT_EXPORT_METHOD(detectAttachedDeviceType:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { resolve(@"none"); }
RCT_EXPORT_METHOD(listDevices:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { resolve(@[]); }
RCT_EXPORT_METHOD(selectDevice:(NSString *)deviceName resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { resolve(@YES); }

// ── Stubs: Ambit1/2 USB-only legacy paths (unreachable from iOS) ───────────
RCT_EXPORT_METHOD(readPersonalSettings:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"USB_ONLY", @"Ambit 1/2 personal settings are USB-only (not available on iOS)", nil); }
RCT_EXPORT_METHOD(writePersonalSetting:(NSInteger)offset width:(NSInteger)width value:(NSInteger)value resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"USB_ONLY", @"Ambit 1/2 personal settings are USB-only (not available on iOS)", nil); }
RCT_EXPORT_METHOD(readLegacyNav:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"USB_ONLY", @"Ambit 1/2 legacy nav is USB-only (not available on iOS)", nil); }
RCT_EXPORT_METHOD(readLegacyRegion:(double)address length:(double)length resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"USB_ONLY", @"Ambit 1/2 legacy region read is USB-only (not available on iOS)", nil); }

// ── Stubs: firmware flashing (USB path; not ported to iOS) ─────────────────
RCT_EXPORT_METHOD(firmwarePreflight:(NSString *)path resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"FIRMWARE_IOS_UNSUPPORTED", @"Firmware flashing is not available on iOS yet", nil); }
RCT_EXPORT_METHOD(firmwareFlash:(NSString *)path commit:(BOOL)commit confirm:(BOOL)confirm resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"FIRMWARE_IOS_UNSUPPORTED", @"Firmware flashing is not available on iOS yet", nil); }

// ── Stubs: Android file/SAF ops (need iOS UIDocument equivalents — TODO) ────
RCT_EXPORT_METHOD(pickGpxFile:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"NOT_IMPLEMENTED_IOS", @"pickGpxFile needs an iOS document-picker implementation", nil); }
RCT_EXPORT_METHOD(shareFile:(NSString *)filePath mimeType:(NSString *)mimeType resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"NOT_IMPLEMENTED_IOS", @"shareFile needs an iOS share-sheet implementation", nil); }
RCT_EXPORT_METHOD(saveToDownloads:(NSString *)filePath fileName:(NSString *)fileName mimeType:(NSString *)mimeType resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"NOT_IMPLEMENTED_IOS", @"saveToDownloads needs an iOS implementation", nil); }
RCT_EXPORT_METHOD(saveFileAs:(NSString *)sourcePath suggestedName:(NSString *)suggestedName mimeType:(NSString *)mimeType resolver:(RCTPromiseResolveBlock)resolve rejecter:(RCTPromiseRejectBlock)reject) { reject(@"NOT_IMPLEMENTED_IOS", @"saveFileAs needs an iOS document-picker implementation", nil); }

@end

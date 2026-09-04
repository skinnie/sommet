/* Read-only bridge to the legacy Ambit1/2 "Bluebird" PMEM 2.0 protocol, for watches that
 * predate SBEM (write_nav.py's own protocol - see that file's 2026-08-22 PRODUCT_IDS comment
 * for how device-info/battery were confirmed common to the whole family, but settings/POIs/
 * memory-map/logs are NOT). Wraps ../openambit_libambit (vendored, GPLv3, see its README for
 * why) - the only implementation of this protocol available in this project. Deliberately a
 * separate, standalone binary invoked via subprocess (see desktop/backend/server.py's
 * run_tool()), the same way every other tools py CLI is - so GPLv3 stays confined to this
 * one helper process instead of reaching the app binary.
 *
 * Started read-only; two real writes added same day (2026-08-22) once André pushed back on
 * staying over-cautious given how much real write-and-verify testing this project had
 * already done on other watches that same session: `gps-orbit-write` (proven working live)
 * and `poi-add`/`poi-clear` (openambit's own `libambit_waypoint_append` +
 * `libambit_navigation_write`, real functions, exercised here for the first time in this
 * project - test with a throwaway name first, same discipline as every other real write in
 * this project: add, read back, verify, revert).
 * Ambit 1/2 personal-SETTINGS write (weight/HR/etc, not waypoints) is real too (SuuntoLink
 * does it) but its wire format has never been captured in this project - see the
 * ambit-app-ambit12-settings-write memory.
 *
 * `sport-mode-write-presets` added 2026-08-23 (André: "do like them", after confirming
 * openambit2's own "Sport mode editor: full read/write to watch" claim is marketing - its
 * "read" is a local JSON file/hardcoded factoryDefaults(), never the device; this family's
 * driver (ours AND theirs - checked openambit2's device_driver_ambit.c) only has
 * sport_mode_write in its function table, no read at all, so there's genuinely nothing to
 * preserve before writing - any write here is a blind, one-way REPLACE of whatever sport
 * modes are on the watch. Ships the exact same 19 factory-default presets as openambit2's
 * SportModeStorage::factoryDefaults() (Running/Trail Running/Cycling/.../Other), transcribed
 * field-for-field from their CustomMode::toAmbitSettings()/hrbeltAndPods() into
 * build_preset_sport_modes() below - same values, same struct layout (this project's vendored
 * libambit and theirs share the same lineage). Each preset carries zero configured Displays,
 * matching their own factoryDefaults() exactly (an empty QVariantList there too) - the watch
 * falls back to its own default display layout for a mode with no custom one, same as any
 * fresh Movescount-authored mode before a user customizes its screens. `--dry-run` builds the
 * full payload and reports its shape without touching the watch.
 *
 * SUPERSEDED for the Ambit1 (2026-08-23, same day): the "no raw read exists" premise above
 * turned out to be FALSE. pmem20.c's read_log_chunk() is a generic flash read (0x0b17 at any
 * address), so the region CAN be read - see `sport-mode-dump` below and, better,
 * ambit1_sport_mode.c, which reads the real modes and does READ-MODIFY-WRITE instead of a
 * blind replace. Prefer `ambit1-sport-mode-read` / `ambit1-sport-mode-patch` on an Ambit1;
 * these preset commands write the 90-byte libambit layout, which is WRONG for that device.
 *
 * One JSON object printed to stdout per invocation - same "--json" convention as every
 * other tools py CLI that backend/server.py's run_tool() already parses.
 *
 *   ambit_legacy_cli device-info
 *   ambit_legacy_cli settings
 *   ambit_legacy_cli logs OUTDIR              # writes OUTDIR/<n>.gpx + prints an index
 *   ambit_legacy_cli gps-orbit-status
 *   ambit_legacy_cli gps-orbit-write FILE
 *   ambit_legacy_cli poi-add NAME LAT LON     # preserves existing waypoints
 *   ambit_legacy_cli poi-clear                # writes back 0 waypoints
 *   ambit_legacy_cli sport-mode-write-presets [--dry-run]   # blind REPLACE, no readback
 *   ambit_legacy_cli sport-mode-write FILE [--dry-run]      # same, from the host master copy
 *   ambit_legacy_cli sport-mode-dump FILE [BYTES]           # raw 0x2000 region read
 *   ambit_legacy_cli ambit1-sport-mode-read                 # Ambit1 ONLY: decode real modes
 *   ambit_legacy_cli ambit1-sport-mode-patch FILE [--dry-run] [--dump OUT]   # Ambit1 RMW
 *
 * Build: see build.sh in this directory (links against ../openambit_libambit's libambit).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <math.h>
#include "libambit.h"
#include "protocol.h"   /* libambit_protocol_command - raw 0x0b17 flash read, see cmd_sport_mode_dump */
#include "ambit1_sport_mode.h"  /* Ambit1-only 76-byte layout, hard-guarded to pid 0x0010 */

/* Emits a JSON string that is always pure ASCII.
 *
 * Every byte >= 0x80 is escaped, not passed through. This family stores text in ISO-8859
 * (single-byte - see docs/ambit1_sport_mode_format.md), so a name like "Corrida de Acção" or
 * a waypoint with an umlaut would otherwise put raw 0xe7/0xe4 bytes into the output. That is
 * not valid UTF-8, and the Python caller reads this pipe with text=True: it died with
 * `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe4`, which is what made the Watch
 * settings page hang on "Reading settings off the watch...". ISO-8859-1 maps 1:1 onto
 * U+0080..U+00FF, so \uXXXX of the byte value is the correct character. */
static void json_str(FILE *f, const char *s) {
    fputc('"', f);
    for (const unsigned char *p = (const unsigned char *)s; s && *p; p++) {
        if (*p == '"' || *p == '\\') fputc('\\', f);
        if (*p < 0x20 || *p >= 0x80) { fprintf(f, "\\u%04x", *p); continue; }
        fputc(*p, f);
    }
    fputc('"', f);
}

/* GPX 1.1 track from one log entry's GPS samples - lat/lon in degrees (already scaled by
 * the caller), no elevation smoothing/simplification, matches build_route.py's own "write
 * the real points, don't be clever" convention. */
static int write_gpx(const char *path, ambit_log_entry_t *entry, const char *name) {
    FILE *f = fopen(path, "w");
    if (!f) return -1;
    fprintf(f, "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n");
    fprintf(f, "<gpx version=\"1.1\" creator=\"ambit-app legacy_cli\" "
               "xmlns=\"http://www.topografix.com/GPX/1/1\">\n");
    fprintf(f, "  <trk><name>%s</name><trkseg>\n", name);
    int points = 0;
    for (uint32_t i = 0; i < entry->samples_count; i++) {
        ambit_log_sample_t *s = &entry->samples[i];
        double lat = 0, lon = 0;
        int has = 0;
        if (s->type == ambit_log_sample_type_gps_base) {
            lat = s->u.gps_base.latitude / 10000000.0;
            lon = s->u.gps_base.longitude / 10000000.0;
            has = 1;
        } else if (s->type == ambit_log_sample_type_gps_small) {
            lat = s->u.gps_small.latitude / 10000000.0;
            lon = s->u.gps_small.longitude / 10000000.0;
            has = 1;
        } else if (s->type == ambit_log_sample_type_gps_tiny) {
            lat = s->u.gps_tiny.latitude / 10000000.0;
            lon = s->u.gps_tiny.longitude / 10000000.0;
            has = 1;
        } else if (s->type == ambit_log_sample_type_position) {
            lat = s->u.position.latitude / 10000000.0;
            lon = s->u.position.longitude / 10000000.0;
            has = 1;
        }
        if (!has || (lat == 0 && lon == 0)) continue;
        fprintf(f, "    <trkpt lat=\"%.7f\" lon=\"%.7f\">"
                   "<time>%04d-%02d-%02dT%02d:%02d:%02dZ</time></trkpt>\n",
                lat, lon, s->utc_time.year, s->utc_time.month, s->utc_time.day,
                s->utc_time.hour, s->utc_time.minute, s->utc_time.msec / 1000);
        points++;
    }
    fprintf(f, "  </trkseg></trk>\n</gpx>\n");
    fclose(f);
    return points;
}

typedef struct { FILE *idx; char *outdir; int index; int first; } log_ctx_t;

static void log_progress_cb(void *userref, uint16_t count, uint16_t current, uint8_t pct) {
    (void)userref;
    fprintf(stderr, "log %u/%u (%u%%)\n", current, count, pct);
}

static void log_push_cb(void *userref, ambit_log_entry_t *entry) {
    log_ctx_t *ctx = (log_ctx_t *)userref;
    ambit_log_header_t *h = &entry->header;
    char name[64];
    snprintf(name, sizeof(name), "%04d-%02d-%02dT%02d-%02d",
             h->date_time.year, h->date_time.month, h->date_time.day,
             h->date_time.hour, h->date_time.minute);
    char path[1200];
    snprintf(path, sizeof(path), "%s/%d_%s.gpx", ctx->outdir, ctx->index, name);
    int points = write_gpx(path, entry, name);

    if (!ctx->first) fprintf(ctx->idx, ",\n");
    ctx->first = 0;
    fprintf(ctx->idx, "    {\"index\": %d, \"date_time\": \"%04d-%02d-%02dT%02d:%02d\", "
            "\"duration_ms\": %u, \"distance_m\": %u, \"ascent_m\": %u, \"descent_m\": %u, "
            "\"heartrate_avg_bpm\": %u, \"heartrate_max_bpm\": %u, \"activity_type\": %u, "
            "\"activity_name\": ", ctx->index, h->date_time.year, h->date_time.month,
            h->date_time.day, h->date_time.hour, h->date_time.minute, h->duration,
            h->distance, h->ascent, h->descent, h->heartrate_avg, h->heartrate_max,
            h->activity_type);
    json_str(ctx->idx, h->activity_name ? h->activity_name : "");
    fprintf(ctx->idx, ", \"energy_consumption_kcal\": %u, \"gpx_points\": %d, \"gpx_file\": ",
            h->energy_consumption, points);
    json_str(ctx->idx, path);
    fprintf(ctx->idx, "}");
    ctx->index++;
}

/* -1 = no --device given: fall back to "first legacy-family device seen" (this CLI's whole
 * purpose is the legacy PMEM 2.0 family, so that's the sane default with one watch plugged
 * in). >=0 = an explicit product_id from --device, required whenever more than one Suunto
 * device might be on the bus - see this function's own comment for why. */
static int g_selected_pid = -1;

static const int LEGACY_PIDS[] = {0x0010, 0x0019, 0x001a, 0x001d};

static int is_legacy_pid(int pid) {
    for (size_t i = 0; i < sizeof(LEGACY_PIDS) / sizeof(LEGACY_PIDS[0]); i++)
        if (LEGACY_PIDS[i] == pid) return 1;
    return 0;
}

/* Real bug, caught 2026-08-23 with an Ambit1 AND an Ambit3 Sport connected at once: this used
 * to be open_first_device(), which unconditionally opened libambit_enumerate()'s list HEAD -
 * whichever Suunto device hid_enumerate() happened to list first, ignoring which watch the
 * app had selected. That device_support.c table recognizes every Ambit model, not just the
 * legacy family, so the "first" device could easily be an Ambit3 - which was happening live:
 * settings for the selected Ambit1 were silently coming back from the Ambit3 Sport instead
 * (its driver has no navigation_read, which is exactly the "Driver does not support
 * navigation_waypoint_read" warning that gave it away). Fixed: honor --device when given
 * (the app always passes the selected watch's product_id now), else fall back to the first
 * legacy-family device in the list - never silently pick an Ambit3+. */
static ambit_object_t *open_selected_device(ambit_device_info_t **out_devices,
                                             ambit_device_info_t **out_info) {
    ambit_device_info_t *devices = libambit_enumerate();
    if (!devices) return NULL;

    ambit_device_info_t *target = NULL;
    for (ambit_device_info_t *d = devices; d; d = d->next) {
        if (g_selected_pid >= 0) {
            if (d->product_id == g_selected_pid) { target = d; break; }
        } else if (is_legacy_pid(d->product_id)) {
            target = d;
            break;
        }
    }
    if (!target) {
        libambit_free_enumeration(devices);
        return NULL;
    }

    ambit_object_t *dev = libambit_new(target);
    *out_devices = devices;
    *out_info = target;
    return dev;
}

static int cmd_device_info(void) {
    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n"); return 1; }
    ambit_device_status_t status = {0};
    libambit_device_status_get(dev, &status);
    fputs("@@JSON@@\n", stdout); printf("{\"ok\": true, \"model\": ");
    json_str(stdout, info->model);
    printf(", \"serial\": ");
    json_str(stdout, info->serial);
    printf(", \"fw_version\": \"%u.%u.%u.%u\", \"hw_version\": \"%u.%u.%u.%u\", "
           "\"battery_percent\": %u, \"is_supported\": %s}\n",
           info->fw_version[0], info->fw_version[1], info->fw_version[2], info->fw_version[3],
           info->hw_version[0], info->hw_version[1], info->hw_version[2], info->hw_version[3],
           status.charge, info->is_supported ? "true" : "false");
    libambit_close(dev);
    libambit_free_enumeration(devices);
    return 0;
}

/* Fast waypoint read: raw 0x0b02 + 0x0b03 per index, STOPPING at the first empty slot - the
 * structured sequence SuuntoLink uses (confirmed in André's own capture, tools/ambit_pcap.py).
 * KEY (measured 2026-08-29): the Ambit2's 0x0b02 returns the slot CAPACITY (~250-300), NOT the
 * used count, so libambit_navigation_read (which reads EVERY slot) spends ~28s scanning empty
 * slots on macOS's ~90ms/report HID. Waypoints are stored contiguously from index 0 (SuuntoLink
 * reads 0..N-1), so we read sequentially and stop at the first empty one -> only ~N+1 reads,
 * single-digit seconds. 55-byte 0x0b03 record: [u16 index][u16 unk][name 16][route_name 16]
 * [ctime 7][i32 lat*1e7][i32 lon*1e7][u8 type]... (offsets confirmed vs the capture:
 * "Arras Town Hall" 50.29067/2.77784). Emits the same waypoints[] shape cmd_settings does. */
static int cmd_waypoints(void) {
    /* Empty waypoint SLOTS don't reply to 0x0b03 - the read blocks for the whole per-read
     * timeout (measured: a valid slot answers in ~7ms, an empty one times out). At the default
     * ~3s that's ~3s PER empty slot -> the ~28s. Cap the per-read timeout LOW so an empty slot
     * fails in ~400ms while valid slots (7ms) have a huge margin. Set BEFORE the first read
     * (the device open below) so protocol.c caches this value. Used waypoints are NOT contiguous
     * (route waypoints + POIs interleave with empties), so we can't stop at the first empty; we
     * read every slot up to the capacity and SKIP the empty/failed ones. ~capacity reads, most
     * ~7ms and the few empties ~400ms -> single-digit seconds, complete. */
    setenv("AMBIT_READ_TIMEOUT_MS", "400", 1);

    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n"); return 1; }

    /* 0x0b02 -> upper bound only (it's the capacity, not the used count). */
    uint8_t *reply = NULL;
    size_t replylen = 0;
    uint32_t cap = 512;
    if (libambit_protocol_command(dev, 0x0b02, NULL, 0, &reply, &replylen, 0) == 0 && replylen >= 2) {
        uint32_t c = (uint32_t)reply[0] | ((uint32_t)reply[1] << 8);
        if (c > 0 && c < cap) cap = c;
    }
    libambit_protocol_free(reply);
    reply = NULL;

    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": true, \"waypoints\": [\n");
    int emitted = 0;
    for (uint32_t x = 0; x < cap; x++) {
        uint8_t req[55];
        memset(req, 0, sizeof(req));
        req[0] = x & 0xff;
        req[1] = (x >> 8) & 0xff;                       /* [u16 index] in a zeroed record */
        reply = NULL;
        replylen = 0;
        if (libambit_protocol_command(dev, 0x0b03, req, sizeof(req), &reply, &replylen, 0) != 0
            || replylen < 55) {
            libambit_protocol_free(reply);
            reply = NULL;
            continue;                                   /* empty/failed slot -> SKIP, keep going */
        }
        char wname[16];
        memcpy(wname, reply + 4, 15);
        wname[15] = '\0';
        int32_t lat = (int32_t)((uint32_t)reply[43] | ((uint32_t)reply[44] << 8)
                                | ((uint32_t)reply[45] << 16) | ((uint32_t)reply[46] << 24));
        int32_t lon = (int32_t)((uint32_t)reply[47] | ((uint32_t)reply[48] << 8)
                                | ((uint32_t)reply[49] << 16) | ((uint32_t)reply[50] << 24));
        if (wname[0] == '\0' && lat == 0 && lon == 0) {  /* empty record -> skip, keep going */
            libambit_protocol_free(reply);
            reply = NULL;
            continue;
        }
        uint16_t widx = (uint16_t)reply[0] | ((uint16_t)reply[1] << 8);
        uint8_t type = reply[51];
        char rname[16];
        memcpy(rname, reply + 20, 15);
        rname[15] = '\0';
        printf("%s    {\"name\": ", emitted ? ",\n" : "");
        json_str(stdout, wname);
        printf(", \"lat\": %.7f, \"lon\": %.7f, \"altitude_m\": 0, \"type\": %u, "
               "\"index\": %u, \"route_name\": ",
               lat / 10000000.0, lon / 10000000.0, type, widx);
        json_str(stdout, rname);
        printf("}");
        emitted++;
        libambit_protocol_free(reply);
        reply = NULL;
    }
    printf("\n  ], \"waypoints_count\": %d}\n", emitted);
    libambit_close(dev);
    libambit_free_enumeration(devices);
    return 0;
}

static int cmd_settings(void) {
    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n"); return 1; }

    ambit_personal_settings_t *ps = libambit_personal_settings_alloc();
    int rc = ps ? libambit_personal_settings_get(dev, ps) : -1;
    /* Real bug, caught 2026-08-22 comparing against openambit2's own devicemanager.cpp
     * (the only other real, independent consumer of this library): personal_settings_get()
     * NEVER touches waypoints/routes (checked personal.c directly - zero mentions) - only
     * the SEPARATE libambit_navigation_read() call populates them. This function used to
     * report waypoints_count/routes_count straight from `ps` without ever calling it, so
     * every "0 waypoints, 0 routes" this project reported for the Ambit1 earlier the same
     * session was never actually queried - same "verify a zero result" mistake as the
     * skip_cb bug, on the same watch, same day. */
    int nav_rc = (rc == 0) ? libambit_navigation_read(dev, ps) : -1;
    if (rc != 0) {
        fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"personal_settings_get failed, rc=%d\"}\n", rc);
        if (ps) libambit_personal_settings_free(ps);
        libambit_close(dev);
        libambit_free_enumeration(devices);
        return 1;
    }

    /* Everything ambit_personal_settings_t actually carries, not just the personal block.
     * André, 2026-08-23: "on those settings guess we miss stuff no? all the units like the
     * ambit 3" - correct: libambit already reads the whole struct (per-unit choices, GPS
     * position format, time/date format, alarm, backlight, tones, alti/baro, pod
     * calibrations...), this only ever printed nine of them, so the Watch settings page had
     * nothing else to show. Nothing new is read off the watch here; the same one read is
     * simply reported in full. */
    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": true, \"weight_kg\": %.2f, \"birthyear\": %u, \"max_hr\": %u, "
           "\"rest_hr\": %u, \"fitness_level\": %u, \"is_male\": %u, \"length_cm\": %u, "
           "\"language\": %u, \"units_mode\": %u, "
           "\"gps_position_format\": %u, \"navigation_style\": %u, "
           "\"sync_time_w_gps\": %u, \"time_format\": %u, \"date_format\": %u, "
           "\"alarm_enable\": %u, \"alarm_hour\": %u, \"alarm_minute\": %u, "
           "\"dual_time_hour\": %u, \"dual_time_minute\": %u, "
           "\"tones_mode\": %u, \"backlight_mode\": %u, \"backlight_brightness\": %u, "
           "\"display_brightness\": %u, \"display_is_negative\": %u, "
           "\"alti_baro_mode\": %u, \"storm_alarm\": %u, \"fused_alti_disabled\": %u, "
           "\"compass_declination\": %u, "
           "\"sportmode_button_lock\": %u, \"timemode_button_lock\": %u, "
           "\"bikepod_calibration\": %.4f, \"bikepod_calibration2\": %.4f, "
           "\"bikepod_calibration3\": %.4f, \"footpod_calibration\": %.4f, "
           "\"automatic_bikepower_calib\": %u, \"automatic_footpod_calib\": %u, "
           "\"training_program\": %u, "
           "\"units\": {\"pressure\": %u, \"altitude\": %u, \"distance\": %u, "
           "\"height\": %u, \"temperature\": %u, \"verticalspeed\": %u, "
           "\"weight\": %u, \"compass\": %u, \"heartrate\": %u, \"speed\": %u}, "
           "\"navigation_read_rc\": %d, "
           "\"waypoints_count\": %u, \"waypoints\": [\n",
           ps->weight / 100.0, ps->birthyear, ps->max_hr, ps->rest_hr, ps->fitness_level,
           ps->is_male, ps->length, ps->language, ps->units_mode,
           ps->gps_position_format, ps->navigation_style,
           ps->sync_time_w_gps, ps->time_format, ps->date_format,
           ps->alarm_enable, ps->alarm.hour, ps->alarm.minute,
           ps->dual_time.hour, ps->dual_time.minute,
           ps->tones_mode, ps->backlight_mode, ps->backlight_brightness,
           ps->display_brightness, ps->display_is_negative,
           ps->alti_baro_mode, ps->storm_alarm, ps->fused_alti_disabled,
           ps->compass_declination,
           ps->sportmode_button_lock, ps->timemode_button_lock,
           ps->bikepod_calibration / 10000.0, ps->bikepod_calibration2 / 10000.0,
           ps->bikepod_calibration3 / 10000.0, ps->footpod_calibration / 10000.0,
           ps->automatic_bikepower_calib, ps->automatic_footpod_calib,
           ps->training_program,
           ps->units.pressure, ps->units.altitude, ps->units.distance,
           ps->units.height, ps->units.temperature, ps->units.verticalspeed,
           ps->units.weight, ps->units.compass, ps->units.heartrate, ps->units.speed,
           nav_rc, ps->waypoints.count);
    for (uint16_t i = 0; i < ps->waypoints.count; i++) {
        ambit_waypoint_t *w = &ps->waypoints.data[i];
        printf("    {\"name\": ");
        /* libambit fills these with strncpy(dst, src, 15), which does NOT NUL-terminate
         * when the source is exactly 15 characters - a real waypoint like "Arras Town Hall"
         * then ran on into whatever followed in the struct ("Arras Town Hall\x14 <garbage>").
         * Bound it here rather than trusting the terminator. */
        {
            char wname[16];
            memcpy(wname, w->name, 15);
            wname[15] = '\0';
            json_str(stdout, wname);
        }
        printf(", \"lat\": %.7f, \"lon\": %.7f, \"altitude_m\": %u, \"type\": %u, "
               "\"index\": %u, \"route_name\": ",
               w->latitude / 10000000.0, w->longitude / 10000000.0, w->altitude, w->type,
               w->index);
        /* On the Ambit1/2 a "route" is a set of waypoints sharing a route_name (libambit
         * reads them as waypoints, never fills ps->routes) - surface it so the backend can
         * regroup them into routes. Same strncpy(15)-no-terminator bound as name above. */
        {
            char rname[16];
            memcpy(rname, w->route_name, 15);
            rname[15] = '\0';
            json_str(stdout, rname);
        }
        printf("}%s\n", (i + 1 < ps->waypoints.count) ? "," : "");
    }
    printf("  ], \"routes_count\": %u, \"routes\": [\n", ps->routes.count);
    for (uint8_t i = 0; i < ps->routes.count; i++) {
        ambit_route_t *r = &ps->routes.data[i];
        printf("    {\"name\": ");
        json_str(stdout, r->name);
        printf(", \"waypoint_count\": %u, \"points_count\": %u, \"distance_m\": %u, "
               "\"altitude_asc_m\": %u, \"altitude_dec_m\": %u, \"points\": [\n",
               r->waypoint_count, r->points_count, r->distance, r->altitude_asc, r->altitude_dec);
        for (uint16_t p = 0; p < r->points_count; p++) {
            ambit_routepoint_t *pt = &r->points[p];
            printf("      {\"lat\": %.7f, \"lon\": %.7f, \"altitude_m\": %d}%s\n",
                   pt->lat / 10000000.0, pt->lon / 10000000.0, pt->altitude,
                   (p + 1 < r->points_count) ? "," : "");
        }
        printf("    ]}%s\n", (i + 1 < ps->routes.count) ? "," : "");
    }
    printf("  ]}\n");

    libambit_personal_settings_free(ps);
    libambit_close(dev);
    libambit_free_enumeration(devices);
    return 0;
}

static int cmd_logs(const char *outdir) {
    struct stat st;
    if (stat(outdir, &st) != 0) mkdir(outdir, 0755);

    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n"); return 1; }

    log_ctx_t ctx = { .idx = NULL, .outdir = (char *)outdir, .index = 0, .first = 1 };
    char idxbuf[65536];
    ctx.idx = fmemopen(idxbuf, sizeof(idxbuf), "w");
    if (!ctx.idx) {
        fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"fmemopen failed\"}\n");
        libambit_close(dev);
        libambit_free_enumeration(devices);
        return 1;
    }

    /* NULL skip_cb, not a callback that always returns "skip" - a real, embarrassing bug
     * found 2026-08-22: libambit.h documents ambit_log_skip_cb as "return 0 to skip entry,
     * else -1", and device_driver_ambit.c's log_read() walks headers first and ONLY starts
     * reading actual PMEM log data once skip_cb returns nonzero for one of them. The earlier
     * version of this file had a skip_cb that always returned 0 ("never skip" - backwards),
     * so it silently walked every header, skipped every one, and reported André's real
     * Ambit1 as having 0 logs when he knew for a fact it had real training data on it.
     * Passing NULL entirely takes the driver's own documented "no skip callback: read
     * everything" path instead - simpler and correct, no callback semantics left to get
     * backwards. */
    int rc = libambit_log_read(dev, NULL, log_push_cb, log_progress_cb, &ctx);
    fflush(ctx.idx);
    fclose(ctx.idx);

    /* libambit_log_read()'s return convention is driver-specific: device_driver_ambit.c
     * returns entries_read (a count, so 0 is a legitimately empty but successful read) and
     * only -1 on a real failure - NOT the plain 0-success/-1-failure convention most of this
     * library's other calls use. Found live, 2026-08-22: a real 9-entry read reported
     * "ok": false here (rc=9 != 0) even though every entry came back correct - cosmetic
     * (the data was already right), but worth getting right so a caller can trust "ok". */
    int ok = rc >= 0;
    fputs("@@JSON@@\n", stdout); printf("{\"ok\": %s, \"total_entries\": %d, \"logs\": [\n%s\n  ]}\n",
           ok ? "true" : "false", ctx.index, idxbuf);

    libambit_close(dev);
    libambit_free_enumeration(devices);
    return ok ? 0 : 1;
}

/* Read-only status query, added 2026-09-04: desktop's /api/agps/status and /api/agps/update
 * never had an Ambit1/2 branch at all - they always shelled out to tools/sgee.py, which is
 * the SBEM path (0x0b21 memory map + an SBEM-flavored device-info call) and this family
 * doesn't speak SBEM, so the Home page's "GPS orbit" card just showed "Failed" for every
 * legacy watch. libambit_gps_orbit_header_read() is NOT new code, though: it's the exact
 * call cmd_gps_orbit_write() below already makes internally (device_driver_ambit.c's own
 * gps_orbit_write() reads the header first to decide whether the watch already has this
 * generation), so this only exposes an already hardware-exercised call as its own command
 * instead of writing a fresh untested one. Layout is the driver's own 8-byte convention
 * (device_driver_ambit.c: reply_data[1..8], i.e. one byte shorter than sgee.py's SBEM 9-byte
 * reply because that extra leading byte there is a separate SBEM validity flag this older
 * protocol doesn't send) - [u16 LE year][u8 month][u8 day][u32 LE seconds-since-midnight
 * UTC]. All-zero is the real "no orbit data written yet" reply, not an error - same
 * decode_orbit_head() convention sgee.py already uses for the Ambit3-family SBEM reply.
 * "glonass": {"supported": false} always - this family has no GlonassSGEE region (same fact
 * cmd_gps_orbit_write's own docstring below already established), so there's nothing to
 * probe; matches the shape sgee.py's glonass_status() returns for a watch without the
 * region, which is what desktop/backend/server.py's _handle_agps_update() branches on.
 * UNVERIFIED against real hardware as its own standalone command - flag for HW confirmation
 * on an Ambit1/2 before shipping the "wrote"/"Updated" UI states that build on this. */
static int cmd_gps_orbit_status(void) {
    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n");
        return 1;
    }

    uint8_t header[8] = {0};
    int rc = libambit_gps_orbit_header_read(dev, header);
    fputs("@@JSON@@\n", stdout);
    if (rc != 0) {
        printf("{\"ok\": false, \"error\": \"failed to read GPS orbit header\"}\n");
        libambit_close(dev);
        libambit_free_enumeration(devices);
        return 1;
    }

    unsigned year = header[0] | (header[1] << 8);
    unsigned month = header[2], day = header[3];
    unsigned seconds = header[4] | (header[5] << 8) | (header[6] << 16) | ((unsigned)header[7] << 24);
    int valid = month >= 1 && month <= 12 && day >= 1 && day <= 31 && seconds < 86400;
    if (valid) {
        printf("{\"ok\": true, \"valid\": true, \"date\": \"%04u-%02u-%02u\", "
               "\"time\": \"%02u:%02u:%02u\", \"glonass\": {\"supported\": false}}\n",
               year, month, day, seconds / 3600, (seconds % 3600) / 60, seconds % 60);
    } else {
        printf("{\"ok\": true, \"valid\": false, \"glonass\": {\"supported\": false}}\n");
    }
    libambit_close(dev);
    libambit_free_enumeration(devices);
    return 0;
}

/* Real write, added 2026-08-22 for André's Ambit1: the legacy family has no 0x0b21 memory
 * map, so tools/sgee.py's own SBEM-based GPS orbit write (find the GpsSGEE region, then
 * write it) can't reach this family at all - confirmed live, "this watch does not declare a
 * GpsSGEE region". openambit's driver has a real, direct equivalent
 * (libambit_gps_orbit_write) that doesn't need a memory map - it's the watch's own driver
 * that knows where GPS orbit data lives for this protocol. Ephemeris data, not firmware -
 * same low-risk category as the Ambit3 family's proven GPS orbit write. */
static int cmd_gps_orbit_write(const char *path) {
    FILE *f = fopen(path, "rb");
    if (!f) { printf("{\"ok\": false, \"error\": \"cannot open %s\"}\n", path); return 1; }
    fseek(f, 0, SEEK_END);
    long len = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *data = malloc(len);
    if (fread(data, 1, len, f) != (size_t)len) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"short read of %s\"}\n", path);
        fclose(f); free(data);
        return 1;
    }
    fclose(f);

    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n");
        free(data);
        return 1;
    }

    int rc = libambit_gps_orbit_write(dev, data, (size_t)len);
    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"bytes_written\": %ld}\n", rc == 0 ? "true" : "false", len);

    free(data);
    libambit_close(dev);
    libambit_free_enumeration(devices);
    return rc == 0 ? 0 : 1;
}

/* Real write, added 2026-08-22: adds ONE waypoint, preserving every waypoint already on
 * the watch - read current (navigation_read), append (openambit's own
 * libambit_waypoint_append, real, not reimplemented here), write back
 * (navigation_write), read back again to CONFIRM (this project's own standing "prove it,
 * don't just trust the ack" discipline - see custom_modes_andre.md). */
static int cmd_poi_add(const char *name, double lat, double lon) {
    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n"); return 1; }

    ambit_personal_settings_t *ps = libambit_personal_settings_alloc();
    int rc = ps ? libambit_personal_settings_get(dev, ps) : -1;
    if (rc == 0) rc = libambit_navigation_read(dev, ps);
    if (rc != 0) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"reading current waypoints failed, rc=%d - refusing to write blind\"}\n", rc);
        if (ps) libambit_personal_settings_free(ps);
        libambit_close(dev);
        libambit_free_enumeration(devices);
        return 1;
    }
    uint16_t before_count = ps->waypoints.count;

    ambit_waypoint_t w;
    memset(&w, 0, sizeof(w));
    strncpy(w.name, name, sizeof(w.name) - 1);
    w.latitude = (int32_t)llround(lat * 10000000.0);
    w.longitude = (int32_t)llround(lon * 10000000.0);
    w.altitude = 0;
    w.type = 17;   /* "Waypoint" - matches write_nav.py's own WAYPOINT_TYPE_DEFAULT convention */
    w.status = 0;  /* real convention from openambit2's own movescountjson.cpp for a new entry */
    libambit_waypoint_append(ps, &w, 1);

    int write_rc = libambit_navigation_write(dev, ps);
    libambit_personal_settings_free(ps);

    /* Re-read for real confirmation rather than trusting the write's own return code. */
    ambit_personal_settings_t *verify = libambit_personal_settings_alloc();
    int verify_rc = verify ? libambit_navigation_read(dev, verify) : -1;
    uint16_t after_count = verify ? verify->waypoints.count : 0;
    int found = 0;
    if (verify) {
        for (uint16_t i = 0; i < verify->waypoints.count; i++) {
            if (strncmp(verify->waypoints.data[i].name, name, sizeof(verify->waypoints.data[i].name) - 1) == 0)
                found = 1;
        }
        libambit_personal_settings_free(verify);
    }

    int ok = (write_rc == 0) && (verify_rc == 0) && found && (after_count == before_count + 1);
    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"write_rc\": %d, \"before_count\": %u, \"after_count\": %u, \"confirmed_present\": %s}\n",
           ok ? "true" : "false", write_rc, before_count, after_count, found ? "true" : "false");

    libambit_close(dev);
    libambit_free_enumeration(devices);
    return ok ? 0 : 1;
}

/* Real write: writes back an empty waypoint list. Used to revert cmd_poi_add's own test
 * writes and, generally, to clear the on-device waypoint list. Does NOT touch routes -
 * ps->routes is left exactly as read. */
/* Read one flash region and print it as hex - READ ONLY, nothing is written.
 *
 * The Ambit1/2 route region (0x041EB0) has no reader anywhere: openambit can write routes
 * (ambit_navigation_route_write) but never reads them back, and this project's own Python
 * read_flash() speaks the Ambit3 dialect - every command through it, device_info included,
 * comes back empty on a Bluebird (checked live, 2026-08-27). libambit's own transport is what
 * this family answers, so the reader belongs here.
 *
 * 0x0b17 takes [u32 address][u32 length] and echoes both back before the data, exactly as
 * André's SuuntoLink capture shows it doing on his own Ambit1 (its first read is
 * 0x2000/4 B, then 512 B at a time). Chunked at 512 to match that capture rather than the
 * 1024 the Ambit3 path uses. */
static int cmd_flash_read(uint32_t address, uint32_t length) {
    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n"); return 1; }

    uint8_t *out = (uint8_t*)malloc(length);
    if (!out) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"out of memory\"}\n"); libambit_close(dev); libambit_free_enumeration(devices); return 1; }

    uint32_t got = 0;
    int rc = 0;
    while (got < length) {
        uint32_t want = (length - got) > 512 ? 512 : (length - got);
        uint8_t req[8];
        uint32_t a = htole32(address + got), l = htole32(want);
        memcpy(req, &a, 4);
        memcpy(req + 4, &l, 4);

        uint8_t *reply = NULL;
        size_t replylen = 0;
        rc = libambit_protocol_command(dev, ambit_command_log_read, req, sizeof(req), &reply, &replylen, 0);
        if (rc != 0 || replylen < 8 + want) {
            fputs("@@JSON@@\n", stdout);
            printf("{\"ok\": false, \"error\": \"0x0b17 at 0x%06x: rc=%d replylen=%zu (wanted %u)\", \"read\": %u}\n",
                   address + got, rc, replylen, want, got);
            if (reply) libambit_protocol_free(reply);
            free(out); libambit_close(dev); libambit_free_enumeration(devices);
            return 1;
        }
        memcpy(out + got, reply + 8, want);
        libambit_protocol_free(reply);
        got += want;
    }

    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": true, \"address\": %u, \"length\": %u, \"hex\": \"", address, got);
    for (uint32_t i = 0; i < got; i++) printf("%02x", out[i]);
    printf("\"}\n");

    free(out);
    libambit_close(dev);
    libambit_free_enumeration(devices);
    return 0;
}

static int cmd_poi_clear(void) {
    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n"); return 1; }

    ambit_personal_settings_t *ps = libambit_personal_settings_alloc();
    int rc = ps ? libambit_personal_settings_get(dev, ps) : -1;
    if (rc == 0) rc = libambit_navigation_read(dev, ps);
    if (rc != 0) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"reading current state failed, rc=%d\"}\n", rc);
        if (ps) libambit_personal_settings_free(ps);
        libambit_close(dev);
        libambit_free_enumeration(devices);
        return 1;
    }
    if (ps->waypoints.data) free(ps->waypoints.data);
    ps->waypoints.data = NULL;
    ps->waypoints.count = 0;

    int write_rc = libambit_navigation_write(dev, ps);
    libambit_personal_settings_free(ps);

    ambit_personal_settings_t *verify = libambit_personal_settings_alloc();
    int verify_rc = verify ? libambit_navigation_read(dev, verify) : -1;
    uint16_t after_count = verify ? verify->waypoints.count : 1;
    if (verify) libambit_personal_settings_free(verify);

    int ok = (write_rc == 0) && (verify_rc == 0) && (after_count == 0);
    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"write_rc\": %d, \"after_count\": %u}\n", ok ? "true" : "false", write_rc, after_count);

    libambit_close(dev);
    libambit_free_enumeration(devices);
    return ok ? 0 : 1;
}

/* hrbeltAndPods() bit pattern, transcribed from openambit2's CustomMode::hrbeltAndPods()
 * (movescount/sportmode.cpp) - our preset table only ever sets hr_belt/foot_pod/bike_pod/
 * cadence_pod, so accelerometer/power-pod bits are always 0 here, kept for fidelity anyway. */
static uint16_t hrbelt_and_pods(bool hr_belt, bool foot_pod, bool bike_pod, bool cadence_pod) {
    uint16_t v = 0;
    if (hr_belt)     v |= 0x0003;
    if (cadence_pod) v |= 0x0082;
    if (foot_pod)    v |= 0x0102;
    if (bike_pod)    v |= 0x0802;
    return v;
}

typedef struct {
    const char *name; uint16_t activity_id; uint16_t mode_id;
    uint16_t gps_interval; uint16_t recording_interval; uint16_t alti_baro_mode;
    bool hr_belt, foot_pod, bike_pod, cadence_pod;
    uint16_t autolap_m;
} preset_t;

/* openambit2's SportModeStorage::factoryDefaults() (src/openambit/sportmodestorage.cpp),
 * transcribed value-for-value - same 19 presets, same order, same field values. */
static const preset_t PRESETS[] = {
    {"Running",         10, 1,  1,  1,  0, true,  false, false, false, 1000},
    {"Trail Running",   13, 2,  1,  1,  1, true,  false, false, false, 5000},
    {"Cycling",         11, 3,  1,  1,  0, true,  false, true,  true,  5000},
    {"Mountain Biking",  3, 4,  1,  1,  1, true,  false, true,  false,    0},
    {"Hiking",           8, 5,  5,  5,  1, false, false, false, false,    0},
    {"Trekking",         9, 6, 10, 10,  1, false, false, false, false,    0},
    {"Nordic Walking",  14, 7,  5,  5,  0, true,  true,  false, false,    0},
    {"Rock Climbing",   25, 8, 30, 15,  1, true,  false, false, false,    0},
    {"Ski (Downhill)",  26, 9,  2,  2,  1, true,  false, false, false,    0},
    {"Ski Touring",     27, 10, 5,  5,  1, true,  false, false, false,    0},
    {"Snowshoeing",     28, 11, 10, 10, 1, false, false, false, false,    0},
    {"XC Skiing",       29, 12, 2,  2,  0, true,  false, false, false,    0},
    {"Kayaking",        18, 13, 2,  2,  0, false, false, false, false,    0},
    {"Rowing",          17, 14, 2,  2,  0, true,  false, false, false,  500},
    {"Surfing",         21, 15, 1,  1,  2, false, false, false, false,    0},
    {"Windsurfing",     20, 16, 1,  1,  2, false, false, false, false,    0},
    {"Sailing",         19, 17, 5,  5,  2, false, false, false, false,    0},
    {"Horse Riding",    23, 18, 5,  5,  0, false, false, false, false,    0},
    {"Other",            0, 19, 30, 30, 0, false, false, false, false,    0},
};
#define PRESET_TABLE_COUNT (sizeof(PRESETS) / sizeof(PRESETS[0]))

/* Real bug, caught 2026-08-23 by André BEFORE any write reached hardware ("the watch supports
 * like 8 screens max"): openambit2's own factoryDefaults() table has 19 entries, but that is
 * NOT this family's real sport-mode capacity - it's just their preset LIST, applied uniformly
 * to every watch with no per-model check (their own gap, not something we should inherit).
 * Ground truth is SuuntoLink's own real code: `getMaxSportModes(Variant.AMBIT/AMBIT2/AMBIT2_R/
 * AMBIT2_S)` all return 10 (ran ambit/sport_mode.js live via node, electron stubbed, same
 * method this project already uses for other per-model facts - see ambit-app-hardware-fleet-
 * check memory). getMaxDisplays() for the same variants returns 8 - the number André actually
 * remembered ("8 screens max" is real, just the wrong axis: it caps displays PER mode, not the
 * count of modes; moot here anyway since every preset carries 0 displays). So this writes only
 * the FIRST N of the table below - never the full 19 - to stay inside the real, Suunto-
 * confirmed capacity for this device family. */
#define LEGACY_SPORT_MODE_WRITE_COUNT 10
#define PRESET_COUNT \
    (LEGACY_SPORT_MODE_WRITE_COUNT < PRESET_TABLE_COUNT \
         ? LEGACY_SPORT_MODE_WRITE_COUNT : PRESET_TABLE_COUNT)

/* Field-for-field port of openambit2's CustomMode::toAmbitSettings() (movescount/sportmode.cpp)
 * for exactly the fields our preset table sets - every field NOT settable from a preset
 * (unknown1..6, HR limits, intervals, auto-pause/scroll, backlight, display mode, quick nav)
 * is left at their own "off"/default value, matching what toAmbitSettings() does when its
 * corresponding QVariantMap key is the makeMode() default (0/false/empty). */
static void fill_preset_settings(ambit_sport_mode_settings_t *s, const preset_t *p) {
    memset(s, 0, sizeof(*s));
    memset(s->activity_name, 0, sizeof(s->activity_name));
    strncpy(s->activity_name, p->name, sizeof(s->activity_name) - 1);
    s->activity_id = p->activity_id;
    s->sport_mode_id = p->mode_id;
    s->hrbelt_and_pods = hrbelt_and_pods(p->hr_belt, p->foot_pod, p->bike_pod, p->cadence_pod);
    s->alti_baro_mode = p->alti_baro_mode;
    s->gps_interval = p->gps_interval;
    s->recording_interval = p->recording_interval;
    s->autolap = p->autolap_m;
    s->heartrate_max = 180;   /* HRLimitHigh default, unused since use_heartrate_limits=0 */
    s->heartrate_min = 120;   /* HRLimitLow default, unused since use_heartrate_limits=0 */
    s->use_heartrate_limits = 0;
    s->auto_pause = 0;
    s->auto_scroll = 0;
    s->use_interval_timer = 0;
    s->interval_repetitions = 0;
    s->interval_timer_max_unit = 0;
    s->interval_timer_max = 0;
    s->interval_timer_min_unit = 0;
    s->interval_timer_min = 0;
    s->backlight_mode = 0;           /* makeMode() sets BacklightMode=0 explicitly */
    s->display_mode = 0;             /* makeMode() sets DisplayIsNegative=0 explicitly */
    s->quick_navigation = 0;
}

static ambit_sport_mode_device_settings_t *build_preset_sport_modes(void) {
    ambit_sport_mode_device_settings_t *settings = libambit_malloc_sport_mode_device_settings();
    if (!settings) return NULL;
    if (!libambit_malloc_sport_modes(PRESET_COUNT, settings)) {
        libambit_sport_mode_device_settings_free(settings);
        return NULL;
    }
    for (size_t i = 0; i < PRESET_COUNT; i++) {
        fill_preset_settings(&settings->sport_modes[i].settings, &PRESETS[i]);
        /* displays_count/apps_list_count already 0 from libambit_malloc_sport_modes - matches
         * openambit2's own factoryDefaults() (Displays: QVariantList(), always empty). */
    }
    return settings;
}

/* Reads the host's master copy of the user's sport modes. One mode per line, pipe-separated,
 * in the same field order as preset_t:
 *
 *   name|activity_id|mode_id|gps_interval|recording_interval|alti_baro_mode|
 *   hr_belt|foot_pod|bike_pod|cadence_pod|autolap_m
 *
 * Deliberately NOT JSON: the master copy itself IS json, but it lives on the Python side
 * (legacy_sport_modes.json - see server.py's LEGACY_SPORT_MODES_FILE), which converts to this
 * trivially-parseable form for the one hop into C. Keeps a hand-rolled JSON parser out of this
 * file entirely - same "don't reimplement what the other side already does well" reasoning as
 * the rest of this CLI. Names are sanitised host-side (no '|', no newline).
 *
 * Blank lines and lines starting with '#' are skipped. Reads at most
 * LEGACY_SPORT_MODE_WRITE_COUNT modes - the real per-device capacity (see PRESET_COUNT's own
 * comment above); anything past that is ignored rather than written past what the watch holds. */
static ambit_sport_mode_device_settings_t *load_sport_modes_file(const char *path,
                                                                  char names[][32],
                                                                  size_t *out_count) {
    FILE *f = fopen(path, "r");
    if (!f) return NULL;

    preset_t parsed[LEGACY_SPORT_MODE_WRITE_COUNT];
    char namebuf[LEGACY_SPORT_MODE_WRITE_COUNT][32];
    size_t n = 0;
    char line[512];

    while (n < LEGACY_SPORT_MODE_WRITE_COUNT && fgets(line, sizeof(line), f)) {
        char *nl = strchr(line, '\n');
        if (nl) *nl = '\0';
        if (line[0] == '\0' || line[0] == '#') continue;

        char *save = NULL;
        char *tok = strtok_r(line, "|", &save);
        if (!tok) continue;
        snprintf(namebuf[n], sizeof(namebuf[n]), "%s", tok);

        unsigned int v[10];
        size_t got = 0;
        while (got < 10 && (tok = strtok_r(NULL, "|", &save)) != NULL)
            v[got++] = (unsigned int)strtoul(tok, NULL, 10);
        if (got < 10) continue;   /* malformed line - skip rather than write garbage */

        parsed[n].name = namebuf[n];
        parsed[n].activity_id       = (uint16_t)v[0];
        parsed[n].mode_id           = (uint16_t)v[1];
        parsed[n].gps_interval      = (uint16_t)v[2];
        parsed[n].recording_interval= (uint16_t)v[3];
        parsed[n].alti_baro_mode    = (uint16_t)v[4];
        parsed[n].hr_belt           = v[5] != 0;
        parsed[n].foot_pod          = v[6] != 0;
        parsed[n].bike_pod          = v[7] != 0;
        parsed[n].cadence_pod       = v[8] != 0;
        parsed[n].autolap_m         = (uint16_t)v[9];
        n++;
    }
    fclose(f);
    if (n == 0) return NULL;

    ambit_sport_mode_device_settings_t *settings = libambit_malloc_sport_mode_device_settings();
    if (!settings) return NULL;
    if (!libambit_malloc_sport_modes(n, settings)) {
        libambit_sport_mode_device_settings_free(settings);
        return NULL;
    }
    for (size_t i = 0; i < n; i++) {
        fill_preset_settings(&settings->sport_modes[i].settings, &parsed[i]);
        snprintf(names[i], 32, "%s", namebuf[i]);
    }
    *out_count = n;
    return settings;
}

/* Shared tail for both write commands - `settings` is consumed (freed) either way. */
static int write_sport_modes(ambit_sport_mode_device_settings_t *settings, size_t count,
                              char names[][32], bool dry_run) {
    if (dry_run) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": true, \"dry_run\": true, \"mode_count\": %zu, \"names\": [", count);
        for (size_t i = 0; i < count; i++) {
            json_str(stdout, names[i]);
            if (i + 1 < count) fputs(", ", stdout);
        }
        printf("]}\n");
        libambit_sport_mode_device_settings_free(settings);
        return 0;
    }

    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n");
        libambit_sport_mode_device_settings_free(settings);
        return 1;
    }

    int rc = libambit_sport_mode_write(dev, settings);
    libambit_sport_mode_device_settings_free(settings);

    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"write_rc\": %d, \"mode_count\": %zu}\n",
           rc == 0 ? "true" : "false", rc, count);

    libambit_close(dev);
    libambit_free_enumeration(devices);
    return rc == 0 ? 0 : 1;
}

/* Raw flash READ of the sport-mode region - the backup openambit2 has no equivalent of.
 *
 * Found 2026-08-23 while about to do the first real sport-mode write: pmem20.c's
 * read_log_chunk() is NOT log-specific despite its name - it just sends
 * ambit_command_log_read (0x0b17) with an arbitrary {u32 address, u32 length} and returns the
 * bytes, which is the same generic flash-read command this project's own write_nav.py already
 * uses on Ambit3 ("read-only: 0x0b17 reads flash, nothing is written"). It's `static`, and its
 * pmem20/driver_data plumbing is private to the driver, so this issues the same command
 * directly instead - read-only either way, nothing is written.
 *
 * This does NOT decode sport modes (nobody can - there is no deserializer anywhere, which is
 * the whole reason the host has to hold the master copy). It captures the region's raw BYTES,
 * so whatever is on the watch before a blind write can be put back verbatim later via
 * libambit_pmem20_data_write at the same address - restore without ever understanding the
 * format. Chunked at the Bluebird driver's own 0x200 (512 B, its device_support.c
 * driver_param); a short/failed reply ends the dump rather than padding it with junk, so the
 * file only ever holds bytes the watch really returned. */
#define LEGACY_SPORT_MODE_ADDR  0x00002000
#define LEGACY_READ_CHUNK       512

static int cmd_region_dump(const char *path, uint32_t total, uint32_t base) {
    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n");
        return 1;
    }

    FILE *out = fopen(path, "wb");
    if (!out) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"cannot open output file\"}\n");
        libambit_close(dev);
        libambit_free_enumeration(devices);
        return 1;
    }

    uint32_t got = 0;
    int failed = 0;
    while (got < total) {
        uint32_t want = total - got;
        if (want > LEGACY_READ_CHUNK) want = LEGACY_READ_CHUNK;

        uint8_t send[8];
        uint32_t addr = base + got;
        send[0] = addr & 0xff; send[1] = (addr >> 8) & 0xff;
        send[2] = (addr >> 16) & 0xff; send[3] = (addr >> 24) & 0xff;
        send[4] = want & 0xff; send[5] = (want >> 8) & 0xff;
        send[6] = (want >> 16) & 0xff; send[7] = (want >> 24) & 0xff;

        uint8_t *reply = NULL;
        size_t replylen = 0;
        if (libambit_protocol_command(dev, 0x0b17, send, sizeof(send),
                                       &reply, &replylen, 0) != 0
            || replylen < want + 8) {
            libambit_protocol_free(reply);
            failed = 1;
            break;
        }
        fwrite(reply + 8, 1, want, out);
        libambit_protocol_free(reply);
        got += want;
    }
    fclose(out);

    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"bytes\": %u, \"address\": %u, \"truncated\": %s, \"path\": ",
           got > 0 ? "true" : "false", got, base,
           failed ? "true" : "false");
    json_str(stdout, path);
    printf("}\n");

    libambit_close(dev);
    libambit_free_enumeration(devices);
    return got > 0 ? 0 : 1;
}

static int cmd_sport_mode_write_presets(bool dry_run) {
    ambit_sport_mode_device_settings_t *settings = build_preset_sport_modes();
    if (!settings) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"failed to allocate preset sport modes\"}\n");
        return 1;
    }
    char names[LEGACY_SPORT_MODE_WRITE_COUNT][32];
    for (size_t i = 0; i < PRESET_COUNT; i++)
        snprintf(names[i], sizeof(names[i]), "%s", PRESETS[i].name);
    return write_sport_modes(settings, PRESET_COUNT, names, dry_run);
}

static int cmd_sport_mode_write_file(const char *path, bool dry_run) {
    char names[LEGACY_SPORT_MODE_WRITE_COUNT][32];
    size_t count = 0;
    ambit_sport_mode_device_settings_t *settings =
        load_sport_modes_file(path, names, &count);
    if (!settings) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"could not read any sport modes from ");
        json_str(stdout, path);
        printf("\"}\n");
        return 1;
    }
    return write_sport_modes(settings, count, names, dry_run);
}


/* Personal-settings WRITE - command 0x0b01, solved 2026-08-23 from André's own USB capture
 * (assets/pcap/2026-08-23-ambit1-suuntolink/ambit1languages.pcap).
 *
 * The format needed no reverse-engineering in the end: SuuntoLink sends back the SAME
 * 132-byte structure the 0x0b00 read returns, with the changed field patched in place. In the
 * capture, 26 consecutive writes differ by exactly ONE byte each - byte 20 stepping through
 * the language enum and ending on 4 (Francais), byte 19 cycling the GPS position format, byte
 * 24 toggling GPS time keeping - which is precisely what André reported doing on the watch.
 * Those offsets are the same ones personal.c's own parser reads, so read and write share one
 * layout.
 *
 * So this is read-modify-write, exactly like the sport-mode patcher: fetch the live 132
 * bytes, patch the requested field, send them back. Nothing is invented and every byte the
 * caller did not ask to change is preserved. Field names/offsets are the parser's own, and a
 * u16 field is written little-endian like everything else here. */
static const struct { const char *name; int off; int width; } A1_SETTING_FIELDS[] = {
    {"sportmode_button_lock", 1, 1}, {"timemode_button_lock", 2, 1},
    /* NOT one u16, whatever libambit's parser suggests. The capture shows byte 4 taking
     * 0/1/2 and byte 5 taking 0/1/180 as André walked SuuntoLink's declination control:
     * off -> W 180 -> W 1 -> E 1 -> E 180 -> off. So byte 4 is the direction and byte 5 the
     * magnitude in degrees; reading them as a little-endian u16 yields nonsense like 46082. */
    {"compass_declination_dir", 4, 1}, {"compass_declination_deg", 5, 1},
    {"units_mode", 8, 1},
    {"units.pressure", 9, 1},  {"units.altitude", 10, 1}, {"units.distance", 11, 1},
    {"units.height", 12, 1},   {"units.temperature", 13, 1}, {"units.verticalspeed", 14, 1},
    {"units.weight", 15, 1},   {"units.compass", 16, 1}, {"units.heartrate", 17, 1},
    {"units.speed", 18, 1},
    {"gps_position_format", 19, 1}, {"language", 20, 1}, {"navigation_style", 21, 1},
    {"sync_time_w_gps", 24, 1}, {"time_format", 25, 1},
    {"alarm_hour", 26, 1}, {"alarm_minute", 27, 1}, {"alarm_enable", 28, 1},
    {"dual_time_hour", 31, 1}, {"dual_time_minute", 32, 1},
    {"date_format", 36, 1}, {"tones_mode", 40, 1},
    {"backlight_mode", 44, 1}, {"backlight_brightness", 45, 1},
    {"display_brightness", 46, 1}, {"display_is_negative", 47, 1},
    {"weight", 48, 2}, {"birthyear", 50, 2},
    {"max_hr", 52, 1}, {"rest_hr", 53, 1}, {"fitness_level", 54, 1},
    {"is_male", 55, 1}, {"length", 56, 1},
    {"alti_baro_mode", 60, 1},
    /* Bike POD calibration factors, stored x10000 (1.0 = 10000). Offsets from personal.c's
     * own 0x80 block. Only these TWO exist here: calibration 3, the foot pod and the
     * auto-calibration flags all sit behind that file's `datalen >= 137` check, whose comment
     * reads "Only Ambit 2 got this!" - and this device's blob is 132 bytes. */
    {"bikepod_calibration", 128, 2}, {"bikepod_calibration2", 130, 2},
};
#define A1_SETTINGS_BLOB 132

static int cmd_settings_write(const char *key, long value, int dry_run) {
    const int nfields = (int)(sizeof(A1_SETTING_FIELDS) / sizeof(A1_SETTING_FIELDS[0]));
    int idx = -1;
    for (int i = 0; i < nfields; i++)
        if (strcmp(A1_SETTING_FIELDS[i].name, key) == 0) { idx = i; break; }
    if (idx < 0) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"unknown setting \"}\n");
        return 1;
    }
    long maxv = (A1_SETTING_FIELDS[idx].width == 2) ? 65535 : 255;
    if (value < 0 || value > maxv) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"value out of range for this field\"}\n");
        return 1;
    }

    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n");
        return 1;
    }
    if (!is_legacy_pid(info->product_id)) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"settings write is the Ambit1/2 (Bluebird) family only\"}\n");
        libambit_close(dev); libambit_free_enumeration(devices);
        return 1;
    }

    /* The settings struct is 132 B on the Ambit1 but 188 B on the Ambit2 (confirmed from a
     * real SuuntoLink USBPcap 2026-08-26 - see docs/ambit2_protocol_findings.md). The
     * confirmed field offsets are family-common (personal.c reads them identically for all),
     * so this is a read-modify-WRITE of the device's OWN full struct: A1_SETTINGS_BLOB (132)
     * stays the sanity floor (every confirmed field lives below it), but we copy and write
     * back exactly `replylen` bytes so the Ambit2's extra tail is preserved, not truncated. */
    uint8_t *reply = NULL; size_t replylen = 0;
    if (libambit_protocol_command(dev, 0x0b00, NULL, 0, &reply, &replylen, 0) != 0
        || replylen < A1_SETTINGS_BLOB) {
        libambit_protocol_free(reply);
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"could not read current settings; nothing written\"}\n");
        libambit_close(dev); libambit_free_enumeration(devices);
        return 1;
    }
    int off = A1_SETTING_FIELDS[idx].off;
    if ((size_t)(off + A1_SETTING_FIELDS[idx].width) > replylen) {
        libambit_protocol_free(reply);
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"field offset beyond this device's settings struct; nothing written\"}\n");
        libambit_close(dev); libambit_free_enumeration(devices);
        return 1;
    }
    uint8_t *blob = malloc(replylen);
    memcpy(blob, reply, replylen);
    libambit_protocol_free(reply);

    unsigned old = (A1_SETTING_FIELDS[idx].width == 2)
                     ? (unsigned)(blob[off] | (blob[off + 1] << 8)) : blob[off];
    if (A1_SETTING_FIELDS[idx].width == 2) {
        blob[off] = value & 0xff; blob[off + 1] = (value >> 8) & 0xff;
    } else {
        blob[off] = (uint8_t)value;
    }

    int rc = 0;
    if (!dry_run) {
        uint8_t *wreply = NULL; size_t wlen = 0;
        rc = libambit_protocol_command(dev, 0x0b01, blob, replylen, &wreply, &wlen, 0);
        libambit_protocol_free(wreply);
    }
    free(blob);

    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"dryRun\": %s, \"field\": ", rc == 0 ? "true" : "false",
           dry_run ? "true" : "false");
    json_str(stdout, key);
    printf(", \"offset\": %d, \"was\": %u, \"now\": %ld, \"writeRc\": %d}\n",
           off, old, value, rc);

    libambit_close(dev);
    libambit_free_enumeration(devices);
    return rc == 0 ? 0 : 1;
}

/* Real flash-region WRITE: chunked 0x0b16 data_write + the 0x0b18 commit tail that makes it
 * stick (openambit omits the tail and its writes silently revert after reconnect - see the
 * ambit-app-legacy-write-commit-tail note). Used to restore the Ambit1/2 route region
 * (0x041EB0, tail extra 0xFFFFFA1A) and, in principle, any legacy flash region.
 *
 * The chunk size is the DEVICE'S OWN driver_param (Ambit2 1024, Ambit1 512 - device_support.c):
 * a 1024-B chunk to an Ambit1 gets NAK'd. Both confirmed against André's own SuuntoLink pcaps
 * (Ambit1 route region written in 512-B 0x0b16 packets, tail 0x041eb0/0xfffffa1a; Ambit2 1024).
 * `extra` is passed in because it is region-specific (routes 0xFFFFFA1A, sport modes 0xFFFFFFFF).
 */
static int cmd_flash_write(uint32_t address, const char *file, uint32_t extra) {
    FILE *f = fopen(file, "rb");
    if (!f) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"cannot open %s\"}\n", file); return 1; }
    fseek(f, 0, SEEK_END);
    long flen = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (flen <= 0 || flen > 2 * 1024 * 1024) {
        fclose(f); fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"implausible file length %ld\"}\n", flen);
        return 1;
    }
    uint8_t *buf = (uint8_t*)malloc((size_t)flen);
    if (!buf || fread(buf, 1, (size_t)flen, f) != (size_t)flen) {
        free(buf); fclose(f); fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"read %s failed\"}\n", file);
        return 1;
    }
    fclose(f);

    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) { free(buf); fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n"); return 1; }

    /* Per-device write chunk = device_support.c's driver_param: the Ambit1 (Bluebird 0x0010)
     * caps 0x0b16 writes at 512, the Ambit2 family (Duck/Colibri/Greentit) at 1024. Both
     * confirmed against André's own SuuntoLink pcaps. Map by product_id directly - the table's
     * find_first() isn't exported from libambit.so. Unknown legacy pid -> the safe 512. */
    uint32_t chunk = (info->product_id == 0x0010) ? 512 : 1024;

    uint32_t off = 0;
    int rc = 0;
    while (off < (uint32_t)flen) {
        uint32_t want = ((uint32_t)flen - off) > chunk ? chunk : ((uint32_t)flen - off);
        uint8_t *req = (uint8_t*)malloc(8 + want);
        uint32_t a = htole32(address + off), l = htole32(want);
        memcpy(req, &a, 4);
        memcpy(req + 4, &l, 4);
        memcpy(req + 8, buf + off, want);
        uint8_t *reply = NULL; size_t replylen = 0;
        rc = libambit_protocol_command(dev, ambit_command_data_write, req, 8 + want, &reply, &replylen, 0);
        free(req);
        if (reply) libambit_protocol_free(reply);
        if (rc != 0) {
            fputs("@@JSON@@\n", stdout);
            printf("{\"ok\": false, \"error\": \"0x0b16 at 0x%06x rc=%d (chunk %u)\", \"written\": %u}\n",
                   address + off, rc, chunk, off);
            free(buf); libambit_close(dev); libambit_free_enumeration(devices);
            return 1;
        }
        off += want;
    }
    free(buf);

    /* 0x0b18 commit tail [u32 addr][u32 extra] - no hash (the sport-mode/nav variant). */
    uint8_t tail[8];
    uint32_t a = htole32(address), e = htole32(extra);
    memcpy(tail, &a, 4);
    memcpy(tail + 4, &e, 4);
    uint8_t *treply = NULL; size_t tlen = 0;
    int trc = libambit_protocol_command(dev, ambit_command_data_tail_len, tail, sizeof(tail), &treply, &tlen, 0);
    if (treply) libambit_protocol_free(treply);

    int ok = (trc == 0);
    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"address\": %u, \"written\": %u, \"chunk\": %u, \"tail_extra\": %u, \"tail_rc\": %d}\n",
           ok ? "true" : "false", address, off, chunk, extra, trc);

    libambit_close(dev);
    libambit_free_enumeration(devices);
    return ok ? 0 : 1;
}

/* Real waypoint (POI) RESTORE: replaces the whole on-device waypoint list with the set in
 * FILE. FILE is a flat binary, 48 bytes per waypoint, little-endian (same layout the Android
 * app's AmbitLegacyNav.encodeWaypoints writes):
 *   [0..15] name  [16..31] route_name  [32] lat i32  [36] lon i32  [40] type(u8, RAW device
 *   type)  [41] year u16  [43] month [44] day [45] hour [46] minute [47] second
 * We keep `type` RAW (no Movescount table conversion) so a backup round-trips byte-exact -
 * hence the command path here rather than libambit_navigation_write (which would convert it).
 *   0x0b1b write_start -> 0x0b04 nav_memory_delete -> one 0x0b05 waypoint_write per point.
 * 0x0b04 clears the waypoint list only (NOT the route flash region - HW-confirmed), so this
 * never endangers routes; the caller restores routes separately via flash-write. */
static int cmd_waypoints_restore(const char *file) {
    FILE *f = fopen(file, "rb");
    if (!f) { fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"cannot open %s\"}\n", file); return 1; }
    fseek(f, 0, SEEK_END);
    long flen = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (flen < 0 || (flen % 48) != 0 || flen > 48 * 250) {
        fclose(f); fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"bad record file length %ld (want a multiple of 48, <=250 pts)\"}\n", flen);
        return 1;
    }
    int count = (int)(flen / 48);
    uint8_t *recs = (uint8_t*)malloc((size_t)(flen > 0 ? flen : 1));
    if (flen > 0 && (!recs || fread(recs, 1, (size_t)flen, f) != (size_t)flen)) {
        free(recs); fclose(f); fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"read %s failed\"}\n", file);
        return 1;
    }
    fclose(f);

    ambit_device_info_t *devices, *info;
    ambit_object_t *dev = open_selected_device(&devices, &info);
    if (!dev) { free(recs); fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n"); return 1; }

    /* write_start then clear the whole nav list. */
    uint8_t *r = NULL; size_t rl = 0;
    if (libambit_protocol_command(dev, ambit_command_write_start, NULL, 0, &r, &rl, 0) != 0) {
        if (r) libambit_protocol_free(r);
        fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"write_start (0x0b1b) denied\"}\n");
        free(recs); libambit_close(dev); libambit_free_enumeration(devices); return 1;
    }
    if (r) { libambit_protocol_free(r); r = NULL; }
    if (libambit_protocol_command(dev, ambit_command_nav_memory_delete, NULL, 0, &r, &rl, 0) != 0) {
        if (r) libambit_protocol_free(r);
        fputs("@@JSON@@\n", stdout); printf("{\"ok\": false, \"error\": \"nav_memory_delete (0x0b04) failed\"}\n");
        free(recs); libambit_close(dev); libambit_free_enumeration(devices); return 1;
    }
    if (r) { libambit_protocol_free(r); r = NULL; }

    int wrote = 0, failed = 0;
    for (int i = 0; i < count; i++) {
        const uint8_t *rec = recs + (size_t)i * 48;
        uint8_t p[55];
        memset(p, 0, sizeof(p));
        memcpy(p + 4, rec + 0, 16);            /* name[16] */
        p[19] = 0;
        memcpy(p + 20, rec + 16, 16);          /* route_name[16] */
        p[35] = 0;
        p[36] = rec[47];                       /* ctime_second */
        p[37] = rec[46];                       /* ctime_minute */
        p[38] = rec[45];                       /* ctime_hour */
        p[39] = rec[44];                       /* ctime_day */
        p[40] = rec[43];                       /* ctime_month */
        p[41] = rec[41]; p[42] = rec[42];      /* ctime_year u16 (LE, copied verbatim) */
        memcpy(p + 43, rec + 32, 4);           /* latitude i32 (LE, verbatim) */
        memcpy(p + 47, rec + 36, 4);           /* longitude i32 (LE, verbatim) */
        p[51] = rec[40];                       /* type (raw device type) */
        p[53] = (uint8_t)strnlen((const char *)(p + 4), 16);  /* name_count */
        uint8_t *wr = NULL; size_t wl = 0;
        if (libambit_protocol_command(dev, ambit_command_waypoint_write, p, sizeof(p), &wr, &wl, 0) != 0) failed++;
        else wrote++;
        if (wr) libambit_protocol_free(wr);
    }
    free(recs);

    /* No in-command navigation_read verify here. libambit's waypoint_read has a latent
     * heap bug that intermittently double-frees when reading a freshly-written list back on
     * this connection (host-side only, AFTER the writes land - the writes themselves are
     * confirmed correct via the separate `settings` command). Reading back through the same
     * process that just wrote is exactly what triggers it, so the caller (server.py's restore
     * / legacy_link) confirms via a fresh `settings` invocation instead - the stable path
     * (personal_settings_get + navigation_read), which never hit this. Report the write result. */
    int ok = (failed == 0);
    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"requested\": %d, \"wrote\": %d, \"failed\": %d}\n",
           ok ? "true" : "false", count, wrote, failed);

    libambit_close(dev);
    libambit_free_enumeration(devices);
    return ok ? 0 : 1;
}

int main(int argc, char **argv) {
    /* Optional leading "--device PID" (decimal or 0x-hex) picks which connected Suunto
     * device this invocation talks to - required whenever more than one might be on the
     * bus (see open_selected_device()'s own comment for the real bug this fixes). */
    if (argc >= 3 && strcmp(argv[1], "--device") == 0) {
        g_selected_pid = (int)strtol(argv[2], NULL, 0);
        argv += 2;
        argc -= 2;
    }
    if (argc < 2) {
        fprintf(stderr, "usage: %s [--device PID] device-info|settings|logs OUTDIR|"
                "gps-orbit-status|gps-orbit-write FILE\n", argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "device-info") == 0) return cmd_device_info();
    if (strcmp(argv[1], "settings") == 0) return cmd_settings();
    if (strcmp(argv[1], "waypoints") == 0) return cmd_waypoints();
    if (strcmp(argv[1], "logs") == 0) {
        if (argc < 3) { fprintf(stderr, "usage: %s logs OUTDIR\n", argv[0]); return 2; }
        return cmd_logs(argv[2]);
    }
    if (strcmp(argv[1], "gps-orbit-status") == 0) return cmd_gps_orbit_status();
    if (strcmp(argv[1], "gps-orbit-write") == 0) {
        if (argc < 3) { fprintf(stderr, "usage: %s gps-orbit-write FILE\n", argv[0]); return 2; }
        return cmd_gps_orbit_write(argv[2]);
    }
    if (strcmp(argv[1], "poi-add") == 0) {
        if (argc < 5) { fprintf(stderr, "usage: %s poi-add NAME LAT LON\n", argv[0]); return 2; }
        return cmd_poi_add(argv[2], atof(argv[3]), atof(argv[4]));
    }
    if (strcmp(argv[1], "poi-clear") == 0) return cmd_poi_clear();
    if (strcmp(argv[1], "flash-read") == 0) {
        if (argc < 4) { fprintf(stderr, "usage: %s flash-read ADDR LEN   (both decimal or 0x...)\n", argv[0]); return 2; }
        return cmd_flash_read((uint32_t)strtoul(argv[2], NULL, 0), (uint32_t)strtoul(argv[3], NULL, 0));
    }
    if (strcmp(argv[1], "flash-write") == 0) {
        if (argc < 5) { fprintf(stderr, "usage: %s flash-write ADDR FILE EXTRA   (ADDR/EXTRA decimal or 0x...)\n", argv[0]); return 2; }
        return cmd_flash_write((uint32_t)strtoul(argv[2], NULL, 0), argv[3], (uint32_t)strtoul(argv[4], NULL, 0));
    }
    if (strcmp(argv[1], "waypoints-restore") == 0) {
        if (argc < 3) { fprintf(stderr, "usage: %s waypoints-restore FILE   (flat 48-byte records)\n", argv[0]); return 2; }
        return cmd_waypoints_restore(argv[2]);
    }
    if (strcmp(argv[1], "settings-write") == 0) {
        if (argc < 4) {
            fprintf(stderr, "usage: %s settings-write KEY VALUE [--dry-run]\n", argv[0]);
            return 2;
        }
        int dry = (argc >= 5 && strcmp(argv[4], "--dry-run") == 0);
        return cmd_settings_write(argv[2], strtol(argv[3], NULL, 10), dry);
    }
    if (strcmp(argv[1], "sport-mode-write-presets") == 0) {
        bool dry_run = (argc >= 3 && strcmp(argv[2], "--dry-run") == 0);
        return cmd_sport_mode_write_presets(dry_run);
    }
    if (strcmp(argv[1], "ambit1-sport-mode-restore") == 0) {
        if (argc < 3) {
            fprintf(stderr, "usage: %s ambit1-sport-mode-restore FILE [--dry-run]\n", argv[0]);
            return 2;
        }
        ambit_device_info_t *devices, *info;
        ambit_object_t *dev = open_selected_device(&devices, &info);
        if (!dev) {
            fputs("@@JSON@@\n", stdout);
            printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n");
            return 1;
        }
        int dry = (argc >= 4 && strcmp(argv[3], "--dry-run") == 0);
        int rc = ambit1_cmd_restore(dev, info, argv[2], dry);
        libambit_close(dev);
        libambit_free_enumeration(devices);
        return rc;
    }
    if (strcmp(argv[1], "ambit1-sport-mode-read") == 0
        || strcmp(argv[1], "ambit1-sport-mode-patch") == 0) {
        ambit_device_info_t *devices, *info;
        ambit_object_t *dev = open_selected_device(&devices, &info);
        if (!dev) {
            fputs("@@JSON@@\n", stdout);
            printf("{\"ok\": false, \"error\": \"no Suunto device found on the USB bus\"}\n");
            return 1;
        }
        int rc;
        if (strcmp(argv[1], "ambit1-sport-mode-read") == 0) {
            rc = ambit1_cmd_read(dev, info);
        } else {
            if (argc < 3) {
                fprintf(stderr, "usage: %s ambit1-sport-mode-patch FILE [--dry-run] [--dump OUT]\n", argv[0]);
                libambit_close(dev); libambit_free_enumeration(devices);
                return 2;
            }
            int dry = 0; const char *dump = NULL;
            for (int i = 3; i < argc; i++) {
                if (strcmp(argv[i], "--dry-run") == 0) dry = 1;
                else if (strcmp(argv[i], "--dump") == 0 && i + 1 < argc) dump = argv[++i];
            }
            rc = ambit1_cmd_patch(dev, info, argv[2], dry, dump);
        }
        libambit_close(dev);
        libambit_free_enumeration(devices);
        return rc;
    }
    if (strcmp(argv[1], "sport-mode-dump") == 0) {
        if (argc < 3) {
            fprintf(stderr, "usage: %s sport-mode-dump FILE [BYTES]\n", argv[0]);
            return 2;
        }
        uint32_t n = (argc >= 4) ? (uint32_t)strtoul(argv[3], NULL, 0) : 8192;
        return cmd_region_dump(argv[2], n, LEGACY_SPORT_MODE_ADDR);
    }
    /* Any flash region, read-only. The Apps region (0x000927c0, PMEM20_APP_START) is the
     * reason this exists: André, 2026-08-23 - "it is the same file for apps for the ambit 3
     * ...they are cross" - so once the bytes are off the watch, tools/apps.py --from decodes
     * them with no Ambit1-specific code at all. */
    if (strcmp(argv[1], "region-dump") == 0) {
        if (argc < 4) {
            fprintf(stderr, "usage: %s region-dump ADDR FILE [BYTES]\n", argv[0]);
            return 2;
        }
        uint32_t base = (uint32_t)strtoul(argv[2], NULL, 0);
        uint32_t n = (argc >= 5) ? (uint32_t)strtoul(argv[4], NULL, 0) : 8192;
        return cmd_region_dump(argv[3], n, base);
    }
    if (strcmp(argv[1], "sport-mode-write") == 0) {
        if (argc < 3) {
            fprintf(stderr, "usage: %s sport-mode-write FILE [--dry-run]\n", argv[0]);
            return 2;
        }
        bool dry_run = (argc >= 4 && strcmp(argv[3], "--dry-run") == 0);
        return cmd_sport_mode_write_file(argv[2], dry_run);
    }
    fprintf(stderr, "unknown command %s\n", argv[1]);
    return 2;
}

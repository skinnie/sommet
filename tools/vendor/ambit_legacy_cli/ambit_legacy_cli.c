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
 * full payload and reports its shape without touching the watch - no raw pre-write backup was
 * added (a symmetric raw-pmem-read-at-address command was never RE'd here or in openambit2 -
 * NOT the same as the log_read/personal_settings_get paths, which are separate, structured
 * mechanisms, not a generic "read N bytes at address"), so this is exactly openambit2's own
 * risk profile: real, but no readback safety net.
 *
 * One JSON object printed to stdout per invocation - same "--json" convention as every
 * other tools py CLI that backend/server.py's run_tool() already parses.
 *
 *   ambit_legacy_cli device-info
 *   ambit_legacy_cli settings
 *   ambit_legacy_cli logs OUTDIR              # writes OUTDIR/<n>.gpx + prints an index
 *   ambit_legacy_cli gps-orbit-write FILE
 *   ambit_legacy_cli poi-add NAME LAT LON     # preserves existing waypoints
 *   ambit_legacy_cli poi-clear                # writes back 0 waypoints
 *   ambit_legacy_cli sport-mode-write-presets [--dry-run]   # blind REPLACE, no readback
 *   ambit_legacy_cli sport-mode-write FILE [--dry-run]      # same, from the host master copy
 *
 * Build: see build.sh in this directory (links against ../openambit_libambit's libambit).
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <math.h>
#include "libambit.h"

static void json_str(FILE *f, const char *s) {
    fputc('"', f);
    for (const unsigned char *p = (const unsigned char *)s; s && *p; p++) {
        if (*p == '"' || *p == '\\') fputc('\\', f);
        if (*p < 0x20) { fprintf(f, "\\u%04x", *p); continue; }
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

    fputs("@@JSON@@\n", stdout); printf("{\"ok\": true, \"weight_kg\": %.2f, \"birthyear\": %u, \"max_hr\": %u, "
           "\"rest_hr\": %u, \"fitness_level\": %u, \"is_male\": %u, \"length_cm\": %u, "
           "\"language\": %u, \"units_mode\": %u, \"navigation_read_rc\": %d, "
           "\"waypoints_count\": %u, \"waypoints\": [\n",
           ps->weight / 100.0, ps->birthyear, ps->max_hr, ps->rest_hr, ps->fitness_level,
           ps->is_male, ps->length, ps->language, ps->units_mode, nav_rc, ps->waypoints.count);
    for (uint16_t i = 0; i < ps->waypoints.count; i++) {
        ambit_waypoint_t *w = &ps->waypoints.data[i];
        printf("    {\"name\": ");
        json_str(stdout, w->name);
        printf(", \"lat\": %.7f, \"lon\": %.7f, \"altitude_m\": %u, \"type\": %u}%s\n",
               w->latitude / 10000000.0, w->longitude / 10000000.0, w->altitude, w->type,
               (i + 1 < ps->waypoints.count) ? "," : "");
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
                "gps-orbit-write FILE\n", argv[0]);
        return 2;
    }
    if (strcmp(argv[1], "device-info") == 0) return cmd_device_info();
    if (strcmp(argv[1], "settings") == 0) return cmd_settings();
    if (strcmp(argv[1], "logs") == 0) {
        if (argc < 3) { fprintf(stderr, "usage: %s logs OUTDIR\n", argv[0]); return 2; }
        return cmd_logs(argv[2]);
    }
    if (strcmp(argv[1], "gps-orbit-write") == 0) {
        if (argc < 3) { fprintf(stderr, "usage: %s gps-orbit-write FILE\n", argv[0]); return 2; }
        return cmd_gps_orbit_write(argv[2]);
    }
    if (strcmp(argv[1], "poi-add") == 0) {
        if (argc < 5) { fprintf(stderr, "usage: %s poi-add NAME LAT LON\n", argv[0]); return 2; }
        return cmd_poi_add(argv[2], atof(argv[3]), atof(argv[4]));
    }
    if (strcmp(argv[1], "poi-clear") == 0) return cmd_poi_clear();
    if (strcmp(argv[1], "sport-mode-write-presets") == 0) {
        bool dry_run = (argc >= 3 && strcmp(argv[2], "--dry-run") == 0);
        return cmd_sport_mode_write_presets(dry_run);
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

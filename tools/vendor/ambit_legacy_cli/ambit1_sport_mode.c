/* Ambit1 (Bluebird, product_id 0x0010) sport modes - READ.
 *
 * DELIBERATELY A SEPARATE FILE FROM ambit_legacy_cli.c, and hard-guarded to product_id
 * 0x0010 only (André, 2026-08-23: "device ambit 1 => a new lib? or a different section?").
 * The reason is a real corruption risk, not tidiness:
 *
 *   The Ambit1 stores a 76-byte sport-mode settings blob. Every OTHER device in this family
 *   - Ambit2, Ambit2 S, Ambit2 R - declares the FULL capability set in SuuntoLink's own
 *   Devices.xml (custommodeid / usehrlimits / autoscrolling / displaymode /
 *   navigationselection), exactly like the Ambit3, and so uses the standard 90-byte layout
 *   libambit already implements. Ambit1 is the odd one out.
 *
 * Format: docs/ambit1_sport_mode_format.md (offsets, evidence, and which bytes are still
 * unproven). Region 0x2000, nested TLV [u16 tag][u16 len], settings blob = tag 0x0102.
 *
 * WRITING moved to Python on 2026-11-26 (watch-protocol/10 L5): tools/legacy_sport_modes.py
 * patch_region / patch_displays build the bytes and tools/legacy_link.py writes them through
 * `flash-write` - the region's first 10240 bytes in 512-byte chunks and then the 0x0b18
 * commit tail [0x2000][0xFFFFFFFF], as SuuntoLink writes this region in its capture. The
 * `ambit1-sport-mode-patch` and `ambit1-sport-mode-restore` commands this file used to have
 * wrote 8192 bytes and no commit tail, which the watch drops after a reconnect (the Android
 * app's 2026-08-27 notes), and patch built a new display by cloning another one on the watch
 * rather than as SuuntoLink does; both are gone. The reader below stays: the desktop's legacy
 * sport-mode master copy (/api/legacy/sport-modes) is still seeded from it.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "libambit.h"
#include "protocol.h"
#include "ambit1_sport_mode.h"

#define A1_PID              0x0010
#define A1_REGION_ADDR      0x00002000
#define A1_CHUNK            512
#define A1_REGION_MAX       8192
#define A1_SETTINGS_LEN     76

#define TAG_ROOT        0x0003
#define TAG_MODES       0x0100
#define TAG_MODE        0x0101
#define TAG_SETTINGS    0x0102
/* Display tags, straight from sport_mode_serialize.h - the Ambit1 uses the same nesting the
 * serializer writes: DISPLAYS > DISPLAY > (LAYOUT, ROWS > ROW, VIEW...). */
#define TAG_DISPLAYS    0x0105
#define TAG_DISPLAY     0x0106
#define TAG_LAYOUT      0x0107
#define TAG_ROWS        0x0108
#define TAG_ROW         0x0109
#define TAG_VIEW        0x010a

/* A user-editable display vs one of the watch's own built-in screens. The Ambit3 decides this
 * from a `Type` field; the Ambit1's blob has no such field, so it is derived from the layout
 * id - and the ids are the SAME ones the Ambit3 uses (260 = 3 rows, 261 = 2, 262 = 1,
 * 257 = graph), which is why the existing page renders them with no per-device mapping.
 * Everything else seen on real hardware (273 / 290 / 291 / 336) is a built-in: compass,
 * navigation, map and friends. Counting only the user ones makes "N display(s)" agree with
 * what the Ambit3 shows for the same watch.
 *
 * CORRECTED 2026-08-23: testing each layout id against a fixed "user" list does NOT work.
 * 257 (graph) and 260 (3-row) appear BOTH as real user displays and inside the built-in
 * tail, which made Running report 10 user displays when this watch's own maximum is 8 -
 * impossible, and the tell that the heuristic was wrong.
 *
 * What is actually true is that every mode ends with the SAME run of built-in screens, so
 * the tail is found by taking the longest display-layout suffix common to all modes. That is
 * self-calibrating, which matters because the tail is NOT a constant: this watch's
 * Movescount-era contents end with a 5-entry tail (273,291,290,336,260) while the same watch
 * after a SuuntoLink sync ends with a 6-entry one (273,291,290,257,336,260) - a hardcoded
 * table would have been wrong for one of them. Same idea as custom_modes.py's own
 * system_tail_length() for the Ambit3, computed rather than listed.
 *
 * 2026-11-26: the format does carry the marker after all - the 0x0107 record's second u16 is
 * 10 on every screen the wearer set up and 4/5/6/7/50/65 on the built-ins, as on the Ambit3
 * (custom_modes.USER_DISPLAY_TYPE). The desktop's Sport Modes page and the display writer use
 * that; this suffix rule gives the same answer on every captured Ambit1 region
 * (tools/test_legacy_display_edit.py). */
static uint16_t rd16(const uint8_t *p, int off) {
    return (uint16_t)(p[off] | (p[off + 1] << 8));
}

static void print_displays_json(FILE *f, const uint8_t *body, uint32_t len, int keep);

static int a1_collect_layouts(const uint8_t *body, uint32_t len, uint16_t *out, int max) {
    uint32_t p = 0; int n = 0;
    while (p + 4 <= len && n < max) {
        uint16_t tag = rd16(body, p), ln = rd16(body, p + 2);
        if (tag == TAG_DISPLAYS || tag == TAG_ROWS || tag == TAG_DISPLAY) { p += 4; continue; }
        if (tag == TAG_LAYOUT) {
            if (ln >= 2) out[n++] = rd16(body, p + 4);
            p += 4 + ln; continue;
        }
        p += 4 + ln;
    }
    return n;
}

/* Offsets inside the 76-byte blob. Confirmed against real SuuntoLink traffic except where
 * noted - see the format doc's evidence column. */
#define OFF_NAME            0   /* 16 bytes, NUL-padded            */
#define OFF_ACTIVITY_ID     16
#define OFF_PODS            18
#define OFF_ALTI_BARO       20
#define OFF_GPS_INTERVAL    22
#define OFF_REC_INTERVAL    24
#define OFF_AUTOLAP         26
#define OFF_HR_MAX          28
#define OFF_HR_MIN          30
/* 32 = the one byte pair never observed non-zero; no SuuntoLink control maps to it. */
#define OFF_AUTO_PAUSE      34
#define OFF_USE_INTERVAL    36
#define OFF_INTERVAL_REPS   38
#define OFF_IT_MAX_UNIT     40
#define OFF_IT_MAX          48
#define OFF_IT_MIN_UNIT     52
#define OFF_IT_MIN          60

int ambit1_guard_ok(const ambit_device_info_t *info) {
    return info && info->product_id == A1_PID;
}

/* Raw flash read via 0x0b17 - the same generic read documented in ambit_legacy_cli.c's own
 * cmd_sport_mode_dump. Read-only. Returns bytes read, or -1. */
int ambit1_read_region(ambit_object_t *dev, uint8_t *out, uint32_t max) {
    uint32_t got = 0;
    while (got < max) {
        uint32_t want = max - got;
        if (want > A1_CHUNK) want = A1_CHUNK;
        uint32_t addr = A1_REGION_ADDR + got;
        uint8_t send[8];
        send[0] = addr & 0xff;        send[1] = (addr >> 8) & 0xff;
        send[2] = (addr >> 16) & 0xff; send[3] = (addr >> 24) & 0xff;
        send[4] = want & 0xff;        send[5] = (want >> 8) & 0xff;
        send[6] = (want >> 16) & 0xff; send[7] = (want >> 24) & 0xff;

        uint8_t *reply = NULL; size_t replylen = 0;
        if (libambit_protocol_command(dev, 0x0b17, send, sizeof(send),
                                       &reply, &replylen, 0) != 0 || replylen < want + 8) {
            libambit_protocol_free(reply);
            return got > 0 ? (int)got : -1;
        }
        memcpy(out + got, reply + 8, want);
        libambit_protocol_free(reply);
        got += want;
    }
    return (int)got;
}

/* Walks the TLV and records where each mode's settings blob starts. Returns mode count. */
int ambit1_find_modes_ex(const uint8_t *buf, uint32_t len, uint32_t *offsets,
                          uint32_t *body_off, uint32_t *body_len, int max_modes) {
    if (len < 4) return 0;
    uint16_t root_tag = rd16(buf, 0), root_len = rd16(buf, 2);
    if (root_tag != TAG_ROOT) return 0;
    uint32_t end = 4 + root_len;
    if (end > len) end = len;

    uint32_t o = 4;
    if (o + 4 > end) return 0;
    if (rd16(buf, o) != TAG_MODES) return 0;
    uint32_t modes_end = o + 4 + rd16(buf, o + 2);
    if (modes_end > end) modes_end = end;
    o += 4;

    int n = 0;
    while (o + 4 <= modes_end && n < max_modes) {
        uint16_t tag = rd16(buf, o), l = rd16(buf, o + 2);
        if (tag != TAG_MODE) {          /* e.g. the 0x010b filler before the first mode */
            o += 4 + l;
            continue;
        }
        uint32_t so = o + 4;
        if (so + 4 <= modes_end && rd16(buf, so) == TAG_SETTINGS
            && rd16(buf, so + 2) == A1_SETTINGS_LEN) {
            if (body_off) body_off[n] = so;
            if (body_len) body_len[n] = l;
            offsets[n++] = so + 4;
        }
        o = so + l;
    }
    return n;
}

/* The Ambit1 stores names in ISO-8859 (single-byte), NOT UTF-8 - proven 2026-08-23 from a
 * real SuuntoLink write of the Portuguese mode name "Corrida de Acção", which appears on the
 * wire as `Corrida de Ac \xe7 \xe3 o`: one byte per accent. UTF-8 would need c3 a7 / c3 a3.
 * The Ambit3 family really is UTF-8 (proven separately on a French Ambit3 Sport), so this is
 * per-DEVICE, matching Suunto's own `supportsUtf8Encoding` capability - do NOT "unify" the two.
 *
 * JSON must be valid UTF-8, so every byte >= 0x80 is transcoded here. ISO-8859-1/15 map to
 * U+0080..U+00FF directly, which is a 2-byte UTF-8 sequence - emitted as \u00XX so the output
 * is pure ASCII and cannot be mis-decoded downstream. (Emitting the raw byte instead produced
 * invalid UTF-8 and a real UnicodeDecodeError in the Python caller.) */
int ambit1_find_modes(const uint8_t *buf, uint32_t len, uint32_t *offsets, int max_modes) {
    return ambit1_find_modes_ex(buf, len, offsets, NULL, NULL, max_modes);
}

static void print_mode_json(FILE *f, const uint8_t *b) {
    unsigned char name[17];
    memcpy(name, b + OFF_NAME, 16);
    name[16] = '\0';
    fprintf(f, "{\"name\": \"");
    for (const unsigned char *p = name; *p; p++) {
        if (*p == '"' || *p == '\\') fputc('\\', f);
        if (*p < 0x20 || *p >= 0x80) { fprintf(f, "\\u%04x", *p); continue; }
        fputc(*p, f);
    }
    fprintf(f,
        "\", \"activityId\": %u, \"pods\": %u, \"altiBaroMode\": %u, \"gpsInterval\": %u, "
        "\"recordingInterval\": %u, \"autolapM\": %u, \"hrMax\": %u, \"hrMin\": %u, "
        "\"autoPause\": %u, \"useIntervalTimer\": %u, \"intervalRepetitions\": %u, "
        "\"intervalMaxUnit\": %u, \"intervalMax\": %u, \"intervalMinUnit\": %u, "
        "\"intervalMin\": %u",
        rd16(b, OFF_ACTIVITY_ID), rd16(b, OFF_PODS), rd16(b, OFF_ALTI_BARO),
        rd16(b, OFF_GPS_INTERVAL), rd16(b, OFF_REC_INTERVAL), rd16(b, OFF_AUTOLAP),
        rd16(b, OFF_HR_MAX), rd16(b, OFF_HR_MIN), rd16(b, OFF_AUTO_PAUSE),
        rd16(b, OFF_USE_INTERVAL), rd16(b, OFF_INTERVAL_REPS), rd16(b, OFF_IT_MAX_UNIT),
        rd16(b, OFF_IT_MAX), rd16(b, OFF_IT_MIN_UNIT), rd16(b, OFF_IT_MIN));
}

/* print_mode_json() deliberately leaves the JSON object OPEN (no trailing brace) so the
 * displays array can be appended here. stdout is a pipe, so rewinding to patch the brace is
 * not an option. */
static void print_mode_json_full(FILE *f, const uint8_t *b,
                                  const uint8_t *body, uint32_t body_len, int keep) {
    print_mode_json(f, b);
    fputs(", \"displays\": ", f);
    print_displays_json(f, body, body_len, keep);
    fputs("}", f);
}

/* Emits one mode's `displays` array in exactly the shape tools/custom_modes.py's
 * _displays_to_json() produces, so SportModesPage renders Ambit1 screens with no per-device
 * branch: index / screenNumber / isBuiltIn / templateId / fields[].
 *
 * Row-to-values rule, read off real hardware: a display carries up to three ROW entries
 * (row 0 = Top, 1 = Center, 2 = Bottom) each holding one field id, plus an optional list of
 * VIEW entries. When VIEWs are present they belong to the LAST row, which is the watch's
 * multi-value row - the one that cycles through several readings. That is the same
 * isMultiValue idea the Ambit3 page already draws, e.g. Cycling display 1 = Top 5, Center 11,
 * Bottom cycling [1, 12, 23, 10].
 *
 * `body`/`len` span one mode's TLV payload (from its SETTINGS header to the end of the mode). */
static void print_displays_json(FILE *f, const uint8_t *body, uint32_t len, int keep) {
    uint32_t p = 0;
    int emitted = 0, user_index = 0;
    int open_display = 0;
    uint16_t layout = 0;
    uint16_t rows[3]; int row_count = 0;
    uint16_t views[16]; int view_count = 0;

    fputs("[", f);
    while (p + 4 <= len) {
        uint16_t tag = rd16(body, p), ln = rd16(body, p + 2);

        if (tag == TAG_DISPLAYS || tag == TAG_ROWS) { p += 4; continue; }  /* containers */

        if (tag == TAG_DISPLAY) {
            /* flush the previous one before starting the next */
            if (open_display && user_index < keep) {
                if (emitted++) fputs(",", f);
                fprintf(f, "\n    {\"index\": %d, \"screenNumber\": %d, \"isBuiltIn\": false, "
                           "\"templateId\": %u, \"template\": \"\", \"fields\": [",
                        user_index, user_index + 1, layout);
                for (int r = 0; r < row_count; r++) {
                    const char *label = (r == 0) ? "Top" : (r == 1) ? "Center" : "Bottom";
                    int last = (r == row_count - 1);
                    int multi = last && view_count > 1;
                    fprintf(f, "%s{\"rowLabel\": \"%s\", \"isMultiValue\": %s, \"values\": [",
                            r ? ", " : "", label, multi ? "true" : "false");
                    if (last && view_count > 0) {
                        for (int v = 0; v < view_count; v++)
                            fprintf(f, "%s{\"type\": %u}", v ? ", " : "", views[v]);
                    } else {
                        fprintf(f, "{\"type\": %u}", rows[r]);
                    }
                    fputs("]}", f);
                }
                fputs("]}", f);
                user_index++;
            }
            open_display = 1; layout = 0; row_count = 0; view_count = 0;
            p += 4; continue;
        }
        if (tag == TAG_LAYOUT && open_display) {
            if (ln >= 2) layout = rd16(body, p + 4);
            p += 4 + ln; continue;
        }
        if (tag == TAG_ROW && open_display) {
            if (ln >= 4 && row_count < 3) rows[row_count++] = rd16(body, p + 6);
            p += 4 + ln; continue;
        }
        if (tag == TAG_VIEW && open_display) {
            /* 0xfffe is a real terminator seen on hardware, not a field id */
            if (ln >= 2 && view_count < 16) {
                uint16_t v = rd16(body, p + 4);
                if (v != 0xfffe) views[view_count++] = v;
            }
            p += 4 + ln; continue;
        }
        p += 4 + ln;
    }
    /* the final display */
    if (open_display && user_index < keep) {
        if (emitted++) fputs(",", f);
        fprintf(f, "\n    {\"index\": %d, \"screenNumber\": %d, \"isBuiltIn\": false, "
                   "\"templateId\": %u, \"template\": \"\", \"fields\": [",
                user_index, user_index + 1, layout);
        for (int r = 0; r < row_count; r++) {
            const char *label = (r == 0) ? "Top" : (r == 1) ? "Center" : "Bottom";
            int last = (r == row_count - 1);
            int multi = last && view_count > 1;
            fprintf(f, "%s{\"rowLabel\": \"%s\", \"isMultiValue\": %s, \"values\": [",
                    r ? ", " : "", label, multi ? "true" : "false");
            if (last && view_count > 0) {
                for (int v = 0; v < view_count; v++)
                    fprintf(f, "%s{\"type\": %u}", v ? ", " : "", views[v]);
            } else {
                fprintf(f, "{\"type\": %u}", rows[r]);
            }
            fputs("]}", f);
        }
        fputs("]}", f);
    }
    fputs("]", f);
}

int ambit1_cmd_read(ambit_object_t *dev, const ambit_device_info_t *info) {
    if (!ambit1_guard_ok(info)) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"not an Ambit1 (product_id 0x0010) - this module "
               "handles the Ambit1 76-byte layout only\"}\n");
        return 1;
    }
    uint8_t buf[A1_REGION_MAX];
    int got = ambit1_read_region(dev, buf, A1_REGION_MAX);
    if (got <= 0) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"region read failed\"}\n");
        return 1;
    }
    uint32_t offs[32], boff[32], blen[32];
    int n = ambit1_find_modes_ex(buf, (uint32_t)got, offs, boff, blen, 32);

    /* The built-in tail: longest display-layout suffix common to every mode (see
     * a1_collect_layouts' comment for why this is computed and not a fixed table). */
    uint16_t lay[32][64];
    int lay_n[32];
    for (int i = 0; i < n; i++)
        lay_n[i] = a1_collect_layouts(buf + boff[i], blen[i], lay[i], 64);
    int tail = 0;
    if (n > 1) {
        int shortest = lay_n[0];
        for (int i = 1; i < n; i++) if (lay_n[i] < shortest) shortest = lay_n[i];
        while (tail < shortest) {
            uint16_t v = lay[0][lay_n[0] - tail - 1];
            int same = 1;
            for (int i = 1; i < n; i++)
                if (lay[i][lay_n[i] - tail - 1] != v) { same = 0; break; }
            if (!same) break;
            tail++;
        }
    }

    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": true, \"source\": \"watch\", \"systemTail\": %d, "
           "\"modeCount\": %d, \"modes\": [\n", tail, n);
    for (int i = 0; i < n; i++) {
        int keep = lay_n[i] - tail;
        if (keep < 0) keep = 0;
        printf("    ");
        print_mode_json_full(stdout, buf + offs[i], buf + boff[i], blen[i], keep);
        printf("%s\n", (i + 1 < n) ? "," : "");
    }
    printf("  ]}\n");
    return 0;
}

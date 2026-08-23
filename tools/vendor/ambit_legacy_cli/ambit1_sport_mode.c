/* Ambit1 (Bluebird, product_id 0x0010) sport modes - READ and READ-MODIFY-WRITE.
 *
 * DELIBERATELY A SEPARATE FILE FROM ambit_legacy_cli.c, and hard-guarded to product_id
 * 0x0010 only (André, 2026-08-23: "device ambit 1 => a new lib? or a different section?").
 * The reason is a real corruption risk, not tidiness:
 *
 *   The Ambit1 stores a 76-byte sport-mode settings blob. Every OTHER device in this family
 *   - Ambit2, Ambit2 S, Ambit2 R - declares the FULL capability set in SuuntoLink's own
 *   Devices.xml (custommodeid / usehrlimits / autoscrolling / displaymode /
 *   navigationselection), exactly like the Ambit3, and so almost certainly uses the standard
 *   90-byte layout libambit already implements. Ambit1 is the odd one out.
 *
 *   So a shared "legacy" code path is precisely how 90-byte records end up written to a
 *   76-byte device (or vice versa), landing every field past offset 18 in the wrong place.
 *   ambit1_guard_ok() makes that structurally impossible: this file refuses to touch anything
 *   that is not an Ambit1. When a real Ambit2 is available to verify against (expected
 *   2026-08-30), its 90-byte path belongs in its OWN file next to this one - NOT bolted on
 *   here with an if.
 *
 * Format: docs/ambit1_sport_mode_format.md (offsets, evidence, and which bytes are still
 * unproven). Region 0x2000, nested TLV [u16 tag][u16 len], settings blob = tag 0x0102.
 *
 * WRITES ARE READ-MODIFY-WRITE, never rebuild-from-scratch. A mode is ~500-650 bytes of
 * which only 76 are settings - the rest is its display configuration. Rebuilding from a
 * preset table (what openambit2's editor does) silently destroys every display the user has.
 * Patching in place keeps the blob length at 76 so nothing shifts, and leaves displays/apps
 * byte-identical.
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
/* SuuntoLink's own getMaxDisplays() for this watch family. */
#define A1_MAX_DISPLAYS     8

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
 * system_tail_length() for the Ambit3, computed rather than listed. */
static uint16_t rd16(const uint8_t *p, int off) {
    return (uint16_t)(p[off] | (p[off + 1] << 8));
}
static void wr16(uint8_t *p, int off, uint16_t v) {
    p[off] = v & 0xff;
    p[off + 1] = (v >> 8) & 0xff;
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

/* Raw flash write via 0x0b16, same framing pmem20.c's write_data_chunk uses:
 * [u32 address][u32 length][data]. Only ever called with the region we just read back and
 * patched in place, so length and structure are unchanged. */
static int ambit1_write_region(ambit_object_t *dev, const uint8_t *data, uint32_t len) {
    uint32_t done = 0;
    while (done < len) {
        uint32_t n = len - done;
        if (n > A1_CHUNK) n = A1_CHUNK;
        uint32_t addr = A1_REGION_ADDR + done;
        uint8_t *send = malloc(8 + n);
        if (!send) return -1;
        send[0] = addr & 0xff;        send[1] = (addr >> 8) & 0xff;
        send[2] = (addr >> 16) & 0xff; send[3] = (addr >> 24) & 0xff;
        send[4] = n & 0xff;           send[5] = (n >> 8) & 0xff;
        send[6] = (n >> 16) & 0xff;   send[7] = (n >> 24) & 0xff;
        memcpy(send + 8, data + done, n);

        uint8_t *reply = NULL; size_t replylen = 0;
        int rc = libambit_protocol_command(dev, 0x0b16, send, 8 + n, &reply, &replylen, 0);
        free(send);
        libambit_protocol_free(reply);
        if (rc != 0) return -1;
        done += n;
    }
    return 0;
}


/* Locates the byte position of one display-row VALUE inside a mode body, so it can be patched
 * in place.
 *
 * Why in place is enough for the edit the UI actually offers: a ROW's payload is
 * [u16 row_nbr][u16 item] and a VIEW's is [u16 item], so changing WHICH data a row shows is a
 * fixed-size, 2-byte write. No TLV length changes, nothing shifts, and the parent length
 * fields stay correct - the same property that makes the settings patcher safe. (Adding or
 * removing a display, or changing a layout's row count, WOULD resize the record and cascade
 * through every parent length; that is deliberately not attempted here.)
 *
 * `disp_idx` counts USER displays only and must match what the reader emitted, so `keep` (the
 * mode's user-display count, system tail already excluded) is passed in and enforced.
 * `value_idx` selects among a multi-value row's VIEW entries; for a single-value row it is 0
 * and the ROW's own item is targeted.
 *
 * Returns the absolute offset of the u16 to write, or 0 if the address does not resolve. */
static uint32_t a1_find_row_value(const uint8_t *body, uint32_t len, int keep,
                                   int disp_idx, int row_idx, int value_idx) {
    uint32_t p = 0;
    int cur_disp = -1, cur_row = -1;
    uint32_t row_item_off = 0;          /* the target ROW's own item field */
    uint32_t views[32];                 /* and any VIEW items that follow it */
    int view_count = 0;
    int in_target_row = 0;

    while (p + 4 <= len) {
        uint16_t tag = rd16(body, p), ln = rd16(body, p + 2);

        if (tag == TAG_DISPLAYS || tag == TAG_ROWS) { p += 4; continue; }

        if (tag == TAG_DISPLAY) {
            if (row_item_off) break;    /* target row already captured; stop here */
            cur_disp++; cur_row = -1; in_target_row = 0;
            if (cur_disp > disp_idx) break;
            p += 4; continue;
        }
        if (tag == TAG_ROW) {
            if (row_item_off) break;
            cur_row++;
            in_target_row = (cur_disp == disp_idx && cur_row == row_idx);
            if (in_target_row) {
                if (ln < 4) return 0;
                row_item_off = p + 6;   /* [hdr 4][u16 row_nbr][u16 item] */
            }
            p += 4 + ln; continue;
        }
        if (tag == TAG_VIEW) {
            /* VIEWs belong to the row most recently seen */
            if (in_target_row && view_count < 32 && ln >= 2) views[view_count++] = p + 4;
            p += 4 + ln; continue;
        }
        p += 4 + ln;
    }

    if (cur_disp >= keep) return 0;     /* refuse anything in the built-in tail */
    if (!row_item_off) return 0;

    /* A row with VIEWs is the watch's multi-value row: its values ARE the VIEW items, and the
     * ROW's own item is 0. Otherwise the single value lives in the ROW itself. */
    if (view_count > 0) {
        if (value_idx >= view_count) return 0;
        return views[value_idx];
    }
    return (value_idx == 0) ? row_item_off : 0;
}


/* ---- structural display edits: add / remove -------------------------------------------
 *
 * Unlike a row-value change, these RESIZE the record, so four nested length fields have to be
 * corrected together: the mode's DISPLAYS container, the MODE itself, the MODES container and
 * the root. Everything after the edit point shifts, and the region's 0xFF tail absorbs the
 * difference - the region length on the wire never changes, only its used extent.
 *
 * A new display is CLONED from a real one already on the watch with the requested layout,
 * rather than synthesised. That guarantees a structurally valid record (correct row count for
 * the layout, correct row numbering, valid field ids) built only from bytes the watch itself
 * produced - the same "use real data, don't invent it" rule the rest of this project follows.
 */

/* Bounds of the Nth USER display inside a mode body. Returns 1 on success. */
static int a1_user_display_bounds(const uint8_t *body, uint32_t len, int keep, int idx,
                                   uint32_t *out_off, uint32_t *out_len) {
    uint32_t p = 0; int cur = -1;
    while (p + 4 <= len) {
        uint16_t tag = rd16(body, p), ln = rd16(body, p + 2);
        if (tag == TAG_DISPLAYS) { p += 4; continue; }
        if (tag == TAG_DISPLAY) {
            cur++;
            if (cur == idx) {
                if (cur >= keep) return 0;          /* inside the built-in tail */
                *out_off = p; *out_len = 4 + ln;
                return 1;
            }
            p += 4 + ln; continue;
        }
        p += 4 + ln;
    }
    return 0;
}

/* Offset of a mode's DISPLAYS container within its body, or 0 if absent. */
static uint32_t a1_displays_container(const uint8_t *body, uint32_t len) {
    uint32_t p = 0;
    while (p + 4 <= len) {
        uint16_t tag = rd16(body, p), ln = rd16(body, p + 2);
        if (tag == TAG_DISPLAYS) return p;
        p += 4 + ln;
    }
    return 0;
}

/* Adds `delta` to the four nested length fields that span a mode body edit. `body_off` is the
 * mode body's absolute offset (its SETTINGS header), `disp_off` the DISPLAYS container's
 * offset within that body. mode_hdr is the MODE TLV header, 4 bytes before body_off. */
static void a1_fix_lengths(uint8_t *buf, uint32_t body_off, uint32_t disp_off, int delta) {
    uint32_t mode_hdr = body_off - 4;
    wr16(buf, (int)(disp_off + body_off + 2), (uint16_t)(rd16(buf, disp_off + body_off + 2) + delta));
    wr16(buf, (int)(mode_hdr + 2),            (uint16_t)(rd16(buf, mode_hdr + 2) + delta));
    wr16(buf, 6,                              (uint16_t)(rd16(buf, 6) + delta));   /* MODES */
    wr16(buf, 2,                              (uint16_t)(rd16(buf, 2) + delta));   /* root  */
}

/* Finds any user display anywhere in the region whose layout matches, to clone. */
static int a1_find_donor(const uint8_t *buf, uint32_t region_len, uint16_t layout,
                          uint32_t *out_off, uint32_t *out_len) {
    uint32_t offs[32], boff[32], blen[32];
    int n = ambit1_find_modes_ex(buf, region_len, offs, boff, blen, 32);
    for (int i = 0; i < n; i++) {
        uint32_t p = 0;
        const uint8_t *body = buf + boff[i];
        while (p + 4 <= blen[i]) {
            uint16_t tag = rd16(body, p), ln = rd16(body, p + 2);
            if (tag == TAG_DISPLAYS) { p += 4; continue; }
            if (tag == TAG_DISPLAY) {
                if (p + 8 <= blen[i] && rd16(body, p + 4) == TAG_LAYOUT
                    && rd16(body, p + 8) == layout) {
                    *out_off = boff[i] + p; *out_len = 4 + ln;
                    return 1;
                }
                p += 4 + ln; continue;
            }
            p += 4 + ln;
        }
    }
    return 0;
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

/* Writes a previously dumped region back verbatim. The safety net for every experiment in
 * this file: any edit can be undone by restoring the dump taken before it. Refuses a size
 * that is not exactly the region length, so a truncated or foreign file cannot be written. */
int ambit1_cmd_restore(ambit_object_t *dev, const ambit_device_info_t *info,
                        const char *path, int dry_run) {
    if (!ambit1_guard_ok(info)) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"not an Ambit1 (product_id 0x0010)\"}\n");
        return 1;
    }
    FILE *f = fopen(path, "rb");
    if (!f) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"cannot open the region file\"}\n");
        return 1;
    }
    static uint8_t img[A1_REGION_MAX];
    size_t nread = fread(img, 1, A1_REGION_MAX, f);
    int extra = fgetc(f) != EOF;
    fclose(f);
    if (nread != A1_REGION_MAX || extra) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"region file must be exactly %d bytes\"}\n",
               A1_REGION_MAX);
        return 1;
    }
    if (rd16(img, 0) != TAG_ROOT) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"file does not start with the root TLV\"}\n");
        return 1;
    }
    int rc = dry_run ? 0 : ambit1_write_region(dev, img, A1_REGION_MAX);
    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"dryRun\": %s, \"bytes\": %d, \"writeRc\": %d}\n",
           rc == 0 ? "true" : "false", dry_run ? "true" : "false", A1_REGION_MAX, rc);
    return rc == 0 ? 0 : 1;
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

/* Patch file: one line per change, `index|field|value`. Unlisted fields keep their current
 * on-watch bytes - that is the whole point of read-modify-write. */
int ambit1_cmd_patch(ambit_object_t *dev, const ambit_device_info_t *info,
                      const char *patch_path, int dry_run, const char *dump_path) {
    if (!ambit1_guard_ok(info)) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"not an Ambit1 (product_id 0x0010) - refusing to "
               "write the 76-byte layout to a different device\"}\n");
        return 1;
    }
    uint8_t buf[A1_REGION_MAX];
    int got = ambit1_read_region(dev, buf, A1_REGION_MAX);
    if (got <= 0) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"region read failed; nothing written\"}\n");
        return 1;
    }
    uint32_t offs[32], boff[32], blen[32];
    int n = ambit1_find_modes_ex(buf, (uint32_t)got, offs, boff, blen, 32);
    if (n == 0) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"no sport modes parsed; nothing written\"}\n");
        return 1;
    }

    /* Same computed system tail as the reader (a1_collect_layouts' comment explains why it
     * is derived, not listed) - display indices in a patch MUST mean the same thing they did
     * in the JSON the UI was drawn from. */
    uint16_t lay[32][64];
    int lay_n[32], keep[32];
    for (int i = 0; i < n; i++) lay_n[i] = a1_collect_layouts(buf + boff[i], blen[i], lay[i], 64);
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
    for (int i = 0; i < n; i++) { keep[i] = lay_n[i] - tail; if (keep[i] < 0) keep[i] = 0; }

    FILE *pf = fopen(patch_path, "r");
    if (!pf) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"cannot open patch file\"}\n");
        return 1;
    }
    char line[256];
    int applied = 0, rejected = 0;
    while (fgets(line, sizeof(line), pf)) {
        char *nl = strchr(line, '\n'); if (nl) *nl = '\0';
        if (!line[0] || line[0] == '#') continue;
        char *save = NULL;
        char *a = strtok_r(line, "|", &save);
        char *b = strtok_r(NULL, "|", &save);
        char *c = strtok_r(NULL, "|", &save);
        if (!a || !b || !c) { rejected++; continue; }
        int idx = atoi(a);
        if (idx < 0 || idx >= n) { rejected++; continue; }
        uint8_t *blob = buf + offs[idx];

        /* `IDX|display-set-type|DISPIDX:LAYOUT` - change an existing display's layout.
         *
         * Done as replace-with-a-clone rather than surgery on the rows: a layout change
         * alters the row COUNT (3-row vs 2-row vs 1-row vs graph), so the record has to be
         * rebuilt anyway. Splicing in a real display of the target layout keeps every byte
         * watch-produced and structurally consistent, exactly like display-add.
         *
         * The row data comes from the donor, i.e. the rows reset to that layout's defaults.
         * That is the same behaviour SportModesPage already models for a layout change
         * (its stagedDisplays() rebuilds the display from displayTypes' own default rows),
         * so the UI and the watch agree on what a setType means. */
        if (strcmp(b, "display-set-type") == 0) {
            int di = -1, layout = -1;
            if (sscanf(c, "%d:%d", &di, &layout) != 2 || di < 0 || layout < 0) {
                rejected++; continue;
            }
            uint32_t disp_c = a1_displays_container(buf + boff[idx], blen[idx]);
            uint32_t doff, dlen;
            if (disp_c == 0
                || !a1_user_display_bounds(buf + boff[idx], blen[idx], keep[idx], di,
                                            &doff, &dlen)) { rejected++; continue; }
            uint32_t soff, slen;
            if (!a1_find_donor(buf, (uint32_t)got, (uint16_t)layout, &soff, &slen)) {
                rejected++; continue;
            }
            uint32_t abs = boff[idx] + doff;
            uint32_t used = 4 + rd16(buf, 2);
            if (used + slen - dlen > (uint32_t)got) { rejected++; continue; }

            uint8_t *clone = malloc(slen);
            if (!clone) { rejected++; continue; }
            memcpy(clone, buf + soff, slen);           /* copy BEFORE anything shifts */
            /* drop the old record, then make room for the new one */
            memmove(buf + abs, buf + abs + dlen, (uint32_t)got - (abs + dlen));
            memset(buf + (uint32_t)got - dlen, 0xff, dlen);
            memmove(buf + abs + slen, buf + abs, (uint32_t)got - (abs + slen));
            memcpy(buf + abs, clone, slen);
            free(clone);
            a1_fix_lengths(buf, boff[idx], disp_c, (int)slen - (int)dlen);

            n = ambit1_find_modes_ex(buf, (uint32_t)got, offs, boff, blen, 32);
            for (int i = 0; i < n; i++)
                lay_n[i] = a1_collect_layouts(buf + boff[i], blen[i], lay[i], 64);
            tail = 0;
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
            for (int i = 0; i < n; i++) { keep[i] = lay_n[i] - tail; if (keep[i] < 0) keep[i] = 0; }
            applied++;
            continue;
        }

        /* `IDX|display-remove|DISPIDX` and `IDX|display-add|LAYOUT` - STRUCTURAL edits.
         * These resize the record, so offsets for every later patch line change; the loop
         * re-derives mode spans and the system tail immediately afterwards. */
        if (strcmp(b, "display-remove") == 0 || strcmp(b, "display-add") == 0) {
            int arg = atoi(c);
            uint32_t disp_c = a1_displays_container(buf + boff[idx], blen[idx]);
            if (disp_c == 0) { rejected++; continue; }

            if (strcmp(b, "display-remove") == 0) {
                uint32_t doff, dlen;
                if (!a1_user_display_bounds(buf + boff[idx], blen[idx], keep[idx], arg,
                                             &doff, &dlen)) { rejected++; continue; }
                if (keep[idx] <= 1) { rejected++; continue; }   /* never leave a mode with none */
                uint32_t abs = boff[idx] + doff;
                memmove(buf + abs, buf + abs + dlen, (uint32_t)got - (abs + dlen));
                memset(buf + (uint32_t)got - dlen, 0xff, dlen);
                a1_fix_lengths(buf, boff[idx], disp_c, -(int)dlen);
            } else {
                if (keep[idx] >= A1_MAX_DISPLAYS) { rejected++; continue; }
                uint32_t soff, slen;
                if (!a1_find_donor(buf, (uint32_t)got, (uint16_t)arg, &soff, &slen)) {
                    rejected++; continue;               /* no real display of that layout to clone */
                }
                /* insert after the LAST user display, i.e. before the built-in tail */
                uint32_t last_off, last_len;
                if (!a1_user_display_bounds(buf + boff[idx], blen[idx], keep[idx],
                                             keep[idx] - 1, &last_off, &last_len)) {
                    rejected++; continue;
                }
                uint32_t abs = boff[idx] + last_off + last_len;
                /* the region must still fit once everything shifts right */
                uint32_t used = 4 + rd16(buf, 2);
                if (used + slen > (uint32_t)got) { rejected++; continue; }
                uint8_t *clone = malloc(slen);
                if (!clone) { rejected++; continue; }
                memcpy(clone, buf + soff, slen);        /* copy BEFORE shifting */
                memmove(buf + abs + slen, buf + abs, (uint32_t)got - (abs + slen));
                memcpy(buf + abs, clone, slen);
                free(clone);
                a1_fix_lengths(buf, boff[idx], disp_c, (int)slen);
            }

            /* re-derive spans + tail: the region just changed shape */
            n = ambit1_find_modes_ex(buf, (uint32_t)got, offs, boff, blen, 32);
            for (int i = 0; i < n; i++)
                lay_n[i] = a1_collect_layouts(buf + boff[i], blen[i], lay[i], 64);
            tail = 0;
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
            for (int i = 0; i < n; i++) { keep[i] = lay_n[i] - tail; if (keep[i] < 0) keep[i] = 0; }
            applied++;
            continue;
        }

        /* `IDX|row|DISP:ROW:VALUE:FIELDID` - change WHICH data a display row shows.
         * Fixed-size: patches the ROW's own item, or the VIEW at VALUE for a multi-value
         * row. Display indices count user displays only, matching the reader's output. */
        if (strcmp(b, "row") == 0) {
            int di = -1, ri = -1, vi = -1, field = -1;
            if (sscanf(c, "%d:%d:%d:%d", &di, &ri, &vi, &field) != 4
                || di < 0 || ri < 0 || vi < 0 || field < 0 || field > 65535) {
                rejected++; continue;
            }
            uint32_t at = a1_find_row_value(buf + boff[idx], blen[idx], keep[idx], di, ri, vi);
            if (at == 0) { rejected++; continue; }
            wr16(buf + boff[idx], (int)at, (uint16_t)field);
            applied++;
            continue;
        }

        if (strcmp(b, "name") == 0) {
            memset(blob + OFF_NAME, 0, 16);
            strncpy((char *)blob + OFF_NAME, c, 15);
            applied++;
            continue;
        }
        long v = strtol(c, NULL, 10);
        if (v < 0 || v > 65535) { rejected++; continue; }
        int off = -1;
        if      (strcmp(b, "activityId") == 0)          off = OFF_ACTIVITY_ID;
        else if (strcmp(b, "pods") == 0)                off = OFF_PODS;
        else if (strcmp(b, "altiBaroMode") == 0)        off = OFF_ALTI_BARO;
        else if (strcmp(b, "gpsInterval") == 0)         off = OFF_GPS_INTERVAL;
        else if (strcmp(b, "recordingInterval") == 0)   off = OFF_REC_INTERVAL;
        else if (strcmp(b, "autolapM") == 0)            off = OFF_AUTOLAP;
        else if (strcmp(b, "hrMax") == 0)               off = OFF_HR_MAX;
        else if (strcmp(b, "hrMin") == 0)               off = OFF_HR_MIN;
        else if (strcmp(b, "autoPause") == 0)           off = OFF_AUTO_PAUSE;
        else if (strcmp(b, "useIntervalTimer") == 0)    off = OFF_USE_INTERVAL;
        else if (strcmp(b, "intervalRepetitions") == 0) off = OFF_INTERVAL_REPS;
        else if (strcmp(b, "intervalMaxUnit") == 0)     off = OFF_IT_MAX_UNIT;
        else if (strcmp(b, "intervalMax") == 0)         off = OFF_IT_MAX;
        else if (strcmp(b, "intervalMinUnit") == 0)     off = OFF_IT_MIN_UNIT;
        else if (strcmp(b, "intervalMin") == 0)         off = OFF_IT_MIN;
        if (off < 0) { rejected++; continue; }
        wr16(blob, off, (uint16_t)v);
        applied++;
    }
    fclose(pf);

    /* Always offer the exact bytes that would be sent, so a dry run can be diffed offline
     * against the pre-write dump (and against SuuntoLink's own captured writes). */
    if (dump_path) {
        FILE *df = fopen(dump_path, "wb");
        if (df) { fwrite(buf, 1, (size_t)got, df); fclose(df); }
    }

    int rc = 0;
    if (!dry_run) rc = ambit1_write_region(dev, buf, (uint32_t)got);

    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": %s, \"dryRun\": %s, \"applied\": %d, \"rejected\": %d, "
           "\"modeCount\": %d, \"regionBytes\": %d, \"writeRc\": %d}\n",
           (rc == 0) ? "true" : "false", dry_run ? "true" : "false",
           applied, rejected, n, got, rc);
    return rc == 0 ? 0 : 1;
}

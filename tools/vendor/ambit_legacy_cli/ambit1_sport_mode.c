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

#define TAG_ROOT        0x0003
#define TAG_MODES       0x0100
#define TAG_MODE        0x0101
#define TAG_SETTINGS    0x0102

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

static uint16_t rd16(const uint8_t *p, int off) {
    return (uint16_t)(p[off] | (p[off + 1] << 8));
}
static void wr16(uint8_t *p, int off, uint16_t v) {
    p[off] = v & 0xff;
    p[off + 1] = (v >> 8) & 0xff;
}

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

/* Walks the TLV and records where each mode's settings blob starts. Returns mode count. */
int ambit1_find_modes(const uint8_t *buf, uint32_t len, uint32_t *offsets, int max_modes) {
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
        "\"intervalMin\": %u}",
        rd16(b, OFF_ACTIVITY_ID), rd16(b, OFF_PODS), rd16(b, OFF_ALTI_BARO),
        rd16(b, OFF_GPS_INTERVAL), rd16(b, OFF_REC_INTERVAL), rd16(b, OFF_AUTOLAP),
        rd16(b, OFF_HR_MAX), rd16(b, OFF_HR_MIN), rd16(b, OFF_AUTO_PAUSE),
        rd16(b, OFF_USE_INTERVAL), rd16(b, OFF_INTERVAL_REPS), rd16(b, OFF_IT_MAX_UNIT),
        rd16(b, OFF_IT_MAX), rd16(b, OFF_IT_MIN_UNIT), rd16(b, OFF_IT_MIN));
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
    uint32_t offs[32];
    int n = ambit1_find_modes(buf, (uint32_t)got, offs, 32);

    fputs("@@JSON@@\n", stdout);
    printf("{\"ok\": true, \"source\": \"watch\", \"modeCount\": %d, \"modes\": [\n", n);
    for (int i = 0; i < n; i++) {
        printf("    ");
        print_mode_json(stdout, buf + offs[i]);
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
    uint32_t offs[32];
    int n = ambit1_find_modes(buf, (uint32_t)got, offs, 32);
    if (n == 0) {
        fputs("@@JSON@@\n", stdout);
        printf("{\"ok\": false, \"error\": \"no sport modes parsed; nothing written\"}\n");
        return 1;
    }

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

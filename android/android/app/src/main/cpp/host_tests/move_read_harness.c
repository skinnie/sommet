/*
 * Host test harness for the Android/iOS move download (GitHub #26, 2026-11-26).
 *
 * Runs the app's own Ambit3 driver (libambit/device_driver_ambit3.c, included below so its
 * static init() and log_read() are reachable) against a fake watch that answers from files:
 *   0x0b02 / 0x0b21  a memory map naming only ExerciseLog (the dump's base and size),
 *   0x1200           the move list, in pages: either built from a listing (one line per move,
 *                    in the watch's own header-record layout, as parse_log_entry() reads it) or
 *                    real pages captured from SuuntoLink (request cursor -> reply bytes),
 *   0x0b17           flash pieces from a saved ExerciseLog dump.
 * Any reply can be made to fail a number of times (a damaged or lost reply over Bluetooth),
 * to check the asking-again of pages and pieces and that a read that cannot complete ends
 * with an error instead of a hang or a crash. Driven by tools/test_android_move_read.py,
 * which compares the moves with the desktop's own reader (tools/exercise_log.py).
 *
 * Built only on the host (gcc); not part of the app (the app's CMake globs libambit/*.c).
 * libambit is openambit's (GPL-3.0-or-later, Emil Ljungdahl and contributors); this file is
 * under the same licence.
 */
#ifndef DRIVER_SOURCE                      /* another copy can be given, to compare */
#define DRIVER_SOURCE "../libambit/device_driver_ambit3.c"
#endif
#include DRIVER_SOURCE

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* ---- what the app links from files this harness leaves out (USB, Bluetooth) ---- */
int hid_set_nonblocking(hid_device *d, int n) { (void)d; (void)n; return 0; }
void hid_close(hid_device *d) { (void)d; }
struct hid_device_info *hid_enumerate(unsigned short v, unsigned short p) { (void)v; (void)p; return NULL; }
void hid_free_enumeration(struct hid_device_info *d) { (void)d; }
hid_device *hid_open_path(const char *p) { (void)p; return NULL; }
void libambit_ble_transport_close(ambit_object_t *o) { (void)o; }

/* ---- the fake watch ---- */
static uint8_t *g_mem;            /* the dump, padded with 0xff to the region size */
static uint32_t g_base, g_size;
static long g_piece_reads, g_piece_bytes, g_page_requests;

#define MAX_FAILS 16
static struct { uint32_t addr; int times; } g_piece_fail[MAX_FAILS];
static int g_n_piece_fail;
static struct { int page; int times; } g_page_fail[MAX_FAILS];
static int g_n_page_fail;

typedef struct { uint8_t cursor[4]; uint8_t *reply; size_t len; } page_t;
static page_t *g_pages;
static int g_n_pages;

typedef struct { uint32_t a1, e1, a2, e2; int synced, known; char when[24]; } move_t;
static move_t *g_moves;
static int g_n_moves;
static int g_all_known;

static uint8_t *dup_bytes(const uint8_t *p, size_t n)
{
    uint8_t *d = malloc(n ? n : 1);
    memcpy(d, p, n);
    return d;
}

/* One SBEM element: id, then a 1-byte length, or 0xff and a 4-byte one past 254 (the rule
 * libambit_sbem0102_data_ptr() reads with). */
static size_t put_element(uint8_t *out, uint8_t id, const uint8_t *data, uint32_t len)
{
    size_t o = 0;
    out[o++] = id;
    if (len > 254) {
        out[o++] = 0xff;
        out[o++] = len & 0xff; out[o++] = (len >> 8) & 0xff;
        out[o++] = (len >> 16) & 0xff; out[o++] = (len >> 24) & 0xff;
    } else {
        out[o++] = (uint8_t)len;
    }
    memcpy(out + o, data, len);
    return o + len;
}

static size_t put32(uint8_t *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; return 4; }

/* One move in the header-record layout parse_log_entry() reads (field by field, same order). */
static size_t put_record(uint8_t *out, const move_t *m, uint8_t tail_len)
{
    size_t o = 0;
    memcpy(out + o, m->when, 19); o += 19;
    out[o++] = 0;                                  /* the end of the time text */
    out[o++] = (uint8_t)m->synced;
    o += put32(out + o, m->a1); o += put32(out + o, m->e1);
    o += put32(out + o, m->a2); o += put32(out + o, m->e2);
    memset(out + o, 0, 3 + 8 + 2 + 8 + 4 + 8 + 2 + 4 + 4 + 4 + 4 + 4 + 4 + 8 + 2 + 1);
    o += 3 + 8 + 2 + 8 + 4 + 8 + 2 + 4 + 4 + 4 + 4 + 4 + 4 + 8 + 2 + 1;
    memcpy(out + o, "Move\0", 5); o += 5;          /* activity name (16 bytes are converted) */
    memset(out + o, 0, 4 + 2 + tail_len + 16);     /* distance, energy, tail, and room for it */
    o += 4 + 2 + tail_len;
    return o;
}

/* The move list as the watch pages it: page 0 with SBEM0102, the totals, then headers; later
 * pages with the cursor and headers only; the 5th byte 0x01 on the last page (as captured). */
static void build_pages(ambit_object_t *obj, int per_page)
{
    ambit3_driver_params_t *dp = &obj->driver_data->driver_params;
    int pages = (g_n_moves + per_page - 1) / per_page;
    if (pages == 0) pages = 1;
    g_pages = calloc(pages, sizeof(page_t));
    g_n_pages = pages;
    int notsynced = 0;
    for (int i = 0; i < g_n_moves; i++) notsynced += !g_moves[i].synced;
    for (int k = 0; k < pages; k++) {
        size_t cap = 64 + (size_t)per_page * (256 + dp->log_header_tail_length);
        uint8_t *block = calloc(1, cap), *reply = calloc(1, cap + 64);
        size_t bl = 0;
        for (int i = k * per_page; i < g_n_moves && i < (k + 1) * per_page; i++)
            bl += put_record(block + bl, &g_moves[i], dp->log_header_tail_length);
        size_t o = 0;
        int last = (k == pages - 1);
        if (k > 0) { reply[0] = 0x02; reply[1] = (uint8_t)k; }       /* this page's cursor */
        memcpy(g_pages[k].cursor, reply, 4);
        /* the reply carries the cursor of the next page (or zeros on the last) */
        reply[0] = last ? 0 : 0x02; reply[1] = last ? 0 : (uint8_t)(k + 1); reply[2] = 0; reply[3] = 0;
        reply[4] = last ? 0x01 : 0x00; reply[5] = 0;
        o = 6;
        if (k == 0) {
            memcpy(reply + o, "SBEM0102", 8); o += 8;
            uint8_t v[2] = { (uint8_t)g_n_moves, (uint8_t)(g_n_moves >> 8) };
            o += put_element(reply + o, dp->log_entries_total_data_id, v, 2);
            uint8_t w[2] = { (uint8_t)notsynced, (uint8_t)(notsynced >> 8) };
            o += put_element(reply + o, dp->log_entries_notsynced_data_id, w, 2);
        }
        if (bl) o += put_element(reply + o, dp->log_header_data_id, block, bl);
        g_pages[k].reply = reply;
        g_pages[k].len = o;
        free(block);
    }
}

static int fail_now_page(int page)
{
    for (int i = 0; i < g_n_page_fail; i++)
        if (g_page_fail[i].page == page && g_page_fail[i].times > 0) { g_page_fail[i].times--; return 1; }
    return 0;
}

static int fail_now_piece(uint32_t addr)
{
    for (int i = 0; i < g_n_piece_fail; i++)
        if (g_piece_fail[i].addr == addr && g_piece_fail[i].times > 0) { g_piece_fail[i].times--; return 1; }
    return 0;
}

static uint8_t g_mm_entry_id;

int libambit_protocol_command(ambit_object_t *object, uint16_t command, uint8_t *data, size_t datalen,
                              uint8_t **reply_data, size_t *replylen, uint8_t legacy_format)
{
    (void)object; (void)legacy_format;
    if (command == ambit_command_waypoint_count) {           /* memory map key */
        uint8_t r[4] = {0};
        *reply_data = dup_bytes(r, 4); *replylen = 4;
        return 0;
    }
    if (command == ambit_command_ambit3_memory_map) {
        uint8_t body[128], r[192];
        size_t b = 0, o = 0;
        memcpy(body + b, "ExerciseLog", 12); b += 12;
        memset(body + b, '0', 64); b += 64; body[b++] = 0;     /* hash text */
        b += put32(body + b, g_base); b += put32(body + b, g_size);
        memcpy(r, "\0\0\0\0\x01\0SBEM0102", 14); o = 14;
        o += put_element(r + o, g_mm_entry_id, body, (uint32_t)b);
        *reply_data = dup_bytes(r, o); *replylen = o;
        return 0;
    }
    if (command == ambit_command_ambit3_log_headers) {
        g_page_requests++;
        if (datalen < 4) return -1;
        for (int k = 0; k < g_n_pages; k++) {
            if (memcmp(g_pages[k].cursor, data, 4) == 0) {
                if (fail_now_page(k)) return -1;              /* damaged or lost */
                *reply_data = dup_bytes(g_pages[k].reply, g_pages[k].len);
                *replylen = g_pages[k].len;
                return 0;
            }
        }
        printf("R no page for cursor %02x%02x%02x%02x\n", data[0], data[1], data[2], data[3]);
        return -1;
    }
    if (command == ambit_command_log_read) {
        if (datalen < 8) return -1;
        uint32_t a = data[0] | data[1] << 8 | data[2] << 16 | (uint32_t)data[3] << 24;
        uint32_t n = data[4] | data[5] << 8 | data[6] << 16 | (uint32_t)data[7] << 24;
        g_piece_reads++;
        if (a < g_base || a + n > g_base + g_size) return -1;
        if (fail_now_piece(a)) return -1;
        uint8_t *r = malloc(8 + n);
        memcpy(r, data, 8);
        memcpy(r + 8, g_mem + (a - g_base), n);
        g_piece_bytes += n;
        *reply_data = r; *replylen = 8 + n;
        return 0;
    }
    printf("R unexpected command 0x%04x\n", command);
    return -1;
}

void libambit_protocol_free(uint8_t *data) { free(data); }

/* ---- what the app does with each move ---- */
static void when_text(const ambit_date_time_t *t, char *out)
{
    sprintf(out, "%04u-%02u-%02uT%02u:%02u:%02u", t->year, t->month, t->day, t->hour, t->minute,
            t->msec / 1000);
}

static int skip_cb(void *ref, ambit_log_header_t *h)
{
    (void)ref;
    char w[24];
    when_text(&h->date_time, w);
    if (g_all_known) return 0;
    for (int i = 0; i < g_n_moves; i++)
        if (strcmp(g_moves[i].when, w) == 0) return g_moves[i].known ? 0 : 1;
    return 1;                                     /* 0 = already here, skip; 1 = read it */
}

static void push_cb(void *ref, ambit_log_entry_t *e)
{
    (void)ref;
    uint32_t timesum = 0;
    for (uint32_t i = 0; i < e->samples_count; i++) timesum += e->samples[i].time;
    printf("R pushed %04u %u %u %u %u %u dur %u dist %u samples %u timesum %u\n",
           e->header.date_time.year, e->header.date_time.month, e->header.date_time.day,
           e->header.date_time.hour, e->header.date_time.minute, e->header.date_time.msec,
           e->header.duration, e->header.distance, e->samples_count, timesum);
}

static int g_walked;
static void progress_cb(void *ref, uint16_t total, uint16_t done, uint8_t pct)
{
    (void)ref; (void)total; (void)pct;
    g_walked = (int)done;
}

static void read_listing(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) { perror("listing"); exit(2); }
    char line[256];
    int cap = 0;
    while (fgets(line, sizeof line, f)) {
        if (g_n_moves == cap) { cap = cap ? cap * 2 : 64; g_moves = realloc(g_moves, cap * sizeof(move_t)); }
        move_t *m = &g_moves[g_n_moves];
        if (sscanf(line, "%x %x %x %x %d %d %23s", &m->a1, &m->e1, &m->a2, &m->e2, &m->synced,
                   &m->known, m->when) == 7)
            g_n_moves++;
    }
    fclose(f);
}

static void read_pages(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) { perror("pages"); exit(2); }
    static char line[1 << 16];
    int cap = 0;
    while (fgets(line, sizeof line, f)) {
        char *sp = strchr(line, ' ');
        if (!sp) continue;
        *sp = 0;
        char *hex = sp + 1;
        size_t n = strspn(hex, "0123456789abcdef") / 2;
        if (g_n_pages == cap) { cap = cap ? cap * 2 : 32; g_pages = realloc(g_pages, cap * sizeof(page_t)); }
        page_t *p = &g_pages[g_n_pages++];
        for (int i = 0; i < 4; i++) { unsigned v; sscanf(line + 2 * i, "%2x", &v); p->cursor[i] = (uint8_t)v; }
        p->reply = malloc(n ? n : 1);
        p->len = n;
        for (size_t i = 0; i < n; i++) { unsigned v; sscanf(hex + 2 * i, "%2x", &v); p->reply[i] = (uint8_t)v; }
    }
    fclose(f);
}

int main(int argc, char **argv)
{
    const char *dump = NULL, *listing = NULL, *pages = NULL;
    unsigned product = 0x1b, fw[3] = {2, 4, 17};
    int per_page = 6;
    setvbuf(stdout, NULL, _IOLBF, 0);
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--dump")) dump = argv[++i];
        else if (!strcmp(argv[i], "--base")) g_base = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--size")) g_size = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--product")) product = (unsigned)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--fw")) sscanf(argv[++i], "%u.%u.%u", &fw[0], &fw[1], &fw[2]);
        else if (!strcmp(argv[i], "--listing")) listing = argv[++i];
        else if (!strcmp(argv[i], "--pages")) pages = argv[++i];
        else if (!strcmp(argv[i], "--per-page")) per_page = atoi(argv[++i]);
        else if (!strcmp(argv[i], "--all-known")) g_all_known = 1;
        else if (!strcmp(argv[i], "--fail-piece") && g_n_piece_fail < MAX_FAILS) {
            unsigned a; int t;
            sscanf(argv[++i], "%x:%d", &a, &t);
            g_piece_fail[g_n_piece_fail].addr = a; g_piece_fail[g_n_piece_fail++].times = t;
        }
        else if (!strcmp(argv[i], "--fail-page") && g_n_page_fail < MAX_FAILS) {
            int k, t;
            sscanf(argv[++i], "%d:%d", &k, &t);
            g_page_fail[g_n_page_fail].page = k; g_page_fail[g_n_page_fail++].times = t;
        }
        else { fprintf(stderr, "unknown argument %s\n", argv[i]); return 2; }
    }
    if (!g_size) g_size = 0x100000;
    if (!g_base) g_base = 0x100000;
    g_mem = malloc(g_size);
    memset(g_mem, 0xff, g_size);
    if (dump) {
        FILE *f = fopen(dump, "rb");
        if (!f) { perror("dump"); return 2; }
        size_t got = fread(g_mem, 1, g_size, f);
        (void)got;
        fclose(f);
    }
    if (listing) read_listing(listing);
    if (pages) read_pages(pages);

    ambit_object_t obj;
    memset(&obj, 0, sizeof obj);
    obj.device_info.product_id = (uint16_t)product;
    obj.device_info.fw_version[0] = (uint8_t)fw[0];
    obj.device_info.fw_version[1] = (uint8_t)fw[1];
    obj.device_info.fw_version[2] = (uint8_t)fw[2];
    obj.driver = &ambit_device_driver_ambit3;
    init(&obj, 0x0400);
    g_mm_entry_id = obj.driver_data->driver_params.mm_entry_data_id;
    if (!pages) build_pages(&obj, per_page);

    int ret = log_read(&obj, skip_cb, push_cb, progress_cb, NULL);
    printf("R result %d walked %d piece_reads %ld piece_bytes %ld page_requests %ld\n",
           ret, g_walked, g_piece_reads, g_piece_bytes, g_page_requests);

    deinit(&obj);
    for (int k = 0; k < g_n_pages; k++) free(g_pages[k].reply);
    free(g_pages);
    free(g_moves);
    free(g_mem);
    return 0;
}

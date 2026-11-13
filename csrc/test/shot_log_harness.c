/* Host harness: the Android app's own log parser (libambit pmem20.c) on a saved ExerciseLog
 * region, with the transport replaced by the file. Prints the shot count of each move's header
 * and each shot sample, for tools/test_alpha_shot_log.py to compare with the Python reader.
 *
 *   shot_log_harness <region file> <mem_start hex> <mem_size>
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "libambit.h"
#include "libambit_int.h"
#include "pmem20.h"
#include "protocol.h"

static uint8_t *g_region;
static size_t g_region_len;
static uint32_t g_mem_start;

int libambit_protocol_command(ambit_object_t *object, uint16_t command, uint8_t *data,
                              size_t datalen, uint8_t **reply_data, size_t *replylen,
                              uint8_t legacy_format)
{
    (void)object; (void)legacy_format;
    if (command != ambit_command_log_read || datalen != 8) return -1;
    uint32_t address, length;
    memcpy(&address, data, 4);
    memcpy(&length, data + 4, 4);
    uint8_t *reply = calloc(1, length + 8);
    if (reply == NULL) return -1;
    memcpy(reply, data, 8);
    if (address >= g_mem_start && address - g_mem_start < g_region_len) {
        size_t off = address - g_mem_start;
        size_t n = g_region_len - off < length ? g_region_len - off : length;
        memcpy(reply + 8, g_region + off, n);
    }
    *reply_data = reply;
    *replylen = length + 8;
    return 0;
}

void libambit_protocol_free(uint8_t *data) { free(data); }

int main(int argc, char **argv)
{
    if (argc != 4) return 2;
    FILE *f = fopen(argv[1], "rb");
    if (f == NULL) return 2;
    g_region = malloc(8 << 20);
    g_region_len = fread(g_region, 1, 8 << 20, f);
    fclose(f);
    g_mem_start = (uint32_t)strtoul(argv[2], NULL, 16);

    libambit_pmem20_t pmem;
    memset(&pmem, 0, sizeof(pmem));
    libambit_pmem20_init(&pmem, NULL, 1024);
    if (libambit_pmem20_log_init(&pmem, g_mem_start, (uint32_t)strtoul(argv[3], NULL, 10)) != 0) {
        fprintf(stderr, "log init failed\n");
        return 1;
    }
    ambit_log_header_t header;
    memset(&header, 0, sizeof(header));
    while (libambit_pmem20_log_next_header(&pmem, &header, LIBAMBIT_PMEM20_FLAGS_UNKNOWN2_PADDING_48) == 1) {
        ambit_log_entry_t *entry = libambit_pmem20_log_read_entry(&pmem, LIBAMBIT_PMEM20_FLAGS_UNKNOWN2_PADDING_48);
        if (entry == NULL) return 1;
        printf("move %s has_shots=%d shots=%d\n", entry->header.activity_name,
               entry->header.has_shots, entry->header.shots);
        for (uint32_t i = 0; i < entry->samples_count; i++) {
            const ambit_log_sample_t *s = &entry->samples[i];
            if (s->type != ambit_log_sample_type_shot) continue;
            if (s->u.shot.has_position)
                printf("shot time_ms=%u lat=%d lon=%d\n", s->time, s->u.shot.latitude, s->u.shot.longitude);
            else
                printf("shot time_ms=%u no position\n", s->time);
        }
    }
    return 0;
}

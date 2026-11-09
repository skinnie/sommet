/*
 * (C) Copyright 2014 Emil Ljungdahl
 *
 * This file is part of libambit.
 *
 * libambit is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 *
 * Contributors:
 *
 */
#include "device_driver.h"
#include "device_driver_common.h"
#include "device_support.h"
#include "libambit_int.h"
#include "protocol.h"
#include "pmem20.h"
#include "personal.h"
#include "sbem0102.h"
#include "sha256.h"
#include "utils.h"
#include "debug.h"
#include "device_driver_ambit3_navigation.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <time.h>
#include <math.h>

/*
 * Local definitions
 */

typedef struct memory_map_entry_s {
    uint32_t start;
    uint32_t size;
    uint8_t hash[32];
} memory_map_entry_t;

enum ambit3_fw_gen {
    AMBIT3_FW_GEN1,
    AMBIT3_FW_GEN2,
    AMBIT3_FW_GEN3,
    AMBIT3_FW_GEN4,
    AMBIT3_VERT_FW_GEN1,
    AMBIT3_VERT_FW_GEN2,
    AMBIT3_VERT_FW_GEN3,
    TRAVERSE_FW_GEN1,
    TRAVERSE_FW_GEN2,
};

typedef struct ambit3_driver_params_s {
    uint8_t mm_legacy_format;
    uint8_t mm_entry_data_id;
    uint8_t log_header_request_data_id;
    uint8_t log_entries_total_data_id;
    uint8_t log_entries_notsynced_data_id;
    uint8_t log_header_data_id;
    uint8_t log_header_tail_length;
    uint8_t log_synced_data_id;
} ambit3_driver_params_t;

struct ambit_device_driver_data_s {
    libambit_pmem20_t pmem20;
    libambit_sbem0102_t sbem0102;
    struct {
        uint8_t initialized;
        memory_map_entry_t waypoints;
        memory_map_entry_t routes;
        memory_map_entry_t rules;
        memory_map_entry_t gps;
        memory_map_entry_t sport_modes;
        memory_map_entry_t training_program;
        memory_map_entry_t exercise_log;
        memory_map_entry_t event_log;
        memory_map_entry_t ble_pairing;
        memory_map_entry_t apps;
        memory_map_entry_t glonass; // Ambit3 Vertical / Traverse
        memory_map_entry_t track_log; // Traverse
    } memory_maps;
    enum ambit3_fw_gen fw_gen;
    ambit3_driver_params_t driver_params;
};

typedef struct ambit3_log_header_s {
    ambit_log_header_t header;
    uint32_t address;
    uint32_t end_address;
    uint32_t address2;
    uint32_t end_address2;
    uint8_t synced;
} ambit3_log_header_t;

/*
 * Static functions
 */
static void init(ambit_object_t *object, uint32_t driver_param);
static void deinit(ambit_object_t *object);
static int personal_settings_get(ambit_object_t *object, ambit_personal_settings_t *settings);
static int log_read(ambit_object_t *object, ambit_log_skip_cb skip_cb, ambit_log_push_cb push_cb, ambit_log_progress_cb progress_cb, void *userref);
static int gps_orbit_header_read(ambit_object_t *object, uint8_t data[8]);
static int gps_orbit_write(ambit_object_t *object, uint8_t *data, size_t datalen);

static int parse_log_header_block(ambit_object_t *object, libambit_sbem0102_data_t *reply_data_object, ambit_log_skip_cb skip_cb, ambit_log_push_cb push_cb, ambit_log_progress_cb progress_cb, void *userref, uint16_t *log_entries_walked, uint16_t log_entries_total);
static size_t parse_log_entry(ambit_object_t *object, const uint8_t *log_data, ambit3_log_header_t *log_header);
static int get_memory_maps(ambit_object_t *object);
static int log_synced(ambit_object_t *object, ambit_log_entry_t *log_entry);
static int date_time_set(ambit_object_t *object, struct tm *tm);

/*
 * Global variables
 */
ambit_device_driver_t ambit_device_driver_ambit3 = {
    init,
    deinit,
    libambit_device_driver_lock_log,
    date_time_set,
    libambit_device_driver_status_get,
    personal_settings_get,
    log_read,
    gps_orbit_header_read,
    gps_orbit_write,
    NULL, // navigation_read
    NULL, // navigation_write
    NULL, // sport_mode_write
    NULL, // app_data_write
    log_synced
};


/*
 * Static functions implementation
 */

/**
 * Performs a lookup of the ambit3 firmware generation for the watch product id and firmware version.
 *
 * \param device_info Device info structure of the watch.
 * \return Enumeration of the watch firmware generation.
 */
static enum ambit3_fw_gen get_ambit3_fw_gen(ambit_device_info_t *device_info)
{
    struct generation {
        uint8_t fw_version[4];
        enum ambit3_fw_gen gen;
    };

    struct generation ambit3_gen[] =  {
        {{2, 4, 1, 0}, AMBIT3_FW_GEN4},
        {{2, 2, 16, 0}, AMBIT3_FW_GEN3},
        {{2, 0, 4, 0}, AMBIT3_FW_GEN2},
        {{0, 0, 0, 0}, AMBIT3_FW_GEN1},
    };

    struct generation ambit3_vert_gen[] = {
        {{1, 1, 22, 0}, AMBIT3_VERT_FW_GEN3},
        {{1, 0, 27, 0}, AMBIT3_VERT_FW_GEN2},
        {{1, 0, 0, 0}, AMBIT3_VERT_FW_GEN1},
    };

    struct generation traverse_gen[] = {
        {{2, 0, 18, 0}, TRAVERSE_FW_GEN2},
        {{1, 0, 4, 0}, TRAVERSE_FW_GEN1},
    };

    struct generation* iter;

    switch (device_info->product_id) {
      case 0x1e:
      case 0x1c:
      case 0x1b:
      case 0x2a: // Kailash ("Hoopoe") — same generation table already used, and
                 // live-validated, over BLE where guessProductId() maps it to 0x1c
        for (size_t i = 0; i < sizeof (ambit3_gen) / sizeof ((ambit3_gen)[0]) && (iter = &ambit3_gen[i]); i++) {
            if (libambit_fw_version_number(iter->fw_version) <= libambit_fw_version_number(device_info->fw_version))
                return iter->gen;
        }
        break;
      case 0x2c: // Ambit3 Vertical
        for (size_t i = 0; i < sizeof (ambit3_vert_gen) / sizeof ((ambit3_vert_gen)[0]) && (iter = &ambit3_vert_gen[i]); i++) {
            if (libambit_fw_version_number(iter->fw_version) <= libambit_fw_version_number(device_info->fw_version))
                return iter->gen;
        }
        break;
      case 0x2b: // Traverse
      case 0x2d: // Traverse Alpha
        for (size_t i = 0; i < sizeof (traverse_gen) / sizeof ((traverse_gen)[0]) && (iter = &traverse_gen[i]); i++) {
            if (libambit_fw_version_number(iter->fw_version) <= libambit_fw_version_number(device_info->fw_version))
                return iter->gen;
        }
        break;
    }

    // No generation matched. This happens for the BSL bootloader, which reports fw 1.0.0.0 -
    // OLDER than every table entry - so init() during a firmware re-enumeration reached here
    // and abort()ed (real crash, 2026-08-15, flashing a Traverse in BSL). The fw_gen only
    // selects driver params for normal sync; the firmware flasher sends raw opcodes and never
    // uses them, so fall back to the oldest generation of the watch's own family instead of
    // crashing - same "don't abort, use the safe fallback" fix the Kailash 0x2a case got.
    LOG_WARNING("get_ambit3_fw_gen: no generation matched product 0x%x fw %d.%d.%d.%d - "
                "defaulting to the family's oldest generation (BSL/too-old firmware)",
                device_info->product_id,
                device_info->fw_version[0], device_info->fw_version[1],
                device_info->fw_version[2], device_info->fw_version[3]);
    switch (device_info->product_id) {
      case 0x2c: return AMBIT3_VERT_FW_GEN1;
      case 0x2b:
      case 0x2d: return TRAVERSE_FW_GEN1;
      default:   return AMBIT3_FW_GEN1;
    }
}

/**
 * Performs a lookup of the ambit3 driver parameters for the watch firmware generation.
 *
 * \param fw_gen Firmware version of the watch.
 * \return ambit3_driver_params_t structure containing the driver parameters.
 */
static ambit3_driver_params_t get_ambit3_driver_params(enum ambit3_fw_gen fw_gen) {

    struct ambit3_driver_params_lookup {
        enum ambit3_fw_gen gen;
        ambit3_driver_params_t driver_params;
    };

    struct ambit3_driver_params_lookup dp_lookup[] =  {
            {AMBIT3_FW_GEN1,        {0x02, 0x3f, 0x81, 0x4e, 0x4f, 0x7e, 0x00, 0x00}},
            {AMBIT3_FW_GEN2,        {0x00, 0x4b, 0x8d, 0x5a, 0x5b, 0x8a, 0x1a, 0x00}},
            {AMBIT3_FW_GEN3,        {0x00, 0x4a, 0x8c, 0x59, 0x5a, 0x89, 0x1a, 0x00}},
            {AMBIT3_FW_GEN4,        {0x00, 0x4a, 0x8d, 0x59, 0x5a, 0x8a, 0x1c, 0x8b}},
            {AMBIT3_VERT_FW_GEN1,   {0x00, 0x4a, 0x8c, 0x59, 0x5a, 0x8a, 0x1c, 0x00}},
            {AMBIT3_VERT_FW_GEN2,   {0x00, 0x4a, 0x8b, 0x58, 0x59, 0x89, 0x1c, 0x00}},
            {AMBIT3_VERT_FW_GEN3,   {0x00, 0x4a, 0x8a, 0x57, 0x58, 0x88, 0x1c, 0x00}},
            {TRAVERSE_FW_GEN1,      {0x00, 0x4a, 0x8a, 0x56, 0x57, 0x87, 0x1c, 0x00}},
            {TRAVERSE_FW_GEN2,      {0x00, 0x4a, 0x88, 0x55, 0x56, 0x86, 0x1c, 0x00}},
    };

    ambit3_driver_params_t params;

    struct ambit3_driver_params_lookup* iter;

    for (size_t i = 0; i < sizeof (dp_lookup) / sizeof ((dp_lookup)[0]) && (iter = &dp_lookup[i]); i++) {
        if (iter->gen == fw_gen) {
            params = iter->driver_params;
            break;
        }
    }

    return params;
}

/**
 * Initialises the ambit3 driver.
 *
 * \param object
 * \param PMEM20 chunk size.
 */
static void init(ambit_object_t *object, uint32_t driver_param)
{
    struct ambit_device_driver_data_s *data;

    LOG_INFO("kailash-debug: ambit3 driver init() entered, driver_param=0x%x", driver_param);
    if ((data = calloc(1, sizeof(struct ambit_device_driver_data_s))) != NULL) {
        object->driver_data = data;
        LOG_INFO("kailash-debug: about to pmem20_init");
        libambit_pmem20_init(&object->driver_data->pmem20, object, driver_param);
        LOG_INFO("kailash-debug: about to sbem0102_init");
        libambit_sbem0102_init(&object->driver_data->sbem0102, object, driver_param);
        LOG_INFO("kailash-debug: about to get_ambit3_fw_gen, fw_version=%d.%d.%d.%d",
                 object->device_info.fw_version[0], object->device_info.fw_version[1],
                 object->device_info.fw_version[2], object->device_info.fw_version[3]);

        // get fw generation specific parameters
        object->driver_data->fw_gen = get_ambit3_fw_gen(&object->device_info);
        LOG_INFO("kailash-debug: fw_gen=%d, about to get_ambit3_driver_params", object->driver_data->fw_gen);
        object->driver_data->driver_params = get_ambit3_driver_params(object->driver_data->fw_gen);
        LOG_INFO("kailash-debug: ambit3 driver init() complete");
    } else {
        LOG_WARNING("kailash-debug: ambit3 driver init() calloc failed");
    }
}

/**
 * De-initialises the ambit3 driver.
 *
 * \param object
 */
static void deinit(ambit_object_t *object)
{
    if (object->driver_data != NULL) {
        libambit_pmem20_deinit(&object->driver_data->pmem20);
        libambit_sbem0102_deinit(&object->driver_data->sbem0102);
    }
}

static float ieee754_to_float(uint32_t bits)
{
    int sign = bits >> 31 ? -1 : 1;
    int exp = (int)(((bits >> 23) & 0xff) - 127);
    int frac = (int)((bits & 0x7fffff) | 0x800000);

    return sign * frac * powf(2.0f, exp - 23);
}

/**
 * Gets the personal settings from the watch.
 *
 * \param object
 * \param settings Setting structure to populate.
 * \return 0 if successful.
 */
static int personal_settings_get(ambit_object_t *object, ambit_personal_settings_t *settings)
{
    uint8_t send_data[4] = { 0x00, 0x00, 0x00, 0x00 };
    libambit_sbem0102_data_t reply_data_object;
    uint32_t alarm_num;
    uint32_t decli_num;

    LOG_INFO("Reading personal settings");

    libambit_sbem0102_data_init(&reply_data_object);
    if (libambit_sbem0102_command_request_raw(&object->driver_data->sbem0102, ambit_command_ambit3_settings, send_data, sizeof(send_data), &reply_data_object) != 0) {
        LOG_WARNING("Failed to read personal settings");
        return -1;
    }

    memset(settings, 0, sizeof(ambit_personal_settings_t));

    while (libambit_sbem0102_data_next(&reply_data_object) == 0) {
        switch (libambit_sbem0102_data_id(&reply_data_object)) {
          case 0x01:
            settings->date_format = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x02:
            settings->tones_mode = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x03:
            settings->gps_position_format = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x04:
            decli_num = read32(libambit_sbem0102_data_ptr(&reply_data_object), 0);
            settings->compass_declination_f = ieee754_to_float(decli_num);
            break;
          case 0x08:
            settings->units_mode = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x12:
            settings->language = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x13: // Map orientation
            settings->navigation_style = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x14:
            settings->time_format = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x15:
            settings->sync_time_w_gps = !libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x16: // Dual time enabled
            break;
          case 0x17:
            settings->alarm_enable = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x18:
            alarm_num = read32(libambit_sbem0102_data_ptr(&reply_data_object), 0);
            settings->alarm.hour = (alarm_num / 60 / 60);
            settings->alarm.minute = (alarm_num / 60) % 60;
            break;
          case 0x19:
            settings->is_male = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x1a:
            settings->weight = read16(libambit_sbem0102_data_ptr(&reply_data_object), 0);
            break;
          case 0x1b:
            settings->max_hr = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x1f:
            if (libambit_sbem0102_data_len(&reply_data_object) == 11) {
                sscanf((const char*)libambit_sbem0102_data_ptr(&reply_data_object), "%04hu-", &settings->birthyear);
            }
            break;
          case 0x20: // Display contrast
            settings->display_brightness = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x21:
            settings->display_is_negative = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x22:
            settings->backlight_mode = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x23:
            settings->backlight_brightness = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x26:
            settings->alti_baro_mode = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x27:
            settings->fused_alti_disabled = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          case 0x28:
            settings->storm_alarm = libambit_sbem0102_data_ptr(&reply_data_object)[0];
            break;
          default:
            /*
            printf("Got id=%02x: ", libambit_sbem0102_data_id(&reply_data_object));
            switch(libambit_sbem0102_data_len(&reply_data_object)) {
              case 1:
                printf("%d", libambit_sbem0102_data_ptr(&reply_data_object)[0]);
                break;
              case 2:
                printf("%d", read16(libambit_sbem0102_data_ptr(&reply_data_object), 0));
                break;
              case 4:
                printf("%d", read32(libambit_sbem0102_data_ptr(&reply_data_object), 0));
                break;
              default:
                {
                    int q;
                for(q=0; q<libambit_sbem0102_data_len(&reply_data_object); q++)
                    printf("%02x", libambit_sbem0102_data_ptr(&reply_data_object)[q]);
                }
                break;
            }
            printf("\n");
            */
            break;
        }
    }

    return 0;
}

/**
 * Processes the log read replies for gen1 firmware. Gen1 has a log_header_data_id byte for each log header.
 *
 * \param object
 * \param reply_data_object Data received from the watch.
 * \param skip_cb Callback function when log header received.
 * \param push_cb Callback function when log received.
 * \param progress_cb Callback function when progress received.
 * \param userref
 * \return Number of entries read or -1 if error.
 */
static int process_log_read_replies_gen1(ambit_object_t *object, libambit_sbem0102_data_t *reply_data_object,
                                         ambit_log_skip_cb skip_cb, ambit_log_push_cb push_cb, ambit_log_progress_cb progress_cb, void *userref)
{
    ambit3_log_header_t log_header;
    ambit_log_entry_t *log_entry;

    int entries_read = 0;

    uint16_t log_entries_total = 0;
    uint16_t log_entries_walked = 0;
    uint16_t log_entries_notsynced;
    ONLYDEBUGVAR(log_entries_notsynced);

    log_header.header.activity_name = NULL;

    while (libambit_sbem0102_data_next(reply_data_object) == 0) {
        if (libambit_sbem0102_data_id(reply_data_object) == object->driver_data->driver_params.log_entries_total_data_id) {
            log_entries_total = read16(libambit_sbem0102_data_ptr(reply_data_object), 0);
            LOG_INFO("Number of logs=%d", log_entries_total);
        }
        else if (libambit_sbem0102_data_id(reply_data_object) == object->driver_data->driver_params.log_entries_notsynced_data_id) {
            log_entries_notsynced = read16(libambit_sbem0102_data_ptr(reply_data_object), 0);
            LOG_INFO("Number of logs marked as not synchronized=%d", log_entries_notsynced);
        }
        else if (libambit_sbem0102_data_id(reply_data_object) == object->driver_data->driver_params.log_header_data_id) {
            const uint8_t *data = libambit_sbem0102_data_ptr(reply_data_object);

            if(parse_log_entry(object, data, &log_header) != 0) {
                LOG_INFO("Log header parsed successfully");
                if (!skip_cb || skip_cb(userref, &log_header.header) != 0) {
                    LOG_INFO("Reading data of log %d of %d", log_entries_walked + 1, log_entries_total);
                    log_entry = libambit_pmem20_log_read_entry_address(&object->driver_data->pmem20,
                                                                       log_header.address,
                                                                       log_header.end_address - log_header.address,
                                                                       0, 0,
                                                                       LIBAMBIT_PMEM20_FLAGS_NONE);
                    if (log_entry != NULL) {
                        if (push_cb != NULL) {
                            push_cb(userref, log_entry);
                        }
                        entries_read++;

                        libambit_log_entry_free(log_entry);
                    }
                }
                else {
                    LOG_INFO("Log entry already exists, skipping");
                }
            }
            else {
                LOG_INFO("Failed to parse log header");
            }
            log_entries_walked++;
            if (progress_cb != NULL && log_entries_total != 0) {
                progress_cb(userref, log_entries_total, log_entries_walked, 100*log_entries_walked/log_entries_total);
            }
        }
        else {
            LOG_INFO("Unknown data id 0x%x", libambit_sbem0102_data_id(reply_data_object));
        }
    }
    
    return entries_read;
}

/**
 * Processes the log read replies for all firmware except gen1. There is a log_header_data_id byte for a blick
 * of log headers.
 *
 * \param object
 * \param reply_data_object Data received from the watch.
 * \param skip_cb Callback function when log header received.
 * \param push_cb Callback function when log received.
 * \param progress_cb Callback function when progress received.
 * \param userref
 * \return Number of entries read or -1 if error.
 */
static int process_log_read_replies(ambit_object_t *object, libambit_sbem0102_data_t *reply_data_object,
                                         ambit_log_skip_cb skip_cb, ambit_log_push_cb push_cb, ambit_log_progress_cb progress_cb, void *userref)
{
    uint16_t log_entries_total = 0;
    uint16_t log_entries_notsynced;
    uint16_t log_entries_walked = 0;
    int entries_read = 0;
    ONLYDEBUGVAR(log_entries_notsynced);

    while (libambit_sbem0102_data_next(reply_data_object) == 0) {
        if (libambit_sbem0102_data_id(reply_data_object) == object->driver_data->driver_params.log_entries_total_data_id) {
            log_entries_total = read16(libambit_sbem0102_data_ptr(reply_data_object), 0);
        }
        else if (libambit_sbem0102_data_id(reply_data_object) == object->driver_data->driver_params.log_entries_notsynced_data_id) {
            log_entries_notsynced = read16(libambit_sbem0102_data_ptr(reply_data_object), 0);
            LOG_INFO("Number of logs marked as not synchronized=%d", log_entries_notsynced);
        }
        else if (libambit_sbem0102_data_id(reply_data_object) == object->driver_data->driver_params.log_header_data_id) {
            entries_read = parse_log_header_block(object, reply_data_object, skip_cb, push_cb, progress_cb, userref, &log_entries_walked, log_entries_total);
        }
        else {
            LOG_INFO("Unknown data id 0x%x", libambit_sbem0102_data_id(reply_data_object));
        }
    }

    return entries_read;
}

/**
 * Gets the logs from the watch.
 *
 * \param skip_cb Callback function when log header received.
 * \param push_cb Callback function when log received.
 * \param progress_cb Callback function when progress received.
 * \param userref
 * \return Number of entries read or -1 if error.
 */
static int log_read(ambit_object_t *object, ambit_log_skip_cb skip_cb, ambit_log_push_cb push_cb, ambit_log_progress_cb progress_cb, void *userref)
{
    int entries_read = 0;
    libambit_sbem0102_data_t send_data_object, reply_data_object;
    LOG_INFO("Reading log headers");

    libambit_sbem0102_data_init(&reply_data_object);
    libambit_sbem0102_data_init(&send_data_object);
    libambit_sbem0102_data_add(&send_data_object, object->driver_data->driver_params.log_header_request_data_id, NULL, 0);

    if (libambit_sbem0102_command_request(&object->driver_data->sbem0102, ambit_command_ambit3_log_headers, &send_data_object, &reply_data_object) != 0) {
        LOG_WARNING("Failed to read log headers");
        return -1;
    }

    if (object->driver_data->memory_maps.initialized == 0) {
        if (get_memory_maps(object) != 0) {
            return -1;
        }
    }

    // Initialize PMEM20 log before starting to read logs
    libambit_pmem20_log_init(&object->driver_data->pmem20, object->driver_data->memory_maps.exercise_log.start, object->driver_data->memory_maps.exercise_log.size);

    if (object->driver_data->fw_gen == AMBIT3_FW_GEN1) {
        entries_read = process_log_read_replies_gen1(object, &reply_data_object, skip_cb, push_cb, progress_cb, userref);
    }
    else {
        entries_read = process_log_read_replies(object, &reply_data_object, skip_cb, push_cb, progress_cb, userref);
    }

    printf("Finished reading logs... I think...\n");

    libambit_sbem0102_data_free(&send_data_object);
    printf("Finished freeing data 1\n");
    libambit_sbem0102_data_free(&reply_data_object);

    printf("Finished freeing data 2\n");

    return entries_read;
}

/**
 * Gets the gps orbit header from the watch.
 *
 * \param object
 * \param data Buffer to populate with gps orbit data.
 * \return 0 if successful.
 */
static int gps_orbit_header_read(ambit_object_t *object, uint8_t data[8])
{
    uint8_t *reply_data = NULL;
    size_t replylen = 0;
    int ret = -1;

    if (libambit_protocol_command(object, ambit_command_gps_orbit_head, NULL, 0, &reply_data, &replylen, 0) == 0 && replylen >= 9) {
        memcpy(data, &reply_data[1], 8);
        libambit_protocol_free(reply_data);

        ret = 0;
    }
    else {
        LOG_WARNING("Failed to read GPS orbit header");
    }

    return ret;
}

/**
 * Writes the gps orbit header to the watch.
 *
 * \param object
 * \param data Buffer containing gps orbit data.
 * \param datalen Size of gps orbit data buiffer.
 * \return 0 if successful.
 */
static int gps_orbit_write(ambit_object_t *object, uint8_t *data, size_t datalen)
{
    uint8_t header[8], cmpheader[8];
    int ret = -1;

    LOG_INFO("Writing GPS orbit data");

    libambit_protocol_command(object, ambit_command_write_start, NULL, 0, NULL, NULL, 0);

    if (object->driver->gps_orbit_header_read(object, header) == 0) {
        cmpheader[0] = data[7]; // Year, swap bytes
        cmpheader[1] = data[6];
        cmpheader[2] = data[8];
        cmpheader[3] = data[9];
        cmpheader[4] = data[13]; // 4 byte swap
        cmpheader[5] = data[12];
        cmpheader[6] = data[11];
        cmpheader[7] = data[10];

        // Check if new data differs
        if (memcmp(header, cmpheader, 8) != 0) {
            // Per-device GpsSGEE base (memory_maps.gps): the Traverse keeps this region at a
            // different offset than the Ambit3 Peak's PMEM20_GPS_ORBIT_START, and writing the
            // wrong base resets the watch mid-write (re-enumerates to fw 0.0.0.0 / BSL). Resolve
            // it from the watch's own 0x0b21 map, populating on demand; fall back to the Peak
            // base only if the watch declares no GpsSGEE region.
            if (object->driver_data->memory_maps.initialized == 0) {
                get_memory_maps(object);
            }
            uint32_t gps_base = object->driver_data->memory_maps.gps.start;
            if (gps_base == 0) gps_base = PMEM20_GPS_ORBIT_START;
            ret = libambit_pmem20_gps_orbit_write(&object->driver_data->pmem20, data, datalen, true, gps_base);
        }
        else {
            LOG_INFO("Current GPS orbit data is already up to date, skipping");
            ret = 0;
        }
    }

    return ret;
}

/**
 * Processes a block of log headers (not gen1).
 *
 * \param object
 * \param reply_data_object Data received from the watch.
 * \param skip_cb Callback function when log header received.
 * \param push_cb Callback function when log received.
 * \param progress_cb Callback function when progress received.
 * \param userref
 * \param log_entries_walked Log entries walked.
 * \param log_entries_total Log entries total.
 * \return Number of entries read in this block or -1 if error.
 */
static int parse_log_header_block(ambit_object_t *object, libambit_sbem0102_data_t *reply_data_object, ambit_log_skip_cb skip_cb, ambit_log_push_cb push_cb, ambit_log_progress_cb progress_cb, void *userref,  uint16_t *log_entries_walked, uint16_t log_entries_total)
{
    ambit3_log_header_t log_header;
    ambit_log_entry_t *log_entry;
    const uint8_t *data;
    size_t length = 0;
    size_t offset = 0;
    size_t log_read_len = 0;
    int current_parse_num_log_read = 0;
    int skip;
    
    length = libambit_sbem0102_data_len(reply_data_object);
    data = libambit_sbem0102_data_ptr(reply_data_object);

    while(offset<length) {
        log_header.header.activity_name = NULL;
        log_read_len = parse_log_entry(object, &data[offset], &log_header);

        if(log_read_len == 0) {
            LOG_ERROR("Could not parse log header");
            return -1;
        }

        offset += log_read_len;

        LOG_INFO ("Next offset: %d of %d\n", offset, length);

        if (skip_cb && !skip_cb(userref, &log_header.header)) {
            skip = 1;
        }
        else {
            skip = 0;
        }
        
        if (skip && !log_header.synced) {
            LOG_INFO("Log not previously synchronized, force update");
            skip = 0;
        }
        
        if (!skip) {
            LOG_INFO("Reading data of log %d of %d", *log_entries_walked + 1, log_entries_total);
            log_entry = libambit_pmem20_log_read_entry_address(&object->driver_data->pmem20,
                                                               log_header.address,
                                                               log_header.end_address - log_header.address,
                                                               log_header.address2,
                                                               log_header.end_address2 - log_header.address2,
                                                               LIBAMBIT_PMEM20_FLAGS_UNKNOWN2_PADDING_48);
            LOG_INFO("Completed data of log %d of %d", *log_entries_walked + 1, log_entries_total);
            if (log_entry != NULL) {
                if (push_cb != NULL) {
                    push_cb(userref, log_entry);
                    LOG_INFO("Completed push_cb");
                }
            }
        }
        else {
            LOG_INFO("Log entry already exists, skipping");
        }

        (*log_entries_walked)++;
        current_parse_num_log_read++;

        if(*log_entries_walked > log_entries_total) {
            log_entries_total = *log_entries_walked; // Handle situations where ambit reports wrong number of total entries
        }

        if (progress_cb != NULL && log_entries_total != 0) {
            LOG_INFO("Do progress_cb");
            progress_cb(userref, log_entries_total, *log_entries_walked, 100*(*log_entries_walked)/log_entries_total);
        }
    }

    return current_parse_num_log_read;
}

/**
 * Parses the log header data for one log entry..
 *
 * \param data Raw log header data buffer.
 * \param log_header Structure to populate with header field values.
 * \return Buffer offset at the end of the log header.
 */
static size_t parse_log_entry(ambit_object_t *object, const uint8_t *data, ambit3_log_header_t *log_header)
{
    struct tm tm;
    char *ptr;
    size_t offset = 0;

    // Start with parsing the time
    if ((ptr = libambit_strptime((const char *)data, "%Y-%m-%dT%H:%M:%S", &tm)) == NULL) {
        return 0;
    }

    log_header->header.date_time.year = 1900 + tm.tm_year;
    log_header->header.date_time.month = tm.tm_mon + 1;
    log_header->header.date_time.day = tm.tm_mday;
    log_header->header.date_time.hour = tm.tm_hour;
    log_header->header.date_time.minute = tm.tm_min;
    log_header->header.date_time.msec = tm.tm_sec*1000;
    offset += (size_t)ptr - (size_t)data + 1;

    log_header->synced = read8inc(data, &offset);

    log_header->address = read32inc(data, &offset);
    log_header->end_address = read32inc(data, &offset);
    log_header->address2 = read32inc(data, &offset);
    log_header->end_address2 = read32inc(data, &offset);
    log_header->header.heartrate_min = read8inc(data, &offset);
    log_header->header.heartrate_avg = read8inc(data, &offset);
    log_header->header.heartrate_max = read8inc(data, &offset);

    if (object->driver_data->fw_gen == AMBIT3_FW_GEN1 || object->driver_data->fw_gen == AMBIT3_FW_GEN2) {
        log_header->header.heartrate_max_time = read32inc(data, &offset);
        log_header->header.heartrate_min_time = read32inc(data, &offset);
    }
    else {
        log_header->header.heartrate_min_time = read32inc(data, &offset);
        log_header->header.heartrate_max_time = read32inc(data, &offset);
    }

    // temperature format is messed up, 1 byte is missing, just skip for now
    log_header->header.temperature_min = 0;
    log_header->header.temperature_max = 0;
    offset += 2;
    log_header->header.temperature_min_time = read32inc(data, &offset);
    log_header->header.temperature_max_time = read32inc(data, &offset);
    log_header->header.altitude_min = read16inc(data, &offset);
    log_header->header.altitude_max = read16inc(data, &offset);
    log_header->header.altitude_min_time = read32inc(data, &offset);
    log_header->header.altitude_max_time = read32inc(data, &offset);
    log_header->header.cadence_avg = read8inc(data, &offset);
    log_header->header.cadence_max = read8inc(data, &offset);
    log_header->header.cadence_max_time = read32inc(data, &offset);
    log_header->header.speed_avg = read16inc(data, &offset); // 10 m/h
    log_header->header.speed_max = read16inc(data, &offset); // 10 m/h
    log_header->header.speed_max_time = read32inc(data, &offset);
    offset += 4; // Unknown bytes
    log_header->header.duration = read32inc(data, &offset)*100; // seconds 0.1
    log_header->header.ascent = read16inc(data, &offset);
    log_header->header.descent = read16inc(data, &offset);
    log_header->header.ascent_time = read32inc(data, &offset)*1000;
    log_header->header.descent_time = read32inc(data, &offset)*1000;
    log_header->header.recovery_time = read16inc(data, &offset)*60*1000;
    log_header->header.peak_training_effect = read8inc(data, &offset);

    if (log_header->header.activity_name) {
        free(log_header->header.activity_name);
    }
    log_header->header.activity_name = utf8memconv((const char*)(data + offset), 16, "UTF-8");

    offset += (strnlen((const char*)(data + offset), 20)+1);

    log_header->header.distance = read32inc(data, &offset);
    log_header->header.energy_consumption = read16inc(data, &offset);

    offset += object->driver_data->driver_params.log_header_tail_length;

    return offset;
}

/**
 * Gets the memory maps from the watch.
 *
 * \param object
 * \return 0 if successful.
 */
static int get_memory_maps(ambit_object_t *object)
{
    uint8_t legacy_format = 0;
    uint8_t *reply_data = NULL;
    size_t replylen = 0;
    uint8_t send_data[4] = { 0x00, 0x00, 0x00, 0x00 };
    libambit_sbem0102_data_t reply_data_object;
    uint8_t mm_entry_data_id = 0;
    memory_map_entry_t *mm_entry;
    const uint8_t *ptr;

    legacy_format = object->driver_data->driver_params.mm_legacy_format;

    if (libambit_protocol_command(object, ambit_command_waypoint_count, NULL, 0, &reply_data, &replylen, legacy_format) != 0 || replylen < 4) {
        libambit_protocol_free(reply_data);
        LOG_WARNING("Failed to read memory map key");
        return -1;
    }
    libambit_protocol_free(reply_data);

    libambit_sbem0102_data_init(&reply_data_object);
    if (libambit_sbem0102_command_request_raw(&object->driver_data->sbem0102, ambit_command_ambit3_memory_map, send_data, sizeof(send_data), &reply_data_object) != 0) {
        LOG_WARNING("Failed to read memory map");
        return -1;
    }

    mm_entry_data_id = object->driver_data->driver_params.mm_entry_data_id;

    while (libambit_sbem0102_data_next(&reply_data_object) == 0) {
        if (libambit_sbem0102_data_id(&reply_data_object) == mm_entry_data_id) {
            ptr = libambit_sbem0102_data_ptr(&reply_data_object);
            LOG_INFO("Memory map entry \"%s\"", ptr);
            mm_entry = NULL;
            if (strcmp((char*)ptr, "Waypoints") == 0) {
                mm_entry = &object->driver_data->memory_maps.waypoints;
            }
            else if (strcmp((char*)ptr, "Routes") == 0) {
                mm_entry = &object->driver_data->memory_maps.routes;
            }
            else if (strcmp((char*)ptr, "Rules") == 0) {
                mm_entry = &object->driver_data->memory_maps.rules;
            }
            else if (strcmp((char*)ptr, "GpsSGEE") == 0) {
                mm_entry = &object->driver_data->memory_maps.gps;
            }
            else if (strcmp((char*)ptr, "CustomModes") == 0) {
                mm_entry = &object->driver_data->memory_maps.sport_modes;
            }
            else if (strcmp((char*)ptr, "TrainingProgram") == 0) {
                mm_entry = &object->driver_data->memory_maps.training_program;
            }
            else if (strcmp((char*)ptr, "ExerciseLog") == 0) {
                mm_entry = &object->driver_data->memory_maps.exercise_log;
            }
            else if (strcmp((char*)ptr, "EventLog") == 0) {
                mm_entry = &object->driver_data->memory_maps.event_log;
            }
            else if (strcmp((char*)ptr, "BlePairingInfo") == 0) {
                mm_entry = &object->driver_data->memory_maps.ble_pairing;
            }
            else if (strcmp((char*)ptr, "Apps") == 0) {
                mm_entry = &object->driver_data->memory_maps.apps;
            }
            else {
                LOG_WARNING("Unknown memory map type \"%s\"", (char*)ptr);
            }

            if (mm_entry != NULL) {
                // We have dealed with the name, advance to hash
                ptr += strlen((char*)ptr) + 1;

                if (libambit_htob((const char*)ptr, mm_entry->hash, sizeof(mm_entry->hash)) < 0) {
                    LOG_ERROR("Failed to read memory map hash");
                }
                ptr += strlen((char*)ptr) + 1;

                mm_entry->start = read32(ptr, 0);
                ptr += 4;
                mm_entry->size = read32(ptr, 0);
            }
        }
    }

    object->driver_data->memory_maps.initialized = 1;
    libambit_sbem0102_data_free(&reply_data_object);

    LOG_INFO("Memory map successfully parsed");

    return 0;
}

/**
 * Set log as synchronized
 *
 * \param object
 * \param log_entry Log to set as synchronized
 * \return 0 if successful.
 */
static int log_synced(ambit_object_t *object, ambit_log_entry_t *log_entry)
{
    libambit_sbem0102_data_t send_data_object, reply_data_object;

    LOG_INFO("Sync log");

    struct {
        uint8_t timestamp[0x14];
        uint8_t synced;
    } sbem0102_synced;

    ambit_date_time_t dt;
    memcpy(&dt, &log_entry->header.date_time, sizeof(dt));
    // date_time.msec holds seconds*1000 (see parse_log_entry: `.msec = tm.tm_sec*1000`), so
    // the seconds field is msec/1000. Upstream openambit passed dt.msec straight into the
    // "%02d" seconds slot, which prints e.g. 6000 for :06 - a malformed timestamp that never
    // matches the move on the watch, so the synced flag silently never applies. Divided here
    // to match the real SuuntoLink cable capture ("...T18:14:06") and the companion desktop
    // tools/mark_synced.py, which is proven byte-exact against that capture.
    snprintf((char*)sbem0102_synced.timestamp, sizeof(sbem0102_synced.timestamp), "%04d-%02d-%02dT%02d:%02d:%02d",
            dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.msec / 1000);

    sbem0102_synced.synced = 1;
    
    libambit_sbem0102_data_init(&reply_data_object);
    libambit_sbem0102_data_init(&send_data_object);
    libambit_sbem0102_data_add(&send_data_object, object->driver_data->driver_params.log_synced_data_id, (uint8_t*)&sbem0102_synced, sizeof(sbem0102_synced));

    if (libambit_sbem0102_command_request(&object->driver_data->sbem0102, ambit_command_ambit3_log_synced, &send_data_object, &reply_data_object) != 0) {
        LOG_WARNING("Failed to sync log");
        return -1;
    }

    return 0;
}

/**
 * Kailash clock write - real, hardware-confirmed on BOTH transports.
 *
 * Real finding, 2026-08-10: neither the cable's plain 0x0300/0x0302 pair nor
 * the generic 0x1101 whole-blob settings write apply here - Kailash's
 * Time.TimeISO8601 entry (id 0x34) never even appears in a 0x1100 settings
 * read, on either transport. The real mechanism, first found byte-exact and
 * identical across five separate real 7R-app BLE captures (kailashpair.pklg,
 * kailashsethome.pklg, kaylashactivity04km.pklg, kaylashactivity1.8km.pklg,
 * kailash7rpair.btsnoop), then CONFIRMED IDENTICAL over cable too (real
 * SuuntoLink capture kailashsynctimefrom12178july2024to10820261618 - same
 * command, same entry id, same "+0200"-style ISO8601 string, byte-exact):
 * a single-entry SBEM0102 push sent via command 0x1201 (the same
 * opcode/wire-shape log_synced() above already uses for its own single-entry
 * push) of entry 0x34 (utf8 ISO8601 string *with* timezone offset, e.g.
 * "2026-08-08T17:20:22+0200", NUL-terminated). The real BLE captures also
 * always sent a second push of entry 0x48 (sml.DeviceLog.NextTime, a single
 * 0x00 byte) right after - the real cable capture does NOT, confirming it's
 * unrelated to the clock write itself; still replicated here for BLE only,
 * non-fatal if rejected.
 *
 * Earlier in this same investigation a bootloader-mode-only hypothesis was
 * drawn from a single capture (resumefirmwarekailash) that happened to enter
 * firmware mode right before a plain 0x0300/0x0302 pair - that pair was
 * always a red herring; Kailash's real clock write never touches firmware or
 * a mode transition on either transport.
 */
/* The SBEM0102 time push (command 0x1201): one entry - a NUL-terminated ISO8601-with-offset
 * string - is what actually sets the clock on the watches that ignore the legacy 0x0300/0x0302
 * command pair. The ENTRY ID is schema-specific:
 *   - Kailash (Hoopoe):        0x34
 *   - Traverse (Jabiru) fw2.x: 0x3f  (fw 1.0.4 used 0x3d - confirmed in the real
 *     traverseoldfirmwaretonew capture, entry id changed across the firmware update; 0x3f is
 *     fw 2.0.22, the shipping firmware)
 * All confirmed byte-exact against real SuuntoLink/7R captures. The legacy 0x0300/0x0302 is
 * MovesLink-era and these firmwares ACK it but never apply it. */
static int ambit3_sbem_time_sync(ambit_object_t *object, struct tm *tm, uint8_t entry_id, bool send_next_time)
{
    libambit_sbem0102_data_t send_data_object, reply_data_object;
    char iso8601[32];
    uint8_t next_time = 0x00;
    int ret = 0;

    // %z on both glibc (desktop test builds) and bionic (real Android target)
    // renders "+0200" (no colon) from a tm with tm_gmtoff/tm_zone set - exactly
    // the real captures' own encoding. tm must come from localtime_r(), not
    // gmtime_r(), for tm_gmtoff to be populated.
    strftime(iso8601, sizeof(iso8601), "%Y-%m-%dT%H:%M:%S%z", tm);

    LOG_INFO("SBEM time sync (entry 0x%02x): %s", entry_id, iso8601);

    libambit_sbem0102_data_init(&send_data_object);
    libambit_sbem0102_data_init(&reply_data_object);
    libambit_sbem0102_data_add(&send_data_object, entry_id, (uint8_t*)iso8601, (uint8_t)(strlen(iso8601) + 1));
    if (libambit_sbem0102_command_request(&object->driver_data->sbem0102, ambit_command_ambit3_log_synced, &send_data_object, &reply_data_object) != 0) {
        LOG_WARNING("SBEM time sync: failed to write Time.TimeISO8601");
        ret = -1;
    }
    libambit_sbem0102_data_free(&send_data_object);
    libambit_sbem0102_data_free(&reply_data_object);

    if (ret == 0 && send_next_time && object->transport == AMBIT_TRANSPORT_BLE) {
        libambit_sbem0102_data_init(&send_data_object);
        libambit_sbem0102_data_init(&reply_data_object);
        libambit_sbem0102_data_add(&send_data_object, 0x48, &next_time, sizeof(next_time));
        // Real capture behavior only, not required for the clock write itself -
        // don't fail the whole sync if this second, still-unexplained push is
        // rejected. Cable's own real capture never sends this at all.
        if (libambit_sbem0102_command_request(&object->driver_data->sbem0102, ambit_command_ambit3_log_synced, &send_data_object, &reply_data_object) != 0) {
            LOG_WARNING("Kailash time sync: NextTime push failed (non-fatal)");
        }
        libambit_sbem0102_data_free(&send_data_object);
        libambit_sbem0102_data_free(&reply_data_object);
    }

    return ret;
}

/* Kailash (Hoopoe): entry 0x34, plus the BLE-only NextTime (0x48) follow-up its own captures show. */
static int kailash_time_sync(ambit_object_t *object, struct tm *tm)
{
    return ambit3_sbem_time_sync(object, tm, 0x34, true);
}

/* Traverse (Jabiru): entry 0x3f on fw 2.x, no NextTime push (its cable capture never sends one). */
static int traverse_time_sync(ambit_object_t *object, struct tm *tm)
{
    return ambit3_sbem_time_sync(object, tm, 0x3f, false);
}

/**
 * date_time_set dispatch: Kailash and Traverse ignore the legacy 0x0300/0x0302 clock command
 * pair (they ACK it but never apply it) - both take their clock as an SBEM 0x1201 ISO8601 push
 * instead, at their own schema's Time.TimeISO8601 entry id (Kailash 0x34, Traverse fw2.x 0x3f;
 * confirmed byte-exact against real SuuntoLink/7R captures). The Ambit3 Peak/Sport still use the
 * MovesLink-confirmed 0x0300/0x0302 pair, which works there.
 *
 * Real bug, caught live 2026-08-10 ("libambit_protocol_command_ble: watch
 * returned errFlags for command 0x0302" - the write silently fell through to
 * the wrong path): can't dispatch on device_info.product_id here - over BLE,
 * AmbitBleModule.kt's own guessProductId() has no real product id to read (the
 * BLE hello payload carries no such field) and picks 0x001c ("Ambit3 Sport")
 * for every non-Traverse Ambit3-family device, Kailash included - identical to
 * a real Ambit3. What IS reliable on both transports is device_info.model,
 * set directly from the device_info/hello reply's own model string
 * (libambit.c for USB, protocol_ble.c for BLE) - "Hoopoe" is Kailash's real
 * internal codename, seen byte-exact in every real capture on both
 * ("486f6f706f6500..." decoded).
 */
static int date_time_set(ambit_object_t *object, struct tm *tm)
{
    if (object->device_info.model != NULL) {
        if (strcmp(object->device_info.model, "Hoopoe") == 0)
            return kailash_time_sync(object, tm);
        if (strcmp(object->device_info.model, "Jabiru") == 0)
            return traverse_time_sync(object, tm);
    }
    return libambit_device_driver_date_time_set(object, tm);
}

/*
 * ─── Route write (GPX-to-watch navigation) ────────────────────────────────
 *
 * Not part of upstream openambit: this is the transport layer for
 * ambit3_navigation_plan() (device_driver_ambit3_navigation.c/.h, ported
 * unmodified from a separate reverse-engineering project, verified byte for
 * byte against real USBPcap captures and against real hardware writes).
 * That function only builds the payloads; everything below reproduces the
 * exact command sequence SuuntoLink itself uses, confirmed on hardware:
 *
 *   0x0b24 (read the POI list) -> waypoint region writes + tail hash ->
 *   route region writes + tail hash -> 0x0b04 (commit) ->
 *   0x0b25 (restore the POI list)
 *
 * A navigation write erases the whole POI store; the read-before/restore-
 * after pair is what makes that invisible to the user. This is not
 * optional. protocol.h's own enum mislabels 0x0b25 as "unknown4" and is
 * missing 0x0b24 entirely -- both are defined locally below rather than
 * trusted from there.
 *
 * IMPORTANT, and reflected below: routes written this way are not durable.
 * SuuntoLink's own sync (and, per field testing, even the Suunto phone app
 * merely coming into BLE range) wholesale-replaces whatever is in the Routes
 * region on its own next sync, with no merge. POIs persist; routes don't.
 * This is a "load right before you go" feature, not permanent storage --
 * make sure any caller surfaces that to the user rather than implying the
 * route is saved for good.
 */

#define AMBIT3_CMD_POI_READ   0x0b24
#define AMBIT3_CMD_POI_WRITE  0x0b25
#define AMBIT3_CMD_NAV_COMMIT 0x0b04 /* == ambit_command_nav_memory_delete in protocol.h; that name is wrong, the value isn't */

#define AMBIT3_POI_ENTRY_ID   0x55

/* [u32 0][u8 0x01][u8 0x01]["SBEM0102"] -- the fixed 14-byte prefix every
 * SBEM0102-framed write (not just POIs) uses. Confirmed byte for byte
 * against a real 0x0b25 capture; not derivable from the protocol.h enum. */
static const uint8_t AMBIT3_SBEM_WRITE_PREFIX[14] = {
    0x00, 0x00, 0x00, 0x00, 0x01, 0x01,
    'S', 'B', 'E', 'M', '0', '1', '0', '2',
};

/* Builds the [u32 address][u32 0][64 ASCII hex chars] tail payload and sends
 * it via 0x0b18. `hash_hex` must be a NUL-terminated 64-character uppercase
 * hex string, as ambit3_navigation_plan() already produces. */
static int ambit3_send_region_tail(ambit_object_t *object, uint32_t address, const char *hash_hex)
{
    uint8_t tail[4 + 4 + 64];
    uint32_t addr_le = htole32(address);
    memcpy(tail, &addr_le, 4);
    memset(tail + 4, 0, 4); /* the second u32 is opaque, supplied-by-the-application in every real
                                capture examined; zeroed here, same as this project's own reference
                                tooling -- the watch has never rejected a write over this field */
    memcpy(tail + 8, hash_hex, 64);

    if (libambit_protocol_command(object, ambit_command_data_tail_len, tail, sizeof(tail), NULL, NULL, 0) != 0) {
        LOG_WARNING("Failed to write region tail for address 0x%06x", address);
        return -1;
    }
    return 0;
}

/* Sends every write in `plan` whose address falls in [base, base+size), via
 * the existing generic pmem20 chunker, then that group's closing tail. */
static int ambit3_send_region(ambit_object_t *object, const ambit3_nav_plan_t *plan,
                               uint32_t base, uint32_t size, const char *hash_hex)
{
    for (size_t i = 0; i < plan->write_count; i++) {
        const ambit3_nav_write_t *w = &plan->writes[i];
        if (w->address < base || w->address >= base + size) continue;
        if (libambit_pmem20_data_write(&object->driver_data->pmem20, w->address, w->data, w->length) != 0) {
            LOG_WARNING("Failed to write region chunk at 0x%06x (%u bytes)", w->address, w->length);
            return -1;
        }
    }
    return ambit3_send_region_tail(object, base, hash_hex);
}

/*
 * Reads the watch's POI list (0x0b24) and collects a pointer/length pair
 * per entry (id AMBIT3_POI_ENTRY_ID) into caller-provided arrays. `bodies[i]`
 * points inside `*poi_reply_out`, which the caller must free with
 * libambit_protocol_free() once done reading them -- NOT before.
 * Returns the entry count (0 for a genuinely empty POI store, a real,
 * reachable state confirmed on hardware), or -1 if the read itself failed.
 */
#define AMBIT3_CMD_POI_COUNT  0x0b02 /* == ambit_command_waypoint_count: on the Ambit3 it counts the POI store */
#define AMBIT3_POI_MAX_PAGES  32

/* The watch's POI count (0x0b02, u32 LE), or -1 if the command failed. In the
 * MSP430X firmware (Emu 2.4.17) 0x0b02 returns count(list1)+count(list2), the same
 * two lists 0x0b24 walks - see scratchpad fwre/FINDINGS.md of 2026-10-24. SuuntoLink's
 * own POI import opens with it too (assets/pcap/ambit3poiimport). */
static int ambit3_poi_count(ambit_object_t *object)
{
    uint8_t *reply = NULL;
    size_t len = 0;
    int n = -1;
    if (libambit_protocol_command(object, AMBIT3_CMD_POI_COUNT, NULL, 0, &reply, &len, 0) == 0
        && reply != NULL && len >= 4) {
        n = (int)((uint32_t)reply[0] | ((uint32_t)reply[1] << 8) |
                  ((uint32_t)reply[2] << 16) | ((uint32_t)reply[3] << 24));
    }
    libambit_protocol_free(reply);
    return n;
}

/*
 * The whole POI list as ONE buffer shaped like a single 0x0b24 reply:
 * [6-byte page header]["SBEM0102"][0x55 entries...]. Returns 0 (with *out NULL and
 * *out_len 0 for an empty store) or -1. Two firmware facts this handles
 * (MSP430X Emu 2.4.17, 0x0b24 case at 0x1a632, serializer FUN_0002fd08):
 *  - An EMPTY store is answered with an error (flags 0x02, errFlags 1, no data): the
 *    serializer wrote nothing. Over USB libambit never looks at errFlags so it read as
 *    0 bytes; over Bluetooth protocol_ble.c returns -1 - which is why POIs, and routes
 *    (their write reads the POIs first), stopped working over Bluetooth once the store
 *    was empty. So a failed read asks for the count: 0 means empty, anything else is a
 *    real failure.
 *  - The reply is PAGED: [u16 nextCursor][u16 0][u16 isLast][data <= 0x3fa bytes], the
 *    SBEM header only on the first page; the request is [u16 cursor][u16 0]. One request
 *    used to be all we sent, so a long list was cut short - and a route write then put
 *    back only that part. SuuntoLink's capture (4 POIs) is one page with isLast = 1.
 */
static int ambit3_read_poi_all(ambit_object_t *object, uint8_t **out, size_t *out_len)
{
    *out = NULL;
    *out_len = 0;
    uint8_t *all = NULL;
    size_t all_len = 0;
    uint16_t cursor = 0;

    for (int page = 0; page < AMBIT3_POI_MAX_PAGES; page++) {
        uint8_t req[4] = { (uint8_t)(cursor & 0xff), (uint8_t)(cursor >> 8), 0, 0 };
        uint8_t *reply = NULL;
        size_t len = 0;
        if (libambit_protocol_command(object, AMBIT3_CMD_POI_READ, req, sizeof(req), &reply, &len, 0) != 0) {
            libambit_protocol_free(reply);
            if (page == 0 && ambit3_poi_count(object) == 0) {
                LOG_INFO("ambit3_read_poi_all: the watch has no POIs");
                return 0;
            }
            LOG_ERROR("ambit3_read_poi_all: page %d (cursor %u) failed", page, cursor);
            free(all);
            return -1;
        }
        if (reply == NULL || len < 6) {
            /* Over USB an empty store comes back as 0 bytes, not as an error. */
            libambit_protocol_free(reply);
            if (page == 0) return 0;
            LOG_ERROR("ambit3_read_poi_all: page %d came back empty", page);
            free(all);
            return -1;
        }
        uint16_t next = (uint16_t)(reply[0] | (reply[1] << 8));
        uint16_t is_last = (uint16_t)(reply[4] | (reply[5] << 8));
        /* page 0 keeps its header; later pages contribute their data only */
        size_t skip = page == 0 ? 0 : 6;
        uint8_t *grown = (uint8_t *)realloc(all, all_len + (len - skip));
        if (grown == NULL) {
            libambit_protocol_free(reply);
            free(all);
            return -1;
        }
        all = grown;
        memcpy(all + all_len, reply + skip, len - skip);
        all_len += len - skip;
        libambit_protocol_free(reply);

        if (is_last != 0) {
            *out = all;
            *out_len = all_len;
            return 0;
        }
        if (next == cursor) {
            LOG_ERROR("ambit3_read_poi_all: the cursor did not move (%u)", cursor);
            free(all);
            return -1;
        }
        cursor = next;
    }
    LOG_ERROR("ambit3_read_poi_all: more than %d pages", AMBIT3_POI_MAX_PAGES);
    free(all);
    return -1;
}

/* The 0x55 entries of a buffer shaped like one 0x0b24 reply ([6-byte page header]
 * ["SBEM0102"][entries]), as pointer/length pairs into `buf`. Returns the count, or -1
 * past max_entries. A buffer with no SBEM header holds no entries. */
static int ambit3_parse_poi_entries(const uint8_t *buf, size_t len,
                                    const uint8_t **bodies, uint32_t *body_lens, size_t max_entries)
{
    size_t entry_count = 0;
    if (buf != NULL && len > 14 && memcmp(buf + 6, "SBEM0102", 8) == 0) {
        libambit_sbem0102_data_t poi_entries;
        poi_entries.data = (uint8_t *)buf + 14;
        poi_entries.size = len - 14;
        libambit_sbem0102_data_reset(&poi_entries);

        while (libambit_sbem0102_data_next(&poi_entries) == 0) {
            if (libambit_sbem0102_data_id(&poi_entries) == AMBIT3_POI_ENTRY_ID) {
                if (entry_count == max_entries) {
                    /* Refuse rather than return a cut list: a route write would put back
                     * only the first max_entries and lose the rest. */
                    LOG_ERROR("ambit3_parse_poi_entries: more than %zu POIs", max_entries);
                    return -1;
                }
                bodies[entry_count] = libambit_sbem0102_data_ptr(&poi_entries);
                body_lens[entry_count] = libambit_sbem0102_data_len(&poi_entries);
                entry_count++;
            }
        }
    }
    return (int)entry_count;
}

static int ambit3_read_poi_entries(ambit_object_t *object,
                                    const uint8_t **bodies, uint32_t *body_lens, size_t max_entries,
                                    uint8_t **poi_reply_out, size_t *poi_reply_len_out)
{
    if (ambit3_read_poi_all(object, poi_reply_out, poi_reply_len_out) != 0) {
        return -1;
    }
    return ambit3_parse_poi_entries(*poi_reply_out, *poi_reply_len_out, bodies, body_lens, max_entries);
}

/*
 * Builds the full 0x0b25 SBEM0102 write payload from a list of existing POI
 * entry bodies, plus an optional new record placed first (poiimport's own
 * behaviour: new POI first, the rest following in whatever order the
 * caller already put `bodies` in -- reversed for a plain preserve, as-read
 * for an add; see the two callers). Returns NULL (and *out_len = 0) if
 * there is nothing to write at all. Caller frees the returned buffer.
 */
static uint8_t *ambit3_build_poi_write_payload(
    const uint8_t *const *bodies, const uint32_t *body_lens, size_t entry_count,
    const uint8_t *new_record, size_t new_record_len,
    size_t *out_len)
{
    size_t total_body_len = new_record_len;
    for (size_t i = 0; i < entry_count; i++) total_body_len += body_lens[i];
    *out_len = 0;
    if (total_body_len == 0) return NULL;

    /* Never the extended 0x55 0xff+u32 header: only the watch writes that form (on read), and
     * writing it corrupted the POI store (desktop, 2026-08-30). A longer list goes through
     * ambit3_write_poi_bodies(), in several entries. */
    if (total_body_len >= 0xff) {
        LOG_ERROR("ambit3_build_poi_write_payload: %zu bytes do not fit one entry", total_body_len);
        return NULL;
    }
    size_t header_len = 2;
    size_t payload_len = sizeof(AMBIT3_SBEM_WRITE_PREFIX) + header_len + total_body_len;
    uint8_t *payload = (uint8_t*)malloc(payload_len);
    if (payload == NULL) return NULL;

    uint8_t *p = payload;
    memcpy(p, AMBIT3_SBEM_WRITE_PREFIX, sizeof(AMBIT3_SBEM_WRITE_PREFIX));
    p += sizeof(AMBIT3_SBEM_WRITE_PREFIX);
    *p++ = AMBIT3_POI_ENTRY_ID;
    *p++ = (uint8_t)total_body_len;
    if (new_record != NULL) {
        memcpy(p, new_record, new_record_len);
        p += new_record_len;
    }
    for (size_t i = 0; i < entry_count; i++) {
        memcpy(p, bodies[i], body_lens[i]);
        p += body_lens[i];
    }

    *out_len = payload_len;
    return payload;
}

/* The watch holds two lists of 250 POIs (MSP430X Emu 2.4.17, flash 0x27100/0x2B100). */
#define AMBIT3_POI_MAX_EXISTING 500

/* Most bytes in one 0x55 entry: its length must stay a single byte (< 0xff). */
#define AMBIT3_POI_ENTRY_MAX 0xfe

/*
 * Appends POI records to the store: packed into 0x55 entries of at most 254 bytes, the
 * SuuntoLink poiimport shape (one entry, single-byte length) and the desktop's
 * poi_entry_payloads() (tools/write_nav.py), one 0x0b25 per entry. 0x0b25 appends what it is given
 * (firmware FUN_0004da50), so several in a row add up, as several Add POI do. Empty bodies
 * are skipped. Returns 0, or -1 if a write failed (the ones before it are stored).
 */
static int ambit3_write_poi_bodies(ambit_object_t *object,
                                   const uint8_t *const *bodies, const uint32_t *body_lens, size_t count)
{
    uint8_t buf[sizeof(AMBIT3_SBEM_WRITE_PREFIX) + 2 + AMBIT3_POI_ENTRY_MAX];
    const size_t head = sizeof(AMBIT3_SBEM_WRITE_PREFIX) + 2;
    size_t i = 0;
    while (i < count) {
        size_t used = 0;
        while (i < count && (body_lens[i] == 0 || used + body_lens[i] <= AMBIT3_POI_ENTRY_MAX)) {
            memcpy(buf + head + used, bodies[i], body_lens[i]);
            used += body_lens[i];
            i++;
        }
        if (used == 0) {
            if (i >= count) break;            /* only empty bodies were left */
            LOG_ERROR("ambit3_write_poi_bodies: a %u-byte POI record does not fit one entry", body_lens[i]);
            return -1;
        }
        memcpy(buf, AMBIT3_SBEM_WRITE_PREFIX, sizeof(AMBIT3_SBEM_WRITE_PREFIX));
        buf[sizeof(AMBIT3_SBEM_WRITE_PREFIX)] = AMBIT3_POI_ENTRY_ID;
        buf[sizeof(AMBIT3_SBEM_WRITE_PREFIX) + 1] = (uint8_t)used;
        if (libambit_protocol_command(object, AMBIT3_CMD_POI_WRITE, buf, head + used, NULL, NULL, 0) != 0) {
            LOG_ERROR("ambit3_write_poi_bodies: a POI write (0x0b25) failed");
            return -1;
        }
    }
    return 0;
}

/* The watch keeps 15 bytes of a POI name. A longer one is stored (the count goes up) but the
 * watch then lists it as an empty 0x55 entry, so it vanishes from every read: seen on André's
 * Peak 2026-10-25 with "114 Av. André M", 15 letters but 16 bytes because of the é. Same rule
 * as tools/ambit_format.py poi_name_bytes(): cut on a UTF-8 character boundary, and drop the
 * spaces the cut leaves at the end. */
#define AMBIT3_POI_NAME_MAX 15

static size_t ambit3_poi_name_len(const char *name)
{
    size_t n = strlen(name);
    if (n <= AMBIT3_POI_NAME_MAX) return n;
    n = AMBIT3_POI_NAME_MAX;
    while (n > 0 && ((unsigned char)name[n] & 0xC0) == 0x80) n--;   /* inside a character */
    while (n > 0 && name[n - 1] == ' ') n--;
    return n;
}

/*
 * Adds one POI to the watch. 0x0b25 APPENDS each 0x55 entry it is given to the POI
 * store (firmware FUN_0004da50 -> FUN_000397e6, no implicit clear), and it acks even
 * when nothing was stored. So only the new record is sent - sending "new + the list
 * already there", as this used to, doubled every existing POI on each add (the repeated
 * names seen on the Peak). SuuntoLink's own import does send the whole list, but only
 * after a 0x0b04, which empties the store (assets/pcap/ambit3poiimport). Checked by the
 * count (0x0b02) going up by exactly one. Never touches the Waypoints/Routes regions.
 * Byte layout ported from sommet/tools/ambit_format.py's build_poi_record().
 * `name` must be non-empty. Returns 0 on success, -1 on failure.
 */
int ambit3_add_poi_to_watch(ambit_object_t *object, const char *name, double lat, double lon, int type)
{
    if (object == NULL || object->driver_data == NULL || name == NULL || name[0] == '\0') {
        LOG_ERROR("ambit3_add_poi_to_watch: invalid arguments");
        return -1;
    }
    /* POI type byte 0-17 (the icon the watch shows). Out of range -> 17 ("Waypoint"),
     * what the watch itself uses, so a bad value never corrupts the record. */
    if (type < 0 || type > 17) type = 17;

    int count_before = ambit3_poi_count(object);
    if (count_before < 0) {
        LOG_ERROR("ambit3_add_poi_to_watch: could not read the POI count, nothing written");
        return -1;
    }
    if (count_before >= AMBIT3_POI_MAX_EXISTING) {
        LOG_ERROR("ambit3_add_poi_to_watch: the watch already holds %d POIs", count_before);
        return -1;
    }

    /* Timestamp: ISO 8601, no offset suffix. tools/README.md documented this
     * field as UTC from a single reference watch's own self-created POI, but
     * a direct test on real hardware here (2026-08-06, watch and phone
     * clocks agreed, watch's own POI screen showed the write's UTC value
     * literally rather than converting it) shows the watch does NOT convert
     * this field for display -- it echoes back whatever was stored. Storing
     * local time instead is what makes the watch's own UI show the real
     * creation time; the original finding was very likely a reference watch
     * that happened to be configured for UTC+0, making the two
     * indistinguishable in that single test. Uses the phone's local
     * timezone, which is what the user actually wants reflected. */
    char stamp[32];
    time_t now = time(NULL);
    struct tm tmv;
    localtime_r(&now, &tmv);
    strftime(stamp, sizeof(stamp), "%Y-%m-%dT%H:%M:%S", &tmv);

    /* name\0 + route_name\0 (empty: standalone, not tied to a route) + stamp\0 +
     * [route_index=0][type=17][sub_type=0][type_index=0][flags=1] +
     * [i32 LE lat*1e7][i32 LE lon*1e7]. type=17/flags=1 match what the watch
     * itself writes for a POI it creates (SuuntoLink instead leaves those at
     * 0 for an imported one) -- picked so this looks the same as a
     * watch-made POI rather than an imported one. */
    size_t name_len = ambit3_poi_name_len(name);
    size_t stamp_len = strlen(stamp);
    size_t record_len = name_len + 1 + 1 /* empty route_name + its NUL */ + stamp_len + 1 + 5 + 8;
    uint8_t *record = (uint8_t*)malloc(record_len);
    int ret = -1;
    if (record == NULL) {
        LOG_ERROR("ambit3_add_poi_to_watch: out of memory building the POI record");
        return -1;
    }
    uint8_t *p = record;
    memcpy(p, name, name_len); p += name_len; *p++ = 0;
    *p++ = 0; /* empty route_name */
    memcpy(p, stamp, stamp_len); p += stamp_len; *p++ = 0;
    *p++ = 0;  /* route_index */
    *p++ = (uint8_t)type; /* POI type (icon); default 17 = Waypoint, validated above */
    *p++ = 0;  /* sub_type */
    *p++ = 0;  /* type_index */
    *p++ = 1;  /* flags */
    int32_t lat_i = (int32_t)llround(lat * 1e7);
    int32_t lon_i = (int32_t)llround(lon * 1e7);
    uint32_t lat_le = htole32((uint32_t)lat_i);
    uint32_t lon_le = htole32((uint32_t)lon_i);
    memcpy(p, &lat_le, 4); p += 4;
    memcpy(p, &lon_le, 4); p += 4;

    size_t payload_len = 0;
    uint8_t *payload = ambit3_build_poi_write_payload(NULL, NULL, 0, record, record_len, &payload_len);
    if (payload == NULL) {
        LOG_ERROR("ambit3_add_poi_to_watch: failed to build the POI write payload");
    } else if (libambit_protocol_command(object, AMBIT3_CMD_POI_WRITE, payload, payload_len, NULL, NULL, 0) != 0) {
        LOG_ERROR("ambit3_add_poi_to_watch: write failed");
    } else {
        int count_after = ambit3_poi_count(object);
        if (count_after == count_before + 1) {
            LOG_INFO("ambit3_add_poi_to_watch: added '%s' (%d -> %d POIs)", name, count_before, count_after);
            ret = 0;
        } else {
            LOG_ERROR("ambit3_add_poi_to_watch: the watch did not keep '%s' (%d -> %d POIs)",
                      name, count_before, count_after);
        }
    }

    free(payload);
    free(record);
    return ret;
}

/*
 * Replaces the watch's POI list with the 0x55 entries of `new_list`, a buffer shaped like a
 * 0x0b24 read (PoiService.ts builds it from a read, minus the POIs being removed; a saved
 * copy restores the same way). `expected` is the read the caller saved a copy of: the list
 * is read again here and nothing is written unless it is byte for byte the same, so that
 * copy is exactly what gets replaced.
 *
 *   0x0b04 empties both POI lists. Firmware 2.4.17, 0x0b04 handler at 0x1a65c:
 *          FUN_000688ca(list1), FUN_000688ca(list2), then the ack - the Waypoints/Routes
 *          regions are not touched (fwre/FINDINGS.md, 2026-10-24). The count must then read 0:
 *          over USB an error reply is not reported, and appending to a store that was not
 *          emptied would double it.
 *   0x0b25 x n  appends the new list (ambit3_write_poi_bodies), in reverse: the store reads
 *          newest first, so this keeps the order, as the route write's restore does.
 *   0x0b02 must then count exactly the new list.
 *
 * Empty entries (`55 00`, a name the watch could not keep) carry nothing to write back and
 * are left out. Returns the number of POIs written, -2 if the watch's list changed since
 * `expected` (nothing written), or -1 on failure: before the clear nothing was written, after
 * it the POIs may be partly written and the caller's copy is the way back.
 */
int ambit3_set_poi_list(ambit_object_t *object, const uint8_t *expected, size_t expected_len,
                        const uint8_t *new_list, size_t new_len)
{
    static const uint8_t *cur_bodies[AMBIT3_POI_MAX_EXISTING];
    static uint32_t cur_lens[AMBIT3_POI_MAX_EXISTING];
    static const uint8_t *new_bodies[AMBIT3_POI_MAX_EXISTING];
    static uint32_t new_lens[AMBIT3_POI_MAX_EXISTING];
    uint8_t *reply = NULL;
    size_t reply_len = 0;
    int ret = -1;

    if (object == NULL || object->driver_data == NULL) {
        LOG_ERROR("ambit3_set_poi_list: invalid arguments");
        return -1;
    }
    int n_new = ambit3_parse_poi_entries(new_list, new_len, new_bodies, new_lens, AMBIT3_POI_MAX_EXISTING);
    if (n_new < 0) return -1;
    if (new_len > 0 && (new_len < 14 || memcmp(new_list + 6, "SBEM0102", 8) != 0)) {
        LOG_ERROR("ambit3_set_poi_list: the new list is not a POI list");
        return -1;
    }
    for (int i = 0; i < n_new; i++) {
        if (new_lens[i] > AMBIT3_POI_ENTRY_MAX) {
            LOG_ERROR("ambit3_set_poi_list: entry %d is %u bytes, not one POI", i, new_lens[i]);
            return -1;
        }
    }
    int keep = 0;
    for (int i = 0; i < n_new; i++) if (new_lens[i] > 0) keep++;

    int n_cur = ambit3_read_poi_entries(object, cur_bodies, cur_lens, AMBIT3_POI_MAX_EXISTING, &reply, &reply_len);
    if (n_cur < 0) {
        LOG_ERROR("ambit3_set_poi_list: could not read the POIs, nothing written");
        goto out;
    }
    if (reply_len != expected_len || (reply_len > 0 && (expected == NULL || memcmp(reply, expected, reply_len) != 0))) {
        LOG_ERROR("ambit3_set_poi_list: the POIs on the watch changed since they were read, nothing written");
        ret = -2;
        goto out;
    }
    LOG_INFO("ambit3_set_poi_list: %d entries on the watch -> %d POIs", n_cur, keep);

    if (libambit_protocol_command(object, AMBIT3_CMD_NAV_COMMIT, NULL, 0, NULL, NULL, 0) != 0) {
        LOG_ERROR("ambit3_set_poi_list: emptying the POIs (0x0b04) failed, nothing written");
        goto out;
    }
    int cleared = ambit3_poi_count(object);
    if (cleared != 0) {
        LOG_ERROR("ambit3_set_poi_list: the watch still counts %d POIs after 0x0b04, nothing written", cleared);
        goto out;
    }
    for (int i = 0; i < n_new / 2; i++) {
        const uint8_t *tb = new_bodies[i]; new_bodies[i] = new_bodies[n_new - 1 - i]; new_bodies[n_new - 1 - i] = tb;
        uint32_t tl = new_lens[i]; new_lens[i] = new_lens[n_new - 1 - i]; new_lens[n_new - 1 - i] = tl;
    }
    if (ambit3_write_poi_bodies(object, new_bodies, new_lens, (size_t)n_new) != 0) {
        LOG_ERROR("ambit3_set_poi_list: writing the POIs failed after they were emptied");
        goto out;
    }
    int after = ambit3_poi_count(object);
    if (after != keep) {
        LOG_ERROR("ambit3_set_poi_list: the watch counts %d POIs, %d were written", after, keep);
        goto out;
    }
    LOG_INFO("ambit3_set_poi_list: done, %d POIs", keep);
    ret = keep;

out:
    libambit_protocol_free(reply);
    return ret;
}

/*
 * Writes one or more routes to the watch, replacing the whole navigation
 * database (that is how the format works -- SuuntoLink itself has no
 * incremental/append mode either). Preserves the POI store across the
 * write. Returns 0 on success, -1 on failure; on failure the watch's own
 * state is whatever the last completed step left it in -- see the ordering
 * note above for what that implies.
 */
int ambit3_write_route_to_watch(ambit_object_t *object, const ambit3_nav_route_t *routes, size_t route_count)
{
    ambit3_nav_plan_t plan;
    uint8_t *poi_reply = NULL;
    size_t poi_reply_len = 0;
    const uint8_t *poi_bodies[AMBIT3_POI_MAX_EXISTING];
    uint32_t poi_lens[AMBIT3_POI_MAX_EXISTING];
    int poi_count = 0;
    int ret = -1;

    /* route_count 0 writes an empty navigation database (ambit3_navigation_plan_sized ->
     * plan_reset_sized, the SuuntoLink routedelete shape): how the last route is removed. */
    if (object == NULL || object->driver_data == NULL || (routes == NULL && route_count > 0)) {
        LOG_ERROR("ambit3_write_route_to_watch: invalid arguments");
        return -1;
    }

    /* Safety gate: refuse rather than write to the wrong place if this
     * watch's live memory map doesn't match the addresses/sizes the plan
     * assumes. Those constants were only ever verified against one
     * model/firmware combination -- see device_driver_ambit3_navigation.h. */
    if (object->driver_data->memory_maps.initialized == 0) {
        if (get_memory_maps(object) != 0) {
            LOG_ERROR("ambit3_write_route_to_watch: failed to read memory map");
            return -1;
        }
    }
    if (object->driver_data->memory_maps.waypoints.start != AMBIT3_WAYPOINT_BASE ||
        object->driver_data->memory_maps.waypoints.size  != AMBIT3_WAYPOINT_REGION_SIZE ||
        object->driver_data->memory_maps.routes.start    != AMBIT3_ROUTE_BASE ||
        object->driver_data->memory_maps.routes.size     != AMBIT3_ROUTE_REGION_SIZE) {
        LOG_ERROR("ambit3_write_route_to_watch: watch's memory map does not match the "
                  "verified addresses (Waypoints 0x%06x/%u, Routes 0x%06x/%u expected; "
                  "got Waypoints 0x%06x/%u, Routes 0x%06x/%u) -- refusing to write",
                  AMBIT3_WAYPOINT_BASE, AMBIT3_WAYPOINT_REGION_SIZE,
                  AMBIT3_ROUTE_BASE, AMBIT3_ROUTE_REGION_SIZE,
                  object->driver_data->memory_maps.waypoints.start, object->driver_data->memory_maps.waypoints.size,
                  object->driver_data->memory_maps.routes.start, object->driver_data->memory_maps.routes.size);
        return -1;
    }

    /* Waypoint records are 54 bytes on the Traverse / Traverse Alpha (an altitude after the
     * longitude), 52 elsewhere - chosen by model name, as SuuntoLink does. */
    if (ambit3_navigation_plan_sized(routes, route_count,
                                     ambit3_waypoint_len_for_model(object->device_info.model),
                                     &plan) != 0) {
        LOG_ERROR("ambit3_write_route_to_watch: failed to build the navigation plan "
                   "(a limit was likely exceeded -- see AMBIT3_MAX_* in the header)");
        return -1;
    }

    /* 1. Read the POI list before touching anything, then rebuild the write
     * payload with the same entries in REVERSE order -- matching exactly
     * what a real capture shows SuuntoLink doing when it preserves (as
     * opposed to adding) a list. This needs no understanding of a POI's own
     * fields, so it cannot corrupt one it doesn't recognise. */
    poi_count = ambit3_read_poi_entries(object, poi_bodies, poi_lens, AMBIT3_POI_MAX_EXISTING, &poi_reply, &poi_reply_len);
    if (poi_count < 0) {
        LOG_ERROR("ambit3_write_route_to_watch: failed to read the POI list, aborting before any write");
        goto cleanup;
    }
    for (int i = 0; i < poi_count / 2; i++) {
        const uint8_t *tb = poi_bodies[i]; poi_bodies[i] = poi_bodies[poi_count - 1 - i]; poi_bodies[poi_count - 1 - i] = tb;
        uint32_t tl = poi_lens[i]; poi_lens[i] = poi_lens[poi_count - 1 - i]; poi_lens[poi_count - 1 - i] = tl;
    }
    /* poi_count == 0 is a genuinely empty POI store (a real, reachable state on hardware):
     * nothing to put back in step 4. */

    /* 2. Waypoint region, then Route region: writes, then each group's tail. */
    if (ambit3_send_region(object, &plan, AMBIT3_WAYPOINT_BASE, AMBIT3_WAYPOINT_REGION_SIZE, plan.waypoint_hash) != 0) {
        goto cleanup;
    }
    if (ambit3_send_region(object, &plan, AMBIT3_ROUTE_BASE, AMBIT3_ROUTE_REGION_SIZE, plan.route_hash) != 0) {
        goto cleanup;
    }

    /* 3. Commit -- after the writes for Ambit3, unlike openambit's Ambit2 path. */
    if (libambit_protocol_command(object, AMBIT3_CMD_NAV_COMMIT, NULL, 0, NULL, NULL, 0) != 0) {
        LOG_ERROR("ambit3_write_route_to_watch: commit failed -- the watch's navigation "
                   "database may be left in a partially-written state. A backup taken "
                   "before this write is the only way back.");
        goto cleanup;
    }

    /* 4. Restore the POI list. This is what makes step 1 invisible to the user. In entries of
     * at most 254 bytes: one entry for the whole list used the extended 0x55 header once it
     * grew past 254 bytes (about 6 POIs), the form that corrupted the store (2026-08-30). */
    if (poi_count > 0) {
        if (ambit3_write_poi_bodies(object, poi_bodies, poi_lens, (size_t)poi_count) != 0) {
            LOG_ERROR("ambit3_write_route_to_watch: route write succeeded but restoring "
                      "the POI list failed -- the watch's POIs may now be empty");
            goto cleanup;
        }
        int kept = 0;
        for (int i = 0; i < poi_count; i++) if (poi_lens[i] > 0) kept++;
        int after = ambit3_poi_count(object);
        if (after != kept) {
            LOG_WARNING("ambit3_write_route_to_watch: %d POIs put back, the watch counts %d", kept, after);
        }
    }

    LOG_INFO("ambit3_write_route_to_watch: wrote %zu route(s), %u waypoint bytes, %u route bytes",
              route_count, AMBIT3_WAYPOINT_REGION_SIZE, AMBIT3_ROUTE_REGION_SIZE);
    ret = 0;

cleanup:
    libambit_protocol_free(poi_reply);
    ambit3_navigation_plan_free(&plan);
    return ret;
}

/*
 * ─── Reading back (routes, waypoints, POIs) ───────────────────────────────
 *
 * All read-only. Byte-level decoding of what these return is deliberately
 * NOT done here -- it happens in TypeScript (RouteReader.ts), same
 * reasoning as RouteSimplify.ts on the write side: the parsing is pure
 * logic with no protocol/hardware dependency once the raw bytes are in
 * hand, so it's easier to get right and iterate on in JS than to add more
 * native surface for something that can't affect the watch either way.
 */

#define AMBIT3_FLASH_READ_CHUNK 1024

/*
 * Reads `length` bytes starting at `address` into a caller-allocated
 * `out_buffer`, via 0x0b17 ([u32 address][u32 length] out, the same eight
 * bytes then the data back) -- sommet's tools/write_nav.py read_flash(),
 * ported directly; this is what makes reading the Waypoints/Routes regions
 * (and nothing else, this project's own log-reading code has its own
 * chunker with unrelated bounds tied to the log region) possible.
 * Returns 0 on success, -1 on failure (including a short/mismatched reply).
 */
int ambit3_read_flash_region(ambit_object_t *object, uint32_t address, uint32_t length, uint8_t *out_buffer)
{
    if (object == NULL || out_buffer == NULL) return -1;

    uint32_t offset = 0;
    while (offset < length) {
        uint32_t want = (length - offset > AMBIT3_FLASH_READ_CHUNK) ? AMBIT3_FLASH_READ_CHUNK : (length - offset);

        uint8_t send[8];
        uint32_t addr_le = htole32(address + offset);
        uint32_t want_le = htole32(want);
        memcpy(send, &addr_le, 4);
        memcpy(send + 4, &want_le, 4);

        /* Per-chunk retry (2026-08-09): over BLE a region read is many chunks
         * (a 130000-byte routes region is ~127), and the transport very
         * occasionally drops or corrupts a single frame (the intermittent
         * small-frame CRC drops seen on real hardware). Without retry, one bad
         * chunk out of ~127 fails the whole export (-1) — which is exactly why
         * route export failed over BLE while activity read, being far fewer/
         * smaller reads, happened to get through. Retrying the individual chunk
         * a few times makes a large read robust against the occasional drop.
         * Harmless over USB (never retries, since chunk 0 succeeds). */
        int chunk_ok = 0;
        for (int attempt = 0; attempt < 4 && !chunk_ok; attempt++) {
            uint8_t *reply = NULL;
            size_t replylen = 0;
            int ret = libambit_protocol_command(object, ambit_command_log_read, send, sizeof(send), &reply, &replylen, 0);
            if (ret == 0 && replylen == (size_t)want + 8) {
                /* First 8 bytes echo [address][length]; a short/mismatched
                 * replylen is already the practical "something went wrong" signal. */
                memcpy(out_buffer + offset, reply + 8, want);
                libambit_protocol_free(reply);
                chunk_ok = 1;
            } else {
                LOG_WARNING("ambit3_read_flash_region: 0x0b17 at 0x%06x wanted %u, got %zu bytes (ret=%d), attempt %d",
                            address + offset, want, replylen, ret, attempt + 1);
                libambit_protocol_free(reply);
            }
        }
        if (!chunk_ok) return -1;
        offset += want;
    }
    return 0;
}

/*
 * Reads the watch's raw POI list (0x0b24), every page, as one buffer shaped like
 * a single reply, including the 14-byte SBEM0102 prefix -- decoding the individual
 * entries happens in TS, which already needs its own small SBEM0102 reader for this
 * (see RouteReader.ts). An empty store gives *out NULL / *out_len 0 (see
 * ambit3_read_poi_all). Caller frees *out with free(). Returns 0 or -1.
 */
int ambit3_read_poi_list_raw(ambit_object_t *object, uint8_t **out, size_t *out_len)
{
    if (object == NULL || out == NULL || out_len == NULL) return -1;
    return ambit3_read_poi_all(object, out, out_len);
}

/*
 * Reads the watch's raw memory-map reply (0x0b21, four zero bytes), the SBEM0102 blob that
 * declares which flash regions this particular watch has and where they live -
 * "Waypoints\0<hash>\0<u32 start><u32 size>", "Routes\0...", "CustomModes\0...", etc. Every
 * Ambit3-family / Traverse / Kailash watch answers this, and the addresses genuinely differ
 * per device (a Traverse's Routes base is not the Ambit3 Peak's) - which is exactly why the
 * TS side must ask the watch rather than hardcode AMBIT3_*_BASE.
 *
 * This mirrors the companion project's tools/write_nav.py read_memory_map() exactly: the bare
 * 0x0b21 command with a 4-zero payload, reply parsed on the far side (MemoryMap.ts) with the
 * same region-name scan + <II> unpack. The bare command is used (not the get_memory_maps()
 * SBEM-request/waypoint-count-key path) so TS sees the whole reply and can pick up regions the
 * C parser skips (GlonassSGEE, TrackLog). Decoding stays in TS on purpose, same discipline as
 * ambit3_read_poi_list_raw / ambit3_read_object_by_id_raw above.
 *
 * *out is allocated by libambit_protocol_command(); caller frees with plain free() (same
 * equivalence as ambit3_read_poi_list_raw's caller - see its comment in jni_bridge.cpp).
 * Returns 0 on success, -1 on failure.
 */
int ambit3_read_memory_map_raw(ambit_object_t *object, uint8_t **out, size_t *out_len)
{
    if (object == NULL || out == NULL || out_len == NULL) return -1;
    uint8_t zero4[4] = { 0, 0, 0, 0 };
    *out = NULL;
    *out_len = 0;
    return libambit_protocol_command(object, ambit_command_ambit3_memory_map, zero4, sizeof(zero4), out, out_len, 0);
}

#define AMBIT3_CMD_LOG_HEADERS 0x1200

/*
 * Reads a real, live "object by identifier" reply for the given SBEM entry ID via 0x1200 -
 * the same command the companion research project's tools/write_nav.py already uses for
 * sml.DeviceLogBook (entry 0x8d, its own CMD_LOG_HEADERS/LOGBOOK_REQUEST). Confirmed live
 * against a real Suunto Kailash, 2026-08-08 (tools/kailash_history.py): entry 0x67 answers
 * with sml.DeviceHistory (visited cities/countries, travel stats, plus a real "activity
 * mode" logbook bundled in the same reply) - not Kailash-specific at the protocol level,
 * just an entry ID this project happened to find via Kailash first. The request payload
 * shape (4 zero bytes, u16 LE "1", u16 LE "10" = len("SBEM0102") + 2, the magic itself, then
 * the 2-byte entry ID) is fixed across every use of this command seen so far - only the
 * final entry_id byte varies.
 *
 * *out is allocated by libambit_protocol_command(); caller frees with plain free() (same as
 * ambit3_read_poi_list_raw's own caller, jni_bridge.cpp - see its own comment on why plain
 * free() is equivalent to libambit_protocol_free() here). Returns 0 on success, -1 on
 * failure.
 */
int ambit3_read_object_by_id_raw(ambit_object_t *object, uint8_t entry_id, uint8_t **out, size_t *out_len)
{
    if (object == NULL || out == NULL || out_len == NULL) return -1;
    uint8_t request[] = {
        0x00, 0x00, 0x00, 0x00,
        0x01, 0x00,
        0x0a, 0x00,
        'S', 'B', 'E', 'M', '0', '1', '0', '2',
        entry_id, 0x00,
    };
    *out = NULL;
    *out_len = 0;
    return libambit_protocol_command(object, AMBIT3_CMD_LOG_HEADERS, request, sizeof(request), out, out_len, 0);
}

#define AMBIT3_CMD_SETTINGS_READ  0x1100
#define AMBIT3_CMD_SETTINGS_WRITE 0x1101

/*
 * Reads the watch's real sml.DeviceSettings tree (0x1100, four zero bytes) - the same
 * command the companion research project's tools/write_nav.py/settings_write.py already
 * use. *out is allocated by libambit_protocol_command(); caller frees with plain free()
 * (same equivalence as ambit3_read_poi_list_raw's own caller - see its comment). Returns 0
 * on success, -1 on failure.
 */
int ambit3_read_settings_raw(ambit_object_t *object, uint8_t **out, size_t *out_len)
{
    if (object == NULL || out == NULL || out_len == NULL) return -1;
    uint8_t zero4[4] = { 0, 0, 0, 0 };
    *out = NULL;
    *out_len = 0;
    return libambit_protocol_command(object, AMBIT3_CMD_SETTINGS_READ, zero4, sizeof(zero4), out, out_len, 0);
}

/*
 * Writes a real sml.DeviceSettings blob back via 0x1101 - real, hardware-confirmed
 * 2026-08-08 (tools/settings_write.py's own docstring): André confirmed on a real
 * connected Ambit3 Peak's own screen that flipping the Display.Invert byte in a blob
 * obtained from ambit3_read_settings_raw() and writing it back here visibly switched the
 * display Light -> Dark. `data`/`datalen` should be the *entire* settings blob (a single
 * changed byte's worth of edits, not a partial tree) - the caller is responsible for
 * reading first, patching the one field it wants to change, and passing the whole thing
 * back, matching how the reference SuuntoLink client itself works
 * (EmuDevice::saveSettings, see custom_modes_andre.md). The watch replies with an empty
 * body on success (confirmed live) - *out / *out_len will be 0/NULL in that case, which is
 * not itself a failure; callers should re-read via ambit3_read_settings_raw() to confirm
 * a write actually took effect, the same "prove it, don't just trust the ACK" rule
 * settings_write.py's own write_one() already applies. Returns 0 on success, -1 on
 * failure.
 */
int ambit3_write_settings_raw(ambit_object_t *object, const uint8_t *data, size_t datalen, uint8_t **out, size_t *out_len)
{
    if (object == NULL || data == NULL || out == NULL || out_len == NULL) return -1;
    *out = NULL;
    *out_len = 0;
    return libambit_protocol_command(object, AMBIT3_CMD_SETTINGS_WRITE, (uint8_t *)data, datalen, out, out_len, 0);
}

#define AMBIT3_CUSTOM_MODES_BASE 0x002000
#define AMBIT3_CUSTOM_MODES_SIZE 12288

/*
 * The CustomModes region base is device-specific: the Traverse keeps it at a DIFFERENT
 * offset than the Ambit3 Peak (confirmed - tools/custom_modes.py resolves it from the
 * watch's own 0x0b21 memory map for exactly this reason). Reading/writing the hardcoded
 * Ambit3 base 0x002000 on a Traverse hits the wrong flash - it breaks the sport-modes read
 * AND faults the watch firmware into a reset (seen live 2026-08-17: repeated Traverse resets
 * while connected). Resolve the real base from the watch's memory map (populating it on
 * demand exactly like the Routes/Waypoints per-device read already does), falling back to the
 * hardcoded Ambit3 base only if the map has no CustomModes entry. The region SIZE is a fixed
 * 12288 across the family, so only the base varies.
 */
static uint32_t ambit3_custom_modes_base(ambit_object_t *object)
{
    if (object->driver_data->memory_maps.initialized == 0) {
        if (get_memory_maps(object) != 0) return AMBIT3_CUSTOM_MODES_BASE;
    }
    uint32_t base = object->driver_data->memory_maps.sport_modes.start;
    return base != 0 ? base : AMBIT3_CUSTOM_MODES_BASE;
}

/*
 * Reads the watch's real CustomModes flash region (sport modes) - the same 12288-byte
 * region the companion research project's tools/custom_modes.py already reads via 0x0b17.
 * Thin wrapper over the existing generic ambit3_read_flash_region(). Returns 0 on success,
 * -1 on failure (including a short/failed read - out_buffer is only valid on success).
 */
int ambit3_read_custom_modes_raw(ambit_object_t *object, uint8_t *out_buffer)
{
    if (object == NULL || out_buffer == NULL) return -1;
    return ambit3_read_flash_region(object, ambit3_custom_modes_base(object), AMBIT3_CUSTOM_MODES_SIZE, out_buffer);
}

/* DEVICE_CUSTOM - the BXml root tag every real CustomModes region starts with (a 4-byte
 * header: [u16 LE tag_id][u16 LE length], content follows). Same value as the companion
 * Python project's custom_modes.py / this app's own CustomModesReader.ts (both named
 * DEVICE_CUSTOM = 0x003) and Android's Kotlin/JNI callers never had their own separate copy
 * of this constant to keep in sync - it's declared once, here. */
#define AMBIT3_DEVICE_CUSTOM_TAG 0x0003

/*
 * Real bug, found + fixed 2026-08-09 ("DESKTOP version got all fixes that android app never
 * got" - freshly re-verified live against a real connected Ambit3 Peak the same day, see
 * this project's own verify_finding28.py-style check: WRITTEN hash matched the watch's own
 * 0x0b21-reported CustomModes hash exactly, PADDED did not). This function used to hash and
 * write the *entire* AMBIT3_CUSTOM_MODES_SIZE (12288) buffer and always send
 * AMBIT3_CMD_NAV_COMMIT afterward - both now-confirmed wrong:
 *
 *   - training_program_andre.md Finding 28 (from 4 real SuuntoLink app-install captures,
 *     byte-exact SHA256 proof against the watch's own self-reported hash): the watch only
 *     ever hashes the *used extent* of the region (the DEVICE_CUSTOM root tag's own declared
 *     length + its 4-byte header, ~5.9 KB here, not the full padded 12288) - hashing/writing
 *     the full padded region is the confirmed cause of "err:62 on all sport modes" after a
 *     cold boot, because the watch recomputes the used-extent hash on boot and it never
 *     matched a full-region hash.
 *   - Finding 27 (same 4 captures): real SuuntoLink NEVER sends CMD_NAV_COMMIT (0x0b04) for
 *     Apps or CustomModes writes - that command is specifically the *navigation database*
 *     commit (Routes/Waypoints); sending it here was this project's own addition, copied
 *     from the Routes/Waypoints path by analogy, and is the strongest suspect for the
 *     "connect to Moveslink" / needs-restart / clock-reset state seen on real hardware after
 *     installs. The real per-region finalization is the CMD_DATA_TAIL hash itself.
 *
 * This is the exact same fix desktop's own tools/ambit_format.py / custom_modes.py already
 * got on 2026-08-09 - this native Android path never received it until now, despite this
 * function's own prior header comment (now corrected) claiming the desktop mechanism was
 * "fully confirmed working" - it was confirmed working for the *pre-Finding-28* method,
 * which desktop has since moved off of for exactly this reason.
 *
 * `datalen` still MUST be the full real region size (AMBIT3_CUSTOM_MODES_SIZE) - the caller
 * is still responsible for reading the current region first
 * (ambit3_read_custom_modes_raw()), patching only the bytes it wants to change, and passing
 * the *whole* buffer back (never a partial-region read/patch). This function itself now
 * works out how much of that buffer is real (the used extent) and only ever writes/hashes
 * that part - the caller does not need to know or compute the extent itself.
 *
 * Returns 0 on success, -1 on failure (including a malformed/implausible root tag - see the
 * bounds checks below, this project's own "bounds-check before write" rule: a real flash
 * write is downstream of this, so a garbage extent must never reach it).
 */
int ambit3_write_custom_modes_raw(ambit_object_t *object, const uint8_t *data, size_t datalen)
{
    if (object == NULL || data == NULL || datalen != AMBIT3_CUSTOM_MODES_SIZE) return -1;

    /* used_extent(): [u16 LE tag_id][u16 LE length] at offset 0, extent = 4 + length. Same
     * real check custom_modes.py's own used_extent() makes, not a new one invented here. */
    if (datalen < 4) return -1;
    uint16_t root_tag = (uint16_t)(data[0] | (data[1] << 8));
    uint16_t root_len = (uint16_t)(data[2] | (data[3] << 8));
    if (root_tag != AMBIT3_DEVICE_CUSTOM_TAG) {
        LOG_ERROR("ambit3_write_custom_modes_raw: root tag 0x%04x != DEVICE_CUSTOM (0x%04x) "
                   "- refusing to write, this doesn't look like a real CustomModes image",
                   root_tag, AMBIT3_DEVICE_CUSTOM_TAG);
        return -1;
    }
    size_t extent = 4 + (size_t)root_len;
    if (extent > datalen) {
        LOG_ERROR("ambit3_write_custom_modes_raw: declared extent %zu exceeds buffer size %zu "
                   "- refusing to write", extent, datalen);
        return -1;
    }

    /* Device-specific base (Traverse != Ambit3 Peak) - the same resolution the read uses, so a
     * write never lands on the wrong flash region (which would corrupt the watch). */
    uint32_t cm_base = ambit3_custom_modes_base(object);

    if (libambit_pmem20_data_write(&object->driver_data->pmem20, cm_base, data, extent) != 0) {
        LOG_ERROR("ambit3_write_custom_modes_raw: region write failed");
        return -1;
    }

    uint8_t hash[32];
    sha256(data, extent, hash);
    char hash_hex[65];
    for (int i = 0; i < 32; i++) {
        sprintf(hash_hex + i * 2, "%02X", hash[i]);
    }
    hash_hex[64] = '\0';

    uint8_t tail[4 + 4 + 64];
    uint32_t addr_le = htole32(cm_base);
    memcpy(tail, &addr_le, 4);
    memset(tail + 4, 0, 4);
    memcpy(tail + 8, hash_hex, 64);
    if (libambit_protocol_command(object, ambit_command_data_tail_len, tail, sizeof(tail), NULL, NULL, 0) != 0) {
        LOG_ERROR("ambit3_write_custom_modes_raw: tail send failed");
        return -1;
    }

    /* No CMD_NAV_COMMIT here - Finding 27, see this function's own header comment. */
    return 0;
}

/* Generic region writer for the experimental App-Zone / Training-program paths (2026-08-14).
 * Same proven finalization as ambit3_write_custom_modes_raw above (Findings 26/27): write the
 * used extent via pmem20, SHA256 that SAME extent, send the 0x0b18 data-tail carrying that
 * hash, and send NO commit (0x0b04). The difference is only that base + extent are parameters
 * and there is no root-tag check: unlike CustomModes (always DEVICE_CUSTOM), the Apps and
 * TrainingProgram regions have different root structures, so the CALLER (the per-region TS
 * builder that owns the format) decides the base, builds the exact image, and passes how many
 * bytes are the real used extent. That keeps this a dumb, auditable primitive whose byte-exact
 * correctness is proven per-feature in TS against real SuuntoLink captures - the same standard
 * as the sport-mode path, not a new trust assumption. */
int ambit3_write_region_raw(ambit_object_t *object, uint32_t base, const uint8_t *data, size_t extent)
{
    if (object == NULL || data == NULL || extent < 4) return -1;

    if (libambit_pmem20_data_write(&object->driver_data->pmem20, base, data, extent) != 0) {
        LOG_ERROR("ambit3_write_region_raw: region write failed at 0x%06x", base);
        return -1;
    }

    uint8_t hash[32];
    sha256(data, extent, hash);
    char hash_hex[65];
    for (int i = 0; i < 32; i++) {
        sprintf(hash_hex + i * 2, "%02X", hash[i]);
    }
    hash_hex[64] = '\0';

    /* Reuse the same tail helper Routes/Waypoints finalize with: [u32 LE base][u32 0]
     * [64 ASCII uppercase hex hash] over 0x0b18. No commit. */
    return ambit3_send_region_tail(object, base, hash_hex);
}

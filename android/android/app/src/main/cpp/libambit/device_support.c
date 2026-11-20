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
#include "device_support.h"
#include "device_driver.h"

#include <string.h>
#include <stdio.h>

/*
 * Local definitions
 */
#define SUUNTO_USB_VENDOR_ID 0x1493

typedef struct int_known_device_s {
    uint16_t vid;
    uint16_t pid;
    char *model;
    uint8_t min_sw_version[4];
    ambit_known_device_t public_info;
} int_known_device_t;

/*
 * Static functions
 */

/*
 * Static variables
 */
static int_known_device_t known_devices[] = {
    // Real bug, found live on a real connected Kailash (2026-08-09, "tablet works ok with
    // ambit 3 but not kailash") - this is the ORIGINAL openambit device_support.c table
    // (unmodified upstream, (C) 2014), which predates Kailash's release and never had an
    // entry for it at all. libambit_new_from_fd() -> libambit_device_support_find_first()
    // walks exactly this table (vid+pid+supported+driver match, no firmware-version
    // filtering for this path) - with no row here, it always returned NULL for Kailash,
    // "libambit initialization failed (VID=0x1493 PID=0x2a)" every time, even though the
    // Kotlin-side SUUNTO_PID_NAMES map (AmbitUsbModule.kt, a DIFFERENT table, display-name/
    // permission-filter only) already had Kailash correctly registered - that one only
    // decides whether Android's own USB-permission dialog and device_filter.xml intent
    // matching accept the device, not whether libambit itself can actually open it. Same
    // real class of "missing from one of several separate device tables" bug this project's
    // own history already hit twice (write_nav.py's PRODUCT_IDS, then AmbitUsbModule.kt's
    // own SUUNTO_PID_NAMES) - this native table was the third place, missed until now.
    // ambit_device_driver_ambit3, not a new driver: Kailash answers the same USB init +
    // 0x0000 device-info commands every Ambit3-family watch does (confirmed throughout this
    // project's own real Kailash work - device history/tracklog/settings/CustomModes reads
    // all already assume this same transport). min_sw_version/komposti_version don't gate
    // libambit_device_support_find_first() at all (only the separate, model-string-matching
    // libambit_device_support_find() reads them) - {0} here matches the Ambit3-family rows'
    // own "accept any version" style below, not a real Kailash-specific value.
    //
    // UPDATE, same day: registering this row got USB-Kailash PAST the "unsupported" error
    // and into a real SIGABRT crash inside libambit_new_from_fd/device_info_get - confirmed
    // via a genuinely rebuilt binary (different BuildId each time), not a stale-build
    // artifact. Two real bugs were found and fixed in libambit.c along the way (a
    // NULL-deref-before-check on komposti_version, and an Ambit3-only "compact serial" quirk
    // command now excluded for this PID) but the crash persisted after both - disabled
    // (supported: false) at the time, a clean "unsupported" rejection instead of a crash.
    //
    // UPDATE, 2026-08-10: tried komposti_version 02 48 03 00 (tools/write_nav.py's own real,
    // hardware-proven device_info request payload, vs this row's previous 02 04 59 00 - the
    // shared value every real Ambit3-family row below uses, copied onto Kailash without real
    // evidence it applies there too). Rebuilt, reinstalled, tested on the real connected
    // Kailash - the crash is UNCHANGED. Real new information though, not a wasted test: the
    // tombstone's backtrace this time is abort -> 2 inlined frames -> libambit_new_from_fd
    // itself (not a further call into device_info_get as such - they may be the same inlined
    // code, optimized build, no line numbers survive) - so either the komposti_version bytes
    // were never the real trigger, or they're a real bug that isn't THE bug. The actual
    // divergence is still somewhere in the live USB device-info exchange with Kailash's real
    // firmware, unidentified.
    //
    // UPDATE, 2026-08-10, continued: got real ground truth instead of guessing further -
    // ran tools/write_nav.py directly against this exact connected Kailash (read-only
    // device_info query) and captured the real reply: 48 bytes, model "Hoopoe", real
    // serial, fw_version 02 00 05 00. Checked byte-for-byte against device_info_get()'s own
    // parsing in libambit.c - every field lands exactly where that code expects it, well
    // within bounds, and fw_version[1]=0 means the compact-serial quirk block can't even
    // fire (needs >=4). Nothing here explains a crash. Re-enabled with checkpoint LOG_INFO
    // calls added throughout libambit.c/libambit_android.c/device_driver_ambit3.c (see
    // their own "kailash-debug" comments) - the crash tombstone's own symbol resolution
    // isn't precise enough (2 unresolved inlined frames), so the next real crash's last
    // logcat line before the SIGABRT is what actually pinpoints it now, not more inference.
    { SUUNTO_USB_VENDOR_ID, 0x002a, "Hoopoe", {0x00,0x00,0x00,0x00}, { "Suunto Kailash", true, &ambit_device_driver_ambit3, 0x0400, {0x02,0x48,0x03,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x002d, "Loon", {0x01,0x00,0x04,0x0}, { "Suunto Traverse Alpha", true, &ambit_device_driver_ambit3, 0x0400, {0x02,0x04,0x59,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x002c, "Kaka", {0x01,0x00,0x1b,0x00}, { "Suunto Ambit3 Vertical", true, &ambit_device_driver_ambit3, 0x0400, {0x02,0x04,0x59,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x002b, "Jabiru", {0x01,0x00,0x04,0x00}, { "Suunto Traverse", true, &ambit_device_driver_ambit3, 0x0400, {0x02,0x04,0x59,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x001e, "Ibisbill", {0x00,0x00,0x00,0x00}, { "Suunto Ambit3 Run", true, &ambit_device_driver_ambit3, 0x0400, {0x02,0x04,0x59,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x001c, "Finch", {0x00,0x00,0x00,0x00}, { "Suunto Ambit3 Sport", true, &ambit_device_driver_ambit3, 0x0400, {0x02,0x04,0x59,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x001b, "Emu", {0x00,0x00,0x00,0x00}, { "Suunto Ambit3 Peak", true, &ambit_device_driver_ambit3, 0x0400, {0x02,0x04,0x59,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x001d, "Greentit", {0x00,0x00,0x00,0x00}, { "Suunto Ambit2 R", true, &ambit_device_driver_ambit, 0x0400, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x001a, "Colibri", {0x01,0x01,0x02,0x00}, { "Suunto Ambit2 S", true, &ambit_device_driver_ambit, 0x0400, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x0019, "Duck", {0x01,0x01,0x02,0x00}, { "Suunto Ambit2", true, &ambit_device_driver_ambit, 0x0400, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x001a, "Colibri", {0x00,0x02,0x03,0x00}, { "Suunto Ambit2 S", false, NULL, 0x0400, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x0019, "Duck", {0x00,0x02,0x03,0x00}, { "Suunto Ambit2", false, NULL, 0x0400, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x001a, "Colibri", {0x00,0x02,0x02,0x00}, { "Suunto Ambit2 S (up to 0.2.2)", false, NULL, 0x0200, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x0019, "Duck", {0x00,0x02,0x02,0x00}, { "Suunto Ambit2 (up to 0.2.2)", false, NULL, 0x0200, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x0010, "Bluebird", {0x02,0x01,0x00,0x00}, { "Suunto Ambit", true, &ambit_device_driver_ambit, 0x0200, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x0010, "Bluebird", {0x01,0x09,0x00,0x00}, { "Suunto Ambit", false, NULL, 0x0200, {0x02,0x00,0x2d,0x00} } }, /* First with PMEM 2.0, {0x02,0x00,0x2d,0x00} !? */
    { SUUNTO_USB_VENDOR_ID, 0x0010, "Bluebird", {0x01,0x06,0x00,0x00}, { "Suunto Ambit", false, NULL, 0, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x0010, "Bluebird", {0x01,0x01,0x00,0x00}, { "Suunto Ambit", false, NULL, 0, {0x02,0x00,0x2d,0x00} } },
    { SUUNTO_USB_VENDOR_ID, 0x0010, "Bluebird", {0x00,0x00,0x00,0x00}, { "Suunto Ambit", false, NULL, 0, {0x02,0x00,0x2d,0x00} } },
    { 0x0000, 0x0000, NULL, {0x00,0x00,0x00,0x00}, { NULL, false, NULL, 0, {0x00,0x00,0x00,0x00} } }
};

bool libambit_device_support_known(uint16_t vendor_id, uint16_t product_id)
{
    int i;

    for (i=0; i<sizeof(known_devices)/sizeof(known_devices[0]); i++) {
        if (vendor_id == known_devices[i].vid && product_id == known_devices[i].pid) {
            // Found at least one row with the correct device
            return true;
        }
    }

    return false;
}

const ambit_known_device_t *libambit_device_support_find(uint16_t vendor_id, uint16_t product_id, const char *model, const uint8_t *fw_version)
{
    ambit_known_device_t *device = NULL;
    int i;

    for (i=0; i<sizeof(known_devices)/sizeof(known_devices[0]); i++) {
        if (vendor_id == known_devices[i].vid &&
            product_id == known_devices[i].pid &&
            strcmp(model, known_devices[i].model) == 0 &&
            libambit_fw_version_number(fw_version) >= libambit_fw_version_number(known_devices[i].min_sw_version)) {
            // Found matching entry, reset to this one!
            device = &known_devices[i].public_info;
            break;
        }
    }

    return device;
}

const ambit_known_device_t *libambit_device_support_find_first(uint16_t vendor_id, uint16_t product_id)
{
    int i;
    for (i = 0; i < (int)(sizeof(known_devices)/sizeof(known_devices[0])); i++) {
        if (vendor_id == known_devices[i].vid &&
            product_id == known_devices[i].pid &&
            known_devices[i].public_info.supported &&
            known_devices[i].public_info.driver != NULL) {
            return &known_devices[i].public_info;
        }
    }
    return NULL;
}

const uint8_t *libambit_device_komposti(uint16_t vendor_id, uint16_t product_id, uint8_t next)
{
    int i;
    for (i=0; i<sizeof(known_devices)/sizeof(known_devices[0]); i++) {
        if(vendor_id == known_devices[i].vid &&
            product_id == known_devices[i].pid) {
                if(next == 0) {
                    printf("libambit_komposti: %x %x %x %x\n", known_devices[i].public_info.komposti_version[0], known_devices[i].public_info.komposti_version[1], known_devices[i].public_info.komposti_version[2], known_devices[i].public_info.komposti_version[3]);
                    return known_devices[i].public_info.komposti_version;
                } else {
                    --next;
                }
        }
    }

    return NULL;
}

uint32_t libambit_fw_version_number(const uint8_t version[4])
{
    return (  (version[0] << 24)
            | (version[1] << 16)
            | (version[2] <<  0)
            | (version[3] <<  8));
}

/* Over Bluetooth the product id was only guessed from the advertised name, which is often
 * missing (it rides in the scan response) and didn't match a Traverse Alpha ("TraverseA ") or an
 * Ambit3 Vertical ("Ambit3V "): both ran as an Ambit3 Sport with that model's entry numbers, so
 * logbook and POI reads went to the wrong entries (GitHub #25). The hello names the model. Hoopoe
 * (Kailash) keeps its guess: it is driven as an Ambit3, which is what was validated live. */
uint16_t libambit_device_support_ble_pid_for_model(const char *model, uint16_t fallback)
{
    static const struct { const char *model; uint16_t pid; } kModels[] = {
        { "Emu", 0x001b }, { "Finch", 0x001c }, { "Ibisbill", 0x001e },
        { "Jabiru", 0x002b }, { "Kaka", 0x002c }, { "Loon", 0x002d },
    };
    if (model == NULL) return fallback;
    for (size_t i = 0; i < sizeof(kModels) / sizeof(kModels[0]); i++)
        if (strcmp(model, kModels[i].model) == 0)
            return kModels[i].pid;
    return fallback;
}

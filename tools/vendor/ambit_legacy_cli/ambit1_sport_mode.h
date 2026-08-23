/* Ambit1 (product_id 0x0010) sport modes - see ambit1_sport_mode.c for why this is a separate,
 * device-guarded module and not a branch inside the shared legacy CLI. */
#ifndef AMBIT1_SPORT_MODE_H
#define AMBIT1_SPORT_MODE_H

#include <stdint.h>
#include "libambit.h"

/* Hard device guard: everything here assumes the Ambit1's 76-byte settings blob. */
int ambit1_guard_ok(const ambit_device_info_t *info);

/* Read-only raw region read (0x0b17 at 0x2000). Returns bytes read, or -1. */
int ambit1_read_region(ambit_object_t *dev, uint8_t *out, uint32_t max);

/* Locates each mode's 76-byte settings blob. Returns the mode count. */
int ambit1_find_modes(const uint8_t *buf, uint32_t len, uint32_t *offsets, int max_modes);

/* Same, plus each mode's body span (offset and length) - needed by anything that walks a
 * mode's displays rather than just its settings. */
int ambit1_find_modes_ex(const uint8_t *buf, uint32_t len, uint32_t *offsets,
                          uint32_t *body_off, uint32_t *body_len, int max_modes);

/* `sport-mode-read`: decode every mode to JSON. Never writes. */
int ambit1_cmd_read(ambit_object_t *dev, const ambit_device_info_t *info);

/* `sport-mode-patch`: read-modify-write. dry_run skips the write; dump_path (optional)
 * receives the exact bytes that would be sent, for offline diffing. */
int ambit1_cmd_patch(ambit_object_t *dev, const ambit_device_info_t *info,
                      const char *patch_path, int dry_run, const char *dump_path);

#endif /* AMBIT1_SPORT_MODE_H */

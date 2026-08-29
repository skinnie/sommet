/*
 * hid_stub.c — iOS build only.
 *
 * libambit.c and protocol.c reference the hidapi USB API (hid_open, hid_write,
 * …). On Android those symbols come from hid-android.c; on iOS there is no USB
 * transport at all (Ambit1/2 are USB-only and unreachable from iOS — see
 * sommet-ios-ble-central-model). These stubs exist purely to satisfy the linker
 * for the shared TUs; the BLE path (protocol_ble.c) never calls them, and the
 * driver dispatch in protocol.c takes the BLE branch for every iOS object.
 */
/* iOS-only TU. The Android CMake globs the libambit *.c files; on Android the real
 * hid_* implementations come from hid-android.c, so guard this stub to an empty
 * TU there to avoid duplicate symbols. */
#ifndef __ANDROID__

#include "hidapi/hidapi.h"
#include <stddef.h>

int hid_init(void) { return 0; }
int hid_exit(void) { return 0; }
struct hid_device_info *hid_enumerate(unsigned short v, unsigned short p) { (void)v; (void)p; return NULL; }
void hid_free_enumeration(struct hid_device_info *devs) { (void)devs; }
hid_device *hid_open(unsigned short v, unsigned short p, const wchar_t *s) { (void)v; (void)p; (void)s; return NULL; }
hid_device *hid_open_path(const char *path) { (void)path; return NULL; }
int hid_write(hid_device *d, const unsigned char *data, size_t len) { (void)d; (void)data; (void)len; return -1; }
int hid_read_timeout(hid_device *d, unsigned char *data, size_t len, int ms) { (void)d; (void)data; (void)len; (void)ms; return -1; }
int hid_read(hid_device *d, unsigned char *data, size_t len) { (void)d; (void)data; (void)len; return -1; }
int hid_set_nonblocking(hid_device *d, int nb) { (void)d; (void)nb; return -1; }
int hid_send_feature_report(hid_device *d, const unsigned char *data, size_t len) { (void)d; (void)data; (void)len; return -1; }
int hid_get_feature_report(hid_device *d, unsigned char *data, size_t len) { (void)d; (void)data; (void)len; return -1; }
void hid_close(hid_device *d) { (void)d; }
int hid_get_manufacturer_string(hid_device *d, wchar_t *s, size_t n) { (void)d; (void)s; (void)n; return -1; }
int hid_get_product_string(hid_device *d, wchar_t *s, size_t n) { (void)d; (void)s; (void)n; return -1; }
int hid_get_serial_number_string(hid_device *d, wchar_t *s, size_t n) { (void)d; (void)s; (void)n; return -1; }
int hid_get_indexed_string(hid_device *d, int idx, wchar_t *s, size_t n) { (void)d; (void)idx; (void)s; (void)n; return -1; }
const wchar_t *hid_error(hid_device *d) { (void)d; return NULL; }

#endif /* !__ANDROID__ */

/*
 * libambit_ios.c — iOS/iPadOS BLE transport glue for libambit.
 *
 * The iOS twin of libambit_android.c's BLE path. Where Android bridges the
 * outgoing GATT write through JNI (bleWriteChunk) and routes incoming
 * notifications through jni_bridge.cpp's g_device + pre-init stash, iOS uses a
 * plain C write callback (backed by CBPeripheral writeValue in the Swift
 * module) and keeps the notify router + pre-init RX stash here so the
 * Objective-C++ bridge only ever calls simple C entry points.
 *
 * Architecture note (2026-08-28): the official Suunto/7R iOS app connects to
 * the watch as a BLE *central* (CBCentralManager) — the watch hosts the NSP
 * service as peripheral — the opposite GATT role from the Android build, which
 * hosts the service itself. This does NOT change the NSP layer: the watch still
 * drives the conversation (pushes 0x1201 / 0x0002, phone answers 0x0000), so
 * the watch-driven handshake in protocol_ble.c applies unchanged. Only the GATT
 * plumbing differs, and that lives in the Swift module, not here.
 */

/* iOS-only translation unit. The Android CMake globs the libambit *.c files, so guard the
 * whole file to an empty TU on Android — its libambit_ble_transport_new and
 * hid-free constructor would otherwise clash with the JNI transport in
 * protocol_ble.c / libambit_android.c. */
#ifndef __ANDROID__

#include "libambit_int.h"
#include "device_support.h"
#include "device_driver.h"

#include <pthread.h>
#include <stdlib.h>
#include <string.h>

/* Framing entry points implemented in protocol_ble.c. */
extern void ambit_ble_on_notify(ambit_object_t *object, const uint8_t *data, size_t len);
extern int  libambit_ble_handshake_device_info(ambit_object_t *object, ambit_device_info_t *info);

/* ─── Active object + pre-init RX stash ──────────────────────────────────────
 * Mirrors jni_bridge.cpp's g_device / g_rx_* machinery. The watch's opening
 * frame can reach the notify callback the instant it subscribes — before
 * libambit_new_from_ble_ios() has published the object. Until the handshake is
 * armed we stash incoming bytes in order rather than dropping them (the Kailash
 * sends its 0x0002 hello exactly once, so a dropped hello would hang the
 * handshake for the full timeout). All three helpers share one mutex, so a
 * notification racing the ready-flip is serialized, never lost or reordered. */
static pthread_mutex_t g_rx_mtx = PTHREAD_MUTEX_INITIALIZER;
static ambit_object_t *g_active_object = NULL;
static uint8_t *g_rx_stash = NULL;
static size_t   g_rx_stash_len = 0;
static size_t   g_rx_stash_cap = 0;
static int      g_rx_ready = 0;   /* 0: stash incoming bytes; 1: feed live */

void ambit_ios_ble_set_active_object(ambit_object_t *obj)
{
    pthread_mutex_lock(&g_rx_mtx);
    g_active_object = obj;
    pthread_mutex_unlock(&g_rx_mtx);
}

/* The device the BLE handshake connected — the iOS equivalent of jni_bridge.cpp's
 * g_device. The AmbitCore data bridge (AmbitCore.mm) reads it so every watch
 * operation acts on the same shared object, exactly as Android's data methods do. */
ambit_object_t *ambit_ios_active_device(void)
{
    ambit_object_t *obj;
    pthread_mutex_lock(&g_rx_mtx);
    obj = g_active_object;
    pthread_mutex_unlock(&g_rx_mtx);
    return obj;
}

void ambit_ios_ble_reset_rx_stash(void)
{
    pthread_mutex_lock(&g_rx_mtx);
    g_rx_ready = 0;
    g_rx_stash_len = 0;
    pthread_mutex_unlock(&g_rx_mtx);
}

/* Called by protocol_ble.c's handshake once the object + handshake_mode are
 * live: replay the parked bytes into the framing layer, then switch to live. */
void ambit_ble_flush_rx_stash(void)
{
    pthread_mutex_lock(&g_rx_mtx);
    if (g_active_object && g_rx_stash_len > 0) {
        ambit_ble_on_notify(g_active_object, g_rx_stash, g_rx_stash_len);
    }
    g_rx_stash_len = 0;
    g_rx_ready = 1;
    pthread_mutex_unlock(&g_rx_mtx);
}

/* Bridge entry point: the Swift module calls this from
 * peripheral(_:didUpdateValueFor:) with each raw GATT notification chunk. */
void ambit_ios_ble_on_notify(const uint8_t *data, size_t len)
{
    pthread_mutex_lock(&g_rx_mtx);
    if (g_rx_ready && g_active_object) {
        pthread_mutex_unlock(&g_rx_mtx);
        ambit_ble_on_notify(g_active_object, data, len);
        return;
    }
    /* Not armed yet — stash in order. */
    if (g_rx_stash_len + len > g_rx_stash_cap) {
        size_t new_cap = g_rx_stash_cap ? g_rx_stash_cap * 2 : 512;
        while (new_cap < g_rx_stash_len + len) new_cap *= 2;
        uint8_t *grown = (uint8_t *)realloc(g_rx_stash, new_cap);
        if (!grown) { pthread_mutex_unlock(&g_rx_mtx); return; }
        g_rx_stash = grown;
        g_rx_stash_cap = new_cap;
    }
    memcpy(g_rx_stash + g_rx_stash_len, data, len);
    g_rx_stash_len += len;
    pthread_mutex_unlock(&g_rx_mtx);
}

/* ─── Constructor ────────────────────────────────────────────────────────────
 * Twin of libambit_android.c's libambit_new_common()/libambit_new_from_ble().
 * Kept local (static) rather than shared because the Android copy lives in the
 * Android-only translation unit. */
static int ios_new_common(ambit_object_t *object, uint16_t vid, uint16_t pid,
                          const ambit_known_device_t **out_known)
{
    const ambit_known_device_t *known = libambit_device_support_find_first(vid, pid);
    if (!known || !known->supported || !known->driver) return -1;

    object->device_info.vendor_id    = vid;
    object->device_info.product_id   = pid;
    object->device_info.is_supported = true;
    memcpy(&object->device_info.komposti_version, &known->komposti_version,
           sizeof(known->komposti_version));
    object->driver = known->driver;
    *out_known = known;
    return 0;
}

ambit_object_t *libambit_new_from_ble_ios(ambit_ble_write_fn write_cb, void *write_ud,
                                          uint16_t vid, uint16_t pid)
{
    ambit_object_t *object = (ambit_object_t *)calloc(1, sizeof(*object));
    if (!object) return NULL;

    if (libambit_ble_transport_new(object, write_cb, write_ud) != 0) {
        free(object);
        return NULL;
    }

    const ambit_known_device_t *known = NULL;
    if (ios_new_common(object, vid, pid, &known) != 0) {
        libambit_ble_transport_close(object);
        free(object);
        return NULL;
    }

    /* Publish to the notify router BEFORE the handshake, which waits for the
     * watch's pushed frames — they only reach the framing layer via
     * ambit_ios_ble_on_notify -> g_active_object. (Same ordering constraint as
     * the Android jni_ble_set_active_object call.) */
    ambit_ios_ble_set_active_object(object);
    if (libambit_ble_handshake_device_info(object, &object->device_info) != 0) {
        ambit_ios_ble_set_active_object(NULL);
        libambit_ble_transport_close(object);
        free(object);
        return NULL;
    }

    if (object->driver->init != NULL) {
        object->driver->init(object, known->driver_param);
    }
    return object;
}

#endif /* !__ANDROID__ */

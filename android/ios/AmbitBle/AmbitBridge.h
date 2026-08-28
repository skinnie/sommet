/*
 * AmbitBridge.h — C surface of libambit exposed to Swift (via the module's
 * bridging header). Kept intentionally minimal and JNI/hidapi-free so it can be
 * imported into Swift without dragging in libambit_int.h. ambit_object_t is
 * opaque here; only the iOS BLE entry points (libambit_ios.c) and libambit_close
 * (libambit.c) are needed by the CBCentralManager module.
 */
#ifndef AMBIT_BRIDGE_H
#define AMBIT_BRIDGE_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct ambit_object_s ambit_object_t;

/* Outgoing GATT-write callback signature: the framing layer hands each
 * <=20-byte chunk of a built NSP frame, in order, to this callback. */
typedef void (*ambit_ble_write_fn)(void *userdata, const uint8_t *data, size_t len);

/* Allocate the device, attach the BLE transport backed by `write_cb`, select the
 * driver by vid/pid, and run the watch-driven device-info handshake. Returns
 * NULL on failure. MUST be called off the queue that delivers notifications
 * (the handshake blocks waiting for frames that arrive via ambit_ios_ble_on_notify). */
ambit_object_t *libambit_new_from_ble_ios(ambit_ble_write_fn write_cb, void *write_ud,
                                          uint16_t vid, uint16_t pid);

/* Feed one raw GATT notification chunk (watch -> phone) into the framing layer. */
void ambit_ios_ble_on_notify(const uint8_t *data, size_t len);

/* Arm the pre-init RX stash before scanning/connecting, so the watch's opening
 * frame isn't dropped in the window before the object is published. */
void ambit_ios_ble_reset_rx_stash(void);

/* Publish / clear the object the notify router feeds. Normally handled inside
 * libambit_new_from_ble_ios; exposed for teardown. */
void ambit_ios_ble_set_active_object(ambit_object_t *obj);

/* The BLE-connected device (iOS equivalent of jni_bridge.cpp's g_device), or
 * NULL if no watch is connected. Used by the AmbitCore data bridge so every
 * watch operation acts on the same object the BLE handshake produced. */
ambit_object_t *ambit_ios_active_device(void);

/* Close and free the device (also releases the BLE transport). */
void libambit_close(ambit_object_t *object);

#ifdef __cplusplus
}
#endif

#endif /* AMBIT_BRIDGE_H */

/*
 * libambit Android bridge — creates an ambit_object_t from an Android
 * USB FileDescriptor, bypassing the desktop HIDAPI/libusb enumeration.
 *
 * Call libambit_new_from_fd() instead of libambit_new() on Android.
 */

#include "libambit.h"
#include "libambit_int.h"
#include "device_support.h"
#include "device_driver.h"
#include "debug.h"

#include <stdlib.h>
#include <string.h>
#include <jni.h>

/* Declared in hidapi/hid-android.c */
hid_device *hid_open_from_fd(int fd, int ep_in, int ep_out);

/* Declared in protocol_ble.c */
int libambit_ble_transport_new(ambit_object_t *object, JavaVM *jvm, jobject module_ref_local, JNIEnv *env);
int libambit_ble_handshake_device_info(ambit_object_t *object, ambit_device_info_t *info);
/* Declared in jni_bridge.cpp — publishes the in-construction object so incoming
 * notifications route to it during the handshake (see its own comment). */
void jni_ble_set_active_object(ambit_object_t *obj);

/*
 * Shared by libambit_new_from_fd() and libambit_new_from_ble(): looks up the
 * driver for vid/pid and fills in the parts of ambit_object_t that don't
 * depend on which transport opened it. Caller has already set object->handle
 * (USB) or object->transport/transport_ctx (BLE) before calling this.
 */
static bool libambit_new_common(ambit_object_t *object, uint16_t vid, uint16_t pid,
                                 const ambit_known_device_t **out_known)
{
    const ambit_known_device_t *known = libambit_device_support_find_first(vid, pid);
    if (!known || !known->supported || !known->driver) {
        return false;
    }

    object->device_info.vendor_id   = vid;
    object->device_info.product_id  = pid;
    object->device_info.is_supported = true;
    memcpy(&object->device_info.komposti_version,
           &known->komposti_version,
           sizeof(known->komposti_version));

    object->driver = known->driver;
    *out_known = known;
    return true;
}

/**
 * Open an Ambit device from an Android USB FileDescriptor.
 *
 * @param fd     FileDescriptor from UsbDeviceConnection.getFileDescriptor()
 * @param ep_in  Interrupt IN endpoint address  (e.g. 0x81)
 * @param ep_out Interrupt OUT endpoint address (e.g. 0x01), or -1 if none
 * @param vid    Vendor ID  (0x1493 for Suunto)
 * @param pid    Product ID (0x001C for Ambit3 Sport, 0x0010 for Ambit 1, …)
 *
 * @return Opaque ambit_object_t* on success, NULL on failure.
 *         Caller must free with libambit_close().
 */
ambit_object_t *libambit_new_from_fd(int fd, int ep_in, int ep_out,
                                     uint16_t vid, uint16_t pid)
{
    ambit_object_t *object = (ambit_object_t *)calloc(1, sizeof(*object));
    if (!object) return NULL;

    object->transport = AMBIT_TRANSPORT_USB;
    object->handle = hid_open_from_fd(fd, ep_in, ep_out);
    if (!object->handle) {
        free(object);
        return NULL;
    }

    const ambit_known_device_t *known = NULL;
    if (!libambit_new_common(object, vid, pid, &known)) {
        hid_close(object->handle);
        free(object);
        return NULL;
    }

    /* Non-blocking during init probe, then switch to blocking for comms */
    hid_set_nonblocking(object->handle, 1);

    /*
     * Fetch the watch's real fw_version over USB before driver->init() runs.
     * Without this, device_info.fw_version stays zeroed (from calloc above),
     * and e.g. get_ambit3_fw_gen() in device_driver_ambit3.c always falls
     * back to its oldest/GEN1 case — wrong protocol IDs and wrong log-reply
     * parser for any watch running newer firmware, silently yielding zero
     * log entries. The desktop hidapi enumeration path (ambit_device_info_new
     * in libambit.c) already does this before calling driver->init(); this
     * mirrors it for the Android fd-based path. Non-fatal if it fails: we
     * just fall back to the previous (zeroed fw_version) behavior.
     */
    /* kailash-debug: bracketing device_info_get()/driver->init() calls to find which of
     * these two a real crash survives past - see libambit.c's own checkpoint logging. */
    device_info_get(object, &object->device_info); /* non-fatal on failure */
    LOG_INFO("kailash-debug: device_info_get() returned to libambit_new_from_fd, about to call driver->init");

    if (object->driver->init != NULL) {
        object->driver->init(object, known->driver_param);
    }
    LOG_INFO("kailash-debug: driver->init() returned, about to set nonblocking + return object");
    hid_set_nonblocking(object->handle, 1); /* keep non-blocking (protocol.c polls) */

    return object;
}

/**
 * Open an Ambit3/Traverse device over BLE. Caller (jni_bridge.cpp's
 * nativeAmbitBleInit) has already connected, discovered the custom GATT
 * service correctly (i.e. after handling the Service Changed indication —
 * see AmbitBleModule.kt and HANDOFF.md Milestone 7), and enabled
 * notifications, before calling this.
 *
 * Unlike the USB path, a failed device_info_get() here is FATAL: BLE has no
 * hardware-confirmed reference for our own device-info request shape (the
 * only capture available showed the watch push a short, differently-shaped
 * message unprompted, not a request/reply matching this call), and route
 * writes need the real firmware generation to compute correct flash offsets
 * (see the memory-map safety gate in ambit3_write_route_to_watch). Refusing
 * to proceed on an unconfirmed generation is the safe default, not a
 * regression — better a connect that fails loudly than a route write against
 * a guessed offset.
 *
 * @param jvm             JavaVM, obtained once in jni_bridge.cpp
 * @param module_ref_local Kotlin AmbitBleModule instance (local ref; a
 *                          global ref is taken internally)
 * @param env             current-thread JNIEnv, for the initial setup calls
 * @param vid  Vendor ID  (0x1493 for Suunto)
 * @param pid  Product ID (Ambit3/Traverse variants only — enforced by the
 *             Kotlin-side scan filter, not re-checked here)
 */
ambit_object_t *libambit_new_from_ble(JavaVM *jvm, jobject module_ref_local, JNIEnv *env,
                                       uint16_t vid, uint16_t pid)
{
    ambit_object_t *object = (ambit_object_t *)calloc(1, sizeof(*object));
    if (!object) return NULL;

    if (libambit_ble_transport_new(object, jvm, module_ref_local, env) != 0) {
        free(object);
        return NULL;
    }

    const ambit_known_device_t *known = NULL;
    if (!libambit_new_common(object, vid, pid, &known)) {
        libambit_ble_transport_close(object);
        free(object);
        return NULL;
    }

    /* Over BLE the watch DRIVES the conversation (it pushes requests, the phone
     * responds), so the USB-style device_info_get() — which sends a request and
     * waits for a reply — can never complete. Instead run the server-side
     * bootstrap handshake: listen for the watch's 0x0002 hello, answer with
     * 0x0000 device_info, and read model/serial/firmware from what the watch
     * pushes. See protocol_ble.c and HANDOFF.md Milestone 7 items 8-9.
     *
     * Publish the object to the notify router FIRST — the handshake below waits
     * for the watch's frames, which only reach it via nativeAmbitBleOnNotify ->
     * g_device, and g_device isn't otherwise assigned until this whole function
     * returns. Without this the handshake gets zero frames and times out. */
    jni_ble_set_active_object(object);
    if (libambit_ble_handshake_device_info(object, &object->device_info) != 0) {
        jni_ble_set_active_object(NULL);
        libambit_ble_transport_close(object);
        free(object);
        return NULL;
    }

    /* The product id passed in was only guessed from the advertised name: take it from the model
     * the watch names in its hello (see libambit_device_support_ble_pid_for_model). */
    {
        const uint16_t guessed = object->device_info.product_id;
        object->device_info.product_id =
            libambit_device_support_ble_pid_for_model(object->device_info.model, guessed);
        if (object->device_info.product_id != guessed)
            LOG_INFO("BLE: model %s -> product 0x%04x (name guess was 0x%04x)",
                     object->device_info.model, object->device_info.product_id, guessed);
    }

    if (object->driver->init != NULL) {
        object->driver->init(object, known->driver_param);
    }

    return object;
}

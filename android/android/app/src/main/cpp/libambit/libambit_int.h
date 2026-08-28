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
#ifndef __LIBAMBIT_INT_H__
#define __LIBAMBIT_INT_H__

#include <stdint.h>
#include "hidapi/hidapi.h"
#include "libambit.h"

/*
 * USB and BLE use genuinely different wire framing (confirmed against a real
 * HCI capture 2026-08-06 — see HANDOFF.md Milestone 7). libambit_protocol_command()
 * in protocol.c dispatches on this tag: AMBIT_TRANSPORT_USB keeps using the
 * existing ambit_msg_header_t/64-byte-report path unchanged; AMBIT_TRANSPORT_BLE
 * calls protocol_ble.c's framing instead. Every device driver file is untouched —
 * they only ever call libambit_protocol_command(), never protocol_write_packet()/
 * protocol_read_packet() directly.
 */
typedef enum {
    AMBIT_TRANSPORT_USB = 0,
    AMBIT_TRANSPORT_BLE = 1,
} ambit_transport_t;

struct ambit_object_s {
    hid_device *handle;  // unused when transport == AMBIT_TRANSPORT_BLE
    uint16_t sequence_no;
    ambit_device_info_t device_info;
    ambit_transport_t transport;
    void *transport_ctx; // BLE-only: opaque ble_ctx_t* from protocol_ble.c, NULL for USB

    struct ambit_device_driver_s *driver;
    struct ambit_device_driver_data_s *driver_data; // Driver specific struct,
                                                    // should be defined
                                                    // locally for each driver
};

/*
 * Queries the watch over USB for its real device info (model, serial,
 * fw_version, hw_version) and fills `info` in place. Only needs
 * object->handle and object->sequence_no — safe to call before
 * object->driver/driver_data are set up (i.e. before driver->init()).
 * Defined in libambit.c; used there by the desktop hidapi enumeration path
 * and by libambit_android.c's fd-based bridge, which otherwise has no way
 * to learn the watch's firmware version before init() picks a fw generation.
 * Returns 0 on success, -1 on failure.
 */
int device_info_get(ambit_object_t *object, ambit_device_info_t *info);

/*
 * Frees object->transport_ctx (the BLE ble_ctx_t from protocol_ble.c) and
 * releases its JNI global ref. No-op if object->transport != AMBIT_TRANSPORT_BLE.
 * Declared here (no JNI types in the signature) so libambit.c's transport-agnostic
 * libambit_close() can call it without pulling <jni.h> into every driver file that
 * includes this header.
 */
void libambit_ble_transport_close(ambit_object_t *object);

/*
 * BLE outgoing-write callback (2026-08-28 iOS port). The transport-agnostic
 * framing in protocol_ble.c hands each <=20-byte GATT chunk of a fully-built
 * frame, in order, to this host-supplied callback. On iOS the callback performs
 * a CBPeripheral writeValue(_:for:type:.withoutResponse) to the watch's NSP
 * write characteristic; the Android build uses JNI (bleWriteChunk) instead and
 * does not use this seam. `userdata` is the pointer passed to
 * libambit_ble_transport_new / libambit_new_from_ble_ios.
 */
typedef void (*ambit_ble_write_fn)(void *userdata, const uint8_t *data, size_t len);

#ifndef __ANDROID__
/*
 * iOS / generic-host BLE constructors, mirroring libambit_android.c's
 * libambit_new_from_ble() but with a plain C write callback instead of a
 * JavaVM/jobject. transport_new attaches the BLE transport to an existing
 * object; new_from_ble_ios allocates the object, selects the driver by vid/pid,
 * and runs the watch-driven device-info handshake. Defined in libambit_ios.c.
 */
int libambit_ble_transport_new(ambit_object_t *object, ambit_ble_write_fn write_cb, void *write_ud);
ambit_object_t *libambit_new_from_ble_ios(ambit_ble_write_fn write_cb, void *write_ud,
                                          uint16_t vid, uint16_t pid);
#endif

#endif /* __LIBAMBIT_INT_H__ */

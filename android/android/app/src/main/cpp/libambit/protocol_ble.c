/*
 * BLE-specific NSP framing for Ambit3/Traverse.
 *
 * USB and BLE do NOT share wire framing (protocol.c's ambit_msg_header_t /
 * 64-byte-report scheme is USB-only) — they only share the command-ID space.
 * This file implements the BLE envelope, reverse-engineered from a real HCI
 * snoop capture of a working Suunto-app BLE sync, 2026-08-06 (see
 * HANDOFF.md, Milestone 7). Confirmed against that capture:
 *
 *   0x7e [12-byte header][payload, dataSize bytes][CRC32(header+payload), LE]0x7e
 *
 * header = [u8 msgId][u8 subId][u8 flags][u8 errFlags][u16 connId][u16 pktNum][u32 dataSize]
 * (msgId/subId together are the same 16-bit "command" protocol.h already
 * defines for USB, just split into two bytes: command = (msgId<<8)|subId).
 *
 * The envelope is chunked into <=20-byte pieces for individual BLE GATT
 * writes by the Kotlin side (AmbitBleModule.kt) — this file hands it
 * complete pre-chunked buffers and lets Kotlin do the actual splitting,
 * since chunk size is a GATT/MTU concern, not a framing concern.
 *
 * CRC32 verified byte-for-byte against 59/59 real frames from the capture
 * (both directions) — see assets/ble 2026-08-06/decoded_suuntoapp_session.txt.
 *
 * What is NOT yet hardware-confirmed for OUR OWN outgoing commands
 * specifically (the capture was an activity-log sync, not a route/POI
 * write): the exact `flags` byte to use when initiating a fresh
 * request. We use 0x06, the one clean example of an app-initiated write
 * seen in the capture (for command 0x1201). If a real device rejects a
 * command (errFlags set in the reply, or no reply at all), that is the
 * first thing to suspect and adjust based on real hardware testing.
 */

#include "libambit_int.h"
#include "protocol.h"

#include <pthread.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/* ─── Transport seam (2026-08-28 iOS port) ───────────────────────────────────
 * The NSP framing/CRC/handshake below is transport-agnostic. Only the outgoing
 * chunk write differs per platform: Android calls back into Kotlin's
 * bleWriteChunk() over JNI; iOS (and any non-Android host) supplies a plain C
 * callback that performs the GATT write (CBPeripheral writeValue on iOS). The
 * JNI details are confined to the __ANDROID__ branches so the shared file has a
 * single source of truth for the protocol. */
#ifdef __ANDROID__
#include <jni.h>
#include <android/log.h>
#define LOG_TAG "AmbitProtocolBle"
#define LOGI(...) __android_log_print(ANDROID_LOG_INFO,  LOG_TAG, __VA_ARGS__)
#define LOGE(...) __android_log_print(ANDROID_LOG_ERROR, LOG_TAG, __VA_ARGS__)
/* Defined in jni_bridge.cpp. Replays notification bytes that arrived before
 * g_device was published (see the call site in the handshake below). */
extern void jni_ble_flush_rx_stash(void);
#else
#include <stdio.h>
#define LOGI(...) fprintf(stderr, "[AmbitProtocolBle] " __VA_ARGS__), fprintf(stderr, "\n")
#define LOGE(...) fprintf(stderr, "[AmbitProtocolBle:E] " __VA_ARGS__), fprintf(stderr, "\n")
/* iOS bridge equivalent of jni_ble_flush_rx_stash — replays the pre-init RX
 * stash. Provided by the iOS transport TU (libambit_ios.c). */
extern void ambit_ble_flush_rx_stash(void);
#endif

#define BLE_HEADER_LEN   12
#define BLE_TRAILER_LEN  4   /* CRC32, little-endian */
#define BLE_CHUNK_SIZE   20
#define BLE_REPLY_TIMEOUT_SEC 20

typedef struct {
#ifdef __ANDROID__
    JavaVM *jvm;
    jobject module_ref;       /* global ref to the Kotlin AmbitBleModule instance */
    jmethodID write_chunk_mid; /* void bleWriteChunk(byte[]) */
#else
    /* Host-supplied GATT write callback (iOS: CBPeripheral writeValue). Called
     * with each <=20-byte chunk of a fully-built frame, in order. */
    ambit_ble_write_fn write_chunk;
    void *write_ud;
#endif

    pthread_mutex_t lock;
    pthread_cond_t cond;
    int reply_ready;
    int reply_is_error;
    uint16_t reply_command;   /* (msgId<<8)|subId echoed in the reply header */
    uint8_t *reply_payload;
    size_t reply_payload_len;

    uint8_t *rx_acc;          /* SLIP-unescaped bytes accumulated between 0x7e markers */
    size_t rx_acc_len;
    size_t rx_acc_cap;
    int rx_in_frame;
    int rx_escape;            /* saw a 0x7d, next byte is SLIP-escaped (persists across
                               * notification chunks, so it must live in ctx) */

    uint16_t conn_id;         /* 0 until the watch's first reply teaches us the real value,
                                * then echoed back for the rest of the session — matches
                                * the capture: connId=0 on the bootstrap DEVICE_INFO reply,
                                * connId=9 (constant) for everything after. */
    uint16_t pkt_num;

    /* Server-side handshake support (2026-08-09). Over BLE the WATCH drives:
     * it pushes requests and the phone responds (see HANDOFF.md Milestone 7
     * item 9 and the byte-exact flow in assets/ble 2026-08-09/). When
     * handshake_mode is set, ambit_ble_on_notify() enqueues each complete
     * incoming frame here instead of into the single reply slot, so the
     * handshake loop can consume them in order without races. */
    int handshake_mode;
#define BLE_HS_QUEUE_CAP 32
    struct {
        uint16_t cmd;
        uint8_t  flags;
        uint16_t connId;
        uint8_t *payload;
        size_t   len;
    } hs_queue[BLE_HS_QUEUE_CAP];
    int hs_head;
    int hs_tail;
} ble_ctx_t;

static uint32_t g_crc32_table[256];
static int g_crc32_table_ready = 0;

static void crc32_init_table(void)
{
    for (uint32_t i = 0; i < 256; i++) {
        uint32_t c = i;
        for (int k = 0; k < 8; k++) {
            c = (c & 1) ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
        }
        g_crc32_table[i] = c;
    }
    g_crc32_table_ready = 1;
}

/* Standard CRC-32 (IEEE 802.3 / zlib "crc32"), init=xorout=0xFFFFFFFF.
 * Confirmed byte-for-byte against a real capture — see file header comment. */
static uint32_t crc32_ieee(const uint8_t *data, size_t len)
{
    if (!g_crc32_table_ready) crc32_init_table();
    uint32_t crc = 0xFFFFFFFFu;
    for (size_t i = 0; i < len; i++) {
        crc = g_crc32_table[(crc ^ data[i]) & 0xFF] ^ (crc >> 8);
    }
    return crc ^ 0xFFFFFFFFu;
}

/*
 * Called from jni_bridge.cpp (nativeAmbitBleInit) once the Kotlin side has
 * connected, discovered the custom service (after correctly handling the
 * Service Changed indication — see AmbitBleModule.kt), and enabled
 * notifications. Does not touch the wire itself.
 */
#ifdef __ANDROID__
int libambit_ble_transport_new(ambit_object_t *object, JavaVM *jvm, jobject module_ref_local, JNIEnv *env)
{
    ble_ctx_t *ctx = (ble_ctx_t *)calloc(1, sizeof(ble_ctx_t));
    if (!ctx) return -1;

    ctx->jvm = jvm;
    ctx->module_ref = (*env)->NewGlobalRef(env, module_ref_local);
    if (!ctx->module_ref) {
        LOGE("NewGlobalRef failed for BLE module instance");
        free(ctx);
        return -1;
    }

    jclass cls = (*env)->GetObjectClass(env, ctx->module_ref);
    ctx->write_chunk_mid = (*env)->GetMethodID(env, cls, "bleWriteChunk", "([B)V");
    (*env)->DeleteLocalRef(env, cls);
    if (!ctx->write_chunk_mid) {
        LOGE("bleWriteChunk([B)V not found on BLE module — Kotlin/native mismatch");
        (*env)->DeleteGlobalRef(env, ctx->module_ref);
        free(ctx);
        return -1;
    }

    pthread_mutex_init(&ctx->lock, NULL);
    pthread_cond_init(&ctx->cond, NULL);

    ctx->rx_acc_cap = 512;
    ctx->rx_acc = (uint8_t *)malloc(ctx->rx_acc_cap);
    if (!ctx->rx_acc) {
        (*env)->DeleteGlobalRef(env, ctx->module_ref);
        pthread_mutex_destroy(&ctx->lock);
        pthread_cond_destroy(&ctx->cond);
        free(ctx);
        return -1;
    }

    object->transport = AMBIT_TRANSPORT_BLE;
    object->transport_ctx = ctx;
    object->handle = NULL;
    return 0;
}
#else /* !__ANDROID__ (iOS / generic host) */
int libambit_ble_transport_new(ambit_object_t *object, ambit_ble_write_fn write_cb, void *write_ud)
{
    if (!write_cb) return -1;
    ble_ctx_t *ctx = (ble_ctx_t *)calloc(1, sizeof(ble_ctx_t));
    if (!ctx) return -1;

    ctx->write_chunk = write_cb;
    ctx->write_ud = write_ud;

    pthread_mutex_init(&ctx->lock, NULL);
    pthread_cond_init(&ctx->cond, NULL);

    ctx->rx_acc_cap = 512;
    ctx->rx_acc = (uint8_t *)malloc(ctx->rx_acc_cap);
    if (!ctx->rx_acc) {
        pthread_mutex_destroy(&ctx->lock);
        pthread_cond_destroy(&ctx->cond);
        free(ctx);
        return -1;
    }

    object->transport = AMBIT_TRANSPORT_BLE;
    object->transport_ctx = ctx;
    object->handle = NULL;
    return 0;
}
#endif

void libambit_ble_transport_close(ambit_object_t *object)
{
    if (!object || object->transport != AMBIT_TRANSPORT_BLE || !object->transport_ctx) return;
    ble_ctx_t *ctx = (ble_ctx_t *)object->transport_ctx;

#ifdef __ANDROID__
    JNIEnv *env = NULL;
    if ((*ctx->jvm)->GetEnv(ctx->jvm, (void **)&env, JNI_VERSION_1_6) == JNI_OK && env) {
        (*env)->DeleteGlobalRef(env, ctx->module_ref);
    }
#endif
    pthread_mutex_destroy(&ctx->lock);
    pthread_cond_destroy(&ctx->cond);
    free(ctx->rx_acc);
    free(ctx->reply_payload);
    free(ctx);
    object->transport_ctx = NULL;
}

/*
 * Called from jni_bridge.cpp (nativeAmbitBleOnNotify) on whatever thread
 * Android's BLE callback delivers notifications on — NOT the same thread
 * that calls libambit_protocol_command_ble(). Appends raw bytes, and
 * whenever a complete 0x7e ... 0x7e frame is found, validates its CRC32
 * and (if a command is currently pending) wakes it up.
 */
void ambit_ble_on_notify(ambit_object_t *object, const uint8_t *data, size_t len)
{
    if (!object || object->transport != AMBIT_TRANSPORT_BLE || !object->transport_ctx) return;
    ble_ctx_t *ctx = (ble_ctx_t *)object->transport_ctx;

    pthread_mutex_lock(&ctx->lock);

    for (size_t i = 0; i < len; i++) {
        uint8_t b = data[i];
        if (b == 0x7e) {
            if (ctx->rx_in_frame && ctx->rx_acc_len > 0) {
                /* complete frame in rx_acc[0..rx_acc_len) */
                if (ctx->rx_acc_len >= BLE_HEADER_LEN + BLE_TRAILER_LEN) {
                    size_t content_len = ctx->rx_acc_len - BLE_TRAILER_LEN;
                    const uint8_t *trailer = ctx->rx_acc + content_len;
                    uint32_t got = crc32_ieee(ctx->rx_acc, content_len);
                    uint32_t want = (uint32_t)trailer[0] | ((uint32_t)trailer[1] << 8) |
                                    ((uint32_t)trailer[2] << 16) | ((uint32_t)trailer[3] << 24);
                    if (got == want) {
                        uint8_t msgId    = ctx->rx_acc[0];
                        uint8_t subId    = ctx->rx_acc[1];
                        uint8_t errFlags = ctx->rx_acc[3];
                        uint16_t connId;
                        uint32_t dataSize;
                        memcpy(&connId, ctx->rx_acc + 4, 2);
                        memcpy(&dataSize, ctx->rx_acc + 8, 4);
                        size_t payload_avail = content_len - BLE_HEADER_LEN;
                        size_t payload_len = (dataSize <= payload_avail) ? dataSize : payload_avail;

                        if (ctx->conn_id == 0 && connId != 0) {
                            ctx->conn_id = connId; /* learn it from the first real reply */
                        }

                        if (ctx->handshake_mode) {
                            /* Server handshake: enqueue the whole frame for the
                             * handshake loop to dispatch, rather than the
                             * request/reply single-slot the driver path uses. */
                            int next = (ctx->hs_tail + 1) % BLE_HS_QUEUE_CAP;
                            if (next == ctx->hs_head) {
                                LOGE("BLE handshake queue full — dropping frame 0x%02x%02x", msgId, subId);
                            } else {
                                uint8_t *pcopy = payload_len ? (uint8_t *)malloc(payload_len) : NULL;
                                if (payload_len && pcopy) memcpy(pcopy, ctx->rx_acc + BLE_HEADER_LEN, payload_len);
                                ctx->hs_queue[ctx->hs_tail].cmd = ((uint16_t)msgId << 8) | subId;
                                ctx->hs_queue[ctx->hs_tail].flags = ctx->rx_acc[2];
                                ctx->hs_queue[ctx->hs_tail].connId = connId;
                                ctx->hs_queue[ctx->hs_tail].payload = pcopy;
                                ctx->hs_queue[ctx->hs_tail].len = pcopy ? payload_len : 0;
                                ctx->hs_tail = next;
                                pthread_cond_signal(&ctx->cond);
                            }
                        } else {
                            free(ctx->reply_payload);
                            ctx->reply_payload = payload_len ? (uint8_t *)malloc(payload_len) : NULL;
                            if (payload_len) memcpy(ctx->reply_payload, ctx->rx_acc + BLE_HEADER_LEN, payload_len);
                            ctx->reply_payload_len = payload_len;
                            ctx->reply_command = ((uint16_t)msgId << 8) | subId;
                            ctx->reply_is_error = (errFlags != 0);
                            ctx->reply_ready = 1;
                            pthread_cond_signal(&ctx->cond);
                        }
                    } else {
                        LOGE("BLE frame CRC mismatch: got=%08x want=%08x len=%zu — dropped",
                             got, want, content_len);
                    }
                } else {
                    LOGE("BLE frame too short (%zu bytes) — dropped", ctx->rx_acc_len);
                }
            }
            /* 0x7e both closes the previous frame and opens the next one. A real
             * 0x7e is never escaped (escaping emits 0x7d 0x5e), so a raw 0x7e is
             * always a genuine frame boundary — reset the escape state too. */
            ctx->rx_acc_len = 0;
            ctx->rx_in_frame = 1;
            ctx->rx_escape = 0;
            continue;
        }
        if (!ctx->rx_in_frame) continue; /* junk before the first 0x7e this session */

        /* SLIP de-escaping (2026-08-09): the on-wire content between the 0x7e
         * delimiters is SLIP-escaped — 0x7d 0x5e means a literal 0x7e, 0x7d 0x5d
         * means a literal 0x7d. Without undoing this, any frame whose header/
         * payload/CRC happens to contain a 0x7e or 0x7d byte (common in large
         * activity-log frames, rare in small ones) reassembles with the escape
         * bytes still in place, so the CRC — which the watch computed over the
         * UNescaped content — never matches and the frame is dropped. This was
         * exactly the "BLE frame CRC mismatch ... len=911/1046 — dropped"
         * failure that stalled sync on real hardware. Same rule this project's
         * tools/ble_pklg.py already applies on the Python side. */
        if (ctx->rx_escape) {
            ctx->rx_escape = 0;
            if (b == 0x5e) b = 0x7e;
            else if (b == 0x5d) b = 0x7d;
            /* any other byte after 0x7d is malformed; pass it through as-is */
        } else if (b == 0x7d) {
            ctx->rx_escape = 1;
            continue;
        }

        if (ctx->rx_acc_len >= ctx->rx_acc_cap) {
            size_t new_cap = ctx->rx_acc_cap * 2;
            uint8_t *grown = (uint8_t *)realloc(ctx->rx_acc, new_cap);
            if (!grown) { LOGE("BLE rx buffer growth failed"); ctx->rx_in_frame = 0; ctx->rx_acc_len = 0; continue; }
            ctx->rx_acc = grown;
            ctx->rx_acc_cap = new_cap;
        }
        ctx->rx_acc[ctx->rx_acc_len++] = b;
    }

    pthread_mutex_unlock(&ctx->lock);
}

/*
 * Builds one 0x7e-wrapped frame for `command`/`data`/`datalen`, chunks it
 * into <=20-byte pieces, and hands each chunk to Kotlin's bleWriteChunk()
 * via JNI, in order. Kotlin performs the actual GATT write (WRITE_TYPE_NO_RESPONSE,
 * matching what the real capture showed the Suunto app using for this traffic).
 */
/* Core sender: builds one 0x7e-delimited, SLIP-escaped, CRC32'd frame and sends
 * it chunked via bleWriteChunk. The content (header+payload+CRC) is SLIP-escaped
 * (0x7e -> 0x7d 0x5e, 0x7d -> 0x7d 0x5d); the outer 0x7e delimiters are NOT. The
 * CRC is computed over the UNescaped content, matching what the watch verifies
 * and what ambit_ble_on_notify un-escapes back to (added 2026-08-09 alongside
 * the receive-side de-escaping — without escaping, our own frames carrying a
 * 0x7e/0x7d byte in the header/payload/CRC would be rejected by the watch). */
static int ble_build_and_send(ble_ctx_t *ctx, uint16_t command, uint8_t flags,
                              uint16_t connId, uint16_t pktNum,
                              const uint8_t *data, size_t datalen)
{
    uint8_t header[BLE_HEADER_LEN];
    header[0] = (uint8_t)(command >> 8);
    header[1] = (uint8_t)(command & 0xFF);
    header[2] = flags;
    header[3] = 0x00; /* errFlags — always 0 outgoing */
    memcpy(header + 4, &connId, 2);
    memcpy(header + 6, &pktNum, 2);
    uint32_t dataSize = (uint32_t)datalen;
    memcpy(header + 8, &dataSize, 4);

    size_t content_len = BLE_HEADER_LEN + datalen;
    size_t raw_len = content_len + BLE_TRAILER_LEN;
    uint8_t *content = (uint8_t *)malloc(raw_len);
    if (!content) return -1;
    memcpy(content, header, BLE_HEADER_LEN);
    if (datalen) memcpy(content + BLE_HEADER_LEN, data, datalen);
    uint32_t crc = crc32_ieee(content, content_len);
    content[content_len + 0] = (uint8_t)(crc & 0xFF);
    content[content_len + 1] = (uint8_t)((crc >> 8) & 0xFF);
    content[content_len + 2] = (uint8_t)((crc >> 16) & 0xFF);
    content[content_len + 3] = (uint8_t)((crc >> 24) & 0xFF);

    /* worst case: every content byte escapes to 2 bytes, plus 2 delimiters */
    uint8_t *frame = (uint8_t *)malloc(raw_len * 2 + 2);
    if (!frame) { free(content); return -1; }
    size_t off = 0;
    frame[off++] = 0x7e;
    for (size_t i = 0; i < raw_len; i++) {
        uint8_t b = content[i];
        if (b == 0x7e) { frame[off++] = 0x7d; frame[off++] = 0x5e; }
        else if (b == 0x7d) { frame[off++] = 0x7d; frame[off++] = 0x5d; }
        else { frame[off++] = b; }
    }
    frame[off++] = 0x7e;
    size_t frame_len = off;
    free(content);

    int ret = 0;
#ifdef __ANDROID__
    JNIEnv *env = NULL;
    int attached = 0;
    if ((*ctx->jvm)->GetEnv(ctx->jvm, (void **)&env, JNI_VERSION_1_6) != JNI_OK) {
        if ((*ctx->jvm)->AttachCurrentThread(ctx->jvm, &env, NULL) != 0) {
            LOGE("AttachCurrentThread failed");
            free(frame);
            return -1;
        }
        attached = 1;
    }

    for (size_t pos = 0; pos < frame_len; pos += BLE_CHUNK_SIZE) {
        size_t chunk_len = frame_len - pos;
        if (chunk_len > BLE_CHUNK_SIZE) chunk_len = BLE_CHUNK_SIZE;
        jbyteArray chunk = (*env)->NewByteArray(env, (jsize)chunk_len);
        if (!chunk) { ret = -1; break; }
        (*env)->SetByteArrayRegion(env, chunk, 0, (jsize)chunk_len, (const jbyte *)(frame + pos));
        (*env)->CallVoidMethod(env, ctx->module_ref, ctx->write_chunk_mid, chunk);
        (*env)->DeleteLocalRef(env, chunk);
        if ((*env)->ExceptionCheck(env)) {
            LOGE("bleWriteChunk threw an exception");
            (*env)->ExceptionDescribe(env);
            (*env)->ExceptionClear(env);
            ret = -1;
            break;
        }
    }

    if (attached) (*ctx->jvm)->DetachCurrentThread(ctx->jvm);
#else /* iOS / generic host: hand each chunk to the C write callback in order */
    for (size_t pos = 0; pos < frame_len; pos += BLE_CHUNK_SIZE) {
        size_t chunk_len = frame_len - pos;
        if (chunk_len > BLE_CHUNK_SIZE) chunk_len = BLE_CHUNK_SIZE;
        ctx->write_chunk(ctx->write_ud, frame + pos, chunk_len);
    }
#endif
    free(frame);
    return ret;
}

/* Driver-path command: phone-initiated request, flags=0x05, using the learned
 * connId and the running pktNum (post-incremented). See ble_build_and_send. */
static int ble_send_command(ble_ctx_t *ctx, uint16_t command,
                             const uint8_t *data, size_t datalen)
{
    uint16_t pkt = ctx->pkt_num++;
    return ble_build_and_send(ctx, command, 0x05, ctx->conn_id, pkt, data, datalen);
}

/*
 * Flexible single-frame send with explicit flags/connId/pktNum — needed for
 * the server-side handshake, where responses use specific header values that
 * differ from ble_send_command's driver-oriented defaults (e.g. the bootstrap
 * device_info response uses flags=0x01, connId=0). Thin wrapper over the shared
 * SLIP-escaping core.
 */
static int ble_send_frame(ble_ctx_t *ctx, uint16_t command, uint8_t flags,
                          uint16_t connId, uint16_t pktNum,
                          const uint8_t *data, size_t datalen)
{
    return ble_build_and_send(ctx, command, flags, connId, pktNum, data, datalen);
}

/*
 * Server-side BLE bootstrap handshake (2026-08-09). Over BLE the watch drives
 * the conversation: right after it subscribes to our notify characteristic it
 * pushes 0x1201 (log_synced) and 0x0002 (hello, carrying its model + firmware
 * id), then waits for the phone to answer 0x0000 device_info before sending
 * anything else (like 0x0b1e, its serial). This function runs that responder
 * exchange far enough to populate device_info and hand the connection off as
 * established. It does NOT drive the full sync — that's the watch-driven
 * command flow still to be built (HANDOFF.md Milestone 7 item 9).
 *
 * Populates info->model / fw_version from the 0x0002 hello, and info->serial
 * from the 0x0b1e reply if it arrives in time (falling back to the hello's own
 * id string otherwise). Returns 0 once the hello has been seen and answered.
 */
int libambit_ble_handshake_device_info(ambit_object_t *object, ambit_device_info_t *info)
{
    if (!object || object->transport != AMBIT_TRANSPORT_BLE || !object->transport_ctx) return -1;
    ble_ctx_t *ctx = (ble_ctx_t *)object->transport_ctx;

    pthread_mutex_lock(&ctx->lock);
    ctx->handshake_mode = 1;
    ctx->hs_head = ctx->hs_tail = 0;
    pthread_mutex_unlock(&ctx->lock);

    /* Replay any notification bytes that arrived before g_device/handshake_mode
     * were live and were parked in the pre-init stash (jni_bridge.cpp), then go
     * live. The watch's 0x0002 hello can land on the binder thread the instant
     * it subscribes — before nativeAmbitBleInit publishes g_device on the
     * executor thread. The Ambit3 re-sends its 0x1201 opener every ~5s so a lost
     * first one didn't matter; the Kailash (Hoopoe) sends its 0x0002 hello
     * exactly once, so a dropped hello hung this handshake for the full timeout
     * (zero "handshake: got frame" logs). Must run here — after handshake_mode=1
     * so replayed frames land in hs_queue, and with ctx->lock released so
     * ambit_ble_on_notify can take it. (2026-08-09.) */
#ifdef __ANDROID__
    jni_ble_flush_rx_stash();
#else
    ambit_ble_flush_rx_stash();

    /* iOS: THE PHONE SPEAKS FIRST. Byte-exact from the working Suunto-app capture
     * (Downloads/*.pklg, decoded 2026-08-30 with tools/ble_pklg.py): right after the watch
     * subscribes, the app sends 0x0000 device_info (flags 0x01, connId 0, pktNum 0, payload
     * {82 06 08 00}) and ONLY THEN does the watch reply 0x0002 hello. The old "watch drives /
     * pushes 0x1201 first" model (below) is what the handshake waited for — but a bonded Ambit3
     * subscribes then sits silent waiting for THIS opener, so both sides hung until the 20 s
     * timeout (the whole iOS "subscribes then nothing" failure). Sending it kicks the exchange.
     * Guarded to non-Android so Android's separately-proven path is unchanged. */
    {
        static const uint8_t hello_req[4] = { 0x82, 0x06, 0x08, 0x00 };
        ble_send_frame(ctx, 0x0000, 0x01, 0x0000, 0x0000, hello_req, sizeof(hello_req));
    }
#endif

    struct timespec deadline;
    clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_sec += BLE_REPLY_TIMEOUT_SEC;

    int got_hello = 0;

    pthread_mutex_lock(&ctx->lock);
    while (!got_hello) {
        while (ctx->hs_head == ctx->hs_tail) {
            int w = pthread_cond_timedwait(&ctx->cond, &ctx->lock, &deadline);
            if (w != 0) goto done; /* timeout */
        }
        /* pop one frame */
        int idx = ctx->hs_head;
        uint16_t cmd   = ctx->hs_queue[idx].cmd;
        uint8_t *pl    = ctx->hs_queue[idx].payload;
        size_t   pll   = ctx->hs_queue[idx].len;
        ctx->hs_head = (ctx->hs_head + 1) % BLE_HS_QUEUE_CAP;
        pthread_mutex_unlock(&ctx->lock);

        LOGI("handshake: got frame cmd=0x%04x len=%zu", cmd, pll);

        if (cmd == 0x1201) {
            /* The watch's opener. Confirmed byte-for-byte from the working Suunto
             * capture (assets/ble 2026-08-09/, true time order): watch sends
             * 0x1201 -> phone answers 0x0000 device_info (flags 0x01, connId 0,
             * pktNum 0, payload 03 00 00 00) -> ONLY THEN does the watch send its
             * 0x0002 hello. So we must answer 0x1201 to make the watch advance;
             * waiting for 0x0002 without answering 0x1201 hangs (the watch just
             * re-sends 0x1201 every ~5s — exactly what we saw before this fix). */
            static const uint8_t dev_info_reply[4] = { 0x03, 0x00, 0x00, 0x00 };
            ble_send_frame(ctx, 0x0000, 0x01, 0x0000, 0x0000, dev_info_reply, sizeof(dev_info_reply));
        } else if (cmd == 0x0002) {
            /* hello: [model 16][id 16][versions 16...]. fw at offset 32
             * (02 04 11 = 2.4.17, the real Ambit3 Peak firmware, in the capture).
             * The clean serial ("1849100781") only arrives later in a phone-driven
             * 0x0b1e exchange; for connect+identify we use the hello's own id
             * string as the serial, which is enough and needs no further round. */
            if (info) {
                if (pll >= 16) {
                    char model[17]; memcpy(model, pl, 16); model[16] = '\0';
                    free(info->model); info->model = strdup(model);
                }
                if (pll >= 36) {
                    info->fw_version[0] = pl[32];
                    info->fw_version[1] = pl[33];
                    info->fw_version[2] = pl[34];
                    info->fw_version[3] = pl[35];
                }
                /* hw_version at offset 36, mirroring the USB device-info reply layout
                 * (libambit.c: model 16, serial 16, fw 4, hw 4). The BLE hello wasn't
                 * reading it, so hw showed 0.0.0 over BLE — needed for picking the right
                 * firmware image. (2026-08-09.) */
                if (pll >= 40) {
                    info->hw_version[0] = pl[36];
                    info->hw_version[1] = pl[37];
                    info->hw_version[2] = pl[38];
                    info->hw_version[3] = pl[39];
                }
                if (pll >= 32) {
                    char id[17]; memcpy(id, pl + 16, 16); id[16] = '\0';
                    free(info->serial); info->serial = strdup(id);
                }
            }
            got_hello = 1;
        }
        /* any other early pushes (retried 0x1201, etc.) are ignored */

        free(pl);
        pthread_mutex_lock(&ctx->lock);
    }
done:
    ctx->handshake_mode = 0;
    /* Continue the pktNum sequence for the phone-driven commands that follow
     * (getLogs etc.): device_info was the watch's pkt 0 exchange, and in the
     * capture the phone's first real request (0x0b1e) is pkt 1. Driver commands
     * go through ble_send_command, which uses and post-increments ctx->pkt_num. */
    if (got_hello) ctx->pkt_num = 1;
    /* drain any leftover queued frames */
    while (ctx->hs_head != ctx->hs_tail) {
        free(ctx->hs_queue[ctx->hs_head].payload);
        ctx->hs_head = (ctx->hs_head + 1) % BLE_HS_QUEUE_CAP;
    }
    pthread_mutex_unlock(&ctx->lock);

    /* Success as long as we saw and answered the hello — the serial is a bonus,
     * and there's a fallback for it from the hello id above. */
    return got_hello ? 0 : -1;
}

int libambit_protocol_command_ble(ambit_object_t *object, uint16_t command,
                                   uint8_t *data, size_t datalen,
                                   uint8_t **reply_data, size_t *replylen,
                                   uint8_t legacy_format)
{
    if (legacy_format != 0) {
        LOGE("libambit_protocol_command_ble: legacy_format=%d not supported over BLE "
             "(no hardware reference for it — see protocol_ble.c header comment)", legacy_format);
        return -1;
    }
    if (!object || object->transport != AMBIT_TRANSPORT_BLE || !object->transport_ctx) {
        LOGE("libambit_protocol_command_ble: not a BLE object");
        return -1;
    }
    ble_ctx_t *ctx = (ble_ctx_t *)object->transport_ctx;

    pthread_mutex_lock(&ctx->lock);
    ctx->reply_ready = 0;
    free(ctx->reply_payload);
    ctx->reply_payload = NULL;
    ctx->reply_payload_len = 0;
    pthread_mutex_unlock(&ctx->lock);

    if (ble_send_command(ctx, command, data, datalen) != 0) {
        LOGE("libambit_protocol_command_ble: send failed for command 0x%04x", command);
        return -1;
    }

    struct timespec deadline;
    clock_gettime(CLOCK_REALTIME, &deadline);
    deadline.tv_sec += BLE_REPLY_TIMEOUT_SEC;

    pthread_mutex_lock(&ctx->lock);
    int wait_ret = 0;
    /* Loop past any frame that isn't actually the reply to THIS command, instead of
     * blindly accepting whatever arrives next. Real, 2026-08-21: ambit_ble_on_notify
     * sets reply_ready for any complete, valid-CRC frame - it stores reply_command but
     * this call never used to check it against `command`. A stray/unrelated frame
     * (or a reply that legitimately belongs to an adjacent request when something
     * upstream got out of sequence) would then be accepted as if it were this
     * command's reply; its length almost never matches what the caller expects (e.g.
     * pmem20's read_log_chunk wants exactly length+8), so the call would "succeed"
     * with garbage/truncated data that got silently written into a log entry's buffer
     * - hardware-observed producing near-zero-duration, zero-distance junk activities
     * from a real BLE sync. Now: a mismatched command is dropped and we keep waiting
     * for the real one, up to the same overall deadline. */
    while (wait_ret == 0) {
        while (!ctx->reply_ready && wait_ret == 0) {
            wait_ret = pthread_cond_timedwait(&ctx->cond, &ctx->lock, &deadline);
        }
        if (!ctx->reply_ready) {
            break;
        }
        if (ctx->reply_command != command) {
            LOGE("libambit_protocol_command_ble: got reply for 0x%04x while waiting for 0x%04x "
                 "- discarding and continuing to wait", ctx->reply_command, command);
            ctx->reply_ready = 0;
            free(ctx->reply_payload);
            ctx->reply_payload = NULL;
            ctx->reply_payload_len = 0;
            continue;
        }
        break;
    }
    if (!ctx->reply_ready) {
        pthread_mutex_unlock(&ctx->lock);
        LOGE("libambit_protocol_command_ble: timeout waiting for reply to 0x%04x", command);
        return -1;
    }
    if (ctx->reply_is_error) {
        pthread_mutex_unlock(&ctx->lock);
        LOGE("libambit_protocol_command_ble: watch returned errFlags for command 0x%04x", command);
        return -1;
    }
    if (reply_data != NULL && replylen != NULL) {
        *replylen = ctx->reply_payload_len;
        if (ctx->reply_payload_len > 0) {
            *reply_data = (uint8_t *)malloc(ctx->reply_payload_len);
            memcpy(*reply_data, ctx->reply_payload, ctx->reply_payload_len);
        } else {
            *reply_data = NULL;
        }
    }
    pthread_mutex_unlock(&ctx->lock);

    return 0;
}

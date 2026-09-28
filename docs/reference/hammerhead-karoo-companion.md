# Hammerhead Karoo 3 — how the Companion app sends a route (and why it needs the cloud)

Source: `assets/Hammerhead+Companion_1.56.0_APKPure.apk`, decompiled with jadx on 2026-09-28
(work dir `../hammerhead-re/out`, outside the repo). One `classes.dex`, R8-obfuscated, no native
code of its own; the app's classes keep their names under `io.hammerhead.companionapp`.

Asked by André, 2026-09-28: "we can't send gpx to it offline. this is a feature the community
wants but hammerhead always make it pass by it's servers, can you reverse enginner it and know
the why?"

## Short answer

The Companion app **never sends route bytes to the Karoo**. It is a thin pipe:

1. **Upload to Hammerhead's cloud.** Sharing a GPX/FIT/TCX to the app (`ImportHandler.handle`,
   `ShareIntent.File`) does a multipart `POST /v1/users/{userId}/import/file` (`ServiceApi.uploadFile`,
   part name `file`, `Authorization` from the signed-in session). The server converts the file and
   answers a `PartialDocument {id, name}`. No session → the import fails with `SIGN_IN_REQUIRED`
   before anything touches Bluetooth. (Links go to `POST .../import/url`, APKs to `.../import/apk`.)
2. **Poke the Karoo over BLE.** It sends one message, `DOCUMENT_SYNCED`, whose payload is just the
   document id as UTF-8 (`ServerMessage.DocumentSynced.encode`).
3. **The Karoo syncs from the cloud itself**, then reports `SYNC_COMPLETE` with sync tag `"route"`
   (`DocType.ROUTE`); the app only watches that progress.

So the "why" is architectural: on the Karoo the Hammerhead account is the single source of truth
for routes (the same library the web dashboard edits), and the route the Karoo loads is the
server's converted document, not the GPX. There is simply no "file" message in the BLE protocol.
Any business reason (account lock-in, SRAM ecosystem) is our inference, not something the code says.

## The lever for an offline path: the Karoo's internet goes *through the phone*

When the Karoo has no Wi-Fi, its cloud sync runs over the phone: `HTTPProxyService`. The Karoo
sends the request **in pieces over BLE** (`HTTP_URL`, `HTTP_HEADERS`, `HTTP_BODY`,
`HTTP_REQUEST_TYPE`, `HTTP_TIMEOUT`, `HTTP_TX_ID`, `HTTP_TAG`), the phone performs it with OkHttp,
and streams back `HTTP_STATUS_CODE` / `HTTP_HEADERS` / `HTTP_BODY` / `HTTP_FAILURE`.

- No host allowlist in the proxy, and no app-layer encryption or signing: TLS ends at the phone.
  The only protection is BLE bonding (characteristics use the *encrypted* read/write permissions).
- Therefore a companion of ours that implements the same GATT server could **answer the Karoo's own
  route-sync requests locally**: send `DOCUMENT_SYNCED(<our id>)`, then serve the route document when
  the Karoo asks the "cloud" for it. That is the plausible offline GPX path.

What we do **not** know yet (it lives in the Karoo firmware, not this APK): the exact URLs and JSON
the Karoo requests during a route sync, what a route document looks like, and whether the Karoo
accepts a companion that doesn't complete the account handshake (`REQUEST_AUTH` / `AUTH_HANDOFF` /
`PROXY_AUTH`; `KarooCapabilities` carries `userId`, `email`, `deviceId`). First step is a
**logging proxy**: our GATT server forwards to the real cloud unchanged and records every request
and response of one real route sync. That capture tells us what to emulate.

Also worth testing separately (not from this APK): the Karoo 3 is Android and we already enable its
developer options for MTP, so an ADB-side import may exist.

## Protocol facts (from the APK)

The **phone is the GATT server**; the Karoo is the client. The phone advertises the service.

| UUID | Role | Props / perms |
|---|---|---|
| `9baf0001-7deb-4901-b0ca-d98764f41cc6` | service | |
| `9baf0002-…` | notification (ANCS-like) | notify+read, read-enc |
| `9baf0003-…` | notification attribute | write+notify / write-enc |
| `9baf0005-…` | message event (chunk/reset requests) | read+write+notify / read-enc+write-enc |
| `9baf0006-…` | receive message chunk (Karoo → phone) | write+write-no-resp / write-enc |
| `9baf0007-…` | send message chunk (phone → Karoo) | notify / write-enc |
| `2f7cabce-808d-411f-9a0c-bb92ba96c102` | media update | |
| `9b3c81d8-57b1-4a8a-b8df-0e56f7ca51c2` | media command | |

Message transport is pull-based: the Karoo writes a `ChunkRequest` (or `ResetRequest` with
`sequenceNumber`, `receivedBytes`) to the message-event characteristic, and the phone notifies the
next slice of at most `currentMaxMessageSize` bytes with a sequence byte that increments per chunk
(`KarooServerMessageFragmenter`, `KarooClientMessageBuilder`). Exact header byte layout: not yet
pinned, read the smali before building.

Server (phone → Karoo) message types, by ordinal: CAPABILITIES 0, HTTP_STATUS_CODE 1,
HTTP_HEADERS 2, HTTP_BODY 3, HTTP_FAILURE 4, SHARED_LOCATION 5, REQUEST_AUTH 6, HTTP_TX_ID 7,
AUTH_HANDOFF 8, DOCUMENT_SYNCED 9, INTERNET_CHECK_RESPONSE 10, PAIRING_COMPLETED 11,
SHARED_APK_URL 12, LIVE_TRACKING 13.

Client (Karoo → phone) types: CAPABILITIES 0, HTTP_URL 1, HTTP_HEADERS 2, HTTP_BODY 3,
HTTP_TIMEOUT 4, HTTP_REQUEST_TYPE 5, PROXY_AUTH 6, INTERNET_CHECK 7, HTTP_TX_ID 8,
MESSAGE_RESULT 9, HTTP_TAG 10, LIVE_TRACKING 11, KEEP_ALIVE 12, SYNC_COMPLETE 13,
REQUEST_CAPABILITIES 14, INTERNET_CHECK_V2 15, BATTERY_STATUS 16, INITIATE_PARTNER_SYNC 17.

Doc types / sync tags: `activity`, `tracking`, `route`, `workout`.

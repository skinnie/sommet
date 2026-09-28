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

### Framing (pinned 2026-09-28, `MessageEvent`, `KarooGattServer`, fragmenter/builder)

Integers little-endian (`EncodedInteger16/32`) unless noted. Same framing both directions:

| Bytes | Meaning | Where |
|---|---|---|
| `00 type:u16 len:u32` | START of a message | message-event char (write by Karoo / notify by phone) |
| `seq:u8 data…` | chunk, `seq` from 0, +1 per chunk | Karoo→phone: write `9baf0006`; phone→Karoo: notify `9baf0007` |
| `01 type:u16` | END | message-event |
| `02 type:u16 received:u32 seq:u8` | RESET: resend from byte `received` with `seq` | message-event |
| `03 type:u16 count:u8` | REQUEST: Karoo pulls `count` more chunks of the phone's message | message-event (write) |

- Phone→Karoo is **pulled**: phone notifies START, then sends one chunk per chunk request, at most
  `MTU − 4` data bytes after the seq byte. When the index reaches the length, the next request
  gets END instead of a chunk. Messages go out one at a time, in order.
- Karoo→phone is **pushed**: START, chunks, END. An out-of-order seq makes the phone notify RESET.
- HTTP proxy details: `HTTP_TIMEOUT` is a **big-endian** int32; `HTTP_REQUEST_TYPE` (one byte,
  GET=0 HEAD POST PUT DELETE PATCH CONNECT OPTIONS TRACE) completes the request. Headers are
  `key:value` joined by `≤≥`. Reply: `HTTP_STATUS_CODE` (u16 LE), `HTTP_HEADERS`, `HTTP_TX_ID`,
  `HTTP_BODY`; an I/O error → `HTTP_TX_ID`, `HTTP_FAILURE`. 4xx/5xx are normal replies.
- Handshake: on subscribe, on `REQUEST_CAPABILITIES` and on the old `INTERNET_CHECK`, the phone
  sends CAPABILITIES JSON (`version`, `supportsHttp`, `hasInternet`, `supportsInternetCheck`,
  `displaysLiveTracking`, `internetCheckV2`, `pairingCompleted`, `clientMsgTypes`,
  `serverMsgTypes`); `INTERNET_CHECK_V2` gets `INTERNET_CHECK_RESPONSE {"hasInternet": bool}`.
- Pairing: the phone gets the Karoo's MAC (QR), `createBond()`s it, stores it, and its GATT server
  **dials** the bonded Karoo (`gattServer.connect(device, autoConnect=true)`) while advertising the
  service UUID. Only a bonded device can be the active session.

## Logging proxy: `tools/karoo_proxy.py`

Built 2026-09-28 from the facts above; the framing and proxy logic are covered offline by
`tools/test_karoo_proxy.py` (14 tests, a fake Karoo drives the byte exchange). **Not yet run
against a real Karoo.**

    ./tools/karoo_proxy.py listen --karoo AA:BB:CC:DD:EE:FF      # pair/connect, proxy, log
    ./tools/karoo_proxy.py listen --karoo … --document-synced <route id>   # also trigger a route sync
    ./tools/karoo_proxy.py show ~/.cache/AmbitApp/karoo_proxy/<file>.jsonl --bodies

Every proxied request/response lands in a 0600 JSONL capture (it holds the Karoo's own auth
headers). BlueZ smoke test on the X230, 2026-09-28: the GATT service registers; a *connectable*
advertisement is refused (`Invalid Parameters`) while `tools/ble_server.py` is running its
continuous scan (a non-connectable advert registers fine), so use `--karoo` to dial, as the
Companion itself does. Plan for the first hardware session: turn Wi-Fi off on the Karoo, close the
Hammerhead app on the phone (or turn its Bluetooth off), run with `--karoo`, then start a route
sync on the Karoo or pass `--document-synced` with an id from the dashboard.

## Plan B: a sideloaded helper APK (checked on the real Karoo 3, 2026-09-28)

André's idea, same day: Hammerhead lets developers sideload APKs. Checked over ADB with the Karoo
plugged in (model `k24`, Android 12 / SDK 32, `user` build, no root, system apps 4.215.1):

- **No app opens GPX files.** `pm query-activities -a VIEW` finds nothing for
  `application/gpx+xml`, `application/octet-stream`, `application/vnd.ant.fit` or `text/xml`.
- **The routes app has no import entry point.** `io.hammerhead.routesapp` (pulled from
  `/system/priv-app/routes`, jadx) declares `ADD_ROUTE` (just opens the route list; the `from`
  extra is analytics), `CREATE_ROUTE`, `EDIT_ROUTE`, and a `MOCK_ROUTE` receiver (simulates riding
  an *existing* `routeId`). It only displays what `datasyncservice` holds.
- **But `datasyncservice` exposes its database to any app.** `io.hammerhead.datasyncservice.v2.
  DataSyncService` is `exported="true"` with **no permission**, and `onBind` hands out two binders
  by intent action: `loginController` and `databaseOperationsController`
  (`io.hammerhead.datasyncservice.v2.DatabaseOperationsAIDL`, class `j7/k`). No
  `getCallingUid`/permission check anywhere in its dispatcher. Transactions include:
  - 5/22 read a document (22 = as JSON, via the type's "share reader"), chunked by transaction id
  - 6 `putDocument(txId, docType, bytes, done)` (parcel form)
  - 7 `deleteDocument`, 8 `documentExists`, 9 `getDocumentTypeList`, 10 count, 11 attachment
  - 19 `getRouteController` (a further binder, not yet read)
  - **23 `putDocumentAsJson(txId, docType, bytes, done)` → returns the new doc id**
- The store is **Couchbase Lite**. Type `"route"` (`k7/w0`) implements the JSON reader and writer:
  `m(json)` parses a `Route`, gives it a fresh UUID id and `createdAt`/`updatedAt`, clears one list
  field, and saves it. Its cloud sync endpoint is `routes/sync?updatedAt=…&page=1&per_page=10`.
- `Route` (`io.hammerhead.datamodels.routes.Route`) fields: `id, name, distance, routePolyline,
  summaryPolyline, elevation, bounds, startLocation, endLocation, startLocationName,
  endLocationName, waypoints, pointsOfInterest, stemSheet` (cues), `routingType, routingConfig,
  surfaceSummary, source, sourceId, collections, isStarred, isPublic, isAutoImported,
  imageVersion, createdAt, updatedAt`.

So a small sideloaded app can very likely insert a route **locally, with no cloud and no proxy**:
bind `DataSyncService` with action `databaseOperationsController`, call transaction 23 with
`docType="route"` and the route JSON. Unknowns, in order: the exact JSON shape (polyline encoding
and precision, the `elevation` object, whether turn-by-turn needs a `stemSheet`), and whether the
next cloud sync keeps or removes a route the server has never seen. The safe first step is
read-only: have the helper read one existing route with transaction 22 and dump its JSON.
Transactions 9/10/22 are reads; 6/7/23 write.

Server (phone → Karoo) message types, by ordinal: CAPABILITIES 0, HTTP_STATUS_CODE 1,
HTTP_HEADERS 2, HTTP_BODY 3, HTTP_FAILURE 4, SHARED_LOCATION 5, REQUEST_AUTH 6, HTTP_TX_ID 7,
AUTH_HANDOFF 8, DOCUMENT_SYNCED 9, INTERNET_CHECK_RESPONSE 10, PAIRING_COMPLETED 11,
SHARED_APK_URL 12, LIVE_TRACKING 13.

Client (Karoo → phone) types: CAPABILITIES 0, HTTP_URL 1, HTTP_HEADERS 2, HTTP_BODY 3,
HTTP_TIMEOUT 4, HTTP_REQUEST_TYPE 5, PROXY_AUTH 6, INTERNET_CHECK 7, HTTP_TX_ID 8,
MESSAGE_RESULT 9, HTTP_TAG 10, LIVE_TRACKING 11, KEEP_ALIVE 12, SYNC_COMPLETE 13,
REQUEST_CAPABILITIES 14, INTERNET_CHECK_V2 15, BATTERY_STATUS 16, INITIATE_PARTNER_SYNC 17.

Doc types / sync tags: `activity`, `tracking`, `route`, `workout`.

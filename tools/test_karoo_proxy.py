#!/usr/bin/env python3
"""Offline tests for karoo_proxy.py's framing and HTTP proxy logic. No Karoo, no Bluetooth,
no network: a fake Karoo drives the same byte-level exchange the Companion's
KarooGattServer / KarooServerMessageFragmenter / KarooClientMessageBuilder implement.

    ./tools/test_karoo_proxy.py            # or: python3 -m unittest test_karoo_proxy
"""
import json
import struct
import threading
import unittest

import karoo_proxy as K


class FakeKaroo:
    """The Karoo end of the pull-based transport: collects our notifications and pulls chunks
    with REQUEST events until END, the way the fragmenter expects to be driven."""

    def __init__(self, mtu=23):
        self.events, self.chunks, self.received = [], [], []
        self.sender = K.ServerSender(self.events.append, self.chunks.append, mtu=lambda: mtu)

    def pull_all(self, per_request=1):
        """Drain every queued server message; returns [(type, bytes)]."""
        out = []
        while self.events:
            ev = self.events.pop(0)
            if ev[0] != K.EV_START:
                continue
            msg_type, length = struct.unpack_from("<HI", ev, 1)
            data, expected_seq = b"", 0
            while True:
                self.sender.request(msg_type, per_request)
                while self.chunks:
                    chunk = self.chunks.pop(0)
                    assert chunk[0] == expected_seq, (chunk[0], expected_seq)
                    expected_seq += 1
                    data += chunk[1:]
                if self.events and self.events[0][0] == K.EV_END:
                    self.events.pop(0)
                    break
            assert len(data) == length
            out.append((msg_type, data))
        return out


def karoo_send(assembler, msg_type, data, chunk_size=19):
    """Karoo -> us: START, [seq][data] chunks, END."""
    assembler.event(K.event_bytes(K.EV_START, msg_type, len(data)))
    for seq, i in enumerate(range(0, len(data), chunk_size)):
        assembler.chunk(bytes([seq]) + data[i:i + chunk_size])
    assembler.event(K.event_bytes(K.EV_END, msg_type))


class Framing(unittest.TestCase):
    def test_event_layout_matches_toNotifyBytes(self):
        self.assertEqual(K.event_bytes(K.EV_START, 9, 300).hex(), "00" "0900" "2c010000")
        self.assertEqual(K.event_bytes(K.EV_END, 9).hex(), "01" "0900")
        self.assertEqual(K.event_bytes(K.EV_RESET, 1, 40, 3).hex(), "02" "0100" "28000000" "03")

    def test_server_message_roundtrip_at_default_and_large_mtu(self):
        payload = bytes(range(256)) * 3
        for mtu in (23, 185, 517):
            with self.subTest(mtu=mtu):
                karoo = FakeKaroo(mtu)
                karoo.sender.send(K.S_HTTP_BODY, payload)
                self.assertEqual(karoo.pull_all(), [(K.S_HTTP_BODY, payload)])
                # chunk data is capped at mtu - 4 (KarooGattServer.getCurrentMaxMessageSize)

    def test_chunks_never_exceed_mtu_minus_3_on_the_wire(self):
        karoo = FakeKaroo(23)
        karoo.sender.send(K.S_HTTP_BODY, b"x" * 100)
        karoo.events.pop(0)
        karoo.sender.request(K.S_HTTP_BODY, 10)
        self.assertTrue(all(len(c) <= 20 for c in karoo.chunks))

    def test_empty_message_is_start_then_end(self):
        karoo = FakeKaroo()
        karoo.sender.send(K.S_HTTP_BODY, b"")
        self.assertEqual(karoo.pull_all(), [(K.S_HTTP_BODY, b"")])

    def test_messages_are_sent_one_at_a_time_in_order(self):
        karoo = FakeKaroo()
        karoo.sender.send(K.S_HTTP_STATUS_CODE, b"\xc8\x00")
        karoo.sender.send(K.S_HTTP_HEADERS, b"a:b")
        self.assertEqual(len([e for e in karoo.events if e[0] == K.EV_START]), 1)
        self.assertEqual([t for t, _ in karoo.pull_all()], [K.S_HTTP_STATUS_CODE, K.S_HTTP_HEADERS])

    def test_batched_requests(self):
        karoo = FakeKaroo()
        karoo.sender.send(K.S_CAPABILITIES, b"y" * 95)
        self.assertEqual(karoo.pull_all(per_request=4), [(K.S_CAPABILITIES, b"y" * 95)])

    def test_reset_resends_from_named_byte(self):
        karoo = FakeKaroo()
        karoo.sender.send(K.S_HTTP_BODY, bytes(range(60)))
        karoo.events.pop(0)
        karoo.sender.request(K.S_HTTP_BODY, 2)          # bytes 0..37, seq 0 and 1
        karoo.chunks.clear()
        karoo.sender.reset(K.S_HTTP_BODY, 19, 1)        # "lost seq 1, resend from byte 19"
        self.assertEqual(karoo.chunks[-1], bytes([1]) + bytes(range(19, 38)))

    def test_client_message_reassembly(self):
        got = []
        asm = K.ClientAssembler(lambda t, d: got.append((t, d)))
        karoo_send(asm, K.C["HTTP_URL"], b"https://example.invalid/" + b"p" * 50)
        self.assertEqual(got, [(K.C["HTTP_URL"], b"https://example.invalid/" + b"p" * 50)])

    def test_out_of_sequence_chunk_asks_for_reset_and_drops_short_message(self):
        got, resets = [], []
        asm = K.ClientAssembler(lambda t, d: got.append(d),
                                on_reset=lambda *a: resets.append(a))
        asm.event(K.event_bytes(K.EV_START, 3, 30))
        asm.chunk(b"\x00" + b"a" * 19)
        asm.chunk(b"\x02" + b"c" * 11)                  # seq 1 lost
        self.assertEqual(resets, [(3, 19, 1)])
        asm.chunk(b"\x01" + b"b" * 11)                  # Karoo resent from byte 19
        asm.event(K.event_bytes(K.EV_END, 3))
        self.assertEqual(got, [b"a" * 19 + b"b" * 11])


class Proxy(unittest.TestCase):
    def run_session(self, messages, performer, **kw):
        sent, log, done = [], [], threading.Event()

        def send(t, d):
            sent.append((t, d))
            if t == K.S_HTTP_BODY or t == K.S_HTTP_FAILURE:
                done.set()

        session = K.Session(send, log.append, performer=performer, **kw)
        asm = K.ClientAssembler(session.on_message)
        for msg_type, data in messages:
            karoo_send(asm, msg_type, data)
        return session, sent, log, done

    def http_messages(self, method=2, body=b'{"a":1}'):
        return [(K.C["HTTP_URL"], b"https://api.example.invalid/v1/x"),
                (K.C["HTTP_HEADERS"], "Authorization:Bearer t≤≥Content-Type:application/json".encode()),
                (K.C["HTTP_BODY"], body),
                (K.C["HTTP_TIMEOUT"], struct.pack(">i", 40)),
                (K.C["HTTP_TX_ID"], b"tx-1"),
                (K.C["HTTP_TAG"], b"route"),
                (K.C["HTTP_REQUEST_TYPE"], bytes([method]))]

    def test_request_is_forwarded_and_answered_in_companion_order(self):
        seen = {}

        def performer(req):
            seen.update(method=req.method, url=req.url, timeout=req.timeout, body=req.body,
                        headers=K.parse_headers(req.headers))
            return 201, [("Content-Type", "application/json")], b'{"ok":true}'

        _, sent, log, done = self.run_session(self.http_messages(), performer)
        self.assertTrue(done.wait(5))
        self.assertEqual(seen["method"], "POST")
        self.assertEqual(seen["timeout"], 40)
        self.assertEqual(seen["headers"], [("Authorization", "Bearer t"),
                                           ("Content-Type", "application/json")])
        self.assertEqual([t for t, _ in sent], [K.S_HTTP_STATUS_CODE, K.S_HTTP_HEADERS,
                                                K.S_HTTP_TX_ID, K.S_HTTP_BODY])
        self.assertEqual(sent[0][1], struct.pack("<H", 201))
        self.assertEqual(sent[1][1], "Content-Type:application/json".encode())
        self.assertEqual(sent[2][1], b"tx-1")
        self.assertEqual(sent[3][1], b'{"ok":true}')
        entry = next(e for e in log if e.get("dir") == "http")
        self.assertEqual((entry["tag"], entry["status"]), ("route", 201))
        self.assertEqual(entry["response_body"], {"text": '{"ok":true}'})

    def test_transport_error_becomes_tx_id_then_failure(self):
        def performer(req):
            raise OSError("no route to host")

        _, sent, _, done = self.run_session(self.http_messages(method=0, body=b""), performer)
        self.assertTrue(done.wait(5))
        self.assertEqual([t for t, _ in sent], [K.S_HTTP_TX_ID, K.S_HTTP_FAILURE])

    def test_handshake_answers(self):
        _, sent, _, _ = self.run_session([(K.C["REQUEST_CAPABILITIES"], b""),
                                          (K.C["INTERNET_CHECK_V2"], b"")], performer=None)
        self.assertEqual(sent[0][0], K.S_CAPABILITIES)
        caps = json.loads(sent[0][1])
        self.assertTrue(caps["supportsHttp"] and caps["hasInternet"])
        self.assertEqual(caps["serverMsgTypes"], list(range(14)))
        self.assertEqual(sent[1], (K.S_INTERNET_CHECK_RESPONSE, b'{"hasInternet": true}'))

    def test_document_synced_follows_karoo_capabilities_once(self):
        caps = json.dumps({"deviceId": "k1", "romVersion": "x"}).encode()
        _, sent, _, _ = self.run_session([(K.C["CAPABILITIES"], caps), (K.C["CAPABILITIES"], caps)],
                                         performer=None, document_synced="route-123")
        self.assertEqual(sent, [(K.S_DOCUMENT_SYNCED, b"route-123")])

    def test_binary_body_logged_as_base64(self):
        self.assertIn("base64", K.body_for_log(b"\x00\xff\x10", [("Content-Type",
                                                                   "application/octet-stream")]))


if __name__ == "__main__":
    unittest.main()

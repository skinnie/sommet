#!/usr/bin/env python3
"""The Windows Bluetooth daemon's parts that need no Windows (tools/ble_server_win.py).

The WinRT side itself only runs on Windows in a desktop session; it was proven there by hand
(P5Q, 2026-11-21: the watch subscribed and wrote its opener). Checked here:
  - the GLib-style timers ServerLink paces its chunks with, on an asyncio loop;
  - write chunks finishing out of order are still reassembled in the order they came;
  - chunks Windows hands over in the wrong order are put back (the real opener from the P5Q);
  - which adverts count as the watch calling;
  - the TCP control socket: the real ble_bridge client reaches it through the port file, a
    request without the right token is refused, status and passkey work.
"""
import asyncio
import json
import pathlib
import sys
import tempfile
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop" / "backend"))

import ble_server_win as w                                     # noqa: E402
from ble_link import NspAssembler, encode_nsp_frame            # noqa: E402


def test_timers_repeat_until_false():
    async def run():
        t = w.LoopTimers(asyncio.get_running_loop())
        hits, done = [], asyncio.Event()

        def tick(tag):
            hits.append(tag)
            if len(hits) == 3:
                done.set()
                return False
            return True
        # from another thread, the way ServerLink.command() calls it
        threading.Thread(target=t.timeout_add, args=(5, tick, "x")).start()
        await asyncio.wait_for(done.wait(), 2)
        await asyncio.sleep(0.05)
        return hits
    assert asyncio.run(run()) == ["x", "x", "x"]


def test_chunks_in_order():
    frame = encode_nsp_frame(0x1201, bytes(range(17)), 0x06, 9, 0)
    chunks = [frame[i:i + 20] for i in range(0, len(frame), 20)]
    got = []
    asm = NspAssembler()
    order = w.InOrder(lambda c: got.extend(asm.feed(c)))
    tickets = [order.ticket() for _ in chunks]
    for n in reversed(tickets):                # the second chunk's value arrives first
        order.finish(n, chunks[n])
    assert len(got) == 1 and got[0].crc_ok and got[0].command == 0x1201
    # an unreadable chunk does not hold the rest back
    order2 = w.InOrder(got.append)
    a, b = order2.ticket(), order2.ticket()
    order2.finish(b, b"B")
    order2.finish(a, None)
    assert got[-1] == b"B"


# The watch's opener exactly as Windows handed it over on the P5Q (2026-11-21): its second chunk
# first. In arrival order the assembler saw a damaged frame.
OPENER = [bytes.fromhex("42454d30313032f601005d8d1b1f7e"),
          bytes.fromhex("7e12010600090001001100000000000000010153")]


def _reassembled(chunks, **kw):
    asm, got = NspAssembler(), []
    r = w.Reorder(lambda c: got.extend(asm.feed(c)), **kw)
    for c in chunks:
        r.feed(c)
    return got


def test_reorder_real_opener():
    got = _reassembled(OPENER)
    assert [(m.command, m.crc_ok) for m in got] == [(0x1201, True)], got


def test_reorder_long_frame():
    import random
    payload = bytes(random.Random(7).randrange(256) for _ in range(900)) + b"\x7e\x7d" * 20
    frame = encode_nsp_frame(0x1200, payload, 0x0A, 9, 5)
    chunks = [frame[i:i + 20] for i in range(0, len(frame), 20)]
    assert len(chunks) > 40
    for a, b in ((3, 4), (10, 12), (len(chunks) - 2, len(chunks) - 1), (0, 1)):
        shuffled = list(chunks)
        shuffled[a], shuffled[b] = shuffled[b], shuffled[a]
        got = _reassembled(shuffled)
        assert len(got) == 1 and got[0].crc_ok and got[0].payload == payload, (a, b)
    # two frames back to back, each with a swap, and a third in order
    f2 = encode_nsp_frame(0x0002, b"hello" * 9, 0x0A, 9, 6)
    c2 = [f2[i:i + 20] for i in range(0, len(f2), 20)]
    got = _reassembled(OPENER + [c2[1], c2[0], *c2[2:]] + chunks)
    assert [m.command for m in got] == [0x1201, 0x0002, 0x1200] and all(m.crc_ok for m in got)


def test_reorder_gives_up_on_a_damaged_frame():
    now = [0.0]
    asm, got = NspAssembler(), []
    r = w.Reorder(lambda c: got.extend(asm.feed(c)), clock=lambda: now[0])
    r.feed(OPENER[1])                        # its other half never comes
    now[0] = 5.0
    frame = encode_nsp_frame(0x0002, b"x" * 30, 0x0A, 9, 1)
    for i in range(0, len(frame), 20):
        r.feed(frame[i:i + 20])
    assert [m.command for m in got if m.crc_ok] == [0x0002]


def test_is_watch():
    nsp = "98ae7120-e62e-11e3-badd-0002a5d5c51b"
    assert w.is_watch("", 0x112233445566, [nsp.upper()])                     # the P5Q's advert
    assert w.is_watch("Ambit3 123", 0x112233445566, [])
    assert w.is_watch("", 0x0C8CDC010203, [])                                # Suunto prefix
    assert w.is_watch("", 0x112233445566, [], known={0x112233445566})        # paired before
    assert not w.is_watch("Headphones", 0x112233445566, ["0000110b-0000-1000-8000-00805f9b34fb"])
    assert w.address_text(0x0C8CDC010203) == "0C:8C:DC:01:02:03"


class _Notify:
    notifying = True


class _Link:
    def __init__(self):
        self.handshake_done = threading.Event()
        self.handshake_done.set()
        self.device_info = {"model": "Ambit3"}
        self.notify_chrc = _Notify()
        self.dry_run = True
        self.sync_notices = 0
        self.last_sync_notice_at = None


def test_tcp_control_through_the_bridge():
    import ble_bridge
    with tempfile.TemporaryDirectory() as tmp:
        port_file = pathlib.Path(tmp) / "ble.port"
        agent = w.Agent()
        state = w.LinkState()
        state.path = "Ambit3"
        server = w.TcpControlServer(_Link(), [42], agent=agent, link_state=state,
                                    forget=lambda: 1, port_file=port_file)
        sock = server.bind()
        threading.Thread(target=server.serve_forever, args=(sock,), daemon=True).start()
        ble_bridge.WINDOWS, ble_bridge.PORT_FILE = True, port_file
        b = ble_bridge.BleBridge()
        st = b.status()
        assert st["running"] and st["handshake_done"] and st["rx_total"] == 42
        assert st["linked_device"] == "Ambit3" and st["pending_passkey_device"] is None
        assert b.forget() == 1
        # the passkey reaches a pairing waiting for it
        got = []
        t = threading.Thread(target=lambda: got.append(agent.wait("Ambit3", 5)))
        t.start()
        while agent.pending_passkey_device is None:
            time.sleep(0.01)
        assert b.status()["pending_passkey_device"] == "Ambit3"
        assert b.submit_passkey(123456)
        t.join(2)
        assert got == [123456]
        # without the token: refused
        info = json.loads(port_file.read_text())
        import socket
        s = socket.create_connection(("127.0.0.1", info["port"]), 2)
        s.sendall(b'{"op": "status"}\n')
        reply = json.loads(s.makefile().readline())
        s.close()
        assert reply == {"ok": False, "error": "bad token"}
        assert w.other_daemon(port_file)          # a second daemon would refuse to start
        server.remove_port_file()
        assert not port_file.exists() and not w.other_daemon(port_file)


if __name__ == "__main__":
    test_timers_repeat_until_false()
    test_chunks_in_order()
    test_reorder_real_opener()
    test_reorder_long_frame()
    test_reorder_gives_up_on_a_damaged_frame()
    test_is_watch()
    test_tcp_control_through_the_bridge()
    print("ble_server_win: timers, chunk order, reordering, watch match, TCP control socket OK")

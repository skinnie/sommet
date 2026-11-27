"""Keeps the desktop backend's tests away from the real Bluetooth daemon.

The backend asks the running daemon (ble_server.py, over its control socket) whether a watch is
linked, and when one is, it talks to that watch. A test that loads `server` without cutting this
off follows whatever is really linked: on 2026-11-27 tools/test_backend_legacy_edits.py read the
routes and POI areas of André's Kailash over Bluetooth, about 690 reads in four runs, while he
was testing its pairing. Nothing was written, but the test failed whenever a watch was linked
(the flaky test of that week) and drove the watch for minutes.

    import server
    import no_live_bluetooth
    no_live_bluetooth.cut_off(server)

After that, the bridge reports no watch, and any request or daemon start raises instead of
reaching the socket. A test may still stub `status` itself for a linked watch it makes up.
"""


def cut_off(server):
    bridge_module = server.ble_bridge
    bridge = bridge_module.bridge

    def refuse(*_args, **_kwargs):
        raise bridge_module.BleBridgeError("a test never reaches the real Bluetooth daemon")

    bridge.status = lambda: {}
    bridge._request = refuse
    bridge.start = refuse
    return bridge

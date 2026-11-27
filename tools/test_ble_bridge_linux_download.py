#!/usr/bin/env python3
"""The Linux download starts its Bluetooth daemon with the system's Python.

The daemon (tools/ble_server.py) needs the system's `dbus` and `gi` modules, which the packaged
backend does not hold: in the download it died on "No module named 'dbus'" right after the
password prompt (P5Q, Mint, 2026-11-22). No watch, no Bluetooth, no root.

    python3 tools/test_ble_bridge_linux_download.py
"""
import os
import pathlib
import sys
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "desktop" / "backend"))
sys.path.insert(0, str(HERE))
import ble_bridge as B  # noqa: E402
import server  # noqa: E402
import no_live_bluetooth  # noqa: E402
no_live_bluetooth.cut_off(server)


class Command(unittest.TestCase):
    def setUp(self):
        self.saved = (B.FROZEN, B.LINUX_FROZEN, B.system_python_with_dbus, dict(os.environ))

    def tearDown(self):
        B.FROZEN, B.LINUX_FROZEN, B.system_python_with_dbus, env = self.saved
        os.environ.clear()
        os.environ.update(env)

    def test_from_source_it_is_this_python(self):
        B.FROZEN = B.LINUX_FROZEN = False
        argv, env = B.daemon_command("/x/ble_server.py")
        self.assertEqual(argv, [sys.executable, "-u", "/x/ble_server.py"])
        self.assertEqual(env["PYTHONUNBUFFERED"], "1")

    def test_a_download_that_is_not_linux_runs_its_own_tool(self):
        B.FROZEN, B.LINUX_FROZEN = True, False
        argv, _env = B.daemon_command("/x/ble_server_win.py")
        self.assertEqual(argv, [B.PYTHON, "--tool", "/x/ble_server_win.py"])

    def test_the_linux_download_uses_the_system_python_with_none_of_the_apps_paths(self):
        B.FROZEN = B.LINUX_FROZEN = True
        B.system_python_with_dbus = lambda: "/usr/bin/python3"
        os.environ.update({"LD_LIBRARY_PATH": "/tmp/.mount_Sommet/usr/lib", "PYTHONHOME": "/tmp/_MEI1",
                           "PYTHONPATH": "/tmp/_MEI1", "GI_TYPELIB_PATH": "/tmp/.mount_Sommet/typelib",
                           "HOME": "/home/someone", "XDG_RUNTIME_DIR": "/run/user/1000"})
        argv, env = B.daemon_command("/tmp/_MEI1/tools/ble_server.py")
        self.assertEqual(argv, ["/usr/bin/python3", "-u", "/tmp/_MEI1/tools/ble_server.py"])
        for leaked in ("LD_LIBRARY_PATH", "PYTHONHOME", "PYTHONPATH", "GI_TYPELIB_PATH"):
            self.assertNotIn(leaked, env)
        self.assertEqual(env["HOME"], "/home/someone")
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/run/user/1000")
        self.assertTrue(env["PATH"].startswith("/usr/local/sbin:/usr/local/bin"))
        self.assertEqual(env["SOMMET_PARENT_PID"], str(os.getpid()))

    def test_without_the_two_system_packages_it_says_which_and_how(self):
        B.FROZEN = B.LINUX_FROZEN = True
        B.system_python_with_dbus = lambda: None
        with self.assertRaises(B.BleBridgeError) as e:
            B.daemon_command("/x/ble_server.py")
        for word in ("python3-dbus", "python3-gi", "sudo apt install", "cable"):
            self.assertIn(word, str(e.exception))


class NoHelperWhenItCannotWork(unittest.TestCase):
    def test_the_check_comes_before_the_helper_starts(self):
        saved = (server.ble_bridge.daemon_command, server.ble_bridge.bridge)

        class Bridge:
            started = False

            def status(self):
                return {"running": False}

            def start(self, **_):
                Bridge.started = True

        def refuse(_script):
            raise server.ble_bridge.BleBridgeError(server.ble_bridge.DBUS_MODULES_HINT)
        server.ble_bridge.daemon_command = refuse
        server.ble_bridge.bridge = Bridge()
        try:
            sent = []
            h = server.Handler.__new__(server.Handler)
            h._send_json = lambda code, obj: sent.append((code, obj))
            h._handle_ble_connect({})
        finally:
            server.ble_bridge.daemon_command, server.ble_bridge.bridge = saved
        self.assertFalse(Bridge.started)
        self.assertEqual(sent[0][0], 502)
        self.assertIn("python3-dbus", sent[0][1]["error"])


class ThisComputer(unittest.TestCase):
    def test_the_probe_agrees_with_this_python(self):
        """On a computer that has the two packages the probe finds a Python; else None."""
        B._SYSTEM_PYTHON.clear()
        found = B.system_python_with_dbus()
        B._SYSTEM_PYTHON.clear()
        if found is not None:
            self.assertTrue(os.path.exists(found))


if __name__ == "__main__":
    unittest.main()

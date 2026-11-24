"""The environment for starting the system's own programs (bluetoothctl, btmgmt, busctl,
notify-send) from inside the packaged app.

The frozen backend sets LD_LIBRARY_PATH to its own bundled libraries, and a system program
started with it loads the bundle's older GLib: on the P5Q (Linux Mint 22.1, 2026-11-24)
bluetoothctl stopped with "undefined symbol: g_string_free_and_steal". Sommet then saw no
paired watch at all: Forget had nothing to remove, and a start with a watch already paired was
taken for a fresh pairing and asked for the password. notify-send failed the same way.
"""

import os

# Paths that point into the package: the AppImage's mount (/tmp/.mount_*) or PyInstaller's
# folder (_MEI*). PyInstaller keeps what was there before it in <NAME>_ORIG.
LEAKS = ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "GI_TYPELIB_PATH",
         "GDK_PIXBUF_MODULE_FILE", "GTK_PATH", "QT_PLUGIN_PATH", "GIO_MODULE_DIR")


def _ours(value):
    return "/.mount_" in value or "/_MEI" in value


def system_env(environ=None):
    """A copy of the environment without the package's own library and Python paths."""
    env = dict(os.environ if environ is None else environ)
    for name in LEAKS:
        before = env.pop(name + "_ORIG", None)
        env.pop(name, None)
        if before and not _ours(before):
            env[name] = before
    if "XDG_DATA_DIRS" in env:
        kept = [p for p in env["XDG_DATA_DIRS"].split(":") if p and not _ours(p)]
        if kept:
            env["XDG_DATA_DIRS"] = ":".join(kept)
        else:
            env.pop("XDG_DATA_DIRS")
    return env


def selftest():
    env = system_env({
        "HOME": "/home/a", "PATH": "/usr/bin",
        "LD_LIBRARY_PATH": "/tmp/.mount_SommetX/usr/bin/backend:/tmp/_MEI1",
        "LD_LIBRARY_PATH_ORIG": "/opt/mine/lib",
        "PYTHONHOME": "/tmp/_MEI1", "GI_TYPELIB_PATH": "/tmp/.mount_SommetX/usr/lib/girepository",
        "XDG_DATA_DIRS": "/tmp/.mount_SommetX/usr/share:/usr/share",
        "XDG_RUNTIME_DIR": "/run/user/1000", "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"})
    assert env == {"HOME": "/home/a", "PATH": "/usr/bin", "LD_LIBRARY_PATH": "/opt/mine/lib",
                   "XDG_DATA_DIRS": "/usr/share", "XDG_RUNTIME_DIR": "/run/user/1000",
                   "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus"}, env
    # A value saved by PyInstaller that itself points into a package is dropped too.
    assert "LD_LIBRARY_PATH" not in system_env({"LD_LIBRARY_PATH": "/tmp/_MEI2",
                                                "LD_LIBRARY_PATH_ORIG": "/tmp/.mount_S/usr/lib"})
    assert system_env({"A": "b"}) == {"A": "b"}


if __name__ == "__main__":
    selftest()
    print("selftest ok")

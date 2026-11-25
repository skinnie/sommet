#!/usr/bin/env python3
"""Linux only: the Settings switch "Keep Bluetooth privacy on", a fallback for a watch that
still forgets this computer after pairing.

Why a watch can forget: with BlueZ's default `Privacy = off` the kernel gives the watch no
identity when pairing (net/bluetooth/smp.c adds SMP_DIST_ID_KEY only with HCI_PRIVACY). The
Ambit3 then files the pairing under MAC 00:00:00:00:00:00 and refuses the computer once the
link drops. Since 2026-11-25 Sommet completes that pairing record on the watch itself, right
after pairing (tools/ble_bond_fix.py, watch-protocol/09), so nothing on the computer changes
(André: "linking a watch should never affect the system"). Seen on the Ambit3 Peak; a watch
whose firmware does not take that fix can still be paired with privacy kept on.

From 2026-10-26 to 2026-11-25 Sommet turned privacy on for each pairing with a password
prompt, which switched Bluetooth off for a second each way and so dropped a Bluetooth mouse or
keyboard. That is gone; only the switch below remains.

The switch is the system's own setting, `Privacy = device` in /etc/bluetooth/main.conf,
changed through set_always() with one password prompt (pkexec runs
sommet-bluetooth-privacy-always as root; it keeps a copy of main.conf and restarts Bluetooth).
Off by default. The price is in the how-to: a Bluetooth mouse or keyboard paired before may
need pairing again.

    ./tools/bt_privacy.py always    -> {"ok", "always"}             (read-only)
"""

import json
import pathlib
import platform
import shutil
import subprocess
import sys

# The root script, its own file so the password prompt names it.
ALWAYS_SCRIPT = pathlib.Path(__file__).resolve().parent / "sommet-bluetooth-privacy-always"
MAIN_CONF = pathlib.Path("/etc/bluetooth/main.conf")
# Where sommet-polkit-install puts a root-owned copy, with polkit's description of it: run from
# there, the password window says why Sommet asks instead of showing a path (André, 2026-11-24).
INSTALLED_DIR = pathlib.Path("/usr/local/libexec/sommet")
POLICY_FILE = pathlib.Path("/usr/share/polkit-1/actions/io.github.skinnie.sommet.policy")


def script_path(bundled):
    """The installed copy of a root script when it is the same file, owned by root and
    described to polkit; otherwise the one in Sommet's package (which installs itself when run)."""
    installed = INSTALLED_DIR / bundled.name
    try:
        st = installed.stat()
        if (POLICY_FILE.exists() and st.st_uid == 0 and not st.st_mode & 0o022
                and installed.read_bytes() == bundled.read_bytes()):
            return installed
    except OSError:
        pass
    return bundled


def always_on(conf=None):
    """True when the system keeps Bluetooth privacy on (`Privacy = device` or `on`, not
    commented out, in main.conf). Read-only, no root."""
    import re                                                # noqa: PLC0415
    try:
        text = pathlib.Path(conf or MAIN_CONF).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return re.search(r"^[ \t]*Privacy[ \t]*=[ \t]*(device|on|network/on|limited-network)\b", text,
                     re.M | re.I) is not None


def set_always(on, prompt_timeout=180):
    """Turns the system's setting on or off: one password prompt (pkexec), Bluetooth restarts.
    Returns {"ok", "always", "error"?}. Blocks while the prompt is up."""
    if platform.system() != "Linux":
        return {"ok": False, "always": False, "error": "this is a Linux setting"}
    if not shutil.which("pkexec"):
        return {"ok": False, "always": always_on(), "error": "pkexec is not installed"}
    try:
        done = subprocess.run(["pkexec", str(script_path(ALWAYS_SCRIPT)), "on" if on else "off"],
                              capture_output=True, text=True, timeout=prompt_timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "always": always_on(), "error": "the password was not typed in time"}
    except OSError as exc:
        return {"ok": False, "always": always_on(), "error": f"could not start pkexec ({exc})"}
    word = (done.stdout or "").strip().splitlines()[-1:] or [""]
    if done.returncode == 126:
        return {"ok": False, "always": always_on(), "error": "cancelled"}
    if word[0] not in ("ON", "OFF"):
        return {"ok": False, "always": always_on(),
                "error": "the Bluetooth setting could not be changed"}
    return {"ok": True, "always": word[0] == "ON"}


def main(argv=None):
    a = argv if argv is not None else sys.argv[1:]
    if a == ["always"]:
        print(json.dumps({"ok": True, "always": always_on()}))
        return 0
    print("usage: bt_privacy.py always")
    return 1


if __name__ == "__main__":
    sys.exit(main())

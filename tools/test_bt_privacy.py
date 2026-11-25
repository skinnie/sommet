#!/usr/bin/env python3
"""bt_privacy.py (the Settings switch "Keep Bluetooth privacy on") without root: a stand-in
`pkexec` on PATH runs the real root script against a scratch main.conf, so on, off, a
cancelled prompt and the polkit install are all checked. Also: the switch is off by default,
and Sommet no longer has a pairing-time privacy step (watch-protocol/09)."""

import os
import pathlib
import stat
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bt_privacy as P  # noqa: E402

# Never this computer's installed copies (turning the switch on here installs them).
P.INSTALLED_DIR = pathlib.Path("/nonexistent/sommet-test")


def fake_tools(tmp, pkexec_code=None):
    pkexec = tmp / "pkexec"
    pkexec.write_text("#!/bin/bash\n" + (f"exit {pkexec_code}\n" if pkexec_code else 'exec "$@"\n'))
    pkexec.chmod(pkexec.stat().st_mode | stat.S_IEXEC)
    os.environ["PATH"] = f"{tmp}:{os.environ['PATH']}"


def test_no_pairing_time_privacy_step_any_more():
    # André, 2026-11-25: "linking a watch should never affect the system". The pairing window
    # (pkexec + btmgmt, Bluetooth off and on twice) is gone; ble_bond_fix.py completes the
    # watch's pairing record instead.
    for gone in ("PrivacyWindow", "run_window", "check", "ROOT_SCRIPT", "notify"):
        assert not hasattr(P, gone), gone
    assert not (pathlib.Path(P.__file__).resolve().parent / "sommet-bluetooth-privacy").exists()


def test_the_switch_is_off_on_a_systems_default_file():
    with tempfile.TemporaryDirectory() as d:
        assert P.always_on(_conf(pathlib.Path(d))) is False


CONF = """[General]
#Name = BlueZ
# Default: "off"
#Privacy = off

[Policy]
AutoEnable=true
"""


def _conf(tmp, text=CONF):
    conf = tmp / "main.conf"
    conf.write_text(text)
    os.environ["SOMMET_BT_CONF"] = str(conf)       # the root script takes it only when not root
    return conf


def test_always_on_is_read_from_the_systems_file():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        conf = _conf(tmp)
        assert P.always_on(conf) is False
        conf.write_text(CONF.replace("#Privacy = off", "Privacy = device"))
        assert P.always_on(conf) is True
        conf.write_text(CONF.replace("#Privacy = off", "  privacy=on"))
        assert P.always_on(conf) is True
        conf.write_text(CONF.replace("#Privacy = off", "Privacy = off"))
        assert P.always_on(conf) is False
        assert P.always_on(tmp / "missing.conf") is False


def test_set_always_on_then_off_changes_one_line_and_keeps_a_copy():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        fake_tools(tmp)
        conf = _conf(tmp)
        saved, P.MAIN_CONF = P.MAIN_CONF, conf
        try:
            r = P.set_always(True, prompt_timeout=10)
            assert r == {"ok": True, "always": True}, r
            assert conf.read_text() == CONF.replace("#Privacy = off", "Privacy = device")
            assert (tmp / "main.conf.before-sommet").read_text() == CONF
            assert P.always_on() is True
            r = P.set_always(False, prompt_timeout=10)
            assert r == {"ok": True, "always": False}, r
            assert conf.read_text() == CONF
            assert (tmp / "main.conf.before-sommet").read_text() == CONF       # the first copy stays
        finally:
            P.MAIN_CONF = saved


def test_set_always_on_a_file_with_no_privacy_line_adds_one_under_general():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        fake_tools(tmp)
        conf = _conf(tmp, "[General]\nName = x\n\n[Policy]\nAutoEnable=true\n")
        saved, P.MAIN_CONF = P.MAIN_CONF, conf
        try:
            assert P.set_always(True, prompt_timeout=10)["ok"]
            assert conf.read_text() == "[General]\nPrivacy = device\nName = x\n\n[Policy]\nAutoEnable=true\n"
        finally:
            P.MAIN_CONF = saved


def test_a_cancelled_prompt_changes_nothing():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d)
        fake_tools(tmp, pkexec_code=126)
        conf = _conf(tmp)
        saved, P.MAIN_CONF = P.MAIN_CONF, conf
        try:
            r = P.set_always(True, prompt_timeout=10)
            assert r["ok"] is False and r["error"] == "cancelled" and r["always"] is False, r
            assert conf.read_text() == CONF
        finally:
            P.MAIN_CONF = saved

def test_installed_copies_and_polkit_description():
    import subprocess
    import xml.etree.ElementTree as ET
    with tempfile.TemporaryDirectory() as t:
        root = pathlib.Path(t)
        tools = pathlib.Path(P.__file__).resolve().parent
        old = root / "usr/local/libexec/sommet/sommet-bluetooth-privacy"     # 0.2.66's, unused now
        old.parent.mkdir(parents=True)
        old.write_text("#!/bin/bash\n")
        r = subprocess.run([str(tools / "sommet-polkit-install"), str(root)], capture_output=True, text=True)
        assert r.returncode == 0 and r.stdout == "", r
        assert not old.exists(), "the old pairing script was left installed"
        dest = root / "usr/local/libexec/sommet"
        name = "sommet-bluetooth-privacy-always"
        assert (dest / name).read_bytes() == (tools / name).read_bytes()
        assert os.access(dest / name, os.X_OK)
        policy = ET.parse(root / "usr/share/polkit-1/actions/io.github.skinnie.sommet.policy")
        paths = {a.get("id"): a.find("annotate").text for a in policy.getroot().iter("action")}
        assert paths == {"io.github.skinnie.sommet.keep-bluetooth-privacy":
                         "/usr/local/libexec/sommet/sommet-bluetooth-privacy-always"}, paths
        assert all(a.find("message").text for a in policy.getroot().iter("action"))
        # The package's copy is used: the test's copies are not root's, and before that, none.
        saved = (P.INSTALLED_DIR, P.POLICY_FILE)
        P.INSTALLED_DIR, P.POLICY_FILE = dest, root / "usr/share/polkit-1/actions/io.github.skinnie.sommet.policy"
        try:
            assert P.script_path(P.ALWAYS_SCRIPT) == P.ALWAYS_SCRIPT   # owned by this user, not root
        finally:
            P.INSTALLED_DIR, P.POLICY_FILE = saved
        assert P.script_path(P.ALWAYS_SCRIPT) == P.ALWAYS_SCRIPT       # nothing installed


def main():
    path = os.environ["PATH"]
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        fn()
        os.environ["PATH"] = path
        print(f"ok  {name}")


if __name__ == "__main__":
    main()

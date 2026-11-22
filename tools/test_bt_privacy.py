#!/usr/bin/env python3
"""bt_privacy.py without root or a real adapter: stand-in `pkexec` and `btmgmt` on PATH run
the real root script against a fake adapter state, so the on / already / cancelled / off
paths and the moment the window closes are all checked."""

import os
import pathlib
import stat
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import bt_privacy as P  # noqa: E402

P.notify = lambda: None          # no real desktop notifications from a test run


def fake_tools(tmp, pkexec_code=None, privacy_on=False):
    state = tmp / "privacy"
    state.write_text("on" if privacy_on else "off")
    calls = tmp / "calls"
    calls.write_text("")
    btmgmt = tmp / "btmgmt"
    btmgmt.write_text(f"""#!/bin/bash
echo "$*" >> {calls}
case "$1" in
  info) s="powered bondable le"; [ "$(cat {state})" = on ] && s="$s privacy"
        echo "hci0: Primary controller"; echo "	current settings: $s" ;;
  privacy) echo "$2" > {state} ;;
esac
""")
    pkexec = tmp / "pkexec"
    pkexec.write_text("#!/bin/bash\n" + (f"exit {pkexec_code}\n" if pkexec_code else 'exec "$@"\n'))
    for f in (btmgmt, pkexec):
        f.chmod(f.stat().st_mode | stat.S_IEXEC)
    os.environ["PATH"] = f"{tmp}:{os.environ['PATH']}"
    return state, calls


def test_on_then_off():
    with tempfile.TemporaryDirectory() as d:
        state, calls = fake_tools(pathlib.Path(d))
        w = P.PrivacyWindow(log=lambda m: None)
        assert w.open(prompt_timeout=10) == "on" and w.proc is not None
        assert state.read_text().strip() == "on"
        w.close()
        assert state.read_text().strip() == "off" and w.state == "off"
        assert calls.read_text().count("power off") == 2


def test_already_on_is_left_alone():
    with tempfile.TemporaryDirectory() as d:
        state, calls = fake_tools(pathlib.Path(d), privacy_on=True)
        w = P.PrivacyWindow(log=lambda m: None)
        assert w.open(prompt_timeout=10) == "already" and w.proc is None
        w.close()
        assert state.read_text().strip() == "on" and "power" not in calls.read_text()


def test_cancelled_prompt():
    with tempfile.TemporaryDirectory() as d:
        state, _ = fake_tools(pathlib.Path(d), pkexec_code=126)
        w = P.PrivacyWindow(log=lambda m: None)
        assert w.open(prompt_timeout=10) == "declined" and w.proc is None
        assert state.read_text().strip() == "off"


def test_closes_when_the_paired_link_drops():
    with tempfile.TemporaryDirectory() as d:
        state, _ = fake_tools(pathlib.Path(d))
        w = P.PrivacyWindow(log=lambda m: None)
        w.open(prompt_timeout=10)
        seq = [{"running": True},                                         # waiting for the code
               {"running": True, "handshake_done": True, "linked_device": "dev"},  # paired
               {"running": True, "handshake_done": True, "linked_device": "dev"},
               {"running": True, "handshake_done": False, "linked_device": None}]  # dropped
        it = iter(seq)
        restarted, finished = [], []
        t = P.run_window(w, lambda: next(it, {"running": True}), lambda: restarted.append(1),
                         on_done=lambda: finished.append(1), poll_s=0.01)
        t.join(10)
        assert restarted == [1] and finished == [1]
        assert state.read_text().strip() == "off"


def test_closes_when_no_watch_comes():
    with tempfile.TemporaryDirectory() as d:
        state, _ = fake_tools(pathlib.Path(d))
        w = P.PrivacyWindow(log=lambda m: None)
        w.open(prompt_timeout=10)
        began = time.monotonic()
        finished = []
        t = P.run_window(w, lambda: {"running": True}, lambda: None,
                         on_done=lambda: finished.append(1), poll_s=0.01, idle_s=0.2)
        t.join(10)
        assert finished == [1] and state.read_text().strip() == "off"
        assert time.monotonic() - began < 5


def test_not_needed_with_a_watch_already_paired(monkeypatch=None):
    real = P.suunto_paired
    P.suunto_paired = lambda: True
    try:
        needed, reason = P.check()
        assert not needed
    finally:
        P.suunto_paired = real



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
            paired, which = P.suunto_paired, P.shutil.which
            P.suunto_paired = lambda: False               # as on a computer with no watch paired
            P.shutil.which = lambda tool, *a, **k: "/usr/bin/" + tool
            try:
                needed, reason = P.check()
            finally:
                P.suunto_paired, P.shutil.which = paired, which
            assert needed is False and "always on" in reason, reason
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

def main():
    path = os.environ["PATH"]
    for name, fn in sorted((k, v) for k, v in globals().items() if k.startswith("test_")):
        fn()
        os.environ["PATH"] = path
        print(f"ok  {name}")


if __name__ == "__main__":
    main()

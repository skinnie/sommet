#!/usr/bin/env python3
"""Tests for suuntolink_guard.py (2026-09-28) on a Linux box: the Mac and Windows paths run
against faked pgrep/osascript/pkill/tasklist/taskkill, so detection, polite-then-forced quit and
the flash Watchdog are all exercised without SuuntoLink installed.   python3 tools/test_suuntolink_guard.py"""
import os, sys, types
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suuntolink_guard as g
# --- Mac: fake pgrep / osascript / pkill, fake install + launch agents
state = {"running": True, "quit_calls": [], "osascript_works": False}
def fake_run(cmd, timeout=5):
    if cmd[0] == "pgrep": return (0, "4242\n") if state["running"] else (1, "")
    if cmd[0] == "osascript":
        state["quit_calls"].append("osascript")
        if state["osascript_works"]: state["running"] = False
        return 0, ""
    if cmd[0] == "pkill": state["quit_calls"].append("pkill"); state["running"] = False; return 0, ""
    if cmd[0] == "tasklist":
        return (0, '"Suuntolink.exe","5151","Console","1","120,000 K"\n') if state["running"] else (0, 'INFO: No tasks')
    if cmd[0] == "taskkill": state["quit_calls"].append(" ".join(cmd)); state["running"] = False; return 0, ""
    return 1, ""
g._run = fake_run
g.sys = types.SimpleNamespace(platform="darwin", argv=[])
g.os.path.isdir = lambda p: p == "/Applications/Suuntolink.app"
g.glob.glob = lambda pat: ["/Users/x/Library/LaunchAgents/com.suunto.suuntolink.launcher.1.plist"] if pat.endswith("com.suunto.*.plist") and "LaunchAgents" in pat and not pat.startswith("/Library") else []
s = g.status(); print("mac status:", s)
assert s["installed"] and s["running"] and s["pids"] == [4242] and s["installPath"] == "/Applications/Suuntolink.app" and s["autoLaunch"] == ["com.suunto.suuntolink.launcher.1"]
r = g.quit_app(wait_s=0.3); print("mac quit (osascript ignored -> force):", r, state["quit_calls"])
assert r == {"wasRunning": True, "stillRunning": False} and state["quit_calls"] == ["osascript", "pkill"]
state.update(running=True, quit_calls=[], osascript_works=True)
r = g.quit_app(wait_s=0.3); assert state["quit_calls"] == ["osascript"], state["quit_calls"]; print("mac quit polite ok")
# --- Watchdog closes a relaunch and reports it
events = []
state.update(running=True, osascript_works=True)
w = g.Watchdog(on_event=events.append, interval=0.05); w.start()
import time; time.sleep(0.3); state["running"] = True; time.sleep(0.3); w.stop(); w.join(1)
print("watchdog events:", len(events), events[0]["message"]); assert len(events) >= 2 and not events[0]["stillRunning"]
# --- Windows
g.sys = types.SimpleNamespace(platform="win32", argv=[]); g.os.name = "nt"
g.os.path.isfile = lambda p: True
g._win_autostart = lambda: ["Suuntolink"]
state.update(running=True, quit_calls=[])
s = g.status(); print("win status:", s); assert s["running"] and s["pids"] == [5151] and s["autoLaunch"] == ["Suuntolink"]
r = g.quit_app(wait_s=0.2); print("win quit:", r, state["quit_calls"]); assert r["stillRunning"] is False
print("ALL OK")

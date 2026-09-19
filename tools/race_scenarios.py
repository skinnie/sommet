#!/usr/bin/env python3
"""Save & compare race scenarios. A scenario is the full set of inputs behind a plan (route, start,
speed, stops, checkpoints, overrides) plus its result summary, stored by name so the rider can
reopen it later and compare "what if I start 2h earlier / sleep less / ride 1 km/h slower".

Store: ~/.sommet/race_scenarios.json -> {"scenarios": [{name, saved_at, ui:{...}, summary:{...}}]}
- save(name, ui, summary): upsert by name.
- list(): lightweight rows (name, saved_at, summary) — the compare table; no bulky GPX.
- get(name): the full scenario (incl. ui to reopen).
- delete(name).

Stdlib only, offline. `--selftest` uses a temp store.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from typing import Any, Dict, List

STORE = os.path.expanduser("~/.sommet/race_scenarios.json")


def _load(path: str) -> Dict[str, Any]:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {"scenarios": []}


def _save_store(path: str, data: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def save(name: str, ui: Dict[str, Any], summary: Dict[str, Any], path: str = STORE) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "a name is required"}
    data = _load(path)
    rows = [s for s in data.get("scenarios", []) if s.get("name") != name]   # upsert
    rows.append({"name": name, "saved_at": datetime.now().isoformat(timespec="seconds"),
                 "ui": ui or {}, "summary": summary or {}})
    data["scenarios"] = rows
    _save_store(path, data)
    return {"ok": True, "n": len(rows)}


def listing(path: str = STORE) -> Dict[str, Any]:
    data = _load(path)
    rows = [{"name": s.get("name"), "saved_at": s.get("saved_at"), "summary": s.get("summary", {})}
            for s in data.get("scenarios", [])]
    rows.sort(key=lambda r: r.get("saved_at") or "", reverse=True)
    return {"ok": True, "scenarios": rows}


def get(name: str, path: str = STORE) -> Dict[str, Any]:
    for s in _load(path).get("scenarios", []):
        if s.get("name") == name:
            return {"ok": True, "scenario": s}
    return {"ok": False, "error": "not found"}


def delete(name: str, path: str = STORE) -> Dict[str, Any]:
    data = _load(path)
    rows = [s for s in data.get("scenarios", []) if s.get("name") != name]
    data["scenarios"] = rows
    _save_store(path, data)
    return {"ok": True, "n": len(rows)}


def _selftest():
    import tempfile
    p = os.path.join(tempfile.mkdtemp(), "sc.json")
    assert listing(p)["scenarios"] == []
    save("Baseline", {"baseSpeed": "26"}, {"elapsed_time_s": 111600, "finish": "Sun 03:30"}, p)
    save("Less sleep", {"baseSpeed": "26"}, {"elapsed_time_s": 104400, "finish": "Sun 01:30"}, p)
    save("Baseline", {"baseSpeed": "27"}, {"elapsed_time_s": 108000}, p)   # upsert, not dup
    l = listing(p)["scenarios"]
    assert len(l) == 2, l
    assert get("Baseline", p)["scenario"]["ui"]["baseSpeed"] == "27"       # latest wins
    print("scenarios:", [(s["name"], s["summary"].get("elapsed_time_s")) for s in l])
    delete("Less sleep", p)
    assert len(listing(p)["scenarios"]) == 1
    print("✓ race_scenarios selftest passed")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Save & compare race scenarios")
    parser.add_argument("input_file", nargs="?", help='JSON {mode, name?, ui?, summary?}')
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args(argv)
    if args.selftest:
        _selftest()
        return
    try:
        body = json.load(open(args.input_file)) if args.input_file else json.load(sys.stdin)
        mode = body.get("mode")
        if mode == "save":
            print(json.dumps(save(body.get("name"), body.get("ui"), body.get("summary"))))
        elif mode == "get":
            print(json.dumps(get(body.get("name"))))
        elif mode == "delete":
            print(json.dumps(delete(body.get("name"))))
        else:
            print(json.dumps(listing()))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()

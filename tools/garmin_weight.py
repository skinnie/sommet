#!/usr/bin/env python3
"""Pulls body-composition history from Garmin Connect (a Garmin Index smart scale syncs there):
weight, BMI, body fat %, muscle mass, bone mass, body water % - the fields intervals.icu's
wellness doesn't carry. Feeds the desktop Weight page's "Garmin" source (André, 2026-08-24).

Auth is Garmin Connect's OAuth via `garminconnect`/garth, which caches tokens to a token store
so the password is used ONCE. Two steps:

    # 1. Log in once (tokens cached under --tokens; add --mfa CODE if your account uses 2FA)
    ./tools/garmin_weight.py --login you@example.com 'password' [--mfa 123456] --tokens DIR

    # 2. Fetch (uses the cached tokens - no password needed again)
    ./tools/garmin_weight.py --days 365 --tokens DIR --json

Nothing is written back to Garmin - read-only pulls. The password is never stored; only the
OAuth token store is kept (treat that dir like a credential).
"""

import argparse
import datetime
import json
import os
import pathlib
import sys

DEFAULT_TOKENS = pathlib.Path.home() / "AmbitAppBackups" / "garmin_tokens"


def _client(tokens_dir):
    """A garminconnect client with the token store attached (not yet logged in)."""
    from garminconnect import Garmin
    return Garmin(), pathlib.Path(tokens_dir)


def do_login(email, password, mfa, tokens_dir):
    from garminconnect import Garmin
    tokens_dir = pathlib.Path(tokens_dir)
    tokens_dir.mkdir(parents=True, exist_ok=True)
    # prompt_mfa is called only if Garmin demands a 2FA code; feed it the one passed in (or fail
    # with a clear message rather than blocking on stdin inside a backend subprocess).
    def _mfa():
        if not mfa:
            raise RuntimeError("Garmin asked for a 2FA code - re-run with --mfa CODE")
        return mfa
    client = Garmin(email=email, password=password, prompt_mfa=_mfa)
    # login(tokenstore=DIR) does the credential login AND persists the OAuth tokens to DIR
    # (garminconnect >=0.2.x API - the client saves them itself; there is no client.garth).
    client.login(tokenstore=str(tokens_dir))
    return {"ok": True, "loggedIn": True, "tokens": str(tokens_dir)}


def _num(v, scale=1.0):
    try:
        return round(float(v) * scale, 2)
    except (TypeError, ValueError):
        return None


def do_fetch(days, tokens_dir):
    from garminconnect import Garmin
    tokens_dir = pathlib.Path(tokens_dir)
    if not tokens_dir.exists():
        return {"ok": False, "error": "not logged in - run with --login first", "needLogin": True}
    client = Garmin()
    try:
        client.login(tokenstore=str(tokens_dir))   # loads the cached OAuth tokens
    except Exception:
        return {"ok": False, "error": "not logged in - run with --login first", "needLogin": True}

    today = datetime.date.today()
    start = today - datetime.timedelta(days=days if days > 0 else 3650)
    raw = client.get_body_composition(start.isoformat(), today.isoformat())

    series = []
    for e in (raw or {}).get("dateWeightList", []):
        # Garmin weights are in grams; percentages are already %. calendarDate is YYYY-MM-DD.
        date = e.get("calendarDate")
        if not date and e.get("date"):  # date is epoch ms
            date = datetime.date.fromtimestamp(e["date"] / 1000).isoformat()
        weight_kg = _num(e.get("weight"), 0.001)
        if weight_kg is None:
            continue
        series.append({
            "date": date,
            "weightKg": weight_kg,
            "bmi": _num(e.get("bmi")),
            "bodyFatPct": _num(e.get("bodyFat")),
            "bodyWaterPct": _num(e.get("bodyWater")),
            "muscleMassKg": _num(e.get("muscleMass"), 0.001),
            "boneMassKg": _num(e.get("boneMass"), 0.001),
        })
    series.sort(key=lambda r: r["date"] or "")
    return {"ok": True, "source": "garmin", "series": series,
            "latest": series[-1] if series else None}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--login", nargs="+", metavar=("EMAIL", "PASSWORD"),
                    help="authenticate and cache tokens; EMAIL PASSWORD (optionally add --mfa)")
    ap.add_argument("--mfa", help="2FA code, if Garmin asks for one during --login")
    ap.add_argument("--days", type=int, default=365,
                    help="how far back to fetch body composition (default 365; 0 = everything)")
    ap.add_argument("--tokens", default=str(DEFAULT_TOKENS),
                    help=f"OAuth token store dir (default {DEFAULT_TOKENS})")
    ap.add_argument("--json", action="store_true", help="print one JSON line (for the backend)")
    args = ap.parse_args()

    try:
        if args.login:
            if len(args.login) < 2:
                ap.error("--login needs EMAIL PASSWORD")
            result = do_login(args.login[0], args.login[1], args.mfa, args.tokens)
        else:
            result = do_fetch(args.days, args.tokens)
    except Exception as exc:                              # report cleanly, never a raw traceback
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    if args.json:
        print(json.dumps(result))
    else:
        if result.get("ok") and "series" in result:
            print(f"Garmin body composition: {len(result['series'])} weigh-in(s)")
            for r in result["series"][-10:]:
                extra = ", ".join(f"{k}={v}" for k, v in r.items()
                                  if k not in ("date", "weightKg") and v is not None)
                print(f"  {r['date']}  {r['weightKg']} kg" + (f"  ({extra})" if extra else ""))
        else:
            print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())

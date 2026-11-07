"""Is this the same move? - Python twin of desktop/src/services/samemove.h and
android/src/services/SameMove.ts (see there for the reasons). Starts are true UTC instants.
Shared cases: android/src/services/__tests__/sameMove.cases.json. Change all three together."""


def same_move(a, b):
    """a, b: dicts with start (epoch s UTC, <0 unknown), durationS, distanceM, watch, device."""
    if a["start"] < 0 or b["start"] < 0:
        return False
    gap = abs(a["start"] - b["start"])
    shifted = False
    if gap > 180:
        h = int((gap + 1800) // 3600)
        if h < 1 or h > 14 or abs(gap - h * 3600) > 5:
            return False
        shifted = True
    if a.get("watch") and b.get("watch") and a.get("device") and b.get("device") and a["device"] != b["device"]:
        return False
    if a["distanceM"] > 100 and b["distanceM"] > 100:
        diff = abs(a["distanceM"] - b["distanceM"])
        return diff <= 50 or diff / max(a["distanceM"], b["distanceM"]) <= (0.02 if shifted else 0.05)
    if shifted and (a["distanceM"] > 100 or b["distanceM"] > 100):
        return False
    if a["durationS"] > 0 and b["durationS"] > 0:
        r = a["durationS"] / b["durationS"]
        return 0.97 <= r <= 1.03 if shifted else 0.66 <= r <= 1.5
    return not shifted

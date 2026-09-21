#!/usr/bin/env python3
"""Rider alertness over a long ride: the classic two-process sleep model (Borbely), personalised to the
rider's usual sleep. Used by race_timeline to slow the rider when sleepy and by race_sleepopt to compare
sleep plans.

  Process S - sleep pressure. Builds while awake (time constant 18.2 h), drains while asleep (4.2 h).
              A short night leaves pressure behind, so it accumulates across days (no ad-hoc "wear").
  Process C - body clock. A 24 h wave; its LOW sits ~2 h before the rider's usual wake time, so it moves
              with the rider (a 06:00 riser bottoms out ~04:00) instead of one fixed clock time for all.
  Sleepiness Z = 0.7 S - 0.3 C. Riding speed is unaffected up to the sleepiness the rider reaches at their
  own usual bedtime after a normal day, then falls linearly to -20 % at the worst case (deep night,
  long awake).

The parameters are published sleep-science values, NOT fitted to any rider's data (André, 2026-09-22:
his rides contain no night riding). They are deliberately on the cautious side. Alertness = 100 x (1 - Z):
>= 45 fine (45 is what they feel at their own bedtime), 35-44 tired, < 35 dangerous (micro-sleeps).

Stdlib only. `--selftest` is offline.
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

TAU_RISE_H = 18.2           # sleep pressure while awake (Achermann/Borbely)
TAU_FALL_H = 4.2            # ... while asleep
S_AFTER_NORMAL_SLEEP = 0.15  # pressure left after a full usual night
W_S, W_C = 0.7, 0.3
MAX_LOSS = 0.20             # speed lost at the worst sleepiness (same floor as the old fatigue model)
Z_WORST = 0.97              # deep circadian low, S ~ 0.95
DANGER_ALERTNESS = 35.0
TIRED_ALERTNESS = 45.0


def _hour(dt: datetime) -> float:
    return dt.hour + dt.minute / 60.0 + dt.second / 3600.0


class Alertness:
    def __init__(self, bed_h: float = 22.0, wake_h: float = 6.0, awake_h_at_start: Optional[float] = None,
                 start: Optional[datetime] = None):
        self.bed_h = float(bed_h) % 24.0
        self.wake_h = float(wake_h) % 24.0
        self.peak_h = (self.wake_h + 10.0) % 24.0          # alertness peak; the low is 12 h later (wake - 2 h)
        # Sleepiness the rider reaches at their own bedtime after a normal day: below this, no slowdown.
        day_h = (self.bed_h - self.wake_h) % 24.0
        s_bed = 1.0 - (1.0 - S_AFTER_NORMAL_SLEEP) * math.exp(-day_h / TAU_RISE_H)
        self.z_ref = W_S * s_bed - W_C * self._c_at_hour(self.bed_h)
        # Pressure at the start line: hours already awake (default: awake since their usual wake time).
        if awake_h_at_start is None:
            if start is not None:
                a = (_hour(start) - self.wake_h) % 24.0
                awake_h_at_start = a if a <= day_h + 3.0 else 0.0   # start inside their usual night: assume rested
            else:
                awake_h_at_start = 0.0
        self.s = 1.0 - (1.0 - S_AFTER_NORMAL_SLEEP) * math.exp(-awake_h_at_start / TAU_RISE_H)
        self.min_alertness = 100.0
        self.min_at: Optional[datetime] = None

    def _c_at_hour(self, h: float) -> float:
        return math.cos(2.0 * math.pi * ((h - self.peak_h) % 24.0) / 24.0)

    def z(self, dt: datetime) -> float:
        return W_S * self.s - W_C * self._c_at_hour(_hour(dt))

    def alertness(self, dt: datetime) -> float:
        return max(0.0, min(100.0, 100.0 * (1.0 - self.z(dt))))

    def speed_factor(self, dt: datetime) -> float:
        span = max(1e-6, Z_WORST - self.z_ref)
        loss = MAX_LOSS * max(0.0, min(1.0, (self.z(dt) - self.z_ref) / span))
        return 1.0 - loss

    def awake(self, seconds: float) -> None:
        self.s = 1.0 - (1.0 - self.s) * math.exp(-seconds / 3600.0 / TAU_RISE_H)

    def sleep(self, seconds: float) -> None:
        self.s *= math.exp(-seconds / 3600.0 / TAU_FALL_H)

    def record(self, dt: datetime) -> None:
        """Note the alertness while riding (for the plan's worst moment)."""
        a = self.alertness(dt)
        if a < self.min_alertness:
            self.min_alertness, self.min_at = a, dt


def band(alertness: float) -> str:
    return "fine" if alertness >= TIRED_ALERTNESS else ("tired" if alertness >= DANGER_ALERTNESS else "dangerous")


def _selftest():
    from datetime import timedelta
    a = Alertness(22.0, 6.0, awake_h_at_start=0.0)
    d0 = datetime(2026, 9, 19, 6, 0)
    # a normal day: no slowdown up to their own bedtime
    a.awake(16 * 3600)
    d = d0 + timedelta(hours=16)
    assert abs(a.speed_factor(d) - 1.0) < 1e-6, a.speed_factor(d)
    assert TIRED_ALERTNESS - 1 <= a.alertness(d) <= 60, a.alertness(d)          # ~55 at their bedtime
    # no sleep through the night: the low (04:00) is far worse than bedtime
    a.awake(6 * 3600)
    d4 = d0 + timedelta(hours=22)
    assert a.alertness(d4) < DANGER_ALERTNESS and a.speed_factor(d4) < 0.9, (a.alertness(d4), a.speed_factor(d4))
    # sleep helps, more sleep helps more, and a short night leaves debt behind
    b = Alertness(22.0, 6.0, awake_h_at_start=0.0); b.awake(16 * 3600); b.sleep(4.5 * 3600)
    c = Alertness(22.0, 6.0, awake_h_at_start=0.0); c.awake(16 * 3600); c.sleep(8 * 3600)
    assert c.s < b.s < 0.6 and c.s < 0.2, (b.s, c.s)
    # the body clock follows the rider: an owl (wake 10:00) is livelier at 21:00 than a lark (wake 06:00)
    lark = Alertness(22.0, 6.0, awake_h_at_start=0.0); owl = Alertness(2.0, 10.0, awake_h_at_start=0.0)
    assert owl.alertness(datetime(2026, 9, 19, 21, 0)) > lark.alertness(datetime(2026, 9, 19, 21, 0))
    print("Alertness: bedtime %.0f, 04:00 without sleep %.0f, after 4.5 h sleep S=%.2f, after 8 h S=%.2f"
          % (a.alertness(d), a.alertness(d4), b.s, c.s))
    print("\n✓ All race_alertness selftest checks passed")


if __name__ == "__main__":
    _selftest()

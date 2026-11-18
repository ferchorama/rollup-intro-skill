#!/usr/bin/env python3
"""Smoke checks for the 2026-11-18 skill update. Does not run the four QC gates
(FORMAT / AUDIO / FRAMING / SPEECH) and never turns an advisory check into a FAIL.

Run:  python scripts/test_smoke.py
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sidekit as sk
import editcheck as ec
import upscale as up


def almost(a, b, eps=1e-6):
    return abs(a - b) < eps


def test_duration_and_structure():
    beats = [
        {"who": "guest", "pieces": [{"in": 0.0, "out": 4.2}]},
        {"who": "andy", "pieces": [{"in": 10.0, "out": 12.5}, {"in": 12.5, "out": 14.8}]},  # 4.8 s
        {"who": "robbie", "pieces": [{"in": 20.0, "out": 23.5}]},  # 3.5 s — short
        {"who": "guest", "pieces": [{"in": 30.0, "out": 35.0}]},
    ]
    assert almost(sk.beat_spoken_s(beats[1]["pieces"]), 4.8)
    flags = sk.duration_flags(beats)
    assert any("beat 3" in f and "< 4.0" in f for f in flags), flags
    assert all("beat 1" not in f or "< 4.0" not in f for f in flags)
    # host cap
    long_host = [{"who": "andy", "pieces": [{"in": 0.0, "out": 7.5}]}]
    hf = sk.duration_flags(long_host)
    assert any("7.50 s > 6.0" in f for f in hf), hf

    st = sk.structure_flags(beats, "guest,andy,robbie,guest")
    assert st == [], st
    bad = sk.structure_flags(beats, ["andy", "guest"])
    assert any("must open with the guest" in f for f in bad)
    assert any("expected" in f for f in bad)
    assert sk.parse_structure("Guest, Andy, Robbie, Guest") == ["guest", "andy", "robbie", "guest"]
    print("OK duration + structure")


def test_tight_tail_b5():
    # crossover: acoustic end 848.740, "you" at 848.755 (15 ms gap)
    plan = sk.plan_tight_tail(848.740, 848.755, 848.760, 848.74, fade_ms=25, full_until=848.800)
    assert plan is not None, "15 ms gap must be treated as tight"
    assert almost(plan["full_until"], 848.800)
    assert almost(plan["out_new"], 848.800 + sk.NEXT_FADE_S + sk.TONE_AFTER_S)
    # full volume through the tail, then 30 ms fade
    pts = {round(p[0], 3): p[1] for p in plan["points"]}
    assert pts[848.800] == 1.0
    assert pts[round(848.800 + 0.030, 3)] == 0.0
    env = sk.complete_tail_env(plan, 849.42)
    assert env["tone"]["src_t"] == 849.42
    assert env["tone"]["from"] < env["tone"]["to"]
    # wide gap is not tight
    assert sk.plan_tight_tail(10.0, 10.40, 10.42, 10.0) is None
    # overlapping tail (acoustic end after the next ASR start)
    ov = sk.plan_tight_tail(848.80, 848.755, 848.80, 848.74, full_until=848.80)
    assert ov is not None
    print("OK B5 tight-tail plan")


def test_room_tone_search():
    import numpy as np
    hop = 0.01
    # 2 s of speech at -20 dB, then 0.4 s of floor at -60 dB
    e = np.concatenate([np.full(160, -20.0), np.full(40, -60.0), np.full(20, -20.0)])
    hit = sk.find_room_tone_in_env(e, 0.0, hop, 0.15, floor=-60.0)
    assert hit is not None and 1.59 <= hit <= 1.61, hit
    # no quiet stretch long enough
    e2 = np.full(50, -20.0)
    assert sk.find_room_tone_in_env(e2, 0.0, hop, 0.15, floor=-60.0) is None
    print("OK room-tone search")


def test_collapsed_repeat():
    import numpy as np
    hop = 0.01
    floor = -60.0
    # 1.0 s window starting at t=718.60; in-point 718.74
    # first burst 718.79-718.94, pause, second 719.11-719.26 (Chris Holmes A2 numbers)
    t0 = 718.60
    n = int(1.0 / hop)
    e = np.full(n, floor)
    def fill(a, b, val=-20.0):
        i0 = int(round((a - t0) / hop))
        i1 = int(round((b - t0) / hop))
        e[i0:i1] = val
    fill(718.79, 718.94)
    fill(719.11, 719.26)
    hit = sk.detect_collapsed_repeat(e, t0, hop, floor, src_in=718.74, asr_repeat=False)
    assert hit is not None, "should detect the repeated burst"
    assert 719.00 <= hit["in_new"] <= 719.10, hit
    # ASR already flagged a stutter: leave it alone
    assert sk.detect_collapsed_repeat(e, t0, hop, floor, 718.74, asr_repeat=True) is None
    # a single burst is not a repeat
    e3 = np.full(n, floor)
    i0 = int(round((718.79 - t0) / hop))
    i1 = int(round((719.20 - t0) / hop))
    e3[i0:i1] = -20
    assert sk.detect_collapsed_repeat(e3, t0, hop, floor, 718.74) is None
    print("OK collapsed-repeat detector")


def test_extend_to_min_dur():
    def w(t, e, word="ok.", spk="guest"):
        return {"t": t, "e": e, "w": word, "spk": spk}
    # 2.5 s sentence, then more words of the same speaker ending cleanly at 4.2 s
    ws = [
        w(0.0, 0.4, "Hello"), w(0.4, 0.8, "there"), w(0.8, 2.5, "friends."),
        w(2.6, 3.2, "We"), w(3.2, 4.2, "agree."),
    ]
    assert ec.extend_to_min_dur(ws, 0, 2, 4.0) == 4
    # cannot cross a long pause (would be silence padding)
    ws2 = [
        w(0.0, 0.4, "Hi"), w(0.4, 2.0, "there."),
        w(3.5, 4.5, "Later."),  # 1.5 s gap
    ]
    assert ec.extend_to_min_dur(ws2, 0, 1, 4.0) is None
    # already long enough
    assert ec.extend_to_min_dur(ws, 0, 4, 4.0) == 4
    print("OK extend_to_min_dur")


def test_qc_gates_untouched():
    """The four pass/fail entry points still exist under the same names."""
    for name in ("format_gate", "audio_gate", "framing_gate", "speech_gate"):
        assert callable(getattr(sk, name)), name
    print("OK four QC gates still present (FORMAT/AUDIO/FRAMING/SPEECH)")


def test_upscale_helpers():
    assert up.MIN_ZOOM == 1.15
    assert up.DENOISE == 0.5
    ok, why = up.available()
    print(f"OK upscale available={ok} ({why})")


def main() -> int:
    test_duration_and_structure()
    test_tight_tail_b5()
    test_room_tone_search()
    test_collapsed_repeat()
    test_extend_to_min_dur()
    test_qc_gates_untouched()
    test_upscale_helpers()
    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""sidekit - the side-layout planner, selection-time vetting and the consolidated QC of introkit.

Imported lazily by introkit.py (`_lib("sidekit")`); keep top-level imports stdlib only.

    assemble   sets.json -> project_<SET>.json + words_<SET>.json (script spelling wins over ASR)
    frame      --layout side: ONE speaker per shot, zoomed, face on a third, captions on the
               opposite black side, other people cropped out or covered by a feathered matte
    vet        selection-time checks, cached by source + in/out: stutters / fillers (ASR VAD on
               and off), acoustic end of the last word (+ --write: the out-point fix), dip grade
               of every cut vs the room floor, headroom feasibility
    qc         four decisive post-build gates in one run: FORMAT, AUDIO, FRAMING, SPEECH; one
               PASS/FAIL table with shot / time / fix hint; per-shot cache; contact sheet
"""
from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import re
import shutil
import statistics as st
import subprocess
import sys
import time
from pathlib import Path


def _ik():
    m = sys.modules.get("__main__")
    if m is not None and hasattr(m, "plan_hw"):
        return m
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import introkit
    return introkit


FPS_DEFAULT = 30
FILLERS = {"um", "uh", "uhm", "umm", "er", "erm", "ah", "eh", "hmm", "mm", "mhm"}
DIP_OK, DIP_MARG = 4.0, 8.0          # cut level above the room floor, dB (absolute <= -45 dBFS is OK too)
PAUSE_MAX = 0.35
END_OK, END_FAIL = 0.04, -0.15     # out-point vs measured acoustic end of the last word, s (calibrated on
                                   # 26 approved vs pre-fix outs, session 2026-11-02: see LESSONS_LEARNED)


def end_grade(out: float, aend: float, asr_end: float | None = None) -> str:
    """OK / LISTEN / FAIL from the measured acoustic end only. Whisper's word end is NOT used: it is
    often 50-400 ms early (the cause of the clipped last words) and sometimes late (false alarms)."""
    m = out - aend
    if m < END_FAIL:
        return "FAIL"
    return "OK" if m >= END_OK else "LISTEN"


def nz(w: str) -> str:
    return re.sub(r"[^a-z0-9%]", "", w.lower())


def toks(s: str) -> list:
    return [t for t in (nz(x) for x in re.sub(r"[-/]", " ", s).split()) if t]


def sha(*parts) -> str:
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


def src_id(path: str) -> list:
    try:
        stt = os.stat(path)
        return [os.path.abspath(path), stt.st_size, int(stt.st_mtime)]
    except OSError:
        return [path]


def _load_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def _save_json(p, v):
    Path(p).write_text(json.dumps(v, indent=1), encoding="utf-8")


def pcm(ff: str, path: str, a: float | None = None, b: float | None = None, sr: int = 16000):
    import numpy as np
    c = [ff, "-v", "error"]
    if a is not None:
        c += ["-ss", f"{max(0.0, a):.3f}"]
    c += ["-i", str(path)]
    if b is not None:
        c += ["-t", f"{max(0.01, b - max(0.0, a or 0.0)):.3f}"]
    c += ["-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"]
    return np.frombuffer(subprocess.run(c, capture_output=True).stdout, np.float32)


def env_db(x, sr=16000, ms=10):
    import numpy as np
    h = max(1, int(sr * ms / 1000))
    n = len(x) // h
    if n == 0:
        return np.zeros(0)
    return 20 * np.log10(np.sqrt(np.mean(x[:n * h].reshape(n, h).astype(np.float64) ** 2, 1)) + 1e-9)


def whisper(a, proj=None):
    ik = _ik()
    ff = ik._which("ffmpeg", getattr(a, "ffmpeg", None))
    plan = ik.plan_hw(ff, getattr(a, "device", None), proj, quiet=True, hwaccel=getattr(a, "hwaccel", None))
    dev, ct = plan["whisper"].split("/")
    return ik.load_whisper(dev, ct, plan["cpu_threads"])


def asr_words(model, x, sr=16000, vad=False):
    import numpy as np
    pad = np.zeros(sr // 2, np.float32)
    segs, _ = model.transcribe(np.concatenate([pad, x, pad]), language="en", word_timestamps=True, vad_filter=vad,
                               beam_size=5, condition_on_previous_text=False)
    out = []
    for sg in segs:
        for w in sg.words or []:
            out.append({"w": w.word.strip(), "t": w.start - 0.5, "e": w.end - 0.5, "p": float(w.probability)})
    return out


def dip_grade(level: float, floor: float) -> str:
    if level <= -45.0 or level - floor <= DIP_OK:
        return "OK"
    return "MARG" if level - floor <= DIP_MARG else "HOT"


def is_join(shots: list, k: int) -> bool:
    """Shot k flows straight into the next clip (next in == this out): a mid-sentence split, no word end here."""
    s = shots[k]
    nx = shots[k + 1] if k + 1 < len(shots) else None
    return bool(nx and nx["kind"] == "clip" and s.get("audio_out") is not None
                and abs(float(nx["src_in"]) - float(s["audio_out"])) < 0.05)


def joined_from(shots: list, k: int) -> bool:
    """Shot k continues the previous clip (its in-point is mid-sentence by design): no dip at the in."""
    return k > 0 and is_join(shots, k - 1)


# ======================================================================= assemble

def make_lines(script, starred, big, small, cream=(242, 231, 213), white=(255, 255, 255)):
    """Caption pages: Playfair runs of <= 4 words / 20 chars, starred words as Anton (max 2),
    a new page after a sentence end (>= 3 words), two lines, or 7 words."""
    lines, page, cur, pw, nl = [], 0, [], 0, 0

    def flush_p():
        nonlocal nl
        while cur:
            chunk, ln = [], 0
            while cur and len(chunk) < 4 and (ln + len(script[cur[0]]) <= 20 or not chunk):
                ln += len(script[cur[0]]) + 1
                chunk.append(cur.pop(0))
            lines.append({"kind": "playfair", "size": small, "colour": list(cream), "words": chunk, "page": page})
            nl += 1
    i = 0
    while i < len(script):
        if starred[i]:
            flush_p()
            grp = [i]
            while i + 1 < len(script) and starred[i + 1] and len(grp) < 2 and not re.search(r"[.?!,]$", script[i]):
                i += 1
                grp.append(i)
            lines.append({"kind": "anton", "size": big, "colour": list(white), "words": grp, "page": page, "gold": grp})
            nl += 1
        else:
            cur.append(i)
        pw += 1
        end_sent = bool(re.search(r"[.?!]$", script[i])) and i < len(script) - 1
        if (end_sent and pw >= 3) or (nl >= 2 and not cur and i < len(script) - 1) or \
                (pw >= 7 and i < len(script) - 1 and not starred[i + 1]):
            flush_p()
            page += 1
            pw = nl = 0
        i += 1
    flush_p()
    return lines


def align(script, aw, t0, t1):
    """Script words get ASR times; the SCRIPT spelling is kept (chyron / names beat ASR)."""
    sk, ak = [nz(w) for w in script], [nz(w["w"]) for w in aw]
    T = [None] * len(script)
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sk, ak, autojunk=False).get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                T[i1 + k] = [aw[j1 + k]["t"], aw[j1 + k]["e"]]
        elif tag == "replace":
            s, e = aw[j1]["t"], aw[j2 - 1]["e"]
            L = [max(1, len(sk[i])) for i in range(i1, i2)]
            c = s
            for k, i in enumerate(range(i1, i2)):
                d = (e - s) * L[k] / sum(L)
                T[i] = [c, c + d]
                c += d
    for i in range(len(T)):
        if T[i] is None:
            p = next((T[j][1] for j in range(i - 1, -1, -1) if T[j]), t0)
            n = next((T[j][0] for j in range(i + 1, len(T)) if T[j]), t1)
            T[i] = [p, max(p + 0.05, n)]
    return T


def cmd_assemble(a) -> None:
    ik = _ik()
    sp_path = Path(a.sets).resolve()
    SP = _load_json(sp_path)
    beats = SP["sets"][a.set_name]
    src = SP["src"]
    src_abs = src if os.path.isabs(src) else str((sp_path.parent / src).resolve())
    # 2026-11-03: default scratch = <source video folder>\intros\work (finals go to intros\ at build);
    # sets.json out_dir overrides the intros folder, --out overrides this json folder
    final_dir = None
    if SP.get("out_dir"):
        final_dir = Path(SP["out_dir"]).expanduser()
        final_dir = (final_dir if final_dir.is_absolute() else sp_path.parent / final_dir).resolve()
    out_dir = Path(a.out).resolve() if a.out else (final_dir or ik.intros_dir(src_abs)) / "work"
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = SP.get("fps", FPS_DEFAULT)
    ff = ik._which("ffmpeg", getattr(a, "ffmpeg", None))
    model = whisper(a)
    shots, words, log, idx = [], {}, [], 1
    for bi, beat in enumerate(beats):
        who = beat["who"]
        P = SP["who"].get(who, {})
        for pi, pc in enumerate(beat["pieces"]):
            t0, t1 = float(pc["in"]), float(pc["out"])
            frames = math.ceil(round((t1 - t0) * fps, 6))
            tk = pc["text"].split()
            starred = [t.startswith("*") for t in tk]
            script = [t.lstrip("*") for t in tk]
            aw = [dict(w, t=w["t"] + t0, e=w["e"] + t0) for w in asr_words(model, pcm(ff, src_abs, t0, t1))]
            aw2 = []
            for w in aw:                                      # merge "24" + "-7"
                if aw2 and (w["w"].startswith("-") or w["w"].startswith("%")):
                    aw2[-1]["e"], aw2[-1]["w"] = w["e"], aw2[-1]["w"] + w["w"]
                else:
                    aw2.append(w)
            T = align(script, aw2, t0, t1)
            rows = [{"w": w, "t": round(max(0.0, s - t0), 3), "e": round(min(t1 - t0, max(s - t0 + 0.04, e - t0)), 3)}
                    for w, (s, e) in zip(script, T)]
            lines = make_lines(script, starred, P.get("big", 180), P.get("small", 96))
            for pg in sorted({l["page"] for l in lines}):            # every caption page starts with a capital
                f = min(i for l in lines if l["page"] == pg for i in l["words"])
                rows[f]["w"] = rows[f]["w"][:1].upper() + rows[f]["w"][1:]
            words[str(idx)] = rows
            sh = {"i": idx, "kind": "clip", "beat": f"{bi + 1}{'abc'[pi] if len(beat['pieces']) > 1 else ''} {beat['label']}",
                  "who": who, "src_in": t0, "frames": frames, "audio_out": t1, "fade_out_ms": pc.get("fade_ms", 25),
                  "speaker": "guest" if who == "guest" else "host", "cy": 540,
                  "side": P.get("side", "left"), "maxw": P.get("maxw", 900), "lines": lines}
            cr = pc.get("crop") or P.get("crop")
            if cr:
                sh.update(crop_x=cr[0], crop_y=cr[1], crop_w=cr[2], crop_h=cr[3])
            if P.get("roi"):
                sh["face_roi"] = P["roi"]
            if pc.get("lock"):
                sh["frame_lock"] = True
            shots.append(sh)
            log.append(f"shot {idx} {who} {t0:.2f}-{t1:.2f} ({frames}f) ASR: " + " ".join(f"{w['w']}@{w['t']:.2f}" for w in aw2))
            idx += 1
        shots.append({"i": idx, "kind": "black", "frames": SP.get("dip_frames", 10)})
        idx += 1
    shots[-1]["frames"] = SP.get("final_black_frames", 15)    # the last black lets the last word finish clean
    shots.append({"i": idx, "kind": "end", "beat": f"{len(beats) + 1} LOGO", "frames": SP.get("end_frames", 81)})
    endstart = sum(s["frames"] for s in shots[:-1])
    base = {"fps": fps, "width": 1920, "height": 1080, "crop": {"w": 1296, "h": 729, "y": 0},
            "grade": "unsharp=5:5:0.45,eq=contrast=1.07:saturation=1.05:brightness=0.004:gamma=0.98,vignette=PI/5",
            "fonts": {"anton": "Anton-Regular.ttf", "playfair": "PlayfairDisplay-Italic.ttf", "barlow": "BarlowCondensed-Bold.ttf"},
            "sfx": {"boom_format": "s16le", "tape_format": "s16le", "sting_format": "s16le"},
            "layout": "side", "device": "auto", "hwaccel": "auto"}
    proj = dict(base, **SP.get("project", {}))
    proj["sfx"] = dict(base["sfx"], **proj.get("sfx", {}), sting_at=round(endstart / fps + 0.07, 3))
    proj.update(name=f"{SP['prefix']}_{a.set_name}", src=src_abs, cards=SP["cards"], shots=shots)
    if final_dir is not None:
        proj["out_dir"] = str(final_dir)
    for k in ("avoid", "speakers", "allow_top_touch", "frame_bounds"):
        if k in SP:
            proj[k] = SP[k]
    pp, wp = out_dir / f"project_{a.set_name}.json", out_dir / f"words_{a.set_name}.json"
    _save_json(pp, proj)
    _save_json(wp, words)
    total = sum(s["frames"] for s in shots)
    log.append(f"total {total}f = {total / fps:.3f}s  sting_at {proj['sfx']['sting_at']}  (music fade / duck: automatic at build)")
    (out_dir / f"assemble_{a.set_name}.txt").write_text("\n".join(log), encoding="utf-8")
    print("\n".join(log))
    print("wrote", pp, wp, "\nnext: introkit vet --project", pp, "--words", wp, "--write ; then frame ; build ; qc")


# ======================================================================= frame --layout side

SIDE = dict(zoom_cap=2.45, gap=48, side_m=0.12, chin=0.07, head=0.30, head_touch=0.10, push=0.04,
            face_guest=0.66, face_host=0.55, wide_face=230, third_left=0.30, third_right=0.70,
            clamp_left=(0.20, 0.38), clamp_right=(0.62, 0.80), body=1.0, matte_feather=90, matte_clear=40)


def _scan_shot(ff, src, t_in, dur, wh, sfps, plan, det):
    """All faces (x,y,w,h,score,yaw) on ~every 0.15 s of the shot (>= 6 samples)."""
    ik = _ik()
    n_tot = max(1, int(round(dur * sfps)))
    step = max(1, n_tot // max(6, int(dur / 0.15)))
    rows = []
    for i, fr in enumerate(ik._frames(ff, src, t_in, dur, wh, plan)):
        if i % step:
            continue
        _, f = det.detect(fr)
        fs = []
        for r in ([] if f is None else f):
            ed = max(1.0, abs(r[6] - r[4]))
            yaw = (r[8] - (r[4] + r[6]) / 2) / ed            # nose vs eye midpoint: + = looks to image right
            fs.append([float(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[-1]), round(float(yaw), 3)])
        rows.append({"t": round(t_in + i / sfps, 3), "faces": fs})
    return rows


def plan_side(proj, s, rows, SW, SH, o=SIDE, log=None, force_side=None):
    log = log if log is not None else []
    OW, OH = proj["width"], proj["height"]
    A = OW / OH
    k = SH / 1080.0
    AVOID = [dict(x=r[0], y=r[1], w=r[2], h=r[3]) if not isinstance(r, dict) else r
             for r in (proj.get("avoid") or []) + (s.get("avoid") or [])]
    fb = s.get("frame_bounds") or proj.get("frame_bounds")
    ybot = min([SH] + [r["y"] for r in AVOID if r["w"] >= 0.8 * SW and r["y"] > SH / 2] + ([fb[1] + fb[3]] if fb else []))
    ik = _ik()
    who = ik.shot_who(s)
    cfg = (proj.get("speakers") or {}).get(who, {})
    guest = s.get("speaker") == "guest"
    roi = s.get("face_roi") or cfg.get("roi")
    mode = cfg.get("pick", "largest")

    def even(v):
        return int(2 * round(v / 2))

    def hits(x, y, w, h):
        return [r for r in AVOID if x < r["x"] + r["w"] and r["x"] < x + w and y < r["y"] + r["h"] and r["y"] < y + h]
    me, others, yaws = [], [], []
    for r in rows:
        fs = [f for f in r["faces"] if f[3] >= 120 * k and f[4] >= 0.6]
        cand = [f for f in fs if not roi or roi[0] <= f[0] + f[2] / 2 <= roi[0] + roi[2]] or ([] if roi else fs)
        if not cand:
            continue
        pick = {"leftmost": min(cand, key=lambda f: f[0]), "rightmost": max(cand, key=lambda f: f[0])}.get(mode) or max(cand, key=lambda f: f[3])
        me.append(pick)
        yaws.append(pick[5])
        others += [f for f in fs if f is not pick and f[3] >= 0.45 * pick[3]]
    if not me:
        log.append(f"  shot {s['i']}: NO FACE found, crop unchanged")
        return None
    mcx = st.median(b[0] + b[2] / 2 for b in me)
    mh = st.median(b[3] for b in me)
    me = [b for b in me if abs(b[0] + b[2] / 2 - mcx) < 0.8 * mh and 0.75 * mh < b[3] < 1.3 * mh] or me
    nrow = max(1, sum(1 for r in rows if r["faces"]))
    others = [f for f in others if f[4] >= 0.7 and f[1] + f[3] / 2 < 0.574 * SH]
    others = [f for f in others if sum(1 for g in others if abs((g[0] + g[2] / 2) - (f[0] + f[2] / 2)) < 0.6 * f[2]) >= max(2, 0.2 * nrow)]
    hm, wm = st.median(b[3] for b in me), st.median(b[2] for b in me)
    fx0, fx1 = min(b[0] for b in me), max(b[0] + b[2] for b in me)
    fy0, fy1 = min(b[1] for b in me), max(b[1] + b[3] for b in me)
    fcx = st.median(b[0] + b[2] / 2 for b in me)
    # look-room rule: the face looks INTO the text. Explicit text_side wins; else head yaw; else
    # position (a face left of centre looks right).
    yaw = st.median(yaws) if yaws else 0.0
    explicit = cfg.get("text_side") or s.get("text_side")
    side = force_side or explicit or (("right" if yaw > 0 else "left") if abs(yaw) > 0.06
                                      else ("right" if fcx < SW / 2 else "left"))
    u = o["third_left"] if side == "right" else o["third_right"]
    lo_u, hi_u = o["clamp_left"] if side == "right" else o["clamp_right"]
    zmin = OW / SW
    zw = cfg.get("zoom") or min(o["zoom_cap"], max(zmin, (o["face_guest"] if guest else o["face_host"]) * OH / hm))
    if hm < o["wide_face"] * k:
        zw = o["zoom_cap"] - 0.05                          # wide / two-shot piece: punch in to the cap
    touch = ik.allow_top_touch(proj, s)
    head = o["head_touch"] if touch else o["head"]
    bodies = [(b[0] - o["body"] * b[2], b[0] + b[2] * (1 + o["body"])) for b in others]
    if not guest and not bodies:
        log.append(f"  note shot {s['i']}: other host not detected (fine if alone in frame)")
    SM = o["side_m"]

    def vert(ch, hd):
        cm = o["chin"] if hd > 0.27 or hd <= 0.10 else 0.055
        ylo = max(0.0, fy1 + cm * hm - ch)
        yhi = min(ybot - ch, max(0.0, fy0 - hd * hm))
        return ylo, yhi

    def place(z, hd):
        cw = even(OW / z)
        ch = even(cw / A)
        if cw > SW or ch > SH:
            return None
        ylo, yhi = vert(ch, hd)
        if ylo > yhi + 1e-6:
            return None
        lo, hi = max(0.0, fx1 + SM * wm - cw), min(SW - cw, fx0 - SM * wm)
        if lo > hi:
            return None
        xw = fcx - u * cw
        yw = max(ylo, min(yhi, fy0 - hd * hm - 0.04 * ch))
        xs = sorted({even(min(max(xw, lo), hi))} | {even(r["x"] + r["w"] + 2) for r in AVOID if lo <= r["x"] + r["w"] + 2 <= hi} |
                    {even(r["x"] - cw - 2) for r in AVOID if lo <= r["x"] - cw - 2 <= hi} | {even(lo + 1), even(hi)}, key=lambda v: abs(v - xw))
        ys = sorted({even(yw), even(ylo + 1), even(yhi)} | {even(r["y"] + r["h"] + 2) for r in AVOID if ylo <= r["y"] + r["h"] + 2 <= yhi},
                    key=lambda v: abs(v - yw))
        for x in xs:
            for y in ys:
                if ylo - 2 <= y <= yhi + 2 and not hits(x, y, cw, ch) and y + ch <= SH and x + cw <= SW and x >= 0 and y >= 0:
                    pos = (fcx - x) / cw
                    if lo_u <= pos <= hi_u:
                        return (z, cw, ch, x, y, pos)
        return None

    def matte_for(b):
        z, cw, ch, x, y, pos = b
        mx = [((b0 - x) * z, (b1 - x) * z) for b0, b1 in bodies]
        mx = [(p, q) for p, q in mx if q > 0 and p < OW]
        if not mx:
            return None, 0
        F = o["matte_feather"]
        if side == "right":
            x0 = int(min(p for p, q in mx)) - 40
            return {"x0": max(0, x0), "x1": OW, "feather": F}, x0 - F - (fx1 + SM * wm - x) * z
        x1 = int(max(q for p, q in mx)) + 40
        return {"x0": 0, "x1": min(OW, x1), "feather": F}, (fx0 - SM * wm - x) * z - (x1 + F)
    best, matte, hd, notes = None, None, head, []
    zs = [round(zw + 0.02 * q, 3) for q in range(int(max(0, o["zoom_cap"] - zw) / 0.02) + 1)]
    for z in zs:                                      # punch in until the speaker fits his third alone
        b = place(z, hd)
        if not b:
            continue
        m, clear = matte_for(b)
        if m is None:
            best, matte = b, None
            break
        if clear >= o["matte_clear"]:
            if best is None:
                best, matte = b, m
            if z > zw + 0.25:
                break
    if not best:                                      # speaker moves vertically: back the zoom off
        hd = min(head, 0.26)
        for q in range(1, 40):
            z = round(zw - 0.02 * q, 3)
            if z < max(1.48, zmin):
                break
            b = place(z, hd)
            if b:
                best = b
                matte, clear = matte_for(b)
                notes.append(f"zoom backed off to {z:.2f}x (vertical movement)")
                if matte and clear < o["matte_clear"]:
                    notes.append(f"WARN matte close to the face ({clear:.0f} px)")
                break
    if not best and not explicit and not force_side:
        other = "left" if side == "right" else "right"
        r = plan_side(proj, s, rows, SW, SH, o, log, force_side=other)
        if r is not None:
            s["framing"]["notes"].append(f"look-room side {side} impossible in this source, text {other}")
        return r
    if not best:
        log.append(f"  shot {s['i']}: NO PLACEMENT (face too close to the frame / avoid rects): crop unchanged")
        return None
    z, cw, ch, x, y, pos = best
    S = dict(x=int(x), y=int(y), w=int(cw), h=int(ch))
    E = None
    ew, eh = even(cw / (1 + o["push"])), even(ch / (1 + o["push"]))
    ex = even(x + (fcx - x) * (1 - ew / cw))
    ey = even(y + ((fy0 + fy1) / 2 - y) * (1 - eh / ch))
    ey = min(ey, even(max(y, fy0 - hd * hm)))
    if (ex <= fx0 - SM * wm and ex + ew >= fx1 + SM * wm and ey + eh >= fy1 + o["chin"] * hm and ey >= y and ex >= x
            and ex + ew <= x + cw and ey + eh <= y + ch and not hits(ex, ey, ew, eh) and z * (1 + o["push"]) <= o["zoom_cap"] + 0.05):
        E = dict(x=ex, y=ey, w=ew, h=eh)
    R = E or S
    if side == "left":
        fe = min((fx0 - SM * wm - c["x"]) * OW / c["w"] for c in (S, R))
        maxw = int(min(1000, fe - 96 - o["gap"]))
    else:
        fe = max((fx1 + SM * wm - c["x"]) * OW / c["w"] for c in (S, R))
        maxw = int(min(1000, OW - 96 - o["gap"] - fe))
    if s.get("side") and s["side"] != side:
        notes.append(f"text side {s['side']} -> {side} (look-room rule, yaw {yaw:+.2f})")
    s.update(crop_x=S["x"], crop_y=S["y"], crop_w=S["w"], crop_h=S["h"], side=side, maxw=maxw)
    for q in ("push", "punch", "matte", "push_ease"):
        s.pop(q, None)
    if E:
        s["push"] = E
    if matte:
        s["matte"] = matte
    fco = (fcx - S["x"]) / S["w"]
    s["framing"] = {"method": "side", "who": who, "zoom_start": round(OW / S["w"], 3), "zoom_end": round(OW / R["w"], 3),
                    "face_x_frac": round(fco, 3), "face_h_out": round(hm * OW / S["w"]), "text_side": side, "maxw": maxw,
                    "yaw": round(yaw, 3), "others": len(bodies), "matte": matte, "top_touch": touch, "notes": notes}
    log.append(f"shot {s['i']:>2} {s.get('beat', ''):<22} {who:<6} face {'RIGHT' if side == 'left' else 'LEFT '} third "
               f"({fco:.2f}W) zoom {OW / S['w']:.2f}x->{OW / R['w']:.2f}x crop {S['x']},{S['y']} {S['w']}x{S['h']} "
               f"text {side} maxw {maxw} face_h {hm * OW / S['w']:.0f}px others {len(bodies)}"
               + (f" MATTE {matte['x0']}-{matte['x1']}" if matte else "") + ("".join(f"\n     - {n}" for n in notes)))
    return s


def cmd_frame_side(a, pp, proj) -> None:
    ik = _ik()
    t0 = time.time()
    import cv2
    ff, ffprobe = ik._which("ffmpeg", a.ffmpeg), ik._which("ffprobe", a.ffprobe)
    plan = ik.plan_hw(ff, getattr(a, "device", None), proj, hwaccel=getattr(a, "hwaccel", None))
    src = proj["_src_abs"]
    v = next(x for x in ik.ffprobe_json(ffprobe, Path(src))["streams"] if x.get("width"))
    SW, SH = int(v["width"]), int(v["height"])
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    sfps = float(num) / float(den or 1)
    det = cv2.FaceDetectorYN.create(str(ik.YUNET_MODEL), "", (SW, SH), 0.55, 0.3, 5000)
    o = dict(SIDE)
    if getattr(a, "zoom_cap", None):
        o["zoom_cap"] = a.zoom_cap
    if getattr(a, "no_push", False):
        o["push"] = 0.0
    elif getattr(a, "push", None) is not None:
        o["push"] = a.push
    if getattr(a, "chin", None) is not None:
        o["chin"] = a.chin
    want = {int(x) for x in a.shots.split(",")} if a.shots else None
    log = [f"introkit frame --layout side  {pp.name}  src {SW}x{SH}@{sfps:.3f}  cap {o['zoom_cap']}x push {o['push']}"]
    fps = proj.get("fps", 30)
    for s in proj["shots"]:
        if s["kind"] != "clip" or (want and s["i"] not in want):
            continue
        if s.get("frame_lock"):
            log.append(f"shot {s['i']:>2}: frame_lock set (hand crop), skipped")
            continue
        rows = _scan_shot(ff, src, float(s["src_in"]), int(s["frames"]) / fps, (SW, SH), sfps, plan, det)
        plan_side(proj, s, rows, SW, SH, o, log)
    out = pp if a.write else (Path(a.out).resolve() if a.out else pp.with_name(pp.stem + ".framed.json"))
    if a.write:
        bak = pp.with_name(pp.stem + ".preframe.json")
        if not bak.exists():
            shutil.copy2(pp, bak)
    proj["layout"] = "side"
    _save_json(out, {k: v for k, v in proj.items() if not k.startswith("_")})
    log.append(f"{time.time() - t0:.1f} s -> {out}")
    print("\n".join(log))
    out.with_name(out.stem + ".frame.txt").write_text("\n".join(log) + "\n", encoding="utf-8")


# ======================================================================= vet (selection time)

def measure_end(ff, src, s, last_w, next_clip_in, model, tail=0.08, fade_ms=25, allow_shorten=False):
    """Acoustic end of the shot's last word + proposed out-point. Energy (10 ms RMS) relative to the
    room floor (8th percentile around the line): the word ends where the level stays within 6 dB of
    the floor for 40 ms; the next sound starts where it rises 12 dB over the floor for 30 ms.
    Whisper's word end is only the search start (it is often 50-400 ms early: the old out-points)."""
    import numpy as np
    a0, out0 = float(s["src_in"]), float(s["audio_out"])
    w0 = max(a0, out0 - 1.5)
    x = pcm(ff, src, w0, out0 + 1.2)
    e = env_db(x)
    big = env_db(pcm(ff, src, max(0, a0 - 1.0), out0 + 1.5))
    floor = float(np.percentile(big, 8)) if len(big) else -70.0
    T = lambda i: w0 + i * 0.01                       # noqa: E731
    I = lambda t: int(round((t - w0) / 0.01))         # noqa: E731
    asr_end, nxt_asr = None, None
    if model is not None:
        ws = [dict(w, t=w["t"] + w0, e=w["e"] + w0) for w in asr_words(model, x)]
        want = nz(last_w)
        cand = [k for k, w in enumerate(ws) if w["t"] < out0 + 0.05 and (nz(w["w"]) == want or (len(want) >= 4 and nz(w["w"])[:4] == want[:4]) or want.endswith(nz(w["w"]) or "#"))]
        if cand:
            k = cand[-1]
            asr_end = ws[k]["e"]
            nxt_asr = ws[k + 1]["t"] if k + 1 < len(ws) else None
    start = min(out0, asr_end if asr_end else out0) - 0.06
    i = max(0, I(start))
    aend = None
    while i < len(e) - 4:
        if all(e[i + q] <= floor + 6 for q in range(4)):
            aend = T(i)
            break
        i += 1
    lim = out0 + 0.6
    if nxt_asr:
        lim = min(lim, nxt_asr - 0.01)
    if next_clip_in is not None and abs(next_clip_in - out0) < 0.05:
        lim = min(lim, out0)                          # continuous join: never move it
    j = I(aend if aend else start)
    nxt_on = None
    while j < len(e) - 3:
        if all(e[j + q] > floor + 12 for q in range(3)):
            nxt_on = T(j)
            break
        j += 1
    if nxt_on:
        lim = min(lim, nxt_on - 0.02)
    if aend is None or aend > lim:
        aend = min(lim, aend or lim)
    new = min(aend + tail, lim)
    if not allow_shorten and new < out0:
        new = out0 if (nxt_on is None or out0 <= nxt_on - 0.02) else new
    new = round(new, 2)
    fade = fade_ms if new - aend >= 0.05 else max(10, min(fade_ms, 20))
    lv = lambda t: float(np.mean(e[max(0, I(t) - 1):I(t) + 2])) if 0 <= I(t) < len(e) else floor  # noqa: E731
    return {"floor": round(floor, 1), "asr_end": round(asr_end, 3) if asr_end else None, "acoustic_end": round(aend, 3),
            "next_onset": round(nxt_on, 3) if nxt_on else None, "next_word": round(nxt_asr, 3) if nxt_asr else None,
            "out_old": out0, "out_new": new, "ext_ms": int(round((new - out0) * 1000)), "fade_ms": int(fade),
            "out_level_db": round(lv(new), 1), "out_grade": dip_grade(lv(new), floor),
            "old_out_grade": dip_grade(lv(out0), floor)}


def cmd_vet(a) -> None:
    import numpy as np
    ik = _ik()
    t_start = time.time()
    pp = Path(a.project).resolve()
    proj = _load_json(pp)
    wp = Path(a.words).resolve()
    WD = _load_json(wp)
    src = proj["src"] if os.path.isabs(proj["src"]) else str((pp.parent / proj["src"]).resolve())
    ff = ik._which("ffmpeg", getattr(a, "ffmpeg", None))
    fps = proj.get("fps", 30)
    cpath = pp.parent / "vet_cache.json"
    cache = {} if a.no_cache else _load_json(cpath, {})
    model = None
    shots = proj["shots"]
    rows, changed = [], []
    det = None
    for k, s in enumerate(shots):
        if s["kind"] != "clip":
            continue
        jin = joined_from(shots, k)
        script = " ".join(w["w"] for w in WD[str(s["i"])])
        out0 = float(s.get("audio_out") or (s["src_in"] + s["frames"] / fps))
        s.setdefault("audio_out", out0)
        nxt = shots[k + 1] if k + 1 < len(shots) and shots[k + 1]["kind"] == "clip" else None
        key = sha(src_id(src), s["src_in"], out0, script, a.tail, a.fade_ms, bool(nxt and abs(float(nxt["src_in"]) - out0) < 0.05))
        r = cache.get(key)
        if r is None:
            if model is None:
                model = whisper(a, proj)
            x = pcm(ff, src, float(s["src_in"]), out0)
            hyp = {v: " ".join(w["w"] for w in asr_words(model, x, vad=v)) for v in (True, False)}
            issues = []
            for v, txt in hyp.items():
                sc, hy = toks(script), toks(txt)
                for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sc, hy, autojunk=False).get_opcodes():
                    if tag == "insert":
                        ins = hy[j1:j2]
                        if any(t in FILLERS for t in ins):
                            issues.append(f"filler '{' '.join(ins)}' (vad={'on' if v else 'off'})")
                        elif j1 > 0 and ins and ins[0] == hy[j1 - 1]:
                            issues.append(f"stutter '{hy[j1 - 1]} {' '.join(ins)}' (vad={'on' if v else 'off'})")
            # in-point dip vs room floor
            big = env_db(pcm(ff, src, max(0, float(s["src_in"]) - 1.0), out0 + 1.5))
            floor = float(np.percentile(big, 8)) if len(big) else -70.0
            ein = env_db(pcm(ff, src, float(s["src_in"]) - 0.02, float(s["src_in"]) + 0.03))
            lin = float(np.mean(ein)) if len(ein) else floor
            end = measure_end(ff, src, s, WD[str(s["i"])][-1]["w"], float(nxt["src_in"]) if nxt else None, model,
                              a.tail, a.fade_ms, a.allow_shorten)
            # headroom feasibility (5 source frames): room above the head for the margin
            import cv2
            if det is None:
                det = cv2.FaceDetectorYN.create(str(ik.YUNET_MODEL), "", (64, 64), 0.6, 0.3, 5000)
            tops = []
            for q in range(5):
                t = float(s["src_in"]) + (q + 0.5) * (out0 - float(s["src_in"])) / 5
                raw = subprocess.run([ff, "-v", "error", "-ss", f"{t:.3f}", "-i", src, "-frames:v", "1", "-f", "image2pipe",
                                      "-vcodec", "png", "-"], capture_output=True).stdout
                im = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR) if raw else None
                if im is None:
                    continue
                det.setInputSize((im.shape[1], im.shape[0]))
                _, f = det.detect(im)
                if f is not None and len(f):
                    roi = s.get("face_roi")
                    c = [b for b in f if not roi or roi[0] <= b[0] + b[2] / 2 <= roi[0] + roi[2]] or list(f)
                    b = max(c, key=lambda b: b[3])
                    tops.append(float(b[1] / b[3]))
            head = min(tops) if tops else None
            r = {"issues": issues, "asr_vad_on": hyp[True], "asr_vad_off": hyp[False], "floor": round(floor, 1),
                 "in_level_db": round(lin, 1), "in_grade": dip_grade(lin, floor), "end": end,
                 "headroom_x_face": round(head, 2) if head is not None else None,
                 "headroom": ("NO FACE" if head is None else "OK" if head >= 0.30 else
                              "TIGHT (source-limited: allow_top_touch or another span)")}
            cache[key] = r
        r = dict(r, in_grade="JOIN" if jin else r["in_grade"])
        if nxt and abs(float(nxt["src_in"]) - out0) < 0.05:
            r["end"] = dict(r["end"], out_grade="JOIN", out_new=out0, ext_ms=0)
        if r["headroom"].startswith("TIGHT") and ik.allow_top_touch(proj, s):
            r = dict(r, headroom="OK (allow_top_touch)")
        rows.append((s, r))
    _save_json(cpath, cache)
    print(f"{'shot':>4} {'beat':<22} {'verdict':7} {'stutter/filler':<28} {'in':5} {'out old>new (ms)':>18} {'end':6} {'dip':5} {'head':5}")
    bad = False
    for s, r in rows:
        e = r["end"]
        eg = "JOIN" if e["out_grade"] == "JOIN" else end_grade(e["out_old"], e["acoustic_end"], e["asr_end"])
        verdict = "FAIL" if r["issues"] or eg == "FAIL" or r["headroom"] == "NO FACE" else \
            ("WARN" if eg == "LISTEN" or "HOT" in (r["in_grade"], e["out_grade"]) or r["headroom"].startswith("TIGHT") else "OK")
        bad |= verdict == "FAIL"
        print(f"{s['i']:>4} {s.get('beat', ''):<22} {verdict:7} {('; '.join(r['issues']) or '-')[:28]:<28} {r['in_grade']:5}"
              f" {e['out_old']:.2f}>{e['out_new']:.2f} ({e['ext_ms']:+d}) {eg:6} {e['out_grade']:5} {r['headroom'][:5]}")
        if r["issues"]:
            print(f"       ASR vad on : {r['asr_vad_on']}\n       ASR vad off: {r['asr_vad_off']}\n"
                  "       fix: move the in/out into the dip around the repeat/filler, or pick another span")
        if r["in_grade"] == "HOT":
            print(f"       note: in-point {r['in_level_db']} dB vs floor {r['floor']} (sound right before the in; fine if the fade-in hides it)")
    if a.write:
        for f in (pp, wp):
            b = f.with_name(f.stem + ".pre_vet" + f.suffix)
            if not b.exists():
                shutil.copy2(f, b)
        for s, r in rows:
            e = r["end"]
            if abs(e["out_new"] - float(s["audio_out"])) < 1e-6 and s.get("fade_out_ms") == e["fade_ms"]:
                continue
            old_f = s["frames"]
            s["audio_out"], s["fade_out_ms"] = e["out_new"], e["fade_ms"]
            s["frames"] = math.ceil(round((e["out_new"] - float(s["src_in"])) * fps, 6))
            lw = WD[str(s["i"])][-1]
            lw["e"] = round(min(e["out_new"], max(e["acoustic_end"], float(s["src_in"]) + lw["e"])) - float(s["src_in"]), 3)
            changed.append(f"shot {s['i']}: out {e['out_old']:.2f} -> {e['out_new']:.2f} ({e['ext_ms']:+d} ms), frames {old_f} -> {s['frames']}, fade {e['fade_ms']} ms")
        fix_end_timeline(proj, changed)
        _save_json(pp, proj)
        _save_json(wp, WD)
        print("\n".join(changed) or "no change")
    print(f"vet {time.time() - t_start:.1f} s (cache: {cpath.name})")
    if bad and not a.write:
        sys.exit(1)


def fix_end_timeline(proj, log=None, final_black=15):
    """Final black >= 15 frames, sting right after the end card starts, music fade / duck automatic."""
    log = log if log is not None else []
    fps = proj.get("fps", 30)
    sh = proj["shots"]
    if len(sh) >= 2 and sh[-1]["kind"] == "end" and sh[-2]["kind"] == "black" and sh[-2]["frames"] < final_black:
        log.append(f"final black {sh[-2]['frames']} -> {final_black} f")
        sh[-2]["frames"] = final_black
    if sh and sh[-1]["kind"] == "end":
        sting = round(sum(x["frames"] for x in sh[:-1]) / fps + 0.07, 3)
        proj.setdefault("sfx", {})["sting_at"] = sting
        proj.pop("music_fade_out", None)
        proj.pop("duck_hold", None)
        mf, dh = _ik().end_audio_plan(proj)
        log.append(f"sting_at {sting}; music fade {mf}; duck hold {dh} (automatic at build)")
    return log


# ======================================================================= qc (post-build)

def format_gate(ffprobe, final, proj):
    p = json.loads(subprocess.run([ffprobe, "-v", "error", "-show_entries",
                                   "stream=codec_type,codec_name,width,height,r_frame_rate,pix_fmt,sample_aspect_ratio,"
                                   "display_aspect_ratio,color_range,color_space,color_primaries,color_transfer,sample_rate,"
                                   "channels:format=duration", "-of", "json", str(final)], capture_output=True, text=True).stdout or "{}")
    v = next((s for s in p.get("streams", []) if s.get("codec_type") == "video"), {})
    au = next((s for s in p.get("streams", []) if s.get("codec_type") == "audio"), {})
    W, H, fps = (proj or {}).get("width", 1920), (proj or {}).get("height", 1080), (proj or {}).get("fps", 30)
    with open(final, "rb") as f:
        head = f.read(1 << 20)
    mo, md = head.find(b"moov"), head.find(b"mdat")
    fails = []
    chk = [("codec", v.get("codec_name") == "h264", v.get("codec_name"), "deliverable is libx264"),
           ("size", (v.get("width"), v.get("height")) == (W, H), f"{v.get('width')}x{v.get('height')}", f"render {W}x{H}"),
           ("fps", v.get("r_frame_rate") == f"{fps}/1", v.get("r_frame_rate"), f"-r {fps}"),
           ("SAR", v.get("sample_aspect_ratio") in ("1:1", None), v.get("sample_aspect_ratio"), "setsar=1 + h264_metadata (rebuild)"),
           ("pix_fmt", v.get("pix_fmt") == "yuv420p", v.get("pix_fmt"), "yuv420p"),
           ("bt709", v.get("color_primaries") == "bt709" and v.get("color_transfer") == "bt709" and v.get("color_space") == "bt709",
            f"{v.get('color_primaries')}/{v.get('color_transfer')}/{v.get('color_space')}", "tag bt709 (rebuild with this kit)"),
           ("range", v.get("color_range") in ("tv", None), v.get("color_range"), "limited range"),
           ("faststart", mo >= 0 and (md < 0 or mo < md), "moov first" if mo >= 0 and (md < 0 or mo < md) else "moov at end", "-movflags +faststart"),
           ("audio", au.get("codec_name") == "aac" and str(au.get("sample_rate")) == "48000" and au.get("channels") == 2,
            f"{au.get('codec_name')} {au.get('sample_rate')} {au.get('channels')}ch", "AAC 48 kHz stereo")]
    for name, ok, got, fix in chk:
        if not ok:
            fails.append({"where": name, "detail": f"{name} = {got}", "fix": fix})
    summ = f"h264 {v.get('width')}x{v.get('height')} {v.get('r_frame_rate')} SAR {v.get('sample_aspect_ratio')} " \
           f"{v.get('color_primaries')} {'faststart' if mo >= 0 and (md < 0 or mo < md) else 'NO-faststart'}"
    return {"pass": not fails, "fails": fails, "summary": summ, "duration": float(p.get("format", {}).get("duration", 0) or 0)}


def format_check(ffprobe, final, proj):           # kept for callers of the first port
    return format_gate(ffprobe, final, proj)


def _sting_len(proj, pp):
    ik = _ik()
    q = dict(proj)
    q["sfx"] = dict(proj.get("sfx", {}))
    ik.apply_asset_defaults(q)
    path = q["sfx"].get("sting")
    if not path:
        return None
    path = path if os.path.isabs(path) else str((pp.parent / path).resolve())
    raw = ik.sniff_raw(path, q["sfx"].get("sting_format"))
    try:
        if raw:
            return os.path.getsize(path) / (44100 * 2 * 2)
        out = subprocess.run([ik._which("ffprobe", None), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                             capture_output=True, text=True).stdout
        return float(out.strip())
    except (OSError, ValueError):
        return None


def audio_gate(ff, final, proj, pp):
    ik = _ik()
    m = ik._ebur(ff, Path(final))
    fails = []
    if abs(m["I"] + 14.0) > 0.5:
        fails.append({"where": "loudness", "detail": f"{m['I']:.1f} LUFS (want -14 +-0.5)", "fix": "rebuild (AAC remaster converges it)"})
    if m["TP"] > -1.0:
        fails.append({"where": "true peak", "detail": f"TP {m['TP']:.1f} dBTP > -1", "fix": "rebuild (remaster knee)"})
    det = f"{m['I']:.1f} LUFS, TP {m['TP']:.1f}"
    if proj:
        tl = ik.timeline(proj)
        total = tl[-1][2]
        sting = (proj.get("sfx") or {}).get("sting_at")
        mf, dh = ik.end_audio_plan(proj)
        if sting is not None:
            need = min(_sting_len(proj, pp) or 2.45, 2.45)
            det += f"; sting tail {total - sting:.2f}s (need {need:.2f})"
            if total - sting < need - 0.01:
                fails.append({"where": f"sting @{sting:.2f}s", "detail": f"tail {total - sting:.2f}s < {need:.2f}s: truncated",
                              "fix": "introkit vet --write (final black 15 f, sting after the end card starts)"})
            if mf and mf[1] > sting + 0.001:
                fails.append({"where": f"music fade {mf[0]:.2f}-{mf[1]:.2f}s", "detail": "music still playing under the sting",
                              "fix": "drop music_fade_out from the project (automatic) or end it before sting_at"})
            clips = [(s, a) for s, a, b in tl if s["kind"] == "clip"]
            if clips and mf:
                s, a = clips[-1]
                se = a + (float(s.get("audio_out") or (s["src_in"] + s["frames"] / proj.get("fps", 30))) - float(s["src_in"]))
                det += f"; last word ends {se:.2f}s, music fade {mf[0]:.2f}-{mf[1]:.2f}s"
                if mf[0] < se - 0.02:
                    fails.append({"where": f"shot {s['i']} @{se:.2f}s", "detail": "music fade starts before the last word ends",
                                  "fix": "drop music_fade_out (automatic: starts at the last word's end)"})
    return {"pass": not fails, "fails": fails, "summary": det, "loudness": m}


def _caption_box(proj, shot, words, fonts):
    """Exact caption bounding box per page (same metrics as the overlay layout), output px."""
    from PIL import ImageFont
    W = proj["width"]
    wl = words[str(shot["i"])]
    side, cap = shot.get("side", "left"), shot.get("maxw", 900)
    cache = {}

    def font(kind, size):
        k = (kind, int(round(size)))
        if k not in cache:
            cache[k] = ImageFont.truetype(fonts[kind], int(round(size)))
        return cache[k]
    boxes = {}
    for pg in sorted({l.get("page", 0) for l in shot.get("lines", [])}):
        PL = [l for l in shot["lines"] if l.get("page", 0) == pg]
        tot, wmax = 0, 0
        for ln in PL:
            size = ln["size"]
            ws = [(wl[i]["w"] if ln["kind"] == "playfair" else wl[i]["w"].upper()) for i in ln["words"]]
            tw = sum(font(ln["kind"], size).getbbox(t)[2] - font(ln["kind"], size).getbbox(t)[0] for t in ws) + size * 0.27 * (len(ws) - 1)
            if tw > cap:
                size *= cap / tw
                tw = sum(font(ln["kind"], size).getbbox(t)[2] - font(ln["kind"], size).getbbox(t)[0] for t in ws) + size * 0.27 * (len(ws) - 1)
            asc, desc = font(ln["kind"], size).getmetrics()
            tot += asc + desc + 28
            wmax = max(wmax, tw)
        tot -= 28
        y0 = shot.get("cy", 520) - tot / 2
        x0 = 96 if side == "left" else W - 96 - wmax
        boxes[pg] = [x0, y0, x0 + wmax, y0 + tot]
    return boxes


def _decode_small(ff, final, wh):
    import numpy as np
    W, H = wh
    p = subprocess.Popen([ff, "-v", "error", "-nostdin", "-i", str(final), "-an", "-vf", f"scale={W}:{H}:flags=area",
                          "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, bufsize=W * H * 3 * 4)
    while True:
        b = p.stdout.read(W * H * 3)
        if len(b) < W * H * 3:
            break
        yield np.frombuffer(b, np.uint8).reshape(H, W, 3)
    p.wait()


def framing_gate(ff, final, proj, words, cache, use_cache, sheet_path):
    """ONE YuNet pass on the final at half size, every 3rd clip frame + each shot's first/last frame:
    head (25 % hair) >= 0 unless allow_top_touch, chin >= 5 % h, sides >= 5 % w on a 5-sample median
    track, exactly one face (duplicate hits inside the speaker's head ignored), face clear of the
    caption box, captions outside the 60 px safe strips. Also builds the contact sheet."""
    import cv2
    import numpy as np
    ik = _ik()
    W, H = proj["width"], proj["height"]
    sw, sh = W // 2, H // 2
    det = cv2.FaceDetectorYN.create(str(ik.YUNET_MODEL), "", (sw, sh), 0.6, 0.3, 5000)
    low = cv2.FaceDetectorYN.create(str(ik.YUNET_MODEL), "", (sw, sh), 0.4, 0.3, 5000)
    try:
        fonts = ik._fonts(proj)
    except SystemExit:
        fonts = None
    tl = ik.timeline(proj)
    fps = proj.get("fps", 30)
    plan, todo, res = {}, set(), {}
    for s, a, b in tl:
        if s["kind"] != "clip":
            continue
        f0, n = int(round(a * fps)), int(s["frames"])
        key = sha("frame", src_id(proj.get("_src_abs", proj["src"])), {k: s.get(k) for k in (
            "src_in", "frames", "crop_x", "crop_y", "crop_w", "crop_h", "push", "push_ease", "matte", "side", "maxw", "cy", "lines")},
            words.get(str(s["i"])) if words else None, proj.get("grade"), proj.get("crop"), W, H, f0)
        s["_qkey"] = key
        if use_cache and key in cache:
            res[s["i"]] = cache[key]
        else:
            todo.add(s["i"])
        ks = sorted(set(range(0, n, 3)) | {0, n - 1})
        plan[s["i"]] = (s, f0, n, ks, {f0 + k for k in ks}, f0 + n // 2)
    need = {}
    for i in todo:
        for fr in plan[i][4]:
            need[fr] = i
    mids = {plan[i][5]: i for i in plan}
    thumbs, samples = {}, {i: [] for i in todo}
    for idx, fr in enumerate(_decode_small(ff, final, (sw, sh))):
        if idx in mids:
            thumbs[mids[idx]] = fr.copy()
        i = need.get(idx)
        if i is None:
            continue
        det.setInputSize((sw, sh))
        _, f = det.detect(fr)
        F = [] if f is None else [list(map(float, r[:4])) for r in f if r[3] >= 30]
        if not F:
            low.setInputSize((sw, sh))
            _, f = low.detect(fr)
            F = [] if f is None else [list(map(float, r[:4])) for r in f if r[3] >= 30][:1]
        F.sort(key=lambda r: -r[2] * r[3])
        if len(F) > 1:
            m = F[0]
            F = [m] + [q for q in F[1:] if not (m[0] - 0.35 * m[2] <= q[0] + q[2] / 2 <= m[0] + 1.35 * m[2]
                                                and m[1] - 0.8 * m[3] <= q[1] + q[3] / 2 <= m[1] + m[3] and q[2] * q[3] < 0.5 * m[2] * m[3])]
        samples[i].append((idx, F))
    for i in todo:
        s, f0, n, ks, _, _ = plan[i]
        top_ok = ik.allow_top_touch(proj, s)
        fails, track = [], []
        for idx, F in samples[i]:
            if len(F) != 1:
                fails.append((idx, "no face" if not F else f"{len(F)} faces", "no face: pick another span / check framing" if not F
                              else "other person visible: tighter crop or matte (frame --layout side)"))
            if F:
                track.append((idx, [v * 2 for v in F[0]]))
        mt = ik._median_track([b for _, b in track])
        caps = {}
        if fonts and words and s.get("lines"):
            try:
                caps = _caption_box(proj, s, words, fonts)
            except Exception:
                caps = {}
        worst = {"top": 1e9, "chin": 1e9, "side": 1e9, "cap_gap": 1e9}
        for (idx, _), (X, Y, Bw, Bh) in zip(track, mt):
            top, chin, left, right = Y - 0.25 * Bh, H - (Y + Bh), X, W - (X + Bw)
            worst["top"] = min(worst["top"], top)
            worst["chin"] = min(worst["chin"], chin - 0.05 * Bh)
            worst["side"] = min(worst["side"], min(left, right) - 0.05 * Bw)
            t = idx / fps
            if top < 0 and not top_ok:
                fails.append((idx, f"head top {top:.0f}px", "raise headroom: lower zoom / move crop up, or allow_top_touch if source-limited"))
            if chin < 0.05 * Bh:
                fails.append((idx, f"chin {chin:.0f}<{0.05 * Bh:.0f}px", "move crop_y down a few px or reduce the end zoom"))
            if min(left, right) < 0.05 * Bw:
                fails.append((idx, f"side {min(left, right):.0f}px", "shift crop_x / reduce zoom"))
            for pg, (x0, y0, x1, y1) in caps.items():
                gap = (X - x1) if s.get("side", "left") == "left" else (x0 - (X + Bw))
                worst["cap_gap"] = min(worst["cap_gap"], gap)
                if gap < 0:
                    fails.append((idx, f"caption overlaps face by {-gap:.0f}px", "reduce maxw (frame --layout side recomputes it)"))
        for pg, (x0, y0, x1, y1) in caps.items():
            if y0 < 60 or y1 > H - 60:
                fails.append((f0, f"caption page {pg} in the safe strip (y {y0:.0f}-{y1:.0f})", "fewer/smaller lines on that page or move cy"))
        r = {"beat": s.get("beat", ""), "sampled": len(samples[i]), "fails": [[a_, d, h] for a_, d, h in fails[:12]],
             "n_fail": len(fails), "worst": {k: (round(v, 1) if v < 1e8 else None) for k, v in worst.items()}, "top_touch": top_ok}
        res[i] = r
        cache[s["_qkey"]] = r
    # contact sheet: mid frame of every clip shot, face box + caption box, verdict
    if sheet_path:
        tiles = []
        for i in sorted(plan):
            fr = thumbs.get(i)
            if fr is None:
                continue
            im = fr.copy()
            s = plan[i][0]
            if fonts and words and s.get("lines"):
                try:
                    for x0, y0, x1, y1 in _caption_box(proj, s, words, fonts).values():
                        cv2.rectangle(im, (int(x0 / 2), int(y0 / 2)), (int(x1 / 2), int(y1 / 2)), (0, 200, 255), 1)
                except Exception:
                    pass
            det.setInputSize((sw, sh))
            _, f = det.detect(im)
            for b in ([] if f is None else f):
                cv2.rectangle(im, (int(b[0]), int(b[1])), (int(b[0] + b[2]), int(b[1] + b[3])), (0, 255, 0), 2)
            ok = res[i]["n_fail"] == 0
            cv2.rectangle(im, (0, 0), (sw, 34), (0, 0, 0), -1)
            cv2.putText(im, f"#{i} {s.get('beat', '')}  {'PASS' if ok else 'FAIL'}", (8, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                        (80, 255, 80) if ok else (60, 60, 255), 2)
            tiles.append(cv2.resize(im, (480, 270)))
        if tiles:
            cols = 3
            rows_ = (len(tiles) + cols - 1) // cols
            sheet = np.full((rows_ * 274 + 4, cols * 484 + 4, 3), 20, np.uint8)
            for q, t in enumerate(tiles):
                y, x = 4 + (q // cols) * 274, 4 + (q % cols) * 484
                sheet[y:y + 270, x:x + 480] = t
            cv2.imwrite(str(sheet_path), sheet, [cv2.IMWRITE_JPEG_QUALITY, 88])
    fails = []
    for i, r in sorted(res.items()):
        for a_, d, h in r["fails"][:3]:
            fails.append({"where": f"shot {i} @{a_ / fps:.2f}s (f{a_})", "detail": d, "fix": h})
    return {"pass": not fails, "fails": fails, "shots": res, "checked": len(todo), "cached": len(plan) - len(todo),
            "summary": f"{len(plan)} shots ({len(todo)} checked, {len(plan) - len(todo)} cached), "
                       f"{sum(r.get('sampled', 0) for r in res.values())} frames sampled"}


def speech_gate(a, ff, final, proj, words, cache, use_cache, wave_path):
    """ONE per-shot ASR pass (large-v3-turbo, VAD off, word times) on the delivered mix vs the
    script: a filler or stutter is FAIL; the last word must be heard (p >= 0.6) unless the cut is
    in a dip of the SOURCE (word-edge energy vs room floor): then it is a WARN (ASR mishear, e.g.
    'chain' -> 'Shane'); a hot edge is FAIL (word clipped). Pauses from the words json:
    internal gap / head > 0.35 s FAIL, tail > 0.35 s WARN (a complete word wins)."""
    import numpy as np
    ik = _ik()
    tl = ik.timeline(proj)
    shots = proj["shots"]
    fps = proj.get("fps", 30)
    src = proj.get("_src_abs", proj["src"])
    mf, dh = ik.end_audio_plan(proj)
    mix_ctx = sha(proj.get("music"), proj.get("sfx"), mf, dh, proj.get("speech_fade_ms"))
    x = None
    model = None
    res, fails, warns = {}, [], []
    for k, (s, t0, t1) in enumerate(tl):
        if s["kind"] != "clip":
            continue
        wl = words.get(str(s["i"]), [])
        out = float(s.get("audio_out") or (s["src_in"] + s["frames"] / fps))
        join = is_join(shots, k)
        key = sha("speech-v3", src_id(src), s["src_in"], out, s["frames"], s.get("fade_out_ms"), [w["w"] for w in wl], t0, mix_ctx)
        r = cache.get(key) if use_cache else None
        if r is None:
            if x is None:
                x = pcm(ff, str(final))
            if model is None:
                model = whisper(a, proj)
            seg = x[int(t0 * 16000):int(t1 * 16000)]
            hw = asr_words(model, seg)
            sc, hy = toks(" ".join(w["w"] for w in wl)), [nz(w["w"]) for w in hw]
            hy_ok = [h for h in hy if h]
            issues, diffs = [], []
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, sc, hy_ok, autojunk=False).get_opcodes():
                if tag == "equal":
                    continue
                ins = hy_ok[j1:j2]
                if tag == "insert" and any(t in FILLERS for t in ins):
                    issues.append(f"filler '{' '.join(ins)}'")
                elif tag == "insert" and j1 > 0 and ins and ins[0] == hy_ok[j1 - 1]:
                    issues.append(f"stutter '{hy_ok[j1 - 1]} {' '.join(ins)}'")
                else:
                    diffs.append(f"{tag}: '{' '.join(sc[i1:i2])}' -> '{' '.join(ins)}'")
            want = nz(wl[-1]["w"]) if wl else ""
            got = next((w for w in reversed(hw) if nz(w["w"])), None)
            g = nz(got["w"]) if got else ""
            heard = bool(got) and (g == want or (len(want) >= 4 and g[:4] == want[:4]) or (g and want.endswith(g)))
            # word edge on the SOURCE: out-point vs the measured acoustic end of the last word
            nxt = shots[k + 1] if k + 1 < len(shots) and shots[k + 1]["kind"] == "clip" else None
            me = measure_end(ff, src, dict(s, audio_out=out), wl[-1]["w"] if wl else "", float(nxt["src_in"]) if nxt else None, model)
            ei = env_db(pcm(ff, src, float(s["src_in"]) - 0.03, float(s["src_in"])))
            li = float(np.mean(ei)) if len(ei) else me["floor"]
            r = {"beat": s.get("beat", ""), "heard": " ".join(w["w"] for w in hw), "issues": issues, "diffs": diffs[:6],
                 "last_want": wl[-1]["w"] if wl else "", "last_got": got["w"] if got else "", "last_p": round(got["p"], 2) if got else 0,
                 "last_heard": heard, "acoustic_end": me["acoustic_end"], "asr_end": me["asr_end"],
                 "end_margin_ms": int(round((out - me["acoustic_end"]) * 1000)), "end_grade": end_grade(out, me["acoustic_end"], me["asr_end"]),
                 "suggest_out": me["out_new"], "floor": me["floor"], "in_db": round(li, 1), "in_grade": dip_grade(li, me["floor"]), "join": join}
            cache[key] = r
        # pauses (cheap, never cached)
        du = out - float(s["src_in"])
        gaps = [wl[q + 1]["t"] - wl[q]["e"] for q in range(len(wl) - 1)]
        r["pause"] = {"head": round(wl[0]["t"], 2) if wl else 0, "tail": round(du - wl[-1]["e"], 2) if wl else 0,
                      "maxgap": round(max(gaps), 2) if gaps else 0.0}
        res[s["i"]] = r
        W_ = f"shot {s['i']} @{t0:.2f}-{t1:.2f}s"
        for it in r["issues"]:
            fails.append({"where": W_, "detail": it, "fix": "move the in/out into the dip around it or pick another span (vet)"})
        if not r["join"]:
            hear = f"ASR on the mix: '{r['last_got']}' p={r['last_p']}"
            if r["end_grade"] == "FAIL":
                fails.append({"where": f"shot {s['i']} @{t1:.2f}s", "detail": f"last word '{r['last_want']}' clipped: out {r['end_margin_ms']:+d} ms vs acoustic end",
                              "fix": f"introkit vet --write (suggested out {r['suggest_out']:.2f})"})
            elif r["end_grade"] == "LISTEN":
                warns.append({"where": f"shot {s['i']} @{t1:.2f}s", "detail": f"LISTEN '{r['last_want']}': out {r['end_margin_ms']:+d} ms vs acoustic end; {hear}",
                              "fix": "listen to this one cut (waveform png); loud rooms read late ends. vet --write if clipped"})
        p = r["pause"]
        if p["maxgap"] > PAUSE_MAX or p["head"] > PAUSE_MAX:
            fails.append({"where": W_, "detail": f"pause head {p['head']} / internal {p['maxgap']} s > {PAUSE_MAX}",
                          "fix": "cut inside the silence (split the piece) or trim the in-point"})
        elif p["tail"] > PAUSE_MAX:
            warns.append({"where": W_, "detail": f"tail {p['tail']} s > {PAUSE_MAX}", "fix": "accepted when it completes the word"})
    if wave_path:
        _waveform(x if x is not None else pcm(ff, str(final)), tl, proj, wave_path)
    return {"pass": not fails, "fails": fails, "warns": warns, "shots": res,
            "summary": f"{len(res)} shots, ASR {'run' if model else 'all cached'}; word ends "
                       f"{sum(1 for r in res.values() if not r['join'] and r['end_grade'] == 'OK')}/{sum(1 for r in res.values() if not r['join'])} "
                       f"clear, {sum(1 for r in res.values() if not r['join'] and r['end_grade'] == 'LISTEN')} to listen"}


def _waveform(x, tl, proj, path):
    import cv2
    import numpy as np
    W_, H_ = 2400, 300
    img = np.full((H_, W_, 3), 24, np.uint8)
    n = max(1, len(x))
    for i, c in enumerate(np.array_split(np.abs(x), W_)):
        if not len(c):
            continue
        a, r = float(c.max()), float(np.sqrt(np.mean(c ** 2)))
        cv2.line(img, (i, int(H_ / 2 - a * H_ / 2)), (i, int(H_ / 2 + a * H_ / 2)), (120, 120, 120), 1)
        cv2.line(img, (i, int(H_ / 2 - r * H_ / 2)), (i, int(H_ / 2 + r * H_ / 2)), (230, 200, 120), 1)
    px = lambda t: int(t * 16000 / n * W_)    # noqa: E731
    for s, a, b in tl:
        if s["kind"] == "clip":
            se = a + float(s.get("audio_out") or (s["src_in"] + s["frames"] / 30)) - float(s["src_in"])
            cv2.line(img, (px(se), 0), (px(se), H_), (0, 200, 255), 1)
            cv2.line(img, (px(b), 0), (px(b), H_), (0, 0, 255), 1)
    st_ = (proj.get("sfx") or {}).get("sting_at")
    if st_ is not None:
        cv2.line(img, (px(st_), 0), (px(st_), H_), (0, 255, 0), 2)
    cv2.putText(img, "orange=speech end  red=cut  green=sting", (10, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1)
    cv2.imwrite(str(path), img)


def cmd_qc(a) -> None:
    ik = _ik()
    t_start = time.time()
    ff, ffprobe = ik._which("ffmpeg", a.ffmpeg), ik._which("ffprobe", a.ffprobe)
    final = Path(a.final).resolve()
    proj = words = pp = None
    if a.project:
        pp, proj = ik._load_project(a.project)
        wpath = a.words
        if not wpath:
            m = re.match(r"project_([^.]+)", pp.stem)
            cand = pp.parent / f"words_{m.group(1)}.json" if m else None
            wpath = str(cand) if cand and cand.is_file() else None
        words = _load_json(wpath) if wpath else None
    base = Path(a.out).with_suffix("") if a.out else final.with_suffix("")
    sheet = Path(a.sheet) if a.sheet else Path(str(base) + "_contact.jpg")
    wave = Path(str(base) + "_wave.png")
    cpath = (pp.parent if pp else final.parent) / "qc_cache.json"
    cache = _load_json(cpath, {}) if not a.no_cache else {}
    use = not a.no_cache
    G, T = {}, {}
    t = time.time(); G["format"] = format_gate(ffprobe, final, proj); T["format"] = time.time() - t
    t = time.time(); G["audio"] = audio_gate(ff, final, proj, pp); T["audio"] = time.time() - t
    if proj:
        t = time.time(); G["framing"] = framing_gate(ff, final, proj, words, cache, use, sheet); T["framing"] = time.time() - t
        if words:
            t = time.time(); G["speech"] = speech_gate(a, ff, final, proj, words, cache, use, wave); T["speech"] = time.time() - t
        else:
            G["speech"] = {"pass": False, "fails": [{"where": "-", "detail": "no words json", "fix": "pass --words"}], "summary": "skipped"}
    if proj:
        _save_json(cpath, cache)
    ok = all(g["pass"] for g in G.values())
    name = proj.get("name") if proj else final.stem
    print(f"\nQC {name}   {'PASS' if ok else 'FAIL'}   ({time.time() - t_start:.1f} s)")
    print(f"{'GATE':9} {'RESULT':6} {'WHERE':30} {'DETAIL':58} FIX")
    for g, r in G.items():
        print(f"{g:9} {'PASS' if r['pass'] else 'FAIL':6} {'-':30} {r['summary'][:58]:58} ({T.get(g, 0):.1f} s)")
        for f in r["fails"]:
            print(f"{'':9} {'FAIL':6} {f['where'][:30]:30} {f['detail'][:58]:58} {f['fix']}")
        for f in r.get("warns", []):
            print(f"{'':9} {'WARN':6} {f['where'][:30]:30} {f['detail'][:58]:58} {f['fix']}")
    if proj:
        print(f"contact sheet: {sheet}" + (f"\nwaveform: {wave}" if words else ""))
    rep = {"name": name, "pass": ok, "seconds": round(time.time() - t_start, 1), "gate_seconds": {k: round(v, 1) for k, v in T.items()},
           "gates": G, "contact_sheet": str(sheet) if proj else None}
    if getattr(a, "classic_faces", False) and proj:
        o = dict(ik.FACE)
        plan = ik.plan_hw(ff, getattr(a, "device", None), None, quiet=True, hwaccel=getattr(a, "hwaccel", None))
        rep["classic_faces"] = ik.qc_faces(final, a.project, ff, ffprobe, plan, o)
        print("[classic faces diagnostic]", "PASS" if rep["classic_faces"]["pass_faces"] else "FAIL")
    if a.out:
        _save_json(a.out, rep)
    if not ok:
        sys.exit(1)

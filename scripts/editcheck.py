"""editcheck - editorial checks for introkit: openers, cut boundaries, the stranger-test script,
the read-only hook shortlist (`signals`) and the right-speaker WARN in `qc`.

Imported lazily by introkit.py / sidekit.py; top-level imports are stdlib only. Everything here is
WARN-level advice: nothing in this module moves an in/out point or fails a gate.

    opener_flags(tokens)          weak first word, bare pronoun, back-reference      (vet WARN)
    boundary_after(...)           what follows the out-word: speaker change / sentence end /
                                  pause / breath / mid-clause, plus a conjunction check   (vet WARN)
    stranger_script(proj, words)  the plain script, one line per beat, for the stranger test
    cmd_signals(a)                ranked guest-line shortlist from transcript words + source audio
    speaker_gate(...)             mouth motion vs speech per shot: ok / ambiguous / WARN  (qc)
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

# ----------------------------------------------------------------------------- word lists
# Our own lists (2026-11-12). Tokens are compared after nz(): lower case, letters/digits/% only,
# so "That's" -> "thats", "it," -> "it".

WEAK_OPENERS = {"yeah", "yes", "yep", "yup", "so", "and", "but", "or", "because", "cause", "also", "plus",
                "well", "okay", "ok", "anyway", "um", "uh", "uhm", "umm", "er", "erm", "ah", "eh", "hmm", "mm"}
BARE_PRONOUNS = {"he", "she", "they", "them", "it", "that", "this", "these", "those", "his", "her", "their",
                 "hes", "shes", "theyre", "theyve", "theyll", "thats", "him"}
BACK_REFERENCES = ["as i said", "like i said", "as i mentioned", "like i mentioned", "as i was saying",
                   "as we said", "as we discussed", "like we discussed", "as we talked about", "like we talked about",
                   "as you said", "like you said", "as you mentioned", "like you mentioned", "you mentioned",
                   "to your point", "going back to", "back to your", "i said earlier", "we talked about earlier",
                   "you said earlier", "same thing", "the other thing"]
CONJ_END = {"and", "but", "so", "because", "cause", "or", "nor", "then", "which", "who", "whom", "whose",
            "if", "when", "while", "although", "though", "unless", "until", "since", "whereas", "like"}
FUNCTION_END = {"the", "a", "an", "of", "to", "for", "in", "on", "at", "from", "by", "with", "into", "about",
                "is", "are", "was", "were", "be", "my", "our", "your", "their", "its", "that", "than", "as"}
FILLER_TOKENS = {"um", "uh", "uhm", "umm", "er", "erm", "ah", "eh", "hmm", "mm", "mhm"}
POWER_WORDS = {"never", "always", "nobody", "everybody", "everyone", "every", "entire", "entirely", "only",
               "actually", "instead", "however", "but", "not", "no", "far", "biggest", "largest", "bigger",
               "best", "worst", "first", "fastest", "cheaper", "cheapest", "inevitable", "impossible",
               "enormous", "massive", "huge", "incredible", "insane", "crazy", "revolution", "transform",
               "transformed", "disrupt", "disruptive", "unprecedented", "future", "forever", "dead", "wrong",
               "mistake", "problem", "truth", "secret", "surpass", "replace", "end", "moment", "now", "fraction",
               "trillion", "trillions", "billion", "billions", "inconceivably", "tiny", "fundamentally"}
CONTRAST = {"but", "however", "instead", "actually", "not", "never", "nobody", "rather", "yet", "wrong", "myth"}
PREDICTION = {"will", "gonna", "going", "future", "next", "eventually", "soon", "inevitable", "surpass", "become"}
STORY = {"when", "remember", "story", "once", "day", "back", "years", "ago", "first"}
NUM_WORDS = {"hundred", "thousand", "million", "millions", "billion", "billions", "trillion", "trillions",
             "percent", "%", "cents", "cent", "dollars", "dollar", "half", "double", "triple", "x"}


def nz(w: str) -> str:
    return re.sub(r"[^a-z0-9%]", "", str(w).lower())


def _toks(words) -> list:
    out = []
    for w in words:
        for t in re.sub(r"[-/]", " ", str(w).replace("*", "")).split():
            if nz(t):
                out.append(nz(t))
    return out


def _sha(*parts) -> str:
    import hashlib
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:16]


# ----------------------------------------------------------------------------- 2. openers

def opener_flags(words) -> list:
    """WARN reasons for how a line STARTS (words = script words of the beat's first piece)."""
    tk = _toks(words)
    if not tk:
        return []
    fl = []
    if tk[0] in WEAK_OPENERS:
        fl.append(f"starts on '{tk[0]}' (weak opener: start at the next clause)")
    elif tk[0] in BARE_PRONOUNS:
        fl.append(f"starts on bare pronoun '{tk[0]}': is the referent in the line? (stranger test)")
    line = " " + " ".join(tk) + " "
    for ph in BACK_REFERENCES:
        if f" {ph} " in line:
            fl.append(f"back-reference '{ph}' (points at something the viewer never saw)")
            break
    return fl


def end_word_flags(last_word: str) -> list:
    t = nz(last_word)
    if t in CONJ_END:
        return [f"ends on conjunction '{t}'"]
    if t in FUNCTION_END and not re.search(r"[.?!]$", str(last_word).strip()):
        return [f"ends on function word '{t}'"]
    return []


# ----------------------------------------------------------------------------- 1. boundary after the out-word

def _speaker_at(tr_words, t):
    best = None
    for w in tr_words:
        if w["start"] - 0.05 <= t <= w["end"] + 0.05:
            return w.get("speaker")
        if w["end"] < t:
            best = w.get("speaker")
    return best


def boundary_after(sk, ff, src, model, out: float, last_word: str, tr_words=None) -> dict:
    """Score the gap after a line's out-word. One ASR pass on [out-2.0, out+1.6] (word text +
    punctuation of the words around the cut) and the 10 ms energy envelope (pause length).
    Kinds and scores (0-1, higher = a cleaner place to stop): speaker change 0.9, sentence end
    0.75-0.95 (longer pause = higher), pause >= 0.6 s without punctuation 0.55, breath 0.2-0.6 s
    0.4, mid-clause < 0.2 s 0.1. It never changes the out-point (that stays the acoustic end)."""
    import numpy as np
    w0 = max(0.0, out - 2.0)
    x = sk.pcm(ff, src, w0, out + 1.6)
    e = sk.env_db(x)
    big = sk.env_db(sk.pcm(ff, src, max(0.0, out - 4.0), out + 3.0))
    floor = float(np.percentile(big, 8)) if len(big) else -70.0
    I = lambda t: int(round((t - w0) / 0.01))         # noqa: E731
    io = min(max(0, I(out)), max(0, len(e) - 1))
    s_end = None
    for i in range(io, max(0, io - 120), -1):          # last speech frame at/before the out
        if e[i] > floor + 6:
            s_end = w0 + i * 0.01
            break
    onset = None
    for j in range(io, len(e) - 3):
        if all(e[j + q] > floor + 12 for q in range(3)):
            onset = w0 + j * 0.01
            break
    gap = (onset - (s_end if s_end is not None else out)) if onset is not None else 1.6
    gap = max(0.0, gap)
    asr_last = asr_next = None
    if model is not None:
        ws = [dict(w, t=w["t"] + w0, e=w["e"] + w0) for w in sk.asr_words(model, x)]
        before = [w for w in ws if w["t"] < out - 0.03]
        after = [w for w in ws if w["t"] >= out - 0.03]
        asr_last = before[-1]["w"] if before else None
        asr_next = after[0]["w"] if after else None
    punct = bool(re.search(r"[.?!][\"')\]]*$", str(last_word).strip())) or \
        bool(asr_last and re.search(r"[.?!][\"')\]]*$", asr_last.strip()))
    spk = None
    if tr_words:
        a_, b_ = _speaker_at(tr_words, out - 0.15), _speaker_at(tr_words, (onset or out + 0.3) + 0.1)
        spk = bool(a_ and b_ and a_ != b_)
    if spk:
        kind, score = "speaker change", 0.9
    elif punct:
        kind, score = "sentence end", round(0.75 + min(0.2, 0.2 * gap), 2)
    elif gap >= 0.6:
        kind, score = "pause", 0.55
    elif gap >= 0.2:
        kind, score = "breath", 0.4
    else:
        kind, score = "mid-clause", 0.1
    flags = end_word_flags(last_word)
    if kind in ("pause", "breath", "mid-clause"):
        nxt = f" (next: '{asr_next}')" if asr_next else ""
        flags.append(("ends mid-sentence" if kind != "mid-clause" else "ends mid-clause") + nxt)
    return {"kind": kind, "score": score, "gap_s": round(gap, 2), "punct": punct, "speaker_change": spk,
            "asr_last": asr_last, "asr_next": asr_next, "flags": flags}


# ----------------------------------------------------------------------------- 3. stranger test

def beat_key(s: dict):
    m = re.match(r"\s*(\d+)([a-z]?)", str(s.get("beat", "")))
    return (int(m.group(1)), m.group(2)) if m else (s.get("i"), "")


def stranger_script(proj: dict, WD: dict) -> list:
    """[(beat label, who, text)] with the pieces of one beat joined."""
    out, last = [], None
    for s in proj["shots"]:
        if s.get("kind") != "clip":
            continue
        n, _ = beat_key(s)
        txt = " ".join(str(w["w"]).replace("*", "") for w in WD.get(str(s["i"]), []))
        if out and last == n:
            out[-1][2] += " " + txt
        else:
            lab = re.sub(r"^\s*\d+[a-z]?\s*", "", str(s.get("beat", ""))) or f"shot {s['i']}"
            out.append([lab, s.get("who", "?"), txt])
        last = n
    return out


def print_stranger(proj, WD):
    rows = stranger_script(proj, WD)
    print("\nSCRIPT (stranger test: read it once as someone who never saw the episode; see "
          "references/line-selection.md)")
    for k, (lab, who, txt) in enumerate(rows, 1):
        print(f"  {k}. {who:<6} {txt}")
    print("  check: unresolved he/it/that or 'as I said'; a beat that starts or ends mid-thought; the weakest "
          "beat; does the closer land; does each host line make sense on its own")


# ----------------------------------------------------------------------------- 4. signals

def _flat_words(tr: dict) -> tuple[list, bool]:
    """[{t,e,w,spk}] from this skill's transcript JSON (word times) or an SRT (cue text spread
    evenly over the cue: approximate)."""
    ws, exact = [], True
    for c in tr.get("cues", []):
        if c.get("words"):
            for w in c["words"]:
                ws.append({"t": float(w["start"]), "e": float(w["end"]), "w": str(w.get("text", w.get("word", ""))).strip(),
                           "spk": w.get("speaker") or c.get("speaker")})
        else:
            exact = False
            tk = str(c.get("text", "")).split()
            if not tk:
                continue
            d = (float(c["end"]) - float(c["start"])) / len(tk)
            for k, t in enumerate(tk):
                ws.append({"t": float(c["start"]) + k * d, "e": float(c["start"]) + (k + 1) * d, "w": t, "spk": c.get("speaker")})
    ws.sort(key=lambda w: w["t"])
    return [w for w in ws if w["w"]], exact


def _sentences(ws: list, max_s: float) -> list:
    """Split the word stream at terminal punctuation, speaker changes and pauses >= 0.8 s; a run
    with no punctuation longer than max_s is chopped at its longest internal gap."""
    out, cur = [], []
    for k, w in enumerate(ws):
        cur.append(w)
        nx = ws[k + 1] if k + 1 < len(ws) else None
        tok = w["w"].strip()
        end = bool(re.search(r"[.?!][\"')\]]*$", tok)) and not re.fullmatch(r"(?:[A-Za-z]\.){2,}", tok)
        if nx is None or end or (nx["spk"] != w["spk"] and (nx["spk"] or w["spk"])) or nx["t"] - w["e"] >= 0.8:
            out.append(cur)
            cur = []
    res = []
    stack = list(reversed(out))
    while stack:
        s = stack.pop()
        if s[-1]["e"] - s[0]["t"] <= max_s or len(s) < 6:
            res.append(s)
            continue
        # chop at a real gap, preferring one in front of a clause opener ("and", "so", "but"...),
        # nearest the middle; with no usable gap, at the middle
        mid = len(s) / 2
        gaps = [((s[k + 1]["t"] - s[k]["e"]) + (0.25 if nz(s[k + 1]["w"]) in WEAK_OPENERS else 0)
                 - 0.02 * abs(k - mid), k) for k in range(2, len(s) - 3)]
        k = max(gaps)[1] if gaps else len(s) // 2
        stack.append(s[k + 1:])
        stack.append(s[:k + 1])
    return res


def _env50(ff, src, sr=16000):
    """Whole-file 50 ms RMS envelope (dB), streamed from ffmpeg (no full decode in memory)."""
    import numpy as np
    hop = sr // 20
    p = subprocess.Popen([ff, "-v", "error", "-nostdin", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "f32le", "-"], stdout=subprocess.PIPE, bufsize=hop * 4 * 200)
    out = []
    while True:
        b = p.stdout.read(hop * 4 * 200)
        if not b:
            break
        x = np.frombuffer(b[:len(b) // 4 * 4], np.float32)
        n = len(x) // hop
        if n:
            out.append(20 * np.log10(np.sqrt(np.mean(x[:n * hop].reshape(n, hop).astype(np.float64) ** 2, 1)) + 1e-9))
    p.wait()
    return np.concatenate(out) if out else np.zeros(0)


def _hook_type(tk: list, text: str) -> str:
    s = set(tk)
    if text.rstrip().endswith("?"):
        return "question"
    if any(t.isdigit() or re.match(r"\d", t) for t in tk) or s & {"percent", "%", "trillion", "billion", "million", "cents"}:
        return "number/stat"
    if s & PREDICTION:
        return "prediction"
    if s & CONTRAST:
        return "contrarian"
    if s & STORY and ("i" in s or "we" in s):
        return "story"
    return "bold claim"


def _ends_clean(w) -> bool:
    return bool(re.search(r"[.?!][\"')\]]*$", w["w"].strip()))


def _trim_span(ws: list, prev_w, next_w):
    """Drop up to two weak opener tokens at the front and conjunction / function words at the end
    (a shortlist line should start and stop on content). Returns (words, start_clean, end_clean)."""
    k = 0
    while k < 2 and k < len(ws) - 3 and nz(ws[k]["w"]) in WEAK_OPENERS:
        k += 1
    j = len(ws)
    while j - k > 3 and nz(ws[j - 1]["w"]) in (CONJ_END | FUNCTION_END) and not _ends_clean(ws[j - 1]):
        j -= 1
    p, n = prev_w, (ws[j] if j < len(ws) else next_w)
    # the start is judged where the sentence starts (dropping a leading "so" keeps it clean); stopping
    # right before a conjunction counts as a clause boundary
    start_clean = p is None or _ends_clean(p) or ws[0]["t"] - p["e"] >= 0.35 or (p.get("spk") != ws[0].get("spk"))
    end_clean = (n is None or _ends_clean(ws[j - 1]) or n["t"] - ws[j - 1]["e"] >= 0.35
                 or n.get("spk") != ws[j - 1].get("spk") or nz(n["w"]) in CONJ_END)
    return ws[k:j], start_clean, end_clean



def extend_to_min_dur(ws, i0, i1, min_dur):
    """Grow a candidate [i0, i1] (inclusive) until its duration is >= min_dur, stopping only
    on a natural word boundary (terminal punctuation, pause >= 0.35 s, or speaker change).
    Never pads with silence. Returns the new end index, or None if it cannot reach min_dur."""
    if not ws or i1 < i0:
        return None
    if ws[i1]["e"] - ws[i0]["t"] >= min_dur - 1e-9:
        return i1
    spk = ws[i0].get("spk")
    last_ok = None
    for j in range(i1 + 1, len(ws)):
        if spk and ws[j].get("spk") and ws[j]["spk"] != spk:
            break
        if ws[j]["t"] - ws[j - 1]["e"] > 0.8:  # a long gap would be silence-padding; stop
            break
        reached = ws[j]["e"] - ws[i0]["t"] >= min_dur - 1e-9
        nxt = ws[j + 1] if j + 1 < len(ws) else None
        end_ok = _ends_clean(ws[j]) or nxt is None or nxt["t"] - ws[j]["e"] >= 0.35 or (
            nxt.get("spk") != ws[j].get("spk")
        )
        if reached and end_ok:
            return j
        if reached:
            last_ok = j
    return last_ok


def _score_span(ws, env, ref, prev_e, lens, start_clean=True, end_clean=True):
    import numpy as np
    t0, t1 = ws[0]["t"], ws[-1]["e"]
    dur = t1 - t0
    text = " ".join(w["w"] for w in ws)
    tk = _toks([w["w"] for w in ws])
    a, b = int(t0 * 20), max(int(t0 * 20) + 1, int(t1 * 20))
    seg = env[a:b] if b <= len(env) else env[a:]
    if len(seg) == 0:
        return None
    sp = seg[seg > ref["floor"] + 10]
    rms = 10 * np.log10(np.mean(10 ** (sp / 10))) if len(sp) else ref["floor"]
    e_rel = rms - ref["speech_db"]
    peak_frac = float(np.mean(seg > ref["p90"]))
    energy = 0.6 * float(np.clip((e_rel + 1.0) / 5.0, 0, 1)) + 0.4 * float(np.clip(peak_frac / 0.25, 0, 1))
    if prev_e is not None:
        gap = max(0.0, t0 - prev_e)
    else:
        pre = env[max(0, a - 40):a]
        q = 0
        for v in pre[::-1]:
            if v > ref["floor"] + 6:
                break
            q += 1
        gap = q * 0.05
    pause = float(np.clip(gap / 1.0, 0, 1))
    question = 1.0 if text.rstrip().endswith("?") else 0.0
    hits = sorted(set(t for t in tk if t in POWER_WORDS))
    power = float(np.clip(len(hits) / 3.0, 0, 1))
    num = 1.0 if (any(re.match(r"\d", t) for t in tk) or set(tk) & (NUM_WORDS - {"x", "half"})) else 0.0
    lo, pk0, pk1, hi = lens
    length = 1.0 if pk0 <= dur <= pk1 else max(0.0, (dur - lo) / (pk0 - lo)) if dur < pk0 else max(0.0, (hi - dur) / (hi - pk1))
    flags = opener_flags([w["w"] for w in ws]) + end_word_flags(ws[-1]["w"])
    fil = sum(1 for t in tk if t in FILLER_TOKENS)
    if fil:
        flags.append(f"{fil} filler(s) inside")
    if not start_clean:
        flags.append("starts mid-sentence")
    if not end_clean:
        flags.append("ends mid-sentence")
    pen = (12 if opener_flags([w["w"] for w in ws]) else 0) + (8 if end_word_flags(ws[-1]["w"]) else 0) + min(20, 10 * fil) \
        + (10 if not start_clean else 0) + (10 if not end_clean else 0)
    score = 100 * (0.28 * energy + 0.12 * pause + 0.08 * question + 0.22 * power + 0.12 * num + 0.18 * length) - pen
    return {"start": round(t0, 2), "end": round(t1, 2), "dur": round(dur, 2), "score": round(float(np.clip(score, 0, 100)), 1),
            "features": {"energy": round(energy, 2), "energy_rel_db": round(float(e_rel), 1), "pause_before_s": round(gap, 2),
                         "pause": round(pause, 2), "question": question, "power": round(power, 2), "power_words": hits,
                         "number": num, "length": round(length, 2)},
            "type_hint": _hook_type(tk, text), "flags": flags, "text": text}


def cmd_signals(a) -> None:
    """Read-only hook shortlist. Ranks guest lines by audio energy, the pause before the line,
    questions, contrast/power words, numbers and length; keeps one speaker's lines (transcript
    speaker labels, or --enroll voiceprints when the transcript has none). Writes JSON + a table."""
    import numpy as np
    sk = __import__("sidekit")
    ik = sk._ik()
    tl = __import__("transcribe")
    t_start = time.time()
    tp = Path(a.transcript).expanduser().resolve()
    tr = tl.read_transcript(tp)
    src = a.src or tr.get("meta", {}).get("source")
    if not src or not Path(src).exists():
        sys.exit("[signals] pass --src (the transcript does not name an existing source)")
    ff = ik._which("ffmpeg", getattr(a, "ffmpeg", None))
    ws, exact = _flat_words(tr)
    if a.start is not None or a.end is not None:
        ws = [w for w in ws if (a.start is None or w["t"] >= a.start) and (a.end is None or w["e"] <= a.end)]
    if not ws:
        sys.exit("[signals] no words in the transcript")
    want = {s.strip() for s in (a.speaker or "guest").split(",") if s.strip()}
    labelled = any(w["spk"] for w in ws)
    sents = _sentences(ws, a.max_dur)
    env = _env50(ff, src)
    floor = float(np.percentile(env, 10)) if len(env) else -70.0
    spv = env[env > floor + 10]
    ref = {"floor": floor, "speech_db": float(np.median(spv)) if len(spv) else floor + 20,
           "p90": float(np.percentile(spv, 90)) if len(spv) else floor + 25}
    lens = (a.min_dur, 4.5, 8.0, a.max_dur + 1.0)
    cands = []
    pos = {id(w): k for k, w in enumerate(ws)}
    for i in range(len(sents)):
        span = []
        for j in range(i, min(i + 3, len(sents))):
            span = span + sents[j]
            k0, k1 = pos[id(span[0])], pos[id(span[-1])]
            tw, sc_, ec_ = _trim_span(span, ws[k0 - 1] if k0 else None, ws[k1 + 1] if k1 + 1 < len(ws) else None)
            dur = tw[-1]["e"] - tw[0]["t"]
            if dur > a.max_dur:
                break
            if dur < a.min_dur:
                # Extend to a natural word boundary to reach the 4 s minimum; never pad silence.
                ks, ke = pos[id(tw[0])], pos[id(tw[-1])]
                knew = extend_to_min_dur(ws, ks, ke, a.min_dur)
                if knew is None:
                    continue
                tw = ws[ks:knew + 1]
                dur = tw[-1]["e"] - tw[0]["t"]
                if dur < a.min_dur or dur > a.max_dur:
                    continue
                nxt = ws[knew + 1] if knew + 1 < len(ws) else None
                ec_ = _ends_clean(tw[-1]) or nxt is None or nxt["t"] - tw[-1]["e"] >= 0.35 or (
                    nxt.get("spk") != tw[-1].get("spk")
                )
            spk = None
            if labelled:
                lab = [w["spk"] for w in tw if w["spk"]]
                top = max(set(lab), key=lab.count) if lab else None
                if not top or lab.count(top) < 0.9 * len(lab) or top not in want:
                    continue
                spk = top
            kk = pos[id(tw[0])]
            prev = ws[kk - 1]["e"] if kk > 0 and exact else None
            r = _score_span(tw, env, ref, prev, lens, sc_, ec_)
            if r:
                r["speaker"] = spk
                cands.append(r)
    cands.sort(key=lambda r: -r["score"])
    picked = []
    def free(r):
        return all(r["end"] <= p["start"] + 0.3 or r["start"] >= p["end"] - 0.3 for p in picked)
    note = None
    if labelled:
        for r in cands:
            if len(picked) >= a.top:
                break
            if free(r):
                picked.append(r)
    elif a.enroll:
        plan = ik.plan_hw(ff, getattr(a, "device", None), None, quiet=True, hwaccel=getattr(a, "hwaccel", None))
        spm, emb = ik._embedder(plan)
        refs = ik._parse_enroll(a.enroll, ff, str(src), emb, spm)
        if not want & set(refs):
            sys.exit(f"[signals] --speaker {sorted(want)} is not among the --enroll names {sorted(refs)}")
        for r in cands:
            if len(picked) >= a.top:
                break
            if not free(r):
                continue
            x = sk.pcm(ff, src, r["start"], r["end"])
            v = emb(x)
            v = v / (np.linalg.norm(v) + 1e-9)
            cos = {n: float(v @ c) for n, c in refs.items()}
            best = max(cos, key=cos.get)
            r["speaker"], r["voice_cos"] = best, round(cos[best], 3)
            if best in want:
                picked.append(r)
        note = "speakers from --enroll voiceprints"
    else:
        note = "NO speaker info (transcript unlabelled, no --enroll): lines from every speaker are listed"
        for r in cands:
            if len(picked) >= a.top:
                break
            if free(r):
                picked.append(r)
    if not exact:
        note = (note + "; " if note else "") + "SRT input: word times spread evenly over each cue (approximate in/outs)"
    out = Path(a.out) if a.out else tp.with_name(tp.stem + ".signals.json")
    rep = {"transcript": str(tp), "src": str(src), "speaker": sorted(want), "note": note, "reference": ref,
           "weights": {"energy": 0.28, "pause": 0.12, "question": 0.08, "power": 0.22, "number": 0.12, "length": 0.18},
           "candidates": picked, "seconds": round(time.time() - t_start, 1)}
    out.write_text(json.dumps(rep, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"[signals] {len(ws)} words, {len(sents)} sentences, {len(cands)} spans scored; top {len(picked)}"
          + (f"  ({note})" if note else ""))
    print(f"{'#':>2} {'in':>8} {'out':>8} {'dur':>4} {'score':>5}  E    P    Q W    N L    {'type':<12} text / flags")
    for k, r in enumerate(picked, 1):
        f = r["features"]
        print(f"{k:>2} {r['start']:8.2f} {r['end']:8.2f} {r['dur']:4.1f} {r['score']:5.1f}  {f['energy']:.2f} {f['pause']:.2f} "
              f"{int(f['question'])} {f['power']:.2f} {int(f['number'])} {f['length']:.2f} {r['type_hint']:<12} {r['text'][:110]}")
        if r["flags"]:
            print(f"{'':>45}flags: {'; '.join(r['flags'])}")
    print(f"wrote {out}  ({rep['seconds']} s). Shortlist only: in/outs come from the audio (vet), the choice from "
          "judgement (references/line-selection.md scoring).")


# ----------------------------------------------------------------------------- 6. right speaker on screen

# Thresholds (2026-11-12, Kash A2 smoke test: correct speakers measured r -0.04..0.51 on one face, so a
# single face only WARNs on clearly negative evidence; anything in between is "ambiguous").
MOUTH = {"r_ok": 0.20, "r_other_margin": 0.20, "r_other_min": 0.30, "r_bad": -0.15, "quiet_ratio_bad": 0.7,
         "min_frames": 30, "presence": 0.4}


def _patch(cv2, np, g, cx, cy, w, h, size):
    H, W = g.shape
    x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
    x1, y1 = x0 + max(4, int(round(w))), y0 + max(4, int(round(h)))
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
        return None
    return cv2.resize(g[y0:y1, x0:x1], size, interpolation=cv2.INTER_AREA).astype(np.float32)


def _corr(a, b, lags=3):
    import numpy as np
    best = None
    for L in range(-lags, lags + 1):
        x, y = (a[L:], b[:len(b) - L]) if L >= 0 else (a[:L], b[-L:])
        m = ~(np.isnan(x) | np.isnan(y))
        if m.sum() < 20 or np.std(x[m]) < 1e-6 or np.std(y[m]) < 1e-6:
            continue
        r = float(np.corrcoef(x[m], y[m])[0, 1])
        best = r if best is None or r > best else best
    return best


def _mouth_shot(ik, sk, ff, src, s, src_wh, fps):
    import cv2
    import numpy as np
    t0 = float(s["src_in"])
    t1 = float(s.get("audio_out") or (t0 + s["frames"] / fps))
    SW, SH = src_wh
    w = 960
    h = int(round(SH * w / SW / 2)) * 2
    sc = w / SW
    p = subprocess.Popen([ff, "-v", "error", "-nostdin", "-ss", f"{t0:.3f}", "-i", str(src), "-t", f"{t1 - t0:.3f}", "-an",
                          "-vf", f"fps={fps},scale={w}:{h}:flags=area", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                         stdout=subprocess.PIPE, bufsize=w * h * 3 * 4)
    det = cv2.FaceDetectorYN.create(str(ik.YUNET_MODEL), "", (w, h), 0.6, 0.3, 5000)
    tracks = []      # {"box":[x,y,w,h], "m":{f:patch}, "u":{f:patch}, "c":{f:(cx,cy)}}
    n = 0
    while True:
        b = p.stdout.read(w * h * 3)
        if len(b) < w * h * 3:
            break
        fr = np.frombuffer(b, np.uint8).reshape(h, w, 3)
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        _, F = det.detect(fr)
        for r in ([] if F is None else F):
            bx = [float(v) for v in r[:4]]
            if bx[3] < 24:
                continue
            cx, cy = bx[0] + bx[2] / 2, bx[1] + bx[3] / 2
            tr = None
            for t in tracks:
                tb = t["box"]
                if abs(cx - (tb[0] + tb[2] / 2)) < 0.5 * tb[2] and abs(cy - (tb[1] + tb[3] / 2)) < 0.5 * tb[3]:
                    tr = t
                    break
            if tr is None:
                tr = {"box": bx, "m": {}, "u": {}, "c": {}}
                tracks.append(tr)
            tr["box"] = bx
            mx, my = (r[10] + r[12]) / 2, (r[11] + r[13]) / 2
            mw = max(1.5 * float(np.hypot(r[12] - r[10], r[13] - r[11])), 0.35 * bx[2])
            mp = _patch(cv2, np, g, mx, my + 0.15 * mw, mw, 0.6 * mw, (32, 18))
            ex, ey = (r[4] + r[6]) / 2, (r[5] + r[7]) / 2
            up = _patch(cv2, np, g, ex, ey, 0.8 * bx[2], 0.25 * bx[2], (32, 10))
            if mp is not None and up is not None:
                tr["m"][n], tr["u"][n], tr["c"][n] = mp, up, (cx, cy)
        n += 1
    p.wait()
    if n < MOUTH["min_frames"]:
        return {"verdict": "ambiguous", "why": f"only {n} frames"}
    e = sk.env_db(sk.pcm(ff, src, t0, t1))
    hop = 100.0 / fps
    sp = np.array([np.mean(e[int(k * hop):max(int(k * hop) + 1, int((k + 1) * hop))]) if int(k * hop) < len(e) else np.nan
                   for k in range(n)])
    flo = float(np.nanpercentile(sp, 10))
    speech = np.clip((sp - flo) / 20.0, 0, 1)
    cr = s.get("crop_x"), s.get("crop_y"), s.get("crop_w"), s.get("crop_h")
    if None in cr:
        cr = (0, 0, SW, SH)
    cx0, cy0, cx1, cy1 = cr[0] * sc, cr[1] * sc, (cr[0] + cr[2]) * sc, (cr[1] + cr[3]) * sc
    stats = []
    for t in tracks:
        fs = sorted(t["m"])
        if len(fs) < max(MOUTH["min_frames"], MOUTH["presence"] * n):
            continue
        mm = np.full(n, np.nan)
        for a_, b_ in zip(fs, fs[1:]):
            if b_ == a_ + 1:
                dm = float(np.mean(np.abs(t["m"][b_] - t["m"][a_])))
                du = float(np.mean(np.abs(t["u"][b_] - t["u"][a_])))
                mm[b_] = max(0.0, dm - 0.8 * du)
        k3 = np.ones(3) / 3
        sm = np.convolve(np.nan_to_num(mm, nan=np.nanmedian(mm) if np.isfinite(np.nanmedian(mm)) else 0), k3, "same")
        sm[np.isnan(mm)] = np.nan
        r = _corr(sm, np.convolve(speech, k3, "same"))
        loud, quiet = speech > 0.5, speech < 0.15
        ratio = None
        if np.sum(quiet & ~np.isnan(sm)) >= 10 and np.sum(loud & ~np.isnan(sm)) >= 10:
            ratio = float(np.nanmean(sm[loud]) / (np.nanmean(sm[quiet]) + 1e-6))
        inside = np.mean([cx0 <= c[0] <= cx1 and cy0 <= c[1] <= cy1 for c in t["c"].values()])
        stats.append({"inside": float(inside), "frames": len(fs), "r": None if r is None else round(r, 3),
                      "speech_quiet_ratio": None if ratio is None else round(ratio, 2)})
    return {"frames": n, "faces": stats}


def mouth_verdict(res: dict) -> dict:
    """ok / ambiguous / WARN from the cached per-face stats (thresholds in MOUTH)."""
    if "faces" not in res:
        return dict(res, verdict=res.get("verdict", "ambiguous"), why=res.get("why", "no data"))
    stats = res["faces"]
    vis = max((q for q in stats if q["inside"] >= 0.5), key=lambda q: (q["inside"], q["frames"]), default=None)
    others = [q for q in stats if q is not vis and q["inside"] < 0.5 and q["r"] is not None]
    if vis is None or vis["r"] is None:
        return dict(res, verdict="ambiguous", why="no steady face track inside the crop")
    rv = vis["r"]
    ro = max((q["r"] for q in others), default=None)
    if ro is not None and ro >= MOUTH["r_other_min"] and ro - rv >= MOUTH["r_other_margin"]:
        return dict(res, verdict="WARN", why=f"possible wrong speaker on screen: an off-crop face moves with the speech "
                                             f"(visible r={rv:.2f}, other r={ro:.2f})")
    if rv >= MOUTH["r_ok"]:
        return dict(res, verdict="ok", why=f"visible mouth follows the speech (r={rv:.2f})")
    qr = vis["speech_quiet_ratio"]
    if rv < MOUTH["r_bad"] and qr is not None and qr < MOUTH["quiet_ratio_bad"]:
        return dict(res, verdict="WARN", why=f"possible wrong speaker on screen: visible mouth not moving with the speech "
                                             f"(r={rv:.2f}, speech/quiet motion {qr:.2f})")
    return dict(res, verdict="ambiguous", why=f"weak evidence (visible r={rv:.2f}"
                + (f", other r={ro:.2f}" if ro is not None else "") + ")")


def speaker_gate(ff, ffprobe, proj, cache, use_cache) -> dict:
    """qc WARN-only row: per clip shot, mouth-region motion of the face inside the crop vs the
    source speech envelope (and vs other faces in the source frame when there are any). Verdict
    ok / ambiguous / WARN; never fails the qc. Cached per shot (only changed shots re-run)."""
    sk = __import__("sidekit")
    ik = sk._ik()
    src = proj.get("_src_abs", proj["src"])
    fps = proj.get("fps", 30)
    wh = ik._src_wh(ffprobe, src)
    shots, warns, n_new, cnt = {}, [], 0, {"ok": 0, "ambiguous": 0, "WARN": 0}
    for s, a_, b_ in ik.timeline(proj):
        if s["kind"] != "clip":
            continue
        key = _sha("mouth-v2", sk.src_id(src), s["src_in"], s.get("audio_out"), s["frames"],
                   [s.get(k) for k in ("crop_x", "crop_y", "crop_w", "crop_h")], s.get("who"))
        r = cache.get(key) if use_cache else None
        if r is None:
            try:
                r = _mouth_shot(ik, sk, ff, src, s, wh, fps)
            except Exception as ex:          # advisory: never let it break qc
                r = {"verdict": "ambiguous", "why": f"check failed: {ex}"}
            cache[key] = r
            n_new += 1
        r = dict(mouth_verdict(r), beat=s.get("beat", ""), who=s.get("who"))
        shots[s["i"]] = r
        cnt[r["verdict"]] = cnt.get(r["verdict"], 0) + 1
        if r["verdict"] == "WARN":
            warns.append({"where": f"shot {s['i']} @{a_:.2f}-{b_:.2f}s", "detail": r["why"],
                          "fix": "look at the shot: is the person talking the one on screen? (WARN only)"})
    return {"pass": True, "fails": [], "warns": warns, "shots": shots,
            "summary": f"{len(shots)} shots ({n_new} checked): {cnt['ok']} ok, {cnt['ambiguous']} ambiguous, "
                       f"{cnt['WARN']} warn (advisory)"}

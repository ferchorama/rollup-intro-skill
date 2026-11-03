"""speakers — offline voiceprints and diarization for introkit (no account, no token, no torch).

Model: the WeSpeaker ResNet34 speaker-embedding network as exported to ONNX in the public,
ungated Hugging Face repo `altunenes/speaker-diarization-community-1-onnx`
(file `embedding_model.onnx`, 256-d output; upstream pyannote/speaker-diarization-community-1
is CC-BY-4.0). It is downloaded once into <ROLLUP_INTRO_HOME>/models/speaker/.

Input features are Kaldi-style 80-bin log-mel filterbanks (25 ms / 10 ms, Povey window,
pre-emphasis 0.97, DC removal) with per-utterance mean removal, computed here in numpy.

Diarization = speech regions (from the transcript's word times, or an energy VAD) ->
1.5 s windows every 0.5 s -> embeddings -> either
  * enrollment: nearest enrolled speaker (cosine), when clips of guest/host are given, or
  * average-linkage agglomerative clustering on cosine distance, with a known speaker
    count or one chosen by silhouette score,
then each word takes the label of the windows that overlap it, and each cue the majority.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

EMBED_REPO = "altunenes/speaker-diarization-community-1-onnx"
EMBED_FILE = "embedding_model.onnx"
EMBED_REVISION = "e2e09da94ae093a56cd5a60a09b138ae3da1959c"     # pinned, verified 2026-10-30
SR = 16000
WIN_S, HOP_S, MIN_WIN_S = 1.5, 0.5, 0.8     # tuned 2026-10-30 on a 2-speaker clip

# ---------------------------------------------------------------- features

_FL, _FS, _NFFT, _NMEL = 400, 160, 512, 80


def _mel(f):
    return 1127.0 * np.log(1.0 + np.asarray(f, dtype=np.float64) / 700.0)


def _mel_banks() -> np.ndarray:
    nb = _NFFT // 2 + 1
    freqs = np.arange(nb) * (SR / _NFFT)
    m = _mel(freqs)
    lo, hi = float(_mel(20.0)), float(_mel(SR / 2))
    d = (hi - lo) / (_NMEL + 1)
    banks = np.zeros((_NMEL, nb))
    for b in range(_NMEL):
        l, c, r = lo + b * d, lo + (b + 1) * d, lo + (b + 2) * d
        up = (m > l) & (m <= c)
        dn = (m > c) & (m < r)
        banks[b, up] = (m[up] - l) / (c - l)
        banks[b, dn] = (r - m[dn]) / (r - c)
    return banks


_BANKS = _mel_banks()
_WIN = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(_FL) / (_FL - 1))) ** 0.85


def fbank(x: np.ndarray) -> np.ndarray:
    n = 1 + (len(x) - _FL) // _FS
    if n <= 0:
        return np.zeros((0, _NMEL), np.float32)
    idx = np.arange(_FL)[None, :] + _FS * np.arange(n)[:, None]
    fr = x[idx].astype(np.float64)
    fr -= fr.mean(axis=1, keepdims=True)
    fr[:, 1:] -= 0.97 * fr[:, :-1]
    fr *= _WIN
    sp = np.fft.rfft(fr, n=_NFFT)
    mel = (sp.real ** 2 + sp.imag ** 2) @ _BANKS.T
    return np.log(np.maximum(mel, 1.1920929e-07)).astype(np.float32)


# ---------------------------------------------------------------- model

def model_path(models_dir: Path) -> Path:
    from huggingface_hub import hf_hub_download
    d = Path(models_dir) / "speaker"
    d.mkdir(parents=True, exist_ok=True)
    return Path(hf_hub_download(EMBED_REPO, EMBED_FILE, revision=EMBED_REVISION, cache_dir=str(d)))


class Embedder:
    def __init__(self, session):
        self.s = session
        self.inp = session.get_inputs()[0].name

    def __call__(self, x: np.ndarray) -> np.ndarray:
        f = fbank(x)
        if f.shape[0] < 10:
            return np.zeros(256)
        f = f - f.mean(axis=0, keepdims=True)
        v = np.asarray(self.s.run(None, {self.inp: f[None]})[0], np.float64).reshape(-1)
        return v / (np.linalg.norm(v) + 1e-9)


# ---------------------------------------------------------------- regions / windows

def regions_from_words(cues: list[dict], offset: float = 0.0, gap: float = 0.3) -> list[tuple[float, float]]:
    ws = sorted((float(w["start"]) - offset, float(w["end"]) - offset)
                for c in cues for w in c.get("words", []) if w["end"] > w["start"])
    out: list[list[float]] = []
    for s, e in ws:
        if out and s - out[-1][1] <= gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(a, b) for a, b in out]


def regions_from_energy(x: np.ndarray, thr_db: float = -40.0, gap: float = 0.3) -> list[tuple[float, float]]:
    hop = int(0.02 * SR)
    n = len(x) // hop
    if n == 0:
        return []
    rms = np.sqrt(np.mean(x[: n * hop].reshape(n, hop) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms / (rms.max() + 1e-12))
    on = db > thr_db
    out, s = [], None
    for i, v in enumerate(on):
        t = i * hop / SR
        if v and s is None:
            s = t
        elif not v and s is not None:
            out.append([s, t]); s = None
    if s is not None:
        out.append([s, n * hop / SR])
    merged: list[list[float]] = []
    for a, b in out:
        if merged and a - merged[-1][1] <= gap:
            merged[-1][1] = b
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged if b - a >= 0.25]


def windows(regions) -> list[tuple[float, float]]:
    w = []
    for a, b in regions:
        if b - a < MIN_WIN_S:
            continue
        t = a
        while t + WIN_S <= b + 1e-6:
            w.append((t, t + WIN_S)); t += HOP_S
        if not w or w[-1][1] < b - 0.25:
            w.append((max(a, b - WIN_S), b))
    return w


# ---------------------------------------------------------------- clustering

def _avg_linkage(D: np.ndarray, k: int) -> np.ndarray:
    try:
        from scipy.cluster.hierarchy import fcluster, linkage
        from scipy.spatial.distance import squareform
        Z = linkage(squareform(D, checks=False), method="average")
        return fcluster(Z, t=k, criterion="maxclust") - 1
    except ImportError:
        lab = np.arange(len(D)); cl = {i: [i] for i in range(len(D))}
        while len(cl) > k:
            keys = list(cl); best = (9, None, None)
            for i, a in enumerate(keys):
                for b in keys[i + 1:]:
                    d = D[np.ix_(cl[a], cl[b])].mean()
                    if d < best[0]:
                        best = (d, a, b)
            cl[best[1]] += cl.pop(best[2])
        for n, (_, mem) in enumerate(cl.items()):
            lab[mem] = n
        return lab


def _silhouette(D: np.ndarray, lab: np.ndarray) -> float:
    ks = np.unique(lab)
    if len(ks) < 2:
        return -1.0
    s = []
    for i in range(len(D)):
        same = lab == lab[i]
        a = D[i, same].sum() / max(1, same.sum() - 1)
        b = min(D[i, lab == k].mean() for k in ks if k != lab[i])
        s.append((b - a) / max(a, b, 1e-9))
    return float(np.mean(s))


def cluster(E: np.ndarray, n_speakers: int | None, max_speakers: int = 6) -> tuple[np.ndarray, dict]:
    D = np.clip(1 - E @ E.T, 0, 2)
    if len(E) < 2:
        return np.zeros(len(E), int), {"k": 1}
    if n_speakers:
        lab = _avg_linkage(D, n_speakers)
        return lab, {"k": int(n_speakers), "chosen_by": "given"}
    best = (-2, None, 1)
    scores = {}
    for k in range(2, min(max_speakers, len(E)) + 1):
        lab = _avg_linkage(D, k)
        sc = _silhouette(D, lab); scores[k] = round(sc, 3)
        if sc > best[0]:
            best = (sc, lab, k)
    return best[1], {"k": best[2], "chosen_by": "silhouette", "silhouette": scores}


# ---------------------------------------------------------------- diarize + merge

def diarize(x: np.ndarray, emb: Embedder, cues: list[dict] | None = None, offset: float = 0.0,
            n_speakers: int | None = None, enroll: dict[str, np.ndarray] | None = None,
            min_margin: float = 0.05) -> dict:
    """x: mono float32 16 kHz starting at `offset` source seconds. Returns turns in source time."""
    regs = regions_from_words(cues, offset) if cues else regions_from_energy(x)
    wins = windows(regs)
    if not wins:
        return {"turns": [], "windows": 0, "method": "none"}
    E = np.stack([emb(x[int(a * SR):int(b * SR)]) for a, b in wins])
    info: dict = {}
    if enroll:
        names = list(enroll)
        C = np.stack([enroll[n] for n in names])
        S = E @ C.T
        lab = S.argmax(1)
        srt = np.sort(S, axis=1)
        margin = srt[:, -1] - (srt[:, -2] if S.shape[1] > 1 else 0)
        labels = [names[i] if m >= min_margin else "unknown" for i, m in zip(lab, margin)]
        info = {"method": "enrollment", "speakers": names}
        conf = S.max(1)
    else:
        lab, info = cluster(E, n_speakers)
        info["method"] = "agglomerative-cosine"
        labels = [f"SPEAKER_{int(l):02d}" for l in lab]
        conf = np.array([float(E[i] @ E[lab == lab[i]].mean(0) / (np.linalg.norm(E[lab == lab[i]].mean(0)) + 1e-9))
                         for i in range(len(E))])
    wl = [{"start": round(a + offset, 3), "end": round(b + offset, 3), "speaker": s, "score": round(float(c), 3)}
          for (a, b), s, c in zip(wins, labels, conf)]
    turns: list[dict] = []
    for w in wl:     # merge consecutive windows with the same label into turns
        if turns and turns[-1]["speaker"] == w["speaker"] and w["start"] <= turns[-1]["end"] + 0.35:
            turns[-1]["end"] = max(turns[-1]["end"], w["end"])
        else:
            turns.append({"start": w["start"], "end": w["end"], "speaker": w["speaker"]})
    info.update(turns=turns, windows=len(wins), window_labels=wl)
    return info


def label_words(cues: list[dict], window_labels: list[dict]) -> list[dict]:
    """Give each word the label with the most window overlap (centre-weighted) and each cue
    the majority over its words. Format stays cues[].words[] plus a "speaker" field."""
    for c in cues:
        votes: dict[str, float] = {}
        for w in c.get("words", []):
            s, e = float(w["start"]), float(w["end"])
            mid = (s + e) / 2
            best, bw = None, 0.0
            for wl in window_labels:
                ov = min(e, wl["end"]) - max(s, wl["start"])
                if ov <= 0:
                    continue
                cen = 1 - abs(mid - (wl["start"] + wl["end"]) / 2) / ((wl["end"] - wl["start"]) / 2 + 1e-9)
                wt = ov * (0.5 + max(0.0, cen))
                if wt > bw:
                    best, bw = wl["speaker"], wt
            if best is None and window_labels:      # zero-length / gap word: nearest window centre
                best = min(window_labels, key=lambda wl: abs(mid - (wl["start"] + wl["end"]) / 2))["speaker"]
            w["speaker"] = best
            if best:
                votes[best] = votes.get(best, 0.0) + (e - s)
        c["speaker"] = max(votes, key=votes.get) if votes else None
    return cues


def split_cues_by_speaker(cues: list[dict]) -> list[dict]:
    """One speaker per cue: split a cue wherever its word labels change. Renumbers ids."""
    out = []
    for c in cues:
        ws = c.get("words") or []
        runs: list[list[dict]] = []
        for w in ws:
            if runs and runs[-1][-1].get("speaker") == w.get("speaker"):
                runs[-1].append(w)
            else:
                runs.append([w])
        if len(runs) <= 1:
            out.append(c); continue
        for r in runs:
            out.append({**c, "start": r[0]["start"], "end": r[-1]["end"], "speaker": r[0].get("speaker"),
                        "text": " ".join(w["text"] for w in r), "words": r})
    for i, c in enumerate(out, 1):
        c["id"] = i
    return out


def cosine_matrix(vecs: dict[str, np.ndarray]) -> dict:
    names = list(vecs)
    M = np.stack([vecs[n] for n in names])
    S = M @ M.T
    return {"names": names, "cosine": [[round(float(v), 3) for v in row] for row in S]}


def save(path: Path, obj) -> None:
    Path(path).write_text(json.dumps(obj, indent=1, ensure_ascii=False), encoding="utf-8")

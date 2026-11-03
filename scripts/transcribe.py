"""transcribe — standalone, word-timestamped ASR for the Rollup intro skill.

Whisper large-v3-turbo through faster-whisper (CTranslate2): float16 on NVIDIA CUDA,
int8 on CPU everywhere else (incl. Apple Silicon). Audio is decoded by ffmpeg, not PyAV.

Output JSON (same schema the skill has always consumed):
  {"meta": {...},
   "cues": [{"id": 1, "start": s, "end": s, "speaker": null|"SPEAKER_00"|"guest",
             "text": "...",
             "words": [{"start": s, "end": s, "text": "word", "probability": p,
                        "speaker": ...optional}]}]}
Times are SOURCE seconds (a --start offset is added back). SRT/TXT are written alongside.

Run through introkit (recommended):  python scripts/introkit.py transcribe --src VIDEO
or directly:                         python scripts/transcribe.py --src VIDEO
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SR = 16000


def load_audio(ff: str, src, start: float | None = None, dur: float | None = None):
    import numpy as np
    cmd = [ff, "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    p = subprocess.run(cmd + ["-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
                       capture_output=True)
    if p.returncode:
        sys.exit(f"[transcribe] ffmpeg could not decode {src}: "
                 + p.stderr.decode("utf-8", "replace")[-400:])
    return np.frombuffer(p.stdout, dtype=np.float32).copy()


def run_asr(model, audio, language="en", hotwords=None, vad=True, offset=0.0) -> list[dict]:
    kw = dict(language=language, word_timestamps=True, vad_filter=vad,
              condition_on_previous_text=False, beam_size=5)
    if hotwords:
        kw["hotwords"] = hotwords
    try:
        segs, _ = model.transcribe(audio, **kw)
    except TypeError as e:              # faster-whisper without hotwords support
        if "hotwords" not in str(e):
            raise
        kw.pop("hotwords")
        segs, _ = model.transcribe(audio, **kw)
    cues = []
    for i, sg in enumerate(segs, 1):
        cues.append({"id": i, "start": round(sg.start + offset, 3), "end": round(sg.end + offset, 3),
                     "speaker": None, "text": sg.text.strip(),
                     "words": [{"start": round(w.start + offset, 3), "end": round(w.end + offset, 3),
                                "text": w.word.strip(), "probability": round(float(w.probability), 4)}
                               for w in (sg.words or [])]})
    return cues


def _ts(t: float, sep=",") -> str:
    ms = round(max(0.0, t) * 1000)
    h, r = divmod(ms, 3_600_000); m, r = divmod(r, 60_000); s, ms = divmod(r, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def write_outputs(cues: list[dict], meta: dict, out_json: Path, formats=("json", "srt", "txt")) -> list[Path]:
    out_json = Path(out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    base = out_json.with_suffix("")
    written = []
    if "json" in formats:
        out_json.write_text(json.dumps({"meta": meta, "cues": cues}, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        written.append(out_json)
    if "srt" in formats:
        p = base.with_suffix(".srt")
        with p.open("w", encoding="utf-8", newline="\n") as fh:
            for i, c in enumerate(cues, 1):
                spk = f"[{c['speaker']}] " if c.get("speaker") else ""
                fh.write(f"{i}\n{_ts(c['start'])} --> {_ts(c['end'])}\n{spk}{c['text']}\n\n")
        written.append(p)
    if "txt" in formats:
        p = base.with_suffix(".txt")
        with p.open("w", encoding="utf-8", newline="\n") as fh:
            for c in cues:
                spk = f"{c['speaker']}: " if c.get("speaker") else ""
                fh.write(f"{_ts(c['start'], '.')}  {spk}{c['text']}\n")
        written.append(p)
    return written


def read_transcript(path: Path) -> dict:
    """Read this skill's JSON (or any {"cues":[...words...]} JSON). Plain .srt is accepted
    for line finding only: it has no word times and no speakers."""
    path = Path(path)
    if path.suffix.lower() == ".json":
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"meta": {}, "cues": d}
    if path.suffix.lower() == ".srt":
        import re
        cues = []
        for blk in re.split(r"\n\s*\n", path.read_text(encoding="utf-8-sig").strip()):
            ln = blk.strip().splitlines()
            m = next((re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", x)
                      for x in ln if "-->" in x), None)
            if not m:
                continue
            g = [int(v) for v in m.groups()]
            st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
            en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
            txt = " ".join(x for x in ln if "-->" not in x and not x.strip().isdigit())
            cues.append({"id": len(cues) + 1, "start": st, "end": en, "speaker": None, "text": txt, "words": []})
        return {"meta": {"source": str(path), "format": "srt"}, "cues": cues}
    sys.exit(f"[transcribe] unsupported transcript {path}")


def has_speakers(tr: dict) -> bool:
    return any(c.get("speaker") for c in tr.get("cues", []))


def has_words(tr: dict) -> bool:
    return any(c.get("words") for c in tr.get("cues", []))


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import introkit
    introkit.main(["transcribe", *sys.argv[1:]])

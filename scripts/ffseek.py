"""Accurate FFmpeg seek, probe and delivery-metrics helpers for the Rollup intro kit.

Cuts use an accurate seek: input-seek one second early, then drop that margin
on the output side so video and audio start at the requested time, and bound
the audio length with atrim at sample resolution.

Why this exists: on B-frame MP4/MOV, an input -ss can land on a keyframe *after*
the requested time and drop the leading frames (and, on a video file, an input
audio seek follows the video keyframe). That is the wrong tool for word-level
in/out points. TOOLS.md already said '-ss before -i is a fast seek — render only,
never verify with it'; the build, vet, QC and transcribe windowed reads follow
that rule.

Ideas (hybrid preroll seek, atrim-bounded audio, VFR / start-time / A-V skew
probe, true-peak loudness) were informed by kajisho5/ffmpeg-skill v3.8.4
(MIT License, Copyright (c) 2026 kajisho5, https://github.com/kajisho5/ffmpeg-skill).
The code and wording here are ours; we did not vendor that project's scripts.
See references/ffmpeg.md.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SEEK_MARGIN = 1.0  # seconds of preroll dropped on the output side


def parse_rate(s) -> float | None:
    if not s or s in ("N/A", "0/0"):
        return None
    try:
        if isinstance(s, (int, float)):
            return float(s)
        if "/" in str(s):
            a, b = str(s).split("/", 1)
            return float(a) / float(b) if float(b) else None
        return float(s)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def enrich(info: dict) -> dict:
    """Extra facts from an ffprobe -show_streams -show_format JSON object."""
    fmt = info.get("format") or {}
    streams = info.get("streams") or []
    v = next((s for s in streams if s.get("width")), None)
    au = next((s for s in streams if s.get("codec_type") == "audio" or s.get("sample_rate")), None)
    out: dict = {
        "duration": float(fmt["duration"]) if fmt.get("duration") else None,
        "size_bytes": int(fmt["size"]) if fmt.get("size") else None,
        "bit_rate": int(fmt["bit_rate"]) if fmt.get("bit_rate") else None,
        "start_time": float(fmt.get("start_time") or 0.0),
        "format_name": fmt.get("format_name"),
        "nb_streams": int(fmt.get("nb_streams") or len(streams)),
    }
    if v:
        r, a = parse_rate(v.get("r_frame_rate")), parse_rate(v.get("avg_frame_rate"))
        out.update({
            "width": v.get("width"), "height": v.get("height"),
            "fps": v.get("r_frame_rate"), "fps_avg": v.get("avg_frame_rate"),
            "fps_r": r, "fps_avg_n": a,
            "vfr_suspected": bool(r and a and abs(r - a) / max(r, a) > 0.01),
            "codec": v.get("codec_name"), "pix_fmt": v.get("pix_fmt"),
            "sar": v.get("sample_aspect_ratio") or "1:1",
            "profile": v.get("profile"),
            "color_space": v.get("color_space"),
            "color_transfer": v.get("color_transfer"),
            "color_primaries": v.get("color_primaries"),
            "has_b_frames": v.get("has_b_frames"),
            "video_start": float(v.get("start_time") or 0.0),
        })
    if au:
        out["audio"] = {
            "codec": au.get("codec_name"),
            "rate": au.get("sample_rate"),
            "channels": au.get("channels"),
            "bitrate": au.get("bit_rate"),
            "start": float(au.get("start_time") or 0.0),
        }
        if v:
            out["av_start_skew_s"] = round(out["audio"]["start"] - out.get("video_start", 0.0), 4)
    return out


def video_seek_args(start: float, dur: float | None) -> tuple[list[str], list[str]]:
    """(args to place just before -i, args to place just after the input path).

    -ss (start-margin) -i SRC -ss margin [-t dur]
    """
    start = max(0.0, float(start or 0.0))
    m = min(SEEK_MARGIN, start)
    before = ["-ss", f"{start - m:.6f}"]
    after = ["-ss", f"{m:.6f}"]
    if dur is not None:
        after += ["-t", f"{float(dur):.6f}"]
    return before, after


def extract_pcm(ff: str, path, start: float | None = None, end: float | None = None,
                sr: int = 16000, channels: int = 1, raw: str | None = None):
    """Decode a float32 PCM span (mono or interleaved stereo) from path.

    Uses the 1 s preroll + output -ss recipe and atrim so the returned samples
    start at *start* and last *end-start* (sample-accurate).
    raw= is a headerless PCM format string (introkit SFX); those skip seeking.
    """
    import numpy as np
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin"]
    if raw:
        if start:
            cmd += ["-ss", f"{float(start):.4f}"]
        cmd += ["-f", raw, "-ar", "44100", "-ac", "2", "-i", str(path)]
        if end is not None and start is not None:
            cmd += ["-t", f"{max(0.01, float(end) - float(start)):.4f}"]
        elif end is not None:
            cmd += ["-t", f"{max(0.01, float(end)):.4f}"]
    elif start is None and end is None:
        cmd += ["-i", str(path)]
    else:
        t0 = 0.0 if start is None else max(0.0, float(start))
        dur = None if end is None else max(0.01, float(end) - t0)
        before, after = video_seek_args(t0, dur)
        cmd += before + ["-i", str(path)] + after
        if dur is not None:
            cmd += ["-af", f"atrim=end={dur:.6f},asetpts=PTS-STARTPTS"]
    cmd += ["-vn", "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "pipe:1"]
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode:
        err = p.stderr.decode("utf-8", "replace")[-400:]
        sys.exit(f"[introkit] audio extract failed:\n  {' '.join(cmd)}\n{err}")
    x = np.frombuffer(p.stdout, dtype=np.float32).copy()
    if channels > 1:
        if x.size % channels:
            x = x[: x.size - (x.size % channels)]
        x = x.reshape(-1, channels)
    return x


def ebur(ff: str, path) -> dict:
    """Integrated loudness, LRA and true peak from ffmpeg ebur128=peak=true."""
    p = subprocess.run(
        [ff, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    text = p.stderr or ""
    out = {"I": 0.0, "LRA": 0.0, "TP": 0.0}

    def last(tag: str):
        vals = []
        for ln in text.splitlines():
            if tag in ln:
                tail = ln.split(tag, 1)[1].strip().split()
                if tail:
                    try:
                        vals.append(float(tail[0].rstrip(",")))
                    except ValueError:
                        pass
        return vals[-1] if vals else None

    # Prefer an explicit True-peak label; peak=true still prints "Peak:" on many builds.
    tp = last("True peak:") or last("True Peak:") or last("True_peak:") or last("Peak:")
    I = last("I:")
    lra = last("LRA:")
    if I is not None:
        out["I"] = I
    if lra is not None:
        out["LRA"] = lra
    if tp is not None:
        out["TP"] = tp
    out["I_err"] = round(abs(out["I"] + 14.0), 3)
    out["tp_over"] = round(max(0.0, out["TP"] + 1.0), 3)
    return out


def file_metrics(ff: str, ffprobe: str, path) -> dict:
    """Format + loudness + size for a finished media file (no ASR, no rebuild)."""
    p = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if p.returncode:
        sys.exit(f"[introkit] ffprobe failed on {path}:\n{(p.stderr or '')[-400:]}")
    info = json.loads(p.stdout)
    d = enrich(info)
    d["path"] = str(Path(path))
    d["loudness"] = ebur(ff, path)
    return d

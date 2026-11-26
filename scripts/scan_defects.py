"""Whole-file source-defect scan: dropouts, digital silence, clipping, static/hash.

The cold open is cut from an event recording, and event recordings arrive with holes: a
recorder dropout mid-sentence, a stretch of electrical hash, a clipped sample run. Five of the
27 episodes under K: needed a repair pass for exactly this (Jack, Ryan, Yannik, Tristan, Evan),
and every one of them re-derived the same scan by hand. This is that scan, as a tool.

Method (the one those sessions converged on):
  * decode the audio once to 48 kHz stereo s16, streamed, 10 ms blocks;
  * per block: RMS, peak, clipped-sample count, DC, zero-crossing rate, and high-band energy
    from the first difference (a cheap high-pass);
  * dropout      = RMS below `--silence-rms` (default 30 = about -60.7 dBFS) for >= 0.5 s;
  * clipping     = samples at +-32767, grouped into runs;
  * static/hash  = high-band/RMS ratio more than `--hash-db` above its own +-50 s median, for
                   >= 0.2 s (an absolute threshold does not work: rooms differ by 20 dB);
  * defects are also binned (`--bin`, default 30 s) so a quiet-looking file cannot hide a
    0.9 s hole.

It never writes audio and never encodes: it reports intervals and a suggested action. Use the
JSON (`--json`) as the defect list for a doner-splice repair, or cut the intervals out.

Validation (2026-11-24): on Tristan Thompson (890 s, one documented 0.94 s dropout at 8:05.25)
it reports exactly that hole at 485.19-486.19 = 8:05.19-8:06.19 and no static, matching the
written cutlist; the whole file takes 5 s. The 0.45 flatness cut is measured, not guessed:
12 000 blocks of real speech peak at 0.18, the documented electrical-hash burst measured 0.70.

    python scripts/scan_defects.py --src "<recording>.mp4"
    python scripts/scan_defects.py --src "<recording>.mp4" --start 480 --dur 60 -v
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

SR = 48000
BLK = 480                      # 10 ms
SILENCE_RMS = 30               # int16 units, about -60.7 dBFS
MIN_SILENCE_S = 0.5
MIN_HASH_S = 0.2
HASH_WINDOW_S = 50.0           # local median window for the hash baseline


def _ffmpeg() -> str:
    from shutil import which
    return which("ffmpeg") or "ffmpeg"


def decode_blocks(src: Path, start: float | None, dur: float | None) -> dict:
    """Stream the audio through ffmpeg and return per-10 ms-block features."""
    cmd = [_ffmpeg(), "-v", "error", "-nostdin"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if dur:
        cmd += ["-t", f"{dur:.3f}"]
    cmd += ["-map", "0:a:0", "-vn", "-ac", "2", "-ar", str(SR), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    feats = {k: [] for k in ("rms_l", "rms_r", "peak_l", "peak_r", "clip_l", "clip_r",
                             "dc_l", "dc_r", "zcr", "hf", "flat")}
    carry = b""
    while True:
        chunk = proc.stdout.read(SR * 4 * 5)          # ~5 s of interleaved stereo
        if not chunk:
            break
        chunk = carry + chunk
        usable = (len(chunk) // (BLK * 4)) * BLK * 4
        carry = chunk[usable:]
        if not usable:
            continue
        seg = np.frombuffer(chunk[:usable], dtype="<i2").astype(np.float32).reshape(-1, 2)
        L, R = seg[:, 0], seg[:, 1]
        nb = len(L) // BLK
        if not nb:
            continue
        L2, R2 = L[: nb * BLK].reshape(nb, BLK), R[: nb * BLK].reshape(nb, BLK)
        feats["rms_l"].append(np.sqrt(np.mean(L2 ** 2, axis=1) + 1e-9))
        feats["rms_r"].append(np.sqrt(np.mean(R2 ** 2, axis=1) + 1e-9))
        feats["peak_l"].append(np.max(np.abs(L2), axis=1))
        feats["peak_r"].append(np.max(np.abs(R2), axis=1))
        feats["clip_l"].append(np.sum(np.abs(L2) >= 32767, axis=1))
        feats["clip_r"].append(np.sum(np.abs(R2) >= 32767, axis=1))
        feats["dc_l"].append(np.mean(L2, axis=1))
        feats["dc_r"].append(np.mean(R2, axis=1))
        mono = (L2 + R2) * 0.5
        feats["zcr"].append(np.mean(np.abs(np.diff(np.signbit(mono).astype(np.int8), axis=1)), axis=1))
        feats["hf"].append(np.sqrt(np.mean(np.diff(mono, axis=1) ** 2, axis=1) + 1e-9))
        # spectral flatness (geometric / arithmetic mean of the magnitude spectrum):
        # speech sits far below 0.5, electrical hash approaches 0.6-0.8. This is what
        # separates a fricative (a short HF spike on top of voiced speech) from static.
        mag = np.abs(np.fft.rfft(mono * np.hanning(BLK).astype(np.float32), axis=1)) + 1e-9
        feats["flat"].append(np.exp(np.mean(np.log(mag), axis=1)) / np.mean(mag, axis=1))
    err = proc.stderr.read().decode("utf-8", "replace").strip()
    proc.wait()
    if err:
        print(f"[scan] ffmpeg: {err}", file=sys.stderr)
    out = {k: np.concatenate(v) if v else np.zeros(0, np.float32) for k, v in feats.items()}
    out["t_offset"] = float(start or 0.0)
    return out


def runs(mask: np.ndarray, min_len: int) -> list[tuple[int, int]]:
    out, i, n = [], 0, len(mask)
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            if j - i + 1 >= min_len:
                out.append((i, j))
            i = j + 1
        else:
            i += 1
    return out


def local_median(x: np.ndarray, win: int) -> np.ndarray:
    """Median of `x` over a +-win block window, computed in chunks (memory-bounded)."""
    n = len(x)
    if n == 0:
        return x
    half = max(1, win // 2)
    pad = np.pad(x, (half, half), mode="edge")
    out = np.empty(n, np.float32)
    span = 2 * half + 1
    for s in range(0, n, 20000):
        e = min(n, s + 20000)
        view = np.lib.stride_tricks.sliding_window_view(pad[s:s + span + (e - s) - 1], span)
        out[s:e] = np.median(view, axis=1)
    return out


def scan(src: Path, start: float | None, dur: float | None, *, silence_rms: float,
         min_silence: float, hash_db: float, flat_thr: float, bin_s: float, verbose: bool) -> dict:
    f = decode_blocks(src, start, dur)
    n = len(f["rms_l"])
    t = f["t_offset"] + np.arange(n) * (BLK / SR)
    rms = (f["rms_l"] + f["rms_r"]) / 2.0
    peak = np.maximum(f["peak_l"], f["peak_r"])
    clip = f["clip_l"] + f["clip_r"]
    if not n:
        raise SystemExit("no audio decoded")

    # ---- dropouts / digital silence
    silence = rms < silence_rms
    sil_runs = runs(silence, max(1, int(round(min_silence * SR / BLK))))

    # ---- clipping
    clip_runs = runs(clip > 0, 1)

    # ---- static / hash: high-band ratio above its own local baseline AND a noise-like
    # spectrum. A fricative is also an HF spike, but it rides on voiced speech and its
    # flatness stays low; electrical hash is broadband, so both tests are required.
    hf_ratio = f["hf"] / (rms + 1.0)
    base = local_median(hf_ratio.astype(np.float32), int(HASH_WINDOW_S * SR / BLK))
    with np.errstate(divide="ignore", invalid="ignore"):
        lift_db = 20.0 * np.log10(np.maximum(hf_ratio, 1e-9) / np.maximum(base, 1e-9))
    hash_mask = (lift_db > hash_db) & (f["flat"] >= flat_thr)
    hash_runs = runs(hash_mask, max(1, int(round(MIN_HASH_S * SR / BLK))))

    # ---- binned view, so a short hole cannot hide in a long file
    per_bin = int(round(bin_s * SR / BLK))
    bins = []
    for s in range(0, n, per_bin):
        e = min(n, s + per_bin)
        zeros = int(np.sum(rms[s:e] < 1.0))
        bins.append(dict(t0=round(float(t[s]), 2), t1=round(float(t[e - 1] + BLK / SR), 2),
                         zero_pct=round(100.0 * zeros / (e - s), 2),
                         rms_db=round(float(20 * np.log10(np.mean(rms[s:e]) / 32768 + 1e-12)), 1),
                         clip=int(clip[s:e].sum())))

    def span(run):
        return round(float(t[run[0]]), 3), round(float(t[run[1]] + BLK / SR), 3)

    dropouts = []
    for run in sil_runs:
        lo, hi = span(run)
        dropouts.append(dict(t0=lo, t1=hi, length=round(hi - lo, 3), kind="dropout"))
    clips = []
    for run in clip_runs:
        lo, hi = span(run)
        if hi - lo < 0.01:
            continue
        clips.append(dict(t0=lo, t1=hi, length=round(hi - lo, 3), kind="clipping",
                          samples=int(clip[run[0]:run[1] + 1].sum())))
    hashes = []
    for run in hash_runs:
        lo, hi = span(run)
        hashes.append(dict(t0=lo, t1=hi, length=round(hi - lo, 3), kind="static",
                           lift_db=round(float(np.max(lift_db[run[0]:run[1] + 1])), 1),
                           flatness=round(float(np.median(f["flat"][run[0]:run[1] + 1])), 2)))

    # ---- verdict: a hole that swallows speech is cut (or spliced); a short one is listened to
    def verdict(d):
        if d["kind"] == "dropout":
            if d["length"] >= 1.0:
                return "CUT (or donor-splice) - speech inside a hole this long is lost"
            if d["length"] >= 0.15:
                return "LISTEN - short hole; cut it if it lands on a word"
            return "ignore - shorter than one word"
        if d["kind"] == "clipping":
            return "LISTEN - clipped samples; re-cut or gain the piece down"
        return "REPAIR (adeclick + lowpass) if speech is under it, else CUT"

    for d in dropouts + hashes + clips:
        d["action"] = verdict(d)

    return dict(src=str(src), start=round(float(start or 0), 3),
                scanned_s=round(float(n * BLK / SR), 3),
                loudness_rms_db=round(float(20 * np.log10(np.mean(rms) / 32768 + 1e-12)), 1),
                block_ms=round(BLK / SR * 1000, 0),
                dropouts=dropouts, clipping=clips, static=hashes, bins=bins,
                counts=dict(dropouts=len(dropouts), clipping=len(clips), static=len(hashes),
                            clipped_samples=int(clip.sum()),
                            silent_s=round(float(sum(d["length"] for d in dropouts)), 2)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True, help="recording to scan (video or audio)")
    ap.add_argument("--start", type=float, default=None, help="scan window start, s")
    ap.add_argument("--dur", type=float, default=None, help="scan window length, s")
    ap.add_argument("--silence-rms", type=float, default=SILENCE_RMS,
                    help="dropout threshold, int16 RMS of a 10 ms block (default 30 = -60.7 dBFS)")
    ap.add_argument("--min-silence", type=float, default=MIN_SILENCE_S, help="min dropout, s")
    ap.add_argument("--hash-db", type=float, default=6.0,
                    help="high-band lift over the local median that counts as static (default 6)")
    ap.add_argument("--bin", type=float, default=30.0, help="bin width for the coverage table, s")
    ap.add_argument("--flatness", type=float, default=0.45,
                    help="spectral flatness that counts as noise-like (default 0.45; speech < 0.4)")
    ap.add_argument("--json", default=None, help="write the intervals here")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every interval")
    a = ap.parse_args(argv)
    return _run(a)


def cmd_scan(a) -> None:
    """Entry point for `introkit.py scan-defects` (the namespace is already parsed)."""
    raise SystemExit(_run(a))


def _run(a) -> int:
    src = Path(a.src)
    if not src.is_file():
        raise SystemExit(f"no such file: {src}")
    rep = scan(src, a.start, a.dur, silence_rms=a.silence_rms, min_silence=a.min_silence,
               hash_db=a.hash_db, flat_thr=a.flatness, bin_s=a.bin, verbose=a.verbose)

    c = rep["counts"]
    print(f"[scan] {src.name}  {rep['scanned_s']:.1f}s scanned  mean RMS {rep['loudness_rms_db']} dB  "
          f"blocks of {rep['block_ms']:.0f} ms")
    print(f"[scan] dropouts {c['dropouts']} ({c['silent_s']}s silent)   clipping runs {c['clipping']} "
          f"({c['clipped_samples']} samples at full scale)   static bursts {c['static']}")
    for kind, label in (("dropouts", "DROPOUT"), ("static", "STATIC"), ("clipping", "CLIP")):
        rows = rep[kind]
        show = rows if a.verbose else sorted(rows, key=lambda d: -d["length"])[:8]
        if rows:
            print(f"\n  {label}")
            for d in show:
                extra = f"  {d['samples']} samples" if "samples" in d else (
                        f"  +{d['lift_db']} dB HF, flatness {d['flatness']}" if "lift_db" in d else "")
                print(f"    {d['t0']:9.2f}-{d['t1']:9.2f}  {d['length']:6.2f}s{extra}   {d['action']}")
            if not a.verbose and len(rows) > len(show):
                print(f"    ... {len(rows) - len(show)} more (use -v)")
    zero_bins = [b for b in rep["bins"] if b["zero_pct"] > 0]
    if zero_bins:
        print(f"\n  bins with digital zeros ({len(zero_bins)}):")
        for b in zero_bins[:10]:
            print(f"    {b['t0']:9.1f}-{b['t1']:9.1f}  {b['zero_pct']:5.2f}% zeros  RMS {b['rms_db']} dB")

    if a.json:
        Path(a.json).write_text(json.dumps(
            dict(src=rep["src"], scanned_s=rep["scanned_s"], counts=c,
                 defects=[{"t0": d["t0"], "t1": d["t1"],
                           "reason": f"{d['kind']}: {d['action']}"}
                          for d in rep["dropouts"] + rep["static"] + rep["clipping"]]),
            indent=1), encoding="utf-8")
        print(f"\n[scan] wrote {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


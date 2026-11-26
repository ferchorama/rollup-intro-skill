# FFmpeg recipes used by the intro pipeline

This file is the FFmpeg handbook for Rollup cold opens: what we adopted from
[kajisho5/ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill) v3.8.4, how the
kit now uses it, and how to measure whether Eric's intros improved on Kash / Quinn.

**License:** ffmpeg-skill is **MIT** (Copyright (c) 2026 kajisho5). We may use and
adapt it with attribution. We did **not** vendor its 42 scripts, templates, or
MCP server — that toolkit is a general editor (captions, Rec.709/HDR, Reels
export, multicam, silence-as-a-product). Our intros already have their own
framing, captions, mix, and 1080p30 H.264 delivery. We took the recipes that
fix *our* recurring cut and measurement problems and reimplemented them in
`scripts/ffseek.py`, then wired them into `introkit.py`, `sidekit.py` and
`transcribe.py`. The code and wording here are ours.

## What is now used in the pipeline (default)

These are used for Eric and every later intro.

### 1. Accurate cuts (the important change)

**Problem.** `stage_base`, `stage_audio`, `vet`/`qc` energy reads and windowed
`transcribe` all used `-ss` *before* `-i`. On B-frame MP4/MOV an input seek can
land on a keyframe *after* the requested time and drop the leading frames. On a
video file, an input *audio* seek follows the video keyframe. A 20–80 ms miss
is the difference between a finished "crypto" and a clipped "cryp…", or a
clean out and a stray "and". TOOLS.md already said "-ss before -i is a fast
seek — render only, never verify with it"; the render itself was still doing it.

**Accurate cut.** For a span starting at `T` lasting `D`:

```
ffmpeg -ss (T-1) -i SRC -ss 1 -t D ...
```

plus, on audio extracts, do **not** pair output `-ss 1` with `atrim=end=D`. After
output `-ss`, PTS still start at the preroll, so `atrim=end=D` keeps only `D-1`
seconds and drops the out-point (and, through `stage_audio`, the last second of
built dialogue). The sample-accurate recipe is input-seek only, then atrim on
that preroll timeline:

```
ffmpeg -ss (T-1) -i SRC -t (1+D) -af atrim=start=1:end=1+D,asetpts=PTS-STARTPTS
```

Picture (`stage_base`, `frame` scans) still uses `-ss (T-1) -i SRC -ss 1 -t D`
with no atrim and is duration-correct. The 1 s preroll is `ffseek.SEEK_MARGIN`.

`build`, `frame` (face scan), `vet`, `qc` (word-edge energy) and windowed
`transcribe` all go through `scripts/ffseek.py`. A full-file transcribe has no
`-ss` and is unchanged, so existing word times stay the timeline we cut from.

### 2. Richer probe

`introkit.py probe` now also records:

- `vfr_suspected` — `r_frame_rate` and `avg_frame_rate` disagree by > 1 %
- `sar`, `start_time`, `pix_fmt`, colour tags, `has_b_frames`
- `av_start_skew_s` — audio stream start minus video stream start

Plan from these numbers. Intros are still delivered CFR 30 fps, SAR 1:1,
yuv420p, BT.709. A VFR source is already conformed by the re-encode (`-r 30`).

### 3. Loudness is still measured on the delivered AAC

The kit already remasters to **-14 LUFS, true peak ≤ -1 dBTP on the encoded
AAC** (not the WAV — LESSONS B6). That stays. `ffseek.ebur` now also keeps
**LRA** and an explicit true-peak parse, and the same figures are written into
`build_metrics.json` and `introkit.py metrics`.

We did **not** replace the remaster with ffmpeg `loudnorm` two-pass. The
current loop already hits the gate and we do not want to change the sound of
approved intros in the same breath as the seek fix. `loudnorm=I=-14:TP=-1:linear=true`
remains a valid *measurement* cross-check, not the mix.

### 4. Verify after write; never overwrite a source

Unchanged rules, now also in this handbook: every shot's frame count is
checked; loudness is read back from the muxed file; originals are never
replaced (a revision is a new name: `_vN`, e.g. `_C_v8`). `--overwrite` is not a
kit flag.

## What we skipped (and why)

| ffmpeg-skill piece | Why it is not in this kit |
|---|---|
| The 42 CLI scripts, MCP server, contract/doctor, render templates | Parallel toolchain. Our entry point stays `introkit.py`. |
| `cut.py --smart` (re-encode only partial GOPs) | For **long** videos (full interviews, trims) use smart trim: re-encode only the head GOP and stream-copy the rest. For short intros (~20-30 s) a full high-quality NVENC re-encode is fine — do not spend long on frame-exact stream-copy splicing (rule 049). TheStitcher-V2 already stream-copies the interview. |
| `silence.py` / filler-as-a-product, `scenes.py` highlights | We already split dead air and fillers in `vet` / line selection, with our own floors and the "err late" rule. |
| Platform export (Reels, TikTok, Shorts, YouTube templates) | Delivery is fixed: 1080p30 H.264 High, CRF 15, AAC 320k, `-g 60`, `+faststart`. |
| Captions, lower-thirds, brand.json, emoji, CJK font routing | We already burn side-third captions with Pillow / bundled fonts. |
| HDR / Log / LUT / legalize, stabilize, denoise, rotate, 360, greenscreen | Interview masters are SDR Rec.709. |
| Multicam energy switching, lav-mic sync / drift | Camera + speaker work is voiceprint + YuNet, not their energy switcher. |
| `check.py --platform`, `look.py` contact sheets as a separate tool | We have `qc` (format, audio, framing, speech) and a contact sheet already. |
| Replacing `ebur128` remaster with `loudnorm` | See above. |

## Recipes (when you hand-write an ffmpeg line)

Prefer going through `introkit.py` / `ffseek.py`. If you must type a command:

**Short intro, full NVENC re-encode** (preferred for ~20-30 s caption or spelling fixes;
do not frame-exact splice these):

```
ffmpeg -y -hwaccel cuda -i SRC.mp4 -c:v h264_nvenc -preset p7 -tune hq -rc vbr -cq 17 -b:v 0 -profile:v high -pix_fmt yuv420p -c:a copy OUT_vN.mp4
```

**Smart trim of a long file** (re-encode only the head GOP, stream-copy the rest): find the
first keyframe at or after the in-point, NVENC-encode the prefix up to that keyframe, and
concat with a stream-copy of everything after it. Never overwrite the source.

**Word-accurate extract (audio):**

```
ffmpeg -ss 940.98 -i SRC -t 3.5 -af "atrim=start=1:end=3.5,asetpts=PTS-STARTPTS" -ac 1 -ar 16000 -f f32le out.f32
```

(The first `-ss` is 1 s before the in-point 941.98. `atrim` is on that preroll timeline, so the kept window is exactly 2.5 s starting at 941.98.)

**Frame-accurate still** (already in TOOLS.md): `-i FILE -ss T -frames:v 1` (`-ss` after `-i`).

**Loudness / true peak on the delivered file:**

```
ffmpeg -i FINAL.mp4 -af ebur128=peak=true -f null -
```

Read the summary `I:`, `LRA:`, and `Peak:` / `True peak:` — not an estimate, not the pre-AAC WAV.

**Do not** `-c copy` a word-level cut. Stream-copy snaps to a GOP and is the
classic "works but starts on a frozen or wrong frame" failure.

## Before / after metrics (Kash, Quinn vs Eric)

The point of the seek change is measurable cut-edge accuracy. Run the same
command on a finished intro from before this change (Kash, Quinn C10) and on
Eric once he is built.

```
# env python, from the skill folder
python scripts/introkit.py metrics --final "PATH\THE_ROLLUP_<Guest>_ColdOpen_<X>_original.mp4" --project "PATH\intros\work\project_<X>.framed.json" --words "PATH\intros\work\words_<X>.json" --out "PATH\intros\work\metrics_<X>.json"
```

Optional: `--qc-json PATH\qc_<X>.json` to attach a previous QC's ASR word
scores without re-running Whisper. `--asr` re-runs per-shot ASR on the mix
(slow; same engine as `qc` speech).

`build` also writes `intros\work\build_metrics.json` (elapsed seconds, seek
mode, size, loudness) after every render.

### What to compare

| Metric | Where | Before (Kash / Quinn) target reading | What "better" looks like on Eric |
|---|---|---|---|
| **end_margin_ms** | per clip shot: `audio_out - acoustic_end` (energy, 10 ms RMS vs room floor) | Watch for negative values (early cut: "cryp…") and for large positive values that eat the next word ("crypto and") | Median ≥ 0 ms, typically 20–150 ms tail; **zero FAIL ends**; fewer LISTEN ends than Quinn C7–C9 |
| **post_out_db** | mean energy in the 80 ms *after* `audio_out`, minus room floor | A stray "and" sits well above the floor | Within ~6 dB of the floor on non-join outs |
| **head_leak_db** | energy in the 80 ms *before* `src_in`, minus floor | C9's opening "yeah" is a loud leak | Near the floor; first script word is the first sound |
| **first_heard / last_heard** | mix ASR vs script (`--asr` or `--qc-json`) | last word p ≥ 0.6 and matches; first word is not "yeah"/"um" | Both true on every clip shot |
| **last_p / first_p** | Whisper probability of those edge words on the mix | Higher is cleaner (less clipped or buried) | Mean p up, min p ≥ 0.6 |
| **I_err** | `abs(I + 14)` on the delivered file | Already tight (Quinn C2 was −14.1) | Still ≤ 0.5 LU (gate); no regression |
| **tp_over** | `max(0, TP + 1)` dB | Must be 0 (TP ≤ −1.0) | Still 0 |
| **LRA** | loudness range, reported now | Informational | Note it; no pass/fail |
| **av_start_skew_s** | audio start − video start on the *delivered* file | Should be ~0 after mux | Stay ≤ 1 frame (≈ 0.033 s at 30 fps) |
| **size_bytes / bit_rate / duration** | container | CRF 15 is unchanged, so size should be similar for a similar length | No quality hunt: flag only a surprise jump |
| **build_elapsed_s** | `build_metrics.json` | Not recorded on Kash / Quinn | Accurate mode decodes ~1 s of preroll per shot; expect a small increase, not a full re-encode tax |

Energy metrics (`end_margin_ms`, `post_out_db`, `head_leak_db`) do not need
ASR and are the fair, repeatable comparison. Word-confidence figures need
`--asr` or an existing `qc` JSON.

### Suggested baselines (run, don't guess)

- Quinn: `THE_ROLLUP_Quinn_Thompson_ColdOpen_C10_original.mp4` and its `work/` project/words (and C7 if you want the known early "crypto" cut as a negative control).
- Kash: `THE_ROLLUP_Kash_Razzaghi_ColdOpen_A2_original.mp4` (the signed-off pick) and its `work/` project/words.
- After Eric builds: the same `metrics` command on each new version.

A rebuild of an older project with this seek path is the cleanest before/after
of the cut itself, but it is a rebuild — only do that when asked.

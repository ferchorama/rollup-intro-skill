# Tools: setup, hardware and exact usage

Everything the skill runs lives in **this folder**. Nothing outside it is required apart from
ffmpeg (and even that can be installed portably into the env folder). Paths in this file are
relative to the skill folder unless marked *example*.

---

## 1. One-folder environment (uv)

```
<skill>/
  scripts/introkit.py      entry point (re-runs itself inside the env venv)
  scripts/transcribe.py    standalone Whisper large-v3-turbo transcriber (word timestamps)
  scripts/speakers.py      voiceprints + diarization (ONNX, no account, no token)
  assets/                  music, SFX, logo, fonts (ASSETS.md)
  requirements*.txt        minimum versions; requirements*.lock.txt = pinned, tested set
  setup.ps1 / setup.sh     bootstrap uv + venv, then hand off to `introkit setup`
  scripts/setupkit.py      setup implementation (stdlib; also auto-runs when pieces are missing)
  .rollup-intro-env/       created by setup (git-ignored); or $ROLLUP_INTRO_HOME
    uv/                    uv itself, only if it was not already on PATH
    python/                uv-managed CPython 3.11
    venv/                  all Python packages, including PyTorch
    models/                Whisper large-v3-turbo (~1.6 GB), speaker model, Real-ESRGAN weights
    hf/  cache/            Hugging Face + uv caches (kept inside the folder)
    tools/ffmpeg/          portable ffmpeg, only if none was found
```

| OS | Command (run from the skill folder) |
|---|---|
| Windows (PowerShell 5.1 or 7) | `powershell -ExecutionPolicy Bypass -File .\setup.ps1` |
| macOS (Intel or Apple Silicon) / Linux | `bash ./setup.sh` |
| Any OS with Python 3.9+ already there | `python scripts/introkit.py setup` |

`introkit setup` is the single entry point (the shell scripts only bootstrap uv + a venv,
then hand off). Any other introkit command runs setup automatically the first time something
is missing (`INTROKIT_NO_SETUP=1` disables that). `introkit upscale` self-checks torch, the
weights, and one test inference.

Options: `-EnvHome D:\x` / `--home /x` / `introkit` respects `ROLLUP_INTRO_HOME`;
`-Cuda yes|no|auto` / `--cuda`; `-Ffmpeg portable|skip|auto` / `--ffmpeg`;
`-SkipModel` / `--skip-model` (download models later with `introkit.py fetch-model`);
`-Unlocked` / `--unlocked` to install from the minimum-version files instead of the locks.

What setup does: uses `uv` if on PATH, else installs it into `<env>/uv` without touching
your PATH; installs a uv-managed Python 3.11 into `<env>/python`; creates `<env>/venv`;
installs `requirements.lock.txt` (plus `requirements-cuda.lock.txt` when `nvidia-smi`
works on Windows/Linux: CUDA 12 cuBLAS + cuDNN 9 wheels, no system CUDA needed); installs
**PyTorch** (official CUDA wheel matching the driver, else the CPU build); finds or
installs ffmpeg (portable copy into the env folder, then winget / brew / apt); downloads
Whisper `large-v3-turbo` and the Real-ESRGAN general-x4v3 weights; skips anything already
present; prints a summary. Zoomed-shot upscaling is on by default afterwards. After setup
the skill runs **offline**. Uninstall = delete the env folder.

`introkit.py` can be launched with any Python 3.9+: it re-executes inside `<env>/venv`
(`INTROKIT_NO_REEXEC=1` disables that). It sets `HF_HOME` inside the env folder and, on
Windows/Linux with the CUDA wheels, makes their DLLs/.so files loadable.

## 2. ffmpeg (per OS)

introkit looks for ffmpeg in this order: `--ffmpeg PATH`, `INTROKIT_FFMPEG`, `PATH`,
`<env>/tools/ffmpeg/bin`. Any build â‰¥ 6 with libx264 works (tested: 8.0.1 Windows gyan
build, 7.1.5 Debian).

| OS | Install |
|---|---|
| Windows | setup prefers a portable BtbN win64-gpl build in `<env>/tools/ffmpeg`; `winget install Gyan.FFmpeg` is the fallback (`-Ffmpeg portable` forces the portable copy) |
| macOS | `brew install ffmpeg` (Homebrew, Intel or Apple Silicon; setup runs it if brew exists) |
| Linux | portable BtbN static build (x86_64 / arm64) into the env folder, or `sudo apt install ffmpeg` (or dnf/pacman) |

| Job | Invocation |
|---|---|
| Facts | `ffprobe -v error -show_streams -show_format -of json FILE` |
| Exact frame count | `ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of csv=p=0 FILE` |
| Loudness | `ffmpeg -i F -af ebur128=peak=true:framelog=quiet -f null -` |
| Dips in the reference | `-vf "blackdetect=d=0.08:pix_th=0.10"` |
| Silence / word tails | `-af "silencedetect=noise=-45dB:d=0.25"` |
| Channel identity | `-af astats=metadata=0:measure_perchannel=Peak_level+RMS_level:measure_overall=none` |
| Frame-accurate still | `-i FILE -ss T -frames:v 1` (ss **after** -i) |
| Word-accurate extract | audio: `-ss (T-1) -i SRC -t (1+D) -af atrim=start=1:end=1+D,asetpts=PTS-STARTPTS`; picture: `-ss (T-1) -i SRC -ss 1 -t D` (`scripts/ffseek.py`) |
| Delivery (introkit does this) | `-c:v libx264 -preset medium -crf 15 -profile:v high -pix_fmt yuv420p -r 30 -g 60 -movflags +faststart -threads 0 -c:a aac -b:a 320k -ar 48000 -ac 2` |

**Traps**: `-ss` before `-i` alone is a fast seek and can land late on B-frame MP4. `build` / `vet` / `qc` / windowed `transcribe` use the accurate preroll recipe (`references/ffmpeg.md`).
`-frames:v N` is the only reliable frame-exact segment length; verify with `-count_frames`.
`overlay` needs `format=auto` when the base is yuv420p and the overlay RGBA.

## 3. Hardware tiers (CPU-only is the baseline)

The skill must run on machines **without a dedicated GPU**. Every accelerated path is
optional, self-tested and falls through silently:

| Tier | OS | Decode (`-hwaccel`) | Preview encoder | Notes |
|---|---|---|---|---|
| `cuda` | Win/Linux | cuda | h264_nvenc | NVIDIA. Whisper float16 on CUDA |
| `qsv` | Win/Linux | qsv | h264_qsv | Intel Quick Sync (iGPU or Arc) |
| `d3d11va` / `dxva2` | Windows | d3d11va / dxva2 | h264_amf (AMD) else libx264 | any DirectX GPU |
| `vaapi` | Linux | vaapi | (libx264) | Intel/AMD on Mesa |
| `videotoolbox` | macOS | videotoolbox | h264_videotoolbox | Intel and Apple Silicon |
| `cpu` | all | software | libx264 | always works |

`--device auto` (default) runs a **1-second test** for each candidate: a hardware decode
forced through `hwdownload` (so a silent software fallback cannot pass) and a 30-frame test
encode. The first tier that passes is used; runtime decode failures retry on CPU. Tests take
about 2 s in total and are printed by `introkit.py hw` (`--json` for machines).

Forcing: `--device cpu|cuda|qsv|d3d11va|dxva2|vaapi|videotoolbox` (aliases: `nvidia`,
`intel`, `amd`, `apple`/`mac`, `none`); `--hwaccel X` pins only the decoder; env
`INTROKIT_DEVICE` / `INTROKIT_HWACCEL`; project keys `"device"` / `"hwaccel"`. A forced tier
that fails its test prints a warning and runs on CPU.

What acceleration touches â€” and what it never touches:

| Work | Accelerated by | CPU path |
|---|---|---|
| source decode, frame sampling (incl. `frame` / `qc --faces`) | the tier's hwaccel | ffmpeg software, `-threads 0` |
| face detection (YuNet 2023mar, 228 KB ONNX) | never (CPU is faster than the GPU round trip at this size) | OpenCV DNN, half-scale frames |
| review previews | the tier's encoder | libx264 veryfast |
| Whisper large-v3-turbo | CUDA float16 (NVIDIA only) | **int8**, `cpu_threads` = physical cores |
| speaker embeddings (ONNX) | CUDA EP â€º OpenVINO EP â€º DirectML EP â€º CoreML EP (if installed) | CPU EP, intra-op threads = physical cores |
| **final deliverable** | **never** | **libx264 CRF 15 preset medium, always** |

The final encode is the same on every machine, so the deliverable does not depend on the
hardware. faster-whisper/CTranslate2 has **no MPS/Metal backend**: Apple Silicon runs Whisper
on CPU int8 (fast on M-series). Optional EPs: `pip install onnxruntime-openvino` (Intel),
`onnxruntime-directml` (Windows) into the env venv; the stock `onnxruntime` wheel includes
CoreML on macOS.

Measured costs (2026-10-30; the CPU numbers are from an 8-vCPU Linux VM with no GPU,
the GPU numbers from an i7-10700 + RTX 3060 desktop):

| Step | CPU only | NVIDIA |
|---|---|---|
| hardware self-test | ~2 s | ~1.9 s |
| Whisper large-v3-turbo, 3.9 s span (cold, incl. model load) | 11.9 s | â€” |
| Whisper, 23.7 s clip (incl. load) | 6.4 s (3.7Ã— realtime) | â€” |
| Whisper, 45 s clip (incl. load) | 8.6 s (5.2Ã— realtime) | â€” |
| Whisper, full episode | not measured | ~30Ã— realtime |
| diarization, 23.7 s / 45 s of speech | 2.3â€“2.7 s / 3.9 s | (embeddings ran on CPU EP) |
| build, 7.9 s mini project (236 frames) | 37 s | not measured |
| `frame`, 6 clip shots / 619 scanned frames, incl. verify back-off | not measured | 50 s (scan 19 s, plan+verify 30 s) |
| build, 21 s intro (632 frames) with 2 face push-ins | not measured | 100-140 s |
| `qc --faces` on a 21 s final (511 clip frames) | not measured | 17 s |

CPU strategy: transcribe **spans** (`--start/--dur`) instead of whole episodes when a
transcript already exists; never switch to a smaller Whisper model.

## 4. Transcription: `introkit.py transcribe` (scripts/transcribe.py)

```bash
python scripts/introkit.py transcribe --src VIDEO --out work/ [--start S --dur D] \
    [--hotwords "Zcash,Grayscale,Solana"] [--no-vad] [--diarize [--speakers 2] \
    [--enroll guest=S:D --enroll host=FILE.wav]] [--formats json,srt,txt] [--device cpu]
```

Model: **always Whisper large-v3-turbo** (`mobiuslabsgmbh/faster-whisper-large-v3-turbo`
via faster-whisper), float16 on CUDA, int8 elsewhere; a CUDA failure falls back to CPU int8.
Audio decode is ffmpeg â†’ 16 kHz mono float32 (PyAV is not used for audio).

Output `work/<stem>.json` (also `.srt`, `.txt`), times in **source seconds**:

```json
{"meta": {"source": "...", "engine": "faster-whisper large-v3-turbo cpu/int8", "start": 0.0, ...},
 "cues": [{"id": 1, "start": 0.3, "end": 3.58, "speaker": "guest", "text": "...",
           "words": [{"start": 0.3, "end": 0.5, "text": "i", "probability": 0.91, "speaker": "guest"}]}]}
```

`speaker` is `null` unless diarized. SRT lines carry `[speaker]` when known.

When to transcribe: see the table in `SKILL.md`. **Traps**: Whisper suppresses fillers â€”
cross-check against the source SRT (rule 018); word starts can run ~0.2 s early (rule 017);
for QC run VAD on (primary) and VAD off as a second, high-sensitivity pass (rule 025) â€”
VAD off can hallucinate over music, so a VAD-off-only token needs a listen.

## 5. Speakers: `diarize` and `voiceprint` (scripts/speakers.py)

No account, no token, offline after setup. Model: WeSpeaker ResNet34 speaker embedding
(256-d) in ONNX from the ungated repo `altunenes/speaker-diarization-community-1-onnx`
(pinned revision), downloaded into `<env>/models/speaker`. Features: Kaldi-style 80-bin
fbank computed in numpy. Runs on the ONNX tier above (CPU EP by default).

```bash
# label an existing word transcript (skips if it already has speakers; --force to redo)
python scripts/introkit.py diarize --transcript work/ep.json [--src VIDEO] [--speakers 2] \
    [--enroll guest=1520.0:12 --enroll host=4:16]
# speaker-on-camera / identity gate: cosine of each line against references
python scripts/introkit.py voiceprint --src VIDEO --span hook=618.3:3.87 --span q1=420.5:6.1 \
    --enroll guest=1520:12 --enroll host=4:16
```

Method: speech regions from the transcript's word times (energy VAD if none) â†’ 1.5 s windows,
0.5 s hop â†’ embeddings â†’ with `--enroll`: nearest enrolled voice (labels are your names);
without: average-linkage agglomerative clustering on cosine distance, `--speakers N` if known,
else k = 2â€¦6 chosen by silhouette. Each word takes the label of the windows overlapping it;
cues are split where the label changes. An SRT (no word times) is labelled per cue.

When it runs / is skipped:

| Situation | Diarize? |
|---|---|
| transcript already has speaker labels | **skip** (`diarize` refuses without `--force`) |
| no speaker labels, and you need to know who says which candidate line | **yes** â€” `transcribe --diarize` or `diarize --transcript` |
| speaker-on-camera / identity gate (gates 4, rule 010) | **voiceprint** on the selected spans only, always |
| map the hosts at the start (single vs two hosts, rule 020) | **yes**, with `--enroll` clips once you know a guest and a host span |

Measured on a 23.7 s splice of five Zach Pandl EDL lines (guest/host/guest/host/guest):
auto k chose 2 (silhouette 0.38 vs â‰¤0.29), 5 turns in the right order, 58/60 words right
(both errors on the boundary word); with enrollment clips 60/60. Voiceprint cosines: same
speaker 0.70â€“0.82, cross 0.09â€“0.22. On a natural 45 s hostâ†’guest span: k=2, one change,
placed one word late. Decision rule (rule 010): **â‰¥ 0.70 to the speaker's own reference and
clear block structure**; cross-recording similarity is weaker â€” read the matrix, do not
threshold across sessions.

**Optional alternative â€” pyannote.audio** (`pyannote/speaker-diarization-community-1` or
3.1): stronger overlap handling, but it **requires a Hugging Face account and access
token, and accepting the model's terms on its Hugging Face page**, plus torch (~2 GB). It is
not installed by setup. If used: `uv pip install --python <env>/venv pyannote.audio`, set
`HF_TOKEN` yourself (never commit it), run the pipeline, and convert its turns into the
`cues[].speaker` / `words[].speaker` fields above. The default path needs none of this.

## 6. Python libraries (in the env venv)

| Package | Use |
|---|---|
| numpy | audio assembly, ducking (50 ms attack / 300 ms release), limiter, fbank, clustering maths |
| Pillow | the whole graphics layer: word tiles, shadows, scrims, cards (PNG compress_level 1, lossless) |
| opencv-python-headless | YuNet face detector (`cv2.FaceDetectorYN`, bundled ONNX model) for `frame` and `qc --faces`, CPU. The 5.x headless wheel has **no Haar cascades** (`CascadeClassifier` is absent) |
| faster-whisper + ctranslate2 | ASR |
| onnxruntime | speaker embeddings |
| scipy | average-linkage clustering |
| huggingface-hub | model download into the env folder |
| psutil | physical-core count for thread settings |

Optional extras for zoomed-shot upscaling are **not** in this table: install
`requirements-upscale.txt` (torch) into the same venv and run `introkit fetch-upscale-model`.
`build` then upscales clip shots zoomed above 1.15x (GPU when CUDA is visible, otherwise CPU).
`--no-upscale` skips it. Weights live in `.rollup-intro-env/models/upscale/` and are not
committed.

`ImageFont.truetype(path, size).getbbox(text)` returns the ink box from the top of the layout
box â€” subtract the ascent when placing on a baseline. Older notes use Haar cascades, which fire on
microphones, desk edges and logos (filter to `centre_y < 0.68Â·H` and `width â‰¥ 140`), but the
bundled OpenCV 5 headless build has none. Use YuNet: `assets/models/face_detection_yunet_2023mar.onnx`
(MIT, OpenCV Zoo; see `assets/models/YUNET_LICENSE.txt`). Set `OPENCV_LOG_LEVEL=ERROR` to
silence its DNN warnings.

## 7. Fonts

Bundled in `assets/fonts/` with their OFL licences (Anton, Playfair Display Italic, Barlow
Condensed Bold, from the Google Fonts repository). introkit resolves fonts from the project's
`fonts_dir`, then `assets/fonts/`, then the OS font folders (Windows `%WINDIR%\Fonts` and
`%LOCALAPPDATA%\Microsoft\Windows\Fonts`; macOS `/Library/Fonts`, `~/Library/Fonts`;
Linux `~/.local/share/fonts`, `/usr/share/fonts`).

## 8. Sound sources

The bundled music bed and SFX (`ASSETS.md`) are the default. Generating new ones
(e.g. with ElevenLabs) is optional and needs the user's own account. Trap: ElevenLabs
`text_to_sound_effects` with `output_format=pcm_*` writes **headerless s16le 44.1 kHz
stereo** under any filename; introkit sniffs file headers and treats headerless files as
s16le 44.1 kHz stereo (rule 012).

## 9. Shell hygiene

Every step is a plain executable call, so the same commands run in PowerShell, bash and zsh.
Windows PowerShell: variables are case-insensitive (`$s` clobbers `$S`); `-f` formatting
follows the locale decimal separator; write Python to UTF-8 `.py` files instead of long
`python -c` one-liners. Always review frames at **full width**, never a working crop.

## 10. Stitching the delivery (amended 2026-11-23, rules 036 / 043 / 049)

Stitch with `scripts/stitch.py`, or with **TheStitcher-V2** when `STITCHER_HOME` points at a local install, using `--no-dynamics`,
in the order intro, subscribe clip, full interview. Apply a measured plain gain to the
subscribe clip only, matched to the interview's LUFS. Leave the intro's speech at unity
gain. Stream-copy the interview; conform the short pieces with NVENC. Note a black first
frame on the interview if it has one. Write `<Guest>_New_Intro.mp4` in the guest folder
(a later stitch is `_New_Intro_v2.mp4`).

The portable path, for any other machine, is `scripts/stitch.py`: ffmpeg + ffprobe and the
Python standard library, nothing else — no app to install, no packages, no GPU. Any Python
3.9+ will run it, and it finds ffmpeg the same way `introkit.py` does
(`STITCH_FFMPEG` / `INTROKIT_FFMPEG` → `PATH` → `<env>/tools/ffmpeg/bin`).

```bat
python scripts/stitch.py "INTRO.mp4" "SUBSCRIBE.mp4" "EPISODE.mp4" -o "Episode_New_Intro.mp4"
python scripts/stitch.py INTRO SUBSCRIBE EPISODE --measure      :: loudness only, writes nothing
python scripts/stitch.py INTRO SUBSCRIBE EPISODE -o OUT --dry-run  :: plan only, writes nothing
```

| Option | What it does |
|---|---|
| `--overlap N` | frames of the subscribe clip's tail that run under the episode (default: its **measured** black tail) |
| `--hook-overlap N` | frames of the intro's tail the clip starts over (default: the intro's measured black tail; `0` turns it off) |
| `--level segment` | portable default: measures all three files. For current deliveries, apply the gain to the **subscribe clip only** and leave the intro at unity (rule 049) |
| `--level off` / `--level -14` | no level pass / the old fixed -14 LUFS target |
| `--target-tp -1.0` | true-peak ceiling for the level pass (dBTP) |
| `--strategy mix` / `drop` | `mix` sums the overlap (re-encodes the sound, default); `drop` cuts the clip's sound where its picture cuts (copies it, loses the clip's last `overlap` frames of sound) |
| `--no-duck` | do not duck the clip's tail under the episode's speech |
| `--dynamics` | the operator's Premiere chain. **Off by default** — the episode audio is never changed (RULE 036) |
| `--audio-bitrate` / `--crf` / `--preset` | final AAC (default 192k), re-encode quality (default crf 14 / medium) |
| `--work DIR` | conformed pieces and the join (default `<out folder>/_stitch_work/<out stem>`) |
| `--no-verify` | skip the measured verification. **Do not ship without it** |

How the join works, and why it is built this way:

- **The picture is copied.** Three cuts, each remuxed to `-video_track_timescale 30000` (mixed
  timescales concatenate "successfully" and play at the wrong speed — the intro ships at 1/15360 and
  the episode at 1/30000), then concatenated through **Annex B** (`h264_mp4toannexb`) so each piece
  carries its own SPS/PPS and a piece whose profile differs still decodes.
- **`-frames:v`, never `-t`.** A duration is a time and ffmpeg decides which frame it includes; a
  frame count cannot round.
- **A cut must be a keyframe.** The intro's black tail almost never starts on one, so the hook is
  re-encoded once (crf 14, `-fps_mode passthrough`, IDRs at 0 and at the cut). The delivered intro
  file is never modified.
- **The sound is one timeline.** The intro's sound runs to its real end, the clip's sound starts where
  the intro's *picture* was cut, the episode's sound starts where the clip's *picture* stops, and
  `amix=normalize=0` sums them (the default would drop both by 6 dB in the one second the delivery is
  trying to make continuous). Two sidechain compressors duck the outgoing piece under the incoming
  one.
- **It verifies, and only then renames.** Frame count against the sum of the pieces, picture and
  sound durations against each other, timescale, frame spacing, and decoded-frame checksums of
  sampled frames against the pieces that were joined. A failed check leaves `<out>.part.mp4` and does
  not write the delivery.

Cost on a 16-minute episode: about 4-5 minutes cold (two short re-encodes, the copy join, one AAC
encode, the frame count), and seconds when the conformed pieces are cached in `--work`.


## Output folder (2026-11-03)

**Output location (default):** `<folder of the source video>\intros\`: finals `<name>.mp4` plus `LINE_SETS.md`, `CUTLIST.md`, `QC_REPORT.md`; scratch (sets.json, project/words json, vet cache, qc json, contact sheets, build `work/`) in `intros\work\`. Created if missing. Override: `build --out DIR` (CLI wins), else `out_dir` in sets.json / the project (relative to that file); `assemble --out DIR` moves only the json files. Example: `Shows/Ep 12/Ep 12.mp4` -> `Shows/Ep 12/intros/THE_ROLLUP_<Guest>_ColdOpen_A.mp4`.
Code: `introkit.intros_dir(src)` and `introkit.resolve_out_dir(proj, cli_out, base)` (build);
`sidekit.cmd_assemble` (json into `<intros>/work`). Per-shot `gain_db` / `duck` and the build's
`level shot N` printout: see `references/pipeline.md`.

## Tool-to-stage map

New in 2026-11-02: `assemble` (sets.json -> project + words), `vet` (selection-time checks,
`--write` fixes outs), `frame --layout side` (default), consolidated `qc` (4 gates, cache,
contact sheet). `scripts/sidekit.py` holds them; it needs no extra packages (opencv, numpy,
pillow, faster-whisper are already in the env).
New in 2026-11-12: `signals` (read-only hook shortlist), editorial WARNs + stranger-test script in
`vet`, advisory SPEAKER row in `qc`; all in `scripts/editcheck.py`, same env, no new packages.
New in 2026-11-13: accurate FFmpeg seeks (`scripts/ffseek.py`), richer `probe`, `introkit.py metrics` for Kash/Quinn vs Eric compares. See `references/ffmpeg.md`.


| Stage | Tools |
|---|---|
| probe / teardown | ffprobe, ffmpeg (astats, blackdetect, ebur128); `introkit.py probe` also reports VFR, SAR, start_time, A/V skew |
| transcript | `introkit.py transcribe` (Whisper large-v3-turbo) |
| identity / hosts | `introkit.py voiceprint`, `diarize` |
| framing | `introkit.py frame` (ffmpeg decode on the hwaccel tier â†’ YuNet on CPU), full-frame review |
| base picture | ffmpeg crop or `perspective` (face push-in)/scale/unsharp/eq/vignette, concat |
| graphics | Pillow, numpy, bundled fonts and logo |
| audio | numpy, bundled music/SFX, ffmpeg ebur128 |
| mux / deliverable | NVIDIA: h264_nvenc (rule 049); otherwise ffmpeg libx264 CRF 15 + AAC |
| stitch delivery | `scripts/stitch.py` (section 10), or TheStitcher-V2 when `STITCHER_HOME` is set; always `--no-dynamics` |
| caption proofread | `scripts/glossary.py` (rule 047) |
| QC | ffmpeg ebur128, `introkit.py qc --faces` (YuNet), `transcribe` on the deliverable, numpy |
| metrics | `introkit.py metrics --final F --project P --words W` (cut-edge energy, loudness, size, A/V skew; optional `--asr`) |
| source scan | `introkit.py scan-defects --src F` (`scripts/scan_defects.py`): 10 ms blocks of RMS/peak/clip/DC/ZCR/HF/flatness via one ffmpeg decode; dropouts, clip runs, noise-like bursts, per-30 s zero bins (rule 051) |

## Hype music beds (added 2026-11-04)
- assets/music/rollup_hype_bed_140bpm_1.mp3 and rollup_hype_bed_140bpm_2.mp3: ~30 s, 140 BPM hype instrumentals. Source: ElevenLabs Music v2, generated 2026-11-04. Point a project's `music` key at one to use it; the default is still rollup_music_bed_32s.mp3. bed_2 has denser onsets from the start (no slow build). Both are loud in the first seconds, so check the shot-1 speech/music gap (aim for >= 5 dB) and add a per-shot `duck`/`gain_db` if needed.
- assets/music/rollup_breaking_news_drums_1.mp3: ~30 s breaking-news style bed with dominant pounding stomp/clap drums (~125 BPM), ticking pulse, tense strings, brass stabs and impact hits; ends on a big hit. Source: ElevenLabs Music v2, generated 2026-11-04. Preferred direction for a high-energy cold open. Same caution: check the shot-1 speech/music gap.


## Scanning the source for defects (added 2026-11-24, rule 051)

`introkit.py scan-defects --src "<recording>.mp4" [--start S] [--dur D] [--json defects.json] [-v]`

One ffmpeg decode, never a render. 10 ms blocks; features RMS (L/R), peak, clipped-sample
count, DC, zero-crossing rate, high-band energy (first difference) and spectral flatness.
Reports three interval classes and a per-30 s coverage table:

| class | test | what to do |
|---|---|---|
| dropout | RMS < `--silence-rms` (default 30 = -60.7 dBFS) for >= `--min-silence` (0.5 s) | >= 1 s: cut or donor-splice (30 ms equal-power crossfades *across* the boundary). 0.15-1 s: listen, cut if it lands on a word |
| static / hash | HF lift >= `--hash-db` (6) over the local +-50 s median **and** flatness >= `--flatness` (0.45), >= 0.2 s | repair under intelligible speech (adeclick + 5.5 kHz lowpass, 10 ms splices), cut when it eats words |
| clipping | samples at +-32767 | LISTEN: re-cut the piece with gain, or accept an isolated peak |

Cost: 890 s of audio in ~5 s, memory flat (5 s chunks, 50 s median window). The JSON is in the
splice tool's defect schema (`t0`, `t1`, `reason`) so a repair pass can consume it directly.

Calibration: 12 000 blocks of real speech peak at 0.18 flatness; a documented electrical-hash
burst measured 0.70 — the 0.45 default sits in the empty gap. Validate on a known defect before
trusting a clean report: on Tristan Thompson it finds the one documented 0.94 s dropout
(8:05.19-8:06.19) and no static, which matches that episode's cutlist.

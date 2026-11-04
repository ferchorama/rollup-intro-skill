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
  setup.ps1 / setup.sh     build the env folder
  .rollup-intro-env/       created by setup (git-ignored); or $ROLLUP_INTRO_HOME
    uv/                    uv itself, only if it was not already on PATH
    python/                uv-managed CPython 3.11
    venv/                  all Python packages
    models/                Whisper large-v3-turbo (~1.6 GB) + speaker model (~27 MB)
    hf/  cache/            Hugging Face + uv caches (kept inside the folder)
    tools/ffmpeg/          portable ffmpeg, only if none was found
```

| OS | Command (run from the skill folder) |
|---|---|
| Windows (PowerShell 5.1 or 7) | `powershell -ExecutionPolicy Bypass -File .\setup.ps1` |
| macOS (Intel or Apple Silicon) / Linux | `bash ./setup.sh` |

Options: `-EnvHome D:\x` / `--home /x` (or set `ROLLUP_INTRO_HOME`) to put the folder
elsewhere; `-Cuda yes|no|auto` / `--cuda`; `-Ffmpeg portable|skip|auto` / `--ffmpeg`;
`-SkipModel` / `--skip-model` (download models later with `introkit.py fetch-model`);
`-Unlocked` / `--unlocked` to install from the minimum-version files instead of the locks.

What setup does: uses `uv` if on PATH, else installs it into `<env>/uv` without touching
your PATH; installs a uv-managed Python 3.11 into `<env>/python`; creates `<env>/venv`;
installs `requirements.lock.txt` (plus `requirements-cuda.lock.txt` when `nvidia-smi -L`
works on Windows/Linux: CUDA 12 cuBLAS + cuDNN 9 wheels, no system CUDA needed); finds or
installs ffmpeg; downloads the models; prints `introkit.py env` and `introkit.py hw`.
After setup the skill runs **offline**. Uninstall = delete the env folder.

`introkit.py` can be launched with any Python 3.9+: it re-executes inside `<env>/venv`
(`INTROKIT_NO_REEXEC=1` disables that). It sets `HF_HOME` inside the env folder and, on
Windows/Linux with the CUDA wheels, makes their DLLs/.so files loadable.

## 2. ffmpeg (per OS)

introkit looks for ffmpeg in this order: `--ffmpeg PATH`, `INTROKIT_FFMPEG`, `PATH`,
`<env>/tools/ffmpeg/bin`. Any build â‰¥ 6 with libx264 works (tested: 8.0.1 Windows gyan
build, 7.1.5 Debian).

| OS | Install |
|---|---|
| Windows | `winget install Gyan.FFmpeg`, or let `setup.ps1` unpack the BtbN win64-gpl build into `<env>/tools/ffmpeg` (`-Ffmpeg portable`) |
| macOS | `brew install ffmpeg` (Homebrew, Intel or Apple Silicon; `setup.sh` runs it if brew exists) |
| Linux | `sudo apt install ffmpeg` (or dnf/pacman), or `setup.sh --ffmpeg portable` for the BtbN static build (x86_64 / arm64) |

| Job | Invocation |
|---|---|
| Facts | `ffprobe -v error -show_streams -show_format -of json FILE` |
| Exact frame count | `ffprobe -v error -select_streams v:0 -count_frames -show_entries stream=nb_read_frames -of csv=p=0 FILE` |
| Loudness | `ffmpeg -i F -af ebur128=peak=true:framelog=quiet -f null -` |
| Dips in the reference | `-vf "blackdetect=d=0.08:pix_th=0.10"` |
| Silence / word tails | `-af "silencedetect=noise=-45dB:d=0.25"` |
| Channel identity | `-af astats=metadata=0:measure_perchannel=Peak_level+RMS_level:measure_overall=none` |
| Frame-accurate still | `-i FILE -ss T -frames:v 1` (ss **after** -i) |
| Delivery (introkit does this) | `-c:v libx264 -preset medium -crf 15 -profile:v high -pix_fmt yuv420p -r 30 -g 60 -movflags +faststart -threads 0 -c:a aac -b:a 320k -ar 48000 -ac 2` |

**Traps**: `-ss` before `-i` is a fast seek â€” render only, never verify with it.
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

## Output folder (2026-11-03)

**Output location (default since 2026-11-03):** `<folder of the source video>\intros\`: finals `<name>.mp4` plus `LINE_SETS.md`, `CUTLIST.md`, `QC_REPORT.md`; scratch (sets.json, project/words json, vet cache, qc json, contact sheets, build `work/`) in `intros\work\`. Created if missing. Override: `build --out DIR` (CLI wins), else `out_dir` in sets.json / the project (relative to that file); `assemble --out DIR` moves only the json files. Example: `H:\Show\Ep 12\Ep 12.mp4` -> `H:\Show\Ep 12\intros\THE_ROLLUP_<Guest>_ColdOpen_A.mp4`.
Code: `introkit.intros_dir(src)` and `introkit.resolve_out_dir(proj, cli_out, base)` (build);
`sidekit.cmd_assemble` (json into `<intros>/work`). Per-shot `gain_db` / `duck` and the build's
`level shot N` printout: see `references/pipeline.md`.

## Tool-to-stage map

New in 2026-11-02: `assemble` (sets.json -> project + words), `vet` (selection-time checks,
`--write` fixes outs), `frame --layout side` (default), consolidated `qc` (4 gates, cache,
contact sheet). `scripts/sidekit.py` holds them; it needs no extra packages (opencv, numpy,
pillow, faster-whisper are already in the env).


| Stage | Tools |
|---|---|
| probe / teardown | ffprobe, ffmpeg (astats, blackdetect, ebur128) |
| transcript | `introkit.py transcribe` (Whisper large-v3-turbo) |
| identity / hosts | `introkit.py voiceprint`, `diarize` |
| framing | `introkit.py frame` (ffmpeg decode on the hwaccel tier â†’ YuNet on CPU), full-frame review |
| base picture | ffmpeg crop or `perspective` (face push-in)/scale/unsharp/eq/vignette, concat |
| graphics | Pillow, numpy, bundled fonts and logo |
| audio | numpy, bundled music/SFX, ffmpeg ebur128 |
| mux / deliverable | ffmpeg libx264 CRF 15 + AAC |
| QC | ffmpeg ebur128, `introkit.py qc --faces` (YuNet), `transcribe` on the deliverable, numpy |

## Hype music beds (added 2026-11-04)
- assets/music/rollup_hype_bed_140bpm_1.mp3 and rollup_hype_bed_140bpm_2.mp3: ~30 s, 140 BPM hype instrumentals. Source: ElevenLabs Music v2, generated 2026-11-04. Point a project's `music` key at one to use it; the default is still rollup_music_bed_32s.mp3. bed_2 has denser onsets from the start (no slow build). Both are loud in the first seconds, so check the shot-1 speech/music gap (aim for >= 5 dB) and add a per-shot `duck`/`gain_db` if needed.
- assets/music/rollup_breaking_news_drums_1.mp3: ~30 s breaking-news style bed with dominant pounding stomp/clap drums (~125 BPM), ticking pulse, tense strings, brass stabs and impact hits; ends on a big hit. Source: ElevenLabs Music v2, generated 2026-11-04. Fernando's preferred direction for a high-energy cold open. Same caution: check the shot-1 speech/music gap.


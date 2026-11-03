# Changelog

## 2026-11-03 (output folder; per-shot audio level)
- **Default output folder is now `<folder of the source video>\intros\`** (user request).
  `build --out` defaults to the project `out_dir`, else `<src folder>\intros` (created if
  missing; build scratch in `intros\work`); was `render/`. `assemble` writes the project /
  words json to `<src folder>\intros\work` (or `<out_dir>\work`; `--out` still wins), writes an
  absolute `src`, and passes a sets.json `out_dir` on to the project. New helpers
  `intros_dir()` / `resolve_out_dir()` in introkit.py. LINE_SETS / CUTLIST / QC_REPORT go next to
  the finals. Existing deliverables were not moved.
- **Per-shot audio:** shot keys `gain_db` (speech gain after the dialogue norm) and `duck`
  (music duck depth, default 0.72); build prints speech-vs-music level per clip.
  Used for Diego Perez de Ayala B2 (quiet button).

## 2026-11-02 (session 2: approved Tom / Kristen layout ported; QC redesign)
- New `scripts/sidekit.py`; `introkit.py` hooks into it. Old projects build unchanged
  (crop keys are read as before; new keys are optional).
- `frame --layout side` (default): one speaker, face on a third, look-room, zoom by face size
  (cap 2.45), wide-shot punch-in, other people cropped or matted, `maxw` from the face edge,
  vertical-movement back-off. `layout: "classic"` keeps the old planner.
- Build: setsar=1 + bt709 on every segment; matte composite; first word of each caption page
  capitalised (`caption_capitalise`); speech fade default 25 ms (`speech_fade_ms`); automatic
  end audio (music fade from the last word to the sting, duck held, sting alone);
  `sfx.<key>_max_s`; WARN on a short sting tail; AAC remaster to TP <= -1.1; h264_metadata
  SAR/colour tags; faststart. Replaces the LOCALPATCH copies (kit2).
- `assemble` (sets.json -> project + words, script spelling wins), `vet` (selection-time
  checks, cached; `--write` = last-word out-point fix at the acoustic end + final black / sting).
- `qc` redesigned: four gates (FORMAT, AUDIO, FRAMING, SPEECH), one PASS/FAIL table with
  shot / time / fix, per-shot cache, contact sheet + waveform; `allow_top_touch` opt-in;
  continuous joins not graded. Removed as gates: multi-model / full-file / windowed ASR,
  every-frame faces (`--classic-faces` diagnostic), voiceprint (diagnostic).

## 2026-11-02 (face-aimed zoom + face QC)
- **`introkit.py frame`** (new) frames the faces in each clip shot.
  - It runs YuNet on every source frame of the shot, plus 0.3 s on each side. On a miss it
    runs a low-score full-resolution ROI pass, and it tracks the nearest face.
  - From the union face box plus margins (hair +30 %, chin +10 %, sides +15 %) it sets the
    zoom to min(face at 34 % of height, the largest crop that fits, 2.0×). It keeps the
    editor's composition, the title text column and 60 px of headroom.
  - It plans a 6 % face-anchored push-in (eased in-out). The push backs off to 0 with a
    note when it does not fit, and a same-speaker jump cut keeps a step of at least 6 %.
  - It re-detects at delivered scale and lowers the zoom in 2 % steps until the qc
    margins + 2 % hold.
  - It writes `crop_*`, `push`, `punch`, `push_ease` and `framing` into the project, plus
    `<out>.faces.json` and a `<out>.frame.txt` report. Options include `--write`,
    `--shots`, `--no-push` and `--no-verify`, and per-shot `frame_lock` / `face_roi`.
  - Allowed area: the editor's crop by default (never widened); project/shot `avoid`
    rects or `frame_bounds` open it up. The crop dodges avoid rects by the least move and
    flags `FURNITURE` / `TEXT` (caption column over the bare face box) as WARN.
  - `--shots` re-frames only the listed shots from the INPUT project; other shots keep
    the input's crops (frame all shots in one run for a full framed project).
- **`qc --faces --project P`** (new) checks every clip frame of the final file. It FAILs
  if head+25 % < 0, chin < 5 %, sides < 5 %, or no face is found (YuNet, then the
  low-score pass, then the sidecar box). Margins are judged on a 5-frame median, with the
  raw minimum also printed. It prints a per-shot table, writes `qc.json → faces`, and
  exits 1 on a FAIL.
- **Base stage:**
  - Per-shot `crop_w` / `crop_h` / `crop_y` (crop_x defaults to 0), falling back to the
    global `crop`. Old projects render byte-identically.
  - A `push` key renders a sub-pixel `perspective` crop lerp (cosine ease) instead of the
    integer-stepping `zoompan`.
  - The legacy `punch` without `push` still renders the old centre zoom, with a warning.
- **Bundled model:** `assets/models/face_detection_yunet_2023mar.onnx` (OpenCV Zoo, MIT,
  sha256 8f2383e4…, `YUNET_LICENSE.txt`). It runs on CPU and needs no new dependency (it
  uses opencv-python-headless, which is already installed).
- Docs updated: SKILL.md (framing step, motion contract, QC checklist), references/pipeline.md
  stage 4a, references/qc.md 3b, TOOLS.md, ASSETS.md, LESSONS_LEARNED.md (B3b; C6 done),
  README.md, scripts/project.example.json.

## 2026-10-30 (session lessons, docs only)
- Added `LESSONS_LEARNED.md` (editorial and technical lessons from a full delivery, SAR fix,
  post-AAC true peak, sting length, music fade-out method, kit TODOs) and a pointer plus QC
  checklist additions in `SKILL.md`. No code or asset changes.

## 2026-10-30
- **Runs without a GPU.** Hardware tiers cuda → qsv → d3d11va/dxva2 → vaapi → videotoolbox →
  cpu, each self-tested (1 s decode + encode) with silent fallback; `--device`, `--hwaccel`,
  `INTROKIT_DEVICE`, project keys. `introkit.py hw` reports the table.
- **Deliverable fixed at libx264 CRF 15 preset medium** on every tier (was CRF 16 in docs).
- **macOS (Intel + Apple Silicon) and Linux** support: pathlib everywhere, OS font folders,
  VideoToolbox previews, Whisper CPU int8 on Apple Silicon, CoreML EP when available.
- **One-folder environment** with uv: `setup.ps1` / `setup.sh`, `.rollup-intro-env/` (or
  `ROLLUP_INTRO_HOME`), pinned locks, optional CUDA wheels, optional portable ffmpeg,
  models downloaded into the folder; introkit re-executes inside it.
- **Self-contained transcription**: `scripts/transcribe.py`, always Whisper large-v3-turbo
  (float16 CUDA / int8 CPU), word timestamps, JSON/SRT/TXT; no external transcriber app.
- **Diarization and voiceprints**: `scripts/speakers.py`, `introkit.py diarize`,
  `voiceprint`, `transcribe --diarize`; ONNX WeSpeaker embeddings, clustering or enrollment,
  no account/token; pyannote documented as an optional alternative.
- **Bundled assets**: music bed, three SFX, logo, fonts + OFL licences (`ASSETS.md`).
- **Fixes**: numeric `sfx.*_at`; binary PCM decode; relative `--out`; relative project paths
  resolve against the project file; header-sniffed SFX format; ffmpeg audio decode for
  Whisper (PyAV 19 incompatibility); overlay PNGs written lossless with compress_level 1 on a
  thread pool (build 141 s → 37 s on the test VM, bit-identical output).
- **Rules 016–030** merged (portrait choice, per-cut ASR in-points, fillers, per-source crop,
  single host, out-points from the waveform, hook compliance, type sizes, timing targets).
- **Type sizes** raised: Playfair 110–130 px, Anton cap ≈133–171 px+, Barlow ≈1.3×; phrases
  are paged and wrap at word boundaries.

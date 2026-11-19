# Changelog

## 2026-11-19 (the stitcher is part of the skill, and it levels the delivery)
Stitching a delivery — hook + subscribe bumper + full episode — used to be an external desktop app,
so the recipe only existed on one machine. It is now **`scripts/stitch.py` in this skill**: ffmpeg
and ffprobe plus the standard library, no app, no packages, no GPU, any OS (RULE 030, RULE 036).
- **Same join, measured the same way:** black heads/tails measured frame by frame (`signalstats`),
  keyframes read from packet timestamps, three copied picture cuts remuxed to a 1/30000 timescale and
  concatenated through Annex B, `-frames:v` never `-t`, and a hook re-encoded once (crf 14) when its
  cut frame is not a keyframe.
- **The sound is one timeline:** the intro's sound to its real end, the clip's from where the intro's
  picture was cut, the episode's from where the clip's picture stops, summed with
  `amix=normalize=0` and ducked by two sidechain compressors. The episode's audio is never
  processed (the dynamics chain is `--dynamics`, off by default, RULE 036).
- **The volume step is now measured, not assumed (RULE 043).** `--level segment` (default) measures
  integrated loudness and true peak of all three files and gain-matches the hook and the subscribe
  clip to the episode, gain only, capped so true peak stays <= -1.0 dBTP. Measured on the Farokh
  delivery: intro -14.15 LUFS -> -13.43 (+0.72 dB), subscribe clip -14.48 -> -13.44 (+1.04 dB, the
  peak ceiling binding), episode untouched at -13.43; finished file -13.44 LUFS, -1.02 dBTP.
  `--measure` prints those numbers without writing anything; `--level off` disables the pass.
- **It verifies before it renames:** frames against the sum of the pieces, picture and sound
  durations against each other, timescale, frame spacing, and decoded-frame checksums of sampled
  frames against the pieces joined. A failure leaves `<out>.part.mp4` and does not write the
  delivery.
- Docs: `SKILL.md` step 10, `TOOLS.md` section 10, `RULES.md` 036 (rewritten) and 043 (new),
  `references/pipeline.md`. No reference to a separate stitcher application remains.

## 2026-11-18 (self-bootstrapping setup)
The skill now installs everything a new computer needs, with no prior knowledge of the
requirements. `introkit setup` (or `setup.ps1` / `setup.sh`) is the single entry point;
any other introkit command runs setup automatically the first time something is missing.
Setup installs uv if needed, creates `.rollup-intro-env`, installs all requirements
including the upscale extras, detects an NVIDIA GPU and installs a matching PyTorch
CUDA wheel (CPU fallback otherwise), finds or installs ffmpeg/ffprobe, and fetches the
Whisper large-v3-turbo model and Real-ESRGAN weights. Already-present pieces are skipped
and a summary is printed. After setup, zoomed-shot upscaling is on by default
(`introkit upscale` self-checks; `--no-upscale` still skips it).

## 2026-11-18 (upscale, structure, talking face, B5 endings, stutter energy, 4 s minimum)
Lessons from the Tristan Thompson intros (including B5 / `_UP`) and Lord Chris Holmes A2 / v3.
All new code is ours (the Tristan / Chris Holmes work-folder methods, generalised). The four
QC pass/fail gates (FORMAT, AUDIO, FRAMING, SPEECH) are unchanged; the new checks only WARN.
- **Real-ESRGAN upscale of zoomed shots** (`scripts/upscale.py`): crop the zoomed source region,
  upscale 4x with realesr-general-x4v3 blended with wdn-x4v3 at 0.5 denoise, then redo push,
  grade and captions. Applied when crop zoom > 1.15x. Default at `build` if optional extras
  (`requirements-upscale.txt`, torch) and weights (`introkit fetch-upscale-model`) are present;
  CPU float32 fallback when there is no CUDA; `--upscale` requires it, `--no-upscale` skips it.
  Audio is stream-copied and verified by MD5 hash after mux.
- **Beat structure option:** `assemble --structure guest,andy,robbie,guest` or `structure` in
  sets.json. The guest always opens. A mismatch WARNs; strict alternation is still a refuse.
- **Right host on screen:** with two faces and no explicit `speakers.*.pick`, `frame` picks the
  face whose mouth motion follows the speech, and keeps it on the side opposite the captions.
  Mid-shot camera cuts and a face sitting near the centre are WARNs (split the beat).
- **B5 word ending:** when the next word follows within ~20 ms, `measure_end` / `vet --write`
  keep full volume through the last word's real tail, fade the next word in 30 ms, and fill
  with matching room tone from the same recording (`shot.tail_env`, applied in `stage_audio`).
  `edges` WARNs `TIGHT` when that envelope is missing (does not fail the run by itself).
- **Collapsed-repeat stutter:** when ASR hears one copy of a repeated phrase, `vet` detects the
  two energy bursts and `--write` moves the in-point into the pause (Chris Holmes A2).
- **4.0 s minimum:** every speaker beat must run >= 4.0 s (host lines still 4-6 s). `signals
  --min-dur` defaults to 4.0 and extends short candidates to a natural word boundary (never
  silence). `assemble` / `vet` / `edges` WARN on short beats.
- **TheStitcher:** RULE 036 now also documents a gain-only subscribe copy at -14 LUFS, TP <= -1,
  always with `--no-dynamics`.
- Smoke: `python scripts/test_smoke.py`.

## 2026-11-16 (B2 house style becomes the default)
Reference: `K:\Lior Messika\intros\THE_ROLLUP_Lior_Messika_ColdOpen_B2_original.mp4` — Fernando: "from now on you will use the same techniques you applied for that one".
- **introkit `stage_audio`:** bed RMS target -26 dB (was -20, project `music_rms_db`); per-shot duck depth
  with 180 ms cosine ramps (was a hard step); shots without `duck` are auto-solved to a 10.6 dB speech-over-bed
  gap and 13 dB under the final guest beat (`duck_gap_db`, `duck_final_gap_db`, `duck_auto`, `duck_ramp_ms`);
  WARN when a shot's gap is < 9.5 dB. Replaces the per-project `_build.py` wrapper and `tune_duck.py`.
- **introkit `build --audio-only` / `--dry-run`:** prints bed / duck / per-shot levels, renders nothing.
- **`end_audio_plan`:** no-logo projects get the bed fade from the last word's end to total - 50 ms and
  the duck hold from the last clip's start (replaces `patch_nologo.py`).
- **sidekit `assemble`:** default end = no logo / no sting / 1 s black tail / `_original` name
  (`end_card: true` for the old end); up to 10 pieces per beat (was 3, `abc`); captions from padded-span
  ASR (0.5 s / 0.8 s) through `fit_times` + `enforce_min_vis` (t < e <= dur, never a negative e, tail words
  redistributed, >= 150 ms per word, no flash at page breaks); WARN when the in cuts into the previous
  word's leftover.
- **new `introkit retime`** (was `_retime.py`) and **`introkit edges`** (was `final_qc.py`: edge ASR on the
  final mp4, FIRST / LAST / FILLER / DIFF / FADE).
- **`vet --write`:** the end fade never starts before the acoustic end of the last word (fade shrinks to
  8 ms instead).
- Docs: SKILL.md "B2 house style", RULES 031-036 (viral hooks, B2 default, fade rule, split lines, build once
  then hand off, TheStitcher-V2 `--no-dynamics`), line-selection viral rule, pipeline / qc sections.
- Verified: B2 dry run with its framed project prints exactly the `build_B2.log` bed / duck / gap lines;
  with the ducks removed the auto-solver lands at 10.4-10.6 dB (final line 12.9-13.0 dB), and `retime` on
  B2 gives VIS_OK for all 83 words.

## 2026-11-13 (fix extract_pcm atrim after preroll)
- **Bug:** \fseek.extract_pcm\ used the picture recipe (\-ss (T-1) -i SRC -ss 1 -t D\) plus \trim=end=D\. After output \-ss\, PTS still start at the 1 s preroll, so \trim=end=D\ kept only \D-1\ seconds and dropped the out-point. \et\ / \qc\ / \metrics\ energy (head_leak_db, post_out_db, end_margin) and windowed \	ranscribe\ reads were short; \stage_audio\ also used \extract_pcm\, so built dialogue was missing the last ~1 s of every shot (padded with silence under the music bed).
- **Fix:** audio extracts now input-seek the preroll, then \trim=start=m:end=m+D,asetpts=PTS-STARTPTS\ (no output \-ss\). Windows are exactly D long. Picture seeks (\stage_base\, \rame\ scans) were already duration-correct (no atrim) and are unchanged.
- Eric A/B/C built before this fix need a rebuild for the dialogue; Kash / Quinn baselines were not rebuilt.

## 2026-11-13 (FFmpeg accurate seeks + delivery metrics, used by the pipeline)
Informed by a review of [kajisho5/ffmpeg-skill](https://github.com/kajisho5/ffmpeg-skill)
v3.8.4 (MIT License, Copyright (c) 2026 kajisho5). We did not vendor its 42 scripts; the
code and wording here are ours (`scripts/ffseek.py`, wired into introkit / sidekit /
transcribe). Accurate preroll seeks are the only seek path (the old single input `-ss`
was removed). Handbook and the Kash/Quinn vs Eric metric protocol: `references/ffmpeg.md`.
- **Accurate cuts (new default):** `build` picture and dialogue, `frame` face scans, `vet`/`qc`
  energy reads, and windowed `transcribe` seek 1 s early then drop that margin on the output
  side, with `atrim` bounding audio length. Fixes input `-ss` landing late on B-frame MP4
  (a known cause of clipped last words and stray next words).
- **probe:** also reports VFR suspicion, SAR, start_time, colour tags, A/V start skew.
- **metrics (new):** `introkit.py metrics --final F --project P --words W` writes cut-edge
  energy, loudness (I, LRA, TP, I_err, tp_over), size, duration, A/V skew. Optional `--asr`
  or `--qc-json`. `build` writes `work/build_metrics.json` (includes `build_elapsed_s`).
- **qc speech:** also records first-word got/p/heard (measurement only; no new FAIL).

## 2026-11-13 (intros always open with the guest)
- **Hard rule:** intros always open with the guest, never with a host. Documented in SKILL.md
  and references/line-selection.md. `assemble` WARNs (does not refuse) if beat 1 is not the guest.

## 2026-11-12 (editorial checks: openers, cut boundary, stranger test, signals, speaker row)
All new code is ours (`scripts/editcheck.py`); ideas and thresholds were informed by a review of
SupoClip (AGPL-3.0), but no code or prompt text was copied. No new dependencies, no accounts.
- **vet:** editorial WARNs per beat: weak opener / bare pronoun / back-reference on the first
  piece; boundary after the last word (speaker change 0.9 / sentence end 0.75-0.95 / pause 0.55 /
  breath 0.4 / mid-clause 0.1, new column `cut after`) with a WARN on mid-sentence ends and
  conjunction / function-word ends. Cached in vet_cache.json; never moves an out-point; never a
  FAIL. New flags `--transcript` (speaker change) and `--no-edit-checks`. vet now ends with the
  plain SCRIPT for the stranger test.
- **signals (new, read-only):** `introkit.py signals --transcript T.json --speaker guest` ranks ~12
  guest lines (energy, pause before, question, power/contrast words, numbers, length; hook-type
  hint; flags). Speakers from transcript labels or `--enroll` voiceprints.
- **qc:** advisory SPEAKER row (mouth motion vs speech, vs other faces in the source): ok /
  ambiguous / WARN, never a FAIL; cached per shot; `--no-speaker-check`.
- **Docs:** line-selection.md (signals, openers, stop rules, scoring checklist with hook types,
  stranger test), SKILL.md workflow, qc.md, pipeline.md, LESSONS_LEARNED F1-F3.

## 2026-11-03 (session 2: strict speaker alternation; later last-word ends)
- **Strict alternation (user rule):** no two consecutive beats by the same speaker; with one host
  + guest it is guest / host / guest / host / guest. `assemble` now refuses a set that breaks it.
  SKILL.md, references/line-selection.md, references/pipeline.md updated.
- **Last-word ends err late:** `sidekit.measure_end` (used by vet --write and qc) needs 60 ms at the
  floor to end a word (was 40 ms; plosive closures read as ends), tail 150 ms (was 80), Whisper's
  next-word start is no longer a hard cap, coarticulated words cut at the deepest dip. Cause:
  Armani Ferrante B clipped 'attack' and 'three'. Backup: scripts/sidekit.py.bak_pre_lateend.

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

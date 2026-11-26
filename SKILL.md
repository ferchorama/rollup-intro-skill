---
name: rollup-intro
description: Builds a 30-34 second broadcast cold open for The Rollup from a raw episode or
  multi-camera session file, matching a supplied reference render. Use when asked to make or
  fix an intro, cold open, hype cut, or hook for The Rollup; to replicate a reference intro's
  pacing, type and sound; or to QC a finished intro against loudness, on-camera-speaker,
  legibility and script gates.
---

# The Rollup — cold open builder

Turns one long session recording into a ~32 s branded open: guest hook → host question →
guest answer → second host question → title card → guest button → logo, with word-synced
kinetic type and a music bed. Runs on Windows, macOS (Intel and Apple Silicon) and Linux,
**CPU-only by default**; a GPU only makes some steps faster (see "Hardware").

## Environment (one folder, set up once per machine)

On a new computer you need **no prior knowledge of the dependencies**. The skill installs
everything itself into `<skill>/.rollup-intro-env` (or `$ROLLUP_INTRO_HOME`). Delete that
folder to uninstall. Details: `TOOLS.md`.

**Do this first** (pick one; all are equivalent):

| OS | Command (run from the skill folder) |
|---|---|
| Windows | `powershell -ExecutionPolicy Bypass -File .\setup.ps1` |
| macOS / Linux | `bash ./setup.sh` |
| Any OS, if some Python 3.9+ is already there | `python scripts/introkit.py setup` |

Setup is one-time and can take several minutes (it downloads Python, packages, a ~1.6 GB
Whisper model, PyTorch, and Real-ESRGAN weights). It:

- installs **uv** if it is missing
- creates the skill venv with a uv-managed Python 3.11 (no system Python is required or touched)
- installs every Python package, including the upscale extras
- detects an NVIDIA GPU and installs the matching **PyTorch CUDA** wheel, or the CPU build if
  there is no GPU (or the CUDA wheel cannot be used)
- finds **ffmpeg / ffprobe**, or installs them (portable copy into the env folder; winget as a
  Windows fallback; Homebrew on macOS; apt on Linux)
- downloads **Whisper large-v3-turbo** and the **Real-ESRGAN** weights
- skips anything already present and prints a summary

If you skip setup and run any other `introkit` command, it runs setup automatically the first
time something is missing (`INTROKIT_NO_SETUP=1` disables that). After setup, zoomed-shot
upscaling is **on by default** (`--no-upscale` to skip). Confirm with `introkit.py upscale`.
`introkit.py` then re-runs itself inside the venv, so any Python can launch it.

The skill ships its own **music bed, SFX, logo and fonts** in `assets/` (`ASSETS.md`).
Projects use them unless they override `music`, `sfx.*`, `logo` or `fonts_dir`.

## When transcription is needed

| Situation | Transcribe? |
|---|---|
| A transcript (`.srt`/`.json`/`.txt`) sits next to the video | **No full-file pass.** Use it to find lines. |
| No transcript next to the video | **Yes**, full file: `introkit.py transcribe --src VIDEO` |
| Selected lines need word timings / in-point and out-point checks (rules 017, 018) | **Yes, spans only**: `transcribe --start S --dur D` per selected line (seconds, cheap on CPU) |
| QC gate 6: diff the delivered file against the script | **Yes, always**: `transcribe --src FINAL.mp4` (VAD on, then off — rule 025) |
| You need to know **who** says each candidate line and the transcript has no speaker labels | **Diarize**: `transcribe --diarize` or `diarize --transcript T.json` (skipped automatically if the transcript already has speakers) |
| Gate 4 / host mapping | **Voiceprint** the selected spans: `introkit.py voiceprint --span ... --enroll guest=.. --enroll host=..` |

Transcription is `scripts/transcribe.py`; speakers are `scripts/speakers.py` (ONNX speaker
embeddings, no account or token, offline). Details and measured accuracy: `TOOLS.md` §4–5.

The model is always Whisper **large-v3-turbo**: float16 on NVIDIA CUDA, otherwise int8 on
CPU (Apple Silicon included; CTranslate2 has no MPS). Never swap in a smaller model; use
spans to save time instead.

## Setup (run this first)

The agent installs the toolchain itself into this folder. Do not ask the user to
install Python, uv, ffmpeg, or models.

```bash
# Windows
powershell -ExecutionPolicy Bypass -File .\setup.ps1

# macOS / Linux
./setup.sh
```

Setup is idempotent. Everything lands in `.rollup-intro-env/` (or `$ROLLUP_INTRO_HOME`):
uv, a managed Python, the venv, portable ffmpeg, Whisper and Real-ESRGAN weights.
No admin rights, no system PATH changes, no registry edits. Delete that folder to
uninstall. Involve the user only when something strictly requires them, and say
exactly why (an API key from `.env.example`, an admin prompt if no user-space
option exists, or a license acceptance).

Any later `introkit` command also runs setup automatically the first time a piece
is missing.

## Hardware

`--device auto` (default) tests each tier with a 1 s decode/encode and takes the first that
works: **cuda → qsv (Intel Quick Sync) → d3d11va/dxva2 (Windows: AMD & others, AMF encoder) →
vaapi (Linux) → videotoolbox (macOS) → cpu**. Acceleration is used for source decode, Whisper, the speaker-embedding model, and — when
an NVIDIA GPU is available — the delivered encode (`h264_nvenc`). On a machine with no
NVIDIA GPU the portable deliverable remains **libx264 CRF 15, preset medium**. Prefer
CUDA decode, h264_nvenc and GPU ASR whenever they are available (rule 049). Force a
path with `--device cpu|cuda|qsv|d3d11va|dxva2|vaapi|videotoolbox`, pin decode only with
`--hwaccel`, or set `INTROKIT_DEVICE`. `introkit.py hw` prints the table.

## Inputs the agent must obtain

| Input | Why it is required | If missing |
|---|---|---|
| `--src` one long recording (episode, ISO, or programme cut) | the only footage | cannot start |
| `--ref` a previous intro render | you match its pacing and levels, not your taste | use the timing targets below |
| guest name + title | title card, chyron cross-check | read the chyron in the source |
| output dir | everything lands here | default `./out` |

Do not ask for the edit decision list — derive it. Do not ask for the transcript — use the one
next to the video or produce it.

## Source hygiene (do this before you select anything)

A cold open is cut from an event recording, and event recordings arrive with holes. Four of the
27 episodes under `K:` carry a source-audio repair, plus the 2 h 32 m master in
`Raw\Audio Corrected`; every one of those sessions re-derived the same scan by hand, which is
why it is now a tool (rule 051).

```
introkit.py scan-defects --src "<recording>.mp4" [--json defects.json]
# or: python scripts/scan_defects.py --src "<recording>.mp4" -v
```

- **Dropouts** (RMS < -60.7 dBFS for >= 0.5 s): a hole >= 1 s has already destroyed the speech
  inside it — cut it or donor-splice it, never try to fade it away.
- **Static / hash** (broadband: HF lift *and* spectral flatness >= 0.45): repair under
  intelligible speech (adeclick + 5.5 kHz lowpass, 10 ms crossfades), cut when it swallows
  words.
- **Clipping** (samples at full scale): a short run is a listening item; a long one is a reason
  to re-cut that piece with gain.
- Read the per-30 s zero-sample bins. A file's average RMS says nothing about a 0.9 s hole.
- An 890 s recording scans in 5 s, so there is no excuse for not doing it.

## Non-negotiable gates

1. **-14.0 LUFS ±0.5 integrated, true peak ≤ -1.0 dBTP on the delivered file**, measured with
   `ffmpeg -af ebur128`, not estimated. Both are done by the master knee (-2.0 dBFS) — over 71
   delivered files the measured spread is I -14.1..-14.0 and TP -2.10..-1.20, none over -1.0.
   Never add a limiter and never trim speech for peak: if TP is high the knee is misconfigured,
   not the speech. (A batch that shipped at 0.0 dBTP had to be re-done; that is the failure this replaces.)
2. **Every spoken word on screen** during its utterance, mixed faces inside one sentence.
3. **No type over a face.** Verified per frame against a measured face box.
4. **The person on camera is the person speaking** at both the in-point and the out-point of
   every cut. Map camera cuts and hosts once with a voiceprint (diagnostic, not a build gate).
5. **No filler, no hesitation, no gap > 0.35 s inside a line, and no stray word after the
   out-point** — out-points come from the waveform, spectrogram and energy decay, never from
   an SRT timestamp or the ASR word end (rules 027, 044). Err late; listen to the final audio.
   ASR cannot confirm an ending (it fills in partial words). This is the defect that actually
   ships: `last word clipped` is the most repeated QC failure (31 of 71 runs, rule 054), so
   `metrics` must show `end_margin_ms >= 40` on every shot before you report the file.
6. **Re-transcribe the deliverable per shot** (`qc` speech gate) and diff it against the script.
   Also run `scripts/glossary.py` on the on-screen words (rule 047).
7. **Hook compliance.** Every line is a complete sentence with hook value ≥ 4 / 5 (rule 045).
   A hook that states or implies price, returns or performance is flagged and ships only with
   a safer backup line ready (rule 028).

## Running rules (read before cutting)

Read `RULES.md` before selecting lines and again before QC. It is the running list of
editorial and kit rules (true peak measured before AAC, sting truncated by a short end
card, no music fade option, non-square SAR, and the workarounds).

## B2 house style — the default for every intro

**Reference example:** the signed-off B2 house-style open, `THE_ROLLUP_Lior_Messika_ColdOpen_B2_original.mp4` (30.73 s, -14.0 LUFS, TP -1.8). It is the way
every intro is made; its project files are `project_B2.framed.json` +
`words_B2.json` + `build_B2.log`. The kit now does all of it by default (no side scripts):

| Technique | Where it lives now | B2 numbers |
|---|---|---|
| Music only: **no logo, end card, sting or jingle**; 1 s (30 f) black tail; name ends `_original` | `assemble` (`end_card: true` in sets.json brings the old end back) | 30.733 s total |
| Bed fades (cosine) from the **end of the last word** to 50 ms before the end; ducked from the last clip's start | `end_audio_plan` (`no_logo`) | fade 29.713 → 30.683, hold 28.933 → 30.733 |
| **Bed level -26 dB RMS** (was -20) | `stage_audio`, project `music_rms_db` | -26.3 |
| **Per-shot duck depth, 180 ms cosine ramps, auto-solved** so active speech is **10.6 dB** over the bed in every shot and **13 dB** under the final guest line (harder duck on the button) | `stage_audio` (`duck_gap_db`, `duck_final_gap_db`, `duck_auto`, `duck_ramp_ms`; an explicit shot `duck` is kept) | gaps 10.1-10.7 dB, final line 12.9-14.8 dB |
| Captions timed from **padded-span ASR** (0.5 s before the in, 0.8 s after the out); t < e <= dur, never negative, tail words redistributed, **every word on screen >= 150 ms**, a later page is delayed (no flash at page breaks) | `assemble`; `introkit retime` after any in/out edit | 83 words, all VIS_OK |
| Lines **split into pieces** (up to 10 per beat) to cut fillers, stutters and false starts, each piece with a short fade (20-40 ms; 100-120 ms at a beat end) | `assemble` (pieces `a`-`j`) | hook = 4 pieces, Andy = 3, button = 3 |
| In-point after the **previous word's leftover** (in in the dip after it) | `assemble` WARN `in cuts into '<w>'` | — |
| **End fade starts only after the last word has fully ended** | `vet --write` (fade shrinks to 8 ms instead of eating the word) | — |
| **Edge ASR on the final mp4** (whole line, first / last 1.2 s, fillers, script diff, fade vs acoustic end) | `introkit edges` | — |
| **Viral hooks only**, guest first, strict alternation, equal host lines | `references/line-selection.md`, `assemble` checks order | Lior hook / Andy / Lior / Robbie / Lior button |

Not part of B2 (added later for B3, use only when that layout needs it): caption `cy 740` on
host singles to clear an on-screen logo.

Dry run (no render) to check the bed and ducks of any project:
`introkit.py build --project P.framed.json --words W.json --out <scratch> --audio-only`.
On B2 it prints exactly the `build_B2.log` level lines; with its ducks removed the auto-solver lands
within 0.3 dB of them.

**Build once, then hand off.** Build each version once as finished 1080p (`_vN`, never
overwrite). Report the path as soon as the file is written, then send one `qc` + `edges`
afterward. The user does the listening QC. No rebuild loops unless the user asks (rule 049).

**Stitching a delivery** (intro + subscribe bumper + full interview): use `scripts/stitch.py`, or TheStitcher-V2 when `STITCHER_HOME` is set, always with `--no-dynamics`. Leave the
episode's audio untouched (no dynamics chain), apply a measured plain gain to the subscribe
clip only, and keep the intro's speech at unity (RULE 043 / 049). See step 10.

## QC: four decisive gates in one command (details in `references/qc.md`)

Checks that depend only on the chosen span run ONCE, at selection time, and are cached
(`vet`). After the build, `qc` checks only what the build can break, in one run:

```
introkit.py vet --project P.json --words W.json [--write]     # per line candidate, cached by source+in/out
introkit.py qc  --final F.mp4 --project P.framed.json [--words W.json] [--out qc.json]
```

- **signals (before selection, read-only):** `introkit.py signals --transcript T.json --speaker
  guest` ranks about 12 guest lines by energy peaks, pause before, questions, contrast/power
  words, numbers and length (JSON + table, hook-type hint, flags). A shortlist, not a choice.
- **vet editorial WARNs + stranger test:** besides the gates below, vet WARNs when a beat starts
  on a weak opener / bare pronoun / back-reference and when it stops mid-sentence or on a
  conjunction (column `cut after`), then prints the plain script for the stranger test
  (`references/line-selection.md`). WARN only; `--no-edit-checks` skips them.
- **vet (selection time):** stutters / fillers (per-clip ASR with VAD on AND off; this is what
  catches hidden stutters that full-file ASR smooths over), the acoustic end of each last word
  (`--write` sets the out-point there + 60-120 ms tail, 20-25 ms fade, never the raw ASR word
  end), every cut's dip graded against the ROOM FLOOR (OK <= floor+4 dB, MARG <= +8, HOT;
  -45 dBFS absolute is unreachable in event audio), headroom feasibility. `--write` also sets
  final black >= 15 f and `sting_at`, and lets the build compute the music fade.
- **qc gates:** FORMAT (one ffprobe: h264 1080p fps SAR 1:1 bt709 yuv420p faststart AAC 48k),
  AUDIO (one ebur128 after AAC: -14 +-0.5 LUFS, TP <= -1; sting tail >= 2.45 s; music fade
  ends before the sting and starts after the last word, from the timeline), FRAMING (one YuNet
  pass: every 3rd frame + each shot's first/last; head/chin/side margins, exactly one face,
  caption box clear of the face, captions outside the 60 px strips), SPEECH (one per-shot ASR
  pass on the final mix vs the script: filler/stutter FAIL; out-point vs the measured acoustic
  end: >= +40 ms OK, -150..+40 ms LISTEN, < -150 ms FAIL; pauses > 0.35 s FAIL, tail WARN).
- Output: one PASS/FAIL table per video with shot, time and the fix; unchanged shots reuse
  cached results (`qc_cache.json`); a contact sheet (`*_contact.jpg`, face + caption boxes per
  shot) and a waveform (`*_wave.png`) are the only human-review artefacts. LISTEN rows are
  the only things to listen to.
- **qc SPEAKER row (advisory, never fails):** mouth-region motion of the face inside each shot's
  crop vs the source speech envelope, and vs other faces in the source frame; `ok`, `ambiguous`
  (the honest answer on weak evidence) or WARN "possible wrong speaker on screen". Cached per
  shot (only changed shots re-run); `--no-speaker-check` skips it.
- Head touching the top is accepted only per shot / speaker via `allow_top_touch` (Kristen).
  It is a waiver with no budget, so **record the measured crop** (`worst.top`, px and % of
  frame height) in the QC note: over 549 sampled shots 67 % crop the head, median 48 px, and
  5.1 % crop more than 100 px. Above ~8 % of frame height, get it approved instead of logging
  a pass (rule 052).
- **"Exactly one face" is a seated-shot rule** (rule 053). A standing / DJI two-shot has two
  people in frame by design: report it as a WARN with the reason and fail only when an extra
  face sits inside the caption box. The strict check fired 77 times across 71 runs, always on
  files that shipped.
- Removed as gates: multi-model ASR voting, full-file ASR, windowed re-transcription, every-
  frame face scan (`--classic-faces` keeps it as a diagnostic), voiceprint (diagnostic only,
  run once when mapping hosts). Expected: ~15-25 s per video cold, ~2-3 s when nothing changed
  (was ~3 min per pass).

## Framing and layout contract (user preferences, 2026-11-02, amended 2026-11-23)

**Seated / podcast-style (default):**
- **One speaker visible per shot.** The face is zoomed, placed as high as possible with no
  crop of the hair, and centred at about **25% or 75%** of the width (planner target; clamps
  0.20-0.38 / 0.62-0.80). Captions sit on the side the face looks toward, on a **faded black
  gradient, never a solid box**.
- **Look-room:** the face looks into the text side (head yaw from YuNet landmarks); when the
  source cannot place it there, the other side.
- Host face ~0.55 of frame height, guest ~0.66; zoom cap 2.45x; zoom backs off for speakers
  who move vertically.
- **The other host is never visible**, not even through the fade: bodies (~1 face width each
  side) are cropped out. Do not put a solid black box behind the caption text; hide a leaking
  second body with the faded gradient (or a feathered matte only as a last resort, and never
  as a caption background).
- **No tint or colour grade.**
- Caption width (`maxw`) is derived from the face edge (96 px margin + 48 px gap).
- Because the type sits opposite the face, seated captions can stay large and centred on
  `cy` (the signed-off Lior B2 reference is Playfair 100 / Anton 190 at `cy 540`). Do not
  shrink a seated block to dodge a chin.
- `frame` (side layout, default) writes all of this; `layout: "classic"` keeps the old planner.

**Standing or DJI walk-and-talk (the exception; e.g. Raoul Pal, rule 048):** locked, static
framing, no camera following, two-shots allowed. Do not apply the seated face-tracking crop.
Close shots are chest-up medium close-ups that show the shoulders, chest and gestures, not tight
floating heads. The top of the head sits only about 2-5% below the frame top, and the head
is about 35% of the frame height (a chest-up MCU frame (Raoul Pal reference)). Both people are
visible and drifting, so **anchor the caption block low** (`caption_bottom`) and keep it inside
the bottom safe rim; solve the size with `caption_fit.py --mode standing`.

## Lines and versions

- **3 versions per guest**, each with a different hook combination (hook / host Q / answer /
  button), never the same hook twice.
- **Every hook must be viral (user rule, 2026-11-16):** provocative, surprising, quotable, with
  strong standalone value for a stranger scrolling past. Bland, generic or definitional lines are
  rejected even when they are clean: "AI is a technology, crypto is a technology" is the canonical
  reject. **Every line** (not just the opening hook) must be a complete, meaningful sentence with
  a hook value of **4 or more out of 5** (rule 045). Canonical keep (B2): "Nobody's incentivized
  to share or monetize their own data, because then that just means I'm giving up my secrets."
- **Clean the line:** no stutters, fillers (uh, umm), doubled words ("what what") or trailing
  unrelated words.
- **Host lines** must be engaging, specific to the guest, and long enough not to feel short
  (Illia B_v5 Andy was rejected). Lengthen the same line or switch to a longer host segment.
  Generic prompts ("What are you most excited about?") are replaced.
- Spell names, acronyms and **crypto tickers** as on the chyron / slides / `references/glossary.md`,
  not as ASR heard them (DTCC, not "DTC"; **SOL**, not "soul"). Run `scripts/glossary.py` on the
  on-screen words (rule 047). Tickers are written in capitals.
- **Beat structure (user rule, 2026-11-23, rule 046).** Both hosts present: guest, Andy, Robbie,
  guest. One host: guest, host, guest. The guest always opens. The user confirms which hosts are
  present — do not run a host-presence check. `assemble --structure` records the confirmed order.
  A mismatch is a WARN; consecutive same-speaker beats are still refused.
- **Each guest segment is at least 5.0 s (user rule, 2026-11-23, rule 045).** Shorter candidates
  are rejected or extended to a natural word boundary — never padded with silence, never cut
  mid-word. `assemble` / `vet` / `edges` WARN on short guest beats.

## Procedure

1. **Probe both files.** `introkit.py probe`. Record fps, duration, colour tags, audio layout
   (a "stereo" track that is byte-identical in both channels is dual-mono — no isolated mics).
   Then **scan the source for defects** (`introkit.py scan-defects --src <recording>`) before
   choosing anything: a dropout inside a line you like is a line you cannot use, and a static
   burst under speech is a repair job, not a cut (rule 051).
2. **Teardown the reference.** Scene cuts, black frames, per-section duration, LUFS, where
   the title card lands. Write the numbers down. Reference targets (IMG_9231, 33.483 s):
   hook 3.68 s, title card at 19.52 s, end card from ≈29.0 s.
3. **Get the words** per the table above. Cross-check fillers against the SRT (Whisper
   suppresses them, rule 018). Never use SRT timestamps as cut points (rule 044).
4. **Confirm the hosts with the user**, then use the matching structure (rule 046): guest,
   Andy, Robbie, guest when both are present, or guest, host, guest with one host. Do not
   run a host-presence check.
5. **Select lines** with `references/line-selection.md`: run `introkit.py signals` for the
   shortlist, keep only complete sentences with hook value ≥ 4 / 5, guest beats ≥ 5 s, no
   fillers / stutters / doubled words, then delivery, cleanliness, hook compliance, 2-3
   alternative sets plus a recommendation.
6. **Assemble and vet.** Write the beats into `<source folder>\intros\work\sets.json`
   (see `references/pipeline.md`) and run `introkit.py assemble --sets sets.json --set A` (project + words, script spelling
   wins), then `introkit.py vet --project project_A.json --words words_A.json --write`.
   Pass `--structure guest,andy,robbie,guest` (or set it in sets.json) when the episode has
   two hosts. Fix every vet FAIL (stutter / filler / clipped end / no face) by choosing another
   span BEFORE framing or rendering anything. LISTEN rows: listen to that one cut. Read every
   `edit:` WARN (weak opener, ends mid-sentence / on a conjunction, short guest beat < 5 s, collapsed
   repeat, tight next-word) and do the **stranger test** on the printed SCRIPT once per set;
   re-pick or extend spans that fail it. `--write` also applies the B5 tight-tail envelope
   (full volume through the last word, 30 ms fade of the next word, room tone from the same
   recording) and, when energy shows a repeated phrase ASR collapsed, moves the in-point into
   the pause.
7. **Frame:** `introkit.py frame --project project_A.json` (side layout by default; list
   broadcast furniture as `avoid: [[x,y,w,h],...]`; per-speaker `speakers: {name: {pick,
   text_side, zoom}}` when two people share a camera). With two faces in frame and no explicit
   `pick`, `frame` uses a mouth-vs-speech check to put the actual speaker on screen, and keeps
   that face on the side opposite the captions. A mid-shot camera cut is a WARN: split the beat.
   Read `<stem>.framed.frame.txt`.
7b. **Fit the captions, then retime.** One size per family for the whole intro, wrapped at word
    boundaries and never shrunk per line (rule 050). Solve the numbers before rendering — from
    the skill folder:
    ```
    python scripts/caption_fit.py heads --project P.framed.json --kit <skill> --out work/heads.json
    python scripts/caption_fit.py solve --project P.framed.json --words words_A.json \
           --kit <skill> --heads work/heads.json --scratch work/capfit \
           --mode standing --write work/P.fitted.json
    ```
    `heads` measures faces on the FRAMED picture (`work/base.mp4`); measuring `--src` is wrong
    because the render is cropped and zoomed. `solve` returns `caption_size_playfair`,
    `caption_size_anton`, `maxw` and (standing only) `caption_bottom` in one pass (~30-100 s, no
    render loop). **Seated is the default; pass `--mode standing` for DJI walk-and-talks.**
    Then `introkit.py retime --project project_A.framed.json --words words_A.json` after any
    in/out edit (padded ASR, >= 150 ms per word). Check bed + ducks with `build --audio-only`
    if in doubt.
8. **Render** (`references/pipeline.md`): `build` does picture -> graphics -> audio -> mux,
   with SAR 1:1 + bt709 tags, the AAC remaster (-14 LUFS, TP <= -1.1) and faststart built in.
   Zoomed shots (crop zoom > 1.15x) are AI-upscaled (Real-ESRGAN x4, 0.5 denoise) **by default**
   after setup. `--no-upscale` skips it, `--upscale` requires it. Audio is stream-copied and
   hash-verified after an upscale build. **Build the finished 1080p file directly** — do not
   spend time on preview versions to show first (rule 049). On NVIDIA, encode with h264_nvenc;
   for a short intro (~20-30 s) a full high-quality NVENC re-encode is fine, and is preferred
   over frame-exact stream-copy splicing. Version as `_vN` and never overwrite. **Report the
   path as soon as the file is written**; send extended QC afterward. Stitching the full
   episode is a separate step (step 10), not part of `build`.
9. **QC once, after the path has been reported:** `introkit.py qc --final intros/NAME.mp4 --project project_A.framed.json --out
   intros/work/qc_A.json`. Also `python scripts/glossary.py words_A.json`. Look at the contact
   sheet; listen to LISTEN rows and to the last word of every beat on the final audio (rule 044);
   look at any SPEAKER WARN shot (advisory: it never blocks a PASS).
   After a fix, re-run `qc`: only the shots whose inputs changed are re-checked.
   Then `introkit.py edges --final intros/NAME.mp4 --project project_A.framed.json --words
   words_A.json` (edge ASR on the delivered mix; FIRST / LAST / FILLER / DIFF / FADE flags).
   ASR can fill in a clipped ending, so a clean transcript is not proof the word is whole.
   **Build once and hand off**: report the file first, then qc + edges; the user QCs by ear.
   Re-build only on his request, always as a new `_vN`.
10. **Stitch the delivery** (only when the episode is wanted as one file). Use `scripts/stitch.py` (or `$STITCHER_HOME` if a local stitcher is configured) with `--no-dynamics`, in the order intro,
    subscribe clip, full interview, writing `<Guest>_New_Intro.mp4` in the guest folder (a
    later stitch is `_New_Intro_v2.mp4`). The portable command is `python
    scripts/stitch.py INTRO.mp4 SUBSCRIBE.mp4 EPISODE.mp4 -o "<Guest>_New_Intro.mp4"` (RULE 030).
    Measure first (`--measure` prints all three files' LUFS/dBTP), then:

    - **hook overlap** = the intro's *measured* black tail: the subscribe clip's picture starts where
      that black starts and the intro's sound plays on under it, ducked;
    - **overlap** = the subscribe clip's *measured* black tail: the episode's picture starts where
      that black starts and the clip's sound runs under the episode's opening, ducked;
    - **the episode's picture and audio are copied**, never re-encoded, and never processed: the
      dynamics chain is OFF by default (RULE 036);
    - **level** (RULE 043 / 049): apply a measured plain gain to the **subscribe clip only**,
      matched to the interview's LUFS. Leave the intro's speech at unity gain. Stream-copy the
      interview; conform the short pieces with NVENC. Note a black first frame on the interview
      if it has one; do not silently trim it. `--level off` disables a portable stitcher pass;
    - **verification**: frame count against the sum of the pieces, picture and sound durations
      against each other, timescale, frame spacing, and decoded-frame checksums of sampled frames
      against the pieces that were joined. It writes `<out>.part.mp4` and renames only on a pass.

    A cut that is not a keyframe costs one re-encode of the hook (crf 14) so an IDR can land there —
    say so in the report; the delivered intro file itself is never modified. Report the measured
    level of each piece and of the finished file.

## Motion and type contract

- Type is **per word**, keyed to the spoken word's start time, revealed **a phrase (page)
  at a time**, with balanced word-boundary wraps; never an orphan word.
- Sizes (1920×1080): Playfair Italic **110-130 px** em; Anton claims with a cap height of
  **≈133-171 px**; Barlow Condensed ≈1.3× the earlier size (≈81-101 px). See
  `references/design-system.md`.
- Punch-in (type): `ease_out_back` 0.84× → 1.0× over 0.30 s, 30 px rise, 0.12 s alpha ramp.
- **Face zoom (clips):** seated layout (the default, see the framing contract above): head as
  high as possible with no hair crop, centred at about 25% or 75% of the width, zoomed (host
  ~0.55 H, guest ~0.66 H, cap 2.45x), a 4 % face-anchored push-in when it fits, captions on the
  faded gradient side. Standing / DJI interviews (the exception) stay locked, with chest-up
  medium close-ups (rule 048) and captions anchored low so they clear both heads. Set by
  `frame`, never by hand; the old centre `punch` is legacy.
- **One size per family:** `caption_size_playfair` / `caption_size_anton` set the type size for
  the whole intro; lines wrap at word boundaries inside `maxw` and are never shrunk individually
  (rule 050). On standing shots `caption_bottom` anchors the block's ink under both heads. Get
  those numbers from `scripts/caption_fit.py`, not by eye.
- Cards push in slowly (1.00 → 1.045). Section changes are **dips to black** (≈0.33 s).

## Sound contract

- Music: the bundled bed (`assets/music/`) at **-26 dB RMS**, ducked per shot (auto-solved, 180 ms
  cosine ramps) so speech sits **~10.6 dB over the bed**, **~13 dB under the final guest line**,
  released in every black gap. **Default since 2026-11-16 (B2): music only, no logo, no sting, no
  jingle, 1 s black tail.** With `end_card: true` in sets.json: tape-stop into the title card,
  boom on each cut, two-note sting on the logo (`assets/sfx/`).
- Speech is the master. If music and speech compete, the music loses.
- **End (B2 default, no logo):** the bed stays ducked from the last clip's start, fades out
  (cosine) from the end of the last word to 50 ms before the end of the 1 s black tail.
- **End (with end_card):** the bed stays ducked from the last clip, fades out (cosine) from the end of the last
  word to 40 ms before the sting and stays down through the end black; the sting plays alone
  and in full (final black >= 15 frames). Automatic at build; explicit `music_fade_out` /
  `duck_hold` in the project still win.
- **Fade rule (B2):** every end fade starts only AFTER the last word has fully ended (acoustic
  end, not the ASR end); a faint tail of the word is better than a clipped one. `vet --write`
  shrinks the fade (8 ms floor) rather than start it inside the word; `edges` flags FADE.
- **Last word:** out-point at the measured acoustic end + tail, 20-25 ms speech fade
  (`speech_fade_ms`, default 25), video follows audio. **Err late (2026-11-03):** final plosives
  (/k/ /t/ /p/: 'attack') and vowel decays ('three') need the later out. `vet --write` now ends a
  word only after 60 ms at the room floor (a plosive closure is not an end), adds a 150 ms tail,
  ignores Whisper's next-word start as a limit (only the measured onset of the next sound caps
  it), and when the word runs into the next one cuts at the deepest energy dip. When in doubt,
  check the 10 ms envelope and put the out after the decay/release, not on the ASR word end.
- **Tight next word (B5, 2026-11-18):** if the next word follows within ~20 ms, a cut at the
  ASR word end clips the last word (e.g. "crossover" then "you"). Keep full volume through the
  word's real tail, fade the next word in about 30 ms, and fill with matching room tone from
  the same recording before the final fade. `vet --write` attaches a `tail_env` on the shot;
  `edges` WARNs (`TIGHT`) when that envelope is missing. This is a flag, not a QC FAIL.
- **Speech stays at unity gain (2026-11-20 / 2026-11-23, rule 049).** Do not raise or lower
  dialogue — the segments are already treated in the full episode. Keep music ducking gentle
  (the B2 auto-solver and 180 ms ramps, not an aggressive extra pass). `gain_db` remains
  available only if the user asks for a level change on a specific shot.
- **Last-word and audio-fix reminder:** never cut on an SRT timestamp; err late; listen to the
  final audio (rule 044). To fix an intro's audio, rebuild it at identical cut times with the
  corrected source and keep the picture.

## Outputs (default `<source video folder>\intros\`)

**Output location (default):** `<folder of the source video>\intros\`: finals `<name>.mp4` plus `LINE_SETS.md`, `CUTLIST.md`, `QC_REPORT.md`; scratch (sets.json, project/words json, vet cache, qc json, contact sheets, build `work/`) in `intros\work\`. Created if missing. Override: `build --out DIR` (CLI wins), else `out_dir` in sets.json / the project (relative to that file); `assemble --out DIR` moves only the json files. Example: `Shows/Ep 12/Ep 12.mp4` -> `Shows/Ep 12/intros/THE_ROLLUP_<Guest>_ColdOpen_A.mp4`.

Into the intros folder: `<name>.mp4`, `CUTLIST.md`, `QC_REPORT.md`, `LINE_SETS.md`
(`cutlist.csv` / `RULES.md` copy when asked). Into `intros\work\`: sets.json, project / words
json, `vet_cache.json`, `qc_<SET>.json` + contact sheet + waveform, `hw.json` (the hardware path
that was used). Never move or overwrite earlier deliverables; a revision is a new name (`_vN`, e.g. `_C_v8`).
To fix an intro's audio, rebuild it at identical cut times with the corrected source audio
and keep the picture. Speech stays at unity gain; music ducking stays gentle (rule 049).

## Definition of done

- Gates 1-7 measured and reported, with the numbers in `QC_REPORT.md`; `introkit.py qc`
  PASS for every version (its table and contact sheet go into the report).
- Cut list carries source timecode `HH:MM:SS:FF` for every clip.
- Deviations from the reference are **stated with their measured delta**, not smoothed over.

## References

- `RULES.md` — running rules 000-049, read first
- `references/glossary.md` — crypto tickers and names; `scripts/glossary.py` to check captions
- `TOOLS.md` — setup (uv, one folder), ffmpeg per OS, hardware tiers, CPU costs
- `ASSETS.md` — bundled music, SFX, logo, fonts; provenance
- `references/line-selection.md` — what makes a line usable, hook compliance, out-points
- `references/pipeline.md` — stage-by-stage commands and file contracts
- `references/design-system.md` — type sizes, paging, colour, layout, scrim maths
- `scripts/caption_fit.py` — solve/verify caption size and anchor (seated default, standing
  exception); measures faces on the framed picture and reads real ink boxes from the engine's
  own overlay, so it needs no render loop
- `references/qc.md` — every gate and how to measure it
- `references/troubleshooting.md` — failures already paid for; read before debugging
- `references/ffmpeg.md` — FFmpeg recipes now used by the kit (accurate seek, probe, loudness, before/after metrics vs Kash/Quinn); informed by kajisho5/ffmpeg-skill (MIT)
- `scripts/introkit.py` — entry point (`sidekit.py` side planner / vet / qc, `editcheck.py` openers / cut boundary / stranger script / `signals` / qc speaker row, `transcribe.py`, `speakers.py`, `ffseek.py` alongside)

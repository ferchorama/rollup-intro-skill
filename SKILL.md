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

All tools and Python packages live in ONE folder, `<skill>/.rollup-intro-env` (or
`$ROLLUP_INTRO_HOME`), built with astral **uv**: `setup.ps1` on Windows, `./setup.sh` on
macOS/Linux. It holds the uv-managed Python, the venv, Whisper `large-v3-turbo`, the HF cache
and (if needed) a portable ffmpeg. `introkit.py` re-runs itself inside that venv, so any
`python` can launch it. Delete the folder to uninstall. Details: `TOOLS.md`.

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

## Hardware

`--device auto` (default) tests each tier with a 1 s decode/encode and takes the first that
works: **cuda → qsv (Intel Quick Sync) → d3d11va/dxva2 (Windows: AMD & others, AMF encoder) →
vaapi (Linux) → videotoolbox (macOS) → cpu**. Acceleration is used only for source decode,
review previews, Whisper and the speaker-embedding model. The **deliverable is always libx264
CRF 15, preset medium**, on every tier, and the output is bit-identical whichever decoder ran. Force a
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

## Non-negotiable gates

1. **-14.0 LUFS ±0.5 integrated, true peak ≤ -1.0 dBTP on the delivered file**, measured with
   `ffmpeg -af ebur128`, not estimated.
2. **Every spoken word on screen** during its utterance, mixed faces inside one sentence.
3. **No type over a face.** Verified per frame against a measured face box.
4. **The person on camera is the person speaking** at both the in-point and the out-point of
   every cut. Map camera cuts and hosts once with a voiceprint (diagnostic, not a build gate).
5. **No filler, no hesitation, no gap > 0.35 s inside a line, and no stray word after the
   out-point** — out-points come from the waveform, not the ASR word end (rule 027).
6. **Re-transcribe the deliverable per shot** (`qc` speech gate) and diff it against the script.
7. **Hook compliance.** A hook that states or implies price, returns or performance is flagged
   and ships only with a safer backup line ready (rule 028).

## Lessons from past sessions (read before cutting)

Read `LESSONS_LEARNED.md` before selecting lines and again before QC. It records what real
sessions taught: editorial rules the client enforced, kit limits (true peak measured before
AAC, sting truncated by a short end card, no music fade option, non-square SAR) and the
exact workarounds. Add new lessons there after every delivery.

## QC: four decisive gates in one command (details in `references/qc.md`)

Checks that depend only on the chosen span run ONCE, at selection time, and are cached
(`vet`). After the build, `qc` checks only what the build can break, in one run:

```
introkit.py vet --project P.json --words W.json [--write]     # per line candidate, cached by source+in/out
introkit.py qc  --final F.mp4 --project P.framed.json [--words W.json] [--out qc.json]
```

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
- Head touching the top is accepted only per shot / speaker via `allow_top_touch` (Kristen).
- Removed as gates: multi-model ASR voting, full-file ASR, windowed re-transcription, every-
  frame face scan (`--classic-faces` keeps it as a diagnostic), voiceprint (diagnostic only,
  run once when mapping hosts). Expected: ~15-25 s per video cold, ~2-3 s when nothing changed
  (was ~3 min per pass).

## Framing and layout contract (user preferences, 2026-11-02)

- **One speaker visible per shot.** The face is zoomed and sits on a third (0.30 / 0.70 W,
  clamps 0.20-0.38 / 0.62-0.80); the captions sit on the opposite, black-faded side.
- **Look-room:** the face looks into the text side (head yaw from YuNet landmarks); when the
  source cannot place it there, the other side.
- Host face ~0.55 of frame height, guest ~0.66; zoom cap 2.45x; wide / two-shot pieces (face
  < 230 px) are punched in to the cap; zoom backs off for speakers who move vertically.
- **The other host is never visible**, not even through the fade: bodies (~1 face width each
  side) are cropped out, or covered by a feathered SOLID black matte on the caption side.
- Caption width (`maxw`) is derived from the face edge (96 px margin + 48 px gap).
- `frame` (side layout, default) writes all of this; `layout: "classic"` keeps the old planner.

## Lines and versions

- **3 versions per guest**, each with a different hook combination (hook / host Q / answer /
  button), never the same hook twice.
- **Host lines must be engaging and specific to the guest** (what they built, a number, a
  claim to react to). Generic prompts ("What are you most excited about?") are replaced.
- Spell names and acronyms as on the chyron / slides, not as ASR heard them (DTCC, not "DTC").

## Procedure

1. **Probe both files.** `introkit.py probe`. Record fps, duration, colour tags, audio layout
   (a "stereo" track that is byte-identical in both channels is dual-mono — no isolated mics).
2. **Teardown the reference.** Scene cuts, black frames, per-section duration, LUFS, where
   the title card lands. Write the numbers down. Reference targets (IMG_9231, 33.483 s):
   hook 3.68 s, title card at 19.52 s, end card from ≈29.0 s.
3. **Get the words** per the table above. Cross-check fillers against the SRT (Whisper
   suppresses them, rule 018).
4. **Map the hosts** with a voiceprint. If the session has a **single host**, the two host
   beats come from the same person (rule 020).
5. **Select lines** with `references/line-selection.md`: delivery first, then cleanliness,
   hook compliance, 2-3 alternative sets plus a recommendation.
6. **Assemble and vet.** Write the beats into `<source folder>\intros\work\sets.json`
   (see `references/pipeline.md`) and run `introkit.py assemble --sets sets.json --set A` (project + words, script spelling
   wins), then `introkit.py vet --project project_A.json --words words_A.json --write`.
   Fix every vet FAIL (stutter / filler / clipped end / no face) by choosing another span
   BEFORE framing or rendering anything. LISTEN rows: listen to that one cut.
7. **Frame:** `introkit.py frame --project project_A.json` (side layout by default; list
   broadcast furniture as `avoid: [[x,y,w,h],...]`; per-speaker `speakers: {name: {pick,
   text_side, zoom}}` when two people share a camera). Read `<stem>.framed.frame.txt`.
8. **Render** (`references/pipeline.md`): `build` does picture -> graphics -> audio -> mux,
   with SAR 1:1 + bt709 tags, the AAC remaster (-14 LUFS, TP <= -1.1) and faststart built in.
   Review with `introkit.py preview`, never ship the preview.
9. **QC once:** `introkit.py qc --final intros/NAME.mp4 --project project_A.framed.json --out
   intros/work/qc_A.json`. Ship only on PASS; look at the contact sheet; listen to LISTEN rows.
   After a fix, re-run `qc`: only the shots whose inputs changed are re-checked.

## Motion and type contract

- Type is **per word**, keyed to the spoken word's start time, revealed **a phrase (page)
  at a time**, with balanced word-boundary wraps; never an orphan word.
- Sizes (1920×1080): Playfair Italic **110-130 px** em; Anton claims with a cap height of
  **≈133-171 px**; Barlow Condensed ≈1.3× the earlier size (≈81-101 px). See
  `references/design-system.md`.
- Punch-in (type): `ease_out_back` 0.84× → 1.0× over 0.30 s, 30 px rise, 0.12 s alpha ramp.
- **Face zoom (clips):** side layout (see the framing contract above): face on a third,
  zoomed (host ~0.55 H, guest ~0.66 H, cap 2.45x), a 4 % face-anchored push-in when it fits,
  captions on the black side. Set by `frame`, never by hand; the old centre `punch` is legacy.
- Cards push in slowly (1.00 → 1.045). Section changes are **dips to black** (≈0.33 s).

## Sound contract

- Music: the bundled bed (`assets/music/`), ducked ~14 dB under speech and released in every
  black gap; tape-stop into the title card, boom on each cut, two-note sting on the logo
  (`assets/sfx/`).
- Speech is the master. If music and speech compete, the music loses.
- **End:** the bed stays ducked from the last clip, fades out (cosine) from the end of the last
  word to 40 ms before the sting and stays down through the end black; the sting plays alone
  and in full (final black >= 15 frames). Automatic at build; explicit `music_fade_out` /
  `duck_hold` in the project still win.
- **Last word:** out-point at the measured acoustic end + tail, 20-25 ms speech fade
  (`speech_fade_ms`, default 25), video follows audio.
- **Per-shot level (2026-11-03):** shot keys `gain_db` (speech gain in dB, applied after the
  global dialogue normalisation) and `duck` (music duck depth 0-1 under that shot, default
  0.72). `build` prints `level shot N: speech X dB, music Y dB, gap Z dB` for every clip; each
  line should sit about **+10 dB or more** over the bed. A quiet clip (typically the button)
  gets `gain_db` +4..+7 and `duck` 0.85 in the framed project; the music fade before the sting
  is unchanged (LESSONS_LEARNED E1).

## Outputs (default `<source video folder>\intros\`)

**Output location (default since 2026-11-03):** `<folder of the source video>\intros\`: finals `<name>.mp4` plus `LINE_SETS.md`, `CUTLIST.md`, `QC_REPORT.md`; scratch (sets.json, project/words json, vet cache, qc json, contact sheets, build `work/`) in `intros\work\`. Created if missing. Override: `build --out DIR` (CLI wins), else `out_dir` in sets.json / the project (relative to that file); `assemble --out DIR` moves only the json files. Example: `H:\Show\Ep 12\Ep 12.mp4` -> `H:\Show\Ep 12\intros\THE_ROLLUP_<Guest>_ColdOpen_A.mp4`.

Into the intros folder: `<name>.mp4`, `CUTLIST.md`, `QC_REPORT.md`, `LINE_SETS.md`
(`cutlist.csv` / `RULES.md` copy when asked). Into `intros\work\`: sets.json, project / words
json, `vet_cache.json`, `qc_<SET>.json` + contact sheet + waveform, `hw.json` (the hardware path
that was used). Never move or overwrite earlier deliverables; a revision is a new name (`_B2`).

## Definition of done

- Gates 1-7 measured and reported, with the numbers in `QC_REPORT.md`; `introkit.py qc`
  PASS for every version (its table and contact sheet go into the report).
- Cut list carries source timecode `HH:MM:SS:FF` for every clip.
- Deviations from the reference are **stated with their measured delta**, not smoothed over.

## References

- `RULES.md` — running rules 000-030, read first
- `TOOLS.md` — setup (uv, one folder), ffmpeg per OS, hardware tiers, CPU costs
- `ASSETS.md` — bundled music, SFX, logo, fonts; provenance
- `references/line-selection.md` — what makes a line usable, hook compliance, out-points
- `references/pipeline.md` — stage-by-stage commands and file contracts
- `references/design-system.md` — type sizes, paging, colour, layout, scrim maths
- `references/qc.md` — every gate and how to measure it
- `LESSONS_LEARNED.md` — session lessons, kit TODOs, SAR and audio workarounds; read before cutting
- `references/troubleshooting.md` — failures already paid for; read before debugging
- `scripts/introkit.py` — entry point (`sidekit.py` side planner / vet / qc, `transcribe.py`, `speakers.py` alongside); `CHANGELOG.md` — what changed

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

## B2 house style — the default for every intro (2026-11-16)

**Reference example:** `K:\Lior Messika\intros\THE_ROLLUP_Lior_Messika_ColdOpen_B2_original.mp4` (30.73 s, -14.0 LUFS, TP -1.8). Fernando signed it off as the way
every intro is made; its project is `K:\Lior Messika\intros\work\project_B2.framed.json` +
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

**Build once, then hand off.** Build each version ONCE, run ONE `qc` (+ `edges`) per file,
report, and stop: Fernando does the listening QC himself. No rebuild loops unless he asks.

**Stitching a delivery** (hook + subscribe bumper + full episode) is `scripts/stitch.py`, part of
this skill: ffmpeg plus the standard library, no app and no install, any OS. It leaves the episode's
audio untouched (no dynamics chain), gain-matches the hook and the clip to the episode's own measured
loudness (RULE 043), and verifies what it wrote. See step 10.

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
- **Every hook must be viral (user rule, 2026-11-16):** provocative, surprising, quotable, with
  strong standalone value for a stranger scrolling past. Bland, generic or definitional lines are
  rejected even when they are clean: "AI is a technology, crypto is a technology" is the canonical
  reject. Re-check every other line of the set for the same hook value (B2 hook: "Nobody's
  incentivized to share or monetize their own data, because then that just means I'm giving up my
  secrets.").
- **Host lines:** equal number of host beats per host across the set; each host line 4-6 s and
  self-contained (passes the stranger test without the episode).
- **Host lines must be engaging and specific to the guest** (what they built, a number, a
  claim to react to). Generic prompts ("What are you most excited about?") are replaced.
- Spell names and acronyms as on the chyron / slides, not as ASR heard them (DTCC, not "DTC").
- **Speakers strictly alternate (user rule, 2026-11-03).** Never two consecutive beats by the
  same person. With one host + the guest: guest / host / guest / host / guest (e.g. hook > host Q >
  guest answer > host Q > button, or simply hook > host > button). With two hosts, host > host is
  allowed only when they are different people. `assemble` refuses a set that breaks this.
- **Intros always open with the guest, never with a host (user rule, 2026-11-13).** Beat 1
  is the guest hook. `assemble` WARNs if the first beat is not the guest.
- **Every speaker segment / beat is at least 4.0 s (user rule, 2026-11-18).** Shorter
  candidates are rejected or extended to a natural word boundary — never padded with silence.
  Host lines stay 4-6 s. `signals --min-dur` defaults to 4.0; `assemble` / `vet` / `edges`
  WARN (they never FAIL) on any beat under 4.0 s.
- **Beat structure is an option** on `assemble --structure guest,andy,robbie,guest` or
  `structure` in sets.json. The guest still always opens. A mismatch is a WARN, not a refusal
  (alternation remains a hard refuse). Two-host default example: Guest, Andy, Robbie, Guest.

## Procedure

1. **Probe both files.** `introkit.py probe`. Record fps, duration, colour tags, audio layout
   (a "stereo" track that is byte-identical in both channels is dual-mono — no isolated mics).
2. **Teardown the reference.** Scene cuts, black frames, per-section duration, LUFS, where
   the title card lands. Write the numbers down. Reference targets (IMG_9231, 33.483 s):
   hook 3.68 s, title card at 19.52 s, end card from ≈29.0 s.
3. **Get the words** per the table above. Cross-check fillers against the SRT (Whisper
   suppresses them, rule 018).
4. **Map the hosts** with a voiceprint. If the session has a **single host**, the two host
   beats come from the same person (rule 020), but never back to back: a guest line goes
   between them (strict alternation).
5. **Select lines** with `references/line-selection.md`: run `introkit.py signals` for the
   shortlist, score each hook (strength / curiosity / clarity / shareability + hook type; A/B/C
   open on three different types), then delivery, cleanliness, hook compliance, 2-3
   alternative sets plus a recommendation.
6. **Assemble and vet.** Write the beats into `<source folder>\intros\work\sets.json`
   (see `references/pipeline.md`) and run `introkit.py assemble --sets sets.json --set A` (project + words, script spelling
   wins), then `introkit.py vet --project project_A.json --words words_A.json --write`.
   Pass `--structure guest,andy,robbie,guest` (or set it in sets.json) when the episode has
   two hosts. Fix every vet FAIL (stutter / filler / clipped end / no face) by choosing another
   span BEFORE framing or rendering anything. LISTEN rows: listen to that one cut. Read every
   `edit:` WARN (weak opener, ends mid-sentence / on a conjunction, short beat < 4 s, collapsed
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
7b. **Retime captions** after any hand edit of in/out points: `introkit.py retime --project
   project_A.framed.json --words words_A.json` (padded ASR, >= 150 ms per word; `assemble` already
   did this for the original spans). Check bed + ducks with `build --audio-only` if in doubt.
8. **Render** (`references/pipeline.md`): `build` does picture -> graphics -> audio -> mux,
   with SAR 1:1 + bt709 tags, the AAC remaster (-14 LUFS, TP <= -1.1) and faststart built in.
   Zoomed shots (crop zoom > 1.15x) are AI-upscaled (Real-ESRGAN x4, 0.5 denoise) **by default**
   after setup. `--no-upscale` skips it, `--upscale` requires it. Audio is stream-copied and
   hash-verified after an upscale build. Review with `introkit.py preview`, never ship the preview.
   Stitching the full episode is a separate step (step 10), not part of `build`.
9. **QC once:** `introkit.py qc --final intros/NAME.mp4 --project project_A.framed.json --out
   intros/work/qc_A.json`. Ship only on PASS; look at the contact sheet; listen to LISTEN rows;
   look at any SPEAKER WARN shot (advisory: it never blocks a PASS).
   After a fix, re-run `qc`: only the shots whose inputs changed are re-checked.
   Then `introkit.py edges --final intros/NAME.mp4 --project project_A.framed.json --words
   words_A.json` (edge ASR on the delivered mix; FIRST / LAST / FILLER / DIFF / FADE flags).
   **Build once and hand off** (B2 house style): one build, one qc + edges per file, report the
   paths, hook text and any flags; Fernando QCs by ear. Re-build only on his request.
10. **Stitch the delivery** (only when the episode is wanted as one file): `python
    scripts/stitch.py INTRO.mp4 SUBSCRIBE.mp4 EPISODE.mp4 -o "<Episode>_New_Intro.mp4"`. It is a
    *portable* part of the skill — ffmpeg and the standard library, nothing else (RULE 030), so it
    works on any machine that can run the rest of the kit. Reads its own measurements first
    (`--measure` prints all three files' LUFS/dBTP and the gains it would apply), then:

    - **hook overlap** = the intro's *measured* black tail: the subscribe clip's picture starts where
      that black starts and the intro's sound plays on under it, ducked;
    - **overlap** = the subscribe clip's *measured* black tail: the episode's picture starts where
      that black starts and the clip's sound runs under the episode's opening, ducked;
    - **the episode's picture and audio are copied**, never re-encoded, and never processed: the
      dynamics chain is OFF by default (RULE 036);
    - **level** (RULE 043): hook and clip gain-matched to the episode's own LUFS, gain only, true
      peak ≤ -1.0 dBTP. `--level off` disables it, `--level -14` reproduces the old fixed target;
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
- **Face zoom (clips):** side layout (see the framing contract above): face on a third,
  zoomed (host ~0.55 H, guest ~0.66 H, cap 2.45x), a 4 % face-anchored push-in when it fits,
  captions on the black side. Set by `frame`, never by hand; the old centre `punch` is legacy.
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

- `RULES.md` — running rules 000-042, read first
- `TOOLS.md` — setup (uv, one folder), ffmpeg per OS, hardware tiers, CPU costs
- `ASSETS.md` — bundled music, SFX, logo, fonts; provenance
- `references/line-selection.md` — what makes a line usable, hook compliance, out-points
- `references/pipeline.md` — stage-by-stage commands and file contracts
- `references/design-system.md` — type sizes, paging, colour, layout, scrim maths
- `references/qc.md` — every gate and how to measure it
- `LESSONS_LEARNED.md` — session lessons, kit TODOs, SAR and audio workarounds; read before cutting
- `references/troubleshooting.md` — failures already paid for; read before debugging
- `references/ffmpeg.md` — FFmpeg recipes now used by the kit (accurate seek, probe, loudness, before/after metrics vs Kash/Quinn); informed by kajisho5/ffmpeg-skill (MIT)
- `scripts/introkit.py` — entry point (`sidekit.py` side planner / vet / qc, `editcheck.py` openers / cut boundary / stranger script / `signals` / qc speaker row, `transcribe.py`, `speakers.py`, `ffseek.py` alongside); `CHANGELOG.md` — what changed

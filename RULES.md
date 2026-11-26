# The Rollup cold open — Running Rules

Every feedback round becomes a numbered rule here. This file ships with the next brief.

## 000 — Baseline brief constraints (from the original brief)
Audio-first radio edit before picture. 3 alternative line sets + one recommendation.
Fades to black between sections, never crossfades. Text never over a face. No split
or hyphenated words. Broadcast clock off. -14 LUFS / <= -1 dBTP. QC numbers reported.

## 001 — Transcription engine (round 1, updated 2026-10-30)
Use the skill's own transcriber: `scripts/introkit.py transcribe` (faster-whisper, model
**`large-v3-turbo`** always; float16 on CUDA, int8 on CPU). Never a smaller model, never a
global `whisper` binary.
- Pass `--hotwords` with the episode's proper nouns.
- Diarization with `--diarize`/`diarize`; same-register hosts can merge — enroll each host
  (`--enroll`) when two hosts are present.

## 002 — Sound design sourcing (round 1, updated 2026-10-30)
Default: the bundled bed and SFX in `assets/` (ASSETS.md). Generating new ones (e.g.
ElevenLabs) only when the user asks and with their account.

## 003 — Soundtrack direction (round 3)
Anthemic Believer-style bed: 125 BPM stomp/clap, floor toms, dark minor synth-bass,
riser into a drop, instrumental. Duck ~14 dB under speech and release in every black
gap. Tape-stop into the title card, boom on each cut, two-note sting on the end card.

## 004 — Kinetic typography (round 3)
Type is kinetic and **word-synced**: every word animates on the frame it is spoken
(word timings from forced alignment). Per word: ease-out-back punch-in from 0.84×,
30 px rise, small alternating sideways kick, 0.12 s alpha ramp, slow block drift.

## 005 — Mix faces inside one sentence (round 3)
The reference mixes fonts **within a single spoken sentence**. Playfair Display Italic
for connective words (cream), Anton for the claim (white) with the keyword inline in
gold, Barlow Condensed Bold for the supporting clause. A gold word shares a line with
white words; do not put every face on its own line.

## 006 — Never end on a black-and-white frame (round 3)
A grayscale portrait card reads as a eulogy. Title card keeps the guest in **colour**
with a slow push-in; end card is deep navy + gold glow, not white-on-black.

## 007 — Verify the speaker on camera at BOTH ends of every cut (round 3)
Check the in-point **and** the out-point (and every ~1 s between). A switched programme
cut changes source inside a selection.

## 008 — Never verify framing on the working crop (round 4)
Frame reviews must be at **full frame width**. A 1248 px working crop starting at x=0
sliced the second host off the right edge for the entire build; every "close-up" was
really a two-shot with the right third cut away.

## 009 — Pan the crop to the speaker, never accept the default offset (round 4)
Andy sits frame-left, Robbie frame-right in a two-shot. For a line spoken by the
frame-right host the crop must pan (`crop_x=560`), otherwise the left host fills the
frame and the shot is wrong.

## 010 — Identity is a voiceprint question, not a face-detection question (round 4)
Embed the line and the speaker's reference; require cosine ≥ 0.70; record the number.
Geometry checks cannot catch "wrong person, right framing".

## 011 — Cut tails to the measured release of the last word (round 4)
Transcript end timestamps stop at the vowel nucleus. "assets" clips to "ass…" and
"collateral" re-transcribes as "collaboration". Measure the RMS envelope and cut
50-150 ms later, inside the pause.

## 012 — ElevenLabs SFX arrive as headerless PCM (round 3)
`text_to_sound_effects` with `output_format=pcm_*` writes raw s16le 44.1 kHz stereo
under an `.mp3` name. Decode with `-f s16le -ar 44100 -ac 2`.

## 013 — Measure contrast against text pixels only (round 3)
Text is alpha 255. A scrim's alpha reaches ~242; thresholding at 200 makes the scrim
count as text and produces 798 false "text over face" violations and 1.01:1 contrast.

## 014 — Remove source furniture by crop, not by masking (round 3)
ON AIR bug, chyron, partner band, markets ticker: find the largest 16:9 window clear of
all of them (Sergey source: `y = 45…747` → 1248×702, upscale 1.54×; measure per source, rule 019). No matte, no clock.

## 015 — State every reference-timing delta, including the failing ones (round 3)
The first block ran 2.69 s over the reference because the hook is a complete sentence.
Say so, with the number and the reason. Do not round into compliance.

## 016 — Title card portrait is chosen, not grabbed
Eyes open, mouth closed, chosen from scored candidates, never the first frame of a clip.
Sample guest close-ups at 2 fps (hwaccel tier if any), keep frames with one centred face,
score eyes + sharpness + headroom − open-mouth, review the top 6 at full resolution, feed
the still via `cards.portrait_image`.

## 017 — Re-check every in-point with ASR on the exact cut
Whisper word starts can run ~0.2 s early, so a filler can sit inside an in-point. Transcribe
each exact cut on its own (`transcribe --start --dur`) AND check the envelope valley; move
the in-point to the valley after any fragment.

## 018 — Whisper suppresses fillers
Cross-check every candidate span against the source SRT (which keeps uh/um) before
accepting "no fillers". Use the SRT only as a filler/index check — never as a cut
point (rule 044).

## 019 — Crop is measured per source
Measure broadcast furniture (ON AIR bug, lower third) on each source and derive the crop
from it. Example (Zach Pandl source): ON AIR rows 14–27, lower-third border at row 754 →
crop 1280×720 at y=31 (1.5×). Never reuse another episode's crop.

## 020 — Single-host sessions (amended 2026-11-23, rule 046)
If only one host is in the episode, the structure is guest, host, guest — not two host
beats from that host. The user confirms which hosts are present; do not run a
host-presence check. State a one-host structure as a note, not a deviation you invent.

## 021 — introkit build pitfalls (fixed 2026-10-30)
Older introkit copies crashed on numeric `sfx.*_at`, on binary PCM decode and on a relative
`--out`. All three are fixed in this version; if you meet an old copy, pass an absolute
`--out` and string paths.

## 022 — Full-file QC ASR can invent tokens
Before re-cutting for a gate-6 diff, re-transcribe the isolated window (with and without
neighbours). AMENDED by 025: a token that does not reproduce is NOT automatically an
artifact.

## 023 — Shell hygiene
PowerShell variables are case-insensitive; `-f` formatting uses the locale decimal
separator; write Python to UTF-8 `.py` files instead of long one-liners.

## 024 — Hardware is optional; the deliverable is not
Accelerate decode, previews, Whisper and embeddings when a tier passes its self-test
(TOOLS.md §3); otherwise CPU. The final is always libx264 CRF 15 preset medium.

## 025 — Unreproduced ASR tokens are real until a human listens
Check the 300 ms after every out-point at high gain (+20..+40 dB envelope view and boosted
ASR, plus a positive control that extends past the out). End the dialogue in the valley
with a 30–60 ms fade; if the valley is short, end earlier and let the dip/sting cover it.

## 026 — Type size
Playfair Italic connective lines 110–130 px em at 1080p (x-height measured 58/63/69 px at
110/120/130 px), Anton claims cap height ≈133–171 px or larger for the keyword, Barlow
≈1.3× the earlier 62–78 px (≈81–101 px). Fit by paging phrases and balanced word-boundary
wraps inside a maxw set from measured face extents; never split or hyphenate words; shrink
only a single word that cannot fit. Every word fully landed before its page clears.

## 027 — Out-points come from the waveform (amended 2026-11-23, rule 044)
ASR word ends stop early and miss stray words ("I" after "come"). Subtitle or SRT
timestamps are not a substitute — never use them as cut points. Set `audio_out` at the
last voiced sample from the RMS envelope, ≈45 ms fade (`fade_out_ms`) before the next
sound, then run rule 025's 300 ms check. Confirm on the waveform, spectrogram, energy
decay, and ASR of a rendered test clip; err late; listen to the final audio.

## 028 — Hook compliance
A hook that states or implies price targets, returns, or performance ("will 10x", "going
to $X") is flagged in LINE_SETS.md and ships only with a safer backup hook (thesis or
narrative line, same speaker, same length ±0.5 s) cut and ready.

## 029 — Timing targets (reference IMG_9231, 33.483 s)
Hook ≈3.68 s, title card at ≈19.52 s, end card from ≈29.0 s, total ≈33.5 s. Report every
delta (rule 015).

## 031 — Viral hooks only (2026-11-16)
Every hook (and every other line of a set) must be provocative, surprising, quotable and carry
standalone value. Bland, definitional lines are rejected even when clean ("AI is a technology,
crypto is a technology"). Versions that used a rejected line are deleted and replaced with the
next version number of the same letter.

## 032 — B2 house style is the default (2026-11-16)
Reference: Lior Messika `ColdOpen_B2_original`. Music only (no logo / end card / sting / jingle),
1 s black tail, bed -26 dB RMS, per-shot auto duck (10.6 dB gap, 13 dB under the final guest
line, 180 ms cosine ramps), padded-ASR captions (0.5 / 0.8 s) with >= 150 ms per word and no flash
at page breaks. All built into `assemble` / `build` / `retime` (see SKILL.md "B2 house style").

## 033 — End fades start after the last word
An end fade never starts inside the last word: it starts at or after the acoustic end. Prefer a
faint trailing word over a clipped one. Check it on the delivered mp4 (`introkit edges`).

## 034 — Split lines to remove fillers, stutters, false starts and leftovers
A beat may be several pieces (up to 10) of the same speaker; cut each um / uh / repeat / false
start out at its dips with 20-40 ms fades. Start a piece after the previous word's leftover
(`assemble` WARNs `in cuts into`).

## 035 — Build once, hand off (amended 2026-11-23, rule 049)
One build per version, then hand off. Report the deliverable path as soon as the file is
written; send `qc` + `edges` afterward instead of holding the first report. The user does
the listening QC; no rebuild loops without his request.

## 036 — Stitching is part of the skill, and the episode audio is never changed (2026-11-19)
A stitched delivery (hook + subscribe bumper + full episode) is made by `scripts/stitch.py`, which
ships with this skill: ffmpeg plus the standard library, no app, no extra install, any OS. The
episode's audio is never changed — no dynamics chain, no levelling, no compression. `--dynamics`
exists only to reproduce the old Premiere track effect, is off by default, and is passed only when a
delivery is explicitly meant to reproduce it.

The subscribe clip is gain-matched to the episode (RULE 043, amended by RULE 049). The intro's
speech stays at unity gain. The raw subscribe masters can sit well below the episode (about
**-18.9 LUFS** against **-13.4** is the historical hole), so a measured gain-only copy of the
clip is the fix — never a dynamics pass, never an edit of the source file, and never a gain
change on the intro dialogue.

The default delivery stitch is `scripts/stitch.py`, or TheStitcher-V2 when `STITCHER_HOME` is set, with
`--no-dynamics`. `scripts/stitch.py` remains the portable path on any other machine (RULE 030).

## 037 — Zoomed shots are AI-upscaled (2026-11-18)
Clip shots whose crop zoom is above 1.15x are upscaled with Real-ESRGAN (realesr-general-x4v3
blended with wdn-x4v3 at 0.5 denoise): crop the zoomed source region, upscale 4x, then redo
push, grade and captions. Default at `build` after `introkit setup` (which installs the
extras and weights). CPU fallback if there is no GPU. `--no-upscale` skips it. Audio is
stream-copied and verified by hash.

## 038 — Beat structure; the guest always opens (2026-11-18, amended 2026-11-23, rule 046)
Default structures: `guest,andy,robbie,guest` when both hosts are in the episode, and
`guest,host,guest` when only one host is present (`assemble --structure` or `structure` in
sets.json). Beat 1 is still the guest. The user confirms which hosts are present. A mismatch
is a WARN, not a refusal.

## 039 — The face on screen is the person speaking (2026-11-18)
When two people share a frame, pick the face whose mouth motion follows the speech (diarization
plus a mouth check). The face sits on the side opposite the captions. A mid-shot camera cut is a
WARN: split the beat so each piece can be framed. Advisory only; the four QC gates are unchanged.

## 040 — Tight next-word endings use the B5 envelope (2026-11-18)
When the next word follows within ~20 ms, do not cut at the ASR word end. Keep full volume
through the word's real tail, fade the next word in about 30 ms, and fill with matching room
tone from the same recording before the final fade.

## 041 — Collapsed ASR repeats are found by energy and cut in the pause (2026-11-18)
If ASR collapses a repeated phrase (e.g. "do you, do you" heard as one "do you"), detect the
two bursts on the energy envelope and move the in-point into the pause. `vet --write` applies it.

## 042 — Duration floors (2026-11-18, amended 2026-11-23, rule 045)
Every guest segment must run >= 5.0 s. Host lines must not feel short — lengthen the same
line to a natural boundary or switch to a longer host segment (Illia B_v5 Andy was rejected
as too short). Never pad with silence. The check WARNs only.

## 043 — A stitched delivery is one level, and the episode is the reference (2026-11-19, amended 2026-11-23, rule 049)
Measure before you join. Use TheStitcher-V2 with `--no-dynamics` when `STITCHER_HOME` is set; the portable
command is `python scripts/stitch.py HOOK CLIP EPISODE -o OUT.mp4 --measure`, which prints
integrated loudness (LUFS) and true peak (dBTP) for all three files. The episode is the
reference — it is the long piece and its audio is never touched. Apply a measured plain gain
to the **subscribe clip only**, matched to the interview's LUFS. Do not change the intro's
speech level (unity gain). Stream-copy the interview; conform the short pieces with NVENC.

Report the before/after number per piece. The intro is cut from the same treated source, so
leave its speech alone and say so. The subscribe clip is the one that is genuinely quiet.


## 044 — Cut points come from the audio, never from subtitles (2026-11-23)
Never use subtitle or SRT timestamps as in-points or out-points. They are a useful index
for finding lines and (rule 018) for spotting fillers, but they are not a reliable source
for cut times.

Find word boundaries from the audio itself: the waveform, the spectrogram, the energy
decay, and ASR run on rendered test clips of the cut. Err late on word endings. A faint
tail of the last word is better than a clipped one (rules 011, 027, 033).

ASR cannot confirm an ending: it fills in partial words, so a transcript that looks
complete can still be clipped. Past misses, caught twice: "informati.." and "everyth..".
Listen to the final audio, not the transcript or the burned caption.

When you extend an ending to keep the last syllable, do not pull in a following filler.
Kash C_v6 kept "everything" and also brought in "uh". Listen after every extension.

## 045 — Line hygiene, hook bar, and duration floors (2026-11-23)
Remove stutters, fillers (uh, umm), doubled words ("what what") and trailing unrelated
words. Every line must be a complete, meaningful sentence with a hook value of 4 or more
out of 5 (hook strength on the line-selection checklist). A clean but bland line is still
rejected (rule 031).

Each guest segment must run at least 5.0 s (this raises the guest floor in rule 042).
Host lines must not feel short: a too-short host line is a defect.
Lengthen the same line to a natural boundary, or switch to a longer host segment. Never
pad with silence. Never cut a word short.

## 046 — Beat structure is confirmed by the user
When both hosts are in the episode, the structure is guest, Andy, Robbie, guest. When
only one host is present, it is guest, host, guest. The guest still always opens (rule
038). Do not run a host-presence check: The user confirms which hosts are in the episode.

## 047 — Proofread crypto names and tickers (2026-11-23)
Captions must spell crypto names and tickers correctly, with tickers in capitals. ASR,
and even a careful listen, can hear "SOL" as "soul" (Kash Dhanda C_v7). Use
`references/glossary.md` and `scripts/glossary.py` to check the on-screen words before
shipping. Script / chyron / glossary spelling beats ASR. The checker only reports; it
never rewrites a file.

## 048 — Seated vs standing framing (2026-11-23, amended 2026-11-24)

**Seated / podcast framing is the default. Standing / DJI is the exception.**

**Seated (default):** one speaker visible, zoomed faces, heads as high as possible with no crop
of the hair, each head centred at about 25% or 75% of the width, text on the side the face looks
toward, a faded black gradient behind the type and never a solid box. No tint or colour grade.
The signed-off reference (Lior Messika B2) is Playfair 100 / Anton 190 centred at `cy 540`, and
that is safe because the type sits opposite the visible face. Do not shrink a seated block to
dodge a chin.

**Standing / DJI (exception):** locked, static framing, no camera following, two-shots allowed.
Do not apply the seated face-tracking crop. Close shots are chest-up medium close-ups that show
the shoulders, chest and gestures, not tight floating heads. The top of the head sits only about
2-5% below the frame top, and the head is about 35% of the frame height. Reference: the signed-off
Raoul Pal frame from 2026-11-23. Because both people are visible and drifting, caption type here
must be anchored low (`caption_bottom`) so it clears both heads.

## 050 — One caption size per family, wrapped not shrunk (2026-11-24)
The type size is set ONCE per family for the whole intro (`caption_size_playfair`,
`caption_size_anton`). A line that does not fit `maxw` **wraps at a word boundary** into balanced
rows; it never shrinks on its own. Per-line shrinking is what produced a single intro whose
Playfair ran from **42.9 px to 92 px** (Raoul Pal C v8) and must never happen again. On standing
shots the block's ink is anchored to a bottom line (`caption_bottom`) instead of being centred on
`cy`, so it clears both heads. Solve the numbers with `scripts/caption_fit.py` (never by eye and
never with a render loop), then build once.

## 049 — Delivery, encoding, stitch, and audio fixes (2026-11-23)
Build the finished 1080p file directly. Do not spend time on preview versions to show
first. Version as `_vN` (C_v7, C_v8, …) and never overwrite an earlier file.

Report each deliverable as soon as its file is written. Send the extended QC afterward
instead of holding the first report until QC finishes.

When an NVIDIA GPU is available, use it: CUDA decode, h264_nvenc, GPU ASR. For long
videos, use smart trim (re-encode only the head GOP and stream-copy the rest). For short
intros (~20–30 s), a full high-quality NVENC re-encode is fine — do not spend long on
frame-exact stream-copy splicing of short clips.

Stitch the full episode with `scripts/stitch.py`, or TheStitcher-V2 when `STITCHER_HOME` is set.
using `--no-dynamics`, in the order intro, subscribe clip, full interview.
`scripts/stitch.py` remains the portable path (rules 030, 036). Apply a measured plain
gain to the subscribe clip only, matched to the interview's LUFS. Do not change the
intro's speech level (unity gain). Stream-copy the interview; conform the short pieces
with NVENC. Interview sources may start with a black frame — note it in the report; do
not silently trim it unless asked. Stitched output goes to `<Guest>_New_Intro.mp4` in
the guest folder (a later stitch is `_New_Intro_v2.mp4`).

To fix an intro's audio, rebuild it at identical cut times with the corrected source
audio and keep the picture. Speech stays at unity gain; music ducking stays gentle.

## 030 — Self-contained skill
Everything runs from the skill folder and its env folder: no absolute user paths, no
external apps. Diarization uses the bundled ONNX embedding path by default (no token).
This includes the stitcher (`scripts/stitch.py`, RULE 036): a delivery must never depend on a
separate application being installed on one particular machine.


## 051 — Scan the source for holes before cutting anything (2026-11-24)

Four of the 27 episodes under `K:` carry a source-*audio* repair (Jack, Ryan, Yannik, Tristan),
plus the episode master, and every one of them re-derived the
same scan by hand. (Evan Van Ness is a fifth repair, but frame-level: logo and burned-in text.)
Run `scripts/scan_defects.py --src <recording>` before selection: 10 ms blocks, dropouts
(RMS < 30 int16 = -60.7 dBFS for >= 0.5 s), clipping runs (|s| = 32767), and static (HF lift
>= 6 dB over the local +-50 s median **and** spectral flatness >= 0.45, for >= 0.2 s).
890 s of audio takes 5 s.

- A dropout >= 1 s that swallows speech is gone: cut it, or donor-splice it with equal-power
  30 ms crossfades placed **across** the boundary. Fading *inside* the replaced region
  reproduces the defect's own step as a click (measured: the step came back at the edge).
- Static under intelligible speech is repaired, not cut: adeclick (10 and 40 ms) + 5.5 kHz
  lowpass, spliced back with 10 ms raised-cosine crossfades. Measured 143 clicks -> 0.
- Read the per-30 s zero-sample bins, not the file's average RMS: Tristan's whole-file RMS
  hides a 0.9 s hole at 8:05 that the 480-510 s bin names (3.1 % zeros).
- Flatness separates a fricative from hash: speech peaks at 0.18 over 12 000 blocks, the
  documented electrical burst measured 0.70. A "static" hit with no flatness is a consonant.
- Validate the detector against a known defect before you trust a clean report.

## 052 — A head-top allowance is a number, not a boolean (2026-11-24)

Across 71 QC runs and 549 sampled shots, **67 %** have the head above the frame line (median
48 px, 5.1 % over 100 px = 9.3 % of frame height), and the head-top gate produced **zero**
fails, because `allow_top_touch` was set on 474 of them. The flag is per-shot, always used as
a list, and carries no budget — so it silently converts a gate into "always pass".

- When you set `allow_top_touch`, put the measured `worst.top` in the QC note, in px and %.
  `qc` now prints it every run: `deepest head crop N px (X% of height)`.
- More than 8 % of frame height (86 px at 1080p) is "head cut": get it approved, do not log
  it as a pass. Nothing under `K:` exceeds 11.6 %; `qc` appends "OVER the 8% budget" when a
  run goes past it.
- The chin margin is the other half of the same problem (55 fails): a face pushed down to
  clear the hair loses the chin instead. Solve top and chin together, or change the source
  framing.

## 053 — Extra faces are a two-shot fact, not a build defect (2026-11-24)

The face gate fails any frame that does not have exactly one face. It fired **77 times**
across 71 QC runs — on episodes that shipped. In a standing / DJI walk-and-talk or a
programme two-shot the second person is in frame by design (rule 048).

- Do not chase a single-face frame by tightening the crop until the subject is a floating
  head; that trades a real composition for a gate.
- For those pieces set `allow_extra_faces: true` (or a list of shot numbers / speakers /
  roles, like `allow_top_touch`). The gate then reports the extra faces without failing and
  keeps every other check: the subject's face must be clear, the caption crosses no face, no
  other person overlaps the caption strip, and a frame with no face still fails.

## 054 — The last word is what actually ships broken (2026-11-24)

Across 71 QC runs, `last word clipped: out -N ms vs acoustic end` is the most repeated
failure (31 times), and another 139 of 549 shot grades are LISTEN.

- `audio_out` comes from the measured acoustic end, never from an ASR word end (rules 027,
  044). Then run `metrics` and require `end_margin_ms >= 40` on every shot before the build.
- `vet --write` shrinks the fade to 8 ms instead of eating the word. If a rebuild moves any
  in/out point, re-run vet: the fix does not survive an unrelated edit.
- A file with a shot below -150 ms is not a deliverable, whatever else passes.

## 055 — Stop hand-patching what the kit already does (2026-11-24)

Six builds carry a copy of `patch_nologo.py` (Dan Tapiero, Eric Saraniecki, Kash Razzaghi,
Lior Messika, Quinn Thompson, Rebecca Rettig) that pops the end card, sets a 30-frame black
tail, deletes `sting_at` and rebuilds the music fade by hand — all of which `no_logo: true`
and `end_audio_plan` do now. Farokh carries `compose.py`, which copies `tail_env.tone`
between builds by hand; `vet --write` derives it.

- Before writing a patch script, check the project keys: `no_logo`, `music_fade_out`,
  `duck_hold`, `allow_top_touch`, `face_roi`, `caption_bottom`, `caption_size_*`,
  `music_rms_db`, `duck_gap_db`.
- A patch that recomputes `frames` from `audio_out` must also clamp the last word's `e`, or
  the caption overruns the cut.
- A patch is evidence that the kit was missing something. Add the key, then delete the patch.

## 056 — Verify a stitch at the joins, not at the duration (2026-11-24)

Five episodes carry a bespoke `verify_stitch.py` because a duration check passes while a join
clicks. Per join, measure: 50 ms of PCM either side (no step, no gap, no doubled buffer), the
section's own LUFS and true peak, and the sum of the parts against the delivered duration
within one frame. Keep the episode audio bit-identical (md5) and report the gain applied to
each section (rule 043).

- The join is exactly where the bumper's level-matching gain shows up: check the seam, not
  the programme average.

## 057 — Never overwrite a delivered file (2026-11-24)

Evan Van Ness shipped `- fixed.mp4`, then `- fixed v2.mp4`, and proved v1 was untouched by
SHA256, length and nanosecond mtime. Every QC note under `K:` keeps a "superseded files
(kept, not overwritten)" line, and versioned deliverables run to v10 (Farokh, Yannik).

- Write `_vN`; never edit a file the user has already seen. When a prior asset was wrong,
  say which asset and prove the old bytes still match.
- When a supplied asset disagrees with the source (Evan's logo PNG against the native badge),
  measure both and trust the source: the PNG was 301x65 at (1608,10) and the native badge had
  a different geometry, so v1 repaired the wrong box.

## 058 — A line set carries its verdict (2026-11-24)

The Armani line sets record, per set, the duration, the pass/fail and **why it was rejected**
("'attack' and 'three' clipped, two Andy beats in a row"), plus the source in/out of every
line. Three of four sets failed for specific, fixable reasons; the fourth passed.

- Keep that file per guest (`LINE_SETS.md`): set, duration, verdict, reason, per-line source
  in/out and any gain applied.
- A set with no recorded reason for rejection will be rebuilt by the next session.

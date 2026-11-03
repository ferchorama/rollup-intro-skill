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
accepting "no fillers".

## 019 — Crop is measured per source
Measure broadcast furniture (ON AIR bug, lower third) on each source and derive the crop
from it. Example (Zach Pandl source): ON AIR rows 14–27, lower-third border at row 754 →
crop 1280×720 at y=31 (1.5×). Never reuse another episode's crop.

## 020 — Single-host sessions
If only one host is on camera, both host beats come from that host; confirm with the
voiceprint block structure and state it as a deviation from the reference.

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

## 027 — Out-points come from the waveform
ASR word ends stop early and miss stray words ("I" after "come"). Set `audio_out` at the
last voiced sample from the RMS envelope, ≈45 ms fade (`fade_out_ms`) before the next
sound, then run rule 025's 300 ms check.

## 028 — Hook compliance
A hook that states or implies price targets, returns, or performance ("will 10x", "going
to $X") is flagged in LINE_SETS.md and ships only with a safer backup hook (thesis or
narrative line, same speaker, same length ±0.5 s) cut and ready.

## 029 — Timing targets (reference IMG_9231, 33.483 s)
Hook ≈3.68 s, title card at ≈19.52 s, end card from ≈29.0 s, total ≈33.5 s. Report every
delta (rule 015).

## 030 — Self-contained skill
Everything runs from the skill folder and its env folder: no absolute user paths, no
external apps. Diarization uses the bundled ONNX embedding path by default (no token).

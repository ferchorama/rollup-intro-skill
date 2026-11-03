# QC — every gate and how to measure it

## 0. The consolidated run (use this; sections 1-9 explain what it measures)

```bash
python scripts/introkit.py vet --project P.json --words W.json --write     # once per line set
python scripts/introkit.py qc  --final out/NAME.mp4 --project P.framed.json --out out/qc.json
```

| Gate | One measurement | FAIL when | Fix hint printed |
|---|---|---|---|
| FORMAT | ffprobe + moov position | not h264 / size / fps / SAR 1:1 / bt709 / yuv420p / faststart / AAC 48k stereo | rebuild with this kit |
| AUDIO | ebur128 on the MP4 + timeline | I outside -14 +-0.5, TP > -1, sting tail < 2.45 s, music fade over the sting or before the last word ends | vet --write / drop music_fade_out |
| FRAMING | YuNet, every 3rd frame + first/last of each shot, 960x540 | head+25 % < 0 (unless allow_top_touch), chin < 5 % h, side < 5 % w, != 1 face, caption over face, caption in 60 px strip | crop / zoom / maxw / matte |
| SPEECH | one ASR per shot on the final mix + source envelope | filler or stutter, out > 150 ms before the acoustic end, pause > 0.35 s | vet --write / another span |

WARN rows do not fail: `LISTEN` (out within -150..+40 ms of the acoustic end: listen to that
cut), tail pause > 0.35 s. Results are cached per shot in `qc_cache.json`; `--no-cache` re-runs
all. The contact sheet (`<out>_contact.jpg`) and waveform (`<out>_wave.png`) are the human
review. Selection-time checks (`vet`, cached in `vet_cache.json`): per-clip ASR with VAD on
and off, acoustic end + proposed out, dip grade vs room floor, headroom.
Not gates any more: multi-model ASR, full-file ASR, every-frame faces (`--classic-faces`
diagnostic), voiceprint (diagnostic). Why: `LESSONS_LEARNED.md` D7.


Report the measured number for each line. If you did not measure it, do not claim it.

## 1. Loudness

```bash
ffmpeg -nostats -i FINAL.mp4 -af ebur128=peak=true:framelog=quiet -f null - 2>&1 \
  | grep -E 'I:|LRA:|Peak:'
```
Gate: integrated **−14.0 ±0.5 LUFS**, true peak **≤ −1.0 dBTP**, measured on the delivery
file. Limiting before encoding is not enough: AAC adds ~0.3 dB of inter-sample overshoot.

## 2. Script integrity

Re-transcribe the delivered audio (VAD on) and diff against the intended script.

```bash
python scripts/introkit.py transcribe --src FINAL.mp4 --out qc/            # VAD on
python scripts/introkit.py transcribe --src FINAL.mp4 --out qc/novad.json --no-vad   # rule 025
```

Pass conditions:
- every word of the script present, in order
- **zero** words outside a dialogue segment's time range (words over a music-only region are
  ASR hallucination — assert they are absent, do not "explain" them)
- no fillers, no repeats

## 3. Text over faces

For every frame:
1. text pixels = overlay alpha **== 255** (threshold 250; the scrim sits below)
2. bounding box of those pixels
3. faces = Haar frontal + profile cascades on the composited frame, filtered to
   `centre_y < 0.68·H` and `width ≥ 140` (kills the microphone and desk false positives)
4. violation if the intersection area > 4 % of a face box

Gate: **0 violations**.

## 3b. Faces inside the frame (`qc --faces`)

```bash
python scripts/introkit.py qc --final FINAL.mp4 --faces --project P.framed.json --out qc.json
```

`qc --faces` checks every clip frame of the delivered file. YuNet runs at half scale, and
on a miss it runs a lower-score pass. If both miss, it uses the face box from the `frame`
sidecar mapped through that frame's crop. A frame with none of these counts as
**NO FACE** and fails. The margins are judged on a 5-frame median of the face track, which
removes the 2-4 % frame-to-frame jitter of the detector box. The raw per-frame minimum is
printed beside it.

A frame **FAILs** if:
- head top + 25 % of face height < 0 px (the hair is cut);
- chin < 5 % of the frame height from the bottom;
- left or right < 5 % of the frame width;
- or no face is found.

It prints the per-shot minimum top, chin, left and right margins and the counts by
detection path. `qc.json → faces` holds the per-frame margins. Gate: **every shot PASS**.
The exit code is 1 on a FAIL. Fix a failing shot by re-running `frame`, which backs off
automatically, or by choosing another span. Never ship a FAIL.

Note: the Haar profile cascade from older notes is not available in the bundled OpenCV 5
headless build. The low-score YuNet pass covers three-quarter faces. A full side profile
is reported, not guessed.

## 4. Legibility

Contrast between text pixels and the plate immediately behind them (dilate the text mask,
take the ring outside the glyphs):

```
ratio = (L_text + 0.05) / (L_background + 0.05)   # WCAG relative luminance
```
Gate: ≥ **3.0:1** for display type (≥ 4.5:1 for body size). Measured on this job: min 3.77,
mean 6.75.

## 5. Speaker identity (the one everyone forgets)

Geometry cannot tell you *who* is talking.

```
cos(embedding(line), embedding(speaker_reference)) ≥ 0.70
```

Embed with the same model used for diarisation (e.g. WeSpeaker ResNet34 ONNX). Compare
**within the session** — cross-recording similarity is unreliable, within-session block
structure is not. On this job two host lines scored 0.22 to each other and 0.73 within their
groups.

Then confirm visually **at full frame width**, at both the in-point and out-point.

## 6. No split or hyphenated words

By construction: phrases are paged and wrap only at word boundaries; no word is split. Assert by printing the display word
list per cut and checking no token contains a soft hyphen and no line exceeds its box.

## 7. Broadcast clock and baked graphics

Crop a 100 px strip off the top and the bottom of the **rendered** file and look at it.
Gate: no ON AIR bug, no chyron, no partner band, no markets ticker, no timecode.

## 8. Section timing

Compare against the reference teardown. Where a beat is outside ±1 s, state the delta and
the reason — do not quietly round it into compliance. This job shipped two out-of-gate
deltas (first block +2.69 s, title card +1.81 s) and documented both, with the cause (the
hook is a complete sentence; trimming it to the reference's 3.68 s would orphan the clause).

## 9. Frame-level sanity

- total frames == sum of segment frames
- final frame is the end card, not black
- mean RGB of the end card is not neutral grey (it should be navy/gold)

## Report template

```markdown
| gate | measured | spec | pass |
|---|---|---|---|
| integrated loudness | -14.0 LUFS | -14 ±0.5 | ✓ |
| true peak | -1.2 dBTP | ≤ -1.0 | ✓ |
| text over faces | 0 violations / 948 frames | 0 | ✓ |
| faces in frame (`qc --faces`) | all shots PASS; min top 3.7 px raw / 17.5 smoothed | head+25 % ≥ 0, chin/sides ≥ 5 % | ✓ |
| speaker identity | 0 mismatches; min cos 0.71 | ≥ 0.70 | ✓ |
| legibility | min 3.77:1 mean 6.75:1 | ≥ 3.0 | ✓ |
| script diff | exact, 0 unintended words | exact | ✓ |
| baked graphics | absent | absent | ✓ |
| section timing | 2 deltas, documented | ±1 s | ✗ (stated) |
```

## Out-point tail (rule 025/027)
For every clip, view the 300 ms after `audio_out` at +20..+40 dB and transcribe it boosted;
any voiced energy or token there fails the gate until a human listens.

## Speaker gate (gate 4)
`introkit.py voiceprint --src SRC --span NAME=IN:DUR ... --enroll guest=.. --enroll host=..`
Each line ≥ 0.70 to its own speaker's reference, and clearly above the other speaker.

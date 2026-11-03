# Troubleshooting — everything that actually went wrong

Each entry is a failure paid for once. Symptom → cause → fix → how to prove it is fixed.
Ordered by how expensive it was.

---

## 1. "It shows Andy while Robbie is talking"

**Symptom** a host question plays over the other host's face.
**Cause** two separate mistakes stacked:
  a) The show's camera does **not** reliably cut to whoever is asking. During Robbie's
     question the programme stayed on Andy for the whole 5 s.
  b) Every frame check was done on a **1248 px working crop starting at x=0**, so the
     second host — seated frame-right — was sliced off the right edge and never seen.
     The "close-up of Andy" was really a two-shot with the right third cropped away.
**Fix** sample the frame at **full 1920 width** for every candidate in/out. If the speaker
is outside the crop, pan the crop (`crop=1248:702:X:45`) until they are inside it with
margin, and re-place the type on the opposite side.
**Prove it** voiceprint the line (`references/qc.md` §Speaker), then eyeball the rendered
frame at full width.

## 2. The hook's last word was cut — "real world ass…"

**Symptom** the final consonant of a cut's last word disappears; ASR of the render comes
back as a different word ("assets" → "ass", "collateral" → "collaboration").
**Cause** cutting on the **transcript's** word-end time. ASR end timestamps stop at the
vowel nucleus; the fricative/plosive release continues 50-150 ms past it.
**Fix** never cut on the transcript time. Measure the waveform: `ffmpeg -af silencedetect`
or an RMS envelope at 10 ms resolution, find where the release actually decays, and put the
cut **after** it, inside the following pause.
**Prove it** re-transcribe the rendered file; the word must come back intact.

## 3. Loudness passed, true peak failed

**Symptom** master measures −14.0 LUFS but −0.7 dBTP after encoding; the spec is ≤ −1.0.
**Cause** AAC adds inter-sample overshoot of ~0.3 dB. A sample-peak limiter at exactly
−1.0 leaves no headroom.
**Fix** limit to −1.75 dBTP before encoding, then **re-measure the encoded file** and
re-trim the gain in a convergence loop (loudness and peak fight each other, so iterate
until both hold). See `scripts/introkit.py::stage_master`.
**Prove it** `ebur128` on the final `.mp4`, not on the intermediate `.wav`.

## 4. ElevenLabs SFX came back unreadable

**Symptom** `Header missing`, `Decode error rate exceeds maximum`, zero output.
**Cause** `text_to_sound_effects` with `output_format=pcm_*` returns **headerless s16le
PCM** but still writes an `.mp3` filename. ffmpeg sniffs the extension and fails.
**Fix** decode explicitly: `ffmpeg -f s16le -ar 44100 -ac 2 -i <file>`. Confirm with
`file_size / (44100 × 2 ch × 2 B) ≈ duration`.
**Prove it** decode to f32 and check the duration matches the request.

## 5. Two type lines rendered on top of each other

**Symptom** stacked lines collide; the block looks like a ransom note.
**Cause** word tiles were positioned with
`y = baseline + (bbox_top + bbox_bottom)/2`. PIL's `getbbox` measures from the **top of the
layout box**, not the baseline, so every word was pushed down by its own ascent.
**Fix** `y = baseline − ascent + (bbox_top + bbox_bottom)/2`.
**Prove it** render a line of 150 px caps above a line of 46 px serif and look at the gap.

## 6. The QC said 798 text-over-face violations

**Symptom** absurd violation count on a cut that looks fine.
**Cause** the text bounding box was taken from `alpha > 24` of the overlay PNG — which
includes the **scrim**, a full-frame gradient up to alpha 242. The "text box" was the whole
frame, and the contrast metric compared scrim pixels with themselves.
**Fix** text pixels are `alpha == 255`. Threshold at 250 for the box and the mask; keep the
scrim below 245.
**Prove it** run the check; the count should collapse from hundreds to single digits.

## 7. Contrast read 1.01:1 on legible text

Same root cause as #6. Fixed with the same threshold change. Then the honest floor on this
job was 3.8:1 (large display type, still above the 3:1 AA threshold).

## 8. The clip showed the guest, then the host, mid-sentence

**Symptom** a 5 s cut flips from one person to another.
**Cause** the source is a **switched programme cut**, not an ISO. The vision mixer changes
source inside your selection.
**Fix** check the in-point *and* the out-point and every ~1 s between; if the shot changes,
shorten to the stable span or choose a different moment.
**Prove it** sample 10 frames evenly across the clip and confirm one subject.

## 9. A "dual-mono" source was assumed to be isolated mics

**Symptom** the brief asks for isolated per-speaker audio that does not exist.
**Cause** both channels of the "stereo" track are byte-identical — one mixed programme bus.
**Fix** check per-channel RMS/peak (`astats` per channel). If identical to 6 decimals there
is one bus. Diarise instead of separating, and state the limitation in the QC report.
**Prove it** `ffmpeg -af astats=measure_perchannel=...` — equal values on both channels.

## 10. Burned-in chyron and ticker leaked into the frame

**Symptom** the ON AIR bug or the markets ticker appears in the render.
**Cause** the crop was chosen from a single eyeballed frame; the furniture is a fixed band
but the safe window is smaller than it looks.
**Fix** measure the bands: crop a horizontal strip at the top and bottom, read off the pixel
rows, then use the largest 16:9 window between them. On this job that was
`y = 45…747` → `crop=1248:702:X:45`, upscaled 1.54×.
**Prove it** extract frames from the *rendered* file and look at the top and bottom 100 px.

## 11. The title card read as a eulogy

**Symptom** "do not end on a black-and-white frame".
**Cause** a grayscale portrait on black is the visual grammar of an obituary.
**Fix** keep the portrait in **colour**, lift contrast slightly, feather it into a navy-gold
gradient, and add a slow 1.045× push so nothing is dead still. End card: deep navy with a
gold glow, not white-on-black.
**Prove it** sample the last frame; check mean RGB is not neutral grey.

## 12. The word "24-7" rendered as "24 -7"

**Symptom** a hyphenated display token splits into two words with a space.
**Cause** ASR emits `24` and `-7` as separate tokens.
**Fix** post-process: any token starting with `-` merges into the previous token and the
timing span extends.
**Prove it** print the display word list for every cut; no token should start with `-`.

## 13. Music and dialogue fought each other

**Symptom** the bed is audible but the speech feels buried, or the bed vanishes.
**Cause** a static gain cannot serve both.
**Fix** build a speech mask from the cut list, smooth it with an asymmetric one-pole
(50 ms attack, 300 ms release) and duck by a fixed amount (~14 dB). Release in every black
gap so the track pumps with the cut.
**Prove it** measure short-term LUFS inside a speech section and inside a black gap; the
difference should be ~10-14 LU, not 0 and not 25.

## 14. Frames were missing or black when sampling the render

**Symptom** `ffmpeg -ss 23.96 -i out.mp4 -frames:v 1` returns black or the wrong section.
**Cause** fast seek lands on a keyframe when the file has sparse keyframes.
**Fix** for verification use the select filter on decoded frames
(`-vf "select='eq(n\,N)'"`) or put `-ss` **after** `-i`. For delivery, encode with
`-g 60` so verification seeks are accurate.
**Prove it** the sampled frame should match the cut list time.

## 15. Overlay PNG sequence ate the disk

**Symptom** a 1080p RGBA sequence for 30 s is ~7 GB uncompressed.
**Cause** writing full frames with no compression strategy.
**Fix** save with `optimize=1` (text-on-transparent compresses hard, ~200-400 KB/frame here
→ ~250 MB) and delete the sequence after the mux.

## 16. The re-transcription invented words at the end

**Symptom** QC transcription appends "Bait. Bait." or "Thank you." over the music outro.
**Cause** ASR hallucination on music-only regions, worsened by `--no-vad`.
**Fix** run the QC pass with VAD **on**; assert the trailing words align to real speech
windows in the cut list. Anything outside a speech window is a hallucination, not a defect.
**Prove it** every transcribed word must fall inside a dialogue segment's time range.

## 17. Two hosts clustered as one voice

**Symptom** diarisation returns one "host" cluster; you cannot tell who asked what.
**Cause** hosts share register and mic; the clustering threshold merges them.
**Fix** don't trust the cluster. Embed each candidate line and each host's reference
recording separately, then compare **within session** (line↔line similarity is the reliable
signal; cross-recording similarity is not). On this job two lines scored 0.22 to each other
and 0.73 within their groups.
**Prove it** print the full cosine matrix; look for a block structure, not absolute values.

## 18. The render looked fine but the speaker was still wrong

**Symptom** all checks pass, the client still says "that's the wrong person talking".
**Cause** every automated check verified *geometry* (box vs face) and never *identity*.
**Fix** identity is a **voiceprint** question, not a face-detection question. Add: embed the
line, embed the speaker's reference, assert cosine ≥ 0.70, and record both numbers in QC.
Then confirm visually at full frame width.
**Prove it** the number is in `QC_REPORT.md`.

## Added 2026-10-30
- **faster-whisper fails inside PyAV (`metadata_errors`)**: PyAV 19 is incompatible; the
  locks pin `av<19` and introkit decodes audio with ffmpeg instead.
- **Overlay stage slow**: PNG `optimize=1` was the cause; frames are now written with
  `compress_level=1` on a thread pool (lossless, bit-identical output).
- **`--device X` ignored**: the forced tier failed its 1 s self-test; `introkit.py hw`
  shows why; the run continues on CPU by design.
- **CUDA DLL/.so not found**: rerun setup with `-Cuda yes` / `--cuda yes`; introkit loads
  the CUDA wheels from the env venv; otherwise Whisper falls back to CPU int8.
- **Diarization merges two hosts**: enroll each host (`--enroll hostA=.. --enroll hostB=..`)
  or pass `--speakers 3`.

# Lessons learned - session log

Hard-won lessons from real delivery sessions, written so the next run does not pay for them
again. Read this together with `RULES.md` (numbered rules) and
`references/troubleshooting.md` (failure entries); this file does not repeat those, it adds
to them. Names are worked examples only (guest = a WisdomTree CIO, host A = curly-haired
host, host B = dark-shirt host); every number below was measured in the session it comes
from.

Session 1 - 2026-10-30, guest cold open: v1 -> v2 -> v3, then alternates ALT_A, ALT_B,
ALT_B2 (v3 hook + ALT_B body) and ALT_B3 (ALT_B2 + music fade before the sting + full-length
sting). Approved deliverable: ALT_B3 after a SAR/DAR fix (section B9).

---

## A. Editorial lessons

### A1. No two opening segments may say the same thing
v1 opened with two guest lines that both made the "bubble" point. The second beat must add
something new (a host question, a turn, a number), never restate the hook.

### A2. Host questions are complete and self-contained
A host beat must parse cold, as a full question. A trimmed stub ("And so what about...")
was rejected in v1 and replaced in v2. This extends the sentence-boundary rule in
`references/line-selection.md`: never cut a host question internally, and never start it
mid-clause either.

### A3. The hook is a cold, concrete, shocking number or claim
What the client approved as hooks: a figure that needs no context ("$8-9 trillion earning
zero"), a velocity claim ("60 apps in 5 months"), a vivid recollection ("bond yields were
negative... it was insane"). Weak: process descriptions, product names, anything that needs
the previous sentence. (Still apply hook compliance, rule 028, to price/performance claims.)

### A4. Zero stutters, repeats, self-corrections or trailing false starts
Every one of these was caught by the client or by the verbatim ASR diff:
- a trailing false start "We're..." after the out-point that played as "where" (fixed by
  moving `audio_out` 0.30 s earlier, into the gap before it);
- "what, what" across an in-point (in-point moved into the dip after the first "what");
- self-correction "tokenization efforts... the tokenized equities efforts" (line ended at
  the first "efforts?" inside the -55 dB gap before the corrective "the");
- "Is, is" (a stuttered restart; it must not survive inside a kept span).
A natural restatement inside a clause ("into account, into his analysis") is the speaker's
own phrasing and was accepted. Rule of thumb: a repeat of a word or a restart of a phrase
is a defect; a parallel phrase that adds meaning is not.

### A5. Hidden bursts at in-points
"Like", "the", "that", "but" and the decay of the previous word often sit 10-60 ms before
the first intended word and are invisible in the transcript. Seen here: a "The" burst
ending 50 ms before the hook's first word; "later" running straight into "It" so "but" was
still audible at in-point x.94 and gone at x.98. Check every in-point with windowed ASR in
both VAD modes AND on the waveform (section B2).

### A6. Quiet trailing words get buried under the music
A line that trails off ("...at the moment", ~15 dB below the body of the sentence) reads as
a mumble once the bed comes up. End on the last crisp, stressed word instead ("...Tesla.").

### A7. Cut internal pauses over 0.35 s - inside the silence
Gaps > 0.35 s inside a line were removed with a micro-cut (a split into two shots, i.e. a
jump cut on the same framing) placed entirely inside the silence, never on a word edge.
The cut is invisible when both halves keep the same crop.

### A8. The speaker must be on camera while speaking
Map the source's camera cuts for every candidate before selecting it: the switched
programme cut often sits on the listener. A strong guest line (the "SpaceX" line) was
dropped because the camera was on a host for most of it. Rule 007 covers in/out checks;
the lesson is to map cuts BEFORE shortlisting so you do not fall in love with an
off-camera line.

### A9. Names
- Guest name spelling comes from the on-air chyron, not from the file/folder name (the
  folder name carried a misspelling; leave folder names as they are, fix the captions and
  card).
- Identify hosts from transcript cues (people addressing each other by name) plus
  voiceprint, then confirm the mapping with the user before shipping.

### A10. Structure and captions
- Order that was approved: **guest > host A > host B > guest** (hook, two host questions,
  guest button), then the logo, with **no title card** (the client's choice; state it as a
  deviation from the reference teardown).
- Captions use first-letter caps, consistently across all beats.

### A11. Alternates and mix-and-match
When asked for alternates, vary the hook and the theme, not just trims. Expect the client
to combine segments across versions (ALT_B2 = v3's hook + ALT_B's body). Keep every
version's project JSON, words JSON and line-set table so segments can be recombined
exactly, and verify the junction (ASR across it, voiceprint of the transplanted segment).

---

## B. Technical lessons

### B1. ASR: VAD and no-VAD, full file and windowed
Run the QC transcription in four views: full deliverable VAD on, full deliverable VAD off,
and each cut windowed (with and without ~0.3 s of neighbours) in both modes. Known
behaviour of the no-VAD pass on this show: "Warsh" -> "wars", a clipped "Tesla" -> "tus" /
"test", a quiet "fund" -> "marketplace", and an invented token over the end card. Before
dismissing a full-file token under music as an artifact, look at the waveform at that time
(rule 025): in this session one "artifact" was real voiced decay of the previous word
(-25 to -27 dB) and needed an out-point move.

### B2. Micro-shifting in/out-points
- 10-30 ms in-point shifts are often enough to dodge a residual word or burst. Move, then
  re-run the windowed ASR in both modes.
- Cut only in envelope dips of about **-45 dB or lower** (10 ms RMS). A cut in a -34 dB
  dip worked once only because the next sound was a plosive onset; do not rely on it.
- Out-point check: an `audio_out` 90 ms short of the speech end turned "Tesla?" into
  "tus"; extending to the measured end (+130 ms) fixed it. Measure the speech end on the
  envelope; do not trust the ASR word end (rule 027).

### B3. Head-safe crops
- Keep the head box plus a **+25 % margin** inside the frame (gate: margin top >= 0). When
  the guest leans up or forward, the default y offset clips the head: use `crop_y = 0`
  (e.g. a 1366x768 window at y 0).
- Avoid the broadcast furniture zones measured per source (ON AIR bug, chyron, monitors in
  the set). On wide shots the chyron starts higher; crop tighter and lower the caption
  `maxw`.
- Keep caption glyphs out of the **60 px top and bottom safe strips** (gate: 0 glyph
  frames, glyph = overlay alpha > 200 and gray > 70). A 4-line stack at large sizes spanned
  y 46-986 and failed; reducing sizes ~12 % fixed it.
- Never let text cross a face (existing gate); `maxw` per shot is the main tool.
- Contrast: steady-state >= 4.5:1. Values below it only during the first ~6 frames of a
  caption fade-in are expected and acceptable; report them as such.

### B3b. Face zoom: never remove it globally, and never zoom on the centre (2026-11-02)
- **Jeremy's zoom removal was per run, not a rule.** The intros used to zoom in on faces.
  On one Jeremy Schwartz run a clip cut the top of his head, and the zoom was taken out of
  every clip. The project JSONs for that delivery have no `punch` key at all (CUTLIST.md,
  LINE_SETS.md). The client wants the zoomed faces back.
- **The kit's old `punch` was a centre-anchored `zoompan`.** It zooms toward the frame
  centre, so a guest whose head sits near the top edge loses the hair line within a few
  frames. It also drifts by whole pixels, which shows as judder. This is what cut the face.
- **Rule:** zoom per shot, anchored on the measured face, and gated per frame.
  - `introkit.py frame` sets each shot's crop and push from the face. It keeps the hair
    +30 %, chin +10 % and sides +15 % margins and 60 px of headroom where the source
    allows, and caps the zoom at 2.0×.
  - `qc --faces` must PASS on the final.
  - A guest framed tight at the top of the source (Jeremy: hair 16-50 px from the source
    top) gets the largest zoom that passes. On the ALT_B3 test that was 1.31-1.42× with
    no push, against 1.89-2.0× with a 6 % push for the hosts. The fix is to accept that
    for the guest, not to remove the zoom for everyone.
- **YuNet's box grows on a magnified face.** On the delivered frame it reads about 4-6 %
  taller and higher than on the source. Margins predicted from source boxes were 15-40 px
  optimistic and failed qc by up to 13 px. `frame` now re-detects at delivered scale and
  backs off. Single-frame box jitter of 2-4 % is why `qc --faces` judges a 5-frame median
  and prints the raw minimum beside it.

### B4. Face detection fallback
Frontal face detection misses three-quarter and profile views. The head-margin and
text-over-face checks need a profile-face fallback; a frame with no detected face must be
reported, not counted as a pass, otherwise a missed face silently "passes".

### B5. Voiceprint scores depend on the references
Same line, different reference clips: 0.54-0.79. The hook scored 0.535-0.562 with short
references and 0.69-0.73 with longer ones. Attribution was always right (next-best speaker
<= 0.37). Therefore:
- store the reference spans used (source in-point and duration per speaker) next to the
  QC JSON;
- report the margin to the wrong speaker, not only the absolute score;
- run voiceprints on music-free source audio (concatenate the source spans of each shot),
  not on the mixed deliverable, which adds noise.

### B6. True peak is measured too early by the kit
The kit converges loudness/peak on the WAV and its QC reads the pre-AAC peak; AAC 320k adds
overshoot. Re-measure the encoded file and, if TP > -1.0 dBTP, remaster: lower the limiter
knee step by step, re-encode, re-measure the MP4, until TP <= -1.1 dBTP and integrated is
within 0.1 LU of -14. Gate on the delivered file only (qc.md section 1).

### B7. The sting is truncated by the end card
The kit uses only the first 2.4 s of the sting and truncates it to the remaining timeline.
With `sting_at = total - 1.93 s` the file ended while the sting was still ~-38 dB below its
peak (an audible chop). The bundled sting decays to about -63 dB relative by 2.4 s. Rule:
make the end card long enough that `total - sting_at >= 2.45 s` (in the approved cut: end
card 81 frames, sting from 18.437 s to 20.837 s, file ends 21.067 s).

### B8. Music fade-out before the sting (client preference)
Without a fade, the music duck releases into the final black and the bed swells under the
sting. The client wanted the music to fade and the sting to play alone. The kit has no
option, so it was done as a runtime patch of `stage_audio`, without editing the kit:
1. Load the kit module and get `stage_audio`'s source with `inspect.getsource`.
2. Insert, right after the music normalisation line, a cosine gain over [F0, F1]:
   `g = 1 for t < F0; 0.5 + 0.5*cos(pi*(t-F0)/(F1-F0)) for F0 <= t < F1; 0 after F1`,
   then `mus *= g`. F1 must be before the sting starts (used: F0 17.00 s, F1 18.40 s with
   speech ending 18.00 s and the sting at 18.44 s).
3. The kit normalises dialogue and music over the whole buffer, so a longer timeline (the
   longer end card) changes the stem gains. Record the dialogue and music gains from the
   approved previous timeline and apply those fixed gains instead of re-normalising.
4. `exec` the patched source in the kit's namespace, run it, stash the stems after
   `mix = dlg + mus + sfx`.
5. Null tests before trusting it: the patch with the fade disabled must reproduce the
   kit's master bit-exactly, and re-deriving the previous version must match its master
   (both were 0 LSB difference here).
6. Proof: the music stem is digital silence inside the sting window; master minus the
   scaled sting stem is below -90 dBFS there.

### B9. SAR bug - outputs looked stretched in Premiere (IMPORTANT)
**Symptom** the deliverable played correctly in most players but Premiere showed it
stretched horizontally.
**Cause** the base stage does `crop=W:H:X:Y,scale=1920:1080` without `setsar`. When the crop
is not exactly 16:9 (e.g. 1352x760), ffmpeg compensates with a non-square SAR: the files
carried **SAR 1521:1520, DAR 169:95**. Premiere honours it.
**Fix (lossless, keeps the libx264 CRF 15 deliverable, verified: decoded frames identical
by framemd5):**
```bash
ffmpeg -i IN.mp4 -map 0 -c copy -aspect 16:9 \
  -bsf:v "h264_metadata=sample_aspect_ratio=1/1:video_full_range_flag=0:colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1" \
  -movflags +faststart OUT.mp4
```
**Fix used for the approved file (re-encode, NVENC):**
```bash
ffmpeg -i IN.mp4 -map 0:v:0 -map 0:a:0 -vf "setsar=1,format=yuv420p" \
  -c:v h264_nvenc -preset p7 -tune hq -rc vbr -cq 17 -b:v 0 -profile:v high -pix_fmt yuv420p \
  -color_range tv -colorspace bt709 -color_primaries bt709 -color_trc bt709 -aspect 16:9 \
  -bsf:v "h264_metadata=sample_aspect_ratio=1/1" -c:a copy -movflags +faststart OUT.mp4
```
The approved file probes as h264 High, 1920x1080, SAR 1:1, DAR 16:9, yuv420p, tv range,
bt709 matrix, encoder h264_nvenc, ~11.6 Mb/s. Without the colour fields in the bitstream
filter, primaries/transfer stay "unknown" in the stream; the first command tags all three.
Note the NVENC re-encode departs from the "libx264 CRF 15" deliverable rule; prefer the
lossless stream-copy fix (checked with ffprobe and framemd5, not yet opened in Premiere).
**Prove it** before delivery:
`ffprobe -v error -select_streams v -show_entries stream=sample_aspect_ratio,display_aspect_ratio,color_range,color_space,color_primaries,color_transfer -of compact OUT.mp4`
must show `1:1`, `16:9`, `tv`, `bt709`.

### B10. Locked files
When the client has a file open in Premiere (or any NLE/player on Windows), it is locked:
overwrite fails or half-writes. Always write a new file name (e.g. `_SAR1`, `_v2`) and
never rename over an open file.

### B11. Hardware
Use the CUDA/NVENC tiers when present: CUDA decode, Whisper float16 on CUDA, NVENC
previews. They cut iteration time a lot on alternates. The deliverable rule stays as in
SKILL.md "Hardware".

### B12. Frame rate rounding
A 29.97 fps source is treated as 30 by the kit: `frames = ceil((out - in) * 30)`. Derive
`frames` from that formula; do not round by hand, or the audio and the picture of a shot
drift by a frame.

### B13. Working hygiene
- Before reusing a project/build tag, check the name does not already exist (an old
  `project_<tag>.json` was silently reused once); use a fresh tag.
- Helper scripts that prefix a work folder break on absolute paths: pass names relative to
  the work folder.
- PowerShell flattens nested arrays (`@(@(a,b))`); build JSON in Python, not in PowerShell.
- Read every gate's actual output before reporting a pass; one report claimed the safe
  strips were fixed before the log had been read.

---

## C. Kit TODOs (C1-C6 applied 2026-11-02; open items at the end)

1. ~~**setsar fix**~~ **DONE** (setsar=1 in every segment, h264_metadata bsf, -aspect, bt709 tags; `qc` FORMAT gate): append `setsar=1` after `scale=` in the base stage and pass `-aspect 16:9`
   on the final mux; tag bt709 / tv range. QC should fail when SAR != 1:1.
2. ~~**Post-AAC true-peak check**~~ **DONE** (`remaster_aac` in the mux; `qc` AUDIO gate): measure TP on the encoded MP4 inside `qc`/`stage_master`
   and iterate the knee until <= -1.1 dBTP.
3. ~~**Sting length**~~ **DONE** (`vet --write` sets final black 15 f + sting_at; build WARNs on a short tail; AUDIO gate): compute the end-card length so the sting plays
   to its 2.4 s cap (or its natural decay), or warn when `total - sting_at < 2.45 s`.
4. ~~**Music fade option**~~ **DONE** (automatic `end_audio_plan`; explicit keys still win; stem-gain pinning still open): project keys such as `music_fade_out: [F0, F1]` (cosine), plus an
   option to pin stem gains when the timeline length changes.
5. ~~**Pause gate**~~ **DONE** (SPEECH gate pauses from the words json; safe strips in FRAMING): built-in envelope gate at -45 dBFS (head/tail <= 0.20 s, internal pause
   <= 0.35 s) and a safe-strip glyph gate (60 px top/bottom).
6. ~~**Head-margin gate with profile fallback** (B4) and per-shot `crop_w/crop_h/crop_y`
   support in the base stage (a local copy needed it for lean-up shots).~~ **DONE
   2026-11-02**:
   - The base stage now supports per-shot `crop_x/y/w/h`.
   - `introkit.py frame` sets a face-aimed zoom and push-in from a YuNet scan of every
     frame, verifies it at delivered scale and backs off automatically.
   - `qc --faces` is the per-frame gate on the final file: it checks head+25 % >= 0 and
     chin and sides >= 5 %, and FAILs on no face.
   - Profile fallback: the Haar profile cascade does not exist in OpenCV 5 headless, so a
     low-score full-resolution YuNet pass on the shot ROI is used instead, plus the
     `frame` sidecar box. A frame that is still empty is reported as NO FACE.
   (Items 1-5 are still open.)
7. **Voiceprint references stored** in `qc.json` with the wrong-speaker margin (B5). Now a diagnostic only (D7).

### B3c. Face zoom known limits (2026-11-02, Jeremy altB3x test)
- Guests filmed large and near the top of frame with an ON AIR bug top-left and a chyron
  tab bottom-left are source-limited: verify backs the zoom off to ≈1.29-1.39×, lower than
  the editor's 1.41-1.50× static crops, because those static crops failed the qc margins
  (chin 15 px vs ≥ 66) and overlapped the ON AIR bug. There is no "never below the editor
  zoom" floor: it would ship a crop that fails qc. Add one only as an opt-in that applies
  when the editor crop itself passes `qc --faces`.
- When dodging furniture puts the caption column over the face, `frame` WARNs `TEXT`;
  the fix is editorial (flip `side`, narrow `maxw`, another span), not automatic.

---

## D. Session 2026-11-02 (Tom Pikett + Kristen Mierzwa, 6 cold opens, approved)

### D1. Layout the client wants
Too many centred heads were rejected. The approved look: ONE speaker per shot, face zoomed
on a third, captions on the opposite black-faded side; the other host must never be visible,
not even faintly through the fade (solid feathered black matte when cropping cannot remove
him). Kristen's head touching the top was accepted (source-limited headroom), which is now
the opt-in `allow_top_touch` per speaker / shot, never a global relaxation.
Calibration of the approved Tom B: guest zoom 1.62x, face 712 px (~0.66 H) at 0.70 W; hosts
2.05-2.4x, face 461-630 px (~0.55 H), Andy at 0.30 W text right, Rob at 0.70 W text left.
Ported into `sidekit.py` (`frame --layout side`, the new default).

### D2. Last word clipped (the cause)
Out-points were set at Whisper's word end. Whisper ends words 50-400 ms early on falling
or questioning endings, and the 40 ms pre-out fade then ate the decay. Fix (now `vet
--write`): measure the acoustic end on the 10 ms envelope relative to the room floor
(8th percentile), add a 60-120 ms tail, 20-25 ms fade, limited by the next onset; video
follows audio (frames = ceil). Calibration on 26 shots: approved Tom outs sit >= +60 ms after
the measured end, the clipped pre-fix outs at -10..0 ms. Kristen's loud event room reads
ends late (-130..+60 ms on approved outs), so -150..+40 ms is LISTEN, not FAIL.
Energy at the cut alone is NOT conclusive (approved -3.5..+12.7 dB over floor vs clipped
+0.4..+16.3), and ASR on the mix is not either: Whisper "hears" truncated words.

### D3. Hidden stutters and fillers
Two stutters were invisible in the full-file transcript and only showed in per-clip ASR,
and only in one of VAD on / VAD off. Per-clip ASR in both modes is now the selection-time
check (`vet`); the post-build speech gate needs only one pass.

### D4. Chyron beats ASR
ASR wrote "DTC" for DTCC and similar slips. `assemble` aligns the SCRIPT text to the ASR
times and keeps the script spelling; write names and acronyms as on the chyron / slides.

### D5. Event audio floor
Floors were -25..-31 dBFS; a -45 dBFS dip rule is unreachable there. Dips are graded
relative to the floor: OK <= +4 dB, MARG <= +8 dB, else HOT.

### D6. Wide and two-shot pieces
Faces < 230 px are punched in to the 2.45x cap; persistent other faces (>= 20 % of samples,
score >= 0.7) define bodies (~1 face width each side) that must be cropped out or matted.
When the look-room side is impossible in the source, the planner takes the other side.

### D7. QC redesign: less, but more conclusive (user request)
Before: ~3 min per pass (full-file ASR, windowed ASR, several models voting, every-frame
face scan, voiceprint), run repeatedly, with false CHECKs (window bleeding over continuous
joins, low ASR probability under music: "productive" p 0.25 in the mix vs 0.94 on the source).
Now: selection-time `vet` (cached by source + in/out) + one `qc` with four gates (FORMAT,
AUDIO, FRAMING, SPEECH), one table, per-shot cache, contact sheet. Measured on the six
approved files: 14-23 s cold, ~2 s cached; all six PASS, 4 LISTEN rows on Kristen.
Removed as gates, and why: multi-model voting (models disagree on the same mishears; it
added time, not certainty); full-file and windowed ASR (smooths over stutters at cuts and
bleeds across joins; per-clip ASR at selection time is decisive); every-frame face scan
(every 3rd frame + first/last of each shot finds the same failures; `--classic-faces`
remains); voiceprint (identity is decided once when mapping hosts; kept as a diagnostic);
the ASR-word-end clip rule (Whisper's ends are both early and late; the acoustic end decides).
Continuous joins (next in == this out) are never graded at the cut.

### D8. TheTrimmer evaluation
Smart-cut / keyframe-aligned stream copy does not help the final render (every shot is
re-encoded: crop, grade, overlay). Worth adopting later (TODO, not implemented): a pause
map of the whole source computed once, cached decoded segments between builds, and
stream-copy previews of the raw spans for line review.

### D9. Open TODOs
- Pause map of the source once per episode (TheTrimmer idea), reused by vet and assemble.
- Segment cache between builds (only re-encode shots whose inputs changed).
- Stream-copy previews of candidate spans for review before assembling.
- Stem-gain pinning when the timeline length changes (C4 remainder).
- Loud-room acoustic-end estimate (Kristen): a crowd-aware floor would turn LISTEN into OK/FAIL.
- `frame --layout side` and `vet --write` on a fresh guest, and an old crop_x-only project
  build (Zach `edl.json` style), have not been re-run end-to-end with this version.

## E. Session 2026-11-03 (Diego Perez de Ayala, Solana Legend; first real run of assemble/vet/frame/qc)

### E1. A quiet button under the music
The client picked B but said the final Diego clip sounded too low against the music. Measured
in the mix (active speech vs the bed under it): hook +5.7 dB, hosts +10.2 / +11.2 dB, button
+3.8 dB and -0.2 dB (the second piece, "I'll show you the outcome", trails off). The global
dialogue norm cannot fix one quiet clip. Fix (B2): `gain_db` +5 / +7 and `duck` 0.85 on the two
button shots -> +13.1 / +12.0 dB, still -14 LUFS / TP -1.3 after AAC, music fade before the sting
unchanged. Rule: after every build read the `level shot N` lines; any clip under about +10 dB
(especially the button, which also sits under the bed fade-out) gets a per-shot gain/duck before
the client hears it. Do not re-mix the other shots.

### E2. Where intros go
From 2026-11-03 the generated intros live next to the source: `<source folder>\intros\`
(scratch in `intros\work\`). Earlier guests stay in `Bitwise_Intro\deliverables\<Guest>\`.

### E3. vet / qc readings worth knowing
- vet's `end` FAIL grades the OLD out-point; after `--write` the out is already at the acoustic
  end (the `out old>new` column), so it is fixed, not a reason to re-pick.
- qc speech FAIL "pause head > 0.35" on a host line can mean the in-point caught the tail of the
  previous word (qc heard "Like, are ..."): move the in to the dip after it. An internal pause
  > 0.35 s in a button: split the piece at the dip (two pieces in one beat).

### E4. Armani Ferrante (Backpack): no consecutive same-speaker beats
The user rejected hook > Andy > Andy > button: with one host and the guest the speakers must
strictly alternate (guest / host / guest / host / guest). B2 = Armani > Andy > Armani > Andy >
Armani. `assemble` now enforces it.

### E5. Last words still clipped after vet --write
'attack' (out 50.20, vowel to ~50.27, /k/ release to ~50.36) and 'three' (vowel decay to ~399.27)
were cut. Two causes: Whisper's next-word start capped the out (it is early, like its word ends),
and a 40 ms floor run accepted a plosive closure as the end. Fixed in measure_end (60 ms run,
150 ms tail, no ASR cap, deepest dip for coarticulated words). Rule: err late on final plosives
and vowel decays.

### E6. Camera cuts inside a candidate
A host line at 1338.3 started 0.8 s on the guest camera (cut at 1339.07); qc read the penguin
plush as a 2nd face. Run a scene-cut check (ffmpeg select=gt(scene,0.25)) on every candidate span
before assembling.

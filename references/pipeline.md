# Pipeline

Six stages, each re-runnable on its own. Every stage writes to `work/` and never reads the
previous stage's intermediate — it reads the source or the cut list.

**Output location (default since 2026-11-03):** `<source folder>\intros\`: finals `<name>.mp4` plus `LINE_SETS.md`, `CUTLIST.md`, `QC_REPORT.md`; scratch (sets.json, project/words json, vet cache, qc json, contact sheets, build `work/`) in `intros\work\`. Created if missing. Override: `build --out DIR` (CLI wins), else `out_dir` in sets.json / the project (relative to that file); `assemble --out DIR` moves only the json files. Example: `H:\Show\Ep 12\Ep 12.mp4` -> `H:\Show\Ep 12\intros\THE_ROLLUP_<Guest>_ColdOpen_A.mp4`.

```
src ──probe──► edl.json ──► base.mp4 ──► overlay/f_%04d.png ──► final.mp4
                  │                                            ▲
                  └──► words.json ──► (overlay timing)          │
                  └──► audio plan ──► mix_master.wav ───────────┘
```

## Stage 0 — probe

```bash
ffprobe -v error -show_streams -show_format -of json "$SRC"
ffmpeg -filters | grep -E 'drawtext|overlay|ebur128|loudnorm|blackdetect'   # capability check
```

Record: duration, fps, size, colour tags, audio sample-rate/channels, and whether both
channels are identical (dual-mono).

## Stage 1 — reference teardown

```bash
# cuts
ffmpeg -nostats -i "$REF" -vf "select='gt(scene,0.28)',metadata=print:file=-" -an -f null -
# dips to black
ffmpeg -nostats -i "$REF" -vf "blackdetect=d=0.08:pix_th=0.10" -an -f null -
# loudness
ffmpeg -nostats -i "$REF" -af ebur128=peak=true -f null -
```

Produces: section boundaries, section durations, dip placement, integrated/LRA/true-peak.
Every one of these is a gate you will be measured against.

## Stage 2 — transcript with word timings

Any word-timestamping ASR works. faster-whisper `large-v3-turbo` on GPU runs ~10× realtime.

```bash
python scripts/introkit.py transcribe --src "$SRC" --out work/ --diarize --hotwords "..."
```

Output contract used downstream: `cues[].words[] = {start, end, text, probability}`.

**Do not** trust diarisation for "which host" — it merges same-register voices. Use it to
separate *guest* from *hosts*, then voiceprint the hosts separately.

## Stage 3 — edl.json

The single source of truth. One entry per shot:

```json
{"i": 3, "kind": "clip", "src_in": 632.98, "frames": 123, "crop_x": 560, "beat": "2 HOST B"}
{"i": 4, "kind": "black", "frames": 10}
{"i": 9, "kind": "card",  "frames": 78, "beat": "5 TITLE CARD"}
```

`kind` ∈ `clip | black | card | end`. Everything else — audio placement, word timing, section
reports — is derived from this file plus the transcript. Never hardcode a timecode
downstream; derive it.

## Stage 3b — sets.json -> project (`assemble`) and `vet`

```json
{"src": "../Guest.mp4", "prefix": "THE_ROLLUP_Guest_ColdOpen", "cards": {...},
 "avoid": [[0,0,170,48],[0,728,1920,352]],
 "who": {"guest": {"big": 180, "small": 96}, "andy": {"big": 150, "small": 90}},
 "speakers": {"andy": {"pick": "leftmost", "text_side": "right"}, "rob": {"pick": "rightmost"}},
 "allow_top_touch": ["guest"],
 "sets": {"A": [{"who": "guest", "label": "HOOK (Guest)",
                 "pieces": [{"in": 858.64, "out": 862.12, "text": "Tokenization is *eating* finance."}]}]}}
```
Optional `"out_dir": "D:/elsewhere"` moves the deliverables (default `<src folder>/intros`).
`*word` = Anton claim (gold). `assemble --sets sets.json --set A` writes project_A.json,
words_A.json (script spelling, ASR times, absolute `src`) and assemble_A.txt into
`<src folder>/intros/work/` (or `<out_dir>/work/`, or `--out`); black 10 f between beats,
final black 15 f, end card 81 f, sting_at = end card start + 0.07 s. Then `vet --write`.

## Stage 4 — base picture

```bash
ffmpeg -ss "$IN" -i "$SRC" -t "$DUR" -frames:v "$N" -r 30 -an \
  -vf "crop=1248:702:${CROP_X}:45,scale=1920:1080:flags=lanczos,
       unsharp=5:5:0.45,eq=contrast=1.07:saturation=1.05:brightness=0.004:gamma=0.98,
       vignette=PI/5" \
  -c:v libx264 -crf 15 -preset medium -pix_fmt yuv420p work/seg/s03.mp4
```

Rules:
- `-frames:v N` makes the segment frame-exact; assert it with `ffprobe -count_frames`.
- The crop is the **graphics-removal tool**. 1248×702 at y=45 was the largest 16:9 window
  clear of the ON AIR bug, chyron, partner band and ticker.
- **Face framing comes from `introkit.py frame`, before this stage** (see below). Do not
  hand-tune zoom, and do not add a centre zoom.
- Legacy only: a shot with `"punch"` and no `"push"` still gets the old centre zoompan
  (`zoompan=z='1+P*on/N':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'`). The build warns about it, because a
  centre zoom cuts the head off a guest who sits near the top of the frame (C6). Re-run `frame`
  instead.
- Concat with the demuxer (`-f concat -safe 0 -c copy`) and re-assert total frames.

### Stage 4a — face framing (`frame`)

**Default since 2026-11-02: `--layout side`** (`scripts/sidekit.py`): YuNet with landmarks on
~every 0.15 s of the shot; pick the speaker (`speakers.<who>.pick` largest|leftmost|rightmost,
or `face_roi`); text side from `text_side`, else head yaw (look-room), else position; zoom for
face 0.55 H (host) / 0.66 H (guest), cap 2.45, wide faces to the cap; face on 0.30/0.70 W;
other persistent faces cropped out or covered by `matte {x0,x1,feather}`; 4 % push if it
fits; `maxw` from the face edge. Writes crop_*, side, maxw, push, matte, framing.
The classic planner below is `layout: "classic"`.


```bash
python scripts/introkit.py frame --project P.json            # writes P.framed.json + report
python scripts/introkit.py frame --project P.json --write    # in place, keeps P.preframe.json
```

For each clip shot, `frame`:

1. **Scans** every source frame of the shot, plus 0.3 s on each side, with YuNet
   (`assets/models/face_detection_yunet_2023mar.onnx`, CPU, half scale). If a frame
   misses, it runs a full-resolution, lower-score pass on the shot's crop (three-quarter
   and side faces). The target face is the one nearest the previous frame's face. A shot
   where most frames have no face is left unchanged and reported **NO-FACE**.
2. **Builds the union face box** and adds the margins: hair +30 % of face height, chin
   +10 %, sides +15 % of face width. When the hair sits near the source top, the hair
   margin is relaxed down to the qc's 25 %. If even that is not possible, the shot is
   reported **SOURCE-LIMITED**.
3. **Chooses the zoom**: min(target, largest crop that fits, 2.0×), where the target makes
   the face 34 % of 1080. It keeps the editor's horizontal and vertical face position,
   stays clear of the title text column, keeps 60 px above the head where the source
   allows, and uses even crop sizes.
4. **Plans the push**: +6 % from crop S to crop E. E sits inside S and both contain the
   face box, so every in-between frame does too. If the face cannot take at least 2 %,
   the push is 0, with a note. In a same-speaker jump cut, the first shot ends at least
   6 % looser than the second shot starts.
5. **Verifies at delivered scale**: it renders each planned frame at the size `qc --faces`
   uses, detects the face again, and needs the qc margins + 2 % of face height. If any
   margin fails, it lowers the zoom in 2 % steps until it passes. This step is needed
   because YuNet's box grows about 4-6 % on a magnified face, so a margin predicted from
   the source box is optimistic. `--no-verify` skips it.

It writes these keys into each shot: `crop_x/y/w/h` (the start crop), `push` {x,y,w,h}
(the end crop), `punch` (push ratio), `push_ease: "inout"`, and `framing` (zoom
before/start/end, max safe zoom, predicted and verified margins, notes). It also writes
`<out>.faces.json`, the per-frame face samples that `qc --faces` uses as a fallback, and
`<out>.frame.txt`, the report. Options:
- `--shots 1,4` limits the run to those shots.
- `--no-push` and `--push 0.04` set the push.
- `--zoom-cap`, `--target`, `--head`, `--chin`, `--side`, `--top-pad` and `--pad` change
  the defaults.
- Per-shot keys: `"frame_lock": true` keeps a hand crop. `"face_roi": [x,y,w,h]` picks
  the person when two faces share the frame.

**How the build renders a push:** `perspective` with `sense=source:eval=frame` lerps the
four crop corners from S to E along `0.5-0.5·cos(π·t)`, then the grade runs. This gives
sub-pixel motion with no zoompan stair-stepping. A shot without `push` uses the plain
`crop`, exactly as before.

## Stage 5 — graphics sequence

Pillow renders `overlay/f_%04d.png` (RGBA, 1920×1080, `optimize=1`):

- per word: pre-render a 1.7× tile once, then resize per frame — never re-render text at a
  new size per frame (font cache explodes)
- baseline: `y = baseline − ascent + (bbox_top + bbox_bottom)/2`
- shadow: blur the whole alpha layer once per frame, not per word
- scrim peak alpha < 245/255

## Stage 6 — audio

Build the master in numpy rather than a 40-node filtergraph:

1. dialogue: one segment per `clip` shot, 8 ms micro-fades, placed on a silent canvas
2. music: full track, gain envelope = `1 − 0.72 · smoothed(speech_mask)`, one-pole 50 ms
   attack / 300 ms release, hard drop into the tape-stop, return on the title card
3. SFX: boom on every cut-in, tape-stop into the card, sting on the logo; truncate to the
   remaining canvas length
4. normalise dialogue to a fixed RMS, then converge the master to −14 LUFS / −1.75 dBTP
5. **measure the encoded MP4**, and if the peak moved, trim and re-encode

## Stage 7 — mux

Since 2026-11-02 the mux re-masters the AAC (soft knee, TP <= -1.1, -14 LUFS +-0.1;
`remaster_aac: false` to skip), stream-copies video with `h264_metadata` SAR 1/1 + bt709,
`-aspect`, faststart. End audio: `end_audio_plan` (music fade from the last word's end to
sting - 0.04 s, duck held, sting alone; explicit `music_fade_out` / `duck_hold` win).


```bash
ffmpeg -i work/base.mp4 -framerate 30 -start_number 0 -i work/overlay/f_%04d.png -i mix_master.wav \
  -filter_complex "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p[v]" \
  -map "[v]" -map 2:a -c:v libx264 -crf 15 -preset medium -profile:v high -level 4.1 \
  -pix_fmt yuv420p -r 30 -g 60 -movflags +faststart \
  -c:a aac -b:a 320k -ar 48000 -ac 2 -shortest out.mp4
```

`-g 60` is not cosmetic: it makes later verification seeks land on the frame you asked for.

## Stage 8 — QC and docs

See `qc.md`. Emit the numbers into `QC_REPORT.md` from the measurement, never from memory.

## Portability

- All paths are CLI arguments; nothing is hardcoded.
- `ffmpeg`/`ffprobe` are discovered on `PATH` (override with `--ffmpeg`).
- Files are opened with `pathlib`; separators are never concatenated by hand.
- The only platform-specific call is finding a font directory; pass `--fonts`.

## Per-shot audio level (2026-11-03)

Shot keys read by `build` (stage_audio): `gain_db` (speech gain, dB, after the global -18 dB
dialogue norm) and `duck` (music duck depth under that shot, 0-1, default 0.72 = about -11 dB).
The build log prints `[introkit] level shot N: speech X dB, music Y dB, gap Z dB` per clip
(active speech RMS vs the bed under it, pre-master). Aim for a gap of about +10 dB or more.
Example (Diego B -> B2): button gaps +3.8 / -0.2 dB -> +13.1 / +12.0 dB with `gain_db` +5 / +7 and
`duck` 0.85; master still -14 LUFS / TP -1.3 after AAC, music fade before the sting unchanged.

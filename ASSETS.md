# Assets — inventory, provenance, licences

Bundled in `assets/` (13 files incl. `models/`, ≈3.2 MB). All files are the **original bytes** as
used in the approved builds (no re-encode, no trim; md5-verified on 2026-10-30). Projects use
them by default; override with the project keys `music`, `sfx.boom|tape|sting`, `logo`,
`fonts_dir`. Measurements: ffmpeg `ebur128` integrated loudness (I), loudness range (LRA),
true peak.

| File | Role | Facts (measured) | Origin | Licence |
|---|---|---|---|---|
| `music/rollup_music_bed_32s.mp3` | music bed under the whole open, ducked ~14 dB under speech | 32.040 s, MP3 48 kHz stereo, I −12.1 LUFS, LRA 4.9 LU, peak −0.0 dBTP, 769,004 B | generated with ElevenLabs `music_v2` for an earlier Rollup build (instrumental) | **unknown / unverified** — depends on the ElevenLabs plan terms of the generating account; check before external reuse |
| `sfx/boom_deep.s16le-44100-stereo.raw` | low boom on each cut | 2.0 s, headerless s16le 44.1 kHz stereo, I −12.5 LUFS, peak 0.0 dBTP, 352,800 B | ElevenLabs `text_to_sound_effects` | **unknown / unverified** (as above) |
| `sfx/tape_stop.s16le-44100-stereo.raw` | tape-stop into the title card | 2.0 s, s16le 44.1 kHz stereo, I −13.7 LUFS, peak −1.5 dBTP, 352,800 B | ElevenLabs `text_to_sound_effects` | **unknown / unverified** |
| `sfx/sting_two_note.s16le-44100-stereo.raw` | two-note sting on the end card / logo | 3.0 s, s16le 44.1 kHz stereo, I −7.1 LUFS, LRA 20 LU, peak +0.1 dBTP, 529,200 B | ElevenLabs `text_to_sound_effects` | **unknown / unverified** |
| `logo/the_rollup_logo_white_dropshadow.png` | end-card logo | 5200×1420 RGBA PNG, 386,416 B | The Rollup brand logo (supplied by the show) | The Rollup trademark; show use only |
| `fonts/Anton-Regular.ttf` | claims, keywords | 170,812 B | Google Fonts repository | SIL OFL 1.1 (`fonts/OFL-Anton.txt`) |
| `fonts/PlayfairDisplay-Italic.ttf` | connective script phrases | 278,688 B | Google Fonts repository | SIL OFL 1.1, Reserved Font Name "Playfair Display" (`fonts/OFL-PlayfairDisplay.txt`) |
| `models/face_detection_yunet_2023mar.onnx` | face detector for `frame` and `qc --faces` (OpenCV `FaceDetectorYN`, CPU) | 232,589 B, sha256 `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` (identical to the OpenCV Zoo file) | OpenCV Zoo, `models/face_detection_yunet` | MIT, Copyright (c) 2020 Shiqi Yu (`models/YUNET_LICENSE.txt`) |
| `fonts/BarlowCondensed-Bold.ttf` | supporting clauses | 109,912 B | Google Fonts repository | SIL OFL 1.1 (`fonts/OFL-BarlowCondensed.txt`) |

The SFX are headerless PCM (rule 012): their names state the format; introkit sniffs headers
and decodes headerless files as s16le 44.1 kHz stereo (`bytes / 176400 = seconds`).
The sting peaks at +0.1 dBTP by itself; the master limiter handles it in the mix.

## Downloaded at setup (not in the folder until `setup` / `fetch-model`)

| Model | Use | Size | Licence |
|---|---|---|---|
| `mobiuslabsgmbh/faster-whisper-large-v3-turbo` | ASR | ~1.6 GB | per its Hugging Face model card (Whisper weights are MIT) — check the card |
| `altunenes/speaker-diarization-community-1-onnx` / `embedding_model.onnx` (pinned revision) | speaker embeddings | 26.5 MB | repo declares none; it is an ONNX export of `pyannote/speaker-diarization-community-1`, which is **CC-BY-4.0** (attribution required) |

## Per-job material (not bundled)

Session recordings, transcripts and the reference render (e.g. `IMG_9231.MP4`, 33.483 s,
1920×1072) belong to The Rollup and stay with the job, not in the skill.

# rollup-intro skill

Builds The Rollup's ~32 s cold open from a raw session recording. Start with `SKILL.md`.

## Quick start

```bash
# once per machine (from this folder) — installs uv, Python, packages, PyTorch, ffmpeg, models
powershell -ExecutionPolicy Bypass -File .\setup.ps1      # Windows
bash ./setup.sh                                           # macOS / Linux
python scripts/introkit.py setup                          # any OS, if some Python 3.9+ is already there

python scripts/introkit.py hw                             # which hardware path will be used
python scripts/introkit.py upscale                        # confirm Real-ESRGAN is ready (on by default at build)
python scripts/introkit.py transcribe --src EPISODE.mp4 --out work/ --diarize   # only if no transcript
python scripts/introkit.py assemble --sets sets.json --set A           # -> <src folder>/intros/work/project_A.json + words_A.json
python scripts/introkit.py vet --project project_A.json --words words_A.json --write   # stutters, last-word ends
python scripts/introkit.py frame --project project_A.json                # side layout -> project_A.framed.json
python scripts/introkit.py build --project project_A.framed.json --words words_A.json   # -> <src folder>/intros/NAME.mp4
python scripts/introkit.py qc --final ../NAME.mp4 --project project_A.framed.json --out qc_A.json
```

Generated intros go to **`<folder of the source video>/intros/`** (finals + LINE_SETS.md,
CUTLIST.md, QC_REPORT.md; scratch in `intros/work/`, where `sets.json` should live too, so run the
json steps from there). Override with `build --out DIR` or `out_dir` in sets.json / the project.

Copy `.env.example` to `.env` only when a job will call ElevenLabs (setup itself does not need a key).

Works CPU-only on Windows, macOS (Intel / Apple Silicon) and Linux; a GPU only speeds up
decode, previews and transcription. The deliverable is always libx264 CRF 15.

## Contents

| Path | What |
|---|---|
| `SKILL.md` | procedure, gates, contracts |
| `RULES.md` | running rules 000–042 |
| `TOOLS.md` | setup, ffmpeg per OS, hardware tiers, transcription, diarization, measured costs |
| `ASSETS.md` | bundled music/SFX/logo/fonts and their provenance |
| `references/` | line selection, pipeline, design system, QC, troubleshooting |
| `scripts/introkit.py` | entry point; `sidekit.py` (side framing, vet, assemble, qc gates); `transcribe.py`, `speakers.py` libraries; `project.example.json` |
| `assets/` | music, sfx, logo, fonts, `models/` (YuNet face detector + licence) |
| `requirements*.txt`, `setup.ps1`, `setup.sh` | environment (setup also installs `requirements-upscale.txt` and fetches weights) |
| `scripts/setupkit.py` | self-bootstrapping setup (also auto-runs when pieces are missing) |
| `scripts/upscale.py` | Real-ESRGAN zoomed-shot pass (on by default after setup) |
| `scripts/test_smoke.py` | smoke checks |

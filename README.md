# rollup-intro skill

Builds The Rollup's ~32 s cold open from a raw session recording. Start with `SKILL.md`.

## Quick start

```bash
# once per machine (from this folder)
powershell -ExecutionPolicy Bypass -File .\setup.ps1      # Windows
bash ./setup.sh                                           # macOS / Linux

python scripts/introkit.py hw                             # which hardware path will be used
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

Works CPU-only on Windows, macOS (Intel / Apple Silicon) and Linux; a GPU only speeds up
decode, previews and transcription. The deliverable is always libx264 CRF 15.

## Contents

| Path | What |
|---|---|
| `SKILL.md` | procedure, gates, contracts |
| `RULES.md` | running rules 000–030 |
| `TOOLS.md` | setup, ffmpeg per OS, hardware tiers, transcription, diarization, measured costs |
| `ASSETS.md` | bundled music/SFX/logo/fonts and their provenance |
| `LESSONS_LEARNED.md` | session lessons, workarounds, kit TODOs |
| `CHANGELOG.md` | changes |
| `references/` | line selection, pipeline, design system, QC, troubleshooting |
| `scripts/introkit.py` | entry point; `sidekit.py` (side framing, vet, assemble, qc gates); `transcribe.py`, `speakers.py` libraries; `project.example.json` |
| `assets/` | music, sfx, logo, fonts, `models/` (YuNet face detector + licence) |
| `requirements*.txt`, `setup.ps1`, `setup.sh` | environment |

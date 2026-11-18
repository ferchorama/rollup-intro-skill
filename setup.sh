#!/usr/bin/env bash
# One-folder tool setup for the Rollup intro skill (macOS Intel / Apple Silicon, Linux).
#
# Everything goes into ONE folder, by default <skill>/.rollup-intro-env
# (override with --home DIR or the ROLLUP_INTRO_HOME environment variable):
#   uv/  python/  cache/uv/  venv/  models/  hf/  tools/ffmpeg/bin/
# Nothing outside that folder is modified (no shell rc edits, no global pip).
# Delete the folder to uninstall.
#
# After uv and the venv exist, this script hands off to `introkit setup`, which
# installs packages, the CUDA or CPU PyTorch wheel, ffmpeg, Whisper and Real-ESRGAN.
# Already-present pieces are skipped. Zoomed-shot upscaling is on by default afterwards.
#
# If any Python 3.9+ is already installed, you can also run:
#   python3 scripts/introkit.py setup
# Any introkit command will run setup automatically the first time something is missing.
#
#   ./setup.sh                      # auto: CUDA torch if nvidia-smi works
#   ./setup.sh --cuda no --skip-model
#   ./setup.sh --ffmpeg portable    # Linux: static BtbN build into the tool folder
set -euo pipefail
SKILL="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HOME_DIR="${ROLLUP_INTRO_HOME:-$SKILL/.rollup-intro-env}"
CUDA=auto; FFMPEG=auto; SKIP_MODEL=0; UNLOCKED=0; PY=3.11
while [ $# -gt 0 ]; do
  case "$1" in
    --home) HOME_DIR="$2"; shift 2 ;;
    --cuda) CUDA="$2"; shift 2 ;;
    --ffmpeg) FFMPEG="$2"; shift 2 ;;
    --skip-model) SKIP_MODEL=1; shift ;;
    --unlocked) UNLOCKED=1; shift ;;
    --python) PY="$2"; shift 2 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
done
mkdir -p "$HOME_DIR"; HOME_DIR="$(cd "$HOME_DIR" && pwd)"
export ROLLUP_INTRO_HOME="$HOME_DIR"
echo "[setup] tool folder: $HOME_DIR"
export UV_CACHE_DIR="$HOME_DIR/cache/uv" UV_PYTHON_INSTALL_DIR="$HOME_DIR/python" \
       UV_LINK_MODE=copy HF_HOME="$HOME_DIR/hf"

# 1. uv (no system Python required)
UV="$HOME_DIR/uv/uv"
if [ ! -x "$UV" ]; then
  if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
  else
    echo "[setup] installing uv into the tool folder"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME_DIR/uv" UV_NO_MODIFY_PATH=1 sh
    UV="$HOME_DIR/uv/uv"
  fi
fi
echo "[setup] uv: $UV ($("$UV" --version))"

# 2. venv with a uv-managed Python inside the tool folder
PYBIN="$HOME_DIR/venv/bin/python"
[ -x "$PYBIN" ] || "$UV" venv "$HOME_DIR/venv" --python "$PY" --python-preference only-managed --prompt rollup-intro

# 3. hand off to introkit setup
KIT="$SKILL/scripts/introkit.py"
SETUP_ARGS=(setup --cuda "$CUDA" --ffmpeg "$FFMPEG" --python "$PY")
[ $SKIP_MODEL = 1 ] && SETUP_ARGS+=(--skip-model)
[ $UNLOCKED = 1 ] && SETUP_ARGS+=(--unlocked)
export INTROKIT_NO_REEXEC=1 INTROKIT_NO_SETUP=1 INTROKIT_SETUP_RUNNING=1
"$PYBIN" "$KIT" "${SETUP_ARGS[@]}"

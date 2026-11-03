#!/usr/bin/env bash
# One-folder tool setup for the Rollup intro skill (macOS Intel / Apple Silicon, Linux).
#
# Everything goes into ONE folder, by default <skill>/.rollup-intro-env
# (override with --home DIR or the ROLLUP_INTRO_HOME environment variable):
#   uv/  python/  cache/uv/  venv/  models/  hf/  tools/ffmpeg/bin/
# Nothing outside that folder is modified (no shell rc edits, no global pip).
# Delete the folder to uninstall.
#
#   ./setup.sh                      # auto: CUDA wheels only if nvidia-smi works
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
echo "[setup] tool folder: $HOME_DIR"
export UV_CACHE_DIR="$HOME_DIR/cache/uv" UV_PYTHON_INSTALL_DIR="$HOME_DIR/python" \
       UV_LINK_MODE=copy HF_HOME="$HOME_DIR/hf"

# 1. uv
UV="$HOME_DIR/uv/uv"
if [ ! -x "$UV" ]; then
  if command -v uv >/dev/null 2>&1; then UV="$(command -v uv)"
  else
    echo "[setup] installing uv into the tool folder"
    curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR="$HOME_DIR/uv" UV_NO_MODIFY_PATH=1 sh
  fi
fi
echo "[setup] uv: $UV ($("$UV" --version))"

# 2. venv with a uv-managed Python inside the tool folder
PYBIN="$HOME_DIR/venv/bin/python"
[ -x "$PYBIN" ] || "$UV" venv "$HOME_DIR/venv" --python "$PY" --python-preference only-managed --prompt rollup-intro

# 3. dependencies
USE_CUDA=0
if [ "$CUDA" = yes ]; then USE_CUDA=1
elif [ "$CUDA" = auto ] && [ "$(uname)" = Linux ] && command -v nvidia-smi >/dev/null 2>&1 \
     && nvidia-smi -L >/dev/null 2>&1; then USE_CUDA=1; fi
if [ $UNLOCKED = 1 ]; then REQS=(-r "$SKILL/requirements.txt"); [ $USE_CUDA = 1 ] && REQS+=(-r "$SKILL/requirements-cuda.txt")
elif [ $USE_CUDA = 1 ]; then REQS=(-r "$SKILL/requirements-cuda.lock.txt")
else REQS=(-r "$SKILL/requirements.lock.txt"); fi
echo "[setup] installing Python packages (NVIDIA CUDA runtime: $USE_CUDA)"
"$UV" pip install --python "$PYBIN" "${REQS[@]}"

# 4. ffmpeg
if [ "$FFMPEG" = portable ] || { [ "$FFMPEG" = auto ] && ! command -v ffmpeg >/dev/null 2>&1; }; then
  DST="$HOME_DIR/tools/ffmpeg"
  case "$(uname)-$(uname -m)" in
    Linux-x86_64)  URL=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz ;;
    Linux-aarch64) URL=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz ;;
    *) URL="" ;;
  esac
  if [ -n "$URL" ]; then
    if [ ! -x "$DST/bin/ffmpeg" ]; then
      echo "[setup] downloading portable ffmpeg (BtbN static GPL build)"
      mkdir -p "$HOME_DIR/tools"; rm -rf "$DST" "$HOME_DIR/tools/ff_unpack"; mkdir -p "$HOME_DIR/tools/ff_unpack"
      curl -LsSf "$URL" | tar -xJ -C "$HOME_DIR/tools/ff_unpack"
      mv "$HOME_DIR/tools/ff_unpack"/*/ "$DST"; rm -rf "$HOME_DIR/tools/ff_unpack"
    fi
    echo "[setup] ffmpeg: $DST/bin/ffmpeg"
  elif [ "$(uname)" = Darwin ]; then
    if command -v brew >/dev/null 2>&1; then
      echo "[setup] installing ffmpeg with Homebrew (includes VideoToolbox)"; brew install ffmpeg
    else
      echo "[setup] WARNING: install Homebrew (https://brew.sh) then 'brew install ffmpeg', or put"
      echo "        ffmpeg + ffprobe binaries in $HOME_DIR/tools/ffmpeg/bin/"
    fi
  fi
elif command -v ffmpeg >/dev/null 2>&1; then echo "[setup] ffmpeg on PATH: $(command -v ffmpeg)"; fi

# 5. Whisper large-v3-turbo into the tool folder
KIT="$SKILL/scripts/introkit.py"
[ $SKIP_MODEL = 1 ] || "$PYBIN" "$KIT" fetch-model

# 6. report
"$PYBIN" "$KIT" env
"$PYBIN" "$KIT" hw
echo "[setup] done. Run:  \"$PYBIN\" \"$KIT\" <command>   (or any python3: introkit re-runs itself in this env)"

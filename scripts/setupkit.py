"""Self-bootstrapping setup for the Rollup intro skill (stdlib only until the venv exists).

`introkit setup` and setup.ps1 / setup.sh all end up here. A missing environment is also
repaired automatically the first time any other introkit command is run (unless
INTROKIT_NO_SETUP=1). Everything lands in <skill>/.rollup-intro-env (or ROLLUP_INTRO_HOME).
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

STATUS_NAME = "setup-status.json"
PYTHON_VERSION = "3.11"
# Newer NVIDIA drivers run older CUDA wheels. Try these official PyTorch indexes in order.
CUDA_WHEEL_TAGS = ("cu128", "cu126", "cu124", "cu121")
FFMPEG_URLS = {
    "Windows-AMD64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
    "Linux-x86_64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz",
    "Linux-aarch64": "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz",
}


def _ik():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import introkit
    return introkit


# ----------------------------------------------------------------------------- paths / logging

def log(msg: str) -> None:
    print(f"[setup] {msg}", flush=True)


def skill_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def env_home() -> Path:
    h = os.environ.get("ROLLUP_INTRO_HOME")
    return Path(h).expanduser().resolve() if h else skill_dir() / ".rollup-intro-env"


def venv_python(home: Path | None = None) -> Path:
    home = home or env_home()
    return home / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def status_path(home: Path | None = None) -> Path:
    return (home or env_home()) / STATUS_NAME


def _apply_env_vars(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("UV_CACHE_DIR", str(home / "cache" / "uv"))
    os.environ.setdefault("UV_PYTHON_INSTALL_DIR", str(home / "python"))
    os.environ.setdefault("UV_LINK_MODE", "copy")
    os.environ.setdefault("HF_HOME", str(home / "hf"))


# ----------------------------------------------------------------------------- probes

def run(cmd, check=True, env=None, cwd=None, capture=True) -> subprocess.CompletedProcess:
    if capture:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, cwd=cwd)
    else:
        p = subprocess.run(cmd, env=env, cwd=cwd)
    if check and p.returncode != 0:
        err = ((p.stderr or p.stdout or "")[-4000:] if capture else "")
        raise RuntimeError(f"command failed ({p.returncode}): {cmd}\n{err}")
    return p


def have_import(mod: str, py: Path | None = None) -> bool:
    exe = str(py or sys.executable)
    p = run([exe, "-c", f"import {mod}"], check=False)
    return p.returncode == 0


def nvidia_info() -> dict:
    """Driver / GPU name / advertised CUDA version, or empty if no NVIDIA device."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {}
    p = run([exe], check=False)
    if p.returncode != 0:
        return {}
    info = {"smi": exe, "gpu": None, "cuda": None, "driver": None}
    text = (p.stdout or "") + (p.stderr or "")
    import re
    m = re.search(r"CUDA UMD Version:\s*([0-9.]+)", text) or re.search(r"CUDA Version:\s*([0-9.]+)", text)
    if m:
        info["cuda"] = m.group(1)
    m = re.search(r"NVIDIA-SMI\s+([0-9.]+)", text)
    if m:
        info["driver"] = m.group(1)
    q = run([exe, "--query-gpu=name,driver_version", "--format=csv,noheader"], check=False)
    if q.returncode == 0 and q.stdout.strip():
        parts = [s.strip() for s in q.stdout.strip().splitlines()[0].split(",")]
        if parts:
            info["gpu"] = parts[0]
        if len(parts) > 1:
            info["driver"] = parts[1]
    return info


def cuda_tags_for_driver(cuda_version: str | None) -> list:
    """Official wheel tags a driver can run, newest first. Empty -> CPU only."""
    if not cuda_version:
        return list(CUDA_WHEEL_TAGS)
    try:
        major, minor = (int(x) for x in cuda_version.split(".")[:2])
    except ValueError:
        return list(CUDA_WHEEL_TAGS)
    ver = major * 100 + minor
    tags = []
    for tag, need in (("cu128", 1208), ("cu126", 1206), ("cu124", 1204), ("cu121", 1201), ("cu118", 1108)):
        if ver >= need:
            tags.append(tag)
    return tags or list(CUDA_WHEEL_TAGS)


def find_ffmpeg() -> str | None:
    for key, name in (("INTROKIT_FFMPEG", "ffmpeg"), ("INTROKIT_FFPROBE", "ffprobe")):
        pass
    for name in ("ffmpeg",):
        e = os.environ.get("INTROKIT_FFMPEG")
        if e and Path(e).exists():
            return e
        w = shutil.which(name)
        if w:
            return w
        home = env_home()
        for cand in (home / "tools" / "ffmpeg" / "bin").glob("ffmpeg*"):
            if cand.stem == "ffmpeg":
                return str(cand)
    return None


def find_ffprobe() -> str | None:
    e = os.environ.get("INTROKIT_FFPROBE")
    if e and Path(e).exists():
        return e
    w = shutil.which("ffprobe")
    if w:
        return w
    for cand in (env_home() / "tools" / "ffmpeg" / "bin").glob("ffprobe*"):
        if cand.stem == "ffprobe":
            return str(cand)
    ff = find_ffmpeg()
    if ff:
        sib = Path(ff).with_name("ffprobe" + (".exe" if os.name == "nt" else ""))
        if sib.exists():
            return str(sib)
    return None


def whisper_ready(home: Path | None = None) -> bool:
    md = (home or env_home()) / "models"
    if not md.is_dir():
        return False
    for p in md.rglob("config.json"):
        s = str(p).lower()
        if "large-v3-turbo" in s:
            return True
    return False


def weights_ready(home: Path | None = None) -> bool:
    d = (home or env_home()) / "models" / "upscale"
    return (d / "realesr-general-x4v3.pth").is_file() and (d / "realesr-general-wdn-x4v3.pth").is_file()


def _site_packages(py: Path) -> Path | None:
    if os.name == "nt":
        cand = py.parent.parent / "Lib" / "site-packages"
        return cand if cand.is_dir() else None
    lib = py.parent.parent / "lib"
    for cand in sorted(lib.glob("python*/site-packages"), reverse=True):
        return cand
    return None


def setup_is_ready() -> bool:
    """Fast check used on every introkit launch. Path-only (no imports), must not raise."""
    try:
        home = env_home()
        py = venv_python(home)
        if not py.is_file():
            return False
        if not find_ffmpeg() or not find_ffprobe():
            return False
        if not whisper_ready(home) or not weights_ready(home):
            return False
        sp = _site_packages(py)
        if sp is None:
            return False
        return all((sp / n).exists() for n in ("numpy", "faster_whisper", "torch"))
    except Exception:
        return False


# ----------------------------------------------------------------------------- installers

def find_uv(home: Path) -> Path | None:
    local = home / "uv" / ("uv.exe" if os.name == "nt" else "uv")
    if local.is_file():
        return local
    w = shutil.which("uv")
    return Path(w) if w else None


def install_uv(home: Path) -> Path:
    existing = find_uv(home)
    if existing:
        return existing
    dest = home / "uv"
    dest.mkdir(parents=True, exist_ok=True)
    log(f"installing uv into {dest}")
    env = dict(os.environ)
    env["UV_INSTALL_DIR"] = str(dest)
    env["UV_NO_MODIFY_PATH"] = "1"
    if os.name == "nt":
        cmd = (
            f"$env:UV_INSTALL_DIR='{dest}'; $env:UV_NO_MODIFY_PATH='1'; "
            "Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression"
        )
        p = subprocess.run(["powershell", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                           env=env, text=True, encoding="utf-8", errors="replace")
        if p.returncode != 0:
            raise RuntimeError(f"uv install failed: {p.stderr or p.stdout}")
    else:
        p = subprocess.run(
            "curl -LsSf https://astral.sh/uv/install.sh | sh",
            shell=True, env=env, text=True, encoding="utf-8", errors="replace",
        )
        if p.returncode != 0:
            raise RuntimeError(f"uv install failed: {p.stderr or p.stdout}")
    got = find_uv(home)
    if not got:
        raise RuntimeError(f"uv installed but not found in {dest}")
    return got


def ensure_venv(home: Path, uv: Path, python_ver: str = PYTHON_VERSION) -> Path:
    py = venv_python(home)
    if py.is_file():
        return py
    log(f"creating venv with CPython {python_ver} (uv-managed, inside the tool folder)")
    run([str(uv), "venv", str(home / "venv"), "--python", python_ver,
         "--python-preference", "only-managed", "--prompt", "rollup-intro"], capture=False)
    if not py.is_file():
        raise RuntimeError(f"venv python missing at {py}")
    return py


def _reexec_in_venv(py: Path, extra_argv: list[str]) -> None:
    """Re-run `introkit setup ...` inside the venv so later imports see installed packages."""
    if Path(sys.prefix).resolve() == (env_home() / "venv").resolve():
        return
    kit = Path(__file__).resolve().parent / "introkit.py"
    env = dict(os.environ, INTROKIT_NO_REEXEC="1", INTROKIT_SETUP_RUNNING="1")
    log(f"continuing setup inside {py}")
    sys.exit(subprocess.run([str(py), str(kit), "setup", *extra_argv], env=env).returncode)


def install_python_packages(home: Path, uv: Path, py: Path, use_cuda: bool, unlocked: bool) -> None:
    skill = skill_dir()
    if have_import("numpy", py) and have_import("faster_whisper", py) and have_import("cv2", py):
        log("base Python packages already present")
    else:
        if unlocked:
            reqs = ["-r", str(skill / "requirements.txt")]
            if use_cuda:
                reqs += ["-r", str(skill / "requirements-cuda.txt")]
        elif use_cuda:
            reqs = ["-r", str(skill / "requirements-cuda.lock.txt")]
        else:
            reqs = ["-r", str(skill / "requirements.lock.txt")]
        log(f"installing Python packages (NVIDIA CUDA runtime wheels: {use_cuda})")
        run([str(uv), "pip", "install", "--python", str(py), *reqs], capture=False)
    log("base Python packages ready")


def install_torch(uv: Path, py: Path, ninfo: dict) -> dict:
    """Install PyTorch: CUDA wheel if an NVIDIA GPU is present, otherwise CPU. Returns info."""
    def info():
        q = run([str(py), "-c",
                 "import torch, json; print(json.dumps({"
                 "'version': torch.__version__, 'cuda': bool(torch.cuda.is_available()), "
                 "'device': 'cuda' if torch.cuda.is_available() else 'cpu', "
                 "'cuda_runtime': getattr(getattr(torch, 'version', None), 'cuda', None)}))"],
                check=False)
        if q.returncode != 0:
            return {}
        try:
            return json.loads(q.stdout.strip() or "{}")
        except json.JSONDecodeError:
            return {}

    have = info()
    want_cuda = bool(ninfo)
    if have and (have.get("cuda") or not want_cuda):
        log(f"torch already ready: {have.get('version')} on {have.get('device')}")
        return have

    if want_cuda:
        tags = cuda_tags_for_driver(ninfo.get("cuda"))
        log(f"NVIDIA GPU detected ({ninfo.get('gpu') or 'unknown'}, "
            f"CUDA {ninfo.get('cuda') or '?'}); trying PyTorch wheels {', '.join(tags)}")
        for tag in tags:
            url = f"https://download.pytorch.org/whl/{tag}"
            log(f"installing torch from {url}")
            p = run([str(uv), "pip", "install", "--python", str(py), "torch>=2.1", "--index-url", url],
                    check=False, capture=False)
            if p.returncode != 0:
                log(f"wheel {tag} failed to install, trying the next")
                continue
            have = info()
            if have.get("cuda"):
                log(f"torch {have.get('version')} CUDA ok")
                return have
            log(f"wheel {tag} imported but torch.cuda.is_available() is False, trying the next")
        log("no CUDA wheel worked; installing the CPU build")

    log("installing CPU torch")
    p = run([str(uv), "pip", "install", "--python", str(py), "torch>=2.1",
             "--index-url", "https://download.pytorch.org/whl/cpu"], check=False, capture=False)
    if p.returncode != 0:
        # last resort: default PyPI (may be CPU or a platform wheel)
        log("CPU index failed; trying PyPI")
        run([str(uv), "pip", "install", "--python", str(py), "torch>=2.1"], capture=False)
    have = info()
    if not have:
        raise RuntimeError("torch installed but could not be imported")
    log(f"torch {have.get('version')} on {have.get('device')}")
    return have


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    log(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "rollup-intro-skill"})
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
    tmp.replace(dest)


def install_ffmpeg_portable(home: Path) -> str | None:
    key = f"{platform.system()}-{platform.machine()}"
    url = FFMPEG_URLS.get(key)
    if not url:
        return None
    dst = home / "tools" / "ffmpeg"
    exe = dst / "bin" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    if exe.is_file():
        return str(exe)
    log(f"downloading portable ffmpeg ({url.rsplit('/', 1)[-1]})")
    tools = home / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    if url.endswith(".zip"):
        zpath = tools / "ffmpeg.zip"
        _download(url, zpath)
        tmp = tools / "ffmpeg_unzip"
        if tmp.exists():
            shutil.rmtree(tmp)
        shutil.unpack_archive(str(zpath), str(tmp))
        inner = next(p for p in tmp.iterdir() if p.is_dir())
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(inner), str(dst))
        shutil.rmtree(tmp, ignore_errors=True)
        zpath.unlink(missing_ok=True)
    else:
        unpack = tools / "ff_unpack"
        if unpack.exists():
            shutil.rmtree(unpack)
        unpack.mkdir(parents=True)
        # tar.xz: use tar (Windows 10+ and Unix both have it)
        raw = tools / "ffmpeg.tar.xz"
        _download(url, raw)
        run(["tar", "-xJ", "-f", str(raw), "-C", str(unpack)])
        inner = next(p for p in unpack.iterdir() if p.is_dir())
        if dst.exists():
            shutil.rmtree(dst)
        shutil.move(str(inner), str(dst))
        shutil.rmtree(unpack, ignore_errors=True)
        raw.unlink(missing_ok=True)
    if exe.is_file():
        return str(exe)
    return None


def install_ffmpeg_winget() -> bool:
    winget = shutil.which("winget")
    if not winget:
        return False
    log("ffmpeg missing; trying winget install Gyan.FFmpeg")
    p = run([winget, "install", "--id", "Gyan.FFmpeg", "-e",
             "--accept-package-agreements", "--accept-source-agreements",
             "--disable-interactivity"], check=False)
    return p.returncode == 0 and bool(shutil.which("ffmpeg"))


def install_ffmpeg_brew() -> bool:
    brew = shutil.which("brew")
    if not brew:
        return False
    log("ffmpeg missing; trying brew install ffmpeg")
    p = run([brew, "install", "ffmpeg"], check=False)
    return p.returncode == 0 and bool(shutil.which("ffmpeg"))


def install_ffmpeg_apt() -> bool:
    apt = shutil.which("apt-get")
    if not apt:
        return False
    log("ffmpeg missing; trying apt-get install ffmpeg")
    p = run(["sudo", "-n", apt, "install", "-y", "ffmpeg"], check=False)
    return p.returncode == 0 and bool(shutil.which("ffmpeg"))


def ensure_ffmpeg(home: Path, mode: str = "auto") -> str | None:
    """mode: auto | portable | skip. Prefer a portable copy inside the env folder."""
    have = find_ffmpeg()
    if have and mode != "portable":
        log(f"ffmpeg already present: {have}")
        return have
    if mode == "skip":
        log("WARNING: ffmpeg not found and skip was requested; install it before building")
        return have
    exe = install_ffmpeg_portable(home)
    if exe:
        log(f"ffmpeg (portable): {exe}")
        return exe
    if mode == "portable":
        log("WARNING: no portable ffmpeg build for this platform")
        return find_ffmpeg()
    if os.name == "nt" and install_ffmpeg_winget():
        log(f"ffmpeg (winget): {shutil.which('ffmpeg')}")
        return shutil.which("ffmpeg")
    if platform.system() == "Darwin" and install_ffmpeg_brew():
        log(f"ffmpeg (brew): {shutil.which('ffmpeg')}")
        return shutil.which("ffmpeg")
    if platform.system() == "Linux" and install_ffmpeg_apt():
        log(f"ffmpeg (apt): {shutil.which('ffmpeg')}")
        return shutil.which("ffmpeg")
    log("WARNING: ffmpeg could not be installed; put ffmpeg and ffprobe on PATH or in "
        f"{home / 'tools' / 'ffmpeg' / 'bin'}")
    return find_ffmpeg()


def fetch_whisper(py: Path) -> None:
    if whisper_ready():
        log("Whisper large-v3-turbo already present")
        return
    log("downloading Whisper large-v3-turbo (~1.6 GB, one-time)")
    kit = Path(__file__).resolve().parent / "introkit.py"
    env = dict(os.environ, INTROKIT_NO_REEXEC="1", INTROKIT_NO_SETUP="1")
    run([str(py), str(kit), "fetch-model"], env=env, capture=False)
    log("Whisper model ready")


def fetch_upscale_weights() -> None:
    if weights_ready():
        log("Real-ESRGAN weights already present")
        return
    log("downloading Real-ESRGAN general-x4v3 weights")
    import upscale
    upscale.fetch_weights()
    log("Real-ESRGAN weights ready")


# ----------------------------------------------------------------------------- status / entry

def collect_status(py: Path, torch_info: dict, ninfo: dict, ff: str | None) -> dict:
    uv = find_uv(env_home())
    st = {
        "ok": False,
        "env_home": str(env_home()),
        "python": str(py) if py else None,
        "uv": None,
        "ffmpeg": ff,
        "ffprobe": find_ffprobe(),
        "whisper": whisper_ready(),
        "upscale_weights": weights_ready(),
        "torch": torch_info.get("version") if torch_info else None,
        "torch_device": torch_info.get("device") if torch_info else None,
        "gpu": ninfo.get("gpu"),
        "cuda_driver": ninfo.get("cuda"),
        "packages": {},
    }
    if uv:
        v = run([str(uv), "--version"], check=False)
        st["uv"] = (v.stdout or "").strip() or str(uv)
    if py and py.is_file():
        v = run([str(py), "--version"], check=False)
        ver = (v.stdout or v.stderr or "").strip() or "python"
        st["python"] = f"{py} ({ver})"
        q = run([str(py), "-c",
                 "import importlib.metadata as im, json\n"
                 "out={}\n"
                 "for p in ['numpy','faster-whisper','ctranslate2','pillow',"
                 "'opencv-python-headless','onnxruntime','torch']:\n"
                 "    try: out[p]=im.version(p)\n"
                 "    except im.PackageNotFoundError: pass\n"
                 "print(json.dumps(out))"], check=False)
        if q.returncode == 0:
            try:
                st["packages"] = json.loads(q.stdout.strip() or "{}")
            except json.JSONDecodeError:
                pass
    st["ok"] = bool(
        st["python"] and st["ffmpeg"] and st["ffprobe"] and st["whisper"]
        and st["upscale_weights"] and st["packages"].get("torch")
        and st["packages"].get("faster-whisper")
    )
    return st


def print_summary(st: dict) -> None:
    print()
    print("======== setup summary ========")
    print(f"  env folder       {st.get('env_home')}")
    print(f"  uv               {st.get('uv') or 'MISSING'}")
    print(f"  python           {st.get('python') or 'MISSING'}")
    print(f"  ffmpeg           {st.get('ffmpeg') or 'MISSING'}")
    print(f"  ffprobe          {st.get('ffprobe') or 'MISSING'}")
    print(f"  Whisper model    {'ready' if st.get('whisper') else 'MISSING'}")
    print(f"  Real-ESRGAN      {'ready' if st.get('upscale_weights') else 'MISSING'}")
    print(f"  torch            {st.get('torch') or 'MISSING'}  ({st.get('torch_device') or 'n/a'})")
    print(f"  GPU              {st.get('gpu') or 'none detected'}"
          + (f"  (driver CUDA {st.get('cuda_driver')})" if st.get("cuda_driver") else ""))
    pkgs = st.get("packages") or {}
    if pkgs:
        print("  packages         " + ", ".join(f"{k} {v}" for k, v in pkgs.items()))
    print(f"  status           {'READY' if st.get('ok') else 'INCOMPLETE'}")
    if st.get("ok"):
        print("  Zoomed-shot upscaling is ON by default. Pass --no-upscale to skip it.")
        print("  The skill now runs offline. Delete the env folder to uninstall.")
    else:
        print("  One or more pieces are missing; re-run:  python scripts/introkit.py setup")
    print("================================")


def run_setup(cuda: str = "auto", ffmpeg: str = "auto", skip_model: bool = False,
              unlocked: bool = False, python_ver: str = PYTHON_VERSION,
              reexec_args: list | None = None) -> dict:
    """Install or repair the full skill environment. Idempotent."""
    home = env_home()
    _apply_env_vars(home)
    log(f"tool folder: {home}")

    uv = install_uv(home)
    log(f"uv: {uv} ({(run([str(uv), '--version'], check=False).stdout or '').strip()})")
    py = ensure_venv(home, uv, python_ver)
    log(f"python: {py}")

    # Once the venv exists, the rest of setup (imports, fetch-model) should run inside it.
    if Path(sys.executable).resolve() != py.resolve() and not os.environ.get("INTROKIT_SETUP_RUNNING"):
        args = list(reexec_args or [])
        _reexec_in_venv(py, args)

    ninfo = nvidia_info()
    if cuda == "yes":
        use_cuda_wheels = True
    elif cuda == "no":
        use_cuda_wheels = False
    else:
        use_cuda_wheels = bool(ninfo) and platform.system() in ("Windows", "Linux")
    if ninfo:
        log(f"NVIDIA GPU: {ninfo.get('gpu') or 'unknown'}  driver {ninfo.get('driver') or '?'}  "
            f"CUDA {ninfo.get('cuda') or '?'}")
    else:
        log("no NVIDIA GPU detected (PyTorch will use CPU)")

    install_python_packages(home, uv, py, use_cuda_wheels, unlocked)
    torch_info = install_torch(uv, py, ninfo if cuda != "no" else {})
    ff = ensure_ffmpeg(home, ffmpeg)
    if not skip_model:
        fetch_whisper(py)
    else:
        log("skipping Whisper download (--skip-model)")
    fetch_upscale_weights()

    st = collect_status(py, torch_info, ninfo, ff)
    status_path(home).write_text(json.dumps(st, indent=2), encoding="utf-8")
    print_summary(st)
    if not st["ok"]:
        raise SystemExit(1)
    return st


def ensure_setup_auto() -> None:
    """If the environment is incomplete, install it, then return (caller re-execs / continues)."""
    if setup_is_ready():
        return
    log("environment incomplete; installing uv, Python, packages, models and ffmpeg (one-time, may take several minutes)")
    home = env_home()
    _apply_env_vars(home)
    uv = install_uv(home)
    py = ensure_venv(home, uv)
    kit = Path(__file__).resolve().parent / "introkit.py"
    env = dict(os.environ, INTROKIT_NO_REEXEC="1", INTROKIT_SETUP_RUNNING="1", INTROKIT_NO_SETUP="1")
    p = subprocess.run([str(py), str(kit), "setup"], env=env)
    if p.returncode:
        raise SystemExit(p.returncode)
    if not setup_is_ready():
        raise SystemExit("[setup] finished but the environment is still incomplete; re-run: python scripts/introkit.py setup")


def cmd_setup(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="introkit setup",
        description="Install or repair the skill environment on this machine "
                    "(uv, Python, packages, PyTorch, ffmpeg, Whisper, Real-ESRGAN). "
                    "Safe to re-run; already-present pieces are skipped.",
    )
    ap.add_argument("--cuda", choices=("auto", "yes", "no"), default="auto",
                    help="install NVIDIA CUDA wheels when a GPU is present (default auto)")
    ap.add_argument("--ffmpeg", choices=("auto", "portable", "skip"), default="auto",
                    dest="ffmpeg_mode")
    ap.add_argument("--skip-model", action="store_true", help="do not download Whisper large-v3-turbo")
    ap.add_argument("--unlocked", action="store_true",
                    help="install from requirements.txt minimums instead of the tested lock")
    ap.add_argument("--python", default=PYTHON_VERSION, help="uv-managed CPython version (default 3.11)")
    ap.add_argument("--check", action="store_true", help="print status and exit 0 if ready, 1 if not (no installs)")
    a = ap.parse_args(argv)
    if a.check:
        st = collect_status(venv_python(), {}, nvidia_info(), find_ffmpeg())
        # fill torch if importable
        py = venv_python()
        if py.is_file() and have_import("torch", py):
            q = run([str(py), "-c",
                     "import torch, json; print(json.dumps({'version': torch.__version__, "
                     "'device': 'cuda' if torch.cuda.is_available() else 'cpu'}))"], check=False)
            if q.returncode == 0:
                try:
                    info = json.loads(q.stdout.strip())
                    st["torch"] = info.get("version")
                    st["torch_device"] = info.get("device")
                except json.JSONDecodeError:
                    pass
            st = collect_status(py, {"version": st.get("torch"), "device": st.get("torch_device")},
                                nvidia_info(), find_ffmpeg())
        print_summary(st)
        return 0 if st.get("ok") and setup_is_ready() else 1
    passthrough = list(argv or [])
    run_setup(cuda=a.cuda, ffmpeg=a.ffmpeg_mode, skip_model=a.skip_model,
              unlocked=a.unlocked, python_ver=a.python, reexec_args=passthrough)
    return 0


def cmd_upscale_check(a=None) -> int:
    """Self-check: torch, weights, and one tiny inference on the active device."""
    if not setup_is_ready() and os.environ.get("INTROKIT_NO_SETUP") != "1":
        log("environment incomplete; running setup first")
        run_setup()
    import torch
    import upscale
    ok, why = upscale.available()
    device = upscale.pick_device()
    print(f"[upscale] torch {torch.__version__}  cuda={torch.cuda.is_available()}  device={device}")
    print(f"[upscale] weights: {upscale.weights_dir() if ok else why}")
    if not ok:
        print(f"[upscale] FAIL: {why}")
        return 1
    model, dev = upscale.load_model(upscale.weights_dir(), upscale.DENOISE, device)
    x = torch.zeros(1, 3, 16, 16)
    if dev == "cuda":
        x = x.half().cuda()
    else:
        x = x.float()
    with torch.inference_mode():
        y = model(x)
    assert tuple(y.shape) == (1, 3, 64, 64), y.shape
    print(f"[upscale] OK  inference 16x16 -> 64x64 on {dev}")
    return 0

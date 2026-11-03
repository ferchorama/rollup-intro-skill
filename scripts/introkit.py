#!/usr/bin/env python3
"""introkit — build a The Rollup style broadcast cold open.

Stages (all re-runnable, OS-agnostic: Windows, macOS Intel/Apple Silicon, Linux):
    hw          report the hardware path that --device auto would choose
    probe       inspect source + reference, report the facts you must match
    transcribe  word-timestamped ASR (faster-whisper; CUDA if present, else CPU int8)
    words       cut per-shot word timings out of a word-level transcript
    assemble    sets.json (beats -> pieces in/out + script text) -> project + words (script spelling wins)
    vet         selection-time checks (cached): stutters, last-word acoustic end (--write fixes outs), dips, headroom
    frame       face-safe zoom; --layout side (default): one speaker, face on a third, text opposite
    build       picture -> graphics -> audio -> mux (SAR 1:1, bt709, AAC remaster, faststart)
    preview     small H.264 review copy (NVENC / VideoToolbox if present, else libx264)
    qc          four decisive gates in one run (format, audio, framing, speech) + contact sheet

Hardware: --device auto|cuda|qsv|d3d11va|dxva2|vaapi|videotoolbox|cpu (default auto; also
project key "device" or env INTROKIT_DEVICE; --hwaccel pins decode only). Each tier is
self-tested (1 s decode + encode) and falls back to CPU. Previews use nvenc / qsv / amf /
videotoolbox / libx264; the deliverable is ALWAYS libx264 CRF 15 preset medium.
Subcommands: hw, env, fetch-model, probe, transcribe [--diarize], diarize, voiceprint,
assemble, words, vet, frame, build, preview, qc. Transcription = scripts/transcribe.py (Whisper large-v3-turbo);
speakers = scripts/speakers.py (ONNX WeSpeaker embeddings, no token).

Config lives in a project JSON (see project.example.json). No path is hardcoded;
ffmpeg/ffprobe are found on PATH or passed explicitly; fonts are found in the project's
fonts_dir, --fonts, INTROKIT_FONTS, or the OS font folders.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import platform
import tempfile
from pathlib import Path

# --------------------------------------------------------------------------- shell

# --------------------------------------------------------------------------- environment
#
# Everything the tool installs lives in ONE folder: ROLLUP_INTRO_HOME, default
# <skill>/.rollup-intro-env, created by setup.ps1 / setup.sh with astral uv:
#     venv/            uv-managed virtualenv (Python + all pip deps)
#     models/          Whisper large-v3-turbo (faster-whisper / CTranslate2 format)
#     hf/              HF_HOME (Hugging Face cache, tokens never stored by the tool)
#     tools/ffmpeg/    optional portable ffmpeg (used only if no --ffmpeg / INTROKIT_FFMPEG)
#     uv/, python/, cache/   uv binary, uv-managed CPython, uv cache

SKILL_DIR = Path(__file__).resolve().parent.parent
WHISPER_MODEL = "large-v3-turbo"        # always; CPU uses the same model with int8 compute


def env_home() -> Path:
    h = os.environ.get("ROLLUP_INTRO_HOME")
    return Path(h).expanduser().resolve() if h else SKILL_DIR / ".rollup-intro-env"


def env_python() -> Path:
    v = env_home() / "venv"
    return v / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _maybe_reexec(argv: list[str]) -> None:
    """If the uv env exists and we are not running inside it, re-run there, so every
    dependency (faster-whisper, numpy, Pillow, OpenCV, onnxruntime) comes from one place.
    Set INTROKIT_NO_REEXEC=1 to use the current interpreter instead."""
    py = env_python()
    if os.environ.get("INTROKIT_NO_REEXEC") or not py.exists():
        return
    if Path(sys.prefix).resolve() == (env_home() / "venv").resolve():
        return
    env = dict(os.environ, INTROKIT_NO_REEXEC="1")
    sys.exit(subprocess.run([str(py), str(Path(__file__).resolve()), *argv], env=env).returncode)


def _env_setup() -> None:
    home = env_home()
    if home.exists():
        os.environ.setdefault("HF_HOME", str(home / "hf"))


def _cuda_dll_bootstrap() -> None:
    """CTranslate2 needs cuBLAS + cuDNN. setup installs them as pip wheels (nvidia-*-cu12)
    inside the venv; make them findable before ctranslate2 is imported. No-op elsewhere."""
    try:
        import importlib.util
        spec = importlib.util.find_spec("nvidia")
        roots = list(spec.submodule_search_locations or []) if spec else []
    except Exception:
        roots = []
    for root in roots:
        for pkg in Path(root).iterdir():
            if os.name == "nt":
                b = pkg / "bin"
                if b.is_dir():
                    try:
                        os.add_dll_directory(str(b))
                    except OSError:
                        pass
                    os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")
            elif sys.platform.startswith("linux"):
                import ctypes
                for so in sorted((pkg / "lib").glob("lib*.so*")) if (pkg / "lib").is_dir() else []:
                    if any(k in so.name for k in ("cublas", "cudnn", "nvrtc")):
                        try:
                            ctypes.CDLL(str(so), mode=ctypes.RTLD_GLOBAL)
                        except OSError:
                            pass


def _which(name: str, override: str | None = None) -> str:
    """--ffmpeg/--ffprobe > INTROKIT_FFMPEG/INTROKIT_FFPROBE > PATH > env tools/ffmpeg."""
    if override:
        return override
    e = os.environ.get(f"INTROKIT_{name.upper()}")
    if e:
        return e
    found = shutil.which(name)
    if not found:
        for cand in (env_home() / "tools" / "ffmpeg" / "bin").glob(name + "*"):
            if cand.stem == name:
                return str(cand)
        sys.exit(f"[introkit] {name} not found on PATH or in {env_home() / 'tools' / 'ffmpeg' / 'bin'}; "
                 f"run setup (see TOOLS.md) or pass --{name} explicitly")
    return found


def run(cmd: list[str], check: bool = True, binary: bool = False) -> subprocess.CompletedProcess:
    if binary:
        p = subprocess.run(cmd, capture_output=True)
        p.stderr = p.stderr.decode("utf-8", "replace")
    else:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        sys.exit("[introkit] command failed:\n  " + " ".join(cmd) + "\n" + p.stderr[-3000:])
    return p


def ffprobe_json(ffprobe: str, path: Path) -> dict:
    p = run([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    return json.loads(p.stdout)


def count_frames(ffprobe: str, path: Path) -> int:
    p = run([ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)])
    return int(p.stdout.strip() or 0)


def duration(ffprobe: str, path: Path) -> float:
    return float(ffprobe_json(ffprobe, path)["format"]["duration"])


# --------------------------------------------------------------------------- hardware
#
# Every accelerator is optional. Detection order for --device auto: cuda -> apple -> cpu.
# GPU paths are used ONLY for: source decode (base picture, frame sampling), Whisper,
# speaker-embedding ONNX, and review previews. The deliverable encode is libx264 CRF 15
# on every machine, so the delivered file does not depend on the hardware.

DEVICES = ("auto", "cuda", "qsv", "d3d11va", "dxva2", "vaapi", "videotoolbox", "cpu")
_DEV_ALIASES = {"gpu": "cuda", "nvidia": "cuda", "nvenc": "cuda", "intel": "qsv",
                "quicksync": "qsv", "amd": "d3d11va", "amf": "d3d11va", "d3d11": "d3d11va",
                "apple": "videotoolbox", "mac": "videotoolbox", "mps": "videotoolbox",
                "metal": "videotoolbox", "none": "cpu"}
HWACCELS = ("auto", "none", "cuda", "qsv", "d3d11va", "dxva2", "vaapi", "videotoolbox")

# tier -> (OSes it can exist on, ffmpeg decode hwaccel, hw frame format, preview encoder)
# Auto order: NVIDIA -> Intel Quick Sync -> Windows D3D11/DXVA2 (AMD & others, AMF encoder)
#             -> Linux VAAPI -> macOS VideoToolbox -> CPU
TIERS = [
    ("cuda",         ("win32", "linux"),  "cuda",         "cuda",            "h264_nvenc"),
    ("qsv",          ("win32", "linux"),  "qsv",          "qsv",             "h264_qsv"),
    ("d3d11va",      ("win32",),          "d3d11va",      "d3d11",           "h264_amf"),
    ("dxva2",        ("win32",),          "dxva2",        "dxva2_vld",       "h264_amf"),
    ("vaapi",        ("linux",),          "vaapi",        "vaapi",           "h264_vaapi"),
    ("videotoolbox", ("darwin",),         "videotoolbox", "videotoolbox_vld", "h264_videotoolbox"),
]


def _ff_list(ff: str, flag: str) -> str:
    try:
        p = subprocess.run([ff, "-hide_banner", flag], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
        return p.stdout + p.stderr
    except Exception:
        return ""


def _vaapi_device() -> str | None:
    for n in range(128, 136):
        d = Path(f"/dev/dri/renderD{n}")
        if d.exists():
            return str(d)
    return None


def physical_cores() -> int:
    """Physical cores (hyper-threads excluded): Whisper cpu_threads and ONNX intra-op threads."""
    try:
        import psutil
        n = psutil.cpu_count(logical=False)
        if n:
            return int(n)
    except Exception:
        pass
    try:
        if sys.platform == "darwin":
            return int(subprocess.run(["sysctl", "-n", "hw.physicalcpu"], capture_output=True,
                                      text=True, timeout=5).stdout.strip())
        if sys.platform.startswith("linux"):
            cores, phys = set(), None
            for ln in Path("/proc/cpuinfo").read_text().splitlines():
                if ln.startswith("physical id"):
                    phys = ln.split(":")[1].strip()
                elif ln.startswith("core id"):
                    cores.add((phys, ln.split(":")[1].strip()))
            if cores:
                return len(cores)
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            ln = wintypes.DWORD(0)
            k.GetLogicalProcessorInformationEx(0, None, ctypes.byref(ln))   # RelationProcessorCore
            buf = ctypes.create_string_buffer(ln.value)
            if k.GetLogicalProcessorInformationEx(0, buf, ctypes.byref(ln)):
                n, off = 0, 0
                while off < ln.value:
                    size = int.from_bytes(buf.raw[off + 4:off + 8], "little")
                    n, off = n + 1, off + size
                if n:
                    return n
    except Exception:
        pass
    return os.cpu_count() or 4


def decode_args(accel: str | None) -> list[str]:
    """Input-side ffmpeg args for a decode tier (same args the self-test verified)."""
    if not accel or accel in ("cpu", "none"):
        return []
    a = ["-hwaccel", accel]
    if accel == "vaapi" and _vaapi_device():
        a += ["-hwaccel_device", _vaapi_device()]
    return a


def encoder_args(enc: str, quality: int = 23) -> tuple[list[str], list[str], str]:
    """(pre-input args, codec args, extra -vf tail) for a PREVIEW encoder. Never the deliverable."""
    if enc == "h264_nvenc":
        return [], ["-c:v", enc, "-preset", "p6", "-cq", str(quality), "-b:v", "0"], ""
    if enc == "h264_qsv":
        return [], ["-c:v", enc, "-preset", "medium", "-global_quality", str(quality)], ",format=nv12"
    if enc == "h264_amf":
        return [], ["-c:v", enc, "-quality", "balanced", "-rc", "cqp",
                    "-qp_i", str(quality), "-qp_p", str(quality + 2)], ",format=nv12"
    if enc == "h264_vaapi":
        dev = _vaapi_device() or "/dev/dri/renderD128"
        return ["-vaapi_device", dev], ["-c:v", enc, "-qp", str(quality)], ",format=nv12,hwupload"
    if enc == "h264_videotoolbox":
        return [], ["-c:v", enc, "-b:v", "5M"], ""
    return [], ["-c:v", "libx264", "-preset", "veryfast", "-crf", str(quality), "-threads", "0"], ""


def _probe_ok(cmd: list[str]) -> bool:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=60)
        return p.returncode == 0
    except Exception:
        return False


def _test_decode(ff: str, accel: str, fmt: str, clip: Path) -> bool:
    """1 s decode that keeps frames on the device and downloads them: a silent software
    fallback cannot pass, because hwdownload rejects software frames."""
    return _probe_ok([ff, "-hide_banner", "-v", "error", *decode_args(accel),
                      "-hwaccel_output_format", fmt, "-i", str(clip),
                      "-vf", "hwdownload,format=nv12", "-f", "null", "-"])


def _test_encode(ff: str, enc: str) -> bool:
    """1 s encode: an encoder listed by ffmpeg -encoders is not proof of a working driver."""
    pre, codec, tail = encoder_args(enc)
    return _probe_ok([ff, "-hide_banner", "-v", "error", *pre, "-f", "lavfi", "-i",
                      "testsrc2=s=640x360:r=30:d=1", "-vf", "format=yuv420p" + tail,
                      *codec, "-frames:v", "30", "-f", "null", "-"])


def detect_hw(ff: str, only: str | None = None) -> dict:
    """Probe every tier that could exist on this OS and self-test it. Never raises."""
    from concurrent.futures import ThreadPoolExecutor
    plat = "win32" if os.name == "nt" else sys.platform
    plat = "linux" if plat.startswith("linux") else plat
    hw = {"os": platform.system(), "machine": platform.machine(),
          "python": platform.python_version(), "physical_cores": physical_cores(),
          "logical_cores": os.cpu_count()}
    hwaccels = _ff_list(ff, "-hwaccels").split()
    encoders = _ff_list(ff, "-encoders")
    smi = shutil.which("nvidia-smi")
    gpu = None
    if smi:
        try:
            q = subprocess.run([smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
                               capture_output=True, text=True, timeout=20)
            gpu = q.stdout.strip().splitlines()[0] if q.returncode == 0 and q.stdout.strip() else None
        except Exception:
            gpu = None
    hw["nvidia_gpu"] = gpu
    hw["apple_silicon"] = plat == "darwin" and platform.machine() == "arm64"
    tiers = {}
    with tempfile.TemporaryDirectory() as td:
        clip = Path(td) / "t.mp4"
        _probe_ok([ff, "-hide_banner", "-v", "error", "-f", "lavfi", "-i",
                   "testsrc2=s=640x360:r=30:d=1", "-c:v", "libx264", "-preset", "ultrafast",
                   "-pix_fmt", "yuv420p", str(clip)])
        jobs = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            for name, oses, accel, fmt, enc in TIERS:
                if only and name != only:
                    continue
                t = {"os_ok": plat in oses, "listed_decode": accel in hwaccels,
                     "listed_encode": enc in encoders, "decode": False, "encode": False}
                tiers[name] = t
                if not t["os_ok"]:
                    continue
                if t["listed_decode"] and clip.exists():
                    jobs[(name, "decode")] = ex.submit(_test_decode, ff, accel, fmt, clip)
                if t["listed_encode"]:
                    jobs[(name, "encode")] = ex.submit(_test_encode, ff, enc)
            for (name, kind), fu in jobs.items():
                tiers[name][kind] = bool(fu.result())
    hw["tiers"] = tiers
    _cuda_dll_bootstrap()
    try:
        import ctranslate2
        hw["ctranslate2"] = ctranslate2.__version__
        hw["whisper_cuda"] = ctranslate2.get_cuda_device_count() > 0
    except Exception:
        hw["ctranslate2"] = None
        hw["whisper_cuda"] = False
    try:
        import torch
        hw["torch_mps"] = bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    except Exception:
        hw["torch_mps"] = False
    try:
        import onnxruntime as ort
        hw["onnx_providers"] = ort.get_available_providers()
    except Exception:
        hw["onnx_providers"] = []
    return hw


def resolve_device(requested: str | None, proj: dict | None = None) -> str:
    d = (requested or (proj or {}).get("device") or os.environ.get("INTROKIT_DEVICE") or "auto")
    d = _DEV_ALIASES.get(str(d).lower(), str(d).lower())
    if d not in DEVICES:
        sys.exit(f"[introkit] unknown --device {d!r}; use one of {', '.join(DEVICES)}")
    return d


def plan_hw(ff: str, requested: str | None, proj: dict | None = None, quiet: bool = False,
            hwaccel: str | None = None) -> dict:
    """Turn --device / --hwaccel into concrete choices. Auto walks the tiers in order and
    takes the first decode and the first preview encoder that PASSED their 1 s self-test;
    anything that fails falls through to the next tier and finally to CPU."""
    dev = resolve_device(requested, proj)
    hwaccel = (hwaccel or (proj or {}).get("hwaccel") or os.environ.get("INTROKIT_HWACCEL") or "auto").lower()
    if hwaccel not in HWACCELS:
        sys.exit(f"[introkit] unknown --hwaccel {hwaccel!r}; use one of {', '.join(HWACCELS)}")
    cores = physical_cores()
    plan = {"requested": dev, "hwaccel_requested": hwaccel, "using": "cpu", "decode": "cpu",
            "preview_encoder": "libx264", "whisper": "cpu/int8", "cpu_threads": cores,
            "onnx_providers": ["CPUExecutionProvider"],
            "final_encoder": "libx264 crf 15 preset medium"}
    if dev == "cpu":
        plan["hw"] = {"os": platform.system(), "machine": platform.machine(), "physical_cores": cores}
    else:
        only = dev if dev != "auto" else None
        hw = detect_hw(ff, only)
        tiers = hw["tiers"]
        order = [t[0] for t in TIERS]
        dec_order = [hwaccel] if hwaccel not in ("auto", "none") else order
        if hwaccel == "none":
            dec_order = []
        for name in dec_order:
            if tiers.get(name, {}).get("decode"):
                plan["decode"] = name
                break
        else:
            if hwaccel not in ("auto", "none"):
                print(f"[introkit] --hwaccel {hwaccel} failed its self-test; decoding on CPU", flush=True)
        for name in order:
            if tiers.get(name, {}).get("encode"):
                plan["preview_encoder"] = dict((t[0], t[4]) for t in TIERS)[name]
                break
        if only and plan["decode"] == "cpu" and plan["preview_encoder"] == "libx264":
            print(f"[introkit] --device {dev} is not usable here (self-test failed); using CPU", flush=True)
        enc_tier = {t[4]: t[0] for t in TIERS}.get(plan["preview_encoder"])
        plan["using"] = plan["decode"] if plan["decode"] != "cpu" else (enc_tier or "cpu")
        if dev in ("auto", "cuda") and hw.get("whisper_cuda"):
            plan["whisper"] = "cuda/float16"
        prov = hw.get("onnx_providers", [])
        pref = {"auto": ["CUDAExecutionProvider", "OpenVINOExecutionProvider", "DmlExecutionProvider",
                         "CoreMLExecutionProvider"],
                "cuda": ["CUDAExecutionProvider"], "qsv": ["OpenVINOExecutionProvider"],
                "d3d11va": ["DmlExecutionProvider"], "dxva2": ["DmlExecutionProvider"],
                "vaapi": ["OpenVINOExecutionProvider"],
                "videotoolbox": ["CoreMLExecutionProvider"]}[dev]
        for p in pref:
            if p in prov:
                plan["onnx_providers"] = [p, "CPUExecutionProvider"]
                break
        plan["hw"] = hw
    if not quiet:
        print(f"[introkit] device: requested={dev} hwaccel={hwaccel} decode={plan['decode']} "
              f"preview={plan['preview_encoder']} whisper={plan['whisper']} "
              f"onnx={plan['onnx_providers'][0]} cpu_threads={cores} "
              f"final={plan['final_encoder']}", flush=True)
    return plan


def onnx_session(model_path, plan: dict | None = None):
    """onnxruntime session with the planned provider and intra-op threads = physical cores.
    Use this for the speaker-embedding (voiceprint) model."""
    import onnxruntime as ort
    so = ort.SessionOptions()
    so.intra_op_num_threads = (plan or {}).get("cpu_threads") or physical_cores()
    so.inter_op_num_threads = 1
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    provs = (plan or {}).get("onnx_providers") or ["CPUExecutionProvider"]
    try:
        return ort.InferenceSession(str(model_path), sess_options=so, providers=provs)
    except Exception as e:          # provider present but broken -> CPU
        print(f"[introkit] onnx provider {provs[0]} failed ({e}); using CPU", flush=True)
        return ort.InferenceSession(str(model_path), sess_options=so,
                                    providers=["CPUExecutionProvider"])


def onnx_providers(plan: dict | None = None) -> list[str]:
    return (plan or {}).get("onnx_providers", ["CPUExecutionProvider"])


def hwaccel_args(plan: dict | None) -> list[str]:
    return decode_args((plan or {}).get("decode"))


def run_decode(cmd_before_i: list[str], rest: list[str], plan: dict | None) -> subprocess.CompletedProcess:
    """Run an ffmpeg command with the planned hardware decode; on any failure retry on CPU.
    Decoded pixels are identical (H.264/HEVC decoding is bit-exact), so the output is too."""
    hx = hwaccel_args(plan)
    if hx:
        p = run(cmd_before_i + hx + rest, check=False)
        if p.returncode == 0:
            return p
        print(f"[introkit] {hx[1]} decode failed, retrying on CPU", flush=True)
    return run(cmd_before_i + rest)


# --------------------------------------------------------------------------- fonts

# --------------------------------------------------------------------------- bundled assets
#
# The skill ships its own music bed, SFX, logo and fonts in <skill>/assets/. Any project key
# overrides them; leaving a key out uses the bundled file. Paths are script-relative, so a
# copied skill folder works on any Windows/macOS/Linux machine.

ASSETS = Path(__file__).resolve().parent.parent / "assets"
DEFAULT_ASSETS = {
    "music": ASSETS / "music" / "rollup_music_bed_32s.mp3",
    "logo": ASSETS / "logo" / "the_rollup_logo_white_dropshadow.png",
    "fonts_dir": ASSETS / "fonts",
    "sfx": {"boom": ASSETS / "sfx" / "boom_deep.s16le-44100-stereo.raw",
            "tape": ASSETS / "sfx" / "tape_stop.s16le-44100-stereo.raw",
            "sting": ASSETS / "sfx" / "sting_two_note.s16le-44100-stereo.raw"},
}


def apply_asset_defaults(proj: dict) -> dict:
    for k in ("music", "logo", "fonts_dir"):
        if k not in proj and DEFAULT_ASSETS[k].exists():
            proj[k] = str(DEFAULT_ASSETS[k])
    sfx = proj.setdefault("sfx", {})
    for k, v in DEFAULT_ASSETS["sfx"].items():
        if k not in sfx and v.exists():
            sfx[k] = str(v)
    return proj


def sniff_raw(path: str, fmt: str | None) -> str | None:
    """Explicit *_format wins. Otherwise a file with a real container header (WAV, MP3/ID3,
    FLAC, Ogg, MP4/M4A) decodes normally, and a headerless file is read as s16le 44.1 kHz
    stereo (what some generators return, even when the file is named .mp3)."""
    if fmt:
        return fmt
    try:
        head = Path(path).read_bytes()[:12]
    except OSError:
        return None
    if head[:4] in (b"RIFF", b"fLaC", b"OggS", b"FORM") or head[:3] == b"ID3" \
            or head[4:8] == b"ftyp" or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return None
    return "s16le"


def _font_dirs(extra: list[str | None]) -> list[Path]:
    home = Path.home()
    dirs = [Path(x) for x in extra if x]
    dirs.append(ASSETS / "fonts")
    if sys.platform == "darwin":
        dirs += [home / "Library" / "Fonts", Path("/Library/Fonts"), Path("/System/Library/Fonts")]
    elif os.name == "nt":
        dirs += [Path(os.environ.get("LOCALAPPDATA", home / "AppData" / "Local")) / "Microsoft" / "Windows" / "Fonts",
                 Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"]
    else:
        dirs += [home / ".local" / "share" / "fonts", home / ".fonts",
                 Path("/usr/local/share/fonts"), Path("/usr/share/fonts")]
    return [d for d in dirs if d.is_dir()]


def find_font(name: str, dirs: list[Path]) -> str:
    """Exact filename first, then a stem match (PlayfairDisplay-Italic.ttf also matches
    PlayfairDisplay-Italic[wght].ttf), searching sub-folders too."""
    stem = Path(name).stem.split("[")[0].lower()
    for d in dirs:
        if (d / name).is_file():
            return str(d / name)
    for d in dirs:
        try:
            cands = sorted(d.rglob("*"))
        except OSError:
            continue
        for f in cands:
            if f.suffix.lower() in (".ttf", ".otf") and f.stem.split("[")[0].lower() == stem:
                return str(f)
    sys.exit(f"[introkit] font {name!r} not found in: " + ", ".join(str(d) for d in dirs)
             + "\n  set fonts_dir in the project, pass --fonts DIR, or set INTROKIT_FONTS")


# --------------------------------------------------------------------------- probe

def cmd_probe(a) -> None:
    ffprobe = _which("ffprobe", a.ffprobe)
    ffmpeg = _which("ffmpeg", a.ffmpeg)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    for label, path in (("src", Path(a.src)), ("ref", Path(a.ref) if a.ref else None)):
        if path is None:
            continue
        info = ffprobe_json(ffprobe, path)
        v = next(s for s in info["streams"] if s.get("width"))
        au = next((s for s in info["streams"] if s.get("sample_rate")), None)
        d = dict(duration=round(float(info["format"]["duration"]), 3),
                 width=v["width"], height=v["height"],
                 fps=v.get("r_frame_rate"), codec=v.get("codec_name"),
                 audio=None)
        if au:
            d["audio"] = dict(codec=au.get("codec_name"), rate=au.get("sample_rate"),
                              channels=au.get("channels"), bitrate=au.get("bit_rate"))
            # dual-mono test: identical channels means one programme bus
            p = run([ffmpeg, "-hide_banner", "-nostats", "-t", "60", "-i", str(path),
                     "-filter_complex",
                     "[0:a]astats=metadata=0:measure_perchannel=Peak_level+RMS_level:"
                     "measure_overall=none[o]", "-map", "[o]", "-f", "null", "-"], check=False)
            vals = [ln.split(":")[-1].strip() for ln in p.stderr.splitlines()
                    if "RMS level dB" in ln]
            d["audio"]["channel_rms"] = vals
            d["audio"]["dual_mono"] = len(vals) == 2 and vals[0] == vals[1]
        report[label] = d
    # reference black frames give you the dip placement
    if a.ref:
        p = run([ffmpeg, "-hide_banner", "-nostats", "-i", a.ref,
                 "-vf", "blackdetect=d=0.08:pix_th=0.10", "-an", "-f", "null", "-"], check=False)
        report["ref"]["black_runs"] = [ln.strip() for ln in p.stderr.splitlines()
                                       if "black_start" in ln]
    print(json.dumps(report, indent=2))
    (out / "probe.json").write_text(json.dumps(report, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- words

def cmd_words(a) -> None:
    proj = json.loads(Path(a.project).read_text(encoding="utf-8"))
    fps = proj.get("fps", 30)
    tr = json.loads(Path(a.transcript).read_text(encoding="utf-8"))
    stream = []
    for cue in tr.get("cues", tr if isinstance(tr, list) else []):
        for w in cue.get("words", []):
            stream.append((float(w["start"]), float(w["end"]), str(w["text"]).strip()))
    stream.sort()
    out = {}
    for shot in proj["shots"]:
        if shot["kind"] != "clip":
            continue
        t0 = float(shot["src_in"])
        t1 = t0 + shot["frames"] / fps
        rows = []
        for (s, e, txt) in stream:
            if s < t0 - 0.06 or e > t1 + 0.10:
                continue
            clean = txt.strip().strip(",.")
            if not clean:
                continue
            if clean.startswith("-") and rows:          # merge "24" + "-7"
                rows[-1]["w"] += clean
                rows[-1]["e"] = round(e - t0, 3)
                continue
            rows.append({"w": clean, "t": round(s - t0, 3), "e": round(e - t0, 3)})
        out[str(shot["i"])] = rows
        print(f"shot {shot['i']:02d}  {len(rows):2d} words  "
              + " ".join(r["w"] for r in rows))
    Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")
    print("wrote", a.out)


# --------------------------------------------------------------------------- build

def _fonts(proj) -> dict:
    dirs = _font_dirs([proj.get("fonts_dir"), proj.get("_fonts_cli"), os.environ.get("INTROKIT_FONTS")])
    names = proj.get("fonts", {})
    return {"anton": find_font(names.get("anton", "Anton-Regular.ttf"), dirs),
            "playfair": find_font(names.get("playfair", "PlayfairDisplay-Italic.ttf"), dirs),
            "barlow": find_font(names.get("barlow", "BarlowCondensed-Bold.ttf"), dirs)}


def shot_rect(proj: dict, shot: dict) -> dict:
    """Start crop of a clip shot in SOURCE pixels. Per-shot crop_w / crop_h / crop_y override
    the project-wide crop {w,h,y}; crop_x is per shot (default 0). Old projects that only have
    the global crop and crop_x render exactly as before."""
    c = proj.get("crop") or {}
    w = int(shot.get("crop_w", c.get("w", proj.get("width", 1920))))
    h = int(shot.get("crop_h", c.get("h", round(w * proj.get("height", 1080) / proj.get("width", 1920)))))
    return {"x": int(shot.get("crop_x", 0)), "y": int(shot.get("crop_y", c.get("y", 0))), "w": w, "h": h}


def _ease(p: float, mode: str = "inout") -> float:
    p = min(1.0, max(0.0, p))
    return p if mode == "linear" else 0.5 - 0.5 * math.cos(math.pi * p)


def shot_rect_at(proj: dict, shot: dict, k: int) -> dict:
    """Crop rect (source px, float) shown on output frame k of the shot: the start crop, or
    for a face-anchored push the eased interpolation start -> shot['push'] end crop."""
    s = shot_rect(proj, shot)
    e = shot.get("push")
    n = int(shot.get("frames", 1))
    if not e:
        return {k2: float(v) for k2, v in s.items()}
    t = _ease(k / max(1, n - 1), shot.get("push_ease", "inout"))
    return {q: s[q] + (float(e[q]) - s[q]) * t for q in ("x", "y", "w", "h")}


def _push_filter(proj: dict, shot: dict, src_wh: tuple[int, int]) -> str:
    """Sub-pixel push-in: `perspective` (sense=source, eval=frame) sends the eased crop rect
    of every frame to the output corners in ONE cubic resample, so there is no integer-step
    jitter (zoompan rounds x/y) and the frame size never changes. Start and end rects both
    contain the face box + margins (see `frame`), so every in-between rect does too."""
    s, e, n = shot_rect(proj, shot), shot["push"], int(shot["frames"])
    pr = f"clip(in/{max(1, n - 1)},0,1)"     # quoted below, so the commas are safe
    ez = pr if shot.get("push_ease", "inout") == "linear" else f"(0.5-0.5*cos(PI*{pr}))"

    def lerp(a, b):
        return f"({a:.3f}+({float(b) - a:.3f})*{ez})"
    x0, y0 = lerp(s["x"], e["x"]), lerp(s["y"], e["y"])
    x1 = lerp(s["x"] + s["w"], float(e["x"]) + float(e["w"]))
    y2 = lerp(s["y"] + s["h"], float(e["y"]) + float(e["h"]))
    vf = (f"perspective=x0='{x0}':y0='{y0}':x1='{x1}':y1='{y0}':x2='{x0}':y2='{y2}'"
          f":x3='{x1}':y3='{y2}':interpolation=cubic:sense=source:eval=frame")
    W, H = proj["width"], proj["height"]
    if tuple(src_wh) != (W, H):
        vf += f",scale={W}:{H}:flags=lanczos"
    return vf


def _src_wh(ffprobe: str, src: str) -> tuple[int, int]:
    v = next(s for s in ffprobe_json(ffprobe, Path(src))["streams"] if s.get("width"))
    return int(v["width"]), int(v["height"])


# Premiere / QuickTime read SAR and colour from the H.264 VUI: tag every encode bt709 limited
# range and force SAR 1:1 (setsar=1 in the graph + h264_metadata on the final), LESSONS B9 / C1.
BT709 = ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv"]
H264_VUI = ("h264_metadata=sample_aspect_ratio=1/1:video_full_range_flag=0:colour_primaries=1:"
            "transfer_characteristics=1:matrix_coefficients=1")


def stage_base(proj, ff, ffprobe, work: Path) -> Path:
    seg = work / "seg"
    seg.mkdir(parents=True, exist_ok=True)
    fps, W, H = proj.get("fps", 30), proj["width"], proj["height"]
    src_wh = None
    files = []
    for shot in proj["shots"]:
        dst = seg / f"s{shot['i']:02d}.mp4"
        n = int(shot["frames"])
        if shot["kind"] == "clip":
            r = shot_rect(proj, shot)
            if shot.get("push"):
                # face-anchored push-in written by `introkit.py frame`
                src_wh = src_wh or _src_wh(ffprobe, proj["src"])
                vf = f"{_push_filter(proj, shot, src_wh)},setsar=1,{proj['grade']}"
            else:
                vf = (f"crop={r['w']}:{r['h']}:{r['x']}:{r['y']},"
                      f"scale={W}:{H}:flags=lanczos,setsar=1,{proj['grade']}")
                if shot.get("punch"):
                    # legacy centre-anchored zoom: eats headroom equally top and bottom, unsafe
                    # for a head near the top edge -> prefer `frame` (writes shot["push"])
                    print(f"[introkit] shot {shot['i']}: legacy centre punch {shot['punch']} "
                          f"(run `frame` for a face-anchored push)", flush=True)
                    vf += (f",zoompan=z='1+{shot['punch']}*on/{n}'"
                           f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={W}x{H}:fps={fps},setsar=1")
            run_decode([ff, "-y", "-hide_banner", "-loglevel", "error"],
                ["-ss", f"{shot['src_in']:.4f}", "-i", proj["src"],
                 "-t", f"{n / fps + 0.5:.4f}", "-vf", vf, "-frames:v", str(n),
                 "-r", str(fps), "-an", "-c:v", "libx264", "-threads", "0", "-crf", "15",
                 "-preset", "medium", "-pix_fmt", "yuv420p", *BT709,
                 "-video_track_timescale", "30000", str(dst)], proj.get("_hw"))
        else:
            run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                 "-i", f"color=c=black:s={W}x{H}:r={fps}:d={n / fps + 0.2:.4f}",
                 "-vf", "setsar=1",
                 "-frames:v", str(n), "-r", str(fps), "-c:v", "libx264", "-threads", "0", "-crf", "15",
                 "-preset", "veryfast", "-pix_fmt", "yuv420p", *BT709,
                 "-video_track_timescale", "30000", str(dst)])
        got = count_frames(ffprobe, dst)
        if got != n:
            sys.exit(f"[introkit] shot {shot['i']}: {got} frames, expected {n}")
        files.append(dst)
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{f.as_posix()}'\n" for f in files), encoding="utf-8")
    base = work / "base.mp4"
    run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", str(base)])
    return base


def stage_overlay(proj, work: Path, words: dict) -> Path:
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except ImportError:
        sys.exit("[introkit] pip install pillow")
    import numpy as np

    fps, W, H = proj.get("fps", 30), proj["width"], proj["height"]
    ovl = work / "overlay"
    ovl.mkdir(parents=True, exist_ok=True)
    F = _fonts(proj)
    cache, tiles = {}, {}

    def font(kind, size):
        k = (kind, int(round(size)))
        if k not in cache:
            cache[k] = ImageFont.truetype(F[kind], int(round(size)))
        return cache[k]

    def tile(text, kind, size, colour):
        k = (text, kind, size, tuple(colour))
        if k in tiles:
            return tiles[k]
        px = max(8, int(round(size * 1.7)))
        f = font(kind, px)
        b = f.getbbox(text)
        pad = max(4, int(px * 0.16))
        im = Image.new("RGBA", (max(1, b[2] - b[0] + 2 * pad),
                                max(1, b[3] - b[1] + 2 * pad)), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((pad - b[0], pad - b[1]), text, font=f,
                                fill=tuple(colour) + (255,))
        tiles[k] = im
        return im

    def scrim(side, strength=0.88):
        x = np.arange(W, dtype=np.float32) / W
        y = np.arange(H, dtype=np.float32)[:, None] / H
        ramp = (np.clip((0.68 - x) / 0.68, 0, 1) ** 1.1 if side == "left"
                else np.clip((x - 0.36) / 0.64, 0, 1) ** 1.1)
        a = ramp[None, :] * strength + 0.30 * np.clip((y - 0.52) / 0.48, 0, 1) ** 1.3
        return Image.fromarray((np.clip(a, 0, 0.95) * 255).astype(np.uint8))

    SCR = {"left": scrim("left"), "right": scrim("right")}

    def ease_back(p, s=1.9):
        p = max(0.0, min(1.0, p))
        return 1 + (s + 1) * (p - 1) ** 3 + s * (p - 1) ** 2

    _mt = {}

    def matte_img(m):
        """Opaque black band x0..x1 (output px) with a feathered inner edge: hides another person
        that no crop <= the zoom cap can drop (`frame --layout side` writes shot["matte"])."""
        k = (int(m["x0"]), int(m["x1"]), int(m.get("feather", 90)))
        if k not in _mt:
            x = np.arange(W, dtype=np.float32)
            f = max(1, k[2])
            lft = (x - (k[0] - f)) / f if k[0] > 0 else np.full(W, 1e9, np.float32)
            rgt = ((k[1] + f) - x) / f if k[1] < W else np.full(W, 1e9, np.float32)
            a = np.clip(np.minimum(lft, rgt), 0, 1)
            al = Image.fromarray((np.repeat(a[None, :], H, 0) * 255).astype(np.uint8))
            _mt[k] = Image.merge("RGBA", (Image.new("L", (W, H), 0),) * 3 + (al,))
        return _mt[k]

    def page_firsts(shot):
        """Index of the first word of every caption page (it is shown capitalised)."""
        fs = {}
        for ln in shot.get("lines", []):
            for i in ln["words"]:
                pg = ln.get("page", 0)
                fs[pg] = min(fs.get(pg, 10 ** 9), i)
        return set(fs.values())

    def layout(shot):
        wl = words[str(shot["i"])]
        lines = shot["lines"]
        side = shot.get("side", "left")
        cap = shot.get("maxw", 900)
        firsts = page_firsts(shot) if proj.get("caption_capitalise", True) else set()
        laid = []
        for ln in lines:
            ws = []
            for i in ln["words"]:
                t0 = wl[i]["w"][:1].upper() + wl[i]["w"][1:] if i in firsts else wl[i]["w"]
                t = t0 if ln["kind"] == "playfair" else t0.upper()
                f = font(ln["kind"], ln["size"])
                b = f.getbbox(t)
                ws.append(dict(i=i, text=t, bbox=b, w=b[2] - b[0], h=b[3] - b[1]))
            sp = ln["size"] * 0.27
            tw = sum(w["w"] for w in ws) + sp * (len(ws) - 1)
            laid.append(dict(ln=ln, words=ws, tw=tw, sp=sp))
        for L in laid:
            if L["tw"] > cap:
                k = cap / L["tw"]
                L["ln"]["size"] *= k
                for w in L["words"]:
                    f = font(L["ln"]["kind"], L["ln"]["size"])
                    b = f.getbbox(w["text"])
                    w["bbox"], w["w"], w["h"] = b, b[2] - b[0], b[3] - b[1]
                L["sp"] = L["ln"]["size"] * 0.27
                L["tw"] = sum(w["w"] for w in L["words"]) + L["sp"] * (len(L["words"]) - 1)
        out = []
        for pg in sorted(set(L["ln"].get("page", 0) for L in laid)):
            PL = [L for L in laid if L["ln"].get("page", 0) == pg]
            total, mets = 0, []
            for L in PL:
                asc, desc = font(L["ln"]["kind"], L["ln"]["size"]).getmetrics()
                L["lh"] = asc + desc
                mets.append(asc)
                total += L["lh"] + 28
            total -= 28
            y = shot.get("cy", 520) - total / 2
            for L, asc in zip(PL, mets):
                x = 96 if side == "left" else W - 96 - L["tw"]
                for w in L["words"]:
                    gold = w["i"] in L["ln"].get("gold", [])
                    out.append(dict(i=w["i"], text=w["text"], kind=L["ln"]["kind"],
                                    size=L["ln"]["size"], page=pg,
                                    colour=(232, 178, 58) if gold else L["ln"]["colour"],
                                    gold=gold, x=x, baseline=y + asc, asc=asc, bbox=w["bbox"]))
                    x += w["w"] + L["sp"]
                y += L["lh"] + 28
        return out, side

    def text_layer(shot, t):
        ws, side = layout(shot)
        wl = words[str(shot["i"])]
        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pstart = {}
        for w in ws:
            pstart[w["page"]] = min(pstart.get(w["page"], 1e9), wl[w["i"]]["t"])
        pg0 = min(pstart)
        active = max(p for p, ts in pstart.items() if p == pg0 or t >= ts - 0.04)
        for k, w in enumerate(ws):
            if w["page"] != active:
                continue
            t0 = wl[w["i"]]["t"]
            p = ease_back((t - t0) / 0.30)
            if p <= 0.01:
                continue
            alpha = max(0.0, min(1.0, (t - t0) / 0.12))
            amp = 0.30 if w["gold"] else 0.20
            scale = (1 - amp) + amp * p
            dx = (1 - min(1, p)) * (-16 if k % 2 == 0 else 12)
            dy = (1 - min(1, p)) * 30 - t * 1.1
            b = w["bbox"]
            cx = w["x"] + (b[0] + b[2]) / 2 + dx
            cy = w["baseline"] - w["asc"] + (b[1] + b[3]) / 2 + dy
            tl = tile(w["text"], w["kind"], w["size"], w["colour"])
            f = scale / 1.7
            nw, nh = max(1, int(tl.width * f)), max(1, int(tl.height * f))
            t2 = tl.resize((nw, nh), Image.BILINEAR)
            if alpha < 0.999:
                t2.putalpha(Image.eval(t2.getchannel("A"), lambda v, al=alpha: int(v * al)))
            layer.alpha_composite(t2, (int(cx - nw / 2), int(cy - nh / 2)))
        if layer.getbbox() is None:
            return layer
        a = layer.getchannel("A")
        sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sh.putalpha(a.point(lambda v: int(v * 0.7)))
        sh = sh.filter(ImageFilter.GaussianBlur(7))
        out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        out.alpha_composite(sh, (0, 7))
        out.alpha_composite(layer)
        return out

    def grad(top, bottom, tint=None):
        a = np.zeros((H, W, 3), np.float32)
        for c in range(3):
            a[:, :, c] = np.linspace(top[c], bottom[c], H)[:, None]
        if tint:
            yy = np.linspace(0, 1, H)[:, None, None]
            xx = np.linspace(0, 1, W)[None, :, None]
            glow = np.exp(-(((xx - tint[0]) ** 2 + (yy - tint[1]) ** 2) / tint[3] ** 2)
                          ) [:, :, 0]
            for c in range(3):
                a[:, :, c] += glow * tint[2][c]
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")

    def portrait(base: Path):
        import cv2
        if proj["cards"].get("portrait_image"):
            # rule 016: a scored still (box-sized, already cropped/graded) instead of a base frame
            im = Image.open(proj["cards"]["portrait_image"]).convert("RGB")
            x0, y0, x1, y1 = proj["cards"]["portrait_box"]
            if im.size != (x1 - x0, y1 - y0):
                im = im.resize((x1 - x0, y1 - y0), Image.LANCZOS)
            frame = None
        else:
            im = None
        cap = cv2.VideoCapture(str(base)) if im is None else None
        frame = None
        for _ in range(int(proj["cards"].get("portrait_frame", 118)) if im is None else 0):
            ok, fr = cap.read()
            if not ok:
                break
            frame = fr
        if im is None:
            cap.release()
            x0, y0, x1, y1 = proj["cards"]["portrait_box"]
            im = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).crop((x0, y0, x1, y1))
        im = Image.fromarray(np.clip(np.asarray(im).astype(np.float32) * 1.07 + 5,
                                     0, 255).astype(np.uint8))
        m = Image.new("L", im.size, 0)
        ImageDraw.Draw(m).ellipse([-im.size[0] * 0.18, -im.size[1] * 0.10,
                                   im.size[0] * 1.18, im.size[1] * 1.04], fill=255)
        m = m.filter(ImageFilter.GaussianBlur(54))
        al = np.array(m).astype(np.float32) / 255.0
        yy = np.linspace(0, 1, im.size[1])[:, None]
        al *= np.clip(1 - np.clip((yy - 0.70) / 0.30, 0, 1) ** 1.2, 0, 1)
        im.putalpha(Image.fromarray((al * 255).astype(np.uint8)))
        return im.convert("RGBA")

    cards = proj["cards"]
    head = portrait(work / "base.mp4") if cards.get("portrait_box") else None

    timeline, f = [], 0
    for shot in proj["shots"]:
        timeline.append((shot, f, (f + shot["frames"]) / fps))
        f += shot["frames"]
    total_frames = f

    rr = proj.get("_render_range")
    # CPU: PNG compression releases the GIL, so saves run on worker threads while the next
    # frame renders. compress_level=1 is still lossless (identical pixels, bigger temp files).
    from concurrent.futures import ThreadPoolExecutor
    saver = ThreadPoolExecutor(max_workers=max(2, physical_cores()))
    pending = []
    for idx in range(total_frames):
        if rr and not (rr[0] <= idx <= rr[1]):
            continue
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        for shot, f0, _ in timeline:
            if f0 <= idx < f0 + shot["frames"]:
                t = (idx - f0) / fps
                if shot["kind"] == "card":
                    img = grad((8, 11, 20), (16, 21, 34), (0.72, 0.35, (60, 42, 12), 0.55))
                    if head is not None:
                        push = 1 + 0.045 * min(1, t / 2.4)
                        hw, hh = int(head.width * push), int(head.height * push)
                        h2 = head.resize((hw, hh), Image.LANCZOS)
                        h2.putalpha(Image.eval(h2.getchannel("A"),
                                               lambda v: int(v * min(1, t / 0.5) * 0.98)))
                        img.alpha_composite(h2, (1130, 40))
                    d = ImageDraw.Draw(img)
                    y = 300
                    for j, txt in enumerate(cards["name"]):
                        q = min(1.0, max(0.0, (t - 0.08 - j * 0.10) / 0.12))
                        if q > 0:
                            d.text((96, y), txt, font=font("anton", 110),
                                   fill=(255, 255, 255, int(255 * q)))
                        y += 122
                    rp = min(1.0, max(0.0, (t - 0.34) / 0.28))
                    if rp > 0:
                        d.rectangle([96, 556, 96 + int(160 * rp), 561],
                                    fill=(232, 178, 58, 255))
                    q = min(1.0, max(0.0, (t - 0.46) / 0.30))
                    if q > 0:
                        d.text((96, 588), cards["title"], font=font("barlow", 56),
                               fill=(232, 178, 58, int(255 * q)))
                elif shot["kind"] == "end":
                    img = grad((6, 8, 14), (11, 15, 26), (0.5, 0.42, (48, 36, 10), 0.7))
                    lg = Image.open(proj["logo"]).convert("RGBA")
                    w = cards.get("logo_w", 780)
                    lg = lg.resize((w, int(lg.height * w / lg.width)), Image.LANCZOS)
                    p = min(1.0, t / 0.38)
                    lg.putalpha(Image.eval(lg.getchannel("A"), lambda v: int(v * p)))
                    img.alpha_composite(lg, ((W - lg.width) // 2, 350))
                    q = min(1.0, max(0.0, (t - 0.26) / 0.32))
                    if q > 0:
                        fb = font("barlow", 54)
                        bb = fb.getbbox(cards["tagline"])
                        ImageDraw.Draw(img).text(((W - (bb[2] - bb[0])) // 2, 660),
                                                 cards["tagline"], font=fb,
                                                 fill=(232, 178, 58, int(255 * q)))
                elif shot["kind"] == "clip":
                    side = shot.get("side", "left")
                    sp = min(1.0, max(0.0, (t - words[str(shot["i"])][0]["t"] + 0.08) / 0.35))
                    if sp > 0:
                        al = SCR[side].point(lambda v, k=sp: int(v * k))
                        img.alpha_composite(Image.merge(
                            "RGBA", (Image.new("L", (W, H), 0),) * 3 + (al,)))
                    if shot.get("matte"):            # the other person is never visible
                        img.alpha_composite(matte_img(shot["matte"]))
                    img.alpha_composite(text_layer(shot, t))
                break
        pending.append(saver.submit(img.save, ovl / f"f_{idx:04d}.png", compress_level=1))
        if len(pending) > 64:
            pending.pop(0).result()
        if idx % 150 == 0:
            print(f"  overlay {idx}/{total_frames}", flush=True)
    for fu in pending:
        fu.result()
    saver.shutdown()
    return ovl


def timeline(proj: dict) -> list:
    """[(shot, start_s, end_s)] for every shot."""
    fps, t, out = proj.get("fps", 30), 0, []
    for s in proj["shots"]:
        out.append((s, t / fps, (t + s["frames"]) / fps))
        t += s["frames"]
    return out


def end_audio_plan(proj: dict):
    """Music fade-out [a, b] and duck hold [a, b] in timeline seconds. Explicit project keys
    music_fade_out / duck_hold win (false = off). Default (needs sfx.sting_at): when only black /
    end shots follow the last clip, the bed fades (cosine) from the END OF THE LAST WORD to 40 ms
    before the sting and stays ducked from that clip's start to the sting; otherwise a 1 s fade
    into the sting. So the last word is never under a swelling bed and the sting never overlaps music."""
    sting = (proj.get("sfx") or {}).get("sting_at")
    mf, dh = proj.get("music_fade_out"), proj.get("duck_hold")
    if sting is None:
        return (mf or None), (dh or None)
    tl = timeline(proj)
    clips = [k for k, (s, a, b) in enumerate(tl) if s["kind"] == "clip"]
    auto_mf = auto_dh = None
    if clips:
        k = clips[-1]
        s, a, b = tl[k]
        speech_end = a + ((s["audio_out"] - s["src_in"]) if s.get("audio_out") is not None else s["frames"] / proj.get("fps", 30))
        tail_only = all(x["kind"] in ("black", "end") for x, _, _ in tl[k + 1:])
        if tail_only and speech_end < sting - 0.15:
            auto_mf, auto_dh = [round(speech_end, 3), round(sting - 0.04, 3)], [round(a, 3), round(sting, 3)]
        else:
            auto_mf = [round(max(0.0, sting - 1.0), 3), round(sting - 0.04, 3)]
    if mf is None:
        mf = auto_mf
    if dh is None:
        dh = auto_dh
    return (mf or None), (dh or None)


def stage_audio(proj, ff, work: Path) -> Path:
    import numpy as np
    SR, fps = 48000, proj.get("fps", 30)
    total = sum(s["frames"] for s in proj["shots"]) / fps
    N = int(round(total * SR))
    adir = work / "audio"
    adir.mkdir(parents=True, exist_ok=True)

    def dec(path, ss=None, dur=None, raw=None):
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y"]
        if ss is not None:
            cmd += ["-ss", f"{ss:.4f}"]
        if raw:
            cmd += ["-f", raw, "-ar", "44100", "-ac", "2"]
        cmd += ["-i", str(path)]
        if dur is not None:
            cmd += ["-t", f"{dur:.4f}"]
        cmd += ["-ac", "2", "-ar", str(SR), "-f", "f32le", "pipe:1"]
        p = run(cmd, binary=True)
        return np.frombuffer(p.stdout, dtype=np.float32).reshape(-1, 2).copy()

    def fade(x, ms=8):
        n = int(SR * ms / 1000)
        if len(x) < 2 * n:
            return x
        r = np.ones(len(x), np.float32)
        r[:n] = np.linspace(0, 1, n)
        r[-n:] = np.linspace(1, 0, n)
        return x * r[:, None]

    def rms_db(x):
        return 20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-12)

    def norm(x, target):
        return x * 10 ** ((target - rms_db(x)) / 20)

    def add(buf, x, i0):
        i0 = max(0, int(i0))
        n = min(len(x), len(buf) - i0)
        if n > 0:
            buf[i0:i0 + n] += x[:n]

    # dialogue
    dlg = np.zeros((N, 2), np.float32)
    t, spans, gspans = 0.0, [], []
    for s in proj["shots"]:
        if s["kind"] == "clip":
            dur = s["frames"] / fps
            seg = dec(proj["src"], s["src_in"], dur)[: int(round(dur * SR))]
            seg = np.pad(seg, ((0, int(round(dur * SR)) - len(seg)), (0, 0)))
            if s.get("audio_out") is not None:  # v2: end dialogue before the frame cut, with an out-fade
                ke = int(round((s["audio_out"] - s["src_in"]) * SR))
                nf = int(SR * s.get("fade_out_ms", proj.get("speech_fade_ms", 25)) / 1000)
                seg[ke:] = 0
                seg[ke - nf:ke] *= np.linspace(1, 0, nf, dtype=np.float32)[:, None]
            add(dlg, fade(seg), t * SR)
            spans.append((t, t + dur))
            gspans.append((t, t + dur, float(s.get("gain_db", 0.0)), s.get("duck"), s.get("i")))
        t += s["frames"] / fps
    dlg = norm(dlg, -18.0)
    for a, b, g, _, _ in gspans:   # per-shot speech gain (shot key gain_db), applied after the global norm
        if g:
            dlg[int(a * SR):int(b * SR)] *= 10 ** (g / 20)

    # music with a speech-mask duck
    mus = dec(proj["music"])[:N]
    mus = np.pad(mus, ((0, N - len(mus)), (0, 0)))
    mask = np.zeros(N, np.float32)
    for a, b in spans:
        mask[int(a * SR):int(b * SR)] = 1.0
    mf, dh = end_audio_plan(proj)
    if dh:          # keep the bed ducked from the last line through the end black to the sting
        mask[int(dh[0] * SR):int(dh[1] * SR)] = 1.0
    env, prev = np.zeros(N, np.float32), 0.0
    ka, kr = math.exp(-1 / (0.05 * SR)), math.exp(-1 / (0.30 * SR))
    for i in range(N):
        a = ka if mask[i] > prev else kr
        prev = a * prev + (1 - a) * mask[i]
        env[i] = prev
    depth = np.full(N, 0.72, np.float32)
    for a, b, _, d, _ in gspans:   # per-shot duck depth (shot key duck, 0-1; default 0.72)
        if d is not None:
            depth[int(a * SR):int(b * SR)] = float(d)
    mus = mus * (1 - depth * env)[:, None]
    mus = norm(mus, -20.0)
    if mf:          # cosine fade of the bed, done before the sting (no music under the sting)
        tt = np.arange(N) / SR
        g = np.where(tt < mf[0], 1.0, np.where(tt >= mf[1], 0.0,
                     0.5 + 0.5 * np.cos(np.pi * (tt - mf[0]) / max(1e-3, mf[1] - mf[0]))))
        mus = mus * g.astype(np.float32)[:, None]
        print(f"[introkit] music fade {mf[0]:.3f} -> {mf[1]:.3f} s" + (f", ducked {dh[0]:.3f} -> {dh[1]:.3f} s" if dh else ""))

    # diagnostic: active-speech level vs music bed under each clip (dB, pre-master)
    for a, b, g, d, si in gspans:
        x, mm = dlg[int(a * SR):int(b * SR)].mean(1), mus[int(a * SR):int(b * SR)].mean(1)
        n = int(0.05 * SR)
        fr = np.array([np.sqrt(np.mean(x[j:j + n] ** 2)) + 1e-12 for j in range(0, max(1, len(x) - n), n)])
        fd = 20 * np.log10(fr)
        act = fr[fd > fd.max() - 20] if len(fr) else fr
        sp = 20 * np.log10(np.sqrt(np.mean(act ** 2)) + 1e-12) if len(act) else -99
        mu = 20 * np.log10(np.sqrt(np.mean(mm ** 2)) + 1e-12)
        print(f"[introkit] level shot {si}: speech {sp:.1f} dB, music {mu:.1f} dB, gap {sp - mu:.1f} dB"
              + (f" (gain {g:+.1f} dB)" if g else "") + (f" (duck {d})" if d is not None else ""), flush=True)

    # sfx
    sfx = np.zeros((N, 2), np.float32)
    cues = []
    boom = proj.get("sfx", {}).get("boom")
    if boom:
        b = dec(boom, raw=sniff_raw(boom, proj["sfx"].get("boom_format")))[: int(1.4 * SR)]
        b = norm(b, -21.0)
        for s in proj["shots"]:
            if s["kind"] != "clip":
                continue
            f0 = sum(x["frames"] for x in proj["shots"][: proj["shots"].index(s)]) / fps
            if proj["shots"].index(s) == 0:
                continue
            add(sfx, b, f0 * SR)
            cues.append(("boom", round(f0, 2)))
    for key, when in (("tape", proj.get("sfx", {}).get("tape_at")),
                      ("sting", proj.get("sfx", {}).get("sting_at"))):
        path = proj.get("sfx", {}).get(key)
        if path and when is not None:
            x = dec(path, raw=sniff_raw(path, proj["sfx"].get(key + "_format")))
            full = len(x) / SR
            x = norm(x[: int(proj.get("sfx", {}).get(key + "_max_s", 2.4) * SR)], -19.5)
            add(sfx, x, when * SR)
            cues.append((key, round(when, 2)))
            if key == "sting" and total - when < min(full, 2.45) - 0.01:
                print(f"[introkit] WARN sting tail {total - when:.2f} s < {min(full, 2.45):.2f} s: the sting is "
                      "truncated; lengthen the final black / end card (`vet --write` sets it)", flush=True)

    mix = dlg + mus + sfx
    peak = float(np.max(np.abs(mix)))
    if peak > 0.89:
        mix *= 0.89 / peak

    def write(path, x):
        import wave
        with wave.open(str(path), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SR)
            w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())

    raw = adir / "mix_raw.wav"
    write(raw, mix)

    # converge loudness / true peak, measuring the file each pass
    def measure(path):
        p = run([ff, "-hide_banner", "-nostats", "-i", str(path), "-af",
                 "ebur128=peak=true", "-f", "null", "-"], check=False)
        out = {}
        for k, tag in (("I", "I:"), ("LRA", "LRA:"), ("TP", "Peak:")):
            v = [ln.split(tag)[1].strip().split()[0] for ln in p.stderr.splitlines()
                 if tag in ln]
            out[k] = float(v[-1]) if v else 0.0
        return out

    x, thr = mix, 10 ** (-1.75 / 20)
    master = adir / "mix_master.wav"
    for _ in range(6):
        write(master, x)
        m = measure(master)
        err = -14.0 - m["I"]
        if abs(err) < 0.08 and m["TP"] <= -1.05:
            break
        y = x * 10 ** (err / 20)
        over = np.abs(y) > thr
        y[over] = np.sign(y[over]) * (thr + (np.abs(y[over]) - thr) * 0.06)
        x = y
    write(master, x)
    print("master:", measure(master))
    return master


def _ebur(ff: str, path: Path) -> dict:
    p = run([ff, "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"], check=False)
    out = {}
    for k, tag in (("I", "I:"), ("LRA", "LRA:"), ("TP", "Peak:")):
        v = [ln.split(tag)[1].strip().split()[0] for ln in p.stderr.splitlines() if tag in ln]
        out[k] = float(v[-1]) if v else 0.0
    return out


def remaster_aac(ff: str, master: Path) -> Path:
    """AAC overshoots the WAV's true peak by ~0.5-1 dB (LESSONS B6): converge -14 LUFS / TP <= -1.1
    on the ENCODED AAC, lowering a soft knee 0.3 dB per round. Returns the .m4a that is muxed as is."""
    import numpy as np
    import wave
    with wave.open(str(master)) as w:
        sr = w.getframerate()
        x = (np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float64) / 32767).reshape(-1, 2)

    def wr(p, y):
        with wave.open(str(p), "wb") as w:
            w.setnchannels(2); w.setsampwidth(2); w.setframerate(sr)
            w.writeframes((np.clip(y, -1, 1) * 32767).astype("<i2").tobytes())
    tp_wav, m4a, knee = master.with_name("mix_master_tp.wav"), master.with_name("mix_master_tp.m4a"), -2.0
    for _ in range(12):
        thr = 10 ** (knee / 20)
        y = x.copy()
        o = np.abs(y) > thr
        y[o] = np.sign(y[o]) * (thr + (np.abs(y[o]) - thr) * 0.06)
        for _ in range(5):
            wr(tp_wav, y)
            m = _ebur(ff, tp_wav)
            err = -14.0 - m["I"]
            if abs(err) < 0.05:
                break
            y = y * 10 ** (err / 20)
            o = np.abs(y) > thr
            y[o] = np.sign(y[o]) * (thr + (np.abs(y[o]) - thr) * 0.06)
        run([ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(tp_wav), "-c:a", "aac", "-b:a", "320k",
             "-ar", "48000", "-ac", "2", str(m4a)])
        ma = _ebur(ff, m4a)
        print(f"[introkit] remaster knee {knee:.1f} dBFS: wav {m['I']:.2f} LUFS / aac {ma['I']:.2f} LUFS TP {ma['TP']:.2f}", flush=True)
        if ma["TP"] <= -1.1 and abs(ma["I"] + 14.0) < 0.1:
            break
        knee -= 0.3
    return m4a


def stage_mux(proj, ff, work: Path, ovl: Path, audio: Path, out: Path) -> Path:
    fps, W, H = proj.get("fps", 30), proj["width"], proj["height"]
    out.parent.mkdir(parents=True, exist_ok=True)
    aac = remaster_aac(ff, audio) if proj.get("remaster_aac", True) else None
    run([ff, "-y", "-hide_banner", "-loglevel", "error",
         "-i", str(work / "base.mp4"),
         "-framerate", str(fps), "-start_number", "0", "-i", str(ovl / "f_%04d.png"),
         "-i", str(aac or audio),
         "-filter_complex", "[0:v][1:v]overlay=0:0:format=auto,format=yuv420p,setsar=1[v]",
         "-map", "[v]", "-map", "2:a",
         "-c:v", "libx264", "-threads", "0", "-preset", "medium", "-crf", "15",
         "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p", *BT709,
         "-bsf:v", H264_VUI, "-aspect", f"{W}:{H}",
         "-r", str(fps), "-g", "60", "-movflags", "+faststart",
         *(["-c:a", "copy"] if aac else ["-c:a", "aac", "-b:a", "320k", "-ar", "48000", "-ac", "2"]),
         "-shortest", str(out)])
    return out


INTROS_DIRNAME = "intros"


def intros_dir(src) -> Path:
    """Default home of generated intros (2026-11-03): <folder of the source video>\\intros.
    Finals + LINE_SETS.md / CUTLIST.md / QC_REPORT.md go here; scratch goes in intros\\work."""
    return Path(str(src)).expanduser().resolve().parent / INTROS_DIRNAME


def resolve_out_dir(proj: dict, cli_out=None, base: Path | None = None) -> Path:
    """Output folder for finals. Precedence: --out (CLI) > project out_dir (relative to the project
    file) > <source folder>\\intros. Created if missing."""
    if cli_out:
        d = Path(cli_out).expanduser()
    elif proj.get("out_dir"):
        d = Path(proj["out_dir"]).expanduser()
        if not d.is_absolute() and base is not None:
            d = base / d
    else:
        d = intros_dir(proj["src"])
    d = d.resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def cmd_build(a) -> None:
    proj_path = Path(a.project).expanduser().resolve()
    proj = json.loads(proj_path.read_text(encoding="utf-8"))
    base = proj_path.parent
    proj = {k: v for k, v in proj.items() if not k.startswith("_comment")}

    def rp(v: str) -> str:
        """Relative paths in the project are relative to the project file, not the cwd."""
        q = Path(v).expanduser()
        return str((q if q.is_absolute() else base / q).resolve())

    proj["src"] = rp(proj["src"])
    apply_asset_defaults(proj)   # bundled files fill whatever the project leaves out (absolute already)
    for k in ("music", "logo", "fonts_dir"):
        if proj.get(k):
            proj[k] = rp(proj[k])
    if proj.get("cards", {}).get("portrait_image"):
        proj["cards"]["portrait_image"] = rp(proj["cards"]["portrait_image"])
    # fix: sfx.*_at are float seconds and *_format are strings -> only resolve real paths
    for k, v in list(proj.get("sfx", {}).items()):
        if k.endswith("_format") or k.endswith("_at") or v is None or not isinstance(v, str):
            continue
        proj["sfx"][k] = rp(v)
    proj["_fonts_cli"] = getattr(a, "fonts", None)
    ff = _which("ffmpeg", a.ffmpeg)
    ffprobe = _which("ffprobe", a.ffprobe)
    plan = plan_hw(ff, getattr(a, "device", None), proj, hwaccel=getattr(a, "hwaccel", None))
    proj["_hw"] = plan
    # fix: a relative --out broke the concat list (paths resolved against the list's folder)
    out_dir = resolve_out_dir(proj, a.out, base)   # default <source folder>\\intros (2026-11-03)
    print("[introkit] out ->", out_dir)
    work = out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    (work / "hw.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    words = json.loads((Path(a.words)).read_text(encoding="utf-8")) if a.words \
        else json.loads((work / "words.json").read_text(encoding="utf-8"))

    print("[introkit] base picture");  stage_base(proj, ff, ffprobe, work)
    print("[introkit] graphics");      ovl = stage_overlay(proj, work, words)
    print("[introkit] audio");         aud = stage_audio(proj, ff, work)
    print("[introkit] mux");           final = stage_mux(proj, ff, work, ovl, aud,
                                                         out_dir / f"{proj['name']}.mp4")
    print("[introkit] done ->", final)
    if not a.keep_overlay:
        shutil.rmtree(ovl, ignore_errors=True)


# --------------------------------------------------------------------------- faces: frame + qc --faces
#
# `frame` scans every source frame of each clip shot (+ pad) with YuNet, takes the UNION of the
# target face's boxes over the whole span, adds head / chin / side margins, and solves the
# tightest safe start crop and a face-anchored push-in end crop (both must contain the union).
# `qc --faces` re-detects the face on every clip frame of the delivered file and fails on any
# head-top margin < 0, face+margin box outside the frame, or a frame with no face.
# Model: assets/models/face_detection_yunet_2023mar.onnx (OpenCV Zoo, MIT, see ASSETS.md).

YUNET_MODEL = ASSETS / "models" / "face_detection_yunet_2023mar.onnx"
FACE = dict(
    head=0.30,        # frame: hair allowance above the YuNet box, x face height (25-35 %)
    chin=0.10,        # frame: below the chin, x face height
    side=0.15,        # frame: each side, x face width
    target=0.34,      # frame: wanted face height / output height (30-38 %)
    zoom_cap=2.0,     # frame: max magnification at any time (source px -> output px)
    push=0.06,        # frame: wanted push-in (end zoom = start zoom x 1.06)
    push_min=0.02,    # frame: smaller than this -> no push (back-off to 0)
    top_pad=60,       # frame: preferred output px between head+margin and the top edge
    jump=1.06,        # frame: a same-speaker jump cut must tighten by >= 6 %
    text_gap=24,      # frame: keep the face+margin this far from the text column
    pad_s=0.3,        # frame: scan this much before src_in and after the last frame
    qc_head=0.25,     # qc: head-top gate, x face height (LESSONS B3)
    qc_chin=0.05,     # qc: chin gate, x face height
    qc_side=0.05,     # qc: side gate, x face width
    score=0.6, score_low=0.4, min_h=40, scan_scale=0.5,
    verify_buf=0.02,     # frame's simulated qc must clear the qc gate by this (x face h / frame)
    verify_step=0.02,    # zoom back-off per step when the simulation fails
    verify_steps=10,
)


def _yunet(size: tuple[int, int], score: float):
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")     # silence dnn backend notices
    try:
        import cv2
    except ImportError:
        sys.exit("[introkit] OpenCV missing: run setup (opencv-python-headless is in requirements.txt)")
    if not YUNET_MODEL.is_file():
        sys.exit(f"[introkit] face model missing: {YUNET_MODEL} (ships with the skill, see ASSETS.md)")
    return cv2.FaceDetectorYN.create(str(YUNET_MODEL), "", (int(size[0]), int(size[1])), score, 0.3, 5000)


def _frames(ff: str, src: str, t0: float, dur: float | None, wh: tuple[int, int], plan: dict | None):
    """Yield BGR frames (numpy HxWx3) decoded by ffmpeg with the planned hardware decode
    (-ss before -i, the same seek the build uses); falls back to CPU if the hw path fails."""
    import numpy as np
    W, H = wh
    size = W * H * 3
    for hx in ([hwaccel_args(plan)] if hwaccel_args(plan) else []) + [[]]:
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-nostdin", *hx]
        if t0:
            cmd += ["-ss", f"{max(0.0, t0):.4f}"]
        cmd += ["-i", str(src)]
        if dur:
            cmd += ["-t", f"{dur:.4f}"]
        cmd += ["-an", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=size * 2)
        got = 0
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size:
                break
            got += 1
            yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        p.stdout.close()
        err = p.stderr.read().decode("utf-8", "replace")
        p.wait()
        if got or not hx:
            if p.returncode and not got:
                sys.exit(f"[introkit] decode failed: {err.strip()[-400:]}")
            return
        print(f"[introkit] {hx[1]} decode failed for the face scan, retrying on CPU", flush=True)


class FaceFinder:
    """YuNet at scan_scale on the whole frame; on a miss, YuNet at full resolution with a
    lower score on the region of interest (catches profiles / three-quarter turns); the
    target face is the one nearest the previous pick inside the ROI (largest on the first)."""

    def __init__(self, wh: tuple[int, int], opts: dict):
        os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
        import cv2
        self.cv2, self.W, self.H, self.o = cv2, wh[0], wh[1], opts
        sc = opts["scan_scale"]
        self.sw, self.sh = max(64, int(wh[0] * sc)), max(36, int(wh[1] * sc))
        self.fast = _yunet((self.sw, self.sh), opts["score"])
        self.low = _yunet((64, 64), opts["score_low"])

    def _run(self, det, img, size):
        det.setInputSize(size)
        _, fs = det.detect(img)
        return [] if fs is None else [[float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])] for f in fs]

    def find(self, frame, roi: dict | None, prev: list | None):
        cv2 = self.cv2
        k = self.W / self.sw
        small = cv2.resize(frame, (self.sw, self.sh), interpolation=cv2.INTER_AREA)
        cands = [[f[0] * k, f[1] * k, f[2] * k, f[3] * k, f[4]] for f in self._run(self.fast, small, (self.sw, self.sh))]
        how = "yunet"
        pick = self._pick(cands, roi, prev)
        if pick is None:
            x0, y0, x1, y1 = 0, 0, self.W, self.H
            if roi:
                mx, my = roi["w"] * 0.1, roi["h"] * 0.1
                x0, y0 = max(0, int(roi["x"] - mx)), max(0, int(roi["y"] - my))
                x1, y1 = min(self.W, int(roi["x"] + roi["w"] + mx)), min(self.H, int(roi["y"] + roi["h"] + my))
            sub = frame[y0:y1, x0:x1]
            if sub.size:
                cands = [[f[0] + x0, f[1] + y0, f[2], f[3], f[4]]
                         for f in self._run(self.low, sub, (sub.shape[1], sub.shape[0]))]
                pick, how = self._pick(cands, roi, prev), "low"
        return (pick, how) if pick is not None else (None, "none")

    def _pick(self, cands, roi, prev):
        c = [f for f in cands if f[3] >= self.o["min_h"]]
        if roi:
            c = [f for f in c if roi["x"] <= f[0] + f[2] / 2 <= roi["x"] + roi["w"]
                 and roi["y"] - 0.2 * roi["h"] <= f[1] + f[3] / 2 <= roi["y"] + roi["h"]]
        if not c:
            return None
        if prev:
            px, py = prev[0] + prev[2] / 2, prev[1] + prev[3] / 2
            best = min(c, key=lambda f: math.hypot(f[0] + f[2] / 2 - px, f[1] + f[3] / 2 - py))
            if math.hypot(best[0] + best[2] / 2 - px, best[1] + best[3] / 2 - py) > 1.5 * max(prev[3], best[3]):
                return None      # a different person, not our target
            return best
        return max(c, key=lambda f: f[3])


def _even(v: float, up: bool = False) -> int:
    i = int(math.ceil(v)) if up else int(round(v))
    return i + (i & 1) if up else i - (i & 1)


def _margins(boxes: list, r: dict, W: int, H: int, head: float) -> dict:
    """Output-px margins of boxes (source px) seen through crop rect r: top = head+margin to
    the top edge, chin = box bottom to the bottom edge, left/right = box to the side edges."""
    out = {"top": 1e9, "chin": 1e9, "left": 1e9, "right": 1e9, "chin_need": 0.0, "side_need": 0.0}
    for b in boxes:
        sx, sy = W / r["w"], H / r["h"]
        X, Y, Bw, Bh = (b[0] - r["x"]) * sx, (b[1] - r["y"]) * sy, b[2] * sx, b[3] * sy
        out["top"] = min(out["top"], Y - head * Bh)
        out["chin"] = min(out["chin"], H - (Y + Bh))
        out["left"] = min(out["left"], X)
        out["right"] = min(out["right"], W - (X + Bw))
    return {k: (round(v, 1) if v < 1e8 else None) for k, v in out.items() if not k.endswith("_need")}


def _text_column(shot: dict, W: int, gap: int) -> tuple[str, float] | None:
    """The caption block's horizontal reach (stage_overlay: x=96 .. 96+maxw on its side)."""
    if not shot.get("lines"):
        return None
    side, cap = shot.get("side", "left"), shot.get("maxw", 900)
    return ("left", 96 + cap + gap) if side == "left" else ("right", W - 96 - cap - gap)


def _hits(x: float, y: float, w: float, h: float, rects: list) -> list:
    return [r for r in rects if x < r["x"] + r["w"] and r["x"] < x + w and y < r["y"] + r["h"] and r["y"] < y + h]


def _dodge(x: float, y: float, w: float, h: float, rects: list, xr: tuple, yr: tuple, bad_x=None):
    """Move the crop (w x h at x,y) the least distance inside the ranges xr/yr so it touches no
    avoid rect; positions where bad_x(x) is true (caption column over the face) are used only
    if nothing else clears. Returns (x, y, still_hit, used_bad)."""
    if not _hits(x, y, w, h, rects):
        return x, y, False, False
    def evens(vals, lo, hi):                            # crops sit on even pixels
        out = set()
        for v in vals:
            for e in (2 * math.floor(v / 2), 2 * math.ceil(v / 2)):
                if lo - 1e-6 <= e <= hi + 1e-6:
                    out.add(int(e))
        return out
    xs = evens({x, xr[0], xr[1]} | {r["x"] + r["w"] for r in rects} | {r["x"] - w for r in rects}, *xr)
    ys = evens({y, yr[0], yr[1]} | {r["y"] + r["h"] for r in rects} | {r["y"] - h for r in rects}, *yr)
    best = None
    for cx in xs:
        bad = bool(bad_x and bad_x(cx))
        for cy in ys:
            if not _hits(cx, cy, w, h, rects):
                d = abs(cx - x) + abs(cy - y) + (1e7 if bad else 0)
                if best is None or d < best[0]:
                    best = (d, cx, cy, bad)
    return (best[1], best[2], False, best[3]) if best else (x, y, True, False)


def plan_shot_frame(boxes: list, cur: dict, shot: dict, src_wh: tuple[int, int], out_wh: tuple[int, int],
                    o: dict, cap: float | None = None, allow_push: bool = True, cap_why: str = "jump-cut",
                    area: dict | None = None) -> dict:
    """Preferred margins first (head/chin/side); if they cost zoom or the push, retry with the
    minimum margins (the qc gate + 2 %) and keep whichever ends tighter. Both pass qc --faces."""
    r = _plan_shot(boxes, cur, shot, src_wh, out_wh, o, cap, allow_push, cap_why, area)
    if r["limited"] and not r["source_limited"]:
        hard = dict(o, head=o["qc_head"] + 0.02, chin=o["qc_chin"] + 0.02, side=o["qc_side"] + 0.02)
        r2 = _plan_shot(boxes, cur, shot, src_wh, out_wh, hard, cap, allow_push, cap_why, area)
        if r2["zoom_end"] > r["zoom_end"] + 0.004 or (r2["punch"] and not r["punch"]):
            r2["notes"].insert(0, f"minimum margins used (head {hard['head']:.0%}, chin {hard['chin']:.0%}, "
                                  f"side {hard['side']:.0%}): preferred margins allowed only {r['zoom_end']:.3f}x")
            r = r2
    return r


def _plan_shot(boxes: list, cur: dict, shot: dict, src_wh: tuple[int, int], out_wh: tuple[int, int],
               o: dict, cap: float | None = None, allow_push: bool = True, cap_why: str = "jump-cut",
               area: dict | None = None) -> dict:
    """Solve start crop S and end crop E (source px) for one shot from its union face box.
    `area`: {"bounds": rect, "avoid": [rects]} in source px. Every crop stays inside bounds
    and off every avoid rect (broadcast furniture)."""
    SW, SH = src_wh
    area = area or {}
    bd = area.get("bounds") or {"x": 0, "y": 0, "w": SW, "h": SH}
    bx0, by0, bx1, by1 = bd["x"], bd["y"], bd["x"] + bd["w"], bd["y"] + bd["h"]
    avoid = area.get("avoid") or []
    W, H = out_wh
    notes = []
    hm = sorted(b[3] for b in boxes)[len(boxes) // 2]
    head = o["head"]
    ry0 = min(b[1] - head * b[3] for b in boxes)
    if ry0 < 0:
        qtop = min(b[1] - o["qc_head"] * b[3] for b in boxes)
        if qtop < 0:
            notes.append(f"SOURCE-LIMITED: head+{int(o['qc_head'] * 100)}% leaves the source top by "
                         f"{-qtop:.0f} px; no crop can pass qc --faces, pick another span")
            ry0 = qtop
        else:
            notes.append(f"head allowance relaxed {int(head * 100)}%->"
                         f"{int(100 * min(b[1] / b[3] for b in boxes))}% (hair near the source top)")
            ry0 = 0.0
    ry1 = max(b[1] + b[3] * (1 + o["chin"]) for b in boxes)
    rx0 = min(b[0] - o["side"] * b[2] for b in boxes)
    rx1 = max(b[0] + b[2] * (1 + o["side"]) for b in boxes)
    if rx0 < 0 or rx1 > SW or ry1 > SH:
        notes.append("face+margin box clipped by the source edge (side/chin)")
    elif rx0 < bx0 or rx1 > bx1 or ry1 > by1 or ry0 < by0:
        notes.append(f"face+margin box clipped by the allowed area {area.get('why', 'bounds')}"
                     " (side/chin/top)")
    rx0, rx1, ry1 = max(float(bx0), rx0), min(float(bx1), rx1), min(float(by1), ry1)
    ry0c = max(float(by0), ry0)
    rw, rh = rx1 - rx0, ry1 - ry0c
    aspect = W / H
    zcap = min(o["zoom_cap"], cap or 9e9)
    z_fit = W / max(rw, rh * aspect)                    # tightest crop that still holds the box
    z_min = W / min(bd["w"], bd["h"] * aspect)          # the whole allowed area
    z_lim = max(z_min, min(zcap, z_fit))
    why = (f"the {cap_why} cap {cap:.3f}x" if cap and cap <= min(o["zoom_cap"], z_fit)
           else f"the {o['zoom_cap']}x cap" if o["zoom_cap"] <= z_fit else "the face+margins (source-limited)")
    z_target = o["target"] * H / hm
    z_floor = W / cur["w"]                              # the editor's / project crop
    z_want = min(max(z_floor, z_target), zcap)
    p_t = o["push"] if allow_push else 0.0
    if z_want * (1 + p_t) <= z_lim:
        z0, p = z_want, p_t
    elif p_t and z_lim / (1 + p_t) >= min(z_floor, z_want):
        z0, p = z_lim / (1 + p_t), p_t
    else:
        z0 = min(z_want, z_lim)
        p = z_lim / z0 - 1 if p_t else 0.0
        if p < o["push_min"]:
            if p_t:
                notes.append(f"push backed off to 0: max zoom {z_lim:.3f}x is set by {why}")
            p = 0.0
    limited = bool(p_t and p < p_t - 1e-6) or z0 < min(z_want, zcap) - 1e-6
    if z0 < z_floor - 1e-6:
        notes.append(f"zoom reduced {z_floor:.3f}x -> {z0:.3f}x by {why}")
    tcol = _text_column(shot, W, o["text_gap"])
    fx0, fx1 = min(b[0] for b in boxes), max(b[0] + b[2] for b in boxes)
    fcx = (fx0 + fx1) / 2
    fcy = (min(b[1] for b in boxes) + max(b[1] + b[3] for b in boxes)) / 2
    u = min(0.8, max(0.2, (fcx - cur["x"]) / cur["w"]))   # keep the editor's composition
    vv = min(0.8, max(0.2, (fcy - cur["y"]) / cur["h"]))

    def place(z: float, inside: dict | None = None) -> dict:
        cw = min(bd["w"], max(_even(W / z), _even(rw, up=True)))
        ch = min(bd["h"], max(_even(cw / aspect), _even(rh, up=True)))
        cw = min(bd["w"], max(cw, _even(ch * aspect)))
        if cw > bd["w"] or ch > bd["h"]:
            cw, ch = (bd["w"], _even(bd["w"] / aspect)) if bd["w"] / aspect <= bd["h"] else (_even(bd["h"] * aspect), bd["h"])
        lox, hix = max(float(bx0), rx1 - cw), min(bx1 - cw, rx0)
        loy, hiy = max(float(by0), ry1 - ch), min(by1 - ch, ry0c)
        if inside:                                      # end crop stays inside the start crop
            lox, hix = max(lox, inside["x"]), min(hix, inside["x"] + inside["w"] - cw)
            loy, hiy = max(loy, inside["y"]), min(hiy, inside["y"] + inside["h"] - ch)
        x = fcx - u * cw
        if tcol:
            if tcol[0] == "left":
                x = min(x, rx0 - tcol[1] * cw / W)
            else:
                x = max(x, rx1 - tcol[1] * cw / W)
        y = min(fcy - vv * ch, ry0c - o["top_pad"] * ch / H)   # editor's height, >= top_pad headroom
        x = min(max(x, lox), max(lox, hix))
        y = min(max(y, loy), max(loy, hiy))
        hit = txt = False
        if avoid:
            def ov(cx):                                 # caption column reach into the bare face box, px
                if not tcol:
                    return 0.0
                return (tcol[1] - (fx0 - cx) * W / cw) if tcol[0] == "left" else ((fx1 - cx) * W / cw - tcol[1])
            ov0 = ov(x)
            x, y, hit, txt = _dodge(x, y, cw, ch, avoid, (lox, max(lox, hix)), (loy, max(loy, hiy)),
                                    bad_x=lambda cx: ov(cx) > max(0.0, ov0))
        flags = {**({"_hit": True} if hit else {}), **({"_txt": True} if txt else {})}
        return {"x": _even(x), "y": _even(y), "w": int(cw), "h": int(ch), **flags}

    S = place(z0)
    if S.get("_hit") or S.get("_txt"):                  # tighter until it clears the furniture
        z, S0 = z0, S
        while (S.get("_hit") or S.get("_txt")) and z * 1.02 <= z_lim + 1e-9:
            z *= 1.02
            S = place(z)
        if S.get("_hit") or S.get("_txt"):
            if S0.get("_txt") and not S0.get("_hit"):
                S, z = S0, z0
            if S.get("_hit"):
                notes.append("FURNITURE: no crop that holds the face+margins clears the avoid rects; "
                             "the crop shows furniture (fix the span or the avoid list)")
            else:
                notes.append("TEXT: the only furniture-free crop puts the caption column over the face; "
                             "flip this shot's `side`, narrow `maxw`, or pick another span")
        else:
            notes.append(f"zoom raised {z0:.3f}x -> {z:.3f}x to keep the furniture (avoid rects) out")
            p = max(0.0, z_lim / z - 1) if p else 0.0
            p = p if p >= o["push_min"] else 0.0
            p = min(p, o["push"])
            z0 = z
    E = place(z0 * (1 + p), inside=S) if p else None
    if E and (E.get("_hit") or (E.get("_txt") and not S.get("_txt"))):
        E, p = None, 0.0
        notes.append("push dropped: its end crop would show furniture or put the caption over the face")
    if E:
        E.pop("_txt", None)
    text_conflict = bool(S.pop("_txt", False))
    furniture_hit = bool(S.pop("_hit", False))
    if E and (E["w"] >= S["w"] or abs(W / E["w"] / (W / S["w"]) - 1) < o["push_min"] * 0.5):
        E, p = None, 0.0
    res = {"start": S, "end": E, "punch": round(W / E["w"] / (W / S["w"]) - 1, 4) if E else 0.0,
           "zoom_before": round(z_floor, 3), "zoom_start": round(W / S["w"], 3),
           "zoom_end": round(W / (E or S)["w"], 3), "zoom_max_safe": round(z_lim, 3),
           "zoom_target": round(z_target, 3), "face_h_src": round(hm, 1), "notes": notes,
           "pred_start": _margins(boxes, S, W, H, o["qc_head"]),
           "pred_end": _margins(boxes, E or S, W, H, o["qc_head"]),
           "source_limited": any(n.startswith("SOURCE-LIMITED") for n in notes), "limited": limited,
           "furniture_hit": furniture_hit, "text_conflict": text_conflict,
           "margins_used": {k: o[k] for k in ("head", "chin", "side")}}
    if tcol:
        for key, r in (("start", S), ("end", E or S)):
            if tcol[0] == "left":
                ov = tcol[1] - (rx0 - r["x"]) * W / r["w"]
            else:
                ov = (rx1 - r["x"]) * W / r["w"] - tcol[1]
            if ov > 0:
                res.setdefault("text_column_overlap_px", {})[key] = round(ov)
    return res


def _load_project(path: str) -> tuple[Path, dict]:
    pp = Path(path).expanduser().resolve()
    proj = json.loads(pp.read_text(encoding="utf-8"))
    q = Path(proj["src"]).expanduser()
    proj["_src_abs"] = str(q if q.is_absolute() else (pp.parent / q).resolve())
    return pp, proj


def _faces_sidecar(pp: Path, proj: dict) -> Path:
    v = proj.get("faces_sidecar")
    if v:
        q = Path(v).expanduser()
        return q if q.is_absolute() else pp.parent / q
    return pp.with_name(pp.stem + ".faces.json")


def _rect(v) -> dict:
    return v if isinstance(v, dict) else {"x": v[0], "y": v[1], "w": v[2], "h": v[3]}


def _frame_area(proj: dict, shot: dict, cur: dict, src_wh: tuple[int, int]) -> dict:
    """Where `frame` may put a crop. With an `avoid` list (project and/or shot: furniture rects
    [x,y,w,h] in source px) or `frame_bounds`, anywhere in the source/bounds off those rects.
    Without either, only inside the editor's crop: the kit cannot know what furniture (ON AIR
    bug, chyron, ticker) lies outside it, so it never widens past it on its own."""
    avoid = [_rect(r) for r in (proj.get("avoid") or []) + (shot.get("avoid") or [])]
    fb = shot.get("frame_bounds") or proj.get("frame_bounds")
    if avoid or fb:
        b = _rect(fb) if fb else {"x": 0, "y": 0, "w": src_wh[0], "h": src_wh[1]}
        return {"bounds": b, "avoid": avoid, "why": "(frame_bounds/avoid)"}
    return {"bounds": dict(cur), "avoid": [], "why": "(the editor's crop: no `avoid` list given)"}


def _sim_shot(r: dict, ra: dict, shot: dict, proj: dict, o: dict, out_wh: tuple[int, int], det) -> dict:
    """Render each core frame of the planned crop (start->end, same easing as the build) at the
    size qc --faces looks at, detect with the same model, and measure the qc margins on a
    5-frame median track. YuNet's box grows/shifts a few % with face scale, so margins
    predicted from source-scale boxes are optimistic for big zooms; this catches that."""
    import numpy as np
    cv2 = det[0]
    W, H = out_wh
    sw, sh = W // 2, H // 2
    fps = proj.get("fps", 30)
    S, E = r["start"], r["end"]
    tmp = dict(shot, crop_x=S["x"], crop_y=S["y"], crop_w=S["w"], crop_h=S["h"])
    tmp.pop("push", None)
    if E:
        tmp.update(push=E, push_ease="inout")
    rows = ra["jpgs"]
    ts = [q[0] for q in rows]
    t_in, n = float(shot["src_in"]), int(shot["frames"])
    found, miss = [], 0
    for k in range(n):
        t = t_in + k / fps
        j = min(range(len(ts)), key=lambda i: abs(ts[i] - t))
        _, jpg, sbox = rows[j]
        rc = shot_rect_at(proj, tmp, k)
        sx, sy = sw / rc["w"], sh / rc["h"]
        img = cv2.warpAffine(cv2.imdecode(jpg, cv2.IMREAD_COLOR),
                             np.float32([[sx, 0, -rc["x"] * sx], [0, sy, -rc["y"] * sy]]), (sw, sh),
                             flags=cv2.INTER_AREA if sx < 1 else cv2.INTER_LINEAR)
        exp = ([(sbox[0] - rc["x"]) * sx, (sbox[1] - rc["y"]) * sy, sbox[2] * sx, sbox[3] * sy] if sbox else None)
        det[1].setInputSize((sw, sh))
        _, fs = det[1].detect(img)
        cands = [] if fs is None else [[float(f[0]), float(f[1]), float(f[2]), float(f[3])] for f in fs]
        pick = None
        if cands and exp:
            best = max(cands, key=lambda c: _iou(c, exp))
            pick = best if _iou(best, exp) > 0.2 else None
        elif cands:
            pick = max(cands, key=lambda c: c[3])
        if pick is None:
            pick = exp
        if pick is None:
            miss += 1
            continue
        found.append([v * 2 for v in pick])
    if not found:
        return {"frames": n, "pass": False, "top": None, "chin": None, "left": None, "right": None,
                "need_top": 0, "need_chin": 0, "need_side": 0, "worst": -1e9, "miss": miss}
    tr = _median_track(found)
    hb = sorted(b[3] for b in tr)[len(tr) // 2]
    m = {"top": min(b[1] - o["qc_head"] * b[3] for b in tr), "chin": min(H - b[1] - b[3] for b in tr),
         "left": min(b[0] for b in tr), "right": min(W - b[0] - b[2] for b in tr)}
    buf = o["verify_buf"]
    need = {"top": buf * hb, "chin": o["qc_chin"] * H + buf * hb, "left": o["qc_side"] * W + buf * hb,
            "right": o["qc_side"] * W + buf * hb}
    worst = min((m[q] - need[q]) / hb for q in m)
    out = {k: round(v, 1) for k, v in m.items()}
    out.update(frames=n, miss=miss, worst=round(worst, 4), pass_=None,
               need_top=round(need["top"], 1), need_chin=round(need["chin"], 1), need_side=round(need["left"], 1))
    out.pop("pass_")
    out["pass"] = worst >= 0 and miss == 0
    return out


def _verify_backoff(r: dict, ra: dict, shot: dict, proj: dict, o: dict, src_wh, out_wh, cap) -> dict:
    """Simulate qc --faces on the plan; on a fail, back the zoom off in `verify_step` steps
    (the push goes first: plan_shot_frame drops it when it no longer fits) until it passes. If
    no step passes, keep the step with the best worst-margin and say so."""
    import cv2
    det = (cv2, _yunet((out_wh[0] // 2, out_wh[1] // 2), o["score"]))
    tried = []
    for step in range(o["verify_steps"] + 1):
        sim = _sim_shot(r, ra, shot, proj, o, out_wh, det)
        r["sim"] = sim
        tried.append(r)
        if sim["pass"] and not r.get("furniture_hit") and not r.get("text_conflict"):
            break
        nz = r["zoom_end"] * (1 - o["verify_step"])
        bd = ra["area"]["bounds"]
        z_min = out_wh[0] / min(bd["w"], bd["h"] * out_wh[0] / out_wh[1])
        if nz <= z_min + 1e-3:
            break
        c2 = min(cap, nz) if cap else nz
        r = plan_shot_frame(ra["boxes"], ra["cur"], shot, src_wh, out_wh, o, cap=c2,
                            cap_why="jump-cut" if cap and cap <= nz else "verify back-off", area=ra["area"])
    ok = [q for q in tried if q["sim"]["pass"] and not q.get("furniture_hit") and not q.get("text_conflict")]
    best = ok[-1] if ok else max(tried, key=lambda q: (not q.get("furniture_hit"), not q.get("text_conflict"),
                                                       q["sim"]["worst"], q["zoom_end"]))
    if not ok and best["punch"]:                        # failing anyway: never push in on top of it
        st = plan_shot_frame(ra["boxes"], ra["cur"], shot, src_wh, out_wh, o, cap=best["zoom_start"],
                             allow_push=False, cap_why="verify back-off", area=ra["area"])
        st["sim"] = _sim_shot(st, ra, shot, proj, o, out_wh, det)
        tried.append(st)
        if (st["sim"]["worst"] >= best["sim"]["worst"] - 0.005 and not st.get("furniture_hit")
                and st.get("text_conflict") <= best.get("text_conflict")):
            st["notes"].append("push dropped: the shot cannot clear the qc margins, so it stays static")
            best = st
    n = len(tried) - 1
    if best["sim"]["pass"] and best is not tried[0]:
        best["notes"].append(f"verify: plan failed the qc margins at delivered scale; zoom backed off "
                             f"{tried[0]['zoom_end']:.3f}x -> {best['zoom_end']:.3f}x in {n} step(s)")
    elif not best["sim"]["pass"]:
        best["notes"].append(f"verify FAIL: no zoom from {tried[0]['zoom_end']:.3f}x down to "
                             f"{tried[-1]['zoom_end']:.3f}x clears the qc margins + {o['verify_buf']:.0%} "
                             f"buffer (kept {best['zoom_end']:.3f}x, the best); the face sits too close to the "
                             f"edge of the allowed area {ra['area'].get('why', '')}: qc --faces may fail; "
                             "pick another span, or list the furniture in `avoid` so frame may widen past the editor's crop")
    return best


def cmd_frame(a) -> None:
    import time
    t_start = time.time()
    pp, proj = _load_project(a.project)
    layout = getattr(a, "layout", None) or proj.get("layout") or "side"
    if layout == "side":
        _lib("sidekit").cmd_frame_side(a, pp, proj)
        return
    o = dict(FACE)
    for k in ("head", "chin", "side", "target", "zoom_cap", "push", "top_pad", "pad_s", "scan_scale"):
        v = getattr(a, k, None)
        if v is not None:
            o[k] = v
    if a.no_push:
        o["push"] = 0.0
    ff, ffprobe = _which("ffmpeg", a.ffmpeg), _which("ffprobe", a.ffprobe)
    plan = plan_hw(ff, getattr(a, "device", None), proj, hwaccel=getattr(a, "hwaccel", None))
    src = proj["_src_abs"]
    info = ffprobe_json(ffprobe, Path(src))
    v = next(s for s in info["streams"] if s.get("width"))
    SW, SH = int(v["width"]), int(v["height"])
    num, den = (v.get("r_frame_rate") or "30/1").split("/")
    sfps = float(num) / float(den or 1)
    fps, W, H = proj.get("fps", 30), proj["width"], proj["height"]
    want = {int(x) for x in a.shots.split(",")} if a.shots else None
    finder = FaceFinder((SW, SH), o)
    verify = not getattr(a, "no_verify", False)
    clips = [s for s in proj["shots"] if s["kind"] == "clip" and (want is None or s["i"] in want)]
    sidecar = {"model": YUNET_MODEL.name, "src": src, "src_wh": [SW, SH], "src_fps": sfps, "shots": {}}
    results, scans = {}, {}
    t_scan = time.time()
    for shot in clips:
        if shot.get("frame_lock"):
            print(f"[frame] shot {shot['i']}: frame_lock set, skipped", flush=True)
            continue
        cur = shot_rect(proj, shot)
        roi = shot.get("face_roi")
        roi = {"x": roi[0], "y": roi[1], "w": roi[2], "h": roi[3]} if roi else cur
        t_in = float(shot["src_in"])
        t_out = t_in + int(shot["frames"]) / fps
        t0 = max(0.0, t_in - o["pad_s"])
        rows, prev, jpgs = [], None, []
        for i, fr in enumerate(_frames(ff, src, t0, (t_out + o["pad_s"]) - t0, (SW, SH), plan)):
            t = t0 + i / sfps
            box, how = finder.find(fr, roi, prev)
            if box:
                prev = box
            if verify:
                jpgs.append((t, finder.cv2.imencode(".jpg", fr, [finder.cv2.IMWRITE_JPEG_QUALITY, 92])[1], box))
            rows.append([round(t, 4), [round(x, 1) for x in box[:4]] + [round(box[4], 3)] if box else None, how])
        core = [r for r in rows if t_in - 0.5 / sfps <= r[0] <= t_out]
        miss = [r[0] for r in core if r[1] is None]
        boxes = [r[1] for r in rows if r[1]]
        scans[shot["i"]] = rows
        sidecar["shots"][str(shot["i"])] = {"src_in": t_in, "frames": shot["frames"], "samples": rows}
        st = {"frames_scanned": len(rows), "core_frames": len(core),
              "yunet": sum(r[2] == "yunet" for r in rows), "low_score": sum(r[2] == "low" for r in rows),
              "no_face": len(miss), "no_face_t": [round(x, 3) for x in miss[:12]]}
        if not boxes or len(miss) > 0.5 * max(1, len(core)):
            results[shot["i"]] = {"status": "NO-FACE", "scan": st, "cur": cur,
                                  "notes": ["no face on most frames: crop left unchanged"]}
            continue
        results[shot["i"]] = {"status": "WARN" if miss else "OK", "scan": st, "cur": cur, "boxes": boxes,
                              "miss": miss, "jpgs": jpgs, "area": _frame_area(proj, shot, cur, (SW, SH))}
    t_scan = time.time() - t_scan
    # plan, last shot first: a same-speaker jump cut (a immediately followed by b) must stay
    # >= `jump` looser than b's start, so b is settled (incl. its verify back-off) before a.
    t_ver = time.time()
    order = proj["shots"]
    for j in range(len(order) - 1, -1, -1):
        sa = order[j]
        ra = results.get(sa.get("i"))
        if not ra or "boxes" not in ra:
            continue
        cap, jn = None, None
        sb = order[j + 1] if j + 1 < len(order) else None
        rb = results.get(sb.get("i")) if sb else None
        if (rb and "start" in rb and sa["kind"] == sb["kind"] == "clip" and sa.get("speaker") == sb.get("speaker")
                and _iou([ra["cur"][q] for q in "xywh"], [rb["cur"][q] for q in "xywh"]) > 0.3):
            cap = rb["zoom_start"] / o["jump"]
            jn = (f"jump cut into shot {sb['i']}: this shot ends <= {cap:.3f}x so the cut "
                  f"still tightens >= {round((o['jump'] - 1) * 100)}%")
        r = plan_shot_frame(ra["boxes"], ra["cur"], sa, (SW, SH), (W, H), o, cap=cap, area=ra["area"])
        if jn and r["zoom_end"] >= cap - 0.01:
            r["notes"].append(jn)
        if verify:
            r = _verify_backoff(r, ra, sa, proj, o, (SW, SH), (W, H), cap)
        r.update(status="WARN" if (ra["miss"] or r["source_limited"] or r.get("furniture_hit") or r.get("text_conflict")
                                   or not r.get("sim", {}).get("pass", True)) else "OK",
                 scan=ra["scan"], cur=ra["cur"], area=ra["area"])
        if ra["miss"]:
            r["notes"].append(f"NO-FACE on {len(ra['miss'])} core frames (first {ra['scan']['no_face_t'][:4]}): "
                              "qc --faces will fail")
        results[sa["i"]] = r
    t_ver = time.time() - t_ver
    # write
    for shot in proj["shots"]:
        r = results.get(shot.get("i"))
        if not r or "start" not in r:
            continue
        S, E = r["start"], r["end"]
        shot.update(crop_x=S["x"], crop_y=S["y"], crop_w=S["w"], crop_h=S["h"])
        shot.pop("push", None)
        shot.pop("punch", None)
        if E:
            shot["push"] = E
            shot["punch"] = r["punch"]
            shot["push_ease"] = "inout"
        shot["framing"] = {k: r[k] for k in ("zoom_before", "zoom_start", "zoom_end", "zoom_max_safe",
                                            "margins_used", "pred_start", "pred_end", "notes")}
        shot["framing"]["area"] = {"bounds": r["area"]["bounds"], "avoid": len(r["area"]["avoid"])}
        if r.get("sim"):
            shot["framing"]["verify"] = {k: v for k, v in r["sim"].items() if k != "per_frame"}
    out_proj = pp if a.write else (Path(a.out).resolve() if a.out else pp.with_name(pp.stem + ".framed.json"))
    if a.write:
        bak = pp.with_name(pp.stem + ".preframe.json")
        if not bak.exists():
            shutil.copy2(pp, bak)
    side_path = out_proj.with_name(out_proj.stem + ".faces.json")
    proj["faces_sidecar"] = side_path.name
    side_path.write_text(json.dumps(sidecar), encoding="utf-8")
    clean = {k: v for k, v in proj.items() if not k.startswith("_")}
    out_proj.write_text(json.dumps(clean, indent=1), encoding="utf-8")
    lines = [f"introkit frame  {pp.name}  src {SW}x{SH}@{sfps:.3f}  model {YUNET_MODEL.name}",
             f"margins: head {o['head']:.0%} chin {o['chin']:.0%} side {o['side']:.0%}  target face "
             f"{o['target']:.0%} of {H}  cap {o['zoom_cap']}x  push {o['push']}  pad {o['pad_s']} s",
             f"{'shot':>4} {'status':8} {'zoom before':>11} {'start':>6} {'end':>6} {'push':>6} {'max':>6}"
             f"  {'start crop (x,y,w,h)':22} {'qc margins at start / end (top chin left right)'}"]
    for i, r in results.items():
        if "start" not in r:
            lines.append(f"{i:>4} {r['status']:8} " + "; ".join(r["notes"]))
            continue
        S, ps, pe = r["start"], r["pred_start"], r["pred_end"]
        lines.append(f"{i:>4} {r['status']:8} {r['zoom_before']:>11.3f} {r['zoom_start']:>6.3f} {r['zoom_end']:>6.3f}"
                     f" {r['punch']:>6.3f} {r['zoom_max_safe']:>6.3f}  {S['x']},{S['y']},{S['w']},{S['h']:<9}"
                     f" {ps['top']}/{pe['top']} {ps['chin']}/{pe['chin']} {ps['left']}/{pe['left']} {ps['right']}/{pe['right']}")
        if r.get("sim"):
            m = r["sim"]
            lines.append(f"     verify at delivered scale ({m['frames']} frames, 5-frame median): top {m['top']} "
                         f"chin {m['chin']} left {m['left']} right {m['right']} -> "
                         f"{'PASS' if m['pass'] else 'FAIL'} (needs top >= {m['need_top']}, chin >= {m['need_chin']}, "
                         f"sides >= {m['need_side']})")
        ab = r["area"]["bounds"]
        lines.append(f"     allowed area {ab['x']},{ab['y']},{ab['w']},{ab['h']} {r['area']['why']}"
                     + (f", {len(r['area']['avoid'])} avoid rect(s)" if r["area"]["avoid"] else ""))
        sc = r["scan"]
        lines.append(f"     scan {sc['frames_scanned']} frames: yunet {sc['yunet']}, low-score {sc['low_score']}, "
                     f"no face {sc['no_face']}" + (f"; text column overlap {r['text_column_overlap_px']}"
                                                    if r.get("text_column_overlap_px") else ""))
        for n in r["notes"]:
            lines.append(f"     - {n}")
    lines.append(f"scan {t_scan:.1f} s, plan+verify {t_ver:.1f} s, total {time.time() - t_start:.1f} s -> {out_proj}")
    rep = "\n".join(lines)
    print(rep)
    out_proj.with_name(out_proj.stem + ".frame.txt").write_text(rep + "\n", encoding="utf-8")


def _iou(a, b) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    i = ix * iy
    return i / max(1e-6, a[2] * a[3] + b[2] * b[3] - i)


def _median_track(boxes: list, k: int = 2) -> list:
    """Per-coordinate running median over +-k found frames: removes single-frame detector
    jitter (YuNet box edges wobble 2-4 % of face height frame to frame), keeps real motion."""
    out = []
    for i in range(len(boxes)):
        win = boxes[max(0, i - k): i + k + 1]
        out.append([sorted(b[j] for b in win)[len(win) // 2] for j in range(4)])
    return out


def shot_who(shot: dict) -> str:
    """Speaker name of a clip shot: shot["who"], else "(Name)" in the beat label, else its speaker role."""
    if shot.get("who"):
        return str(shot["who"]).lower()
    import re
    m = re.search(r"\(([^)]+)\)", shot.get("beat", ""))
    return m.group(1).strip().lower() if m else str(shot.get("speaker", "guest")).lower()


def allow_top_touch(proj: dict, shot: dict) -> bool:
    """Opt-in: the head may touch the top edge (source-limited headroom the user accepted). Shot key
    allow_top_touch: true, or project allow_top_touch: true | [shot numbers / speaker names / roles].
    Chin, sides and one-face are still gated."""
    if "allow_top_touch" in shot:
        return bool(shot["allow_top_touch"])
    v = proj.get("allow_top_touch")
    if isinstance(v, list):
        keys = {str(x).lower() for x in v}
        return bool(keys & {str(shot.get("i")), shot_who(shot), str(shot.get("speaker", "")).lower()})
    return bool(v)


def qc_faces(final: Path, project: str, ff: str, ffprobe: str, plan: dict | None, o: dict) -> dict:
    """Per-frame face gate on the delivered file. Detect on every clip frame (YuNet, then a
    low-score pass); if both miss, use the `frame` sidecar's source box mapped through that
    frame's crop. Any frame with none of these is NO FACE and fails. Margins are judged on a
    5-frame median of the face track (raw per-frame minimum is reported too)."""
    pp, proj = _load_project(project)
    fps = proj.get("fps", 30)
    info = ffprobe_json(ffprobe, final)
    v = next(s for s in info["streams"] if s.get("width"))
    W, H = int(v["width"]), int(v["height"])
    side = {}
    sp = _faces_sidecar(pp, proj)
    if sp.is_file():
        side = json.loads(sp.read_text(encoding="utf-8")).get("shots", {})
    spans, t = [], 0
    for s in proj["shots"]:
        if s["kind"] == "clip":
            spans.append((t, t + int(s["frames"]) - 1, s))
        t += int(s["frames"])
    finder = FaceFinder((W, H), o)
    R, track, prev_shot, prev = {}, {}, None, None
    for i, fr in enumerate(_frames(ff, str(final), 0.0, None, (W, H), plan)):
        hit = [(a_, b_, s) for a_, b_, s in spans if a_ <= i <= b_]
        if not hit:
            continue
        a_, b_, s = hit[0]
        k = i - a_
        if s is not prev_shot:
            prev_shot, prev = s, None
        r = R.setdefault(s["i"], {"beat": s.get("beat", ""), "frames": 0, "yunet": 0, "low_score": 0,
                                  "source_mapped": 0, "no_face": [], "fail_frames": []})
        r["frames"] += 1
        exp = None
        rows = side.get(str(s["i"]), {}).get("samples")
        if rows:
            ts = float(s["src_in"]) + k / fps
            near = min(rows, key=lambda q: abs(q[0] - ts))
            if near[1] and abs(near[0] - ts) < 0.05:
                rc = shot_rect_at(proj, s, k)
                sx, sy = W / rc["w"], H / rc["h"]
                b = near[1]
                exp = [(b[0] - rc["x"]) * sx, (b[1] - rc["y"]) * sy, b[2] * sx, b[3] * sy]
        box, how = finder.find(fr, None, prev or exp)
        if box and exp and _iou(box, exp) < 0.2:
            box, how = None, "none"
        if box is None and exp:
            box, how = exp, "source_mapped"
        if box is None:
            r["no_face"].append(i)
            continue
        prev = box[:4]
        r[{"yunet": "yunet", "low": "low_score"}.get(how, "source_mapped")] += 1
        track.setdefault(s["i"], []).append((i, [float(x) for x in box[:4]]))
    ok = True
    byid = {s["i"]: s for _, _, s in spans}
    for sid, r in R.items():
        top_ok = allow_top_touch(proj, byid[sid])
        r["allow_top_touch"] = top_ok
        tr = track.get(sid, [])
        sm = _median_track([b for _, b in tr])
        per, raw_top = [], 1e9
        m = {"top": 1e9, "chin": 1e9, "left": 1e9, "right": 1e9}
        for (i, b), (X, Y, Bw, Bh) in zip(tr, sm):
            raw_top = min(raw_top, b[1] - o["qc_head"] * b[3])
            top, chin, left, right = Y - o["qc_head"] * Bh, H - (Y + Bh), X, W - (X + Bw)
            for q, val in (("top", top), ("chin", chin), ("left", left), ("right", right)):
                m[q] = min(m[q], val)
            per.append([i, round(top, 1), round(chin, 1), round(left, 1), round(right, 1), round(Bh, 1)])
            if top < 0 and top_ok:
                r["top_touch_accepted"] = r.get("top_touch_accepted", 0) + 1
            if (top < 0 and not top_ok) or chin < o["qc_chin"] * Bh or left < o["qc_side"] * Bw or right < o["qc_side"] * Bw:
                r["fail_frames"].append(i)
        r["fail_frames"] = sorted(set(r["fail_frames"]) | set(r["no_face"]))
        for q in m:
            r[q] = round(m[q], 1) if m[q] < 1e8 else None
        r["top_raw"] = round(raw_top, 1) if raw_top < 1e8 else None
        r["per_frame"] = per                      # [frame, top, chin, left, right, face_h] (smoothed)
        r["pass"] = not r["fail_frames"]
        ok &= r["pass"]
        r["no_face"] = r["no_face"][:20]
    missing = sorted({s["i"] for _, _, s in spans} - set(R))
    if missing:
        ok = False
    return {"pass_faces": bool(ok and R), "sidecar": str(sp) if side else None, "shots": R,
            "gate": f"top(head+{o['qc_head']:.0%})>=0 (unless allow_top_touch), chin>={o['qc_chin']:.0%}h, "
                    f"sides>={o['qc_side']:.0%}w (5-frame median track), a face on every clip frame",
            "missing_shots": missing}


# --------------------------------------------------------------------------- hw / preview

def cmd_hw(a) -> None:
    ff = _which("ffmpeg", a.ffmpeg)
    plan = plan_hw(ff, a.device, None, hwaccel=a.hwaccel)
    tiers = plan.get("hw", {}).get("tiers", {})
    if tiers:
        print(f"{'tier':14}{'os':5}{'listed dec':12}{'decode test':13}{'listed enc':12}{'encode test'}")
        for n, t in tiers.items():
            yn = lambda v: "yes" if v else "-"
            print(f"{n:14}{yn(t['os_ok']):5}{yn(t['listed_decode']):12}{('PASS' if t['decode'] else '-'):13}"
                  f"{yn(t['listed_encode']):12}{'PASS' if t['encode'] else '-'}")
    if a.json:
        print(json.dumps(plan, indent=2))


def cmd_preview(a) -> None:
    """Review copy only (never the deliverable): first hardware encoder that passed its
    self-test (NVENC, Quick Sync, AMF, VAAPI, VideoToolbox), else libx264 veryfast."""
    ff = _which("ffmpeg", a.ffmpeg)
    plan = plan_hw(ff, a.device, None, hwaccel=a.hwaccel)
    out = Path(a.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    src = str(Path(a.final).expanduser().resolve())
    for enc in dict.fromkeys([plan["preview_encoder"], "libx264"]):
        pre, codec, tail = encoder_args(enc)
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", *pre]
        rest = ["-i", src, "-vf", f"scale=-2:{int(a.height)}" + tail, *codec,
                "-maxrate", "6M", "-bufsize", "12M", "-c:a", "aac", "-b:a", "160k",
                "-movflags", "+faststart", str(out)]
        if enc == "libx264":
            rest[rest.index("-maxrate"):rest.index("-maxrate")] = ["-pix_fmt", "yuv420p"]
        p = run_decode(cmd, rest, plan) if enc == "libx264" else run(cmd + hwaccel_args(plan) + rest, check=False)
        if p.returncode == 0:
            print(f"[introkit] preview ({enc}) -> {out}  {out.stat().st_size / 1e6:.1f} MB")
            return
        print(f"[introkit] preview with {enc} failed, falling back to libx264", flush=True)


# --------------------------------------------------------------------------- transcribe

def models_dir() -> Path:
    return env_home() / "models"


def load_whisper(dev: str, ct: str, threads: int):
    from faster_whisper import WhisperModel
    md = models_dir()
    md.mkdir(parents=True, exist_ok=True)
    kw = dict(device=dev, compute_type=ct, download_root=str(md))
    if dev == "cpu":
        kw["cpu_threads"] = threads
    try:
        return WhisperModel(WHISPER_MODEL, **kw)
    except Exception as e:
        if dev == "cuda":           # broken CUDA runtime -> same model on CPU int8
            print(f"[introkit] CUDA Whisper failed ({e.__class__.__name__}: {e}); using CPU int8", flush=True)
            kw.update(device="cpu", compute_type="int8", cpu_threads=threads)
            return WhisperModel(WHISPER_MODEL, **kw)
        raise


def cmd_fetch_model(a) -> None:
    """Download Whisper large-v3-turbo (~1.6 GB) and the speaker-embedding ONNX (~27 MB) into
    <ROLLUP_INTRO_HOME>/models (one-time; offline afterwards)."""
    from faster_whisper.utils import download_model
    md = models_dir()
    md.mkdir(parents=True, exist_ok=True)
    path = download_model(WHISPER_MODEL, cache_dir=str(md))
    print(f"[introkit] {WHISPER_MODEL} ready at {path}")
    sp = _lib("speakers")
    print(f"[introkit] speaker-embedding model ready at {sp.model_path(md)}")


def cmd_env(a) -> None:
    import importlib.metadata as im
    print(f"skill dir     {SKILL_DIR}")
    print(f"env home      {env_home()}  ({'exists' if env_home().exists() else 'MISSING - run setup'})")
    print(f"python        {sys.executable}  {platform.python_version()}")
    print(f"models        {models_dir()}")
    print(f"HF_HOME       {os.environ.get('HF_HOME', '(unset)')}")
    for name in ("ffmpeg", "ffprobe"):
        try:
            print(f"{name:13} {_which(name, getattr(a, name, None))}")
        except SystemExit as e:
            print(f"{name:13} {e}")
    for pkg in ("faster-whisper", "ctranslate2", "av", "numpy", "pillow", "opencv-python-headless",
                "onnxruntime", "onnxruntime-gpu", "onnxruntime-openvino", "onnxruntime-directml",
                "psutil", "nvidia-cublas-cu12", "nvidia-cudnn-cu12"):
        try:
            print(f"  {pkg:24} {im.version(pkg)}")
        except im.PackageNotFoundError:
            pass


def _lib(name: str):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    return __import__(name)


def _embedder(plan: dict):
    sp = _lib("speakers")
    try:
        sess = onnx_session(sp.model_path(models_dir()), plan)
    except ImportError:
        sys.exit("[introkit] onnxruntime / huggingface-hub missing: run setup (TOOLS.md)")
    return sp, sp.Embedder(sess)


def _parse_enroll(items, ff: str, src: str | None, emb, sp) -> dict:
    import numpy as np
    """NAME=START:DUR (a span of --src) or NAME=FILE or NAME=FILE@START:DUR, repeatable.
    Several clips for one NAME are averaged into one centroid."""
    tr = _lib("transcribe")
    vecs: dict = {}
    for it in items or []:
        name, spec = it.split("=", 1)
        f, rng = src, spec
        if "@" in spec:
            f, rng = spec.rsplit("@", 1)
        elif ":" not in spec or Path(spec).exists():
            f, rng = spec, None
        st, du = (float(x) for x in rng.split(":")) if rng else (None, None)
        x = tr.load_audio(ff, f, st, du)
        ws = sp.windows([(0.0, len(x) / sp.SR)]) or [(0.0, len(x) / sp.SR)]
        v = np.mean([emb(x[int(s0 * sp.SR):int(s1 * sp.SR)]) for s0, s1 in ws], axis=0)
        vecs.setdefault(name, []).append(v)
    return {n: (lambda m: m / (np.linalg.norm(m) + 1e-9))(np.mean(v, axis=0)) for n, v in vecs.items()}


def _diarize_into(tr: dict, ff: str, src, plan: dict, n_speakers, enroll_items, start=None, dur=None) -> dict:
    sp, emb = _embedder(plan)
    tl = _lib("transcribe")
    off = float(start or 0)
    x = tl.load_audio(ff, src, start, dur)
    enroll = _parse_enroll(enroll_items, ff, str(src), emb, sp) if enroll_items else None
    import time
    t0 = time.time()
    info = sp.diarize(x, emb, tr["cues"], offset=off, n_speakers=n_speakers, enroll=enroll)
    sp.label_words(tr["cues"], info["window_labels"])
    if any(c.get("words") and len(c["words"]) > 1 for c in tr["cues"]):
        tr["cues"] = sp.split_cues_by_speaker(tr["cues"])
    wt = [w for c in tr["cues"] for w in c.get("words", []) if w.get("speaker")]
    if wt:      # turns from labelled words: contiguous, non-overlapping
        turns: list[dict] = []
        for w in wt:
            if turns and turns[-1]["speaker"] == w["speaker"]:
                turns[-1]["end"] = w["end"]
            else:
                turns.append({"start": w["start"], "end": w["end"], "speaker": w["speaker"]})
        info["turns"] = turns
    meta = tr.setdefault("meta", {})
    meta["diarization"] = {k: v for k, v in info.items() if k != "window_labels"}
    meta["diarization"]["model"] = f"{sp.EMBED_REPO}/{sp.EMBED_FILE}@{sp.EMBED_REVISION[:8]}"
    meta["diarization"]["providers"] = plan["onnx_providers"]
    meta["diarization"]["seconds"] = round(time.time() - t0, 2)
    print(f"[introkit] diarization: {info.get('method')} k={info.get('k', len(enroll or {}))} "
          f"{info['windows']} windows, {len(info['turns'])} turns, {meta['diarization']['seconds']} s")
    return tr


def cmd_transcribe(a) -> None:
    """Word-timestamped ASR (Whisper large-v3-turbo), optional diarization. See transcribe.py."""
    import time
    import numpy as np  # noqa: F401
    _cuda_dll_bootstrap()
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        sys.exit(f"[introkit] faster-whisper is not installed in {sys.executable}. Run setup.ps1 "
                 f"(Windows) or setup.sh (macOS/Linux) to create {env_home()}; see TOOLS.md")
    tl = _lib("transcribe")
    ff = _which("ffmpeg", a.ffmpeg)
    plan = plan_hw(ff, a.device, None, hwaccel=a.hwaccel)
    dev, ct = plan["whisper"].split("/")
    if a.compute_type:
        ct = a.compute_type
    src = Path(a.src).expanduser().resolve()
    audio = tl.load_audio(ff, src, a.start, a.dur)
    audio_s = len(audio) / tl.SR
    print(f"[introkit] whisper {WHISPER_MODEL} on {dev}/{ct}, {audio_s:.1f} s of audio"
          + ("  (CPU int8: slower than CUDA; use --start/--dur to transcribe only the spans "
             "you need)" if dev == "cpu" else ""), flush=True)
    t0 = time.time()
    model = load_whisper(dev, ct, plan["cpu_threads"])
    off = float(a.start or 0)
    cues = tl.run_asr(model, audio, a.language, a.hotwords, not a.no_vad, off)
    el = time.time() - t0
    meta = {"source": str(src), "engine": f"faster-whisper {WHISPER_MODEL} {dev}/{ct}",
            "language": a.language, "vad": not a.no_vad, "start": off,
            "duration": round(audio_s, 3), "seconds": round(el, 2), "tool": "rollup-intro introkit"}
    tr = {"meta": meta, "cues": cues}
    if a.diarize or a.enroll:
        tr = _diarize_into(tr, ff, src, plan, a.speakers, a.enroll, a.start, a.dur)
    out = Path(a.out).expanduser().resolve()
    if out.suffix.lower() != ".json":
        out = out / (src.stem + ".json")
    files = tl.write_outputs(tr["cues"], tr["meta"], out, tuple(a.formats.split(",")))
    print(f"[introkit] {sum(len(c['words']) for c in cues)} words in {el:.1f} s "
          f"({audio_s / max(el, 1e-6):.2f}x realtime) -> " + ", ".join(str(f) for f in files))
    for c in tr["cues"]:
        print(f"  {c['start']:8.2f} {c.get('speaker') or '':12} {c['text']}")


def cmd_diarize(a) -> None:
    """Add speaker labels to an existing word-level transcript JSON. Skipped if it already
    has speakers (use --force to redo)."""
    tl = _lib("transcribe")
    tr = tl.read_transcript(Path(a.transcript))
    if tl.has_speakers(tr) and not a.force:
        print(f"[introkit] {a.transcript} already has speaker labels; skipping (use --force)")
        return
    ff = _which("ffmpeg", a.ffmpeg)
    plan = plan_hw(ff, a.device, None, hwaccel=a.hwaccel)
    src = a.src or tr.get("meta", {}).get("source")
    if not src:
        sys.exit("[introkit] pass --src (the transcript does not name its source)")
    cue_level = not tl.has_words(tr)
    if cue_level:      # SRT: one pseudo-word per cue so cue spans drive the windows and labels
        print("[introkit] transcript has no word times (SRT): labelling whole cues")
        for c in tr["cues"]:
            c["words"] = [{"start": c["start"], "end": c["end"], "text": c["text"], "probability": 1.0}]
    tr = _diarize_into(tr, ff, src, plan, a.speakers, a.enroll, a.start, a.dur)
    if cue_level:
        for c in tr["cues"]:
            c["words"] = []
    out = Path(a.out or Path(a.transcript).with_suffix(".speakers.json")).expanduser().resolve()
    files = tl.write_outputs(tr["cues"], tr["meta"], out, tuple(a.formats.split(",")))
    print("[introkit] wrote " + ", ".join(str(f) for f in files))
    for t in tr["meta"]["diarization"]["turns"]:
        print(f"  {t['start']:8.2f}-{t['end']:8.2f}  {t['speaker']}")


def cmd_voiceprint(a) -> None:
    """Cosine matrix between spans (speaker-on-camera gate, host identity). --span NAME=START:DUR
    of --src, or NAME=FILE / NAME=FILE@START:DUR; repeat. Optional --enroll for references."""
    ff = _which("ffmpeg", a.ffmpeg)
    plan = plan_hw(ff, a.device, None, hwaccel=a.hwaccel)
    sp, emb = _embedder(plan)
    vecs = _parse_enroll(a.span, ff, a.src, emb, sp)
    refs = _parse_enroll(a.enroll, ff, a.src, emb, sp) if a.enroll else {}
    allv = {**{f"ref:{k}": v for k, v in refs.items()}, **vecs}
    m = sp.cosine_matrix(allv)
    w = max(len(n) for n in m["names"]) + 2
    print(" " * w + "".join(f"{n[:9]:>10}" for n in m["names"]))
    for n, row in zip(m["names"], m["cosine"]):
        print(f"{n:{w}}" + "".join(f"{v:10.3f}" for v in row))
    if refs:
        m["best_ref"] = {n: max(refs, key=lambda r: float(vecs[n] @ refs[r])) for n in vecs}
        for n, r in m["best_ref"].items():
            print(f"  {n} -> {r}  cos={float(vecs[n] @ refs[r]):.3f}")
    if a.out:
        sp.save(Path(a.out), m)


# --------------------------------------------------------------------------- qc

def cmd_qc(a) -> None:
    """Consolidated post-build QC (scripts/sidekit.py): four decisive gates (format, audio, framing,
    speech) in ONE run, one PASS/FAIL table with shot/time + fix hint, per-shot cache, contact sheet.
    --classic-faces additionally runs the older every-frame face gate as a diagnostic."""
    _lib("sidekit").cmd_qc(a)


# --------------------------------------------------------------------------- cli

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    _maybe_reexec(argv)
    _env_setup()
    ap = argparse.ArgumentParser(prog="introkit", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ffmpeg", default=None)
    ap.add_argument("--ffprobe", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def dev(p):
        p.add_argument("--device", default=None, choices=list(DEVICES) + list(_DEV_ALIASES),
                       help="auto (default: cuda -> apple -> cpu), cuda, apple, cpu; "
                            "also project key 'device' or env INTROKIT_DEVICE")
        p.add_argument("--hwaccel", default=None, choices=list(HWACCELS),
                       help="pin the DECODE path only (auto, none, cuda, qsv, d3d11va, dxva2, "
                            "vaapi, videotoolbox); also project key 'hwaccel' or env INTROKIT_HWACCEL")
        return p

    h = dev(sub.add_parser("hw", help="show the hardware path --device would use"))
    h.add_argument("--json", action="store_true", help="also dump the full plan as JSON")
    h.set_defaults(func=cmd_hw)

    e = sub.add_parser("env", help="show the tool folder, interpreter, ffmpeg and package versions")
    e.set_defaults(func=cmd_env)
    m = sub.add_parser("fetch-model", help=f"download Whisper {WHISPER_MODEL} into the tool folder")
    m.set_defaults(func=cmd_fetch_model)

    p = sub.add_parser("probe", help="inspect source and reference")
    p.add_argument("--src", required=True)
    p.add_argument("--ref")
    p.add_argument("--out", default="work")
    p.set_defaults(func=cmd_probe)

    def spk(p):
        p.add_argument("--speakers", type=int, default=None, help="known speaker count (else silhouette)")
        p.add_argument("--enroll", action="append", default=None,
                       help="NAME=START:DUR of --src, NAME=FILE or NAME=FILE@START:DUR; repeatable. "
                            "Labels become the names (e.g. guest, host_robbie)")
        p.add_argument("--formats", default="json,srt,txt")
        return p

    t = spk(dev(sub.add_parser("transcribe", help=f"word-timestamped ASR, Whisper {WHISPER_MODEL}")))
    t.add_argument("--src", required=True)
    t.add_argument("--out", default="work", help="folder (writes <src stem>.json/.srt/.txt) or a .json path")
    t.add_argument("--language", default="en")
    t.add_argument("--start", type=float, default=None, help="span start (s)")
    t.add_argument("--dur", type=float, default=None, help="span length (s)")
    t.add_argument("--hotwords", default=None)
    t.add_argument("--no-vad", action="store_true",
                   help="disable VAD (QC passes: run with VAD on AND off, see RULES 025)")
    t.add_argument("--compute-type", default=None, help="override, e.g. int8, int8_float16, float16")
    t.add_argument("--diarize", action="store_true", help="also label speakers (see `diarize`)")
    t.set_defaults(func=cmd_transcribe)

    d = spk(dev(sub.add_parser("diarize", help="add speaker labels to a transcript (skips if labelled)")))
    d.add_argument("--transcript", required=True, help="JSON from `transcribe` (or .srt: cue level)")
    d.add_argument("--src", default=None, help="video/audio (default: transcript meta.source)")
    d.add_argument("--out", default=None, help="default <transcript>.speakers.json")
    d.add_argument("--start", type=float, default=None)
    d.add_argument("--dur", type=float, default=None)
    d.add_argument("--force", action="store_true")
    d.set_defaults(func=cmd_diarize)

    vp = dev(sub.add_parser("voiceprint", help="cosine matrix of spans vs reference voices"))
    vp.add_argument("--src", default=None)
    vp.add_argument("--span", action="append", required=True, help="NAME=START:DUR | NAME=FILE[@S:D]")
    vp.add_argument("--enroll", action="append", default=None, help="reference voices, same syntax")
    vp.add_argument("--out", default=None)
    vp.set_defaults(func=cmd_voiceprint)

    w = sub.add_parser("words", help="build per-shot word timings")
    w.add_argument("--project", required=True)
    w.add_argument("--transcript", required=True)
    w.add_argument("--out", default="work/words.json")
    w.set_defaults(func=cmd_words)

    b = dev(sub.add_parser("build", help="render the intro"))
    b.add_argument("--project", required=True)
    b.add_argument("--words", default=None)
    b.add_argument("--out", default=None, help="output folder (default: project out_dir, else "
                                               "<source video folder>\\intros; scratch in <out>\\work)")
    b.add_argument("--fonts", default=None, help="font folder (else project fonts_dir, "
                                                  "INTROKIT_FONTS, OS font folders)")
    b.add_argument("--keep-overlay", action="store_true")
    b.set_defaults(func=cmd_build)

    v = dev(sub.add_parser("preview", help="small review copy (not the deliverable)"))
    v.add_argument("--final", required=True)
    v.add_argument("--out", required=True)
    v.add_argument("--height", type=int, default=720)
    v.set_defaults(func=cmd_preview)

    fr = dev(sub.add_parser("frame", help="face-safe zoom: per-shot crop + face-anchored push-in from "
                                           "a YuNet scan of every source frame"))
    fr.add_argument("--project", required=True)
    fr.add_argument("--out", default=None, help="framed project path (default <project>.framed.json)")
    fr.add_argument("--write", action="store_true", help="update --project in place (keeps <stem>.preframe.json)")
    fr.add_argument("--shots", default=None, help="only these shot numbers, e.g. 1,3,4")
    fr.add_argument("--no-push", action="store_true", help="static zoom only")
    fr.add_argument("--push", type=float, default=None, help=f"wanted push-in (default {FACE['push']})")
    fr.add_argument("--zoom-cap", dest="zoom_cap", type=float, default=None, help=f"max zoom (default {FACE['zoom_cap']})")
    fr.add_argument("--target", type=float, default=None, help=f"face height / frame height (default {FACE['target']})")
    fr.add_argument("--head", type=float, default=None, help=f"hair allowance x face height (default {FACE['head']})")
    fr.add_argument("--chin", type=float, default=None, help=f"chin allowance x face height (default {FACE['chin']})")
    fr.add_argument("--side", type=float, default=None, help=f"side allowance x face width (default {FACE['side']})")
    fr.add_argument("--top-pad", dest="top_pad", type=float, default=None, help=f"preferred output px above head+margin (default {FACE['top_pad']})")
    fr.add_argument("--pad", dest="pad_s", type=float, default=None, help=f"scan pad around each shot, s (default {FACE['pad_s']})")
    fr.add_argument("--scan-scale", dest="scan_scale", type=float, default=None, help=f"detector scale (default {FACE['scan_scale']})")
    fr.add_argument("--layout", choices=["side", "classic"], default=None,
                    help="side (default; also project key layout): ONE speaker per shot, zoomed, face on a third "
                         "(0.30/0.70 W), captions on the opposite black side, other people cropped or matted; "
                         "classic: the older union-of-boxes planner below")
    fr.add_argument("--no-verify", dest="no_verify", action="store_true",
                    help="skip the delivered-scale qc simulation + zoom back-off (faster, less safe)")
    fr.set_defaults(func=cmd_frame)

    q = dev(sub.add_parser("qc", help="consolidated gates: format, audio, framing, speech -> one PASS/FAIL table"))
    q.add_argument("--final", required=True)
    q.add_argument("--project", default=None, help="project the file was built from (framing + speech gates)")
    q.add_argument("--words", default=None, help="words json (default: words_<X>.json next to project_<X>*.json)")
    q.add_argument("--out", default=None, help="JSON report (contact sheet + waveform go next to it)")
    q.add_argument("--sheet", default=None, help="contact sheet path (default <out or final stem>_contact.jpg)")
    q.add_argument("--no-cache", action="store_true", help="re-run every shot (default: unchanged shots reuse results)")
    q.add_argument("--classic-faces", action="store_true", help="diagnostic: also the old every-frame face gate")
    q.add_argument("--faces", action="store_true", help=argparse.SUPPRESS)       # old flag: gates are automatic now
    q.set_defaults(func=cmd_qc)

    sk = _lib("sidekit")
    ab = dev(sub.add_parser("assemble", help="sets.json -> project_<SET>.json + words_<SET>.json"))
    ab.add_argument("--sets", required=True)
    ab.add_argument("--set", required=True, dest="set_name")
    ab.add_argument("--out", default=None, help="folder for project/words json (default: <out_dir>\\work if "
                                                "sets.json has out_dir, else <source video folder>\\intros\\work)")
    ab.set_defaults(func=sk.cmd_assemble)

    vt = dev(sub.add_parser("vet", help="selection-time checks per shot, cached: stutters/fillers (ASR VAD on+off), "
                                         "acoustic end of the last word, dip grade vs room floor, headroom; --write fixes outs"))
    vt.add_argument("--project", required=True)
    vt.add_argument("--words", required=True)
    vt.add_argument("--write", action="store_true", help="apply the out-point fix to project + words (keeps .pre_vet copies)")
    vt.add_argument("--tail", type=float, default=0.08, help="tail after the acoustic end, s (0.06-0.12)")
    vt.add_argument("--fade-ms", dest="fade_ms", type=int, default=25, help="speech fade-out, ms (20-30)")
    vt.add_argument("--allow-shorten", action="store_true", help="also pull outs earlier (default: only extend)")
    vt.add_argument("--no-cache", action="store_true")
    vt.set_defaults(func=sk.cmd_vet)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()

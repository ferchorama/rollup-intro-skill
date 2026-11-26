# Real-ESRGAN upscale of zoomed intro shots (Tristan _UP / Lord Chris Holmes v3 method).
#
# Crop the zoomed source region, run realesr-general-x4v3 blended with wdn-x4v3 at
# denoise 0.5 (GPU half when CUDA is present, otherwise CPU float32), then redo the
# same push, grade and captions as introkit. Applied to clip shots whose crop zoom
# is above MIN_ZOOM (1.15x). Audio is never re-encoded here: picture only; the
# build mux stream-copies the staged audio and verifies it by hash.
#
# Optional extras: requirements-upscale.txt (torch). Weights are fetched into
# .rollup-intro-env/models/upscale/ by `introkit fetch-upscale-model`.
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

MIN_ZOOM = 1.15
DENOISE = 0.5
WEIGHT_URLS = {
    "realesr-general-x4v3.pth":
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
    "realesr-general-wdn-x4v3.pth":
        "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-wdn-x4v3.pth",
}


def _ik():
    import introkit as K
    return K


def weights_dir() -> Path:
    K = _ik()
    env = K.env_home() / "models" / "upscale"
    assets = K.SKILL_DIR / "assets" / "models"
    for d in (env, assets):
        if (d / "realesr-general-x4v3.pth").is_file() and (d / "realesr-general-wdn-x4v3.pth").is_file():
            return d
    return env


def weights_ready(d: Path | None = None) -> bool:
    d = d or weights_dir()
    return (d / "realesr-general-x4v3.pth").is_file() and (d / "realesr-general-wdn-x4v3.pth").is_file()


def torch_ready() -> bool:
    try:
        import torch  # noqa: F401
        return True
    except ImportError:
        return False


def available() -> tuple[bool, str]:
    if not torch_ready():
        return False, "torch not installed (optional extras: requirements-upscale.txt)"
    if not weights_ready():
        return False, "Real-ESRGAN weights missing (run: introkit fetch-upscale-model)"
    return True, "ok"


def fetch_weights(dest: Path | None = None, log=print) -> Path:
    """Download the two general-x4v3 weight files if they are not already on disk."""
    K = _ik()
    dest = dest or (K.env_home() / "models" / "upscale")
    dest.mkdir(parents=True, exist_ok=True)
    try:
        from urllib.request import urlopen, Request
    except ImportError as e:  # pragma: no cover
        raise SystemExit(f"fetch-upscale-model: urllib missing ({e})") from e
    for name, url in WEIGHT_URLS.items():
        path = dest / name
        if path.is_file() and path.stat().st_size > 1_000_000:
            log(f"[up] have {path}")
            continue
        log(f"[up] downloading {name}")
        tmp = path.with_suffix(".tmp")
        req = Request(url, headers={"User-Agent": "rollup-intro-skill"})
        with urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
        tmp.replace(path)
        log(f"[up] wrote {path} ({path.stat().st_size} bytes)")
    return dest


def pick_device() -> str:
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


# ---- SRVGGNetCompact (realesr-general-x4v3). Original implementation used by the
# Tristan _UP builds; not copied from any third-party repo's training code.
def _srvgg():
    import torch.nn as nn
    import torch.nn.functional as F

    class SRVGG(nn.Module):
        def __init__(self, nf=64, nc=32, up=4):
            super().__init__()
            self.up = up
            body = [nn.Conv2d(3, nf, 3, 1, 1), nn.PReLU(num_parameters=nf)]
            for _ in range(nc):
                body += [nn.Conv2d(nf, nf, 3, 1, 1), nn.PReLU(num_parameters=nf)]
            body += [nn.Conv2d(nf, 3 * up * up, 3, 1, 1)]
            self.body = nn.ModuleList(body)
            self.ps = nn.PixelShuffle(up)

        def forward(self, x):
            o = x
            for layer in self.body:
                o = layer(o)
            return self.ps(o) + F.interpolate(x, scale_factor=self.up, mode="nearest")

    return SRVGG


def load_model(weights: Path, denoise: float = DENOISE, device: str | None = None):
    import torch
    SRVGG = _srvgg()
    device = device or pick_device()
    general = weights / "realesr-general-x4v3.pth"
    wdn = weights / "realesr-general-wdn-x4v3.pth"
    params = torch.load(str(general), map_location="cpu")["params"]
    if denoise < 1:
        mix = torch.load(str(wdn), map_location="cpu")["params"]
        params = {k: denoise * params[k] + (1 - denoise) * mix[k] for k in params}
    model = SRVGG()
    model.load_state_dict(params, strict=True)
    model.eval()
    if device == "cuda":
        return model.half().cuda(), device
    return model.float().cpu(), "cpu"


def process_shot(job: dict, model=None, device: str | None = None, weights: Path | None = None) -> None:
    """Stream: ffmpeg crop -> Real-ESRGAN x4 (or bicubic if UP_BYPASS) -> resize -> push/grade/encode."""
    import numpy as np
    import torch
    import torch.nn.functional as F

    if model is None:
        model, device = load_model(weights or weights_dir(), job.get("denoise", DENOISE), device)
    device = device or pick_device()
    Ux, Uy, Uw, Uh = job["U"]
    ow, oh = job["out_wh"]
    n = int(job["n"])
    dec = [job["ff"], "-hide_banner", "-loglevel", "error", *job["before"], "-i", job["src"], *job["after"],
           "-vf", f"crop={Uw}:{Uh}:{Ux}:{Uy},scale=in_color_matrix=bt709:in_range=tv,format=rgb24",
           "-frames:v", str(n), "-r", str(job["fps"]), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    enc = [job["ff"], "-y", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
           "-s", f"{ow}x{oh}", "-r", str(job["fps"]), "-i", "-",
           "-vf", f"scale=out_color_matrix=bt709:out_range=tv,format=yuv444p,{job['vf']}",
           "-frames:v", str(n), "-r", str(job["fps"]), "-an", *job["enc"], job["dst"]]
    pd = subprocess.Popen(dec, stdout=subprocess.PIPE)
    pe = subprocess.Popen(enc, stdin=subprocess.PIPE)
    frame_bytes = Uw * Uh * 3
    k = 0
    t0 = time.time()
    use_cuda = device == "cuda"
    bypass = bool(os.environ.get("UP_BYPASS"))
    with torch.inference_mode():
        while True:
            buf = pd.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            arr = np.frombuffer(buf, np.uint8).reshape(Uh, Uw, 3).copy()
            if use_cuda:
                x = torch.from_numpy(arr).cuda().permute(2, 0, 1)[None].half() / 255
            else:
                x = torch.from_numpy(arr).permute(2, 0, 1)[None].float() / 255
            y = F.interpolate(x, scale_factor=4, mode="bicubic") if bypass else model(x)
            y = y.clamp_(0, 1)
            if (oh, ow) != (Uh * 4, Uw * 4):
                y = F.interpolate(y.float(), size=(oh, ow), mode="area")
            else:
                y = y.float()
            pe.stdin.write((y[0].permute(1, 2, 0) * 255 + 0.5).clamp_(0, 255).byte().cpu().numpy().tobytes())
            k += 1
    pe.stdin.close()
    pe.wait()
    pd.wait()
    print(f"[up] {k}/{n} frames {Uw}x{Uh} -> {ow}x{oh} on {device} in {time.time() - t0:.1f}s", flush=True)
    if k != n or pe.returncode != 0:
        raise SystemExit(1)


def _push_float(K, proj, shot, wh):
    old = K.shot_rect
    K.shot_rect = lambda p, s: {
        "x": float(s["crop_x"]), "y": float(s["crop_y"]),
        "w": float(s["crop_w"]), "h": float(s["crop_h"]),
    }
    try:
        return K._push_filter(proj, shot, wh)
    finally:
        K.shot_rect = old


def shot_zoom(K, proj, shot) -> float:
    s = K.shot_rect(proj, shot)
    e = shot.get("push") or s
    W = proj["width"]
    return W / min(float(s["w"]), float(e["w"]))


def stage_base(proj, ff, ffprobe, work: Path, plain=None, min_zoom: float = MIN_ZOOM) -> Path:
    """introkit picture stage with an AI pass on crops zoomed above min_zoom."""
    import ffseek
    K = _ik()
    plain = plain or K.stage_base_plain
    ok, why = available()
    if not ok:
        print(f"[up] skipped: {why}", flush=True)
        return plain(proj, ff, ffprobe, work)

    seg = work / "seg"
    seg.mkdir(parents=True, exist_ok=True)
    cache = work / "upscale_cache"
    cache.mkdir(parents=True, exist_ok=True)
    fps, W, H = proj.get("fps", 30), proj["width"], proj["height"]
    SW, SH = K._src_wh(ffprobe, proj["src"])
    enc = ["-c:v", "libx264", "-threads", "0", "-crf", "15", "-preset", "medium",
           "-pix_fmt", "yuv420p", *K.BT709, "-video_track_timescale", "30000"]
    files, log = [], []
    model, device = None, None
    wdir = weights_dir()

    for shot in proj["shots"]:
        dst = seg / f"s{shot['i']:02d}.mp4"
        n = int(shot["frames"])
        if shot["kind"] == "clip":
            s = K.shot_rect(proj, shot)
            e = shot.get("push") or s
            zoom = W / min(float(s["w"]), float(e["w"]))
            if zoom > min_zoom:
                if model is None:
                    model, device = load_model(wdir, proj.get("upscale_denoise", DENOISE))
                    print(f"[up] model on {device}, denoise {proj.get('upscale_denoise', DENOISE)}", flush=True)
                x0 = max(0, int(math.floor(min(s["x"], e["x"]))) - 8)
                y0 = max(0, int(math.floor(min(s["y"], e["y"]))) - 8)
                x1 = min(SW, int(math.ceil(max(s["x"] + s["w"], e["x"] + e["w"]))) + 8)
                y1 = min(SH, int(math.ceil(max(s["y"] + s["h"], e["y"] + e["h"]))) + 8)
                Uw, Uh = (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2
                f = min(4.0, math.ceil(W / float(min(s["w"], e["w"])) * 2) / 2)
                ow, oh = int(round(Uw * f / 2)) * 2, int(round(Uh * f / 2)) * 2
                fx, fy = ow / Uw, oh / Uh
                sh2 = dict(shot)
                sh2["crop_x"] = (s["x"] - x0) * fx
                sh2["crop_y"] = (s["y"] - y0) * fy
                sh2["crop_w"] = s["w"] * fx
                sh2["crop_h"] = s["h"] * fy
                if shot.get("push"):
                    sh2["push"] = {
                        "x": (e["x"] - x0) * fx, "y": (e["y"] - y0) * fy,
                        "w": e["w"] * fx, "h": e["h"] * fy,
                    }
                    vf = _push_float(K, proj, sh2, (ow, oh)) + f",setsar=1,{proj['grade']}"
                else:
                    cx = int(round(sh2["crop_x"]))
                    cy = int(round(sh2["crop_y"]))
                    cw = int(round(sh2["crop_w"])) // 2 * 2
                    ch = int(round(sh2["crop_h"])) // 2 * 2
                    vf = (f"crop={cw}:{ch}:{cx}:{cy},scale={W}:{H}:flags=lanczos,"
                          f"setsar=1,{proj['grade']}")
                before, after = ffseek.video_seek_args(shot["src_in"], n / fps + 0.5)
                job = dict(ff=ff, src=proj["src"], before=before, after=after, n=n, fps=fps,
                           U=[x0, y0, Uw, Uh], out_wh=[ow, oh], vf=vf, enc=enc,
                           denoise=proj.get("upscale_denoise", DENOISE))
                key = hashlib.sha1(json.dumps(job, sort_keys=True).encode()).hexdigest()[:16]
                cf = cache / f"{key}.mp4"
                if not cf.exists():
                    job["dst"] = str(cache / f"{key}.tmp.mp4")
                    process_shot(job, model=model, device=device)
                    Path(job["dst"]).replace(cf)
                shutil.copyfile(cf, dst)
                msg = (f"shot {shot['i']} {shot.get('beat', '')} zoom {zoom:.2f}x  "
                       f"src crop {Uw}x{Uh}@{x0},{y0} -> AI x4 -> {ow}x{oh}  cache {key}")
                log.append(msg)
                print("[up]", msg, flush=True)
                got = K.count_frames(ffprobe, dst)
                if got != n:
                    sys.exit(f"[up] shot {shot['i']}: {got} frames, expected {n}")
                files.append(dst)
                continue
        sub = dict(proj)
        sub["shots"] = [shot]
        sw = work / "single"
        sw.mkdir(exist_ok=True)
        plain(sub, ff, ffprobe, sw)
        shutil.copyfile(sw / "seg" / f"s{shot['i']:02d}.mp4", dst)
        if shot["kind"] == "clip":
            log.append(f"shot {shot['i']} {shot.get('beat', '')} untouched (zoom at or under {min_zoom}x)")
        files.append(dst)

    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in files), encoding="utf-8")
    base = work / "base.mp4"
    K.run([ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", str(lst), "-c", "copy", str(base)])
    (work / "upscale_log.txt").write_text("\n".join(log), encoding="utf-8")
    return base


def audio_stream_hash(ff: str, path) -> str:
    """MD5 of the first audio stream, bitstream copy (no decode)."""
    p = subprocess.run(
        [ff, "-v", "error", "-i", str(path), "-map", "0:a:0", "-c", "copy",
         "-f", "hash", "-hash", "md5", "-"],
        capture_output=True, text=True,
    )
    text = (p.stdout or "") + (p.stderr or "")
    for line in text.splitlines():
        line = line.strip()
        if "MD5=" in line.upper() or line.upper().startswith("MD5="):
            return line.split("=", 1)[-1].strip().lower()
    raise RuntimeError(f"ffmpeg hash failed for {path}: {text.strip() or p.returncode}")


def verify_audio_copy(ff: str, staged, final, log=print) -> bool:
    """Confirm the muxed file's audio is a bitstream copy of the staged audio. WARN only."""
    try:
        a = audio_stream_hash(ff, staged)
        b = audio_stream_hash(ff, final)
    except Exception as exc:  # noqa: BLE001
        log(f"[up] WARN audio hash could not be computed: {exc}")
        return False
    if a == b:
        log(f"[up] audio hash OK MD5={a}")
        return True
    log(f"[up] WARN audio hash mismatch staged={a} final={b} (audio must be stream-copied)")
    return False


def cmd_fetch_upscale(a) -> None:
    dest = fetch_weights()
    print(f"[up] weights in {dest}")
    ok, why = available()
    print(f"[up] extras: {why}" if not ok else f"[up] ready ({pick_device()})")


if __name__ == "__main__":
    # Worker: `python upscale.py --shot job.json`  or  `python upscale.py --fetch`
    argv = sys.argv[1:]
    if argv[:1] == ["--fetch"]:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        fetch_weights()
    elif argv[:1] == ["--shot"] and len(argv) >= 2:
        job = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        process_shot(job)
    else:
        sys.exit("usage: upscale.py --fetch | --shot JOB.json")

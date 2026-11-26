"""caption_fit.py -- solve + verify caption type placement for the Rollup cold open.

**Seated / podcast framing is the default. Standing / DJI is the exception.** (rule 048)

    seated (default)   one speaker, zoomed, type on the OPPOSITE side of the face.
                       The signed-off reference (Lior Messika B2) is Playfair 100 / Anton 190
                       centred at cy=540. That is safe because the type never overlaps the
                       visible face horizontally -- it does not need reducing.
    standing (exception) locked two-shot (Raoul Pal), both people visible and drifting, type on
                       a dark side. Here the type CAN cross a chin, so keep it low.

`caption_mode` on the project selects the contract:

    "seated"    (default) -- sanity-check only: nothing may overlap a visible face, but the
                             size/height are whatever the design already uses.
    "standing"             -- additionally require the block to clear every visible face and
                             sit inside the bottom safe rim.

Solves the numbers the fixed `layout()` needs:

    caption_size_playfair, caption_size_anton, maxw, caption_bottom

Why this is exact and fast
--------------------------
It does not re-implement the type engine. It renders the project's OWN overlay for the first
frame of every caption page (~16 frames, a couple of seconds) and reads the real ink boxes back
from the PNGs. The clearance it reports is what the finished video does -- no full render, no
guess-then-check loop.

Two traps that cost real time before this existed, both handled here:

* **Faces must be measured on the FRAMED picture** (`<out>/work/base.mp4`), never on `--src`.
  A crop/zoom moves every head, so source-space chins are meaningless for caption placement.
* **Every caption page must be probed**, not just the densest. A 2-row Anton page can be shorter
  than a 3-row Playfair page; one probe per shot under-reports the worst case.

Usage
-----
  # once per episode (cached; ~30-60 s, GPU decode):
  python caption_fit.py heads  --project P.json --kit KIT --out heads.json

  # seated (default): check the design as-is
  python caption_fit.py solve  --project P.framed.json --words W.json --kit KIT \\
                               --heads heads.json --scratch S

  # standing (exception): keep the type low and clear of both people
  python caption_fit.py solve  --project P.framed.json --words W.json --kit KIT \\
                               --heads heads.json --scratch S --mode standing \\
                               --write P.fitted.json

  # confirm final numbers, no render:
  python caption_fit.py verify --project P.fitted.json --words W.json --kit KIT \\
                               --heads heads.json --scratch S
"""
from __future__ import annotations

import argparse
import copy
import json
import shutil
import sys
from pathlib import Path

# seated is the default; standing is the exception
DEFAULT_MODE = "seated"

# seated design sizes, mirroring the signed-off Lior B2 reference
SEATED_SIZES = [(100, 190), (96, 180), (92, 176), (88, 170)]
# standing needs smaller type so it can sit under the chins
STANDING_SIZES = [(80, 168), (78, 164), (76, 160), (74, 156), (72, 152),
                  (70, 148), (68, 144), (66, 140)]

DEFAULT_CAPS = (1140, 1200)
SEATED_BOTTOMS = (0,)
STANDING_BOTTOMS = (1060, 1064)


# ---------------------------------------------------------------------------------------------
# heads
# ---------------------------------------------------------------------------------------------


def cmd_heads(a) -> None:
    """Cache every subject's face box per shot, measured on the FRAMED picture.

    Two framing contracts must both work (rule 048):

      * **seated / podcast** -- one speaker, zoomed, type on the OPPOSITE side. The signed-off
        reference (Lior Messika B2) uses Playfair 100 / Anton 190 centred at cy=540, and it is
        safe because the type never overlaps the visible face horizontally. Clearing "the
        deepest chin" blindly would force that design to a much smaller size for no reason.
      * **standing / DJI walk-and-talk** -- locked two-shot, type on a dark side, both people
        visible and drifting. Here the type genuinely can cross a chin.

    So this stores the boxes and lets `solve` decide overlap per page (see `_collisions`),
    instead of reducing each shot to a single "chin" number.
    """
    import subprocess

    import cv2
    import numpy as np

    proj = json.loads(Path(a.project).read_text(encoding="utf-8"))
    W, H = proj["width"], proj["height"]
    model = Path(a.kit) / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
    if not model.exists():
        sys.exit(f"[heads] YuNet model not found at {model}")
    det = cv2.FaceDetectorYN.create(str(model), "", (W, H), 0.6, 0.3, 5000)

    base = Path(a.video) if a.video else Path(a.scratch) / "base.mp4"
    if not base.exists():
        sys.exit(f"[heads] no framed picture at {base}\n"
                 "        run `introkit build` once so work/base.mp4 exists, or pass --video.\n"
                 "        Measuring --src would be WRONG: the render is cropped and zoomed.")
    print(f"[heads] measuring on {base.name} (framed picture, same canvas as the type)")

    dec = ["ffmpeg", "-v", "error"]
    if a.device and a.device != "cpu":
        dec += ["-hwaccel", a.device]
    dec += ["-i", str(base), "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]
    proc = subprocess.Popen(dec, stdout=subprocess.PIPE)

    spans, f = [], 0
    for s in proj["shots"]:
        spans.append((f, f + s["frames"], s))
        f += s["frames"]

    FB = W * H * 3
    # per shot: the union of each tracked face (index 0 = largest) over time, so a drifting
    # head is covered at both extremes instead of one lucky frame
    out, k = {}, 0
    while True:
        buf = proc.stdout.read(FB)
        if len(buf) < FB:
            break
        hit = [q for q in spans if q[0] <= k < q[1]]
        if hit and hit[0][2].get("kind") == "clip" and (k - hit[0][0]) % a.step == 0:
            s = hit[0][2]
            fr = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
            _, faces = det.detect(fr)
            keep = []
            for fc in ([] if faces is None else faces):
                x, y, w, h = fc[:4]
                if w < a.min_face or not (0.50 <= w / max(h, 1) <= 1.10):
                    continue
                if x <= 8 or x + w >= W - 8:        # a passer-by at the frame edge
                    continue
                keep.append((w * h, [int(x), int(y), int(w), int(h)]))
            keep.sort(reverse=True)
            rec = out.setdefault(s["i"], dict(faces=[], matte=s.get("matte")))
            for idx, (_, box) in enumerate(keep[:2]):
                while len(rec["faces"]) <= idx:
                    rec["faces"].append(dict(x0=W, y0=H, x1=0, y1=0))
                b = rec["faces"][idx]
                b["x0"] = min(b["x0"], box[0])
                b["y0"] = min(b["y0"], box[1])
                b["x1"] = max(b["x1"], box[0] + box[2])
                b["y1"] = max(b["y1"], box[1] + box[3])
        k += 1
    proc.wait()

    for rec in out.values():
        rec["faces"] = [b for b in rec["faces"] if b["x1"] > b["x0"]]
        for b in rec["faces"]:
            b["chin"] = b["y1"]
    data = dict(source=str(base), width=W, height=H, step=a.step, shots=out)
    Path(a.out).write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"[heads] {len(out)} shots -> {a.out}")
    for i, v in sorted(out.items()):
        fs = " ".join(f"[{b['x0']}-{b['x1']} chin {b['chin']}]" for b in v["faces"])
        print(f"  shot {i}: {len(v['faces'])} face(s) {fs}"
              f"{'  (matte)' if v.get('matte') else ''}")


# ---------------------------------------------------------------------------------------------
# shared: exact ink geometry from the engine's overlay
# ---------------------------------------------------------------------------------------------


def _engine(kit: Path):
    sys.path.insert(0, str(Path(kit) / "scripts"))
    import introkit  # noqa: PLC0415
    return introkit


def _ink_boxes(ovl: Path, proj: dict) -> dict:
    """Union of bright type pixels per shot. The scrim is opaque but black, so thresholding on
    alpha>=245 AND luma>=110 isolates the letters."""
    import numpy as np
    from PIL import Image

    spans, f = [], 0
    for s in proj["shots"]:
        spans.append((f, f + s["frames"], s["i"]))
        f += s["frames"]
    per = {}
    for png in sorted(ovl.glob("f_*.png")):
        k = int(png.stem.split("_")[-1])
        sid = [q[2] for q in spans if q[0] <= k < q[1]][0]
        im = Image.open(png)
        al = np.asarray(im.getchannel("A")).astype(np.int16)
        lum = np.asarray(im.convert("RGB")).astype(np.int16).max(axis=2)
        ys, xs = np.nonzero((al >= 245) & (lum >= 110))
        if len(xs):
            per.setdefault(sid, []).append((int(xs.min()), int(ys.min()),
                                            int(xs.max()), int(ys.max())))
    return {k: (min(v[0] for v in vs), min(v[1] for v in vs),
                max(v[2] for v in vs), max(v[3] for v in vs)) for k, vs in per.items()}


def geometry(proj: dict, words: dict, kit: Path, scratch: Path) -> dict:
    """Exact ink bbox per shot, from one overlay frame per caption page.

    Cheap by construction: `_render_range` is pinned to a single frame per page, so a project
    with 16 pages needs 16 overlay frames, not the whole intro.
    """
    introkit = _engine(kit)
    work = Path(scratch)
    work.mkdir(parents=True, exist_ok=True)
    ovl = work / "overlay"

    picks, f = {}, 0
    for s in proj["shots"]:
        if s.get("kind") == "clip":
            wl = words[str(s["i"])]
            firsts = {}
            for ln in s.get("lines", []):
                pg = ln.get("page", 0)
                firsts[pg] = min(firsts.get(pg, 10 ** 9), min(ln["words"]))
            for pg, wi in firsts.items():
                picks[(s["i"], pg)] = f + int(round(wl[wi]["t"] * proj["fps"])) + 6
        f += s["frames"]
    if not picks:
        return {}

    boxes = {}
    for (sid, _pg), frame in picks.items():
        p = copy.deepcopy(proj)
        p.pop("_kit_work", None)
        if ovl.exists():
            shutil.rmtree(ovl)
        p["_render_range"] = [frame, frame]
        introkit.stage_overlay(p, work, words)
        for sid2, box in _ink_boxes(ovl, proj).items():
            if sid2 in boxes:
                b = boxes[sid2]
                boxes[sid2] = (min(b[0], box[0]), min(b[1], box[1]),
                               max(b[2], box[2]), max(b[3], box[3]))
            else:
                boxes[sid2] = box
    shutil.rmtree(ovl, ignore_errors=True)
    return boxes


def _with_sizes(proj: dict, pf: int, an: int, cap: int, bottom: int) -> dict:
    p = copy.deepcopy(proj)
    p["caption_size_playfair"] = pf
    p["caption_size_anton"] = an
    if bottom:
        p["caption_bottom"] = bottom
    else:
        p.pop("caption_bottom", None)        # seated default: keep the design's own centring
    for s in p["shots"]:
        if s.get("kind") == "clip":
            s["maxw"] = cap
            for ln in s.get("lines", []):
                ln["size"] = float(pf if ln["kind"] == "playfair" else an)
    return p


def _visible_faces(rec: dict, side: str, W: int) -> list:
    """Faces that can actually collide with type on `side`, after the shot's matte is applied.

    Seated framing writes `shot["matte"]` = {x0, x1} to hide the other host behind an opaque
    band (rule 048). A face lying fully inside that band is not on screen, so it must not
    constrain the type -- otherwise seated shots get needlessly shrunk.
    """
    faces = list(rec.get("faces") or [])
    m = rec.get("matte")
    if m:
        x0, x1 = float(m.get("x0", 0)), float(m.get("x1", W))
        faces = [f for f in faces if not (f["x1"] <= x0 + 2 or f["x0"] >= x1 - 2)]
    return faces

def _collisions(boxes: dict, heads: dict, proj: dict) -> list:
    """Every place the type overlaps a visible face (both axes), as (shot, overlap_px)."""
    W, H = proj["width"], proj["height"]
    out = []
    for s in proj["shots"]:
        if s.get("kind") != "clip":
            continue
        box = boxes.get(s["i"])
        if not box:
            continue
        tx0, ty0, tx1, ty1 = box
        for f in _visible_faces(heads[str(s["i"])], s.get("side", "left"), W):
            ox = min(tx1, f["x1"]) - max(tx0, f["x0"])
            oy = min(ty1, f["y1"]) - max(ty0, f["y0"])
            if ox > 0 and oy > 0:
                out.append((s["i"], int(ox), int(oy)))
    return out


def _score(boxes: dict, heads: dict, proj: dict, a) -> tuple:
    """(clearance, rim, widest, ok) under the shot's framing contract.

    seated (default): the type sits opposite the face, so the only hard rule is that nothing
      overlaps a visible face. Size stays the design's own (Lior's 100/190 works because the
      face is elsewhere); no bottom anchor is imposed.
    standing (exception): type is on the same canvas as two drifting people, so it must stay
      clear of every visible face AND inside the bottom safe rim.
    """
    H, W = proj["height"], proj["width"]
    mode = getattr(a, "mode", DEFAULT_MODE) or DEFAULT_MODE
    cols = _collisions(boxes, heads, proj)
    clear = -max(oy for _, _, oy in cols) if cols else 10 ** 6
    rim = min(H - 1 - boxes[i][3] for i in boxes)
    wide = max(boxes[i][2] - boxes[i][0] for i in boxes)
    in_frame = all(b[0] >= 0 and b[2] < W and b[3] <= H - 1 for b in boxes.values())

    if mode == "standing":
        ok = (not cols) and rim >= a.min_rim and wide <= a.max_width and in_frame
    else:                                   # seated: default contract
        ok = (not cols) and wide <= a.max_width and in_frame
        if rim < 0:                         # still must not run off the frame
            ok = False
    return clear, rim, wide, ok


# ---------------------------------------------------------------------------------------------
# solve
# ---------------------------------------------------------------------------------------------


def cmd_solve(a) -> None:
    proj = json.loads(Path(a.project).read_text(encoding="utf-8"))
    words = json.loads(Path(a.words).read_text(encoding="utf-8"))
    heads = json.loads(Path(a.heads).read_text(encoding="utf-8"))["shots"]
    kit, scratch = Path(a.kit), Path(a.scratch)

    mode = a.mode or proj.get("caption_mode", DEFAULT_MODE)
    a.mode = mode
    if mode == "standing":
        sizes = [tuple(x) for x in a.candidates] if a.candidates else STANDING_SIZES
        bottoms = a.bottoms or list(STANDING_BOTTOMS)
    else:
        sizes = [tuple(x) for x in a.candidates] if a.candidates else SEATED_SIZES
        bottoms = a.bottoms or list(SEATED_BOTTOMS)
    caps = a.caps or list(DEFAULT_CAPS)

    print(f"[solve] mode={mode}"
          f"{' (default: seated/podcast)' if mode == 'seated' else ' (exception: standing DJI)'}")
    print(f"{'pf':>4} {'an':>4} {'cap':>5} {'bot':>5} {'overlap':>10} {'rim':>6} {'wide':>6}  verdict")
    best = None
    for pf, an in sizes:
        for cap in caps:
            for bot in bottoms:
                boxes = geometry(_with_sizes(proj, pf, an, cap, bot), words, kit, scratch)
                if not boxes:
                    print(f"{pf:>4} {an:>4} {cap:>5} {bot:>5}  no type found")
                    continue
                clear, rim, wide, ok = _score(boxes, heads, proj, a)
                shown = "no overlap" if clear > 10 ** 5 else str(clear)
                print(f"{pf:>4} {an:>4} {cap:>5} {bot:>5} {shown:>10} {rim:>6} {wide:>6}  "
                      f"{'OK' if ok else '--'}")
                if ok:
                    s = pf + 0.55 * an
                    if best is None or s > best[0]:
                        best = (s, pf, an, cap, bot, clear, rim, wide)

    if not best:
        sys.exit("[solve] nothing fits -- widen --caps/--bottoms, pass --candidates, "
                 "or check that base.mp4 is the framed picture")
    _, pf, an, cap, bot, clear, rim, wide = best
    print(f"\n[solve] BEST playfair={pf} anton={an} maxw={cap} "
          f"caption_bottom={bot if bot else 'unset (centred, seated default)'}")
    print(f"        {'no face overlap' if clear > 10 ** 5 else f'overlap {clear}px'}"
          f" | {rim}px bottom rim | widest {wide}px")
    if a.write:
        out = _with_sizes(proj, pf, an, cap, bot)
        out["caption_mode"] = mode
        if not bot:
            out.pop("caption_bottom", None)
        out.pop("_kit_work", None)
        Path(a.write).write_text(json.dumps(out, indent=1), encoding="utf-8")
        print(f"        wrote {a.write}")


# ---------------------------------------------------------------------------------------------
# verify
# ---------------------------------------------------------------------------------------------


def cmd_verify(a) -> None:
    proj = json.loads(Path(a.project).read_text(encoding="utf-8"))
    words = json.loads(Path(a.words).read_text(encoding="utf-8"))
    heads = json.loads(Path(a.heads).read_text(encoding="utf-8"))["shots"]
    a.mode = a.mode or proj.get("caption_mode", DEFAULT_MODE)
    boxes = geometry(proj, words, Path(a.kit), Path(a.scratch))
    cols = {(c[0]): c for c in _collisions(boxes, heads, proj)}

    print(f"[verify] mode={a.mode}")
    print(f"{'shot':>4} {'x0':>5} {'x1':>5} {'y_top':>6} {'y_bot':>6} {'rim':>5} {'overlap':>9}")
    ok = True
    for sid in sorted(boxes):
        x0, y0, x1, y1 = boxes[sid]
        rim = proj["height"] - 1 - y1
        col = cols.get(sid)
        flags = ""
        if col:
            flags += f" FACE({col[1]}x{col[2]})"
            ok = False
        if a.mode == "standing" and rim < a.min_rim:
            flags += " RIM"
            ok = False
        if rim < 0:
            flags += " OFF-FRAME"
            ok = False
        print(f"{sid:>4} {x0:>5} {x1:>5} {y0:>6} {y1:>6} {rim:>5} "
              f"{'--' if not col else f'{col[1]}x{col[2]}':>9}{flags}")
    print("\nVERDICT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    h = sub.add_parser("heads", help="cache the deepest chin per shot (measure the framed picture)")
    h.add_argument("--project", required=True)
    h.add_argument("--kit", required=True)
    h.add_argument("--out", default="heads.json")
    h.add_argument("--video", help="framed picture (default <scratch>/base.mp4)")
    h.add_argument("--scratch", default="scratch")
    h.add_argument("--device", default="cuda")
    h.add_argument("--step", type=int, default=3)
    h.add_argument("--min-face", type=int, default=110)

    for name, fn in (("solve", cmd_solve), ("verify", cmd_verify)):
        s = sub.add_parser(name)
        s.add_argument("--project", required=True)
        s.add_argument("--words", required=True)
        s.add_argument("--kit", required=True)
        s.add_argument("--heads", required=True)
        s.add_argument("--scratch", required=True)
        s.add_argument("--mode", choices=("seated", "standing"), default=None,
                       help="seated (default) or standing (exception); "
                            "defaults to the project's caption_mode, else seated")
        s.add_argument("--min-clear", type=int, default=16)
        s.add_argument("--min-rim", type=int, default=12)
        s.add_argument("--max-width", type=int, default=1300)
        if name == "solve":
            s.add_argument("--write", help="write the solved project here")
            s.add_argument("--candidates", type=int, nargs=2, action="append",
                           metavar=("PLAYFAIR", "ANTON"))
            s.add_argument("--caps", type=int, nargs="+")
            s.add_argument("--bottoms", type=int, nargs="+")

    a = ap.parse_args(argv)
    {"heads": cmd_heads, "solve": cmd_solve, "verify": cmd_verify}[a.cmd](a)


if __name__ == "__main__":
    main()

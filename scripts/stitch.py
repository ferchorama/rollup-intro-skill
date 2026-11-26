#!/usr/bin/env python3
"""Stitch a hook, a subscribe bumper and the full episode into one MP4.

Portable on purpose: **ffmpeg and ffprobe plus the standard library, nothing else.** No app, no
license, no GPU, no Python packages. Any machine that has run `introkit.py setup` (or that simply has
ffmpeg on PATH) can make a delivery from three files.

    python scripts/stitch.py HOOK SUBSCRIBE SEGMENT -o OUT.mp4
        [--overlap FRAMES] [--hook-overlap FRAMES] [--strategy mix|drop]
        [--level segment|-14|off] [--target-tp -1.0]
        [--no-duck] [--dynamics] [--audio-bitrate 192k]
        [--crf 14] [--preset medium]
        [--work DIR] [--measure] [--dry-run] [--no-verify]
        [--ffmpeg PATH] [--ffprobe PATH]

## What it makes

Three cuts of picture, copied, joined through Annex B; and one continuous sound built from the same
three files on a filtergraph. The seams do not show black and nothing is missing:

* the hook's last frames are its *measured* black tail. The subscribe clip's picture starts where that
  black starts, and the hook's sound plays on underneath to its real end (ducked under the clip);
* the subscribe clip's last frames are its *measured* black tail. The episode's picture starts where
  that black starts, and the clip's sound runs on underneath the episode's opening (ducked under the
  episode's speech).

Both overlaps are measured from the files, never assumed: the bumper's tail is a property of that
clip (23 to 40 frames across the eight that ship) and the hook's is a property of the intro that was
built.

## The volume step (why this script has it)

A stitched delivery inherits three different masterings. The hook was built to the intro's own target
(-14 LUFS); the episode is whatever the editor exported; the subscribe clip is usually *much* quieter
than either — the raw masters measure about -18.9 LUFS against an episode's -13.4, which is audible
as a hole in the middle of the delivery.

`--level segment` (the default) measures all three and gain-matches the hook and the subscribe clip to
the **episode's own integrated loudness**, never past a true peak of `--target-tp` (default -1.0).
It is a gain, applied inside the mix filtergraph — one encode, no extra generation, and it can only
scale: nothing compresses, levels or re-times the episode. `--level off` leaves every file exactly as
it is; `--level -14` reproduces the old fixed target.

## The sound of the episode is never changed

`--dynamics` is OFF by default, and the skill's rule 036 says it stays off: the interview audio must
come out of the stitch exactly as it went in (apart from the one AAC encode the mix needs). The
dynamics chain exists only because the operator's Premiere timeline had one; pass `--dynamics` only
when a delivery is explicitly meant to reproduce it.

## What it reports

The plan before anything is written (frame counts, overlaps, gains, which pieces are copied), then a
measured verification of the file it wrote: frames against the sum of the pieces, picture and sound
durations against each other, the timescale, frame spacing, and decoded-frame checksums of sampled
frames against the pieces they claim to come from. A check that cannot be run is reported as not
checked and never as a pass.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

#: The timescale every piece is normalised to before the join.
#:
#: Three sources with three different video track timescales concatenate *without complaint* and
#: produce a file whose picture plays at the wrong speed while the sound is fine: the concat demuxer
#: adopts the first file's timescale and reinterprets every later file's raw timestamps in it. The
#: intro ships at 1/15360 and the episode at 1/30000, so this is not hypothetical.
TARGET_TIMESCALE = 30000

#: Luma at or below which a frame counts as black. Matches the intent of a "black" frame in an
#: exported MP4: true black plus the residue of the encoder, not a dark shot.
BLACK_LUMA = 20.0

#: How many frames a black scan looks at, from each end. A tail is never longer than this in these
#: files, and the scan is bounded so a long episode costs the same as a short one.
BLACK_SCAN_LIMIT = 90

#: The final sound's bitrate. 192k is what the delivered episodes have used and is transparent for
#: speech at this length; `--audio-bitrate` raises it if a delivery needs more.
AUDIO_BITRATE = "192k"

#: Quality for a piece that has to be re-encoded. 14 is visually lossless on 1080p30 for one
#: generation, which is all a join should ever cost.
DEFAULT_CRF = 14
DEFAULT_PRESET = "medium"

#: The compressor that ducks the subscribe clip's tail under the episode's speech: threshold about
#: -30 dBFS, RMS detection, ratio 3, so speech at a normal level pulls the tail down ~8-10 dB while
#: the music is still heard.
DUCK = "sidechaincompress=threshold=0.03:ratio=3:attack=10:release=300:detection=rms:makeup=1"

#: The compressor that ducks the hook's fading tail under the subscribe clip's voice and music.
HOOK_DUCK = "sidechaincompress=threshold=0.02:ratio=4:attack=5:release=250:detection=rms:makeup=1"

#: The operator's Premiere track effect, reproduced exactly. Ratio 1 means the compressor performs no
#: gain reduction - the block is a flat +12 dB fader into a -6 dBFS limiter - and it is kept as it was
#: set rather than "fixed". `level=0` is not optional: alimiter's automatic level adds about +6 dB.
DYNAMICS = (
    "acompressor=threshold=-20dB:ratio=1:attack=1:release=50:makeup=3.9812,"
    "alimiter=limit=0.5012:release=50:level=0"
)


class StitchError(RuntimeError):
    """A refusal or a failure that the operator can act on."""


# ---------------------------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------------------------

def _which(name: str, override: str | None = None) -> str:
    """--ffmpeg/--ffprobe > STITCH_FFMPEG > INTROKIT_FFMPEG > PATH > the skill's tools/ dir."""
    if override:
        return override
    for variable in (f"STITCH_{name.upper()}", f"INTROKIT_{name.upper()}"):
        value = os.environ.get(variable)
        if value:
            return value
    found = shutil.which(name)
    if found:
        return found
    # The skill's own environment, the same one `introkit.py setup` provisions.
    default_home = Path(__file__).resolve().parent.parent / ".rollup-intro-env"
    home = Path(os.environ.get("ROLLUP_INTRO_HOME") or default_home).expanduser()
    for candidate in (home / "tools" / "ffmpeg" / "bin").glob(name + "*"):
        if candidate.stem == name:
            return str(candidate)
    raise StitchError(
        f"{name} not found. Put it on PATH, pass --{name}, or run `python scripts/introkit.py setup`."
    )


def run(cmd: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    """Run one command. A failure carries the command and the end of its own diagnostics."""
    args = [str(part) for part in cmd]
    process = subprocess.run(
        args,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdin=subprocess.DEVNULL if capture else None,
    )
    if process.returncode != 0:
        tail = (process.stderr or "").strip().splitlines()[-12:]
        raise StitchError("command failed:\n  " + " ".join(args) + "\n  " + "\n  ".join(tail))
    return process


def _json(ffprobe: str, args: list[str]) -> dict:
    return json.loads(run([ffprobe, *args]).stdout or "{}")


# ---------------------------------------------------------------------------------------------
# What a file is
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class AudioStream:
    codec: str
    sample_rate: int
    channels: int

    def describe(self) -> str:
        return f"{self.codec} {self.sample_rate} Hz, {self.channels}ch"


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    codec: str
    profile: str
    width: int
    height: int
    pix_fmt: str
    rate: Fraction
    frames: int
    duration: float
    timebase: Fraction
    audio: AudioStream | None

    def seconds_of(self, frame: int) -> float:
        """Where a frame sits on the rate grid. One spelling of the division, so it cannot drift."""
        return float(Fraction(frame, 1) / self.rate)

    def frame_of(self, seconds: float) -> int:
        return int(round(seconds * float(self.rate)))

    def summary(self) -> str:
        audio = "no audio" if self.audio is None else self.audio.describe()
        return (
            f"{self.width}x{self.height} {self.codec} {self.profile} at {float(self.rate):g} fps, "
            f"{self.frames} frames, {self.duration:.3f}s, {audio}"
        )


def _fraction(value: str | None) -> Fraction:
    """`"30/1"` as an exact rational. Never a float: 29.97 is not 29.97."""
    if not value:
        return Fraction(0)
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def probe(ffprobe: str, path: Path) -> MediaInfo:
    """Read a file's streams. The one place a source is measured."""
    result = _json(ffprobe, ["-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)])
    streams = result.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise StitchError(f"{path.name} has no video stream, so there is nothing to stitch")

    rate = _fraction(video.get("r_frame_rate")) or _fraction(video.get("avg_frame_rate"))
    if rate == 0:
        raise StitchError(f"{path.name} does not declare a frame rate")

    duration = float(result.get("format", {}).get("duration") or 0.0)
    if duration <= 0:
        duration = float(video.get("duration") or 0.0)

    raw_frames = video.get("nb_frames")
    frames = int(raw_frames) if raw_frames and str(raw_frames).isdigit() else 0
    if frames <= 0 and duration > 0:
        # A count the header omits is still implied by the duration on the rate grid. It is a derived
        # number, and the verifier re-measures it with count_frames.
        frames = int(round(duration * float(rate)))

    return MediaInfo(
        path=path,
        codec=str(video.get("codec_name") or "?"),
        profile=str(video.get("profile") or "?"),
        width=int(video.get("width") or 0),
        height=int(video.get("height") or 0),
        pix_fmt=str(video.get("pix_fmt") or "?"),
        rate=rate,
        frames=frames,
        duration=duration,
        timebase=_fraction(video.get("time_base")),
        audio=(
            None
            if audio is None
            else AudioStream(
                codec=str(audio.get("codec_name") or "?"),
                sample_rate=int(audio.get("sample_rate") or 0),
                channels=int(audio.get("channels") or 0),
            )
        ),
    )


def keyframes(ffprobe: str, info: MediaInfo) -> list[int]:
    """Every keyframe's frame number, ascending.

    The frame number comes from the packet's presentation time, not from its position in the packet
    list: packets are stored in decode order, so with B-frames the two differ by the reorder depth,
    and a copy started one frame early is a copy started mid-GOP.
    """
    result = _json(ffprobe, [
        "-v", "error", "-select_streams", "v:0",
        "-show_entries", "packet=pts_time,flags", "-of", "json", str(info.path),
    ])
    frames: set[int] = set()
    for packet in result.get("packets", []):
        if "K" not in str(packet.get("flags") or ""):
            continue
        raw = packet.get("pts_time")
        if raw is None or raw == "N/A":
            continue
        frames.add(info.frame_of(float(raw)))
    return sorted(frames)


def keyframe_at_or_before(keys: list[int], frame: int) -> int:
    """The latest keyframe at or before `frame`. A stream copy can only begin here."""
    earlier = [key for key in keys if key <= frame]
    return earlier[-1] if earlier else 0


def _luma(ffmpeg: str, path: Path, count: int, seek: list[str]) -> list[float]:
    """Mean luma for `count` frames, read back from ffmpeg's own log.

    `seek` is an *input* option list (that is where `-sseof` belongs). `metadata=print` writes to the
    log rather than a file on purpose: a `file=` argument is a path interpolated into a filtergraph,
    and a Windows path with a colon in it is a syntax error rather than a filename.
    """
    process = run([
        ffmpeg, "-v", "info", "-hide_banner", *seek, "-i", str(path),
        "-map", "0:v:0", "-vf", "signalstats,metadata=print",
        "-frames:v", str(count), "-f", "null", "-",
    ])
    values = []
    for line in (process.stderr or "").splitlines():
        marker = "lavfi.signalstats.YAVG="
        if marker in line:
            try:
                values.append(float(line.split(marker, 1)[1].strip()))
            except ValueError:
                pass
    return values


def black_head_frames(ffmpeg: str, info: MediaInfo) -> int:
    """How many frames at the start are black. Measured, not inferred from an interval."""
    values = _luma(ffmpeg, info.path, min(BLACK_SCAN_LIMIT, info.frames), [])
    leading = 0
    for value in values:
        if value > BLACK_LUMA:
            break
        leading += 1
    return leading


def black_tail_frames(ffmpeg: str, info: MediaInfo) -> int:
    """How many frames at the end are black.

    `-sseof` seeks from the end, so a long episode costs the same as a short intro. It lands on the
    keyframe at or before the requested time and decodes forward *without discarding*, so the first
    frame out is earlier than asked - by up to a GOP. Reading exactly `count` frames would therefore
    stop short of the real end and undercount the tail, so a second of frames is read as margin and
    the count runs back from the actual end.
    """
    count = min(BLACK_SCAN_LIMIT, info.frames)
    margin = min(info.frames, int(round(float(info.rate))) + 1)
    window = (count + margin) / float(info.rate)
    values = _luma(ffmpeg, info.path, count + margin, ["-sseof", f"-{window:.3f}"])
    tail = values[-count:] if len(values) > count else values
    trailing = 0
    for value in reversed(tail):
        if value > BLACK_LUMA:
            break
        trailing += 1
    return trailing


def count_frames(ffprobe: str, path: Path) -> int:
    """The frames actually in the file, counted rather than trusted."""
    out = run([ffprobe, "-v", "error", "-select_streams", "v:0", "-count_frames",
               "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)]).stdout
    return int(out.strip().splitlines()[0] or 0)


def stream_duration(ffprobe: str, path: Path, stream: str = "v:0") -> float:
    out = run([ffprobe, "-v", "error", "-select_streams", stream,
               "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)]).stdout
    try:
        return float(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0.0


def measure_loudness(ffmpeg: str, path: Path) -> dict:
    """Integrated loudness (LUFS), true peak (dBTP) and LRA, measured on the sound only."""
    process = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-map", "0:a:0",
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
    )
    log = process.stderr or ""
    start, end = log.rfind("{"), log.rfind("}")
    if start < 0 or end < start:
        raise StitchError(f"could not measure the loudness of {path.name}:\n{log[-1500:]}")
    try:
        return json.loads(log[start:end + 1])
    except json.JSONDecodeError as error:
        raise StitchError(f"could not read the loudness of {path.name}: {error}") from error


# ---------------------------------------------------------------------------------------------
# Shapes and conforming
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Shape:
    width: int
    height: int
    rate: Fraction
    sample_rate: int
    channels: int

    def key(self) -> str:
        return (f"{self.width}x{self.height}@{self.rate.numerator}-{self.rate.denominator}"
                f"_{self.sample_rate}_{self.channels}ch")

    def describe(self) -> str:
        return (f"{self.width}x{self.height} H.264 yuv420p at {float(self.rate):g} fps, "
                f"AAC {self.sample_rate} Hz {self.channels}ch")


def _picture_can_be_copied(info: MediaInfo) -> bool:
    return (info.codec == "h264" and info.pix_fmt == "yuv420p"
            and info.width % 2 == 0 and info.height % 2 == 0 and info.rate > 0)


def target_shape(segment: MediaInfo) -> Shape:
    """The shape the delivery is joined at: the episode's own, when its picture can be copied."""
    audio = segment.audio
    sample_rate, channels = (audio.sample_rate, audio.channels) if audio else (48000, 2)
    if _picture_can_be_copied(segment):
        return Shape(segment.width, segment.height, segment.rate, sample_rate, channels)
    return Shape(1920, 1080, Fraction(30), sample_rate, channels)


def _picture_fits(info: MediaInfo, shape: Shape) -> bool:
    return (info.codec == "h264" and info.pix_fmt == "yuv420p"
            and info.width == shape.width and info.height == shape.height and info.rate == shape.rate)


def _sound_fits(info: MediaInfo, shape: Shape) -> bool:
    return (info.audio is not None and info.audio.sample_rate == shape.sample_rate
            and info.audio.channels == shape.channels)


def _video_filters(info: MediaInfo, shape: Shape) -> list[str]:
    filters: list[str] = []
    if info.rate != shape.rate:
        # Whole frames kept or repeated on the target grid, never blended.
        filters.append(f"fps={shape.rate.numerator}/{shape.rate.denominator}")
    if (info.width, info.height) != (shape.width, shape.height):
        filters.append(
            f"scale={shape.width}:{shape.height}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={shape.width}:{shape.height}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
    filters.append("format=yuv420p")
    return filters


def _audio_args(info: MediaInfo, shape: Shape, silent_input: int | None) -> list[str]:
    """Map and encode the sound to the target layout; silence when the source has none."""
    if info.audio is None:
        layout = "stereo" if shape.channels == 2 else ("mono" if shape.channels == 1 else "stereo")
        return ["-f", "lavfi", "-i", f"anullsrc=r={shape.sample_rate}:cl={layout}",
                "-map", f"{silent_input}:a:0", "-c:a", "aac", "-b:a", "320k", "-shortest"]
    return [
        "-map", "0:a:0", "-af", f"aresample={shape.sample_rate}",
        "-ar", str(shape.sample_rate), "-ac", str(shape.channels),
        "-c:a", "aac", "-b:a", "320k",
    ]


@dataclass(frozen=True)
class Conformed:
    hook: Path
    bumper: Path
    segment: Path
    shape: Shape
    notes: tuple[str, ...] = ()


def _encoder(preset: str, crf: int, gpu: bool) -> list[str]:
    if gpu:
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq", "-rc", "vbr",
                "-cq", str(crf), "-b:v", "0", "-profile:v", "high", "-pix_fmt", "yuv420p"]
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]


def _has_nvenc(ffmpeg: str) -> bool:
    try:
        return "h264_nvenc" in run([ffmpeg, "-hide_banner", "-encoders"]).stdout
    except StitchError:
        return False


def _write(source: MediaInfo, target: Path, video: list[str], audio: list[str]) -> None:
    staging = target.with_name(target.stem + ".part.mp4")
    run([_FFMPEG, "-y", "-v", "error", "-i", str(source.path), *video, *audio,
         "-movflags", "+faststart", str(staging)])
    os.replace(staging, target)


def _reencode(info: MediaInfo, shape: Shape, target: Path, *, preset: str, crf: int) -> None:
    """Picture and sound to the target shape. NVENC first, libx264 when absent or refused."""
    video = ["-map", "0:v:0", "-vf", ",".join(_video_filters(info, shape)),
             *_encoder(preset, crf, _has_nvenc(_FFMPEG)),
             "-video_track_timescale", str(TARGET_TIMESCALE)]
    audio = _audio_args(info, shape, 1)
    try:
        _write(info, target, video, audio)
    except StitchError:
        if not _has_nvenc(_FFMPEG):
            raise
        video = ["-map", "0:v:0", "-vf", ",".join(_video_filters(info, shape)),
                 *_encoder(preset, crf, False),
                 "-video_track_timescale", str(TARGET_TIMESCALE)]
        print("  NVENC refused this encode; falling back to libx264.")
        _write(info, target, video, audio)


def _resound(info: MediaInfo, shape: Shape, target: Path) -> None:
    """The picture copied, only the sound re-encoded."""
    _write(info, target,
           ["-map", "0:v:0", "-c:v", "copy", "-video_track_timescale", str(TARGET_TIMESCALE)],
           _audio_args(info, shape, 1))


def _bumper_is_ready(ffprobe: str, ffmpeg: str, info: MediaInfo, shape: Shape) -> bool:
    """Fits the shape, starts on picture at a keyframe, and its tail cut is a keyframe."""
    if not (_picture_fits(info, shape) and _sound_fits(info, shape)):
        return False
    head = black_head_frames(ffmpeg, info)
    tail = black_tail_frames(ffmpeg, info)
    keys = keyframes(ffprobe, info)
    if head != 0 or not keys or keys[0] != 0:
        return False
    return tail == 0 or (info.frames - tail) in keys


def _prepare_bumper(info: MediaInfo, shape: Shape, target: Path) -> None:
    """Head dropped, tail kept (it is the overlap), an IDR at frame 0 and at the tail cut.

    The cut is measured on the *written* file and the IDR moved there if the retime disagreed with
    the arithmetic - the same loop, for the same reason, as `prepare_bumper.py`.
    """
    head = black_head_frames(_FFMPEG, info)
    tail = black_tail_frames(_FFMPEG, info)
    if head >= info.frames - tail:
        raise StitchError(
            f"the subscribe clip's {head}-frame black head and {tail}-frame black tail leave nothing "
            f"of its {info.frames} frames to show"
        )
    print(f"  preparing the subscribe clip: {info.summary()} -> {shape.describe()}; "
          f"black head {head}, black tail {tail} (kept: it is the overlap)")

    ratio = shape.rate / info.rate
    frames_out = int(round((info.frames - head) * float(ratio)))
    cut = frames_out - int(round(tail * float(ratio)))
    head_seconds = float(Fraction(head, 1) / info.rate)

    def encode(cut_frame: int) -> None:
        video = ["-map", "0:v:0", "-vf", ",".join(_video_filters(info, shape)),
                 "-frames:v", str(frames_out),
                 "-c:v", "libx264", "-preset", "slow", "-crf", str(DEFAULT_CRF), "-pix_fmt", "yuv420p",
                 "-force_key_frames",
                 "expr:" + "eq(n,0)" + (f"+eq(n,{cut_frame})" if 0 < cut_frame < frames_out else ""),
                 "-video_track_timescale", str(TARGET_TIMESCALE)]
        audio = _audio_args(info, shape, 1)
        staging = target.with_name(target.stem + ".part.mp4")
        args = [_FFMPEG, "-y", "-v", "error"]
        if head > 0:
            args += ["-ss", f"{head_seconds:.6f}"]
        run([*args, "-i", str(info.path), *video, *audio, "-movflags", "+faststart", str(staging)])
        os.replace(staging, target)

    for _ in range(3):
        encode(cut)
        written = probe(_FFPROBE, target)
        measured = written.frames - black_tail_frames(_FFMPEG, written)
        if measured == cut or tail == 0:
            break
        cut = measured


def _force_cut(info: MediaInfo, shape: Shape, target: Path, cut: int, *, preset: str, crf: int) -> None:
    """The hook re-encoded frame for frame with IDRs at 0 and at `cut`, so a copy can end there.

    A piece that ends mid-GOP loses the B-frame references the cut removes; forcing an IDR at the cut
    closes the GOP before it. `passthrough` keeps every source frame and timestamp, so frame `n`
    stays frame `n`.
    """
    print(f"  the hook's cut at frame {cut} is not a keyframe; re-encoding the hook with an IDR there")
    _write(info, target,
           ["-map", "0:v:0", "-fps_mode", "passthrough", "-frames:v", str(info.frames),
            *_encoder(preset, crf, False),
            "-force_key_frames", f"expr:eq(n,0)+eq(n,{cut})",
            "-video_track_timescale", str(TARGET_TIMESCALE)],
           _audio_args(info, shape, 1))


def conform(hook: Path, bumper: Path, segment: Path, work: Path, *, hook_overlap: int | None,
            preset: str, crf: int) -> Conformed:
    """The three inputs as files the join accepts: each the original, or a conformed copy."""
    infos = {role: probe(_FFPROBE, path)
             for role, path in (("hook", hook), ("bumper", bumper), ("segment", segment))}
    shape = target_shape(infos["segment"])
    work.mkdir(parents=True, exist_ok=True)
    notes: list[str] = []
    out: dict[str, Path] = {}

    for role, path in (("hook", hook), ("bumper", bumper), ("segment", segment)):
        info = infos[role]
        if role == "bumper":
            ready = _bumper_is_ready(_FFPROBE, _FFMPEG, info, shape)
        else:
            ready = _picture_fits(info, shape) and _sound_fits(info, shape)
        if ready:
            out[role] = path
            continue
        target = work / f"{role}.conformed.{shape.key()}.mp4"
        if not target.is_file():
            if role == "bumper":
                _prepare_bumper(info, shape, target)
            elif _picture_fits(info, shape):
                _resound(info, shape, target)
            else:
                _reencode(info, shape, target, preset=preset, crf=crf)
        notes.append(f"the {role} ({info.summary()}) was conformed to {shape.describe()}: {target}")
        out[role] = target

    # The hook overlap cuts the hook's picture at `frames - overlap`; that frame must be a keyframe
    # for the copied hook to end cleanly.
    hook_info = probe(_FFPROBE, out["hook"])
    hook_tail = (black_tail_frames(_FFMPEG, hook_info)
                 if hook_overlap is None else int(hook_overlap))
    if 0 < hook_tail < hook_info.frames:
        cut = hook_info.frames - hook_tail
        if cut not in keyframes(_FFPROBE, hook_info):
            target = work / f"hook.cut{cut}.{shape.key()}.mp4"
            if not target.is_file():
                _force_cut(hook_info, shape, target, cut, preset=preset, crf=crf)
            notes.append(f"the hook was re-encoded with a keyframe at its cut, frame {cut}: {target}")
            out["hook"] = target

    for note in notes:
        print(f"  {note}")
    return Conformed(out["hook"], out["bumper"], out["segment"], shape, tuple(notes))


# ---------------------------------------------------------------------------------------------
# The plan
# ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Piece:
    role: str
    source: Path
    in_frame: int
    out_frame: int
    copied: bool

    @property
    def frames(self) -> int:
        return self.out_frame - self.in_frame


@dataclass
class Plan:
    hook: MediaInfo
    bumper: MediaInfo
    segment: MediaInfo
    pieces: list[Piece]
    shape: Shape
    overlap: int
    hook_overlap: int
    head_black: int
    hook_tail: int
    strategy: str
    duck: bool
    dynamics: bool
    audio_bitrate: str
    hook_gain: float = 0.0
    bumper_gain: float = 0.0
    loudness: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def video_frames(self) -> int:
        return sum(piece.frames for piece in self.pieces)

    @property
    def audio_frames(self) -> int:
        # The sound ends where the picture does, for both strategies - a track that ends anywhere
        # else either runs under a frozen frame or stops before the picture does.
        return self.video_frames

    @property
    def duration(self) -> float:
        return float(Fraction(self.video_frames, 1) / self.hook.rate)


def plan_stitch(hook: MediaInfo, bumper: MediaInfo, segment: MediaInfo, shape: Shape, *,
                overlap: int | None, hook_overlap: int | None, head_black: int, bumper_tail: int,
                hook_tail: int, strategy: str, duck: bool, dynamics: bool,
                audio_bitrate: str) -> Plan:
    """Decide the three cuts. Every number here is measured or refused, never defaulted."""
    if hook.rate != shape.rate or bumper.rate != shape.rate or segment.rate != shape.rate:
        raise StitchError("the conformed inputs are not all on one frame rate; this is a bug in conform()")

    cut_overlap = bumper_tail if overlap is None else int(overlap)
    if cut_overlap < 0:
        raise StitchError("the overlap cannot be negative")
    if cut_overlap >= bumper.frames:
        raise StitchError(
            f"the overlap is {cut_overlap} frames but the subscribe clip is only {bumper.frames} "
            f"frames long, so there would be nothing of it to show"
        )
    if head_black < 0 or head_black >= bumper.frames - cut_overlap:
        raise StitchError(
            f"the subscribe clip's black head is {head_black} frames and its black tail is "
            f"{cut_overlap}, which leaves nothing of a {bumper.frames}-frame clip to play"
        )

    cut_hook = hook_tail if hook_overlap is None else int(hook_overlap)
    if cut_hook < 0:
        raise StitchError("the hook overlap cannot be negative")
    if cut_hook >= hook.frames:
        raise StitchError(
            f"the hook overlap is {cut_hook} frames but the hook is only {hook.frames} frames long"
        )
    if cut_hook > (bumper.frames - cut_overlap) - head_black:
        raise StitchError(
            f"the hook overlap is {cut_hook} frames but the subscribe clip shows only "
            f"{(bumper.frames - cut_overlap) - head_black} frames of picture, so the hook's sound "
            f"would run on into the episode"
        )

    wanted = [
        ("hook", hook, 0, hook.frames - cut_hook),
        ("bumper", bumper, head_black, bumper.frames - cut_overlap),
        ("segment", segment, 0, segment.frames),
    ]
    pieces: list[Piece] = []
    for role, info, in_frame, out_frame in wanted:
        keys = keyframes(_FFPROBE, info)
        copied = keyframe_at_or_before(keys, in_frame) == in_frame
        pieces.append(Piece(role, info.path, in_frame, out_frame, copied))

    notes: list[str] = []
    if cut_hook > 0:
        notes.append(
            f"the hook's last {cut_hook} frames ({hook.seconds_of(cut_hook):.3f}s, its measured black "
            f"tail) are taken off its picture: the subscribe clip starts there and the hook's sound "
            f"plays on under it to its end"
        )
    notes.append(
        f"the subscribe clip's last {cut_overlap} frames ({bumper.seconds_of(cut_overlap):.3f}s, its "
        f"measured black tail) are taken off its picture: the episode starts there and the clip's "
        f"sound runs under the episode's opening"
    )
    notes.append(
        f"the episode's picture and sound are copied untouched from frame 0; its sound starts "
        f"{hook.seconds_of(pieces[0].frames + pieces[1].frames):.3f}s into the delivery"
    )

    return Plan(hook=hook, bumper=bumper, segment=segment, pieces=pieces, shape=shape,
                overlap=cut_overlap, hook_overlap=cut_hook, head_black=head_black,
                hook_tail=hook_tail, strategy=strategy, duck=duck, dynamics=dynamics,
                audio_bitrate=audio_bitrate, notes=notes)


def apply_level(plan: Plan, mode: str, target_tp: float) -> None:
    """Gain-match the hook and the subscribe clip to the episode. Gain only; nothing is compressed.

    The measurement is on all three, because "match" is meaningless without the reference. The
    episode is the reference and is never scaled: it is the long piece and the delivery is built
    around it.
    """
    if mode == "off":
        return
    reference = measure_loudness(_FFMPEG, plan.segment.path)
    measured = {
        "hook": measure_loudness(_FFMPEG, plan.hook.path),
        "bumper": measure_loudness(_FFMPEG, plan.bumper.path),
        "segment": reference,
    }
    target = float(reference["input_i"]) if mode == "segment" else float(mode)
    plan.loudness = measured

    for role in ("hook", "bumper"):
        values = measured[role]
        gain = target - float(values["input_i"])
        ceiling = target_tp - float(values["input_tp"])
        if gain > ceiling:
            print(f"  {role}: +{gain:.2f} dB would peak at {float(values['input_tp']) + gain:.2f} dBTP; "
                  f"held to {ceiling:+.2f} dB so true peak stays <= {target_tp:.1f}")
            gain = ceiling
        if abs(gain) < 0.05:
            gain = 0.0
        if role == "hook":
            plan.hook_gain = gain
        else:
            plan.bumper_gain = gain
        if gain:
            print(f"  {role}: {float(values['input_i']):.2f} LUFS -> "
                  f"{float(values['input_i']) + gain:.2f} LUFS ({gain:+.2f} dB), "
                  f"true peak {float(values['input_tp']) + gain:.2f} dBTP")


# ---------------------------------------------------------------------------------------------
# The sound
# ---------------------------------------------------------------------------------------------

def _gain(value: float) -> str:
    return f",volume={value:.2f}dB" if abs(value) >= 0.05 else ""


def audio_graph(plan: Plan) -> str:
    """One timeline, three sounds, summed where they overlap.

    The hook's sound runs to its real end; the subscribe clip's sound starts where the hook's
    *picture* was cut; the episode's sound starts where the clip's picture stops. `normalize=0` on
    `amix` because the default divides each input by the number of inputs, which would drop both
    sides by 6 dB in exactly the second the delivery is trying to make continuous.
    """
    rate = plan.hook.rate
    sample_rate = plan.hook.audio.sample_rate if plan.hook.audio else 48000
    hook_end = float(Fraction(plan.hook.frames, 1) / rate)
    hook_cut = float(Fraction(plan.pieces[0].frames, 1) / rate)
    bumper_start = float(Fraction(plan.head_black, 1) / rate)
    bumper_end = float(Fraction(plan.bumper.frames, 1) / rate)
    segment_end = float(Fraction(plan.segment.frames, 1) / rate)
    bumper_delay = int(round(hook_cut * sample_rate))
    segment_delay = int(round(float(Fraction(plan.video_frames - plan.segment.frames, 1) / rate)
                              * sample_rate))
    parts = [f"[0:a]atrim=start=0:end={hook_end:.6f},asetpts=N/SR/TB{_gain(plan.hook_gain)}[hook]"]

    bumper = (f"[1:a]atrim=start={bumper_start:.6f}:end={bumper_end:.6f},asetpts=N/SR/TB"
              f"{_gain(plan.bumper_gain)},adelay={bumper_delay}S:all=1")
    if plan.duck:
        parts.append(bumper + ",asplit=2[bumper][bumperkey0]")
        parts.append(f"[bumperkey0]atrim=start=0:end={hook_end + 1.0:.6f},asetpts=N/SR/TB[bumperkey]")
        parts.append(f"[hook][bumperkey]{HOOK_DUCK}[hookd]")
        hook_label = "hookd"
    else:
        parts.append(bumper + "[bumper]")
        hook_label = "hook"

    segment = f"[2:a]atrim=start=0:end={segment_end:.6f},asetpts=N/SR/TB"
    if plan.duck and plan.overlap > 0:
        key_end = float(Fraction(plan.video_frames - plan.segment.frames + plan.overlap, 1) / rate) + 1.0
        parts.append(segment + ",asplit=2[seg][segkey]")
        parts.append(f"[seg]adelay={segment_delay}S:all=1[tail]")
        parts.append(f"[segkey]adelay={segment_delay}S:all=1,atrim=start=0:end={key_end:.6f},"
                     f"asetpts=N/SR/TB[key]")
        parts.append(f"[bumper][key]{DUCK}[bumperd]")
        bumper_label = "bumperd"
    else:
        parts.append(segment + f",adelay={segment_delay}S:all=1[tail]")
        bumper_label = "bumper"

    parts.append(
        f"[{hook_label}][{bumper_label}][tail]amix=inputs=3:duration=longest:normalize=0"
        f"{',' + DYNAMICS if plan.dynamics else ''}[out]"
    )
    return ";".join(parts)


def dropped_graph(plan: Plan) -> str:
    """`drop`: the clip's sound cut where its picture already cuts. The episode loses nothing."""
    rate = plan.hook.rate
    hook_end = float(Fraction(plan.pieces[0].frames, 1) / rate)
    bumper_start = float(Fraction(plan.head_black, 1) / rate)
    bumper_end = float(Fraction(plan.bumper.frames - plan.overlap, 1) / rate)
    segment_end = float(Fraction(plan.segment.frames, 1) / rate)
    return (
        f"[0:a]atrim=start=0:end={hook_end:.6f},asetpts=N/SR/TB[hook];"
        f"[1:a]atrim=start={bumper_start:.6f}:end={bumper_end:.6f},asetpts=N/SR/TB[bumper];"
        f"[2:a]atrim=start=0:end={segment_end:.6f},asetpts=N/SR/TB[segment];"
        f"[hook][bumper][segment]concat=n=3:v=0:a=1"
        f"{',' + DYNAMICS if plan.dynamics else ''}[out]"
    )


# ---------------------------------------------------------------------------------------------
# The join
# ---------------------------------------------------------------------------------------------

def _concat_list(paths: list[Path], target: Path) -> Path:
    lines = []
    for path in paths:
        text = str(path.resolve()).replace("\\", "/").replace("'", "'\\''")
        lines.append(f"file '{text}'")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def _piece_args(piece: Piece, plan: Plan, out: Path, preset: str, crf: int) -> list[str]:
    args = [_FFMPEG, "-y", "-v", "error"]
    if piece.in_frame > 0:
        # Input seeking: it lands on the keyframe at or before the time and decodes forward. For a
        # copy the landing point *is* the piece's start, because the plan refused a non-keyframe start.
        args += ["-ss", f"{float(Fraction(piece.in_frame, 1) / plan.shape.rate):.6f}"]
    # `-frames:v`, not `-t`: a duration is a time, and ffmpeg decides which frame a time includes by
    # comparing it to the frame's timestamp, so a duration can ask for 404 frames and get 405. A frame
    # count is a count and cannot round.
    args += ["-i", str(piece.source), "-frames:v", str(piece.frames), "-map", "0:v:0", "-an"]
    if piece.copied:
        args += ["-c:v", "copy"]
    else:
        args += ["-c:v", "libx264", "-preset", preset, "-crf", str(crf), "-pix_fmt", "yuv420p"]
    args += ["-video_track_timescale", str(TARGET_TIMESCALE),
             "-avoid_negative_ts", "make_zero", "-fflags", "+genpts", str(out)]
    return args


def join(plan: Plan, work: Path, out: Path, *, preset: str, crf: int) -> tuple[list[str], list[Path]]:
    """Write the three pieces, the picture and the sound, and mux them.

    Returns its own log and the piece files, so the verifier can compare the delivery against the
    exact files that were joined rather than against the sources it re-measured.
    """
    log: list[str] = []
    piece_paths: list[Path] = []
    for index, piece in enumerate(plan.pieces):
        target = work / f"piece{index}.mp4"
        piece_paths.append(target)
        run(_piece_args(piece, plan, target, preset, crf))
        log.append(f"[{piece.role}: {piece.frames} frames "
                   f"{'copied' if piece.copied else 're-encoded'}]")

    listing = _concat_list(piece_paths, work / "pieces.txt")
    # The join goes through Annex B: `h264_mp4toannexb` rewrites each piece's length-prefixed NAL
    # units as start-code-prefixed ones and re-emits the parameter sets at every keyframe, so a piece
    # whose SPS differs from the first piece's is decoded with its own rather than with the first's.
    run([_FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing),
         "-c", "copy", "-bsf:v", "h264_mp4toannexb", "-an", "-f", "mpegts", str(work / "joined.ts")])
    log.append("[join the picture]")

    run([_FFMPEG, "-y", "-v", "error", "-i", str(work / "joined.ts"),
         "-map", "0:v:0", "-c", "copy", "-an",
         "-video_track_timescale", str(TARGET_TIMESCALE), str(work / "video.mp4")])
    log.append("[settle the joined picture into mp4]")

    uses_filter = plan.strategy == "mix" or plan.dynamics
    if uses_filter:
        graph = audio_graph(plan) if plan.strategy == "mix" else dropped_graph(plan)
        run([_FFMPEG, "-y", "-v", "error",
             "-i", str(plan.hook.path), "-i", str(plan.bumper.path), "-i", str(plan.segment.path),
             "-filter_complex", graph, "-map", "[out]", "-c:a", "aac", "-b:a", plan.audio_bitrate,
             "-movflags", "+faststart", str(work / "audio.m4a")])
        label = f"[sum the {plan.overlap}-frame overlap"
        if plan.duck and plan.overlap > 0:
            label += ", ducked under the episode's speech"
        if plan.hook_overlap > 0:
            label += f", with the hook's last {plan.hook_overlap} frames of sound under the clip"
        log.append(label + "]")
        audio_source = work / "audio.m4a"
    else:
        cut_sources = [
            ("hook", plan.hook.path, 0.0, plan.hook.seconds_of(plan.pieces[0].frames)),
            ("bumper", plan.bumper.path, plan.bumper.seconds_of(plan.head_black),
             plan.bumper.seconds_of(plan.bumper.frames - plan.overlap)),
            ("segment", plan.segment.path, 0.0, plan.segment.seconds_of(plan.segment.frames)),
        ]
        cuts: list[Path] = []
        for index, (role, source, start, end) in enumerate(cut_sources):
            target = work / f"audio{index}.aac"
            args = [_FFMPEG, "-y", "-v", "error"]
            if start > 0:
                args += ["-ss", f"{start:.6f}"]
            run([*args, "-i", str(source), "-t", f"{end - start:.6f}", "-map", "0:a:0",
                 "-c:a", "copy", "-f", "adts", str(target)])
            cuts.append(target)
            log.append(f"[{role} audio, copied]")
        audio_listing = _concat_list(cuts, work / "audio.txt")
        run([_FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(audio_listing),
             "-c", "copy", "-f", "adts", str(work / "audio.aac")])
        log.append("[join the sound]")
        audio_source = work / "audio.aac"

    published = out.with_name(out.stem + ".part" + out.suffix)
    mux = [_FFMPEG, "-y", "-v", "error",
           "-i", str(work / "video.mp4"), "-i", str(audio_source),
           "-map", "0:v:0", "-map", "1:a:0", "-c", "copy"]
    if not uses_filter:
        # ADTS carries its own headers; MP4 wants them in the sample table instead.
        mux += ["-bsf:a", "aac_adtstoasc"]
    mux += ["-video_track_timescale", str(TARGET_TIMESCALE), "-movflags", "+faststart", str(published)]
    run(mux)
    log.append("[put the picture and the sound together]")
    return log, piece_paths


# ---------------------------------------------------------------------------------------------
# Reading the result back
# ---------------------------------------------------------------------------------------------

def _frame_md5(path: Path, frame: int, rate: Fraction) -> str:
    """The decoded bytes of one frame, as one checksum.

    Seeking as an *input* option is frame-accurate for a decode (ffmpeg lands on the keyframe at or
    before the time and discards forward to it), and both files are read the same way, so both land
    on the frame that was asked for. Reading one frame at a time is what lets the samples be spread
    across the piece instead of being a single contiguous run at its start.
    """
    args = [_FFMPEG, "-v", "error"]
    if frame > 0:
        args += ["-ss", f"{float(Fraction(frame, 1) / rate):.6f}"]
    args += ["-i", str(path), "-map", "0:v:0", "-frames:v", "1", "-f", "framemd5", "-"]
    out = run(args).stdout
    for line in out.splitlines():
        if line.strip() and not line.startswith("#"):
            return line.rsplit(",", 1)[-1].strip()
    return ""


def _frame_timing(ffprobe: str, path: Path, rate: Fraction) -> tuple[int, float]:
    """The largest gap between consecutive frame timestamps, in frames. 1.0 is perfectly even."""
    out = run([ffprobe, "-v", "error", "-select_streams", "v:0",
               "-show_entries", "packet=pts_time", "-of", "csv=p=0", str(path)]).stdout
    times = sorted({float(line) for line in out.splitlines() if line.strip() and line != "N/A"})
    worst = 0.0
    for earlier, later in zip(times, times[1:]):
        worst = max(worst, (later - earlier) * float(rate))
    return len(times), worst


def verify(plan: Plan, output: Path, pieces: list[Path]) -> list[tuple[bool, str]]:
    """Measure what was written and say whether it is right. Not-checked is never a pass."""
    rows: list[tuple[bool, str]] = []
    rate = plan.hook.rate
    frames = count_frames(_FFPROBE, output)
    rows.append((frames == plan.video_frames,
                 f"frames: {frames}, which is the sum of the pieces ({plan.video_frames})"))

    picture = stream_duration(_FFPROBE, output, "v:0")
    sound = stream_duration(_FFPROBE, output, "a:0")
    tolerance = 0.5 / float(rate)
    rows.append((abs(picture - plan.duration) <= tolerance,
                 f"picture duration: {picture:.3f}s against {plan.duration:.3f}s planned "
                 f"({abs(picture - plan.duration) * 1000:.0f}ms, within one frame)"))
    rows.append((abs(sound - picture) <= tolerance,
                 f"sound duration: {sound:.3f}s against {picture:.3f}s of picture "
                 f"({(sound - picture) * 1000:+.0f}ms)"))

    timescale = probe(_FFPROBE, output).timebase
    rows.append((timescale == Fraction(1, TARGET_TIMESCALE),
                 f"video timescale: {timescale} (wanted 1/{TARGET_TIMESCALE})"))

    stamps, worst = _frame_timing(_FFPROBE, output, rate)
    rows.append((worst <= 1.5,
                 f"frame timing: {stamps} frames, largest gap {worst:.2f} frames "
                 f"({'evenly spaced' if worst <= 1.5 else 'a seam is off'})"))

    # Decoded frames against the pieces they claim to come from: three per piece, spread across it,
    # because a copy that is right at frame 1 and wrong at frame 300 is not a copy.
    offset = 0
    for index, piece in enumerate(plan.pieces):
        samples = sorted({min(piece.frames - 1, max(0, at))
                          for at in (1, piece.frames // 2, piece.frames - 2)})
        source = pieces[index] if index < len(pieces) else None
        if source is None or not source.is_file():
            rows.append((False, f"{piece.role} frames: the piece file is missing, so the "
                                f"copied frames could not be checked"))
        else:
            try:
                same = sum(
                    1 for sample in samples
                    if _frame_md5(source, sample, rate) == _frame_md5(output, offset + sample, rate)
                )
                rows.append((same == len(samples),
                             f"{piece.role} frames: {same}/{len(samples)} sampled frames are "
                             f"bit-identical to the piece that was joined"))
            except StitchError as error:
                rows.append((False, f"{piece.role} frames: could not be checked ({error})"))
        offset += piece.frames
    return rows


# ---------------------------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------------------------

_FFMPEG = "ffmpeg"
_FFPROBE = "ffprobe"


def _report_rows(rows: list[tuple[bool, str]]) -> bool:
    for ok, text in rows:
        print(f"  {'ok  ' if ok else 'FAIL'} {text}")
    return all(ok for ok, _ in rows)


def _measure_only(paths: dict[str, Path]) -> None:
    print("integrated loudness and true peak (measured on the sound only):")
    values = {role: measure_loudness(_FFMPEG, path) for role, path in paths.items()}
    for role, data in values.items():
        print(f"  {role:<8} I={float(data['input_i']):>7.2f} LUFS  "
              f"TP={float(data['input_tp']):>7.2f} dBTP  LRA={float(data['input_lra']):>5.2f}")
    segment = float(values["segment"]["input_i"])
    print(f"\nto match the episode ({segment:.2f} LUFS), gain-only, true peak <= -1.0:")
    for role in ("hook", "bumper"):
        data = values[role]
        gain = segment - float(data["input_i"])
        ceiling = -1.0 - float(data["input_tp"])
        note = "" if gain <= ceiling else f"  (held to {ceiling:+.2f} dB by the peak ceiling)"
        print(f"  {role:<8} {gain:+.2f} dB{note}")


def main(argv: list[str]) -> int:
    global _FFMPEG, _FFPROBE

    parser = argparse.ArgumentParser(
        description="Stitch a hook + subscribe clip + full episode into one MP4.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("hook", type=Path)
    parser.add_argument("subscribe", type=Path)
    parser.add_argument("segment", type=Path)
    parser.add_argument("-o", "--out", type=Path, help="the delivery to write")
    parser.add_argument("--overlap", type=int,
                        help="frames of the subscribe clip's tail that run under the episode "
                             "(default: its measured black tail)")
    parser.add_argument("--hook-overlap", type=int,
                        help="frames of the hook's tail the subscribe clip starts over "
                             "(default: the hook's measured black tail; 0 turns it off)")
    parser.add_argument("--strategy", default="mix", choices=("mix", "drop"),
                        help="mix sums the overlap (re-encodes the sound); drop cuts the clip's "
                             "sound where its picture cuts (copies it)")
    parser.add_argument("--level", default="segment",
                        help="match the hook and clip to the episode's loudness ('segment'), to a "
                             "fixed LUFS value ('-14'), or 'off'")
    parser.add_argument("--target-tp", type=float, default=-1.0,
                        help="true peak ceiling for the level pass, dBTP (default -1.0)")
    parser.add_argument("--no-duck", action="store_true",
                        help="do not duck the clip's tail under the episode's speech")
    parser.add_argument("--dynamics", action="store_true",
                        help="apply the operator's Premiere dynamics chain (off by default: the "
                             "episode's audio is never changed)")
    parser.add_argument("--audio-bitrate", default=AUDIO_BITRATE)
    parser.add_argument("--crf", type=int, default=DEFAULT_CRF)
    parser.add_argument("--preset", default=DEFAULT_PRESET)
    parser.add_argument("--work", type=Path,
                        help="where the conformed pieces and the join are written "
                             "(default: <out folder>/_stitch_work/<out stem>)")
    parser.add_argument("--measure", action="store_true",
                        help="report the loudness of the three files and the gains a stitch would "
                             "apply, then stop")
    parser.add_argument("--dry-run", action="store_true",
                        help="plan and print, write nothing")
    parser.add_argument("--no-verify", action="store_true")
    parser.add_argument("--ffmpeg")
    parser.add_argument("--ffprobe")
    args = parser.parse_args(argv[1:])

    _FFMPEG = _which("ffmpeg", args.ffmpeg)
    _FFPROBE = _which("ffprobe", args.ffprobe)

    for path in (args.hook, args.subscribe, args.segment):
        if not path.is_file():
            parser.error(f"not a file: {path}")
    if args.measure:
        _measure_only({"hook": args.hook, "bumper": args.subscribe, "segment": args.segment})
        return 0
    if args.out is None:
        parser.error("-o/--out is required unless --measure is given")
    if args.strategy == "drop" and args.dynamics:
        parser.error("--strategy drop copies the sound, so --dynamics cannot be applied to it")

    started = time.monotonic()
    out = args.out.resolve()
    work = (args.work or out.parent / "_stitch_work" / out.stem).resolve()

    conformed = conform(args.hook.resolve(), args.subscribe.resolve(), args.segment.resolve(), work,
                        hook_overlap=args.hook_overlap, preset=args.preset, crf=args.crf)
    hook = probe(_FFPROBE, conformed.hook)
    bumper = probe(_FFPROBE, conformed.bumper)
    segment = probe(_FFPROBE, conformed.segment)

    print(f"joining at {conformed.shape.describe()}")
    head_black = black_head_frames(_FFMPEG, bumper)
    bumper_tail = black_tail_frames(_FFMPEG, bumper)
    hook_tail = black_tail_frames(_FFMPEG, hook)
    print(f"hook: {hook.frames} frames, black tail {hook_tail} ({hook.seconds_of(hook_tail):.3f}s, "
          f"starts at frame {hook.frames - hook_tail} = {hook.seconds_of(hook.frames - hook_tail):.3f}s)")
    print(f"subscribe clip: {bumper.frames} frames, black head {head_black}, black tail "
          f"{bumper_tail} ({bumper.seconds_of(bumper_tail):.3f}s, starts at frame "
          f"{bumper.frames - bumper_tail} = {bumper.seconds_of(bumper.frames - bumper_tail):.3f}s)")

    plan = plan_stitch(hook, bumper, segment, conformed.shape, overlap=args.overlap,
                       hook_overlap=args.hook_overlap, head_black=head_black,
                       bumper_tail=bumper_tail, hook_tail=hook_tail, strategy=args.strategy,
                       duck=not args.no_duck, dynamics=args.dynamics,
                       audio_bitrate=args.audio_bitrate)
    apply_level(plan, args.level, args.target_tp)

    print(f"hook overlap {plan.hook_overlap} frames ({hook.seconds_of(plan.hook_overlap):.3f}s): the "
          f"subscribe clip starts at {hook.seconds_of(plan.pieces[0].frames):.3f}s and the hook's "
          f"sound runs to {hook.seconds_of(hook.frames):.3f}s")
    print(f"overlap {plan.overlap} frames ({bumper.seconds_of(plan.overlap):.3f}s): the episode's "
          f"picture starts at {hook.seconds_of(plan.pieces[0].frames + plan.pieces[1].frames):.3f}s; "
          f"total {plan.duration:.3f}s")
    for note in plan.notes:
        print(f"  - {note}")
    print(f"  - dynamics chain: {'ON' if plan.dynamics else 'off (the episode audio is never changed)'}")

    if args.dry_run:
        print("\ndry run: nothing written")
        return 0

    log, piece_files = join(plan, work, out, preset=args.preset, crf=args.crf)
    for label in log:
        print(label)
    published = out.with_name(out.stem + ".part" + out.suffix)
    print(f"wrote {out}")

    ok = True
    if not args.no_verify:
        rows = verify(plan, published, piece_files)
        print(f"\n{out.name}: {plan.video_frames} frames, {plan.duration:.3f}s of picture, "
              f"{plan.duration:.3f}s of sound")
        ok = _report_rows(rows)

    if ok:
        os.replace(published, out)
        print(f"\ndone in {time.monotonic() - started:.1f}s -> {out}")
        return 0
    print(f"\nthe delivery did NOT verify; it is left at {published} and {out.name} was not written")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

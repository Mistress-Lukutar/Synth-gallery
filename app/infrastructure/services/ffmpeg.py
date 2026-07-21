'''
File:   ffmpeg.py
Brief:  ffprobe/ffmpeg wrappers for video probing and thumbnail extraction.
Author: Mistress-Lukutar
Date:   2026-07-21
Version: v0.1.0
'''

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from app.logging_config import get_logger

logger = get_logger(__name__)


def _exe(name: str) -> str:
    '''Append .exe on Windows.'''
    return f"{name}.exe" if os.name == "nt" else name


def _get_ffmpeg_binary(name: str) -> Path | None:
    '''Locate an ffmpeg/ffprobe binary.

    Resolution order:
    1. Explicit ``FFMPEG_TOOL_DIR`` environment variable.
    2. Project-local copy (``.venv/ffmpeg``).
    3. Bundled copy next to this module (``../bin/ffmpeg``).
    4. Binary available on ``PATH``.

    Args:
        name: Bare binary name (ffmpeg or ffprobe); OS suffix is applied.

    Returns:
        Absolute path to the binary, or ``None`` if not found.
    '''
    binary_name = _exe(name)

    # Explicit override via environment.
    tool_dir = os.environ.get("FFMPEG_TOOL_DIR")
    if tool_dir:
        candidate = Path(tool_dir) / binary_name
        if candidate.is_file():
            return candidate.resolve()

    # Project-local copy.
    venv_tool_dir = (
        Path(__file__).resolve().parent.parent.parent.parent / ".venv" / "ffmpeg"
    )
    candidate = venv_tool_dir / "bin" / binary_name
    if candidate.is_file():
        return candidate.resolve()

    # Bundled copy shipped with the application.
    bundled_dir = Path(__file__).resolve().parent.parent / "bin" / "ffmpeg"
    candidate = bundled_dir / binary_name
    if candidate.is_file():
        return candidate.resolve()

    # System PATH.
    system_path = shutil.which(name)
    if system_path:
        return Path(system_path).resolve()

    return None


def _run_tool(
    binary: Path,
    args: list[str],
    timeout: int = 30,
) -> subprocess.CompletedProcess:
    '''Run an ffmpeg/ffprobe tool and return the completed process.

    Args:
        binary: Path to the binary.
        args: List of command-line arguments.
        timeout: Timeout in seconds.

    Returns:
        CompletedProcess with captured stdout/stderr.
    '''
    cmd = [str(binary)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
    )


def is_ffmpeg_available() -> bool:
    '''Return True if both ffmpeg and ffprobe are resolvable.'''
    return (
        _get_ffmpeg_binary("ffmpeg") is not None
        and _get_ffmpeg_binary("ffprobe") is not None
    )


def probe_media(file_path: Path) -> dict | None:
    '''Probe a media file via ffprobe.

    Args:
        file_path: Path to the media file.

    Returns:
        Dict with normalized fields: ``width``, ``height``, ``duration``
        (float seconds), ``codec_name`` (video stream), ``fps`` (float or None),
        ``bit_rate`` (int or None). Returns ``None`` on failure.
    '''
    binary = _get_ffmpeg_binary("ffprobe")
    if binary is None:
        logger.warning("ffprobe binary not found; cannot probe media")
        return None

    result = _run_tool(
        binary,
        [
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ],
    )
    if result.returncode != 0:
        logger.warning(
            "ffprobe failed (code %s): %s", result.returncode, result.stderr
        )
        return None

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        logger.warning("ffprobe returned non-JSON output")
        return None

    streams = data.get("streams", []) or []
    fmt = data.get("format", {}) or {}

    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video_stream:
        return None

    width = int(video_stream.get("width", 0) or 0)
    height = int(video_stream.get("height", 0) or 0)
    codec_name = video_stream.get("codec_name")

    # FPS: parse the raw rational (e.g. "30000/1001").
    fps_raw = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate")
    fps: float | None = None
    if fps_raw and "/" in fps_raw:
        try:
            num, den = fps_raw.split("/", 1)
            den_f = float(den)
            fps = float(num) / den_f if den_f else None
        except (ValueError, ZeroDivisionError):
            fps = None
    elif fps_raw:
        try:
            fps = float(fps_raw)
        except ValueError:
            fps = None

    # Duration: prefer stream, fall back to format.
    duration: float | None = None
    duration_raw = video_stream.get("duration") or fmt.get("duration")
    if duration_raw is not None:
        try:
            duration = float(duration_raw)
        except ValueError:
            duration = None

    bit_rate_raw = fmt.get("bit_rate") or video_stream.get("bit_rate")
    bit_rate: int | None = None
    if bit_rate_raw is not None:
        try:
            bit_rate = int(bit_rate_raw)
        except ValueError:
            bit_rate = None

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "codec_name": codec_name,
        "fps": fps,
        "bit_rate": bit_rate,
    }


def extract_video_thumbnail(
    in_path: Path,
    out_path: Path,
    time_offset: float = 1.0,
    size: tuple[int, int] = (400, 400),
) -> tuple[int, int] | None:
    '''Extract a single video frame as a JPEG thumbnail.

    Uses ``ffmpeg -ss <t> -i <in> -frames:v 1`` with a scale/pad filter that
    preserves aspect ratio.

    Args:
        in_path: Path to the source video.
        out_path: Output JPEG path.
        time_offset: Seek time in seconds.
        size: Max thumbnail dimensions.

    Returns:
        Tuple (width, height) of the produced JPEG, or ``None`` on failure.
    '''
    binary = _get_ffmpeg_binary("ffmpeg")
    if binary is None:
        logger.warning("ffmpeg binary not found; cannot extract thumbnail")
        return None

    max_w, max_h = size
    scale_filter = (
        f"scale={max_w}:{max_h}:force_original_aspect_ratio=decrease,"
        f"pad={max_w}:{max_h}:(ow-iw)/2:(oh-ih)/2"
    )

    args = [
        "-y",
        "-ss", f"{time_offset:.3f}",
        "-i", str(in_path),
        "-frames:v", "1",
        "-vf", scale_filter,
        "-q:v", "3",
        str(out_path),
    ]

    result = _run_tool(binary, args, timeout=60)
    if result.returncode != 0 or not out_path.exists():
        # Seek beyond EOF or unsupported codec at that offset: retry from t=0.
        logger.warning(
            "ffmpeg thumbnail seek failed, retrying from t=0: %s",
            (result.stderr or "").strip()[:200],
        )
        args_retry = [
            "-y",
            "-i", str(in_path),
            "-frames:v", "1",
            "-vf", scale_filter,
            "-q:v", "3",
            str(out_path),
        ]
        result = _run_tool(binary, args_retry, timeout=60)
        if result.returncode != 0 or not out_path.exists():
            logger.warning(
                "ffmpeg thumbnail extraction failed: %s",
                (result.stderr or "").strip()[:200],
            )
            return None

    # Read produced dimensions via Pillow (avoids a second ffprobe round-trip).
    try:
        from PIL import Image
        with Image.open(out_path) as img:
            return img.size
    except Exception:
        return size


def extract_video_thumbnail_bytes(
    in_path: Path,
    time_offset: float = 1.0,
    size: tuple[int, int] = (400, 400),
) -> tuple[bytes, int, int] | None:
    '''Extract a video thumbnail and return JPEG bytes plus dimensions.

    Args:
        in_path: Path to the source video.
        time_offset: Seek time in seconds.
        size: Max thumbnail dimensions.

    Returns:
        Tuple of (jpeg_bytes, width, height), or ``None`` on failure.
    '''
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        out_path = Path(tmp.name)
    try:
        dims = extract_video_thumbnail(in_path, out_path, time_offset, size)
        if dims is None:
            return None
        return out_path.read_bytes(), dims[0], dims[1]
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass

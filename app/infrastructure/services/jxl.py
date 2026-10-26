'''
File:   jxl.py
Brief:  JPEG XL (JXL) decoding and introspection via the official libjxl binaries.
Author: Mistress-Lukutar
Date:   2026-07-11
'''

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


# Magic bytes for JPEG XL (container and bare codestream)
JXL_CONTAINER_MAGIC = b"\x00\x00\x00\x0cJXL "
JXL_CODESTREAM_MAGIC = b"\xff\x0a"


def _get_jxl_binary(name: str) -> Path | None:
    '''Locate a libjxl binary (djxl / jxlinfo).

    Resolution order:
    1. Explicit ``JXL_TOOL_DIR`` environment variable.
    2. Project-local copy downloaded by Start.bat (``.venv/jxl-tools``).
    3. Common local installation path (``C:/jxl-x64-windows-static``).
    4. Bundled copy next to this module (``../bin/jxl``).
    5. Binary available on ``PATH``.

    Returns the absolute path or ``None`` if not found.
    '''
    # Explicit override via environment
    tool_dir = os.environ.get("JXL_TOOL_DIR")
    if tool_dir:
        candidate = Path(tool_dir) / name
        if candidate.is_file():
            return candidate.resolve()

    # Project-local copy downloaded by Start.bat
    venv_tool_dir = Path(__file__).resolve().parent.parent.parent.parent / ".venv" / "jxl-tools"
    candidate = venv_tool_dir / "bin" / name
    if candidate.is_file():
        return candidate.resolve()

    # Common local installation path on Windows
    local_install = Path(r"C:\jxl-x64-windows-static")
    candidate = local_install / "bin" / name
    if candidate.is_file():
        return candidate.resolve()

    # Bundled copy shipped with the application
    bundled_dir = Path(__file__).resolve().parent.parent / "bin" / "jxl"
    candidate = bundled_dir / name
    if candidate.is_file():
        return candidate.resolve()

    # System PATH
    system_path = shutil.which(name)
    if system_path:
        return Path(system_path).resolve()

    return None


def _run_tool(binary: Path, args: list[str]) -> subprocess.CompletedProcess:
    '''Run a libjxl tool and return the completed process.'''
    cmd = [str(binary)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )


def _run_djxl_stdin(args: list[str], content: bytes) -> subprocess.CompletedProcess:
    '''Run djxl with the JXL bytes on stdin and raw output on stdout.

    Uses ``djxl - - <args>`` so no temporary files are involved.

    Raises:
        RuntimeError: if djxl is unavailable or fails.
    '''
    binary = _get_jxl_binary("djxl.exe" if os.name == "nt" else "djxl")
    if binary is None:
        raise RuntimeError("JXL decoder (djxl) not found")

    result = subprocess.run(
        [str(binary), "-", "-"] + args,
        input=content,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"djxl failed (code {result.returncode}): "
            f"{result.stderr.decode('utf-8', errors='ignore')}"
        )
    return result


def is_jxl_content(content: bytes) -> bool:
    '''Return True if the bytes look like a JPEG XL file.'''
    if len(content) < 2:
        return False
    if content.startswith(JXL_CONTAINER_MAGIC):
        return True
    if content.startswith(JXL_CODESTREAM_MAGIC):
        return True
    return False


def decode_jxl(content: bytes) -> bytes:
    '''Decode a JPEG XL image to a standard format (PNG) as bytes.

    Decoding is performed exclusively through the official ``djxl`` CLI
    (bytes in via stdin, PNG out via stdout — no temporary files).

    Raises:
        RuntimeError: if JXL cannot be decoded.
    '''
    return _run_djxl_stdin(["--output_format=png"], content).stdout


#: PAM TUPLTYPE values decodable straight into a PNM buffer Pillow reads.
_PNM_FAST_TUPLE_TYPES = {"RGB": "P6", "GRAYSCALE": "P5"}


def decode_jxl_to_pixels(content: bytes) -> bytes:
    '''Decode a JPEG XL image to raw pixels as a Pillow-readable PNM buffer.

    djxl decodes to PAM (uncompressed, fastest path) and the PAM payload is
    re-wrapped with a P6 (RGB) / P5 (grayscale) header so Pillow can open
    it directly. Images djxl would render as anything else — alpha channels
    (``RGB_ALPHA``), >8-bit depth — fall back to a PNG decode, which is
    alpha-safe and lossless, trading speed for correctness.

    Returns:
        Image bytes ``Image.open`` accepts (PPM/PGM or PNG).

    Raises:
        RuntimeError: if JXL cannot be decoded.
    '''
    pam = _run_djxl_stdin(["--output_format=pam"], content).stdout
    wrapped = _wrap_pam_as_pnm(pam)
    if wrapped is not None:
        return wrapped
    # Alpha / 16-bit / exotic layout: decode to PNG instead.
    return _run_djxl_stdin(["--output_format=png"], content).stdout


def _wrap_pam_as_pnm(pam: bytes) -> bytes | None:
    '''Rewrap a PAM image as a P6/P5 buffer, or return None if unsupported.

    Only plain 8-bit RGB and grayscale PAM payloads are converted; anything
    else (alpha, 16-bit, CMYK) returns None so callers can use the PNG path.
    '''
    end = pam.find(b"ENDHDR\n")
    if end < 0:
        return None
    header: dict[str, str] = {}
    for line in pam[: end + 7].decode("ascii", errors="ignore").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            header[parts[0]] = parts[1]
    magic = _PNM_FAST_TUPLE_TYPES.get(header.get("TUPLTYPE", ""))
    if magic is None or header.get("MAXVAL") != "255":
        return None
    try:
        width, height = header["WIDTH"], header["HEIGHT"]
    except KeyError:
        return None
    return f"{magic}\n{width} {height}\n255\n".encode("ascii") + pam[end + 7 :]


def reconstruct_jpeg(content: bytes) -> bytes | None:
    '''Losslessly reconstruct the original JPEG from a JXL transcode.

    cjxl stores the source JPEG inside lossless JPEG transcodes, letting
    djxl rebuild it bit-exactly (EXIF and orientation untouched). Returns
    ``None`` when the JXL does not carry JPEG reconstruction data.

    Raises:
        RuntimeError: if djxl is unavailable.
    '''
    binary = _get_jxl_binary("djxl.exe" if os.name == "nt" else "djxl")
    if binary is None:
        raise RuntimeError("JXL decoder (djxl) not found")

    result = subprocess.run(
        [str(binary), "-", "-", "--output_format=jpg", "-J"],
        input=content,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    return result.stdout or None


def extract_jxl_exif(content: bytes) -> bytes | None:
    '''Extract the raw EXIF (TIFF) blob embedded in a JXL container.

    Metadata-only: djxl parses the container without a full pixel decode.
    Returns ``None`` when the image carries no EXIF.

    Raises:
        RuntimeError: if djxl is unavailable.
    '''
    result = _run_djxl_stdin(["--output_format=exif"], content)
    return result.stdout or None


def get_jxl_dimensions(content: bytes) -> tuple[int, int] | None:
    '''Return (width, height) of a JPEG XL image.

    Dimensions are read exclusively through the official ``jxlinfo`` CLI.
    '''
    binary = _get_jxl_binary("jxlinfo.exe" if os.name == "nt" else "jxlinfo")
    if binary is None:
        return None

    with tempfile.NamedTemporaryFile(suffix=".jxl", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        result = _run_tool(binary, [str(tmp_path)])
        if result.returncode != 0:
            return None

        # First line looks like:
        # "JPEG XL image, 100x80, lossy, 8-bit RGB"
        match = re.search(r"(\d+)x(\d+)", result.stdout)
        if match:
            return int(match.group(1)), int(match.group(2))
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass

    return None


def is_jxl_available() -> bool:
    '''Return True if the application can decode JXL files.'''
    return _get_jxl_binary("djxl.exe" if os.name == "nt" else "djxl") is not None

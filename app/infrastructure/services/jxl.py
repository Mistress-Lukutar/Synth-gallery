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

    Decoding is performed exclusively through the official ``djxl`` CLI.

    Raises:
        RuntimeError: if JXL cannot be decoded.
    '''
    binary = _get_jxl_binary("djxl.exe" if os.name == "nt" else "djxl")
    if binary is None:
        raise RuntimeError("JXL decoder (djxl) not found")

    with tempfile.NamedTemporaryFile(suffix=".jxl", delete=False) as in_file:
        in_file.write(content)
        in_path = Path(in_file.name)

    out_path = in_path.with_suffix(".png")
    try:
        result = _run_tool(binary, [str(in_path), str(out_path)])
        if result.returncode != 0:
            raise RuntimeError(
                f"djxl failed (code {result.returncode}): {result.stderr}"
            )
        if not out_path.exists():
            raise RuntimeError("djxl did not produce output file")
        return out_path.read_bytes()
    finally:
        try:
            in_path.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            out_path.unlink(missing_ok=True)
        except Exception:
            pass


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

"""JPEG XL (JXL) support using the bundled libjxl binaries.

The application ships with ``djxl`` and ``jxlinfo`` executables so that JXL
files can be decoded on platforms where Pillow does not natively support JXL.
On systems where a native Pillow plugin is available it will be used
automatically and the binaries are ignored.
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from io import BytesIO

from PIL import Image

# Optional native Pillow plugin for JPEG XL. If installed, it takes priority
# over the bundled libjxl binaries.
try:
    import pillow_jxl  # noqa: F401
except ImportError:
    pass


# Magic bytes for JPEG XL (container and bare codestream)
JXL_CONTAINER_MAGIC = b"\x00\x00\x00\x0cJXL "
JXL_CODESTREAM_MAGIC = b"\xff\x0a"


def _get_jxl_binary(name: str) -> Optional[Path]:
    """Locate a libjxl binary (djxl / jxlinfo).

    Resolution order:
    1. Explicit ``JXL_TOOL_DIR`` environment variable.
    2. Project-local copy downloaded by Start.bat (``.venv/jxl-tools``).
    3. Common local installation path (``C:/jxl-x64-windows-static``).
    4. Bundled copy next to this module (``../bin/jxl``).
    5. Binary available on ``PATH``.

    Returns the absolute path or ``None`` if not found.
    """
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
    """Run a libjxl tool and return the completed process."""
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
    """Return True if the bytes look like a JPEG XL file."""
    if len(content) < 2:
        return False
    if content.startswith(JXL_CONTAINER_MAGIC):
        return True
    if content.startswith(JXL_CODESTREAM_MAGIC):
        return True
    return False


def _pil_supports_jxl() -> bool:
    """Check whether Pillow can already open JXL files natively."""
    return ".jxl" in Image.registered_extensions()


def decode_jxl(content: bytes) -> bytes:
    """Decode a JPEG XL image to a standard format (PNG) as bytes.

    First tries a native Pillow plugin if available, otherwise falls back to
    the bundled ``djxl`` binary.

    Raises:
        RuntimeError: if JXL cannot be decoded.
    """
    # Fast path: Pillow has native JXL support
    if _pil_supports_jxl():
        with Image.open(BytesIO(content)) as img:
            output = BytesIO()
            if img.mode in ("RGBA", "P"):
                img.save(output, "PNG")
            else:
                img.save(output, "PNG")
            return output.getvalue()

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


def get_jxl_dimensions(content: bytes) -> Optional[Tuple[int, int]]:
    """Return (width, height) of a JPEG XL image.

    Tries Pillow first, then falls back to ``jxlinfo``.
    """
    if _pil_supports_jxl():
        try:
            with Image.open(BytesIO(content)) as img:
                return img.size
        except Exception:
            pass

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
    """Return True if the application can decode JXL files."""
    if _pil_supports_jxl():
        return True
    return _get_jxl_binary("djxl.exe" if os.name == "nt" else "djxl") is not None

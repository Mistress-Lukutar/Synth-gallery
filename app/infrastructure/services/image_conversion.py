'''
File:   image_conversion.py
Brief:  On-demand image conversion for downloads (original / JXL / JPEG /
        PNG / WebP). Stored JXL is decoded through djxl (raw pixels or
        bit-exact JPEG reconstruction); encoding of JXL targets goes through
        cjxl; all other targets are produced with Pillow.
Author: Mistress-Lukutar
Date:   2026-08-19
'''
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from PIL import Image, ImageOps

from .jxl import decode_jxl_to_pixels, extract_jxl_exif, reconstruct_jpeg
from .jxl_encoder import encode_to_lossless_jxl

logger = logging.getLogger(__name__)

#: Target formats offered by the download modal. ``original`` serves the
#: stored bytes as-is (no re-encode).
SUPPORTED_DOWNLOAD_FORMATS = ('original', 'jxl', 'jpeg', 'png', 'webp')

#: Format that always passes stored bytes through untouched.
ORIGINAL_FORMAT = 'original'

#: (content_type, file extension) per target format.
_FORMAT_INFO = {
    'jxl': ('image/jxl', '.jxl'),
    'jpeg': ('image/jpeg', '.jpg'),
    'png': ('image/png', '.png'),
    'webp': ('image/webp', '.webp'),
}


class ImageConversionError(Exception):
    '''Raised when an image cannot be converted to the requested format.'''


@dataclass
class ConversionSettings:
    '''User-selectable conversion options from the download modal.

    Attributes:
        format: Target format (one of SUPPORTED_DOWNLOAD_FORMATS).
        jpeg_quality: JPEG quality 1-100 (lossy).
        webp_quality: WebP quality 1-100 (ignored when lossless).
        webp_lossless: Encode WebP losslessly instead.
        jxl_effort: cjxl effort override (1-9); None uses JXL_EFFORT.
        png_optimize: Ask Pillow to optimize the PNG payload.
    '''

    format: str = ORIGINAL_FORMAT
    jpeg_quality: int = 90
    webp_quality: int = 90
    webp_lossless: bool = False
    jxl_effort: int | None = None
    png_optimize: bool = True


def is_convertible(content_type: str) -> bool:
    '''Return True when the stored object is an image conversion applies to.'''
    return bool(content_type) and content_type.startswith('image/')


def format_extension(fmt: str) -> str | None:
    '''Return the canonical file extension for a download format.

    Args:
        fmt: One of SUPPORTED_DOWNLOAD_FORMATS.

    Returns:
        The extension including the leading dot, or None for unknown formats.
    '''
    info = _FORMAT_INFO.get(fmt)
    return info[1] if info else None


def needs_conversion(content_type: str, settings: ConversionSettings) -> bool:
    '''Decide whether stored bytes must be re-encoded for these settings.

    ``original`` never converts. Lossless targets are passed through when
    the source is already stored in the same format (jxl -> jxl, png -> png).
    Lossy targets (jpeg, webp) always re-encode so the chosen quality is
    actually applied.

    Args:
        content_type: Stored MIME type of the item.
        settings: Requested conversion settings.

    Returns:
        True when the item should go through convert_image().
    '''
    if settings.format == ORIGINAL_FORMAT or not is_convertible(content_type):
        return False
    target = settings.format
    if target == 'jxl':
        return content_type != 'image/jxl'
    if target == 'png':
        return content_type != 'image/png'
    return True  # jpeg / webp: quality applies, always re-encode


def convert_image(
    data: bytes,
    source_content_type: str,
    settings: ConversionSettings,
) -> tuple[bytes, str, str]:
    '''Convert raw image bytes to the requested target format.

    JXL sources are decoded via djxl (see :func:`decode_jxl_to_pixels`);
    JXL targets are encoded via cjxl so JPEG sources keep the reversible
    lossless transcode. When both source and target are JPEG-related the
    stored JXL is first offered to djxl for bit-exact JPEG reconstruction;
    if the JXL carries no reconstruction data the normal decode + re-encode
    path applies the requested quality. Every other conversion goes through
    Pillow.

    Args:
        data: Decrypted plaintext image bytes.
        source_content_type: MIME type the item is stored as.
        settings: Requested conversion settings.

    Returns:
        Tuple (converted_bytes, content_type, file_extension).

    Raises:
        ImageConversionError: When the target format is unknown or the
            conversion fails (decoder/encoder missing, corrupt input).
    '''
    if settings.format not in _FORMAT_INFO:
        raise ImageConversionError(
            f'Unsupported target format: {settings.format!r}'
        )
    try:
        if settings.format == 'jxl':
            converted = encode_to_lossless_jxl(
                data, effort=settings.jxl_effort
            )
        elif settings.format == 'jpeg' and source_content_type == 'image/jxl':
            # cjxl --lossless_jpeg transcodes keep the original JPEG inside
            # the container; reconstructing it is both faster than a pixel
            # round-trip and bit-exact (quality setting does not apply).
            reconstructed = reconstruct_jpeg(data)
            if reconstructed is not None:
                return reconstructed, 'image/jpeg', '.jpg'
            converted = _convert_with_pillow(data, source_content_type, settings)
        else:
            converted = _convert_with_pillow(data, source_content_type, settings)
    except ImageConversionError:
        raise
    except Exception as exc:  # noqa: BLE001 - normalize for callers
        raise ImageConversionError(str(exc)) from exc

    content_type, extension = _FORMAT_INFO[settings.format]
    return converted, content_type, extension


def _convert_with_pillow(
    data: bytes,
    source_content_type: str,
    settings: ConversionSettings,
) -> bytes:
    '''Decode via Pillow (djxl for JXL sources) and re-encode.'''
    with _open_image(data, source_content_type) as img:
        img = ImageOps.exif_transpose(img)
        output = io.BytesIO()
        if settings.format == 'jpeg':
            _save_jpeg(img, output, settings)
        elif settings.format == 'png':
            img.save(output, 'PNG', optimize=settings.png_optimize)
        elif settings.format == 'webp':
            _save_webp(img, output, settings)
        else:  # pragma: no cover - guarded by convert_image()
            raise ImageConversionError(
                f'Unsupported target format: {settings.format!r}'
            )
        return output.getvalue()


def _open_image(data: bytes, source_content_type: str) -> Image.Image:
    '''Open image bytes with Pillow, decoding JXL via djxl when needed.

    JXL sources are decoded to raw pixels (no PNG round-trip) and their
    embedded EXIF blob is re-attached to the image so downstream saves can
    pass it through. djxl already applies orientation to the decoded
    pixels, mirroring the previous PNG-based decode.
    '''
    exif_blob: bytes | None = None
    if source_content_type == 'image/jxl':
        try:
            exif_blob = extract_jxl_exif(data)
        except RuntimeError:
            exif_blob = None  # metadata is best-effort
        data = decode_jxl_to_pixels(data)
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
        if exif_blob:
            img.info['exif'] = exif_blob
        return img
    except Exception as exc:  # noqa: BLE001 - corrupt/unsupported input
        raise ImageConversionError(
            f'Cannot decode source image: {exc}'
        ) from exc


def _save_jpeg(img: Image.Image, output: io.BytesIO, settings: ConversionSettings) -> None:
    '''Save as JPEG; alpha/paletted modes are flattened to RGB.'''
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')
    kwargs: dict = {
        'quality': settings.jpeg_quality,
        'optimize': True,
    }
    # Best-effort EXIF passthrough (exif_transpose already stripped the
    # orientation tag from the copied EXIF payload).
    exif = img.info.get('exif')
    if exif:
        kwargs['exif'] = exif
    img.save(output, 'JPEG', **kwargs)


def _save_webp(img: Image.Image, output: io.BytesIO, settings: ConversionSettings) -> None:
    '''Save as WebP; paletted/alpha modes keep their alpha channel.'''
    if img.mode in ('P', 'LA'):
        img = img.convert('RGBA')
    if settings.webp_lossless:
        img.save(output, 'WEBP', lossless=True)
    else:
        img.save(output, 'WEBP', quality=settings.webp_quality)

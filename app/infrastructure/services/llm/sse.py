'''
File:   sse.py
Brief:  Minimal server-sent-events frame parser for httpx byte streams.
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

from typing import AsyncIterator, Optional

__all__ = ["iter_sse_frames"]


def _next_line(buffer: str) -> tuple[Optional[str], str, bool]:
    '''Split the first complete line off ``buffer``.

    Handles ``\\n`` and ``\\r\\n`` (plus a lone ``\\r``). A trailing ``\\r``
    at the very end of the buffer is kept, because it may be the first half
    of a ``\\r\\n`` pair that arrives with the next chunk.

    Returns:
        ``(line, rest, found)`` — ``line`` is ``None`` when no complete
        line is available yet.
    '''
    idx_lf = buffer.find("\n")
    idx_cr = buffer.find("\r")
    if idx_lf == -1 and idx_cr == -1:
        return None, buffer, False
    if idx_cr != -1 and (idx_lf == -1 or idx_cr < idx_lf):
        if idx_cr == len(buffer) - 1:
            return None, buffer, False  # wait: may be half of "\r\n"
        if buffer[idx_cr + 1] == "\n":
            return buffer[:idx_cr], buffer[idx_cr + 2:], True
        return buffer[:idx_cr], buffer[idx_cr + 1:], True
    return buffer[:idx_lf], buffer[idx_lf + 1:], True


async def iter_sse_frames(
    chunks: AsyncIterator[bytes],
) -> AsyncIterator[tuple[Optional[str], str]]:
    '''Parse an SSE byte stream into ``(event_name, data)`` frames.

    Feed it with the chunks produced by ``response.aiter_bytes()``. Lines
    of the form ``event: X`` name the next frame, ``data: Y`` accumulate
    the payload (multiple ``data:`` lines are joined with ``\\n``) and a
    blank line dispatches the frame. ``:`` comment lines and ``id:`` /
    ``retry:`` fields are ignored. A frame without ``data:`` lines is
    dropped, per the SSE specification. A pending frame at end of stream
    is flushed for robustness against servers that omit the final blank
    line.

    Args:
        chunks: Async iterator of UTF-8 byte chunks.

    Yields:
        ``(event_name_or_None, data_str)`` tuples.
    '''
    buffer = ""
    event_name: Optional[str] = None
    data_lines: list[str] = []

    async for chunk in chunks:
        buffer += chunk.decode("utf-8", errors="replace")
        while True:
            line, buffer, found = _next_line(buffer)
            if not found:
                break
            if line == "":
                # Blank line: dispatch the current frame, if it has data.
                if data_lines:
                    yield (event_name, "\n".join(data_lines))
                event_name = None
                data_lines = []
            elif line.startswith(":"):
                continue  # comment / keep-alive
            else:
                field_name, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if field_name == "event":
                    event_name = value
                elif field_name == "data":
                    data_lines.append(value)
                # "id" / "retry" / unknown fields are ignored.

    # End of stream: flush a trailing frame the server did not terminate
    # with a blank line.
    if data_lines:
        yield (event_name, "\n".join(data_lines))

'''
File:   test_sse.py
Brief:  Unit tests for the minimal SSE frame parser.
Author: Mistress-Lukutar
Date:   2026-10-09
'''

from __future__ import annotations

from typing import AsyncIterator

from app.infrastructure.services.llm.sse import iter_sse_frames


async def _chunks(*pieces: bytes) -> AsyncIterator[bytes]:
    for piece in pieces:
        yield piece


async def _parse(*pieces: bytes) -> list[tuple]:
    return [frame async for frame in iter_sse_frames(_chunks(*pieces))]


async def test_crlf_and_lf_frames():
    frames = await _parse(
        b'event: add\r\ndata: {"a": 1}\r\n\r\n'
        b'data: second\n\n'
    )
    assert frames == [("add", '{"a": 1}'), (None, "second")]


async def test_multiline_data_joined_with_newline():
    frames = await _parse(b'event: x\ndata: line1\ndata: line2\n\n')
    assert frames == [("x", "line1\nline2")]


async def test_comments_id_and_retry_ignored():
    frames = await _parse(
        b': keep-alive\nid: 42\nretry: 100\ndata: hello\n\n'
    )
    assert frames == [(None, "hello")]


async def test_chunk_boundary_split_mid_line():
    frames = await _parse(b'event: ev\nda', b'ta: val', b'ue\n\n')
    assert frames == [("ev", "value")]


async def test_crlf_split_across_chunks():
    frames = await _parse(b'event: a\r', b'\ndata: v\r\n\r\n')
    assert frames == [("a", "v")]


async def test_frame_without_data_dropped_and_trailing_flush():
    frames = await _parse(b'event: only-event\n\n', b'data: tail\n')
    assert frames == [(None, "tail")]


async def test_data_field_without_space():
    frames = await _parse(b'data:no-space\n\n')
    assert frames == [(None, "no-space")]


async def test_empty_stream_yields_nothing():
    assert await _parse(b'') == []

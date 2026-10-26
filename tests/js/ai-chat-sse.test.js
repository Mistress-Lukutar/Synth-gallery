/**
 * Unit tests for the SSE frame parser of ai-chat.js.
 *
 * parseSSEFrames(bufferText) -> { frames: [{event, data}], rest }
 * must handle split-across-chunk frames, \n\n and \r\n\r\n separators,
 * multiple data lines and incomplete trailing data.
 */
const { parseSSEFrames } = require('../../app/static/js/ai-chat.js');

describe('parseSSEFrames', () => {
    test('parses a single complete frame', () => {
        const result = parseSSEFrames('event: token\ndata: {"text":"Hi"}\n\n');
        expect(result.frames).toEqual([
            { event: 'token', data: { text: 'Hi' } },
        ]);
        expect(result.rest).toBe('');
    });

    test('parses multiple complete frames', () => {
        const result = parseSSEFrames(
            'event: token\ndata: {"text":"a"}\n\n' +
            'event: token\ndata: {"text":"b"}\n\n' +
            'event: turn_done\ndata: {"stop_reason":"end"}\n\n'
        );
        expect(result.frames).toHaveLength(3);
        expect(result.frames[0].data).toEqual({ text: 'a' });
        expect(result.frames[2]).toEqual({ event: 'turn_done', data: { stop_reason: 'end' } });
        expect(result.rest).toBe('');
    });

    test('keeps an incomplete trailing frame in rest (split across chunks)', () => {
        const first = parseSSEFrames('event: token\ndata: {"te');
        expect(first.frames).toEqual([]);
        expect(first.rest).toBe('event: token\ndata: {"te');

        const second = parseSSEFrames(first.rest + 'xt":"hi"}\n\n');
        expect(second.frames).toEqual([{ event: 'token', data: { text: 'hi' } }]);
        expect(second.rest).toBe('');
    });

    test('splits a buffer holding a complete and an incomplete frame', () => {
        const result = parseSSEFrames(
            'event: token\ndata: {"text":"a"}\n\nevent: to'
        );
        expect(result.frames).toEqual([{ event: 'token', data: { text: 'a' } }]);
        expect(result.rest).toBe('event: to');
    });

    test('handles CRLF separators (\\r\\n\\r\\n)', () => {
        const result = parseSSEFrames('event: token\r\ndata: {"text":"a"}\r\n\r\n');
        expect(result.frames).toEqual([{ event: 'token', data: { text: 'a' } }]);
        expect(result.rest).toBe('');
    });

    test('handles CRLF frames split across chunks', () => {
        const first = parseSSEFrames('event: token\r\ndata: {"text":"a"}\r\n\r');
        expect(first.frames).toEqual([]);
        // The lone trailing \r cannot start a new frame terminator yet.
        expect(first.rest).toBe('event: token\r\ndata: {"text":"a"}\r\n\r');

        const second = parseSSEFrames(first.rest + '\n');
        expect(second.frames).toEqual([{ event: 'token', data: { text: 'a' } }]);
        expect(second.rest).toBe('');
    });

    test('joins multiple data lines with newlines', () => {
        const result = parseSSEFrames('event: token\ndata: {"text":\ndata: "split"}\n\n');
        expect(result.frames).toEqual([{ event: 'token', data: { text: 'split' } }]);
    });

    test('defaults the event name to "message" when missing', () => {
        const result = parseSSEFrames('data: {"text":"x"}\n\n');
        expect(result.frames).toEqual([{ event: 'message', data: { text: 'x' } }]);
    });

    test('keeps non-JSON data as a raw string', () => {
        const result = parseSSEFrames('event: ping\ndata: keep-alive\n\n');
        expect(result.frames).toEqual([{ event: 'ping', data: 'keep-alive' }]);
    });

    test('ignores comment/keep-alive lines and data-less frames', () => {
        const result = parseSSEFrames(': ping\n\n: another\n\ndata: {"ok":true}\n\n');
        expect(result.frames).toEqual([{ event: 'message', data: { ok: true } }]);
    });

    test('returns empty frames for a buffer without a terminator', () => {
        const result = parseSSEFrames('event: token\ndata: 1');
        expect(result.frames).toEqual([]);
        expect(result.rest).toBe('event: token\ndata: 1');
    });

    test('handles empty and null buffers', () => {
        expect(parseSSEFrames('')).toEqual({ frames: [], rest: '' });
        expect(parseSSEFrames(null)).toEqual({ frames: [], rest: '' });
    });
});

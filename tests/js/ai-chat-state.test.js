/**
 * Unit tests for the pure chat-state reducer of ai-chat.js.
 *
 * applyChatEvent(state, event) must return a NEW state for every SSE event
 * type (token, reasoning, tool_start, tool_result, vision_request,
 * turn_done, error) and ignore unknown events.
 */
const {
    applyChatEvent,
    appendUserMessage,
    stateFromMessages,
    newChatState,
} = require('../../app/static/js/ai-chat.js');

describe('applyChatEvent: token', () => {
    test('creates a streaming assistant message on the first token', () => {
        const next = applyChatEvent(newChatState(), { type: 'token', text: 'Hel' });
        expect(next.streaming).toBe(true);
        expect(next.messages).toHaveLength(1);
        expect(next.messages[0].role).toBe('assistant');
        expect(next.messages[0].parts).toEqual([{ type: 'text', text: 'Hel' }]);
    });

    test('appends subsequent tokens to the same text part', () => {
        let state = applyChatEvent(newChatState(), { type: 'token', text: 'Hel' });
        state = applyChatEvent(state, { type: 'token', text: 'lo' });
        expect(state.messages[0].parts).toEqual([{ type: 'text', text: 'Hello' }]);
    });

    test('opens a new assistant message after a user message', () => {
        let state = appendUserMessage(newChatState(), 'question', null);
        state = applyChatEvent(state, { type: 'token', text: 'answer' });
        expect(state.messages).toHaveLength(2);
        expect(state.messages[0].role).toBe('user');
        expect(state.messages[1].role).toBe('assistant');
        expect(state.messages[1].parts[0].text).toBe('answer');
    });
});

describe('applyChatEvent: reasoning', () => {
    test('collects reasoning text without crashing', () => {
        let state = applyChatEvent(newChatState(), { type: 'reasoning', text: 'think' });
        state = applyChatEvent(state, { type: 'reasoning', text: 'ing' });
        expect(state.streaming).toBe(true);
        expect(state.messages[0].parts).toEqual([{ type: 'reasoning', text: 'thinking' }]);
    });
});

describe('applyChatEvent: tool events', () => {
    test('tool_start creates a running tool card', () => {
        const state = applyChatEvent(newChatState(), {
            type: 'tool_start',
            call_id: 'call-1',
            name: 'search_items',
            arguments: { query: 'cats' },
        });
        expect(state.streaming).toBe(true);
        const part = state.messages[0].parts[0];
        expect(part.type).toBe('tool');
        expect(part.call_id).toBe('call-1');
        expect(part.name).toBe('search_items');
        expect(part.args).toEqual({ query: 'cats' });
        expect(part.status).toBe('running');
    });

    test('tool_start accepts string arguments (JSON parsed)', () => {
        const state = applyChatEvent(newChatState(), {
            type: 'tool_start',
            call_id: 'c2',
            name: 't',
            arguments: '{"a":1}',
        });
        expect(state.messages[0].parts[0].args).toEqual({ a: 1 });
    });

    test('tool_result updates the matching card by call_id', () => {
        let state = applyChatEvent(newChatState(), {
            type: 'tool_start',
            call_id: 'call-1',
            name: 'search_items',
            arguments: {},
        });
        state = applyChatEvent(state, {
            type: 'tool_result',
            call_id: 'call-1',
            name: 'search_items',
            summary: '3 items found',
            is_error: false,
        });
        const part = state.messages[0].parts[0];
        expect(part.status).toBe('done');
        expect(part.summary).toBe('3 items found');
    });

    test('tool_result marks the card as error', () => {
        let state = applyChatEvent(newChatState(), {
            type: 'tool_start',
            call_id: 'call-1',
            name: 'search_items',
            arguments: {},
        });
        state = applyChatEvent(state, {
            type: 'tool_result',
            call_id: 'call-1',
            name: 'search_items',
            summary: 'boom',
            is_error: true,
        });
        expect(state.messages[0].parts[0].status).toBe('error');
    });

    test('tool_result without a matching start still keeps the result', () => {
        const state = applyChatEvent(newChatState(), {
            type: 'tool_result',
            call_id: 'ghost',
            name: 'search_items',
            summary: 'late result',
            is_error: false,
        });
        const part = state.messages[0].parts[0];
        expect(part.type).toBe('tool');
        expect(part.status).toBe('done');
        expect(part.summary).toBe('late result');
    });
});

describe('applyChatEvent: vision + turn lifecycle', () => {
    test('vision_request sets pendingVision', () => {
        const state = applyChatEvent(newChatState(), {
            type: 'vision_request',
            request_id: 'vr-1',
            items: [{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }],
        });
        expect(state.pendingVision).toEqual({
            request_id: 'vr-1',
            items: [{ id: 'a', title: 'A' }, { id: 'b', title: 'B' }],
        });
    });

    test('vision_decided clears pendingVision', () => {
        let state = applyChatEvent(newChatState(), {
            type: 'vision_request',
            request_id: 'vr-1',
            items: [],
        });
        state = applyChatEvent(state, { type: 'vision_decided' });
        expect(state.pendingVision).toBeNull();
    });

    test('turn_done clears the streaming flag', () => {
        let state = applyChatEvent(newChatState(), { type: 'token', text: 'x' });
        expect(state.streaming).toBe(true);
        state = applyChatEvent(state, { type: 'turn_done', stop_reason: 'end_turn' });
        expect(state.streaming).toBe(false);
        expect(state.stopReason).toBe('end_turn');
        // The message content stays.
        expect(state.messages[0].parts[0].text).toBe('x');
    });

    test('error appends an error part and stops streaming', () => {
        let state = applyChatEvent(newChatState(), { type: 'token', text: 'partial' });
        state = applyChatEvent(state, { type: 'error', message: 'Provider unavailable' });
        expect(state.streaming).toBe(false);
        const parts = state.messages[0].parts;
        expect(parts[0]).toEqual({ type: 'text', text: 'partial' });
        expect(parts[1]).toEqual({ type: 'error', text: 'Provider unavailable' });
    });

    test('error without an existing assistant message creates one', () => {
        const state = applyChatEvent(newChatState(), { type: 'error', message: 'No model' });
        expect(state.messages).toHaveLength(1);
        expect(state.messages[0].role).toBe('assistant');
        expect(state.messages[0].parts[0]).toEqual({ type: 'error', text: 'No model' });
    });

    test('unknown events are ignored and return an equal state', () => {
        const base = newChatState();
        const next = applyChatEvent(base, { type: 'image_ref', item_id: 'x' });
        expect(next).toEqual(base);
    });
});

describe('applyChatEvent: purity', () => {
    test('does not mutate the input state', () => {
        const base = appendUserMessage(newChatState(), 'q', ['item-1']);
        const snapshot = JSON.stringify(base);
        applyChatEvent(base, { type: 'token', text: 'a' });
        applyChatEvent(base, { type: 'tool_start', call_id: 'c', name: 'n', arguments: {} });
        applyChatEvent(base, { type: 'vision_request', request_id: 'r', items: [] });
        expect(JSON.stringify(base)).toBe(snapshot);
    });
});

describe('appendUserMessage', () => {
    test('adds a context part when item ids are present', () => {
        const state = appendUserMessage(newChatState(), 'tag these', ['a', 'b']);
        expect(state.messages[0].parts).toEqual([
            { type: 'text', text: 'tag these' },
            { type: 'context', item_ids: ['a', 'b'] },
        ]);
    });

    test('omits the context part when no items are attached', () => {
        const state = appendUserMessage(newChatState(), 'hello', []);
        expect(state.messages[0].parts).toEqual([{ type: 'text', text: 'hello' }]);
    });
});

describe('stateFromMessages (history hydration)', () => {
    test('merges tool_result messages into their tool cards', () => {
        const state = stateFromMessages([
            { role: 'user', parts: [{ type: 'text', text: 'hi' }] },
            {
                role: 'assistant',
                parts: [
                    { type: 'text', text: 'let me look' },
                    { type: 'tool_call', call_id: 'c1', name: 'search', arguments: { q: 'x' } },
                ],
            },
            {
                role: 'tool',
                parts: [{ type: 'tool_result', call_id: 'c1', name: 'search', content: 'result text', images: ['i1'], is_error: false }],
            },
            { role: 'assistant', parts: [{ type: 'text', text: 'found it' }] },
        ]);

        expect(state.streaming).toBe(false);
        expect(state.pendingVision).toBeNull();
        // user, assistant(merged), assistant('found it')
        expect(state.messages).toHaveLength(3);
        const card = state.messages[1].parts[1];
        expect(card.type).toBe('tool');
        expect(card.status).toBe('done');
        expect(card.result).toBe('result text');
        expect(card.images).toEqual(['i1']);
    });

    test('renders a dangling tool_call as expired', () => {
        const state = stateFromMessages([
            { role: 'assistant', parts: [{ type: 'tool_call', call_id: 'c9', name: 'search', arguments: {} }] },
        ]);
        expect(state.messages[0].parts[0].status).toBe('expired');
    });

    test('keeps image_ref and other unknown parts as-is', () => {
        const state = stateFromMessages([
            { role: 'assistant', parts: [{ type: 'image_ref', item_id: 'i-1' }, { type: 'text', text: 'see image' }] },
        ]);
        expect(state.messages[0].parts[0]).toEqual({ type: 'image_ref', item_id: 'i-1' });
        expect(state.messages[0].parts[1]).toEqual({ type: 'text', text: 'see image' });
    });

    test('handles null/missing parts defensively', () => {
        const state = stateFromMessages(null);
        expect(state.messages).toEqual([]);
        expect(state.streaming).toBe(false);
        const state2 = stateFromMessages([{ role: 'user' }]);
        expect(state2.messages).toHaveLength(1);
        expect(state2.messages[0].parts).toEqual([]);
    });
});

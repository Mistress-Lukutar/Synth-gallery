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

    test('tool_start for an existing call_id updates the card instead of duplicating', () => {
        let state = applyChatEvent(newChatState(), {
            type: 'tool_start',
            call_id: 'call-1',
            name: 'add_item_tags',
            arguments: { item_id: 'x', tag_names: ['a'] },
        });
        // Resumed turn re-announces the confirmed call.
        state = applyChatEvent(state, {
            type: 'tool_start',
            call_id: 'call-1',
            name: 'add_item_tags',
            arguments: { item_id: 'x', tag_names: ['b'] },
        });
        expect(state.messages[0].parts).toHaveLength(1);
        const part = state.messages[0].parts[0];
        expect(part.status).toBe('running');
        expect(part.args).toEqual({ item_id: 'x', tag_names: ['b'] });
    });

    test('resume flow: re-announced call keeps a single card that receives the result', () => {
        let state = applyChatEvent(newChatState(), {
            type: 'tool_start',
            call_id: 'call-1',
            name: 'add_item_tags',
            arguments: { item_id: 'x' },
        });
        state = applyChatEvent(state, {
            type: 'tool_confirm',
            request_id: 'r1',
            call_id: 'call-1',
            name: 'add_item_tags',
            arguments: { item_id: 'x' },
        });
        state = applyChatEvent(state, {
            type: 'tool_start',
            call_id: 'call-1',
            name: 'add_item_tags',
            arguments: { item_id: 'x' },
        });
        state = applyChatEvent(state, {
            type: 'tool_result',
            call_id: 'call-1',
            name: 'add_item_tags',
            summary: '2 added',
            content: '{"added": ["a", "b"]}',
            is_error: false,
        });
        expect(state.messages[0].parts).toHaveLength(1);
        const part = state.messages[0].parts[0];
        expect(part.status).toBe('done');
        expect(part.result).toBe('{"added": ["a", "b"]}');
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
            content: '{"items": [1, 2, 3]}',
            is_error: false,
        });
        const part = state.messages[0].parts[0];
        expect(part.status).toBe('done');
        expect(part.summary).toBe('3 items found');
        expect(part.result).toBe('{"items": [1, 2, 3]}');
    });

    test('tool_result without content keeps the card result empty', () => {
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
            summary: 'done',
            is_error: false,
        });
        expect(state.messages[0].parts[0].result).toBeNull();
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

    test('atIndex replaces the target message and the tail (edit/retry echo)', () => {
        const state = stateFromMessages([
            { id: 1, role: 'user', parent_id: null, parts: [{ type: 'text', text: 'first' }] },
            { id: 2, role: 'assistant', parent_id: 1, parts: [{ type: 'text', text: 'answer' }] },
            {
                id: 3, role: 'user', parent_id: 2,
                sibling_ids: [3], version: 1, version_count: 1,
                parts: [{ type: 'text', text: 'second' }],
            },
            { id: 4, role: 'assistant', parent_id: 3, parts: [{ type: 'text', text: 'answer 2' }] },
        ]);
        const next = appendUserMessage(state, 'second (edited)', null, 2);
        // Earlier messages stay, the retried message + its answer are gone.
        expect(next.messages).toHaveLength(3);
        expect(next.messages[1].parts[0].text).toBe('answer');
        expect(next.messages[2].role).toBe('user');
        expect(next.messages[2].parts[0].text).toBe('second (edited)');
        expect(next.messages[2].id).toBeNull();
    });

    test('atIndex leaves the input state untouched', () => {
        const state = stateFromMessages([
            { id: 1, role: 'user', parent_id: null, parts: [{ type: 'text', text: 'q' }] },
            { id: 2, role: 'assistant', parent_id: 1, parts: [{ type: 'text', text: 'a' }] },
        ]);
        const snapshot = JSON.stringify(state);
        appendUserMessage(state, 'q2', null, 0);
        expect(JSON.stringify(state)).toBe(snapshot);
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

    test('restores a pending vision request from the endpoint payload', () => {
        const state = stateFromMessages({
            messages: [
                { role: 'user', parts: [{ type: 'text', text: 'look' }] },
                { role: 'assistant', parts: [{ type: 'tool_call', call_id: 'v1', name: 'view_images', arguments: {} }] },
            ],
            pending_vision: {
                request_id: 'req-1',
                call_id: 'v1',
                items: [{ id: 'i1', title: 'a.jpg' }],
            },
            pending_tool_confirm: null,
        });
        // The approval card comes back after a reload...
        expect(state.pendingVision).toEqual({
            request_id: 'req-1',
            call_id: 'v1',
            items: [{ id: 'i1', title: 'a.jpg' }],
        });
        // ...and the paused call shows as running, not expired.
        expect(state.messages[1].parts[0].status).toBe('running');
    });

    test('restores a pending tool confirmation from the endpoint payload', () => {
        const state = stateFromMessages({
            messages: [
                { role: 'assistant', parts: [{ type: 'tool_call', call_id: 'm1', name: 'add_item_tags', arguments: {} }] },
            ],
            pending_vision: null,
            pending_tool_confirm: {
                request_id: 'req-2',
                call_id: 'm1',
                name: 'add_item_tags',
                arguments: { item_id: 'i1', tag_names: ['fox'] },
            },
        });
        expect(state.pendingToolConfirm).toEqual({
            request_id: 'req-2',
            call_id: 'm1',
            name: 'add_item_tags',
            arguments: { item_id: 'i1', tag_names: ['fox'] },
        });
        expect(state.pendingVision).toBeNull();
        expect(state.messages[0].parts[0].status).toBe('running');
    });

    test('a bare message list still loads without pendings', () => {
        const state = stateFromMessages([
            { role: 'assistant', parts: [{ type: 'tool_call', call_id: 'c9', name: 'search', arguments: {} }] },
        ]);
        expect(state.pendingVision).toBeNull();
        expect(state.pendingToolConfirm).toBeNull();
        expect(state.messages[0].parts[0].status).toBe('expired');
    });
});

describe('message tree metadata (edit/retry/version switch)', () => {
    test('stateFromMessages carries id/parent/version metadata', () => {
        const state = stateFromMessages([
            {
                id: 1, role: 'user', parent_id: null,
                sibling_ids: [1, 5], version: 1, version_count: 2,
                parts: [{ type: 'text', text: 'v1' }],
            },
            { id: 2, role: 'assistant', parent_id: 1, parts: [{ type: 'text', text: 'a' }] },
        ]);
        expect(state.messages[0].id).toBe(1);
        expect(state.messages[0].parentId).toBeNull();
        expect(state.messages[0].siblingIds).toEqual([1, 5]);
        expect(state.messages[0].version).toBe(1);
        expect(state.messages[0].versionCount).toBe(2);
        // Assistant messages carry the row id too (no actions rendered).
        expect(state.messages[1].id).toBe(2);
    });

    test('cloneState preserves the tree metadata across event updates', () => {
        let state = stateFromMessages([
            {
                id: 7, role: 'user', parent_id: 3,
                sibling_ids: [5, 7], version: 2, version_count: 2,
                parts: [{ type: 'text', text: 'q' }],
            },
        ]);
        state = applyChatEvent(state, { type: 'token', text: 'answer' });
        const user = state.messages[0];
        expect(user.id).toBe(7);
        expect(user.parentId).toBe(3);
        expect(user.siblingIds).toEqual([5, 7]);
        expect(user.versionCount).toBe(2);
    });

    test('optimistic user messages have no id (no actions rendered yet)', () => {
        const state = appendUserMessage(newChatState(), 'hello', null);
        expect(state.messages[0].id).toBeNull();
        expect(state.messages[0].siblingIds).toEqual([]);
    });

    test('messageText/messageContextIds read user message parts', () => {
        const mod = require('../../app/static/js/ai-chat.js');
        const message = {
            parts: [
                { type: 'text', text: 'describe this' },
                { type: 'context', item_ids: ['abc', 'def'] },
            ],
        };
        expect(mod.messageText(message)).toBe('describe this');
        expect(mod.messageContextIds(message)).toEqual(['abc', 'def']);
        expect(mod.messageContextIds({ parts: [{ type: 'text', text: 'x' }] })).toEqual([]);
    });
});

/**
 * Unit tests for the tool-confirmation events of the ai-chat.js reducer.
 *
 * A mutating tool pauses the turn with a `tool_confirm` SSE event; the
 * user answers through a card (Approve / Always allow / Deny) and the
 * `confirm_decided` pseudo-event clears the pending confirmation.
 */
const {
    applyChatEvent,
    stateFromMessages,
    newChatState,
} = require('../../app/static/js/ai-chat.js');

describe('applyChatEvent: tool_confirm', () => {
    test('stores the pending confirmation with parsed arguments', () => {
        const next = applyChatEvent(newChatState(), {
            type: 'tool_confirm',
            request_id: 'req1',
            call_id: 'c1',
            name: 'add_item_tags',
            arguments: JSON.stringify({ item_id: 'i1', tag_names: ['sunset'] }),
        });
        expect(next.pendingToolConfirm).toEqual({
            request_id: 'req1',
            call_id: 'c1',
            name: 'add_item_tags',
            arguments: { item_id: 'i1', tag_names: ['sunset'] },
        });
    });

    test('keeps raw string arguments when JSON parsing fails', () => {
        const next = applyChatEvent(newChatState(), {
            type: 'tool_confirm',
            request_id: 'req1',
            call_id: 'c1',
            name: 'create_tags',
            arguments: '{not json',
        });
        expect(next.pendingToolConfirm.arguments).toBe('{not json');
    });

    test('confirm_decided clears the pending confirmation', () => {
        let state = applyChatEvent(newChatState(), {
            type: 'tool_confirm',
            request_id: 'req1',
            call_id: 'c1',
            name: 'update_item',
            arguments: {},
        });
        state = applyChatEvent(state, { type: 'confirm_decided' });
        expect(state.pendingToolConfirm).toBeNull();
    });

    test('returns a NEW state object for every confirmation event', () => {
        const before = newChatState();
        const after = applyChatEvent(before, {
            type: 'tool_confirm',
            request_id: 'req1',
            call_id: 'c1',
            name: 'create_tags',
            arguments: {},
        });
        expect(after).not.toBe(before);
        expect(before.pendingToolConfirm).toBeNull();
    });

    test('clones the pending confirmation without sharing references', () => {
        const state = applyChatEvent(newChatState(), {
            type: 'tool_confirm',
            request_id: 'req1',
            call_id: 'c1',
            name: 'add_item_tags',
            arguments: { tag_names: ['a'] },
        });
        const cloned = applyChatEvent(state, { type: 'token', text: 'x' });
        expect(cloned.pendingToolConfirm).toEqual(state.pendingToolConfirm);
        expect(cloned.pendingToolConfirm).not.toBe(state.pendingToolConfirm);
    });

    test('history loads never leave a pending confirmation behind', () => {
        const state = stateFromMessages([
            {
                role: 'assistant',
                parts: [{ type: 'tool_call', call_id: 'c1', name: 'create_tags' }],
            },
        ]);
        expect(state.pendingToolConfirm).toBeNull();
        expect(state.messages[0].parts[0].status).toBe('expired');
    });
});

/**
 * Unit tests for groupConversationsByAgent and parseCollapsedGroups helpers
 * exported by ai-chat.js.
 */
const { groupConversationsByAgent, parseCollapsedGroups } = require('../../app/static/js/ai-chat.js');

// ---------------------------------------------------------------------------
// Fixture factories
// ---------------------------------------------------------------------------

function makeConversation(id, title, agentId) {
    return { id: id, title: title, agent_id: agentId };
}

function makeAgent(id, name, icon, colorIndex) {
    return { id: id, name: name, icon: icon, color_index: colorIndex };
}

// ===========================================================================
// groupConversationsByAgent
// ===========================================================================

describe('groupConversationsByAgent', () => {
    test('empty conversations returns []', () => {
        expect(groupConversationsByAgent([], [])).toEqual([]);
    });

    test('no agents with no conversations returns []', () => {
        expect(groupConversationsByAgent([], [makeAgent('a1', 'A')])).toEqual([]);
    });

    test('conversations with null agent_id go to default group', () => {
        const convs = [makeConversation('c1', 'Chat 1', null)];
        const result = groupConversationsByAgent(convs, []);
        expect(result).toHaveLength(1);
        expect(result[0].key).toBe('default');
        expect(result[0].agent).toBeNull();
        expect(result[0].items).toEqual(convs);
    });

    test('conversations with undefined agent_id go to default group', () => {
        const convs = [makeConversation('c1', 'Chat 1', undefined)];
        const result = groupConversationsByAgent(convs, []);
        expect(result).toHaveLength(1);
        expect(result[0].key).toBe('default');
    });

    test('conversations with unknown (dangling) agent_id go to default group', () => {
        const convs = [makeConversation('c1', 'Chat 1', 'unknown-agent')];
        const agents = [makeAgent('a1', 'A')];
        const result = groupConversationsByAgent(convs, agents);
        expect(result).toHaveLength(1);
        expect(result[0].key).toBe('default');
        expect(result[0].items).toEqual(convs);
    });

    test('agents with zero conversations are omitted', () => {
        const convs = [makeConversation('c1', 'Chat 1', 'a1')];
        const agents = [makeAgent('a1', 'A'), makeAgent('a2', 'B')];
        const result = groupConversationsByAgent(convs, agents);
        expect(result).toHaveLength(1);
        expect(result[0].key).toBe('agent-a1');
    });

    test('default group is omitted when empty and no dangling/no-agent chats exist', () => {
        const convs = [
            makeConversation('c1', 'Chat 1', 'a1'),
            makeConversation('c2', 'Chat 2', 'a2'),
        ];
        const agents = [makeAgent('a1', 'A'), makeAgent('a2', 'B')];
        const result = groupConversationsByAgent(convs, agents);
        expect(result).toHaveLength(2);
        expect(result[0].key).toBe('agent-a1');
        expect(result[1].key).toBe('agent-a2');
    });

    test('agents are listed in the input order', () => {
        const convs = [makeConversation('c1', 'Chat 1', 'a2')];
        const agents = [makeAgent('a1', 'A'), makeAgent('a2', 'B')];
        const result = groupConversationsByAgent(convs, agents);
        expect(result).toHaveLength(1);
        expect(result[0].key).toBe('agent-a2');
    });

    test('within-group order matches input order', () => {
        const convs = [
            makeConversation('c1', 'First', 'a1'),
            makeConversation('c2', 'Second', 'a1'),
            makeConversation('c3', 'Third', 'a1'),
        ];
        const result = groupConversationsByAgent(convs, []);
        expect(result).toHaveLength(1);
        expect(result[0].items.map(c => c.id)).toEqual(['c1', 'c2', 'c3']);
    });

    test('input arrays are not mutated', () => {
        const convs = [makeConversation('c1', 'Chat 1', null)];
        const agents = [makeAgent('a1', 'A')];
        const originalConvsLen = convs.length;
        const originalAgentsLen = agents.length;
        groupConversationsByAgent(convs, agents);
        expect(convs.length).toBe(originalConvsLen);
        expect(agents.length).toBe(originalAgentsLen);
    });

    test('conversation objects are not replaced — same references', () => {
        const convs = [makeConversation('c1', 'Chat 1', null)];
        const result = groupConversationsByAgent(convs, []);
        expect(result[0].items[0]).toBe(convs[0]);
    });

    test('agents array is not mutated', () => {
        const convs = [makeConversation('c1', 'Chat 1', 'a1')];
        const agents = [makeAgent('a1', 'A'), makeAgent('a2', 'B')];
        const agentRefs = agents.map(a => Object.isFrozen ? Object.isFrozen(a) : true);
        groupConversationsByAgent(convs, agents);
        expect(agents.length).toBe(agentRefs.length);
    });

    // -----------------------------------------------------------------------
    // Regression: each conversation appears EXACTLY ONCE across all groups
    // -----------------------------------------------------------------------
    test('a known-agent conversation appears only in its agent group, not in default', () => {
        const convs = [
            makeConversation('c1', 'Default chat', null),
            makeConversation('c2', 'Agent A chat', 'a1'),
            makeConversation('c3', 'Another A chat', 'a1'),
        ];
        const agents = [makeAgent('a1', 'A')];
        const result = groupConversationsByAgent(convs, agents);

        // Collect all ids across groups
        const allIds = [];
        for (const group of result) {
            for (const item of group.items) {
                allIds.push(item.id);
            }
        }
        // No duplicates
        expect(new Set(allIds).size).toBe(allIds.length);
        // Exactly one match per conversation
        expect(allIds).toHaveLength(3);
    });

    test('dangling agent_id appears only in default group', () => {
        const convs = [makeConversation('c1', 'Dangling', 'dead-agent')];
        const agents = [makeAgent('a1', 'A')];
        const result = groupConversationsByAgent(convs, agents);
        expect(result).toHaveLength(1);
        expect(result[0].key).toBe('default');
    });

    // -----------------------------------------------------------------------
    // Mixed scenario regression case
    // -----------------------------------------------------------------------
    test('mixed: 1 default + 2 agent B + 1 agent A + 1 dangling, agents=[A,B]', () => {
        const agentA = makeAgent('a1', 'Agent A', 'sparkles', 0);
        const agentB = makeAgent('a2', 'Agent B', 'book', 1);

        const convs = [
            makeConversation('c1', 'Default chat', null),         // default
            makeConversation('c2', 'B chat 1', 'a2'),            // B
            makeConversation('c3', 'B chat 2', 'a2'),            // B
            makeConversation('c4', 'A chat', 'a1'),              // A
            makeConversation('c5', 'Dangling chat', 'dead-id'),  // default (dangling)
        ];

        const result = groupConversationsByAgent(convs, [agentA, agentB]);

        // Expect 3 groups: default, agent-a1, agent-a2
        expect(result).toHaveLength(3);
        expect(result[0].key).toBe('default');
        expect(result[0].agent).toBeNull();
        expect(result[1].key).toBe('agent-a1');
        expect(result[2].key).toBe('agent-a2');

        // Default group: input-order items with no-agent + dangling
        expect(result[0].items.map(c => c.id)).toEqual(['c1', 'c5']);

        // Agent A: 1 item
        expect(result[1].items).toHaveLength(1);
        expect(result[1].items[0].id).toBe('c4');

        // Agent B: 2 items in input order
        expect(result[2].items.map(c => c.id)).toEqual(['c2', 'c3']);

        // No id appears twice
        const allIds = [];
        for (const group of result) {
            for (const item of group.items) {
                allIds.push(item.id);
            }
        }
        expect(new Set(allIds).size).toBe(allIds.length);
    });

    // -----------------------------------------------------------------------
    // Agents empty but conversations have agent_id present
    // -----------------------------------------------------------------------
    test('agents=[] with conversations carrying agent_id -> all in default', () => {
        const convs = [
            makeConversation('c1', 'Chat 1', 'a1'),
            makeConversation('c2', 'Chat 2', 'a2'),
        ];
        const result = groupConversationsByAgent(convs, []);
        expect(result).toHaveLength(1);
        expect(result[0].key).toBe('default');
        expect(result[0].items.map(c => c.id)).toEqual(['c1', 'c2']);
    });

    // -----------------------------------------------------------------------
    // Non-mutation: individual item properties unchanged
    // -----------------------------------------------------------------------
    test('conversation object references are preserved (toBe check)', () => {
        const conv = makeConversation('c1', 'Chat 1', 'a1');
        const agent = makeAgent('a1', 'A', 'icon', 0);
        const convs = [conv];
        const agents = [agent];
        const result = groupConversationsByAgent(convs, agents);
        // Default group is omitted (empty), so the agent group is at index 0
        expect(result[0].items[0]).toBe(conv);
    });

    test('agents array elements are not replaced in output', () => {
        const agent = makeAgent('a1', 'A', 'icon', 0);
        const convs = [makeConversation('c1', 'Chat 1', 'a1')];
        const result = groupConversationsByAgent(convs, [agent]);
        expect(result[0].agent).toBe(agent);
    });
});

// ===========================================================================
// parseCollapsedGroups
// ===========================================================================

describe('parseCollapsedGroups', () => {
    test('null input returns {}', () => {
        expect(parseCollapsedGroups(null)).toEqual({});
    });

    test('undefined input returns {}', () => {
        expect(parseCollapsedGroups(undefined)).toEqual({});
    });

    test('number input returns {}', () => {
        expect(parseCollapsedGroups(42)).toEqual({});
        expect(parseCollapsedGroups(0)).toEqual({});
        expect(parseCollapsedGroups(-1)).toEqual({});
    });

    test('boolean input returns {}', () => {
        expect(parseCollapsedGroups(true)).toEqual({});
        expect(parseCollapsedGroups(false)).toEqual({});
    });

    test('empty string returns {}', () => {
        expect(parseCollapsedGroups('')).toEqual({});
    });

    test('whitespace-only string returns {}', () => {
        expect(parseCollapsedGroups('   ')).toEqual({});
    });

    test('invalid JSON string returns {}', () => {
        expect(parseCollapsedGroups('{broken')).toEqual({});
        expect(parseCollapsedGroups('not json at all')).toEqual({});
        expect(parseCollapsedGroups('[unclosed')).toEqual({});
    });

    test('JSON array string returns {}', () => {
        expect(parseCollapsedGroups('[]')).toEqual({});
        expect(parseCollapsedGroups('[1, 2, 3]')).toEqual({});
        expect(parseCollapsedGroups('["a", "b"]')).toEqual({});
    });

    test('valid JSON object string is parsed', () => {
        expect(parseCollapsedGroups('{"default": true}')).toEqual({ default: true });
    });

    test('valid JSON object with multiple keys is parsed', () => {
        const input = '{"default":true,"agent-a1":false,"agent-a2":true}';
        const expected = { default: true, 'agent-a1': false, 'agent-a2': true };
        expect(parseCollapsedGroups(input)).toEqual(expected);
    });

    test('nested object values are preserved', () => {
        const input = '{"default":{"collapsed":true}}';
        expect(parseCollapsedGroups(input)).toEqual({ default: { collapsed: true } });
    });

    test('empty JSON object string returns empty object', () => {
        expect(parseCollapsedGroups('{}')).toEqual({});
    });
});

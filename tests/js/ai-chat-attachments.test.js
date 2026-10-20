/**
 * Unit tests for attachment helpers in ai-chat.js.
 */
const { contextThumbIds, attachChipLabel } = require('../../app/static/js/ai-chat.js');

// ====================================================================
// contextThumbIds
// ====================================================================

describe('contextThumbIds', () => {
    test('under cap: all shown, hidden=0', () => {
        const result = contextThumbIds(['a', 'b'], 5);
        expect(result.shown).toEqual(['a', 'b']);
        expect(result.hidden).toBe(0);
    });

    test('at cap: exactly cap shown', () => {
        const ids = ['a', 'b', 'c'];
        const result = contextThumbIds(ids, 3);
        expect(result.shown).toEqual(['a', 'b', 'c']);
        expect(result.hidden).toBe(0);
    });

    test('over cap: hidden count correct', () => {
        const ids = ['a', 'b', 'c', 'd', 'e'];
        const result = contextThumbIds(ids, 3);
        expect(result.shown).toEqual(['a', 'b', 'c']);
        expect(result.hidden).toBe(2);
    });

    test('non-array input returns empty shown/hidden', () => {
        const result = contextThumbIds(null);
        expect(result.shown).toEqual([]);
        expect(result.hidden).toBe(0);
    });

    test('custom cap respected', () => {
        const ids = ['a', 'b', 'c', 'd', 'e', 'f'];
        const result = contextThumbIds(ids, 2);
        expect(result.shown).toEqual(['a', 'b']);
        expect(result.hidden).toBe(4);
    });

    test('invalid cap (0) falls back to default CONTEXT_THUMB_CAP (8)', () => {
        // Default is 8; with only 3 items all should be shown
        const ids = ['a', 'b', 'c'];
        const result = contextThumbIds(ids, 0);
        expect(result.shown).toEqual(['a', 'b', 'c']);
        expect(result.hidden).toBe(0);
    });

    test('invalid cap (negative) falls back to default CONTEXT_THUMB_CAP (8)', () => {
        const ids = ['a', 'b', 'c'];
        const result = contextThumbIds(ids, -1);
        expect(result.shown).toEqual(['a', 'b', 'c']);
        expect(result.hidden).toBe(0);
    });

    test('undefined cap falls back to default CONTEXT_THUMB_CAP (8)', () => {
        const ids = ['a', 'b', 'c'];
        const result = contextThumbIds(ids, undefined);
        expect(result.shown).toEqual(['a', 'b', 'c']);
        expect(result.hidden).toBe(0);
    });

    test('empty array returns empty shown/hidden', () => {
        const result = contextThumbIds([]);
        expect(result.shown).toEqual([]);
        expect(result.hidden).toBe(0);
    });
});

// ====================================================================
// attachChipLabel
// ====================================================================

describe('attachChipLabel', () => {
    test('photos only (singular)', () => {
        expect(attachChipLabel(1, 0)).toBe('Attach selection (1 item)');
    });

    test('photos only (plural)', () => {
        expect(attachChipLabel(3, 0)).toBe('Attach selection (3 items)');
    });

    test('albums only (singular)', () => {
        expect(attachChipLabel(0, 1)).toBe('Attach selection (1 album)');
    });

    test('albums only (plural)', () => {
        expect(attachChipLabel(0, 3)).toBe('Attach selection (3 albums)');
    });

    test('mixed photos and albums', () => {
        expect(attachChipLabel(2, 1)).toBe('Attach selection (2 items, 1 album)');
    });

    test('both zero returns default label with no counts', () => {
        expect(attachChipLabel(0, 0)).toBe('Attach selection ()');
    });
});

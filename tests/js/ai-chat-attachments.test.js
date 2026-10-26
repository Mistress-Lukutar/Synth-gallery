/**
 * Unit tests for attachment helpers in ai-chat.js.
 */
const { contextThumbIds, attachChipLabel, mergeAttachments, attachmentRequestIds } = require('../../app/static/js/ai-chat.js');

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

    test('both zero returns default label "Attach"', () => {
        expect(attachChipLabel(0, 0)).toBe('Attach');
    });
});

// ====================================================================
// mergeAttachments
// ====================================================================

describe('mergeAttachments', () => {
    test('empty existing + empty ids → []', () => {
        const result = mergeAttachments([], [], []);
        expect(result).toEqual([]);
    });

    test('appends item ids in order as { kind: "item", id } entries', () => {
        const result = mergeAttachments([], ['id1', 'id2'], []);
        expect(result).toEqual([
            { kind: 'item', id: 'id1' },
            { kind: 'item', id: 'id2' },
        ]);
    });

    test('appends album ids after item ids as { kind: "album", id } entries', () => {
        const result = mergeAttachments([], ['id1'], ['alb1']);
        expect(result).toEqual([
            { kind: 'item', id: 'id1' },
            { kind: 'album', id: 'alb1' },
        ]);
    });

    test('dedups against existing entries (same kind+id not added twice)', () => {
        const existing = [{ kind: 'item', id: 'a' }];
        const result = mergeAttachments(existing, ['a'], []);
        expect(result).toEqual([
            { kind: 'item', id: 'a' },
        ]);
    });

    test('dedups duplicates within the SAME call (e.g. ["a","a"] → one entry)', () => {
        const result = mergeAttachments([], ['a', 'a'], []);
        expect(result).toEqual([
            { kind: 'item', id: 'a' },
        ]);
    });

    test('an item id and an album id with the same string are both kept (different keys)', () => {
        const result = mergeAttachments([], ['same'], ['same']);
        expect(result).toEqual([
            { kind: 'item', id: 'same' },
            { kind: 'album', id: 'same' },
        ]);
    });

    test('does NOT mutate the existing array passed in', () => {
        const existing = [{ kind: 'item', id: 'x' }];
        mergeAttachments(existing, ['y'], []);
        expect(existing).toEqual([{ kind: 'item', id: 'x' }]);
    });

    test('tolerates null/non-array arguments: mergeAttachments(null, "notanarray", undefined) → []', () => {
        const result = mergeAttachments(null, 'notanarray', undefined);
        expect(result).toEqual([]);
    });

    test('tolerates null itemIds but valid albumIds', () => {
        const result = mergeAttachments([], null, ['alb']);
        expect(result).toEqual([
            { kind: 'album', id: 'alb' },
        ]);
    });
});

// ====================================================================
// attachmentRequestIds
// ====================================================================

describe('attachmentRequestIds', () => {
    test('splits mixed entries into { itemIds, albumIds } preserving order', () => {
        const attachments = [
            { kind: 'item', id: 'i1' },
            { kind: 'album', id: 'a1' },
            { kind: 'item', id: 'i2' },
        ];
        const result = attachmentRequestIds(attachments);
        expect(result).toEqual({
            itemIds: ['i1', 'i2'],
            albumIds: ['a1'],
        });
    });

    test('non-array input → { itemIds: [], albumIds: [] }', () => {
        const result = attachmentRequestIds(null);
        expect(result).toEqual({ itemIds: [], albumIds: [] });
    });

    test('skips null entries and entries without id', () => {
        const attachments = [
            null,
            { kind: 'item' },
            { id: 'x' }, // no kind
            { kind: 'item', id: 'valid' },
        ];
        const result = attachmentRequestIds(attachments);
        expect(result).toEqual({
            itemIds: ['valid'],
            albumIds: [],
        });
    });

    test('entries with an unrecognized kind are skipped (only "item" → itemIds, only "album" → albumIds)', () => {
        const attachments = [
            { kind: 'item', id: 'i1' },
            { kind: 'unknown', id: 'u1' },
            { kind: 'album', id: 'a1' },
            { kind: '', id: 'e1' },
        ];
        const result = attachmentRequestIds(attachments);
        expect(result).toEqual({
            itemIds: ['i1'],
            albumIds: ['a1'],
        });
    });
});

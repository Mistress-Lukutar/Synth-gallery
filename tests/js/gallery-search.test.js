/**
 * Unit tests for the pure helpers extracted from gallery-search.js:
 * query parsing, suggestion application and search API URLs.
 */
const {
    parseSearchQuery,
    applySuggestionToInput,
    buildTagSearchApiUrl,
    buildSearchApiUrl,
} = require('../../app/static/js/gallery-search.js');

describe('parseSearchQuery', () => {
    test('returns the last word as the current word', () => {
        const parsed = parseSearchQuery('cat sitting');
        expect(parsed.currentWord).toBe('sitting');
        expect(parsed.isNegative).toBe(false);
        expect(parsed.searchWord).toBe('sitting');
    });

    test('detects a negative tag search', () => {
        const parsed = parseSearchQuery('cat -wat');
        expect(parsed.currentWord).toBe('-wat');
        expect(parsed.isNegative).toBe(true);
        expect(parsed.searchWord).toBe('wat');
    });

    test('lowercases the current word', () => {
        expect(parseSearchQuery('CAT').currentWord).toBe('cat');
    });

    test('splits on any whitespace run', () => {
        expect(parseSearchQuery('a  b\tc').words).toEqual(['a', 'b', 'c']);
    });
});

describe('applySuggestionToInput', () => {
    test('replaces the last word and keeps a trailing space', () => {
        expect(applySuggestionToInput('cat sit', 'sitting', false))
            .toBe('cat sitting ');
    });

    test('preserves the negative marker', () => {
        expect(applySuggestionToInput('cat -wat', 'water', true))
            .toBe('cat -water ');
    });

    test('handles a single-word input', () => {
        expect(applySuggestionToInput('ca', 'cat', false)).toBe('cat ');
    });
});

describe('buildTagSearchApiUrl', () => {
    test('encodes the query and applies the default limit', () => {
        expect(buildTagSearchApiUrl('', 'a b'))
            .toBe('/api/tags/search?q=a%20b&limit=50');
    });

    test('honours a custom limit and base path', () => {
        expect(buildTagSearchApiUrl('/synth', 'x', 10))
            .toBe('/synth/api/tags/search?q=x&limit=10');
    });
});

describe('buildSearchApiUrl', () => {
    test('encodes all parameters', () => {
        const url = buildSearchApiUrl('', {
            tags: 'cat -dog',
            folderId: 'folder 1',
            sort: 'taken',
        });
        expect(url).toBe(
            '/api/search?tags=cat%20-dog&folder_id=folder%201&sort=taken');
    });
});

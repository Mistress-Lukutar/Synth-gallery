/**
 * Unit tests for the pure helpers extracted from navigation.js:
 * folder URL building, note extension display and sort labels.
 */
const {
    buildFolderUrl,
    getNoteExtension,
    getSortLabel,
} = require('../../app/static/js/navigation.js');

describe('buildFolderUrl', () => {
    test('builds a folder URL without a base path', () => {
        expect(buildFolderUrl('', 'abc-123')).toBe('/?folder_id=abc-123');
    });

    test('builds a folder URL under a subpath', () => {
        expect(buildFolderUrl('/synth', 'abc-123'))
            .toBe('/synth/?folder_id=abc-123');
    });
});

describe('getNoteExtension', () => {
    test('extracts and lowercases the extension', () => {
        expect(getNoteExtension('notes.MD')).toBe('md');
        expect(getNoteExtension('archive.tar.gz')).toBe('gz');
    });

    test('defaults to txt when no extension is present', () => {
        expect(getNoteExtension('README')).toBe('txt');
        expect(getNoteExtension('')).toBe('txt');
        expect(getNoteExtension(undefined)).toBe('txt');
    });
});

describe('getSortLabel', () => {
    test('labels the two sort modes', () => {
        expect(getSortLabel('taken')).toBe('Sort: Date Taken');
        expect(getSortLabel('uploaded')).toBe('Sort: Date Uploaded');
    });
});

describe('buildTagsUrl', () => {
    test('builds a tags URL with a tag name and empty base', () => {
        expect(buildTagsUrl('', 'silver_fox')).toBe('/?view=tags&tag=silver_fox');
    });

    test('builds a tags URL with null tag name', () => {
        expect(buildTagsUrl('', null)).toBe('/?view=tags');
    });

    test('builds a tags URL with undefined tag name', () => {
        expect(buildTagsUrl('', undefined)).toBe('/?view=tags');
    });

    test('builds a tags URL with empty string tag name', () => {
        expect(buildTagsUrl('', '')).toBe('/?view=tags');
    });

    test('encodes spaces in tag name under a subpath', () => {
        expect(buildTagsUrl('/synth', 'two words'))
            .toBe('/synth/?view=tags&tag=two%20words');
    });

    test('encodes special characters (ampersand) in tag name', () => {
        expect(buildTagsUrl('/synth', 'a&b'))
            .toBe('/synth/?view=tags&tag=a%26b');
    });
});

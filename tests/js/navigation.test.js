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

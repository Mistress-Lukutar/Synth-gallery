/**
 * Unit tests for the pure helpers extracted from gallery-notes.js:
 * note filename → highlight.js language mapping and markdown detection.
 */
const {
    noteLanguageFromName,
    isMarkdownNote,
} = require('../../app/static/js/gallery-notes.js');

describe('noteLanguageFromName', () => {
    test.each([
        ['readme.md', 'markdown'],
        ['README.MARKDOWN', 'markdown'],
        ['data.json', 'json'],
        ['table.csv', 'csv'],
        ['conf.yaml', 'yaml'],
        ['conf.yml', 'yaml'],
        ['notes.txt', 'plaintext'],
    ])('maps %s to %s', (name, expected) => {
        expect(noteLanguageFromName(name)).toBe(expected);
    });

    test('falls back to plaintext for unknown extensions', () => {
        expect(noteLanguageFromName('file.log')).toBe('plaintext');
    });

    test('falls back to plaintext for names without an extension', () => {
        expect(noteLanguageFromName('untitled')).toBe('plaintext');
        expect(noteLanguageFromName('')).toBe('plaintext');
        expect(noteLanguageFromName(null)).toBe('plaintext');
    });
});

describe('isMarkdownNote', () => {
    test('detects markdown by content type', () => {
        expect(isMarkdownNote({ content_type: 'text/markdown' })).toBe(true);
        expect(isMarkdownNote({ content_type: 'text/plain' })).toBe(false);
    });

    test('detects markdown by filename when content type is generic', () => {
        expect(isMarkdownNote({
            content_type: 'text/plain',
            original_name: 'doc.md',
        })).toBe(true);
        expect(isMarkdownNote({
            content_type: 'text/plain',
            original_name: 'doc.txt',
        })).toBe(false);
    });

    test('handles missing fields', () => {
        expect(isMarkdownNote({})).toBe(false);
    });
});

/**
 * Unit tests for the pure helpers extracted from gallery-notes.js:
 * note filename → highlight.js language mapping, markdown detection,
 * heading slugs and note link classification.
 */
const {
    noteLanguageFromName,
    isMarkdownNote,
    slugifyHeading,
    parseNoteLinkHref,
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

describe('slugifyHeading', () => {
    test.each([
        ['Part 1', 'part-1'],
        ['  spaces   collapse  ', 'spaces-collapse'],
        ['MixedCASE', 'mixedcase'],
    ])('slugifies %s', (input, expected) => {
        expect(slugifyHeading(input)).toBe(expected);
    });

    test('keeps cyrillic letters', () => {
        expect(slugifyHeading('Часть 1')).toBe('часть-1');
        expect(slugifyHeading('Демонстрация возможностей Markdown')).toBe('демонстрация-возможностей-markdown');
    });

    test('strips punctuation and markdown remnants', () => {
        expect(slugifyHeading('1. Текст и форматирование')).toBe('1-текст-и-форматирование');
        expect(slugifyHeading('**Bold** heading!')).toBe('bold-heading');
    });

    test('falls back to empty when nothing is left', () => {
        expect(slugifyHeading('!!!')).toBe('');
        expect(slugifyHeading('')).toBe('');
        expect(slugifyHeading(null)).toBe('');
    });
});

describe('parseNoteLinkHref', () => {
    const origin = 'http://localhost:8008';
    const base = 'synth';

    test('classifies hash-only links as fragments', () => {
        expect(parseNoteLinkHref('#part1', base, origin)).toEqual({
            type: 'fragment', folderId: null, photoId: null, fragment: 'part1',
        });
        expect(parseNoteLinkHref('#Часть-1', base, origin)).toEqual({
            type: 'fragment', folderId: null, photoId: null, fragment: 'Часть-1',
        });
    });

    test('parses root-relative links that would lose the base path', () => {
        expect(parseNoteLinkHref('/?folder_id=f1&photo_id=p1', base, origin)).toEqual({
            type: 'internal', folderId: 'f1', photoId: 'p1', fragment: null,
        });
    });

    test('parses base-relative and absolute same-origin links', () => {
        expect(parseNoteLinkHref('/synth/?photo_id=p1#part1', base, origin)).toEqual({
            type: 'internal', folderId: null, photoId: 'p1', fragment: 'part1',
        });
        expect(parseNoteLinkHref(`${origin}/synth/?folder_id=f1#Часть-2`, base, origin)).toEqual({
            type: 'internal', folderId: 'f1', photoId: null, fragment: 'Часть-2',
        });
    });

    test('treats other paths and foreign origins as external', () => {
        expect(parseNoteLinkHref(`${origin}/synth/other/?x=1`, base, origin).type).toBe('external');
        expect(parseNoteLinkHref('https://example.com/synth/?photo_id=p1', base, origin).type).toBe('external');
        expect(parseNoteLinkHref('mailto:someone@example.com', base, origin).type).toBe('external');
        expect(parseNoteLinkHref('', base, origin).type).toBe('external');
    });

    test('works without a base path', () => {
        expect(parseNoteLinkHref('/?photo_id=p1', '', origin)).toEqual({
            type: 'internal', folderId: null, photoId: 'p1', fragment: null,
        });
    });
});
